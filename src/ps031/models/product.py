from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass
class Product:
    """Represents a product catalog item."""

    sku: str
    name: str
    category: str
    brand: str
    price: Decimal

    def __post_init__(self) -> None:
        for field_name, value in [
            ("SKU", self.sku),
            ("Name", self.name),
            ("Category", self.category),
            ("Brand", self.brand),
        ]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        if isinstance(self.price, bool):
            raise TypeError(
                f"Price cannot be a boolean, got {type(self.price).__name__}."
            )

        if not isinstance(self.price, Decimal):
            try:
                self.price = Decimal(str(self.price))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(f"Invalid price: {self.price}") from err

        if not self.price.is_finite():
            raise ValueError(f"Price must be a finite number, got {self.price}.")

        if self.price < Decimal("0"):
            raise ValueError(f"Price cannot be negative, got {self.price}.")
