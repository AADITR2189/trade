from datetime import datetime

from india_trader.market_calendar import IST
from india_trader.tick import plan


def at(day, hh, mm):
    return datetime(2026, 10, day, hh, mm, tzinfo=IST)


def test_morning_scan_catches_up_when_the_745_slot_was_skipped():
    assert plan(at(27, 7, 30), scanned=False) == (False, None)      # too early
    assert plan(at(27, 7, 52), scanned=False) == (True, "13:22")    # due; bot stays online
    assert plan(at(27, 8, 37), scanned=False) == (True, "14:07")    # 7:45 skipped: still runs
    assert plan(at(27, 8, 37), scanned=True) == (False, "14:07")    # never twice
    assert plan(at(27, 10, 7), scanned=False) == (True, "15:37")    # very late: scan, then bot (5.5 h cap)


def test_bot_online_in_market_hours_with_6_hour_cap():
    assert plan(at(27, 9, 7), scanned=True) == (False, "14:37")     # 5.5 h cap
    assert plan(at(27, 12, 52), scanned=True) == (False, "15:45")
    assert plan(at(27, 15, 52), scanned=True) == (False, None)


def test_weekends_and_holidays_only_answer_commands():
    assert plan(at(31, 10, 7), scanned=False) == (False, None)      # Saturday
    assert plan(datetime(2026, 11, 10, 10, 7, tzinfo=IST), scanned=False) == (False, None)  # Diwali


def test_failed_scan_alerts_once_and_keeps_session_short(monkeypatch, tmp_path):
    from india_trader import tick
    sent, bot_args = [], []
    monkeypatch.setattr(tick, "SCAN_ALERT", tmp_path / "alert.txt")
    monkeypatch.setattr(tick.market_calendar, "now_ist", lambda: at(27, 9, 37))
    monkeypatch.setattr(tick.morning, "scanned_today", lambda d: False)
    monkeypatch.setattr(tick.morning, "main", lambda argv: 1 / 0)
    monkeypatch.setattr(tick, "send_telegram", lambda m: sent.append(m))
    monkeypatch.setattr(tick.bot, "main", lambda argv: bot_args.append(argv) or 0)
    tick.run()
    tick.run()
    assert len(sent) == 1 and "failed" in sent[0]
    assert bot_args == [["--until", "09:51"], ["--until", "09:51"]]


def _run_tick(monkeypatch, tmp_path, start, end, until_expected):
    from india_trader import tick
    clock = iter([start, start, end, end])
    monkeypatch.setattr(tick, "NEXT_FLAG", tmp_path / "next")
    monkeypatch.setattr(tick.market_calendar, "now_ist", lambda: next(clock))
    monkeypatch.setattr(tick.morning, "scanned_today", lambda d: True)
    seen = []
    monkeypatch.setattr(tick.bot, "main", lambda argv: seen.append(argv) or 0)
    tick.run()
    assert seen == [until_expected]
    return (tmp_path / "next").exists()


def test_session_hands_over_only_after_running_to_its_cap(monkeypatch, tmp_path):
    # 8:31 start -> online until 14:01; at 14:01 it hands over to a new session.
    assert _run_tick(monkeypatch, tmp_path, at(28, 8, 31), at(28, 14, 1), ["--until", "14:01"])


def test_no_handover_at_close_on_holidays_or_after_early_exit(monkeypatch, tmp_path):
    assert not _run_tick(monkeypatch, tmp_path, at(28, 12, 0), at(28, 15, 45), ["--until", "15:45"])
    assert not _run_tick(monkeypatch, tmp_path / "b", at(28, 8, 31), at(28, 8, 32), ["--until", "14:01"])
    hol = datetime(2026, 11, 10, 10, 0, tzinfo=IST)
    assert not _run_tick(monkeypatch, tmp_path / "c", hol, hol, ["--once"])
