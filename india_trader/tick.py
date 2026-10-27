"""One heartbeat that does whatever is due right now. Runs every 15 minutes.

GitHub's scheduler often starts runs late or skips them, so nothing here depends on being
started at an exact time; each tick catches up:
  1. Trading day, after 7:40 AM, morning message not sent yet -> run the morning scan.
  2. Trading day, 9:00 AM - 3:45 PM -> keep the Telegram bot online (live prices every minute)
     until 3:45 PM, or for at most 5.5 hours (GitHub stops a job after 6); the next tick
     takes over.
  3. Any other time -> answer waiting Telegram commands once.

    python -m india_trader.tick
"""
import sys
from datetime import datetime, time as dtime, timedelta

from . import bot, main as morning, market_calendar

SCAN_FROM, SCAN_UNTIL = dtime(7, 40), dtime(15, 30)
BOT_FROM, BOT_UNTIL = dtime(9, 0), dtime(15, 45)
MAX_SESSION = timedelta(hours=5, minutes=30)


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
        except Exception as exc:  # keep the bot running even if the scan fails
            print(f"Morning scan failed: {exc!r}")
    # The scan takes a few minutes; re-check the clock before choosing the bot mode.
    _, until = plan(market_calendar.now_ist(), True)
    return bot.main(["--until", until] if until else ["--once"])


if __name__ == "__main__":
    sys.exit(run())
