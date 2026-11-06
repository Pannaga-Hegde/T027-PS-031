from datetime import date, datetime
from decimal import Decimal
import pytest

from ps031.models.rider import Rider, RiderStatus


def test_rider_valid_construction_with_defaults():
    """Rider with default status and zero daily penalty."""
    r = Rider(rider_id="rider_01", name="Ramesh Kumar")
    assert r.rider_id == "rider_01"
    assert r.name == "Ramesh Kumar"
    assert r.status == RiderStatus.AVAILABLE
    assert r.status == "AVAILABLE"
    assert r.daily_penalty == Decimal("0.00")
    assert isinstance(r.daily_penalty, Decimal)


def test_rider_valid_construction_with_explicit_values():
    """Rider with explicit penalty and custom status."""
    r = Rider(
        rider_id="rider_02",
        name="Suresh Rao",
        status="BUSY",
        daily_penalty=Decimal("30.00"),
    )
    assert r.status == RiderStatus.BUSY
    assert r.status == "BUSY"
    assert r.daily_penalty == Decimal("30.00")


def test_rider_with_enum_status():
    """Rider constructed directly with RiderStatus enum members."""
    r_busy = Rider("r1", "Name", status=RiderStatus.BUSY)
    assert r_busy.status is RiderStatus.BUSY

    r_off = Rider("r2", "Name", status=RiderStatus.OFF_DUTY)
    assert r_off.status is RiderStatus.OFF_DUTY


def test_rider_status_string_case_insensitivity():
    """Rider status accepts case-insensitive valid strings."""
    r1 = Rider("r1", "Name", status="available")
    assert r1.status is RiderStatus.AVAILABLE

    r2 = Rider("r2", "Name", status="Busy")
    assert r2.status is RiderStatus.BUSY

    r3 = Rider("r3", "Name", status="off_duty")
    assert r3.status is RiderStatus.OFF_DUTY


def test_rider_rejects_invalid_status_string():
    """Rider rejects invalid status string values with informative message."""
    for invalid in ["ACTIVE", "ONLINE", "IDLE", "INACTIVE", "UNKNOWN"]:
        with pytest.raises(ValueError, match="Invalid rider status"):
            Rider("r1", "Name", status=invalid)


def test_rider_coerces_numeric_daily_penalty():
    """Rider coerces integer and string penalty to Decimal."""
    r_int = Rider("r1", "Name", daily_penalty=30)
    assert r_int.daily_penalty == Decimal("30")

    r_str = Rider("r2", "Name", daily_penalty="45.50")
    assert r_str.daily_penalty == Decimal("45.50")


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"rider_id": ""},
        {"rider_id": "   "},
        {"name": ""},
        {"name": "   "},
        {"status": ""},
        {"status": "   "},
    ],
)
def test_rider_rejects_empty_strings(bad_kwargs):
    """Rider rejects empty string fields."""
    kwargs = {"rider_id": "r1", "name": "Name", "status": "AVAILABLE"}
    kwargs.update(bad_kwargs)
    with pytest.raises(ValueError, match="must not be empty"):
        Rider(**kwargs)


@pytest.mark.parametrize(
    "bad_kwargs",
    [{"rider_id": 101}, {"name": None}, {"status": ["ACTIVE"]}],
)
def test_rider_rejects_non_string_types(bad_kwargs):
    """Rider rejects invalid string types."""
    kwargs = {"rider_id": "r1", "name": "Name", "status": "AVAILABLE"}
    kwargs.update(bad_kwargs)
    with pytest.raises(TypeError, match="must be a string"):
        Rider(**kwargs)


def test_rider_rejects_negative_penalty():
    """Rider rejects negative penalty amounts."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Rider("r1", "Name", daily_penalty=Decimal("-10.00"))


def test_rider_rejects_boolean_penalty():
    """Rider rejects boolean penalty values."""
    with pytest.raises(TypeError, match="cannot be a boolean"):
        Rider("r1", "Name", daily_penalty=True)


def test_rider_rejects_non_finite_penalty():
    """Rider rejects NaN or Infinity."""
    for bad in [Decimal("NaN"), Decimal("Infinity"), float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="finite"):
            Rider("r1", "Name", daily_penalty=bad)


def test_rider_daily_penalty_date_defaults_to_none():
    """Rider defaults daily_penalty_date to None."""
    r = Rider("r1", "Ramesh Kumar")
    assert r.daily_penalty_date is None


def test_rider_daily_penalty_date_accepts_date_instance():
    """Rider accepts explicit date instance for daily_penalty_date."""
    d = date(2026, 11, 6)
    r = Rider("r1", "Ramesh Kumar", daily_penalty=Decimal("30.00"), daily_penalty_date=d)
    assert r.daily_penalty_date == d


def test_rider_daily_penalty_date_coerces_iso_string():
    """Rider coerces valid ISO date string into date instance."""
    r = Rider("r1", "Ramesh Kumar", daily_penalty_date="2026-11-06")
    assert r.daily_penalty_date == date(2026, 11, 6)


def test_rider_daily_penalty_date_rejects_empty_string():
    """Rider rejects empty or whitespace-only daily_penalty_date string."""
    with pytest.raises(ValueError, match="must not be empty"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date="")
    with pytest.raises(ValueError, match="must not be empty"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date="   ")


def test_rider_daily_penalty_date_rejects_malformed_string():
    """Rider rejects invalid date string formats."""
    with pytest.raises(ValueError, match="Invalid daily penalty date format"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date="not-a-date")
    with pytest.raises(ValueError, match="Invalid daily penalty date format"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date="2026/11/06")


def test_rider_daily_penalty_date_rejects_boolean():
    """Rider rejects boolean for daily_penalty_date."""
    with pytest.raises(TypeError, match="cannot be a boolean"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date=True)


def test_rider_daily_penalty_date_rejects_datetime():
    """Rider rejects datetime instance to avoid timezone/time ambiguity."""
    dt = datetime(2026, 11, 6, 10, 30)
    with pytest.raises(TypeError, match="must be a date, not a datetime"):
        Rider("r1", "Ramesh Kumar", daily_penalty_date=dt)


def test_rider_daily_penalty_date_rejects_invalid_types():
    """Rider rejects non-date/non-string types."""
    for bad in [123, 12.34, [date(2026, 11, 6)]]:
        with pytest.raises(TypeError, match="must be a date or None"):
            Rider("r1", "Ramesh Kumar", daily_penalty_date=bad)
