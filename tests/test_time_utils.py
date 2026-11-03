from datetime import datetime, timedelta, timezone
import pytest

from ps031.utils.time_utils import (
    SLOT_DURATION_MINUTES,
    get_slot_end,
    get_slot_range,
    get_slot_start,
)


def test_slot_duration_constant():
    assert SLOT_DURATION_MINUTES == 10


def test_get_slot_start_exact_boundary():
    dt = datetime(2026, 11, 3, 10, 0, 0)
    assert get_slot_start(dt) == datetime(2026, 11, 3, 10, 0, 0)


def test_get_slot_start_mid_slot():
    dt = datetime(2026, 11, 3, 10, 4, 30)
    assert get_slot_start(dt) == datetime(2026, 11, 3, 10, 0, 0)


def test_get_slot_start_boundary_minus_one_second():
    dt = datetime(2026, 11, 3, 10, 9, 59)
    assert get_slot_start(dt) == datetime(2026, 11, 3, 10, 0, 0)


def test_get_slot_start_next_slot_exact():
    dt = datetime(2026, 11, 3, 10, 10, 0)
    assert get_slot_start(dt) == datetime(2026, 11, 3, 10, 10, 0)


def test_get_slot_start_all_ten_minute_slots():
    test_cases = [
        (datetime(2026, 11, 3, 14, 0, 15), datetime(2026, 11, 3, 14, 0, 0)),
        (datetime(2026, 11, 3, 14, 12, 0), datetime(2026, 11, 3, 14, 10, 0)),
        (datetime(2026, 11, 3, 14, 29, 59), datetime(2026, 11, 3, 14, 20, 0)),
        (datetime(2026, 11, 3, 14, 35, 1), datetime(2026, 11, 3, 14, 30, 0)),
        (datetime(2026, 11, 3, 14, 40, 0), datetime(2026, 11, 3, 14, 40, 0)),
        (datetime(2026, 11, 3, 14, 59, 59), datetime(2026, 11, 3, 14, 50, 0)),
    ]
    for input_dt, expected_slot_start in test_cases:
        assert get_slot_start(input_dt) == expected_slot_start


def test_get_slot_start_zeroes_microseconds():
    dt = datetime(2026, 11, 3, 10, 7, 45, 123456)
    res = get_slot_start(dt)
    assert res == datetime(2026, 11, 3, 10, 0, 0, 0)
    assert res.microsecond == 0


def test_get_slot_start_preserves_timezone_awareness():
    tz = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(2026, 11, 3, 10, 15, 0, tzinfo=tz)
    res = get_slot_start(dt)
    assert res == datetime(2026, 11, 3, 10, 10, 0, tzinfo=tz)
    assert res.tzinfo == tz


def test_get_slot_start_preserves_naive_datetime():
    dt = datetime(2026, 11, 3, 10, 25, 0)
    res = get_slot_start(dt)
    assert res.tzinfo is None


def test_get_slot_start_custom_duration():
    dt = datetime(2026, 11, 3, 10, 17, 30)
    assert get_slot_start(dt, slot_minutes=15) == datetime(2026, 11, 3, 10, 15, 0)
    assert get_slot_start(dt, slot_minutes=5) == datetime(2026, 11, 3, 10, 15, 0)


def test_get_slot_end():
    dt = datetime(2026, 11, 3, 10, 4, 0)
    assert get_slot_end(dt) == datetime(2026, 11, 3, 10, 10, 0)


def test_get_slot_range():
    dt = datetime(2026, 11, 3, 10, 23, 45)
    start, end = get_slot_range(dt)
    assert start == datetime(2026, 11, 3, 10, 20, 0)
    assert end == datetime(2026, 11, 3, 10, 30, 0)


def test_get_slot_start_invalid_types():
    with pytest.raises(TypeError, match="dt must be a datetime instance"):
        get_slot_start("2026-11-03 10:00:00")  # type: ignore

    with pytest.raises(TypeError, match="dt must be a datetime instance"):
        get_slot_start(True)  # type: ignore

    with pytest.raises(ValueError, match="slot_minutes must be a positive integer"):
        get_slot_start(datetime(2026, 11, 3, 10, 0), slot_minutes=0)

    with pytest.raises(ValueError, match="slot_minutes must be a positive integer"):
        get_slot_start(datetime(2026, 11, 3, 10, 0), slot_minutes=-5)

    with pytest.raises(ValueError, match="slot_minutes must be a positive integer"):
        get_slot_start(datetime(2026, 11, 3, 10, 0), slot_minutes=True)  # type: ignore
