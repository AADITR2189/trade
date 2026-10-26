from india_trader.live import check_pick, summary

ROW = {"symbol": "ABC", "signal_date": "2026-10-23", "entry": 100.0, "stop": 95.0,
       "target1": 110.0, "target2": 115.0, "status": "WAITING", "fill_price": None}


def q(last, high, low, prev=99.0):
    return {"last": last, "high": high, "low": low, "prev_close": prev}


def events(row, quote, sent=()):
    return [e for e, _ in check_pick(row, quote, list(sent))]


def test_nothing_before_entry():
    assert events(ROW, q(99, 99.5, 97)) == []


def test_entry_alert_ignores_lows_from_before_entry():
    # Day low 94 came before the price rose to 101: that is not a stop-loss.
    assert events(ROW, q(101, 101.5, 94)) == ["entry"]


def test_entry_then_stop_later_same_day():
    assert events(ROW, q(94.5, 101.5, 94), sent=["entry"]) == ["stop"]


def test_open_trade_uses_day_range():
    row = {**ROW, "status": "OPEN", "fill_price": 100.0}
    assert events(row, q(97, 99, 94.9)) == ["stop"]
    assert events(row, q(108, 110.5, 99)) == ["t1"]


def test_each_alert_rings_once():
    row = {**ROW, "status": "OPEN", "fill_price": 100.0}
    assert events(row, q(108, 110.5, 99), sent=["t1"]) == []
    assert events(row, q(94, 110.5, 93), sent=["stop"]) == []


def test_after_target1_breakeven_and_target2():
    row = {**ROW, "status": "T1_HIT", "fill_price": 100.0}
    assert events(row, q(116, 116, 104)) == ["t2"]
    assert events(row, q(100, 112, 99.5)) == ["breakeven"]


def test_summary_mentions_every_pick():
    text = summary([ROW], {"ABC": q(101, 101.5, 98)}, {"ABC|2026-10-23": ["entry"]})
    assert "ABC" in text and "entry triggered" in text
