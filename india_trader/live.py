"""Live alerts during market hours, for the picks that are still active.

Runs every 15 minutes from 9:15 AM to 3:30 PM India time, plus one closing summary.

    python -m india_trader.live            # the real run (needs internet)
    python -m india_trader.live --dry-run  # print alerts; send nothing, save nothing
    python -m india_trader.live --force    # ignore market hours (testing)

Each alert rings once per pick per day (remembered in data/alert_state.json).
These alerts are reminders. data/picks.csv stays the official record and is updated
from daily prices by the morning run.
"""
import argparse
import json
import sys
from datetime import time as dtime
from pathlib import Path

import requests

from . import data_sources, market_calendar, tracker
from .notify import _inr, send_telegram

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "alert_state.json"
OPEN_TIME, CLOSE_TIME, LAST_RUN = dtime(9, 15), dtime(15, 30), dtime(16, 30)
EVENT_WORDS = {"entry": "entry triggered", "t1": "Target 1 hit", "t2": "Target 2 hit",
               "stop": "stop-loss hit", "breakeven": "back to buy price"}


def load_state(today: str, path: Path = STATE_FILE) -> dict:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("date") != today:
        state = {"date": today, "sent": {}, "summary_sent": False, "warned": False}
    return state


def pick_key(row: dict) -> str:
    return f"{row['symbol']}|{row['signal_date']}"


def check_pick(row: dict, q: dict, sent: list[str]) -> list[tuple[str, str]]:
    """New alerts for one pick, given today's quote and the alerts already sent today."""
    sym, entry, stop, t1, t2 = row["symbol"], row["entry"], row["stop"], row["target1"], row["target2"]
    out = []
    done = {"stop", "t2", "breakeven"}
    if done & set(sent):
        return out
    # Once something happened today, only the current price is safe to use: the day's
    # high or low may have come before that event.
    hi, lo = (q["last"], q["last"]) if sent else (q["high"], q["low"])
    now = f"now {_inr(q['last'])}"

    status = row["status"]
    if status == "WAITING" and "entry" not in sent:
        if q["high"] < entry:
            return out
        where = (now if q["last"] >= entry
                 else f"earlier today, now back at {_inr(q['last'])} (high {_inr(q['high'])})")
        out.append(("entry", f"🟢 <b>{sym}</b>: crossed Buy above {_inr(entry)}, {where}. "
                             f"Entry triggered. Stop-loss {_inr(stop)}, Target 1 {_inr(t1)}."))
        hi, lo = q["last"], q["last"]
    in_trade = status in ("OPEN", "T1_HIT") or out or "entry" in sent
    if not in_trade:
        return out

    past_t1 = status == "T1_HIT" or "t1" in sent
    if not past_t1:
        if lo <= stop:
            out.append(("stop", f"🛑 <b>{sym}</b>: stop-loss {_inr(stop)} hit, {now}. Exit the trade."))
            return out
        if hi >= t1:
            out.append(("t1", f"🎯 <b>{sym}</b>: Target 1 {_inr(t1)} reached, {now}. "
                              f"Book half and move your stop to your buy price."))
            past_t1 = True
            hi, lo = q["last"], q["last"]
    if past_t1:
        be = row.get("fill_price") or entry
        if lo <= be:
            out.append(("breakeven", f"↩️ <b>{sym}</b>: back to your buy price {_inr(be)}, {now}. "
                                     f"Exit the rest at breakeven."))
        elif hi >= t2:
            out.append(("t2", f"🏆 <b>{sym}</b>: Target 2 {_inr(t2)} reached, {now}. Book the rest."))
    return out


def summary(rows: list[dict], quotes: dict, sent: dict) -> str:
    lines = ["<b>📊 Market closed: your picks today</b>"]
    for row in rows:
        q = quotes.get(row["symbol"])
        if not q:
            lines.append(f"{row['symbol']}: price not available")
            continue
        events = sent.get(pick_key(row), [])
        chg = (q["last"] / q["prev_close"] - 1) * 100 if q["prev_close"] else 0
        state = {"WAITING": "waiting for entry", "OPEN": "in trade", "T1_HIT": "half booked"}[row["status"]]
        if events:
            state = "today: " + ", ".join(EVENT_WORDS[e] for e in events)
        ref = row.get("fill_price") or row["entry"]
        vs = f", {(q['last'] / ref - 1) * 100:+.1f}% vs entry" if row["status"] != "WAITING" or events else ""
        lines.append(f"{row['symbol']}: {_inr(q['last'])} ({chg:+.1f}% today{vs}), {state}")
    lines.append("\n<i>Tomorrow morning's message has the official update and new setups.</i>")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print only; do not send or save")
    ap.add_argument("--force", action="store_true", help="run outside market hours (testing)")
    args = ap.parse_args(argv)

    now = market_calendar.now_ist()
    if not args.force:
        if not market_calendar.is_trading_day(now.date()):
            print(f"{now.date()} is not an NSE trading day.")
            return 0
        if not OPEN_TIME <= now.time() <= LAST_RUN:
            print(f"Outside market hours ({now:%H:%M} IST).")
            return 0

    picks = tracker.load()
    active = [r for r in picks.to_dict("records") if r["status"] in tracker.ACTIVE]
    if not active:
        print("No active picks to watch.")
        return 0
    for r in active:
        if r.get("fill_price") != r.get("fill_price"):  # NaN -> None
            r["fill_price"] = None

    state = load_state(now.date().isoformat())
    quotes = {}
    with requests.Session() as session:
        for r in active:
            q = data_sources.fetch_quote(r["symbol"], session=session)
            if q and q["time"].date() == now.date():
                quotes[r["symbol"]] = q
    print(f"{now:%H:%M} IST: live prices for {len(quotes)} of {len(active)} active picks.")

    messages = []
    if not quotes and not state["warned"] and now.time() >= dtime(9, 45):
        messages.append("⚠️ Live prices are not available right now, so intraday alerts are paused. "
                        "The morning message still works from daily prices.")
        state["warned"] = True

    if now.time() < LAST_RUN or args.force:
        for r in active:
            q = quotes.get(r["symbol"])
            if not q:
                continue
            key = pick_key(r)
            for event, text in check_pick(r, q, state["sent"].get(key, [])):
                state["sent"].setdefault(key, []).append(event)
                messages.append(text)

    if now.time() >= CLOSE_TIME and not state["summary_sent"] and quotes:
        messages.append(summary(active, quotes, state["sent"]))
        state["summary_sent"] = True

    for m in messages:
        print(m + "\n")
    if args.dry_run:
        print("(dry run: nothing sent or saved)")
        return 0
    if messages:
        send_telegram("\n\n".join(messages))
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
