from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum


class SubstitutionPolicy(str, Enum):
    """Customer substitution preferences defined in BR-05 and the glossary."""

    ALLOW = "ALLOW"
    SIMILAR_ONLY = "SIMILAR_ONLY"
    NONE = "NONE"


@dataclass
class OrderItem:
    """Represents an individual item line in an order."""

    product_id: str
    quantity: int
    unit_price: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError(
                f"Quantity must be an integer (booleans, strings, and floats are not permitted), got {type(self.quantity).__name__}."
            )
        if self.quantity <= 0:
            raise ValueError(
                f"Quantity must be a positive integer, got {self.quantity}."
            )

        if isinstance(self.unit_price, bool):
            raise TypeError(
                f"Unit price cannot be a boolean, got {type(self.unit_price).__name__}."
            )

        if not isinstance(self.unit_price, Decimal):
            try:
                self.unit_price = Decimal(str(self.unit_price))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(f"Invalid unit price: {self.unit_price}") from err

        if not self.unit_price.is_finite():
            raise ValueError(
                f"Unit price must be a finite number, got {self.unit_price}."
            )

        if self.unit_price < Decimal("0"):
            raise ValueError(
                f"Unit price cannot be negative, got {self.unit_price}."
            )


@dataclass
class Order:
    """Represents a customer order."""

    order_id: str
    customer_id: str
    items: list[OrderItem] = field(default_factory=list)
    substitution_policy: SubstitutionPolicy = SubstitutionPolicy.NONE

    def __post_init__(self) -> None:
        if self.items is None:
            self.items = []
        elif not isinstance(self.items, list):
            self.items = list(self.items)

        if isinstance(self.substitution_policy, str) and not isinstance(
            self.substitution_policy, SubstitutionPolicy
        ):
            self.substitution_policy = SubstitutionPolicy(self.substitution_policy)
