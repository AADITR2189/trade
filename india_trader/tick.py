"""One heartbeat that does whatever is due right now. Runs every 15 minutes.

GitHub's scheduler often starts runs late or skips them, so nothing here depends on being
started at an exact time; each tick catches up:
  1. Trading day, after 7:40 AM, morning message not sent yet -> run the morning scan.
  2. Trading day, 7:40 AM - 3:45 PM -> keep the Telegram bot online (live prices every minute
     once the market opens) until 3:45 PM, or for at most 5.5 hours (GitHub stops a job after
     6); the workflow then starts the next tick itself (see tick.yml), so one tick in the
     morning keeps the bot online all day.
  3. Any other time -> answer waiting Telegram commands once.

    python -m india_trader.tick
"""
import sys
import traceback
from datetime import datetime, time as dtime, timedelta

from . import bot, main as morning, market_calendar
from .gitsave import commit_paths
from .notify import send_telegram

SCAN_FROM, SCAN_UNTIL = dtime(7, 40), dtime(15, 30)
BOT_FROM, BOT_UNTIL = dtime(7, 40), dtime(15, 45)
MAX_SESSION = timedelta(hours=5, minutes=30)
SCAN_ALERT = morning.ROOT / "data" / "scan_alert.txt"
NEXT_FLAG = morning.ROOT / ".next_tick"  # tells tick.yml to start the next session


def plan(now: datetime, scanned: bool) -> tuple[bool, str | None]:
    """(run the morning scan?, keep the bot online until 'HH:MM' or None for one quick pass)."""
    trading = market_calendar.is_trading_day(now.date())
    scan = trading and not scanned and SCAN_FROM <= now.time() < SCAN_UNTIL
    if trading and BOT_FROM <= now.time() < BOT_UNTIL:
        end = min(now + MAX_SESSION, now.replace(hour=BOT_UNTIL.hour, minute=BOT_UNTIL.minute,
                                                 second=0, microsecond=0))
        return scan, end.strftime("%H:%M")
    return scan, None


def run() -> int:
    now = market_calendar.now_ist()
    scan, until = plan(now, morning.scanned_today(now.date()))
    print(f"Tick at {now:%a %d %b %H:%M} IST: morning scan {'now' if scan else 'not due'}, "
          f"bot {'online until ' + until if until else 'quick pass'}")
    if scan:
        try:
            morning.main([])
        except Exception as exc:  # tell the user once a day, then keep the bot running
            traceback.print_exc()
            today = now.date().isoformat()
            if _read(SCAN_ALERT) != today:
                send_telegram(f"⚠️ Today's morning scan failed ({type(exc).__name__}: {exc}). "
                              "I'll keep retrying every 15 minutes. Live updates still work.")
                SCAN_ALERT.write_text(today + "\n")
                commit_paths([SCAN_ALERT], f"Morning scan failed {today}")
    # The scan takes a few minutes; re-check the clock before choosing the bot mode.
    later = market_calendar.now_ist()
    retry = scan and not morning.scanned_today(later.date())
    _, until = plan(later, True)
    if until and retry:
        # Keep this session short so the next tick can retry the scan.
        until = min(until, (later + timedelta(minutes=14)).strftime("%H:%M"))
    code = bot.main(["--until", until] if until else ["--once"])
    # Hand over to a new session only when this one ran to its end before 3:45 PM.
    if code == 0 and until and until < BOT_UNTIL.strftime("%H:%M") \
            and market_calendar.now_ist().strftime("%H:%M") >= until:
        NEXT_FLAG.write_text(until + "\n")
    return code


def _read(path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


if __name__ == "__main__":
    sys.exit(run())
