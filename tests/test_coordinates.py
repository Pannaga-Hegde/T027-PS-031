import math

import pytest

from ps031.models.coordinates import Coordinates


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------


def test_coordinates_valid_typical():
    """Construct with ordinary float values."""
    c = Coordinates(latitude=12.9716, longitude=77.5946)

    assert c.latitude == pytest.approx(12.9716)
    assert c.longitude == pytest.approx(77.5946)


def test_coordinates_origin():
    """(0, 0) is a valid coordinate pair."""
    c = Coordinates(latitude=0.0, longitude=0.0)

    assert c.latitude == 0.0
    assert c.longitude == 0.0


def test_coordinates_negative_values():
    """Negative latitude and longitude are valid."""
    c = Coordinates(latitude=-33.8688, longitude=-70.6693)

    assert c.latitude == pytest.approx(-33.8688)
    assert c.longitude == pytest.approx(-70.6693)


# ---------------------------------------------------------------------------
# Boundary values — latitude
# ---------------------------------------------------------------------------


def test_coordinates_latitude_minimum():
    """Latitude exactly -90.0 is accepted."""
    c = Coordinates(latitude=-90.0, longitude=0.0)

    assert c.latitude == -90.0


def test_coordinates_latitude_maximum():
    """Latitude exactly 90.0 is accepted."""
    c = Coordinates(latitude=90.0, longitude=0.0)

    assert c.latitude == 90.0


def test_coordinates_latitude_below_minimum():
    """Latitude below -90.0 is rejected."""
    with pytest.raises(ValueError, match="-90.0 and 90.0"):
        Coordinates(latitude=-90.0001, longitude=0.0)


def test_coordinates_latitude_above_maximum():
    """Latitude above 90.0 is rejected."""
    with pytest.raises(ValueError, match="-90.0 and 90.0"):
        Coordinates(latitude=90.0001, longitude=0.0)


# ---------------------------------------------------------------------------
# Boundary values — longitude
# ---------------------------------------------------------------------------


def test_coordinates_longitude_minimum():
    """Longitude exactly -180.0 is accepted."""
    c = Coordinates(latitude=0.0, longitude=-180.0)

    assert c.longitude == -180.0


def test_coordinates_longitude_maximum():
    """Longitude exactly 180.0 is accepted."""
    c = Coordinates(latitude=0.0, longitude=180.0)

    assert c.longitude == 180.0


def test_coordinates_longitude_below_minimum():
    """Longitude below -180.0 is rejected."""
    with pytest.raises(ValueError, match="-180.0 and 180.0"):
        Coordinates(latitude=0.0, longitude=-180.0001)


def test_coordinates_longitude_above_maximum():
    """Longitude above 180.0 is rejected."""
    with pytest.raises(ValueError, match="-180.0 and 180.0"):
        Coordinates(latitude=0.0, longitude=180.0001)


# ---------------------------------------------------------------------------
# Integer-to-float coercion
# ---------------------------------------------------------------------------


def test_coordinates_integer_inputs_are_coerced_to_float():
    """Integer latitude and longitude are accepted and stored as floats."""
    c = Coordinates(latitude=0, longitude=0)

    assert isinstance(c.latitude, float)
    assert isinstance(c.longitude, float)


def test_coordinates_integer_boundary_values_coerced():
    """Integer boundary values are coerced and accepted."""
    c = Coordinates(latitude=-90, longitude=180)

    assert c.latitude == -90.0
    assert c.longitude == 180.0
    assert isinstance(c.latitude, float)
    assert isinstance(c.longitude, float)


# ---------------------------------------------------------------------------
# Invalid data types
# ---------------------------------------------------------------------------


def test_coordinates_rejects_string_latitude():
    """String latitude raises TypeError."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude="12.97", longitude=0.0)


def test_coordinates_rejects_string_longitude():
    """String longitude raises TypeError."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude=0.0, longitude="77.59")


def test_coordinates_rejects_none_latitude():
    """None latitude raises TypeError."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude=None, longitude=0.0)


def test_coordinates_rejects_none_longitude():
    """None longitude raises TypeError."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude=0.0, longitude=None)


def test_coordinates_rejects_list_latitude():
    """List latitude raises TypeError."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude=[12.0], longitude=0.0)


# ---------------------------------------------------------------------------
# Boolean values
# ---------------------------------------------------------------------------


def test_coordinates_rejects_boolean_latitude_true():
    """True for latitude raises TypeError before int coercion."""
    with pytest.raises(TypeError, match="boolean"):
        Coordinates(latitude=True, longitude=0.0)


def test_coordinates_rejects_boolean_latitude_false():
    """False for latitude raises TypeError before int coercion."""
    with pytest.raises(TypeError, match="boolean"):
        Coordinates(latitude=False, longitude=0.0)


def test_coordinates_rejects_boolean_longitude_true():
    """True for longitude raises TypeError before int coercion."""
    with pytest.raises(TypeError, match="boolean"):
        Coordinates(latitude=0.0, longitude=True)


def test_coordinates_rejects_boolean_longitude_false():
    """False for longitude raises TypeError before int coercion."""
    with pytest.raises(TypeError, match="boolean"):
        Coordinates(latitude=0.0, longitude=False)


# ---------------------------------------------------------------------------
# NaN and infinite values
# ---------------------------------------------------------------------------


def test_coordinates_rejects_nan_latitude():
    """NaN latitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=float("nan"), longitude=0.0)


def test_coordinates_rejects_positive_infinity_latitude():
    """Positive infinity latitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=float("inf"), longitude=0.0)


def test_coordinates_rejects_negative_infinity_latitude():
    """Negative infinity latitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=float("-inf"), longitude=0.0)


def test_coordinates_rejects_nan_longitude():
    """NaN longitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=0.0, longitude=float("nan"))


def test_coordinates_rejects_positive_infinity_longitude():
    """Positive infinity longitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=0.0, longitude=float("inf"))


def test_coordinates_rejects_negative_infinity_longitude():
    """Negative infinity longitude raises ValueError."""
    with pytest.raises(ValueError, match="finite"):
        Coordinates(latitude=0.0, longitude=float("-inf"))


# ---------------------------------------------------------------------------
# Numeric strings (must be rejected — not auto-coerced)
# ---------------------------------------------------------------------------


def test_coordinates_rejects_numeric_string_latitude():
    """A numeric string like '12.97' is not accepted for latitude."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude="12.97", longitude=0.0)


def test_coordinates_rejects_numeric_string_longitude():
    """A numeric string like '77.59' is not accepted for longitude."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude=0.0, longitude="77.59")


def test_coordinates_rejects_zero_string_latitude():
    """The string '0' is not accepted for latitude."""
    with pytest.raises(TypeError, match="float"):
        Coordinates(latitude="0", longitude=0.0)
