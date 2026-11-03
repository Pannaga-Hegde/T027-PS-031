from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from ps031.models.coordinates import Coordinates
from ps031.models.order_state import OrderState


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
    fulfilled_quantity: int = 0
    substituted_product_sku: str | None = None
    substituted_quantity: int = 0
    charged_unit_price: Decimal | None = None

    def __post_init__(self) -> None:
        # Validate product_id
        if isinstance(self.product_id, bool) or not isinstance(self.product_id, str):
            raise TypeError(
                f"Product ID must be a string, got {type(self.product_id).__name__}."
            )
        if not self.product_id.strip():
            raise ValueError("Product ID must not be empty.")

        # Validate requested quantity
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError(
                f"Quantity must be an integer (booleans, strings, and floats are not permitted), got {type(self.quantity).__name__}."
            )
        if self.quantity <= 0:
            raise ValueError(
                f"Quantity must be a positive integer, got {self.quantity}."
            )

        # Validate unit_price
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

        # Validate fulfilled_quantity
        if isinstance(self.fulfilled_quantity, bool) or not isinstance(
            self.fulfilled_quantity, int
        ):
            raise TypeError(
                f"Fulfilled quantity must be an integer, got {type(self.fulfilled_quantity).__name__}."
            )
        if not (0 <= self.fulfilled_quantity <= self.quantity):
            raise ValueError(
                f"Fulfilled quantity must be between 0 and {self.quantity}, got {self.fulfilled_quantity}."
            )

        # Validate substituted_quantity
        if isinstance(self.substituted_quantity, bool) or not isinstance(
            self.substituted_quantity, int
        ):
            raise TypeError(
                f"Substituted quantity must be an integer, got {type(self.substituted_quantity).__name__}."
            )
        if not (0 <= self.substituted_quantity <= self.fulfilled_quantity):
            raise ValueError(
                f"Substituted quantity must be between 0 and {self.fulfilled_quantity}, got {self.substituted_quantity}."
            )

        # Validate substituted_product_sku
        if self.substituted_quantity > 0:
            if self.substituted_product_sku is None or (
                isinstance(self.substituted_product_sku, str)
                and not self.substituted_product_sku.strip()
            ):
                raise ValueError(
                    "A substitute SKU is required when substituted quantity is positive."
                )
            if isinstance(self.substituted_product_sku, bool) or not isinstance(
                self.substituted_product_sku, str
            ):
                raise TypeError(
                    f"Substituted product SKU must be a string, got {type(self.substituted_product_sku).__name__}."
                )
            if self.substituted_product_sku.strip() == self.product_id:
                raise ValueError(
                    "Substitute product SKU cannot be the same as the original product ID."
                )
        elif self.substituted_product_sku is not None:
            raise ValueError(
                "Substitute SKU must be None when substituted quantity is zero."
            )

        # Validate charged_unit_price under BR-05 contract
        if self.charged_unit_price is None:
            self.charged_unit_price = self.unit_price
        else:
            if isinstance(self.charged_unit_price, bool):
                raise TypeError(
                    f"Charged unit price cannot be a boolean, got {type(self.charged_unit_price).__name__}."
                )
            if not isinstance(self.charged_unit_price, Decimal):
                try:
                    self.charged_unit_price = Decimal(str(self.charged_unit_price))
                except (InvalidOperation, TypeError, ValueError) as err:
                    raise ValueError(
                        f"Invalid charged unit price: {self.charged_unit_price}"
                    ) from err
            if not self.charged_unit_price.is_finite():
                raise ValueError(
                    f"Charged unit price must be a finite number, got {self.charged_unit_price}."
                )
            if self.charged_unit_price < Decimal("0"):
                raise ValueError(
                    f"Charged unit price cannot be negative, got {self.charged_unit_price}."
                )
            if self.charged_unit_price != self.unit_price:
                raise ValueError(
                    f"Under BR-05, charged unit price ({self.charged_unit_price}) must equal "
                    f"original unit price ({self.unit_price})."
                )

    @property
    def original_fulfilled_quantity(self) -> int:
        """Quantity fulfilled from the originally requested product."""
        return self.fulfilled_quantity - self.substituted_quantity

    @property
    def shortfall_quantity(self) -> int:
        """Shortfall quantity that could not be fulfilled."""
        return self.quantity - self.fulfilled_quantity


@dataclass
class Order:
    """Represents a customer order."""

    order_id: str
    customer_id: str
    items: list[OrderItem] = field(default_factory=list)
    substitution_policy: SubstitutionPolicy = SubstitutionPolicy.NONE
    delivery_location: Coordinates | None = None
    order_time: datetime | None = None
    status: OrderState = OrderState.RECEIVED
    allocated_store_id: str | None = None
    promised_eta: datetime | None = None
    delayed_promise: bool = False
    slow_delivery: bool = False
    delivery_fee: Decimal = Decimal("0.00")
    surge_fee: Decimal = Decimal("0.00")
    actual_delivery_time: datetime | None = None

    def __post_init__(self) -> None:
        # Validate identifiers
        for field_name, value in [
            ("Order ID", self.order_id),
            ("Customer ID", self.customer_id),
        ]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        # Validate items list
        if self.items is None:
            self.items = []
        elif not isinstance(self.items, (list, tuple)):
            raise TypeError(
                f"Items must be a list of OrderItem objects, got {type(self.items).__name__}."
            )
        else:
            self.items = list(self.items)

        for item in self.items:
            if not isinstance(item, OrderItem):
                raise TypeError(
                    f"All order items must be OrderItem instances, got {type(item).__name__}."
                )

        # Validate substitution_policy
        if isinstance(self.substitution_policy, str) and not isinstance(
            self.substitution_policy, SubstitutionPolicy
        ):
            try:
                self.substitution_policy = SubstitutionPolicy(self.substitution_policy)
            except ValueError:
                try:
                    self.substitution_policy = SubstitutionPolicy[
                        self.substitution_policy.upper()
                    ]
                except (KeyError, AttributeError):
                    raise ValueError(
                        f"Invalid substitution policy: {self.substitution_policy}"
                    )
        elif not isinstance(self.substitution_policy, SubstitutionPolicy):
            raise TypeError(
                f"substitution_policy must be SubstitutionPolicy or str, got {type(self.substitution_policy).__name__}."
            )

        # Validate delivery_location
        if self.delivery_location is not None and not isinstance(
            self.delivery_location, Coordinates
        ):
            raise TypeError(
                f"delivery_location must be a Coordinates instance or None, got {type(self.delivery_location).__name__}."
            )

        # Validate datetime fields
        for field_name, value in [
            ("order_time", self.order_time),
            ("promised_eta", self.promised_eta),
            ("actual_delivery_time", self.actual_delivery_time),
        ]:
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, datetime):
                    raise TypeError(
                        f"{field_name} must be a datetime or None, got {type(value).__name__}."
                    )

        # Validate timezone awareness consistency among provided timestamps
        provided_timestamps = [
            (name, val)
            for name, val in [
                ("order_time", self.order_time),
                ("promised_eta", self.promised_eta),
                ("actual_delivery_time", self.actual_delivery_time),
            ]
            if val is not None
        ]
        if provided_timestamps:
            first_is_aware = provided_timestamps[0][1].tzinfo is not None
            for name, val in provided_timestamps[1:]:
                if (val.tzinfo is not None) != first_is_aware:
                    raise TypeError(
                        f"Timestamp '{name}' timezone awareness does not match "
                        f"'{provided_timestamps[0][0]}'. All order timestamps must be "
                        "uniformly timezone-aware or uniformly timezone-naive."
                    )

        # Validate status
        if isinstance(self.status, str) and not isinstance(self.status, OrderState):
            try:
                self.status = OrderState(self.status)
            except ValueError:
                try:
                    self.status = OrderState[self.status.upper()]
                except (KeyError, AttributeError):
                    raise ValueError(f"Invalid order status: {self.status}")
        elif not isinstance(self.status, OrderState):
            raise TypeError(
                f"status must be an OrderState or valid str, got {type(self.status).__name__}."
            )

        # Validate allocated_store_id
        if self.allocated_store_id is not None:
            if isinstance(self.allocated_store_id, bool) or not isinstance(
                self.allocated_store_id, str
            ):
                raise TypeError(
                    f"allocated_store_id must be a string or None, got {type(self.allocated_store_id).__name__}."
                )
            if not self.allocated_store_id.strip():
                raise ValueError("allocated_store_id must not be empty.")

        # Validate boolean flags
        if not isinstance(self.delayed_promise, bool):
            raise TypeError(
                f"delayed_promise must be a boolean, got {type(self.delayed_promise).__name__}."
            )
        if not isinstance(self.slow_delivery, bool):
            raise TypeError(
                f"slow_delivery must be a boolean, got {type(self.slow_delivery).__name__}."
            )

        # Validate monetary fees
        for fee_name, fee_val in [
            ("delivery_fee", self.delivery_fee),
            ("surge_fee", self.surge_fee),
        ]:
            if isinstance(fee_val, bool):
                raise TypeError(f"{fee_name} cannot be a boolean, got bool.")
            if not isinstance(fee_val, Decimal):
                try:
                    fee_val = Decimal(str(fee_val))
                except (InvalidOperation, TypeError, ValueError) as err:
                    raise ValueError(f"Invalid {fee_name}: {fee_val}") from err
            if not fee_val.is_finite():
                raise ValueError(
                    f"{fee_name} must be a finite number, got {fee_val}."
                )
            if fee_val < Decimal("0"):
                raise ValueError(f"{fee_name} cannot be negative, got {fee_val}.")
            setattr(self, fee_name, fee_val)
