"""NYSE regular sessions, including holidays, DST and early closes."""
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

NEW_YORK = ZoneInfo("America/New_York")

@lru_cache(maxsize=4)
def calendar_for(year: int):
    return xcals.get_calendar("XNYS", start=f"{year-2}-01-01", end=f"{year+1}-12-31", side="left")

def sessions(as_of: datetime, count: int = 65, *, complete: bool = True):
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    day = as_of.astimezone(NEW_YORK).date()
    cal = calendar_for(day.year)
    labels = cal.sessions_in_range(day - timedelta(days=count * 3 + 15), day)
    result = []
    for label in labels:
        opened = cal.session_open(label).to_pydatetime()
        closed = cal.session_close(label).to_pydatetime()
        if (closed if complete else opened) <= as_of:
            result.append({"date": label.date().isoformat(), "open": opened, "close": closed})
    return result[-count:]

def checkpoints(as_of: datetime):
    result = []
    for session in sessions(as_of, 2, complete=False):
        current = session["open"] + timedelta(hours=1)
        while current < session["close"]:
            result.append((current, "小时报告"))
            current += timedelta(hours=1)
        result.append((session["close"], "收盘报告"))
    return [(time, kind) for time, kind in result if time + timedelta(minutes=20 if kind == "收盘报告" else 2) <= as_of]
