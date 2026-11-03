from datetime import time, timezone
import pytest

from ps031.models.operating_hours import OperatingHours


def test_operating_hours_valid_construction():
    """Verify valid OperatingHours creation."""
    hours = OperatingHours(open_time=time(7, 0), close_time=time(23, 0))
    assert hours.open_time == time(7, 0)
    assert hours.close_time == time(23, 0)


def test_operating_hours_is_open_at_opening_time():
    """Opening time is inclusive: is_open returns True."""
    hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    assert hours.is_open(time(8, 0)) is True


def test_operating_hours_is_closed_at_closing_time():
    """Closing time is exclusive: is_open returns False."""
    hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    assert hours.is_open(time(22, 0)) is False


def test_operating_hours_is_open_inside_operating_hours():
    """Times strictly between open and close return True."""
    hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    assert hours.is_open(time(8, 1)) is True
    assert hours.is_open(time(12, 30)) is True
    assert hours.is_open(time(21, 59, 59)) is True


def test_operating_hours_is_closed_outside_operating_hours():
    """Times before opening or after closing return False."""
    hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    assert hours.is_open(time(7, 59, 59)) is False
    assert hours.is_open(time(0, 0)) is False
    assert hours.is_open(time(22, 1)) is False
    assert hours.is_open(time(23, 30)) is False


def test_operating_hours_rejects_equal_open_and_close_time():
    """Equal opening and closing times raise ValueError."""
    with pytest.raises(ValueError, match="cannot be equal"):
        OperatingHours(open_time=time(10, 0), close_time=time(10, 0))


def test_operating_hours_rejects_overnight_hours():
    """Opening time after closing time raises ValueError."""
    with pytest.raises(ValueError, match="cannot be after closing time"):
        OperatingHours(open_time=time(23, 0), close_time=time(6, 0))


@pytest.mark.parametrize("bad_val", ["08:00", 800, True, False, None, [time(8, 0)]])
def test_operating_hours_rejects_invalid_constructor_types(bad_val):
    """Constructor rejects non-time types for open_time and close_time."""
    with pytest.raises(TypeError, match="datetime.time"):
        OperatingHours(open_time=bad_val, close_time=time(22, 0))

    with pytest.raises(TypeError, match="datetime.time"):
        OperatingHours(open_time=time(8, 0), close_time=bad_val)


@pytest.mark.parametrize("bad_val", ["12:00", 1200, True, False, None, 12.5])
def test_operating_hours_is_open_rejects_invalid_type(bad_val):
    """is_open rejects non-time arguments."""
    hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    with pytest.raises(TypeError, match="datetime.time"):
        hours.is_open(bad_val)


def test_operating_hours_rejects_timezone_mismatch():
    """Reject OperatingHours where one boundary is aware and other is naive."""
    with pytest.raises(TypeError, match="both be naive or both be timezone-aware"):
        OperatingHours(open_time=time(8, 0), close_time=time(22, 0, tzinfo=timezone.utc))

    with pytest.raises(TypeError, match="both be naive or both be timezone-aware"):
        OperatingHours(open_time=time(8, 0, tzinfo=timezone.utc), close_time=time(22, 0))


def test_operating_hours_is_open_rejects_timezone_mismatch():
    """is_open rejects comparing naive hours with aware query or vice-versa."""
    naive_hours = OperatingHours(open_time=time(8, 0), close_time=time(22, 0))
    with pytest.raises(TypeError, match="timezone-aware and timezone-naive"):
        naive_hours.is_open(time(10, 0, tzinfo=timezone.utc))

    aware_hours = OperatingHours(
        open_time=time(8, 0, tzinfo=timezone.utc),
        close_time=time(22, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(TypeError, match="timezone-aware and timezone-naive"):
        aware_hours.is_open(time(10, 0))


def test_operating_hours_timezone_aware_functioning():
    """Timezone-aware operating hours function accurately."""
    hours = OperatingHours(
        open_time=time(8, 0, tzinfo=timezone.utc),
        close_time=time(22, 0, tzinfo=timezone.utc),
    )
    assert hours.is_open(time(8, 0, tzinfo=timezone.utc)) is True
    assert hours.is_open(time(12, 0, tzinfo=timezone.utc)) is True
    assert hours.is_open(time(22, 0, tzinfo=timezone.utc)) is False
    assert hours.is_open(time(7, 59, tzinfo=timezone.utc)) is False
