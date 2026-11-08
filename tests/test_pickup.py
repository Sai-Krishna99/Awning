import pytest

from awning.pickup import friendly_pickup_time


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-09T09:00:00-06:00", "Mon, Nov 9 · 9:00 AM"),
        ("2026-11-09T09:00Z", "Mon, Nov 9 · 9:00 AM"),
        ("2026-11-09 08:30 AM", "Mon, Nov 9 · 8:30 AM"),
        ("2026-11-09 8:30 pm", "Mon, Nov 9 · 8:30 PM"),
        ("2026-11-09 13:05", "Mon, Nov 9 · 1:05 PM"),
        ("2026-11-09 00:15", "Mon, Nov 9 · 12:15 AM"),
        ("  2026-11-09 08:00  ", "Mon, Nov 9 · 8:00 AM"),
    ],
)
def test_formats_dated_times(raw: str, expected: str) -> None:
    assert friendly_pickup_time(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["tomorrow 8:00 AM", "12:00pm", "8am", "2026-13-40 10:00", "2026-11-09 13:00 PM", "2026-11-09 25:00", "noon"],
)
def test_leaves_other_text_alone(raw: str) -> None:
    assert friendly_pickup_time(raw) == raw.strip()
