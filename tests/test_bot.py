from datetime import datetime

import pandas as pd
import pytest

from india_trader import bot, tracker
from india_trader.main import load_config
from india_trader.market_calendar import IST

CFG = load_config()
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=IST)  # a Tuesday, market open


def q(last, prev=100.0):
    return {"last": last, "high": last, "low": last, "prev_close": prev, "time": pd.Timestamp(NOW)}


@pytest.fixture
def prices(monkeypatch):
    table = {"IRFC": q(138.0), "CYIENT": q(1260.0)}
    monkeypatch.setattr(bot, "quote", lambda s: table.get(s))
    return table


def picks():
    return tracker._text_columns(pd.DataFrame([{
        "signal_date": "2026-10-23", "symbol": "CYIENT", "name": "Cyient Ltd.", "sector": "IT", "score": 90,
        "entry": 1270.55, "stop": 1190.1, "target1": 1431.45, "target2": 1511.9, "qty": 3,
        "status": "WAITING"}], columns=tracker.COLUMNS))


def state():
    return {"offset": None, "stocks": {}, "summary_date": None}


@pytest.mark.parametrize("text,cmd", [
    ("IRFC start", "start"), ("irfc START", "start"), ("start irfc", "start"), ("M&M start", "start"),
    ("IRFC stop", "stop"), ("stop IRFC", "stop"), ("stop all", "stop_all"),
    ("watch it", "watch"), ("Watch It", "watch"), ("price IRFC", "price"), ("/start", "help"),
    ("buy everything now", "unknown"),
])
def test_parse(text, cmd):
    assert bot.parse(text)[0] == cmd


def test_parse_custom_levels():
    cmd, a = bot.parse("IRFC start 140 132 155.5")
    assert cmd == "start" and a["levels"] == {"entry": 140, "stop": 132, "target1": 155.5, "target2": None}


def test_start_with_pick_levels_then_stop(prices):
    s = state()
    reply = bot.handle("CYIENT start", s, CFG, NOW, picks())
    assert "ON for <b>CYIENT</b>" in reply and "morning list" in reply
    assert s["stocks"]["CYIENT"]["levels"]["entry"] == 1270.55
    assert "Stopped" in bot.handle("cyient stop", s, CFG, NOW, picks())
    assert s["stocks"] == {}


def test_start_unknown_symbol(prices):
    s = state()
    assert "Couldn't find" in bot.handle("XYZXYZ start", s, CFG, NOW, picks())
    assert s["stocks"] == {}


def test_bad_custom_levels_rejected(prices):
    s = state()
    assert "Levels must be" in bot.handle("IRFC start 140 150 155", s, CFG, NOW, picks())


def test_watch_it_lists_all(prices):
    s = state()
    bot.handle("IRFC start", s, CFG, NOW, picks())
    bot.handle("CYIENT start", s, CFG, NOW, picks())
    reply = bot.handle("watch it", s, CFG, NOW, picks())
    assert "IRFC" in reply and "CYIENT" in reply and "₹138.00" in reply


def test_level_alerts_sequence():
    w = {"since": "x", "fired": [], "last_alert_price": 99.0,
         "levels": {"entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 115.0}}
    assert bot.check_stock("ABC", w, 99.5, 2) == []
    assert "crossed your buy price" in bot.check_stock("ABC", w, 100.5, 2)[0]
    assert bot.check_stock("ABC", w, 101.0, 2) == []
    assert "Target 1" in bot.check_stock("ABC", w, 110.2, 2)[0]
    assert "Target 2" in bot.check_stock("ABC", w, 115.1, 2)[0]
    assert w["fired"] == ["entry", "t1", "t2"]
    # Trade finished: only % moves from now on.
    assert "+2.6%" in bot.check_stock("ABC", w, 118.1, 2)[0]


def test_stop_loss_and_move_alerts():
    w = {"since": "x", "fired": ["entry"], "last_alert_price": 100.0,
         "levels": {"entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 115.0}}
    assert "-2.0%" in bot.check_stock("ABC", w, 98.0, 2)[0]
    assert bot.check_stock("ABC", w, 97.0, 2) == []
    assert "stop-loss" in bot.check_stock("ABC", w, 94.9, 2)[0]


def test_moves_only_without_levels():
    w = {"since": "x", "fired": [], "last_alert_price": 200.0, "levels": None}
    assert bot.check_stock("ABC", w, 203.0, 2) == []
    assert "📈" in bot.check_stock("ABC", w, 204.1, 2)[0]
    assert w["last_alert_price"] == 204.1


def test_only_owner_can_command(monkeypatch, tmp_path, prices):
    sent = []
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "w.json")
    monkeypatch.setattr(bot, "send_telegram", lambda m: sent.append(m))
    monkeypatch.setattr(bot.tracker, "load", picks)
    monkeypatch.setattr(bot, "get_updates", lambda off, wait_seconds=0: [
        {"update_id": 5, "message": {"chat": {"id": 999}, "text": "IRFC start"}},
        {"update_id": 6, "message": {"chat": {"id": 111}, "text": "CYIENT start"}}])
    monkeypatch.setattr(bot.market_calendar, "now_ist", lambda: NOW)
    assert bot.main(["--once"]) == 0
    saved = bot.load_state(tmp_path / "w.json")
    assert list(saved["stocks"]) == ["CYIENT"] and saved["offset"] == 7 and len(sent) == 1
