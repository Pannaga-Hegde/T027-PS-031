from dataclasses import dataclass, field
from datetime import datetime

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours


@dataclass
class Store:
    """Represents a quick-commerce dark store."""

    store_id: str
    name: str
    location: Coordinates
    operating_hours: OperatingHours
    picking_capacity: int = 12
    active_riders: int = 0
    weather_hold_until: datetime | None = None
    inventory: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate store_id and name
        for field_name, value in [("Store ID", self.store_id), ("Name", self.name)]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        # Validate location
        if not isinstance(self.location, Coordinates):
            raise TypeError(
                f"Location must be a Coordinates instance, got {type(self.location).__name__}."
            )

        # Validate operating_hours
        if not isinstance(self.operating_hours, OperatingHours):
            raise TypeError(
                f"Operating hours must be an OperatingHours instance, got {type(self.operating_hours).__name__}."
            )

        # Validate picking_capacity
        if isinstance(self.picking_capacity, bool) or not isinstance(
            self.picking_capacity, int
        ):
            raise TypeError(
                f"Picking capacity must be an integer, got {type(self.picking_capacity).__name__}."
            )
        if self.picking_capacity <= 0:
            raise ValueError(
                f"Picking capacity must be a positive integer, got {self.picking_capacity}."
            )

        # Validate active_riders
        if isinstance(self.active_riders, bool) or not isinstance(
            self.active_riders, int
        ):
            raise TypeError(
                f"Active riders must be an integer, got {type(self.active_riders).__name__}."
            )
        if self.active_riders < 0:
            raise ValueError(
                f"Active riders cannot be negative, got {self.active_riders}."
            )

        # Validate weather_hold_until
        if self.weather_hold_until is not None:
            if isinstance(self.weather_hold_until, bool) or not isinstance(
                self.weather_hold_until, datetime
            ):
                raise TypeError(
                    f"weather_hold_until must be a datetime or None, got {type(self.weather_hold_until).__name__}."
                )

        # Validate and isolate inventory
        if self.inventory is None:
            self.inventory = {}
        elif not isinstance(self.inventory, dict):
            raise TypeError(
                f"Inventory must be a dictionary, got {type(self.inventory).__name__}."
            )
        else:
            self.inventory = dict(self.inventory)

        for sku, qty in self.inventory.items():
            if isinstance(sku, bool) or not isinstance(sku, str):
                raise TypeError(
                    f"Inventory SKU key must be a string, got {type(sku).__name__}."
                )
            if not sku.strip():
                raise ValueError("Inventory SKU key must not be empty.")
            if isinstance(qty, bool) or not isinstance(qty, int):
                raise TypeError(
                    f"Inventory quantity for SKU '{sku}' must be an integer, got {type(qty).__name__}."
                )
            if qty < 0:
                raise ValueError(
                    f"Inventory quantity for SKU '{sku}' cannot be negative, got {qty}."
                )
