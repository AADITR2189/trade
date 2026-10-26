"""Finds swing-trade setups and builds entry / stop-loss / targets for each.

Score out of 100:
  Trend     25  close > EMA20 > EMA50 > EMA200, and EMA50 rising
  Momentum  20  RSI in the 55-68 sweet spot, ADX showing a real trend
  Volume    25  yesterday's volume vs the 20-day average (2x or more = full marks)
  Breakout  30  close at/above the 20-day high, and near the 52-week high
"""
import math

import pandas as pd

from .indicators import add_indicators

MIN_HISTORY = 210  # trading days needed for a reliable 200-day EMA


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _tick_up(price: float, tick: float = 0.05) -> float:
    return round(math.ceil(round(price / tick, 6)) * tick, 2)


def _tick_down(price: float, tick: float = 0.05) -> float:
    return round(math.floor(round(price / tick, 6)) * tick, 2)


def _tick_near(price: float, tick: float = 0.05) -> float:
    return round(round(price / tick) * tick, 2)


def evaluate(df: pd.DataFrame, cfg: dict) -> dict | None:
    """Return the setup for one stock, or None when it fails a must-have rule."""
    if df is None or len(df) < MIN_HISTORY:
        return None
    d = add_indicators(df)
    last = d.iloc[-1]
    close = float(last["close"])

    # --- must-have rules ---------------------------------------------------
    if close < cfg["min_price"]:
        return None
    if pd.isna(last["avg_value20"]) or last["avg_value20"] < cfg["min_avg_traded_value_cr"] * 1e7:
        return None
    if not (close > last["ema20"] > last["ema50"] > last["ema200"]):
        return None
    if not (cfg["rsi_min"] <= last["rsi"] <= cfg["rsi_max"]):
        return None
    if last["adx"] < cfg["adx_min"]:
        return None
    extension = (close / last["ema20"] - 1) * 100
    if extension > cfg["max_extension_pct"]:
        return None
    atr_pct = last["atr"] / close * 100
    if atr_pct > cfg["max_atr_pct"]:
        return None

    # --- score ---------------------------------------------------------------
    ema50_10d_ago = d["ema50"].iloc[-11]
    ema50_slope = (last["ema50"] / ema50_10d_ago - 1) * 100
    trend = 15 + 10 * _clamp(ema50_slope / 3)

    r = last["rsi"]
    if 55 <= r <= 68:
        rsi_part = 1.0
    elif r < 55:
        rsi_part = _clamp((r - 50) / 5)
    else:
        rsi_part = _clamp((75 - r) / 7)
    momentum = 10 * rsi_part + 10 * _clamp((last["adx"] - 20) / 20)

    vol_ratio = last["volume"] / last["avg_vol20"] if last["avg_vol20"] else 0
    volume = 25 * _clamp(vol_ratio - 1)

    near_20 = close / last["high20"]
    breakout = 20 * (1.0 if near_20 >= 1 else _clamp((near_20 - 0.97) / 0.03))
    if not pd.isna(last["high252"]):
        breakout += 10 * _clamp((close / last["high252"] - 0.85) / 0.15)

    score = round(trend + momentum + volume + breakout, 1)

    # --- trade plan ----------------------------------------------------------
    entry = _tick_up(float(last["high"]) * (1 + cfg["entry_buffer_pct"] / 100))
    stop = _tick_down(entry - cfg["stop_atr_multiple"] * float(last["atr"]))
    risk = entry - stop
    target1 = _tick_near(entry + cfg["target1_r"] * risk)
    target2 = _tick_near(entry + cfg["target2_r"] * risk)

    risk_money = cfg["capital_inr"] * cfg["risk_per_trade_pct"] / 100
    max_money = cfg["capital_inr"] * cfg["max_position_pct"] / 100
    qty = int(min(risk_money // risk, max_money // entry)) if risk > 0 else 0

    reasons = []
    if near_20 >= 0.9995:
        reasons.append("closed at a 20-day high")
    else:
        reasons.append(f"{(1 - near_20) * 100:.1f}% below its 20-day high")
    reasons.append(f"volume {vol_ratio:.1f}x average")
    reasons.append(f"RSI {r:.0f}")
    reasons.append(f"ADX {last['adx']:.0f}")

    return {
        "date": str(d.index[-1]),
        "close": round(close, 2),
        "score": score,
        "parts": {"trend": round(trend, 1), "momentum": round(momentum, 1),
                  "volume": round(volume, 1), "breakout": round(breakout, 1)},
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "risk_pct": round(risk / entry * 100, 2),
        "qty": qty,
        "rsi": round(float(r), 1),
        "adx": round(float(last["adx"]), 1),
        "vol_ratio": round(float(vol_ratio), 2),
        "why": ", ".join(reasons),
    }


def scan(prices: dict[str, pd.DataFrame], universe: list[dict], cfg: dict,
         exclude: set[str] = frozenset()) -> list[dict]:
    """Rank every stock that passes the rules; best first, with a per-sector limit."""
    info = {s["symbol"]: s for s in universe}
    found = []
    for sym, df in prices.items():
        if sym not in info or sym in exclude:
            continue
        setup = evaluate(df, cfg)
        if setup:
            found.append({"symbol": sym, "name": info[sym]["name"],
                          "sector": info[sym]["sector"], **setup})
    found = [s for s in found if s["score"] >= cfg.get("min_score", 0)]
    found.sort(key=lambda x: x["score"], reverse=True)

    picks, per_sector = [], {}
    for s in found:
        if per_sector.get(s["sector"], 0) >= cfg["max_per_sector"]:
            continue
        picks.append(s)
        per_sector[s["sector"]] = per_sector.get(s["sector"], 0) + 1
        if len(picks) == cfg["top_n"]:
            break
    return picks


def market_mood(nifty: pd.DataFrame | None) -> dict:
    """Simple read of the Nifty 50 trend: Bullish / Neutral / Bearish."""
    if nifty is None or len(nifty) < 60:
        return {"mood": "Unknown", "detail": "Nifty 50 data not available"}
    d = add_indicators(nifty)
    last = d.iloc[-1]
    close, e20, e50 = last["close"], last["ema20"], last["ema50"]
    if close > e20 > e50:
        mood = "Bullish"
    elif close < e20 < e50:
        mood = "Bearish"
    else:
        mood = "Neutral"
    change = (close / d["close"].iloc[-2] - 1) * 100
    return {"mood": mood, "close": round(float(close), 2), "change_pct": round(float(change), 2),
            "rsi": round(float(last["rsi"]), 1),
            "detail": f"Nifty 50 {close:,.0f} ({change:+.2f}%), RSI {last['rsi']:.0f}"}
