"""Formats a raw UTC timeslot (start/end ISO8601 strings from the MCP API)
into the local timezone with "Today"/"Tomorrow" wording -- the raw strings
are always UTC (+00:00), which reads as a foreign, hard-to-place time
without doing the tz math in your head first.
"""

from datetime import date, datetime, timedelta


def format_timeslot(start: str | None, end: str | None) -> str:
    if not start or not end:
        return f"{start} - {end}"
    try:
        local_start = datetime.fromisoformat(start).astimezone()
        local_end = datetime.fromisoformat(end).astimezone()
    except ValueError:
        return f"{start} - {end}"

    today = date.today()
    slot_date = local_start.date()
    if slot_date == today:
        day = "Today"
    elif slot_date == today + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = local_start.strftime("%d.%m")

    return f"{day}, {local_start:%H:%M}-{local_end:%H:%M}"
