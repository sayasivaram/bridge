"""Time zones. Timestamps are stored in UTC ('YYYY-MM-DD HH:MM:SS'); "today" and "this week"
are always in the viewer's own time zone (users.timezone).

Without this, a moment Priya adds at 9pm in New Haven (01:00 UTC the next day) would land on
tomorrow's date: missing from tonight's call prep, and on Sunday night, in next week's jar.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
STORED = "%Y-%m-%d %H:%M:%S"  # format of every created_at column


def now_utc() -> datetime:
    """The current time (tests replace this to freeze the clock)."""
    return datetime.now(UTC)


def zone(name: str | None) -> ZoneInfo | timezone:
    """The user's time zone, or UTC if it's missing or unknown."""
    try:
        return ZoneInfo(name) if name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def to_stored(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(STORED)


def local_today(tz_name: str | None) -> date:
    return now_utc().astimezone(zone(tz_name)).date()


def local_date(stored_utc: str, tz_name: str | None) -> date:
    """The local calendar date of a stored UTC timestamp."""
    return datetime.strptime(str(stored_utc)[:19], STORED).replace(tzinfo=UTC).astimezone(zone(tz_name)).date()


def utc_bounds(first_day: date, last_day: date, tz_name: str | None) -> tuple[str, str]:
    """UTC [start, end) covering local days first_day..last_day inclusive, for SQL comparisons.

    Uses real local midnights, so days that are 23 or 25 hours long (DST changes) are right.
    """
    tz = zone(tz_name)
    start = datetime.combine(first_day, time(0), tzinfo=tz)
    end = datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=tz)
    return to_stored(start), to_stored(end)
