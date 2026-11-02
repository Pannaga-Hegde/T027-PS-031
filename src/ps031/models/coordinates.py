import math
from dataclasses import dataclass


@dataclass
class Coordinates:
    """Geographic position as a latitude/longitude pair.

    Latitude is in the range [-90.0, 90.0].
    Longitude is in the range [-180.0, 180.0].
    NaN and infinite values are rejected.
    Boolean values are rejected even though bool is a subclass of int.
    """

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        # --- latitude ---
        if isinstance(self.latitude, bool):
            raise TypeError(
                "Latitude must be a number, not a boolean."
            )
        if not isinstance(self.latitude, (int, float)):
            raise TypeError(
                f"Latitude must be a float, got {type(self.latitude).__name__}."
            )
        self.latitude = float(self.latitude)
        if not math.isfinite(self.latitude):
            raise ValueError(
                f"Latitude must be a finite number, got {self.latitude}."
            )
        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(
                f"Latitude must be between -90.0 and 90.0, got {self.latitude}."
            )

        # --- longitude ---
        if isinstance(self.longitude, bool):
            raise TypeError(
                "Longitude must be a number, not a boolean."
            )
        if not isinstance(self.longitude, (int, float)):
            raise TypeError(
                f"Longitude must be a float, got {type(self.longitude).__name__}."
            )
        self.longitude = float(self.longitude)
        if not math.isfinite(self.longitude):
            raise ValueError(
                f"Longitude must be a finite number, got {self.longitude}."
            )
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(
                f"Longitude must be between -180.0 and 180.0, got {self.longitude}."
            )
