"""Daily run: update earlier picks, find today's top setups, send them to Telegram.

    python -m india_trader.main            # the real run (needs internet)
    python -m india_trader.main --dry-run  # print the message; send nothing, save nothing
    python -m india_trader.main --demo     # made-up prices, to see how it looks offline
    python -m india_trader.main --force    # run even on a weekend or NSE holiday
"""
import argparse
import html
import json
import sys
from datetime import time as dtime
from pathlib import Path

from . import context, data_sources, market_calendar, news, scanner, tracker
from .gitsave import commit_paths
from .notify import build_message, send_telegram

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "latest.md"
LAST_SCAN = ROOT / "data" / "last_scan.txt"


def scanned_today(today) -> bool:
    try:
        return LAST_SCAN.read_text().strip() == today.isoformat()
    except OSError:
        return False


def load_config() -> dict:
    cfg = json.loads((ROOT / "config.json").read_text())
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def drop_unfinished_candle(prices: dict, now) -> None:
    """During market hours the newest candle is still moving; use completed days only."""
    if now.time() >= dtime(15, 45):
        return
    today = now.date()
    for sym, df in prices.items():
        if len(df) and df.index[-1] == today:
            prices[sym] = df.iloc[:-1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print only; do not send or save")
    ap.add_argument("--demo", action="store_true", help="use made-up prices (implies --dry-run)")
    ap.add_argument("--force", action="store_true", help="run even when NSE is closed today")
    ap.add_argument("--limit", type=int, default=0, help="scan only the first N stocks (testing)")
    ap.add_argument("--test-send", action="store_true",
                    help="send the message to Telegram marked as a test; save nothing")
    ap.add_argument("--no-news", action="store_true", help="skip news, results calendar and FII/DII")
    args = ap.parse_args(argv)
    if args.demo:
        args.dry_run = args.no_news = True
    if args.test_send:
        args.dry_run = True

    cfg = load_config()
    now = market_calendar.now_ist()
    today = now.date()
    note = ""
    if not market_calendar.holidays_known(today.year):
        note = f"NSE holiday list for {today.year} is not loaded yet; weekdays are assumed open."
    if not args.force and not args.demo and not market_calendar.is_trading_day(today):
        print(f"{today} is not an NSE trading day. Nothing to do.")
        return 0
    if not args.force and not args.dry_run and scanned_today(today):
        print(f"The morning message for {today} was already sent.")
        return 0

    if not args.demo and data_sources.refresh_universe():
        print("Nifty 500 list refreshed.")
    if not args.demo and data_sources.refresh_all_nse():
        print("All-NSE stock list refreshed.")
    universes = {"nifty500": data_sources.load_universe(), "all_nse": data_sources.load_all_nse()}
    if universes["all_nse"] is None:
        universes["all_nse"] = universes["nifty500"]
        note = (note + " " if note else "") + "List of all NSE stocks unavailable; using the Nifty 500 for every list."
    if args.limit:
        universes = {k: v[: args.limit] for k, v in universes.items()}
    picks_log = tracker.load()
    symbols = sorted({s["symbol"] for u in universes.values() for s in u} | tracker.active_symbols(picks_log))

    print(f"Fetching prices for {len(symbols)} stocks + Nifty 50 + India VIX ...")
    if args.demo:
        from .demo import fake_prices
        prices = fake_prices(symbols, today)
        note = "DEMO: made-up prices, not real stocks data."
    else:
        prices = data_sources.fetch_many(symbols + [data_sources.NIFTY50, data_sources.INDIA_VIX])
    drop_unfinished_candle(prices, now)
    nifty = prices.pop(data_sources.NIFTY50, None)
    vix = prices.pop(data_sources.INDIA_VIX, None)
    print(f"Got prices for {len(prices)} of {len(symbols)} stocks.")
    if len(prices) < len(symbols) * 0.5:
        msg = (f"⚠️ Trade helper: price download failed ({len(prices)} of {len(symbols)} stocks). "
               "No picks today. The data source may be down.")
        print(msg)
        if not args.dry_run:
            send_telegram(msg)
        return 1

    data_date = max(str(df.index[-1]) for df in prices.values())
    picks_log, events = tracker.update(picks_log, prices, cfg)

    mood = scanner.market_mood(nifty)
    extra = {"pulse": None, "skipped": [], "warnings": [], "down": []}
    nse = meetings = None
    if not args.no_news:
        print("Checking results calendar, FII/DII and India VIX ...")
        nse = news.NSE()
        meetings = nse.board_meetings()
        extra["pulse"] = context.market_pulse(nse, vix)
        active_rows = picks_log[picks_log["status"].isin(tracker.ACTIVE)].to_dict("records")
        extra["warnings"] = context.holding_warnings(active_rows, meetings, cfg, today)

    sections, picks = [], []
    if mood["mood"] == "Bearish" and cfg["skip_new_picks_when_market_bearish"]:
        note = (note + " " if note else "") + "No new picks: market is bearish (your setting)."
    else:
        fresh = {s: df for s, df in prices.items() if str(df.index[-1]) == data_date}
        taken = set(tracker.active_symbols(picks_log))
        down = set()
        for lst in cfg["lists"]:
            lcfg = {**cfg, "top_n": lst["top_n"], "max_entry_price": lst.get("max_entry_price")}
            ranked = scanner.rank(fresh, universes[lst["universe"]], lcfg, exclude=taken)
            skipped = []
            if nse is not None:
                print(f"{lst['title']}: checking news for the top "
                      f"{min(len(ranked), context.MAX_CANDIDATES)} candidates ...")
                ranked, skipped, d = context.research(ranked, lcfg, today, nse, meetings)
                down |= set(d)
            chosen = scanner.select(ranked, lcfg)
            for p in chosen:
                p["list"] = lst["title"]
            taken |= {p["symbol"] for p in chosen}
            picks += chosen
            sections.append({"title": lst["title"], "top_n": lst["top_n"], "picks": chosen,
                             "skipped": skipped})
        extra["down"] = sorted(down)

    picks_log = tracker.add(picks_log, picks)
    stats = tracker.stats(picks_log)
    text = build_message(today.strftime("%a %d %b %Y"), data_date, mood, sections, events, stats, cfg, note, extra)
    print("\n" + text + "\n")

    if args.test_send:
        send_telegram("🧪 <b>TEST MESSAGE</b> (nothing saved; the real one comes at 7:45 AM)\n\n" + text)
        print("(test: sent to Telegram, nothing saved)")
        return 0
    if args.dry_run:
        print("(dry run: nothing sent or saved)")
        return 0
    tracker.save(picks_log)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(html.unescape(text).replace("<b>", "**").replace("</b>", "**")
                      .replace("<i>", "_").replace("</i>", "_") + "\n")
    send_telegram(text)
    LAST_SCAN.write_text(today.isoformat() + "\n")
    commit_paths([ROOT / "data", REPORT.parent], f"Daily scan {today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
