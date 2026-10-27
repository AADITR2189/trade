"""Telegram bot: live updates only for the stocks you choose.

Commands (send them to the bot in Telegram, capitals or not):
    IRFC start              watch IRFC; uses the morning list's levels if it was a pick
    IRFC start 140 132 155  watch IRFC with your own buy, stop-loss and target prices
    IRFC stop               stop watching IRFC
    stop all                stop watching everything
    watch it                current price of every stock you watch
    price IRFC              current price of any NSE stock, watched or not
    help                    this list

While watching, you get a message when the price
  - crosses the buy price, hits the stop-loss, Target 1, Target 2, or falls back to the buy
    price after Target 1 (only when there are levels), and
  - moves move_alert_pct (default 2%) up or down since the last message about that stock,
plus a closing line for each watched stock after 3:30 PM. Watching lasts until you send stop.

    python -m india_trader.bot --once           # answer waiting messages, check prices once
    python -m india_trader.bot --until 15:45    # stay online until 3:45 PM India time
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd
import requests

from . import data_sources, market_calendar, tracker
from .gitsave import commit_paths
from .notify import _inr, get_updates, send_telegram

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "watchlist.json"
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)
CLOSED_EVENTS = {"stop", "t2", "breakeven"}

HELP = (
    "<b>Commands</b>\n"
    "<code>IRFC start</code>: live updates for IRFC (levels from the morning list if it was a pick)\n"
    "<code>IRFC start 140 132 155</code>: your own buy, stop-loss and target\n"
    "<code>IRFC stop</code>: stop updates for IRFC\n"
    "<code>stop all</code>: stop everything\n"
    "<code>watch it</code>: current price of all your watched stocks\n"
    "<code>price IRFC</code>: current price of any NSE stock\n\n"
    "You'll get a message when a watched stock crosses its buy price, stop-loss or targets, "
    "or moves {pct}% since the last update, plus a closing line after 3:30 PM."
)


# --- state -------------------------------------------------------------------------

def load_state(path: Path | None = None) -> dict:
    path = path or STATE_FILE
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    state.setdefault("offset", None)
    state.setdefault("stocks", {})
    state.setdefault("summary_date", None)
    return state


def save_state(state: dict, path: Path | None = None) -> None:
    (path or STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def commit_state(message: str) -> None:
    """On GitHub, save the watchlist into the repository so the next run remembers it."""
    commit_paths([STATE_FILE], message)


# --- prices ------------------------------------------------------------------------

def quote(symbol: str) -> dict | None:
    return data_sources.fetch_quote(symbol)


def market_open(now: datetime) -> bool:
    return market_calendar.is_trading_day(now.date()) and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def price_line(sym: str, q: dict | None, w: dict | None, now: datetime) -> str:
    if not q:
        return f"<b>{sym}</b>: price not available right now"
    chg = (q["last"] / q["prev_close"] - 1) * 100 if q["prev_close"] else 0
    live = q["time"].date() == now.date() and market_open(now)
    when = f"at {q['time']:%H:%M}" if live else f"last close {q['time']:%d %b}"
    line = f"<b>{sym}</b> {_inr(q['last'])} ({chg:+.2f}% today, {when})"
    if w and w.get("levels"):
        lv = w["levels"]
        line += f"\n   {phase_text(w)} · buy {_inr(lv['entry'])} · SL {_inr(lv['stop'])} · T1 {_inr(lv['target1'])}"
        if lv.get("target2"):
            line += f" · T2 {_inr(lv['target2'])}"
        ref = lv["entry"] if "entry" in w["fired"] else None
        if ref:
            line += f"\n   {(q['last'] / ref - 1) * 100:+.1f}% vs buy price"
    if w:
        line += f"\n   watching since {w['since']}"
    return line


def phase_text(w: dict) -> str:
    f = set(w["fired"])
    if f & CLOSED_EVENTS:
        return "trade finished"
    if "t1" in f:
        return "half booked"
    if "entry" in f:
        return "in trade"
    return "waiting for buy price"


# --- commands ----------------------------------------------------------------------

SYMBOL = r"([A-Za-z0-9&\-]{1,20})"
NUM = r"(\d+(?:\.\d+)?)"


def parse(text: str) -> tuple[str, dict]:
    t = " ".join((text or "").strip().split())
    low = t.lower()
    if low in ("/start", "/help", "help", "hi", "hello"):
        return "help", {}
    if low in ("watch it", "watch", "watchlist", "status", "/watch", "list"):
        return "watch", {}
    if low in ("stop all", "stopall", "/stopall"):
        return "stop_all", {}
    m = re.fullmatch(rf"(?:price|watch|quote)\s+{SYMBOL}", t, re.I)
    if m:
        return "price", {"symbol": m.group(1).upper()}
    m = re.fullmatch(rf"{SYMBOL}\s+start(?:\s+{NUM}\s+{NUM}\s+{NUM})?", t, re.I) or \
        re.fullmatch(rf"start\s+{SYMBOL}(?:\s+{NUM}\s+{NUM}\s+{NUM})?", t, re.I)
    if m:
        args = {"symbol": m.group(1).upper()}
        if m.group(2):
            args["levels"] = {"entry": float(m.group(2)), "stop": float(m.group(3)),
                              "target1": float(m.group(4)), "target2": None}
        return "start", args
    m = re.fullmatch(rf"{SYMBOL}\s+stop", t, re.I) or re.fullmatch(rf"stop\s+{SYMBOL}", t, re.I)
    if m:
        return "stop", {"symbol": m.group(1).upper()}
    return "unknown", {}


def levels_from_picks(symbol: str, picks: pd.DataFrame) -> tuple[dict | None, list[str], str]:
    """Levels and starting phase from the latest active pick of this stock, if any."""
    if not len(picks):
        return None, [], ""
    rows = picks[(picks["symbol"] == symbol) & picks["status"].isin(tracker.ACTIVE)]
    if not len(rows):
        return None, [], ""
    r = rows.sort_values("signal_date").iloc[-1]
    lv = {k: float(r[k]) for k in ("entry", "stop", "target1", "target2")}
    fired = {"WAITING": [], "OPEN": ["entry"], "T1_HIT": ["entry", "t1"]}[r["status"]]
    return lv, fired, f"from the morning list of {r['signal_date']}"


def handle(text: str, state: dict, cfg: dict, now: datetime, picks: pd.DataFrame) -> str:
    cmd, a = parse(text)
    stocks = state["stocks"]
    if cmd == "help":
        return HELP.format(pct=cfg["move_alert_pct"])
    if cmd == "watch":
        if not stocks:
            return "You're not watching any stock. Send e.g. <code>IRFC start</code>."
        return "\n\n".join(price_line(s, quote(s), w, now) for s, w in sorted(stocks.items()))
    if cmd == "price":
        return price_line(a["symbol"], quote(a["symbol"]), stocks.get(a["symbol"]), now)
    if cmd == "stop_all":
        if not stocks:
            return "Nothing to stop."
        names = ", ".join(sorted(stocks))
        stocks.clear()
        return f"⏹ Stopped live updates for {names}."
    if cmd == "stop":
        if stocks.pop(a["symbol"], None) is None:
            return f"You weren't watching {a['symbol']}."
        return f"⏹ Stopped live updates for {a['symbol']}."
    if cmd == "start":
        sym = a["symbol"]
        q = quote(sym)
        if not q:
            return f"Couldn't find a price for {sym} on NSE. Check the symbol (as shown in Kite)."
        if "levels" in a:
            lv, fired, src = a["levels"], [], "your levels"
            if not lv["stop"] < lv["entry"] < lv["target1"]:
                return "Levels must be: buy, then a lower stop-loss, then a higher target. " \
                       "Example: <code>IRFC start 140 132 155</code>"
        else:
            lv, fired, src = levels_from_picks(sym, picks)
        stocks[sym] = {"since": now.strftime("%d %b %Y"), "levels": lv, "fired": fired,
                       "last_alert_price": q["last"]}
        msg = f"▶️ Live updates ON for <b>{sym}</b>"
        msg += f" ({src})" if lv else " (no buy/stop/target levels: moves only)"
        msg += f".\n{price_line(sym, q, stocks[sym], now)}\n"
        msg += f"You'll hear from me on a {cfg['move_alert_pct']}% move"
        msg += " or when a level is crossed" if lv else ""
        msg += f". Send <code>{sym} stop</code> to end."
        return msg
    return "Sorry, I didn't understand. Send <code>help</code> to see the commands."


# --- price alerts ------------------------------------------------------------------

def check_stock(sym: str, w: dict, price: float, move_pct: float) -> list[str]:
    """New alerts for one watched stock at this price. Updates w in place."""
    out = []
    lv, fired = w.get("levels"), w["fired"]
    if lv and not set(fired) & CLOSED_EVENTS:
        if "entry" not in fired:
            if price >= lv["entry"]:
                fired.append("entry")
                out.append(f"🟢 <b>{sym}</b> crossed your buy price {_inr(lv['entry'])}: now {_inr(price)}. "
                           f"Stop-loss {_inr(lv['stop'])}, Target 1 {_inr(lv['target1'])}.")
        elif "t1" not in fired:
            if price <= lv["stop"]:
                fired.append("stop")
                out.append(f"🛑 <b>{sym}</b> hit the stop-loss {_inr(lv['stop'])}: now {_inr(price)}. Exit.")
            elif price >= lv["target1"]:
                fired.append("t1")
                nxt = (f"Book half, move your stop to {_inr(lv['entry'])}, next target {_inr(lv['target2'])}."
                       if lv.get("target2") else "Consider booking profit.")
                out.append(f"🎯 <b>{sym}</b> reached Target 1 {_inr(lv['target1'])}: now {_inr(price)}. {nxt}")
                if not lv.get("target2"):
                    fired.append("t2")
        else:
            if price <= lv["entry"]:
                fired.append("breakeven")
                out.append(f"↩️ <b>{sym}</b> fell back to your buy price {_inr(lv['entry'])}: now "
                           f"{_inr(price)}. Exit the rest at no loss.")
            elif lv.get("target2") and price >= lv["target2"]:
                fired.append("t2")
                out.append(f"🏆 <b>{sym}</b> reached Target 2 {_inr(lv['target2'])}: now {_inr(price)}. Book the rest.")
    if out:
        if set(fired) & CLOSED_EVENTS:
            out[-1] += f" I'll keep sending {move_pct}% moves until you send <code>{sym} stop</code>."
        w["last_alert_price"] = price
        return out
    ref = w.get("last_alert_price") or price
    move = (price / ref - 1) * 100
    if abs(move) >= move_pct:
        arrow = "📈" if move > 0 else "📉"
        out.append(f"{arrow} <b>{sym}</b> {move:+.1f}% since my last update: now {_inr(price)}.")
        w["last_alert_price"] = price
    return out


def check_prices(state: dict, cfg: dict, now: datetime) -> list[str]:
    msgs = []
    for sym, w in sorted(state["stocks"].items()):
        q = quote(sym)
        if q and q["time"].date() == now.date():
            msgs += check_stock(sym, w, q["last"], cfg["move_alert_pct"])
    return msgs


def closing_summary(state: dict, now: datetime) -> str:
    lines = ["<b>📊 Market closed: your watched stocks</b>"]
    lines += [price_line(s, quote(s), w, now) for s, w in sorted(state["stocks"].items())]
    return "\n\n".join(lines)


# --- main loop ---------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="answer waiting messages, check prices once, exit")
    ap.add_argument("--until", default="", help="stay online until this India time, e.g. 15:45")
    args = ap.parse_args(argv)

    from .main import load_config
    cfg = load_config()
    chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    state = load_state()
    end = None
    if args.until and not args.once:
        hh, mm = map(int, args.until.split(":"))
        end = dtime(hh, mm)
    last_check = 0.0
    print(f"Bot online at {market_calendar.now_ist():%H:%M} IST, watching {sorted(state['stocks'])}")

    while True:
        now = market_calendar.now_ist()
        changed, out = False, []
        wait = 0 if (args.once or end is None) else 20
        updates = get_updates(state["offset"], wait_seconds=wait)
        if updates is None:
            print("Could not reach Telegram.")
            if args.once or end is None:
                return 1
            time.sleep(10)
            updates = []
        if updates:
            picks = tracker.load()
            for u in updates:
                state["offset"] = u["update_id"] + 1
                changed = True
                msg = u.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != chat_id:
                    continue  # only you can command your bot
                print(f"Command: {msg.get('text', '')!r}")
                out.append(handle(msg.get("text", ""), state, cfg, now, picks))

        if state["stocks"] and market_open(now) and time.time() - last_check >= cfg["price_check_seconds"]:
            last_check = time.time()
            alerts = check_prices(state, cfg, now)
            if alerts:
                out += alerts
                changed = True

        today = now.date().isoformat()
        if (state["stocks"] and market_calendar.is_trading_day(now.date())
                and now.time() >= MARKET_CLOSE and state["summary_date"] != today):
            out.append(closing_summary(state, now))
            state["summary_date"] = today
            changed = True

        for m in out:
            send_telegram(m)
        if changed:
            save_state(state)
            commit_state(f"Telegram bot {now:%Y-%m-%d %H:%M}")

        if args.once or end is None or market_calendar.now_ist().time() >= end:
            return 0
        if not market_calendar.is_trading_day(now.date()) and not updates:
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
