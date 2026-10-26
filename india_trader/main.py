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

from . import data_sources, market_calendar, scanner, tracker
from .notify import build_message, send_telegram

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "latest.md"


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
    args = ap.parse_args(argv)
    if args.demo:
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

    if not args.demo and data_sources.refresh_universe():
        print("Nifty 500 list refreshed.")
    universe = data_sources.load_universe()
    if args.limit:
        universe = universe[: args.limit]
    picks_log = tracker.load()
    symbols = sorted({s["symbol"] for s in universe} | tracker.active_symbols(picks_log))

    print(f"Fetching prices for {len(symbols)} stocks + Nifty 50 ...")
    if args.demo:
        from .demo import fake_prices
        prices = fake_prices(symbols, today)
        note = "DEMO: made-up prices, not real stocks data."
    else:
        prices = data_sources.fetch_many(symbols + [data_sources.NIFTY50])
    drop_unfinished_candle(prices, now)
    nifty = prices.pop(data_sources.NIFTY50, None)
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
    if mood["mood"] == "Bearish" and cfg["skip_new_picks_when_market_bearish"]:
        picks = []
        note = (note + " " if note else "") + "No new picks: market is bearish (your setting)."
    else:
        fresh = {s: df for s, df in prices.items() if str(df.index[-1]) == data_date}
        picks = scanner.scan(fresh, universe, cfg, exclude=tracker.active_symbols(picks_log))

    picks_log = tracker.add(picks_log, picks)
    stats = tracker.stats(picks_log)
    text = build_message(today.strftime("%a %d %b %Y"), data_date, mood, picks, events, stats, cfg, note)
    print("\n" + text + "\n")

    if args.dry_run:
        print("(dry run: nothing sent or saved)")
        return 0
    tracker.save(picks_log)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(html.unescape(text).replace("<b>", "**").replace("</b>", "**")
                      .replace("<i>", "_").replace("</i>", "_") + "\n")
    send_telegram(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
