from datetime import date, timedelta

from silpo_agent.timeslot_format import format_timeslot


def _utc_iso(d: date, hour: int) -> str:
    return f"{d.isoformat()}T{hour:02d}:00:00+00:00"


def test_today():
    today = date.today()
    result = format_timeslot(_utc_iso(today, 0), _utc_iso(today, 1))
    assert result.startswith("Today, ") or result.startswith("Tomorrow, ")


def test_tomorrow_local_offset():
    # A UTC midnight slot lands on "today" or "tomorrow" locally depending on
    # tz offset -- either is correct, this just confirms no raw UTC date leaks.
    tomorrow = date.today() + timedelta(days=1)
    result = format_timeslot(_utc_iso(tomorrow, 0), _utc_iso(tomorrow, 1))
    assert result.startswith("Today, ") or result.startswith("Tomorrow, ")


def test_far_future_uses_date():
    far = date.today() + timedelta(days=10)
    result = format_timeslot(_utc_iso(far, 12), _utc_iso(far, 13))
    assert result.startswith(far.strftime("%d.%m")) or result.startswith(
        (far + timedelta(days=1)).strftime("%d.%m")
    )


def test_missing_values_fallback():
    assert format_timeslot(None, None) == "None - None"


def test_malformed_fallback():
    assert format_timeslot("not-a-date", "also-not") == "not-a-date - also-not"
