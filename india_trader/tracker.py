"""Keeps score: every pick is followed day by day until it hits a target, the stop-loss,
expires, or runs out of time. Results live in data/picks.csv.

How a pick is followed (on daily candles, so it is an estimate, not your actual fills):
  WAITING  -> bought when the price trades at or above the entry (at the open if it gaps above)
           -> EXPIRED if that does not happen within entry_valid_days
  OPEN     -> SL_HIT at the stop-loss (or the open, if it gaps below the stop)
           -> T1_HIT at target 1: half is booked, the stop on the rest moves to the entry price
  T1_HIT   -> T2_HIT at target 2, or BREAKEVEN_AFTER_T1 if it falls back to the entry
  any open trade is closed at the day's close after max_hold_days (TIMED_OUT)
Same-day ties are counted against you: the stop is checked before the target.
"""
from datetime import date
from pathlib import Path

import pandas as pd

from .notify import _inr

PICKS_FILE = Path(__file__).resolve().parent.parent / "data" / "picks.csv"
COLUMNS = ["signal_date", "symbol", "name", "sector", "score", "entry", "stop", "target1",
           "target2", "qty", "status", "entry_date", "fill_price", "exit_date", "exit_price",
           "result_pct"]
ACTIVE = {"WAITING", "OPEN", "T1_HIT"}
CLOSED = {"SL_HIT", "T2_HIT", "BREAKEVEN_AFTER_T1", "TIMED_OUT"}


TEXT_COLUMNS = ["signal_date", "symbol", "name", "sector", "status", "entry_date", "exit_date"]


def _text_columns(picks: pd.DataFrame) -> pd.DataFrame:
    for c in TEXT_COLUMNS:
        picks[c] = picks[c].astype(object)
    return picks


def load(path: Path = PICKS_FILE) -> pd.DataFrame:
    if path.exists():
        return _text_columns(pd.read_csv(path, dtype={c: str for c in TEXT_COLUMNS}))
    return pd.DataFrame(columns=COLUMNS)


def save(picks: pd.DataFrame, path: Path = PICKS_FILE) -> None:
    picks[COLUMNS].to_csv(path, index=False)


def add(picks: pd.DataFrame, new: list[dict]) -> pd.DataFrame:
    rows = [{
        "signal_date": p["date"], "symbol": p["symbol"], "name": p["name"], "sector": p["sector"],
        "score": p["score"], "entry": p["entry"], "stop": p["stop"], "target1": p["target1"],
        "target2": p["target2"], "qty": p["qty"], "status": "WAITING",
    } for p in new]
    if not rows:
        return picks
    added = pd.DataFrame(rows, columns=COLUMNS)
    return _text_columns(added if picks.empty else pd.concat([picks, added], ignore_index=True))


def active_symbols(picks: pd.DataFrame) -> set[str]:
    return set(picks.loc[picks["status"].isin(ACTIVE), "symbol"]) if len(picks) else set()


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100


def simulate(row: dict, candles: pd.DataFrame, cfg: dict) -> dict:
    """Replay the pick on the candles after its signal day. Returns the updated fields."""
    entry, stop, t1, t2 = row["entry"], row["stop"], row["target1"], row["target2"]
    state = {"status": "WAITING", "entry_date": None, "fill_price": None,
             "exit_date": None, "exit_price": None, "result_pct": None}
    waited = held = 0
    fill = None
    for day, c in candles.iterrows():
        if state["status"] == "WAITING":
            waited += 1
            if c["high"] < entry:
                if waited >= cfg["entry_valid_days"]:
                    state.update(status="EXPIRED", exit_date=str(day))
                    break
                continue
            fill = max(entry, c["open"])
            state.update(status="OPEN", entry_date=str(day), fill_price=round(fill, 2))

        held += 1
        half_t1 = _pct(t1, fill) / 2
        cur_stop = stop if state["status"] == "OPEN" else fill
        if c["low"] <= cur_stop:
            out = cur_stop if str(day) == state["entry_date"] else min(cur_stop, c["open"])
            if state["status"] == "OPEN":
                state.update(status="SL_HIT", result_pct=_pct(out, fill))
            else:
                state.update(status="BREAKEVEN_AFTER_T1", result_pct=half_t1 + _pct(out, fill) / 2)
            state.update(exit_date=str(day), exit_price=round(out, 2))
            break
        if state["status"] == "OPEN" and c["high"] >= t1:
            state["status"] = "T1_HIT"
        if state["status"] == "T1_HIT" and c["high"] >= t2:
            state.update(status="T2_HIT", exit_date=str(day), exit_price=t2,
                         result_pct=half_t1 + _pct(t2, fill) / 2)
            break
        if held >= cfg["max_hold_days"]:
            close = c["close"]
            res = half_t1 + _pct(close, fill) / 2 if state["status"] == "T1_HIT" else _pct(close, fill)
            state.update(status="TIMED_OUT", exit_date=str(day), exit_price=round(close, 2),
                         result_pct=res)
            break
    if state["result_pct"] is not None:
        state["result_pct"] = round(state["result_pct"], 2)
    return state


def _event(row: dict, new: dict) -> str | None:
    s, name = new["status"], row["symbol"]
    if s == row["status"]:
        return None
    if s == "OPEN":
        return f"🟢 {name}: entry triggered at {_inr(new['fill_price'])} ({new['entry_date']})"
    if s == "T1_HIT":
        return f"🎯 {name}: Target 1 {_inr(row['target1'])} reached. Book half, move stop to entry."
    if s == "T2_HIT":
        return f"🏆 {name}: Target 2 {_inr(row['target2'])} reached ({new['result_pct']:+.2f}%)"
    if s == "SL_HIT":
        return f"🛑 {name}: stop-loss hit at {_inr(new['exit_price'])} ({new['result_pct']:+.2f}%)"
    if s == "BREAKEVEN_AFTER_T1":
        return f"↩️ {name}: fell back to entry after Target 1 ({new['result_pct']:+.2f}%)"
    if s == "TIMED_OUT":
        return f"⌛ {name}: time limit reached, closed at {_inr(new['exit_price'])} ({new['result_pct']:+.2f}%)"
    if s == "EXPIRED":
        return f"⚪ {name}: never crossed entry {_inr(row['entry'])}, idea cancelled"
    return None


def update(picks: pd.DataFrame, prices: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.DataFrame, list[str]]:
    events = []
    for i, row in picks.iterrows():
        if row["status"] not in ACTIVE or row["symbol"] not in prices:
            continue
        df = prices[row["symbol"]]
        signal = date.fromisoformat(str(row["signal_date"]))
        candles = df[[d > signal for d in df.index]]
        new = simulate(row.to_dict(), candles, cfg)
        msg = _event(row.to_dict(), new)
        if msg:
            events.append(msg)
        for k, v in new.items():
            picks.at[i, k] = v
    return picks, events


def stats(picks: pd.DataFrame) -> dict:
    closed = picks[picks["status"].isin(CLOSED)] if len(picks) else picks
    res = pd.to_numeric(closed["result_pct"], errors="coerce") if len(closed) else pd.Series(dtype=float)
    return {
        "total_ideas": int(len(picks)),
        "expired": int((picks["status"] == "EXPIRED").sum()) if len(picks) else 0,
        "open": int(picks["status"].isin({"OPEN", "T1_HIT"}).sum()) if len(picks) else 0,
        "waiting": int((picks["status"] == "WAITING").sum()) if len(picks) else 0,
        "closed": int(len(closed)),
        "wins": int((res > 0).sum()),
        "losses": int((res < 0).sum()),
        "win_rate": round(float((res > 0).mean() * 100), 1) if len(res) else None,
        "avg_result_pct": round(float(res.mean()), 2) if len(res) else None,
    }
