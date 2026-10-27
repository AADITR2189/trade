from datetime import datetime

from india_trader.market_calendar import IST
from india_trader.tick import plan


def at(day, hh, mm):
    return datetime(2026, 10, day, hh, mm, tzinfo=IST)


def test_morning_scan_catches_up_when_the_745_slot_was_skipped():
    assert plan(at(27, 7, 30), scanned=False) == (False, None)      # too early
    assert plan(at(27, 7, 52), scanned=False) == (True, None)       # due, before bot hours
    assert plan(at(27, 8, 37), scanned=False) == (True, None)       # 7:45 skipped: still runs
    assert plan(at(27, 8, 37), scanned=True) == (False, None)       # never twice
    assert plan(at(27, 10, 7), scanned=False) == (True, "15:37")    # very late: scan, then bot (5.5 h cap)


def test_bot_online_in_market_hours_with_6_hour_cap():
    assert plan(at(27, 9, 7), scanned=True) == (False, "14:37")     # 5.5 h cap
    assert plan(at(27, 12, 52), scanned=True) == (False, "15:45")
    assert plan(at(27, 15, 52), scanned=True) == (False, None)


def test_weekends_and_holidays_only_answer_commands():
    assert plan(at(31, 10, 7), scanned=False) == (False, None)      # Saturday
    assert plan(datetime(2026, 11, 10, 10, 7, tzinfo=IST), scanned=False) == (False, None)  # Diwali
