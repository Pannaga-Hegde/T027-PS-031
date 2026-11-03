from decimal import Decimal, ROUND_HALF_UP
import math

from ps031.models.coordinates import Coordinates

EARTH_RADIUS_KM: float = 6371.0


def haversine_distance(coord1: Coordinates, coord2: Coordinates) -> float:
    """Calculate the great-circle distance between two geographic coordinates in kilometres.

    Uses standard mean Earth radius R = 6371.0 km per BR-01 and DEC-05.
    Returns the distance rounded to two decimal places using explicit Decimal ROUND_HALF_UP.

    Raises:
        TypeError: If coord1 or coord2 is not a valid Coordinates instance or is a boolean.
        ValueError: If coordinate latitude or longitude values are non-finite.
    """
    if isinstance(coord1, bool) or not isinstance(coord1, Coordinates):
        raise TypeError(
            f"coord1 must be a Coordinates instance, got {type(coord1).__name__}."
        )
    if isinstance(coord2, bool) or not isinstance(coord2, Coordinates):
        raise TypeError(
            f"coord2 must be a Coordinates instance, got {type(coord2).__name__}."
        )

    for c, name in [(coord1, "coord1"), (coord2, "coord2")]:
        if not math.isfinite(c.latitude) or not math.isfinite(c.longitude):
            raise ValueError(
                f"{name} must contain finite latitude and longitude values."
            )

    # Convert decimal degrees to radians
    phi1 = math.radians(coord1.latitude)
    phi2 = math.radians(coord2.latitude)
    delta_phi = math.radians(coord2.latitude - coord1.latitude)
    delta_lambda = math.radians(coord2.longitude - coord1.longitude)

    # Haversine formula
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * (
        math.sin(delta_lambda / 2.0) ** 2
    )
    # Clamp 'a' to [0.0, 1.0] to safeguard against precision overflow in asin/atan2
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    raw_distance_km = EARTH_RADIUS_KM * c

    # Round using Decimal ROUND_HALF_UP with 7-decimal float normalization
    dec_val = Decimal(str(round(raw_distance_km, 7)))
    rounded_dec = dec_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(rounded_dec)
