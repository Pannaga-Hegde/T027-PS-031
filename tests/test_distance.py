import math
import pytest

from ps031.models.coordinates import Coordinates
from ps031.utils.distance import EARTH_RADIUS_KM, haversine_distance


def _coordinate_at_distance(
    origin: Coordinates, target_distance_km: float
) -> Coordinates:
    """Helper creating a coordinate directly north of origin at target_distance_km."""
    dlat = (target_distance_km * 180.0) / (EARTH_RADIUS_KM * math.pi)
    return Coordinates(latitude=origin.latitude + dlat, longitude=origin.longitude)


def test_haversine_same_coordinates():
    """Identical coordinates produce 0.00 km distance."""
    c1 = Coordinates(latitude=12.9716, longitude=77.5946)
    c2 = Coordinates(latitude=12.9716, longitude=77.5946)
    assert haversine_distance(c1, c2) == 0.0


def test_haversine_at_2_999_km():
    """Distance of 2.999 km rounds to 3.00 km."""
    origin = Coordinates(latitude=12.9716, longitude=77.5946)
    target = _coordinate_at_distance(origin, 2.999)
    assert haversine_distance(origin, target) == 3.0


def test_haversine_at_3_000_km():
    """Distance of exactly 3.000 km rounds to 3.00 km."""
    origin = Coordinates(latitude=12.9716, longitude=77.5946)
    target = _coordinate_at_distance(origin, 3.000)
    assert haversine_distance(origin, target) == 3.0


def test_haversine_at_3_004_km():
    """Distance of 3.004 km rounds down to 3.00 km."""
    origin = Coordinates(latitude=12.9716, longitude=77.5946)
    target = _coordinate_at_distance(origin, 3.004)
    assert haversine_distance(origin, target) == 3.0


def test_haversine_at_3_005_km_rounds_half_up():
    """Distance of 3.005 km rounds half-up to 3.01 km."""
    origin = Coordinates(latitude=12.9716, longitude=77.5946)
    target = _coordinate_at_distance(origin, 3.005)
    assert haversine_distance(origin, target) == 3.01


def test_haversine_at_3_010_km():
    """Distance of 3.010 km rounds to 3.01 km."""
    origin = Coordinates(latitude=12.9716, longitude=77.5946)
    target = _coordinate_at_distance(origin, 3.010)
    assert haversine_distance(origin, target) == 3.01


def test_haversine_symmetry():
    """haversine_distance(a, b) equals haversine_distance(b, a)."""
    c1 = Coordinates(latitude=12.9716, longitude=77.5946)
    c2 = Coordinates(latitude=12.9352, longitude=77.6245)
    assert haversine_distance(c1, c2) == haversine_distance(c2, c1)


@pytest.mark.parametrize(
    "invalid_input",
    [None, "12.9716, 77.5946", 12.9716, True, False, [12.9716, 77.5946]],
)
def test_haversine_rejects_invalid_types(invalid_input):
    """haversine_distance rejects non-Coordinates types with TypeError."""
    valid = Coordinates(latitude=12.9716, longitude=77.5946)
    with pytest.raises(TypeError, match="Coordinates"):
        haversine_distance(invalid_input, valid)

    with pytest.raises(TypeError, match="Coordinates"):
        haversine_distance(valid, invalid_input)
