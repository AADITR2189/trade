from datetime import date, timedelta

import numpy as np
import pandas as pd

from india_trader import indicators, market_calendar, scanner, tracker
from india_trader.demo import fake_series
from india_trader.main import load_config
from india_trader.notify import _inr

CFG = {**load_config(), "top_n": 5, "max_entry_price": None}


def candles(rows, start=date(2026, 11, 2)):
    idx = [start + timedelta(days=i) for i in range(len(rows))]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


PICK = {"entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 115.0, "status": "WAITING"}


def test_calendar():
    assert not market_calendar.is_trading_day(date(2026, 11, 10))  # Diwali Balipratipada
    assert not market_calendar.is_trading_day(date(2026, 10, 31))  # Saturday
    assert market_calendar.is_trading_day(date(2026, 10, 27))


def test_inr_grouping():
    assert _inr(1234567.5) == "₹12,34,567.50"
    assert _inr(100000, 0) == "₹1,00,000"


def test_rsi_bounds_and_trend():
    up = pd.Series(np.linspace(100, 200, 100))
    assert indicators.rsi(up).iloc[-1] > 95
    assert indicators.rsi(-up + 400).iloc[-1] < 5


def test_expires_when_entry_never_hit():
    s = tracker.simulate(PICK, candles([[98, 99, 97, 98]] * 3), CFG)
    assert s["status"] == "EXPIRED"


def test_stop_loss():
    s = tracker.simulate(PICK, candles([[99, 101, 98, 100], [99, 99, 94, 95]]), CFG)
    assert s["status"] == "SL_HIT" and s["exit_price"] == 95 and s["result_pct"] == -5.0


def test_gap_down_exits_at_open_not_stop():
    s = tracker.simulate(PICK, candles([[99, 101, 98, 100], [90, 91, 89, 90]]), CFG)
    assert s["status"] == "SL_HIT" and s["exit_price"] == 90


def test_gap_up_fills_at_open():
    s = tracker.simulate(PICK, candles([[103, 104, 102, 103]]), CFG)
    assert s["status"] == "OPEN" and s["fill_price"] == 103


def test_target2_books_half_at_each_target():
    s = tracker.simulate(PICK, candles([[99, 101, 98, 100], [105, 111, 104, 110], [111, 116, 110, 115]]), CFG)
    assert s["status"] == "T2_HIT" and s["result_pct"] == 12.5


def test_breakeven_after_target1():
    s = tracker.simulate(PICK, candles([[99, 101, 98, 100], [105, 111, 104, 110], [104, 105, 99, 100]]), CFG)
    assert s["status"] == "BREAKEVEN_AFTER_T1" and s["result_pct"] == 5.0


def test_stop_checked_before_target_on_same_day():
    s = tracker.simulate(PICK, candles([[99, 101, 98, 100], [100, 111, 94, 100]]), CFG)
    assert s["status"] == "SL_HIT"


def test_scanner_finds_strong_breakout_and_plan_is_consistent():
    end = date(2026, 10, 23)
    prices = {"GOOD": fake_series(3, end, trend=0.0025, breakout=True),
              "FLAT": fake_series(1, end, trend=-0.001)}
    uni = [{"symbol": "GOOD", "name": "Good Ltd", "sector": "X"},
           {"symbol": "FLAT", "name": "Flat Ltd", "sector": "X"}]
    picks = scanner.scan(prices, uni, CFG)
    assert [p["symbol"] for p in picks] == ["GOOD"]
    p = picks[0]
    assert p["stop"] < p["close"] < p["entry"] < p["target1"] < p["target2"]
    assert 0 <= p["score"] <= 100
    risk = p["entry"] - p["stop"]
    assert p["qty"] * risk <= CFG["capital_inr"] * CFG["risk_per_trade_pct"] / 100 + 1e-6
    assert p["qty"] * p["entry"] <= CFG["capital_inr"] * CFG["max_position_pct"] / 100


def test_tracker_update_and_stats():
    log = tracker.add(tracker.load(path=__import__("pathlib").Path("/nonexistent.csv")),
                      [{"date": "2026-11-01", "symbol": "ABC", "name": "A", "sector": "X", "score": 80,
                        **{k: PICK[k] for k in ("entry", "stop", "target1", "target2")}, "qty": 10}])
    prices = {"ABC": candles([[99, 101, 98, 100], [99, 99, 94, 95]])}
    log, events = tracker.update(log, prices, CFG)
    assert log.loc[0, "status"] == "SL_HIT" and "stop-loss" in events[0]
    st = tracker.stats(log)
    assert st["closed"] == 1 and st["losses"] == 1 and st["win_rate"] == 0.0


def test_min_score_filters_weak_setups():
    end = date(2026, 10, 23)
    prices = {"GOOD": fake_series(3, end, trend=0.0025, breakout=True)}
    uni = [{"symbol": "GOOD", "name": "Good Ltd", "sector": "X"}]
    score = scanner.scan(prices, uni, {**CFG, "min_score": 0})[0]["score"]
    assert scanner.scan(prices, uni, {**CFG, "min_score": score + 1}) == []


def test_max_entry_price_cap():
    end = date(2026, 10, 23)
    df = fake_series(3, end, trend=0.0025, breakout=True)
    p = scanner.evaluate(df, CFG)
    assert p is not None
    assert scanner.evaluate(df, {**CFG, "max_entry_price": p["entry"] - 1}) is None
    assert scanner.evaluate(df, {**CFG, "max_entry_price": p["entry"] + 1}) is not None


def test_unknown_sector_has_no_sector_limit():
    picks = [{"symbol": f"S{i}", "sector": "", "score": 90 - i} for i in range(4)]
    assert len(scanner.select(picks, {**CFG, "max_per_sector": 2, "top_n": 10})) == 4


def test_parse_all_nse_keeps_eq_series_only():
    from india_trader.data_sources import parse_all_nse
    text = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n"
            "IRFC,Indian Railway Finance Corporation Limited,EQ,29-JAN-2021\n"
            "XYZ,Trade For Trade Ltd,BE,01-JAN-2020\n")
    assert parse_all_nse(text) == [{"symbol": "IRFC", "name": "Indian Railway Finance Corporation Limited"}]


def test_small_capital_sizing_explains_zero_shares():
    end = date(2026, 10, 23)
    df = fake_series(3, end, trend=0.0025, breakout=True)
    p = scanner.evaluate(df, {**CFG, "capital_inr": 10000, "risk_per_trade_pct": 1, "max_position_pct": 50})
    risk = p["entry"] - p["stop"]
    assert p["qty"] == int(min(100 // risk, 5000 // p["entry"]))
    tiny = scanner.evaluate(df, {**CFG, "capital_inr": 500, "risk_per_trade_pct": 1, "max_position_pct": 50})
    assert tiny["qty"] == 0 and "limit" in tiny["qty_note"]
