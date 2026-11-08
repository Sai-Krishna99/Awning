import re
from datetime import date

_DATED = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?"
    r"\s*(?:([AaPp][Mm])|Z|[+-]\d{2}:?\d{2})?$"
)


def friendly_pickup_time(raw: str) -> str:
    """Turn a dated pickup time like '2026-11-09T09:00:00-06:00' into 'Mon, Nov 9 · 9:00 AM'.

    The wall-clock time is kept as written (no timezone conversion). Anything else,
    such as 'tomorrow 8am', is returned unchanged apart from trimming.
    """
    text = raw.strip()
    match = _DATED.match(text)
    if not match:
        return text
    year, month, day, hour, minute = (int(part) for part in match.groups()[:5])
    meridiem = (match.group(6) or "").upper()
    if meridiem:
        if not 1 <= hour <= 12:
            return text
        hour = hour % 12 + (12 if meridiem == "PM" else 0)
    try:
        when = date(year, month, day)
    except ValueError:
        return text
    if hour > 23 or minute > 59:
        return text
    clock = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    return f"{when:%a}, {when:%b} {day} · {clock}"
