"""NSE trading days (equity segment).

Holidays from nseindia.com/resources/exchange-communication-holidays, read 2026-10-26.
Add next year's list here when NSE publishes it (usually in December).
"""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

NSE_HOLIDAYS = {
    2026: {
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
        "2026-12-25",
    },
}


def now_ist() -> datetime:
    return datetime.now(IST)


def holidays_known(year: int) -> bool:
    return year in NSE_HOLIDAYS


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in NSE_HOLIDAYS.get(d.year, set())
