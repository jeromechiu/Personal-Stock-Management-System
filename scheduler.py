"""Taiwan stock-market analysis schedule."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(13, 30)


def next_taiwan_analysis_time(
    now: datetime,
    interval_seconds: int = 900,
    *,
    include_current: bool = True,
) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    moment = (
        now.replace(tzinfo=TAIPEI)
        if now.tzinfo is None
        else now.astimezone(TAIPEI)
    )
    session_date = moment.date()
    while session_date.weekday() >= 5:
        session_date += timedelta(days=1)

    session_open = datetime.combine(session_date, MARKET_OPEN, tzinfo=TAIPEI)
    session_close = datetime.combine(session_date, MARKET_CLOSE, tzinfo=TAIPEI)
    if moment < session_open:
        return session_open
    if moment > session_close:
        return _next_weekday_open(session_date + timedelta(days=1))

    elapsed = (moment - session_open).total_seconds()
    slot = int(elapsed // interval_seconds)
    candidate = session_open + timedelta(seconds=slot * interval_seconds)
    if candidate < moment or (candidate == moment and not include_current):
        candidate += timedelta(seconds=interval_seconds)

    if candidate <= session_close:
        return candidate
    return _next_weekday_open(session_date + timedelta(days=1))


def _next_weekday_open(day: date) -> datetime:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, MARKET_OPEN, tzinfo=TAIPEI)
