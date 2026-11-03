from datetime import datetime, time
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.store import Store


@pytest.fixture
def sample_location():
    return Coordinates(latitude=12.9716, longitude=77.5946)


@pytest.fixture
def sample_hours():
    return OperatingHours(open_time=time(6, 0), close_time=time(23, 0))


def test_store_valid_construction_with_defaults(sample_location, sample_hours):
    """Store with required arguments and default values."""
    s = Store(
        store_id="store_koramangala",
        name="Koramangala Dark Store",
        location=sample_location,
        operating_hours=sample_hours,
    )
    assert s.store_id == "store_koramangala"
    assert s.name == "Koramangala Dark Store"
    assert s.location is sample_location
    assert s.operating_hours is sample_hours
    assert s.picking_capacity == 12
    assert s.active_riders == 0
    assert s.weather_hold_until is None
    assert s.inventory == {}


def test_store_valid_construction_with_explicit_values(sample_location, sample_hours):
    """Store with all explicit arguments."""
    hold_time = datetime(2026, 11, 3, 10, 30)
    inv = {"SKU-MILK": 15, "SKU-BREAD": 20}
    s = Store(
        store_id="store_01",
        name="Indiranagar Dark Store",
        location=sample_location,
        operating_hours=sample_hours,
        picking_capacity=15,
        active_riders=4,
        weather_hold_until=hold_time,
        inventory=inv,
    )
    assert s.picking_capacity == 15
    assert s.active_riders == 4
    assert s.weather_hold_until == hold_time
    assert s.inventory == {"SKU-MILK": 15, "SKU-BREAD": 20}


def test_store_inventory_isolation_from_caller(sample_location, sample_hours):
    """Modifying the passed-in inventory dictionary must not mutate store inventory."""
    caller_inv = {"SKU-1": 10}
    s = Store(
        store_id="s1",
        name="Store 1",
        location=sample_location,
        operating_hours=sample_hours,
        inventory=caller_inv,
    )
    caller_inv["SKU-1"] = 99
    caller_inv["SKU-2"] = 5

    assert s.inventory == {"SKU-1": 10}
    assert "SKU-2" not in s.inventory


def test_store_separate_instances_have_independent_inventories(
    sample_location, sample_hours
):
    """Separate Store instances do not share default inventory dictionaries."""
    s1 = Store("s1", "Store 1", sample_location, sample_hours)
    s2 = Store("s2", "Store 2", sample_location, sample_hours)

    assert s1.inventory is not s2.inventory
    s1.inventory["SKU-NEW"] = 5
    assert "SKU-NEW" not in s2.inventory


@pytest.mark.parametrize(
    "id_kwargs",
    [{"store_id": ""}, {"store_id": "   "}, {"name": ""}, {"name": "   "}],
)
def test_store_rejects_empty_identifiers(sample_location, sample_hours, id_kwargs):
    """Store rejects empty or whitespace-only identifiers."""
    kwargs = {
        "store_id": "s1",
        "name": "Store",
        "location": sample_location,
        "operating_hours": sample_hours,
    }
    kwargs.update(id_kwargs)
    with pytest.raises(ValueError, match="must not be empty"):
        Store(**kwargs)


def test_store_rejects_invalid_location_type(sample_hours):
    """Store rejects location that is not Coordinates."""
    with pytest.raises(TypeError, match="Coordinates"):
        Store("s1", "Store", (12.97, 77.59), sample_hours)


def test_store_rejects_invalid_operating_hours_type(sample_location):
    """Store rejects operating_hours that is not OperatingHours."""
    with pytest.raises(TypeError, match="OperatingHours"):
        Store("s1", "Store", sample_location, "08:00-22:00")


@pytest.mark.parametrize("bad_cap", [0, -1, -10])
def test_store_rejects_non_positive_picking_capacity(
    sample_location, sample_hours, bad_cap
):
    """Picking capacity must be strictly > 0."""
    with pytest.raises(ValueError, match="positive integer"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            picking_capacity=bad_cap,
        )


@pytest.mark.parametrize("bad_cap", [True, False, 12.5, "12", None])
def test_store_rejects_invalid_capacity_types(
    sample_location, sample_hours, bad_cap
):
    """Picking capacity rejects non-int and boolean types."""
    with pytest.raises(TypeError, match="integer"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            picking_capacity=bad_cap,
        )


@pytest.mark.parametrize("bad_riders", [-1, -5])
def test_store_rejects_negative_active_riders(
    sample_location, sample_hours, bad_riders
):
    """Active riders cannot be negative."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            active_riders=bad_riders,
        )


@pytest.mark.parametrize("bad_riders", [True, False, "3", 2.5, None])
def test_store_rejects_invalid_active_riders_types(
    sample_location, sample_hours, bad_riders
):
    """Active riders rejects boolean and non-int types."""
    with pytest.raises(TypeError, match="integer"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            active_riders=bad_riders,
        )


def test_store_rejects_invalid_weather_hold_type(sample_location, sample_hours):
    """weather_hold_until must be datetime or None."""
    with pytest.raises(TypeError, match="datetime"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            weather_hold_until="2026-11-03 10:00:00",
        )


def test_store_rejects_invalid_inventory_keys_and_values(
    sample_location, sample_hours
):
    """Inventory keys must be non-empty strings and values non-negative ints."""
    # Empty SKU key
    with pytest.raises(ValueError, match="SKU key must not be empty"):
        Store(
            "s1", "Store", sample_location, sample_hours, inventory={"": 10}
        )

    # Non-string SKU key
    with pytest.raises(TypeError, match="SKU key must be a string"):
        Store(
            "s1", "Store", sample_location, sample_hours, inventory={123: 10}
        )

    # Boolean quantity
    with pytest.raises(TypeError, match="must be an integer"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            inventory={"SKU-1": True},
        )

    # Negative quantity
    with pytest.raises(ValueError, match="cannot be negative"):
        Store(
            "s1",
            "Store",
            sample_location,
            sample_hours,
            inventory={"SKU-1": -1},
        )
