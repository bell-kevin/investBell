"""US equity sessions from exchange_calendars; never infer sessions from weekdays.

XNYS regular sessions cover the current US index ETF universe. The maintained
calendar includes exchange holidays, exceptional closures and shortened days.
Keep the dependency current when exchanges announce schedule changes.
"""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo


CALENDAR_NAME = "XNYS"
PUBLICATION_DELAY = timedelta(minutes=15)
NEW_YORK = ZoneInfo("America/New_York")


class CalendarUnavailable(RuntimeError):
    """Session availability cannot be established; do not proceed with trading."""


@lru_cache(maxsize=16)
def _calendar(first_year, last_year):
    try:
        import exchange_calendars
        # Explicit bounds preserve old research ranges independently of the
        # package's moving default range. Padding also covers holiday endpoints.
        return exchange_calendars.get_calendar(
            CALENDAR_NAME, start=f"{first_year - 1:04d}-01-01",
            end=f"{last_year + 1:04d}-12-31")
    except Exception as error:
        raise CalendarUnavailable(
            "The US equity exchange calendar is unavailable for these dates. "
            "Install the pinned requirements and check the calendar before proceeding.") from error


def _date(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    if type(value) is not date:
        raise ValueError("Calendar dates must be ISO dates or datetime.date values.")
    return value


def expected_sessions(start, end):
    """Return ISO session dates in [start, end), including historical holidays."""
    first, last = _date(start), _date(end)
    if first >= last:
        raise ValueError("end must follow start; end is exclusive.")
    try:
        calendar = _calendar(first.year, last.year)
        return tuple(day.date().isoformat() for day in calendar.sessions_in_range(
            first.isoformat(), (last - timedelta(days=1)).isoformat()))
    except CalendarUnavailable:
        raise
    except Exception as error:
        raise CalendarUnavailable("The US equity session schedule could not be verified.") from error


def latest_completed_session(now=None):
    """Latest session with its actual close plus a 15-minute data buffer elapsed."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required.")
    local_day = now.astimezone(NEW_YORK).date()
    try:
        calendar = _calendar(local_day.year, local_day.year)
        closes = calendar.closes.loc[:local_day.isoformat()]
        completed = closes[closes <= now.astimezone(timezone.utc) - PUBLICATION_DELAY]
        if completed.empty:
            raise CalendarUnavailable("No completed US equity session could be verified.")
        return completed.index[-1].date()
    except CalendarUnavailable:
        raise
    except Exception as error:
        raise CalendarUnavailable("The latest completed US equity session could not be verified.") from error
