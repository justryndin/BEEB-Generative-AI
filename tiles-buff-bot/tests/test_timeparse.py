import pytest

from core.timeparse import DAY, HOUR, MINUTE, format_duration, parse_duration


@pytest.mark.parametrize(
    "text, expected",
    [
        ("21д 5ч", 21 * DAY + 5 * HOUR),
        ("21д5ч", 21 * DAY + 5 * HOUR),
        ("100", 100 * DAY),
        ("100 дней", 100 * DAY),
        ("21 день 5 часов", 21 * DAY + 5 * HOUR),
        ("20d 13:45:12", 20 * DAY + 13 * HOUR + 45 * MINUTE + 12),
        ("13:45", 13 * HOUR + 45 * MINUTE),
        ("3д, 4ч и 30м", 3 * DAY + 4 * HOUR + 30 * MINUTE),
        ("1,5д", int(1.5 * DAY)),
        ("7 Дн", 7 * DAY),
    ],
)
def test_parse_ok(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "привет", "21 5", "0", "5 лет", "500д", "10:75", "21д abc"])
def test_parse_bad(text):
    assert parse_duration(text) is None


def test_format():
    assert format_duration(21 * DAY + 5 * HOUR) == "21д 5ч"
    assert format_duration(7 * DAY) == "7д"
    assert format_duration(5 * HOUR + 30 * MINUTE) == "5ч 30м"
    assert format_duration(-5) == "0м"
