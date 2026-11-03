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


# OrderStatus aliases OrderState for lifecycle compatibility
OrderStatus = OrderState


@dataclass
class OrderItem:
    """Represents an individual item line in an order."""

    product_id: str = ""
    quantity: int = 1
    unit_price: Decimal = Decimal("0.00")
    category: str = ""
    brand: str = ""
    fulfilled_quantity: int = 0
    substituted_product_sku: str | None = None
    substituted_quantity: int = 0
    charged_unit_price: Decimal | None = None

    def __init__(
        self,
        *args,
        product_id: str | None = None,
        quantity: int | None = None,
        unit_price: Decimal | int | float | str | None = None,
        category: str = "",
        brand: str = "",
        requested_qty: int | None = None,
        fulfilled_quantity: int | None = None,
        fulfilled_qty: int | None = None,
        substituted_product_sku: str | None = None,
        substituted_sku_id: str | None = None,
        substituted_quantity: int | None = None,
        charged_unit_price: Decimal | int | float | str | None = None,
        sku_id: str | None = None,
        **kwargs,
    ) -> None:
        pos_sku_id = None
        pos_qty = None
        pos_price = None
        pos_category = None
        pos_brand = None

        if len(args) == 5:
            pos_sku_id = args[0]
            pos_category = args[1]
            pos_brand = args[2]
            pos_price = args[3]
            pos_qty = args[4]
        elif len(args) == 3:
            pos_sku_id = args[0]
            pos_qty = args[1]
            pos_price = args[2]
        elif len(args) == 2:
            pos_sku_id = args[0]
            pos_qty = args[1]
        elif len(args) == 1:
            pos_sku_id = args[0]
        elif len(args) > 5:
            raise TypeError(
                f"OrderItem takes at most 5 positional arguments, got {len(args)}"
            )

        resolved_product_id = sku_id if sku_id is not None else (
            product_id if product_id is not None else pos_sku_id
        )
        if resolved_product_id is None:
            raise TypeError(
                "OrderItem missing required argument: 'product_id' (or 'sku_id')."
            )
        if isinstance(resolved_product_id, bool) or not isinstance(resolved_product_id, str):
            raise TypeError(
                f"Product ID must be a string, got {type(resolved_product_id).__name__}."
            )
        if not resolved_product_id.strip():
            raise ValueError("Product ID must not be empty.")

        resolved_category = category if category else (pos_category or "")
        resolved_brand = brand if brand else (pos_brand or "")

        raw_qty = requested_qty if requested_qty is not None else (
            quantity if quantity is not None else pos_qty
        )
        if raw_qty is None:
            raise TypeError(
                "OrderItem missing required argument: 'quantity' (or 'requested_qty')."
            )
        if isinstance(raw_qty, bool) or not isinstance(raw_qty, int):
            raise TypeError(
                f"Quantity must be an integer (booleans, strings, and floats are not permitted), got {type(raw_qty).__name__}."
            )
        if raw_qty <= 0:
            raise ValueError(
                f"Quantity must be a positive integer, got {raw_qty}."
            )

        raw_price = unit_price if unit_price is not None else pos_price
        if raw_price is None:
            raise TypeError("OrderItem missing required argument: 'unit_price'.")
        if isinstance(raw_price, bool):
            raise TypeError(
                f"Unit price cannot be a boolean, got {type(raw_price).__name__}."
            )
        if not isinstance(raw_price, Decimal):
            try:
                raw_price = Decimal(str(raw_price))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(f"Invalid unit price: {raw_price}") from err
        if not raw_price.is_finite():
            raise ValueError(
                f"Unit price must be a finite number, got {raw_price}."
            )
        if raw_price < Decimal("0"):
            raise ValueError(
                f"unit_price cannot be negative: {raw_price}."
            )

        raw_fulfilled_qty = (
            fulfilled_quantity
            if fulfilled_quantity is not None
            else (fulfilled_qty if fulfilled_qty is not None else 0)
        )
        if isinstance(raw_fulfilled_qty, bool) or not isinstance(raw_fulfilled_qty, int):
            raise TypeError(
                f"Fulfilled quantity must be an integer, got {type(raw_fulfilled_qty).__name__}."
            )
        if not (0 <= raw_fulfilled_qty <= raw_qty):
            raise ValueError(
                f"Fulfilled quantity must be between 0 and {raw_qty}, got {raw_fulfilled_qty}."
            )

        raw_sub_sku = (
            substituted_product_sku
            if substituted_product_sku is not None
            else substituted_sku_id
        )

        raw_sub_qty = substituted_quantity
        if raw_sub_qty is None:
            if raw_sub_sku is not None:
                raw_sub_qty = raw_fulfilled_qty
            else:
                raw_sub_qty = 0

        if isinstance(raw_sub_qty, bool) or not isinstance(raw_sub_qty, int):
            raise TypeError(
                f"Substituted quantity must be an integer, got {type(raw_sub_qty).__name__}."
            )
        if not (0 <= raw_sub_qty <= raw_fulfilled_qty):
            raise ValueError(
                f"Substituted quantity must be between 0 and {raw_fulfilled_qty}, got {raw_sub_qty}."
            )

        if raw_sub_qty > 0:
            if raw_sub_sku is None or (
                isinstance(raw_sub_sku, str) and not raw_sub_sku.strip()
            ):
                raise ValueError(
                    "A substitute SKU is required when substituted quantity is positive."
                )
            if isinstance(raw_sub_sku, bool) or not isinstance(raw_sub_sku, str):
                raise TypeError(
                    f"Substituted product SKU must be a string, got {type(raw_sub_sku).__name__}."
                )
            if raw_sub_sku.strip() == resolved_product_id:
                raise ValueError(
                    "Substitute product SKU cannot be the same as the original product ID."
                )
        elif raw_sub_sku is not None:
            raise ValueError(
                "Substitute SKU must be None when substituted quantity is zero."
            )

        raw_charged_price = charged_unit_price
        if raw_charged_price is None:
            raw_charged_price = raw_price
        else:
            if isinstance(raw_charged_price, bool):
                raise TypeError(
                    f"Charged unit price cannot be a boolean, got {type(raw_charged_price).__name__}."
                )
            if not isinstance(raw_charged_price, Decimal):
                try:
                    raw_charged_price = Decimal(str(raw_charged_price))
                except (InvalidOperation, TypeError, ValueError) as err:
                    raise ValueError(
                        f"Invalid charged unit price: {raw_charged_price}"
                    ) from err
            if not raw_charged_price.is_finite():
                raise ValueError(
                    f"Charged unit price must be a finite number, got {raw_charged_price}."
                )
            if raw_charged_price < Decimal("0"):
                raise ValueError(
                    f"Charged unit price cannot be negative, got {raw_charged_price}."
                )
            if raw_charged_price != raw_price:
                raise ValueError(
                    f"Under BR-05, charged unit price ({raw_charged_price}) must equal "
                    f"original unit price ({raw_price})."
                )

        self.product_id = resolved_product_id
        self.quantity = raw_qty
        self.unit_price = raw_price
        self.category = resolved_category
        self.brand = resolved_brand
        self.fulfilled_quantity = raw_fulfilled_qty
        self.substituted_product_sku = raw_sub_sku
        self.substituted_quantity = raw_sub_qty
        self.charged_unit_price = raw_charged_price

    @property
    def sku_id(self) -> str:
        return self.product_id

    @sku_id.setter
    def sku_id(self, value: str) -> None:
        self.product_id = value

    @property
    def requested_qty(self) -> int:
        return self.quantity

    @requested_qty.setter
    def requested_qty(self, value: int) -> None:
        self.quantity = value

    @property
    def fulfilled_qty(self) -> int:
        return self.fulfilled_quantity

    @fulfilled_qty.setter
    def fulfilled_qty(self, value: int) -> None:
        self.fulfilled_quantity = value
        if self.substituted_product_sku is not None and self.substituted_quantity == 0:
            self.substituted_quantity = value

    @property
    def substituted_sku_id(self) -> str | None:
        return self.substituted_product_sku

    @substituted_sku_id.setter
    def substituted_sku_id(self, value: str | None) -> None:
        self.substituted_product_sku = value
        if value is not None and self.substituted_quantity == 0 and self.fulfilled_quantity > 0:
            self.substituted_quantity = self.fulfilled_quantity
        elif value is None:
            self.substituted_quantity = 0

    @property
    def original_fulfilled_quantity(self) -> int:
        """Quantity fulfilled from the originally requested product."""
        return self.fulfilled_quantity - self.substituted_quantity

    @property
    def shortfall_quantity(self) -> int:
        """Shortfall quantity that could not be fulfilled."""
        return self.quantity - self.fulfilled_quantity

    @property
    def shortfall_qty(self) -> int:
        """Alias for shortfall_quantity."""
        return self.shortfall_quantity

    @property
    def original_line_total(self) -> Decimal:
        """Original expected line cost based on requested quantity."""
        return self.unit_price * self.quantity

    @property
    def original_line_value(self) -> Decimal:
        """Alias for original_line_total."""
        return self.original_line_total

    @property
    def charged_line_total(self) -> Decimal:
        """Actual line cost based on fulfilled quantity and charged price."""
        price = (
            self.charged_unit_price
            if self.charged_unit_price is not None
            else self.unit_price
        )
        return price * self.fulfilled_quantity

    @property
    def fulfilled_line_value(self) -> Decimal:
        """Alias for charged_line_total."""
        return self.charged_line_total

    @property
    def savings(self) -> Decimal:
        """Customer savings due to substitution price cap (under BR-05, always 0.00)."""
        return self.original_line_total - self.charged_line_total

    @property
    def is_substituted(self) -> bool:
        """Whether any portion of this item line was substituted."""
        return self.substituted_quantity > 0 or self.substituted_product_sku is not None

    @property
    def is_fulfilled(self) -> bool:
        """Whether requested quantity was completely fulfilled."""
        return self.fulfilled_quantity == self.quantity

    @property
    def is_fully_fulfilled(self) -> bool:
        """Alias for is_fulfilled."""
        return self.is_fulfilled

    @property
    def is_partially_fulfilled(self) -> bool:
        """Whether some, but not all, items were fulfilled."""
        return 0 < self.fulfilled_quantity < self.quantity

    @property
    def is_unfulfilled(self) -> bool:
        """Whether zero items were fulfilled."""
        return self.fulfilled_quantity == 0


@dataclass
class Order:
    """Represents a customer order."""

    order_id: str
    customer_id: str
    customer_lat: float = 0.0
    customer_lon: float = 0.0
    order_time: datetime | None = None
    items: list[OrderItem] = field(default_factory=list)
    delivery_location: Coordinates | None = None
    status: OrderState = OrderState.RECEIVED
    allocated_store_id: str | None = None
    promised_eta: datetime | None = None
    delayed_promise: bool = False
    slow_delivery: bool = False
    delivery_fee: Decimal = Decimal("0.00")
    surge_fee: Decimal = Decimal("0.00")
    pricing_frozen: bool = False
    actual_delivery_time: datetime | None = None
    refund_amount: Decimal = Decimal("0.00")
    cancellation_fee: Decimal = Decimal("0.00")
    wallet_credit: Decimal = Decimal("0.00")
    free_coupon: bool = False
    _substitution_policy: SubstitutionPolicy = SubstitutionPolicy.NONE

    def __init__(
        self,
        order_id: str,
        customer_id: str,
        *args,
        items: list[OrderItem] | None = None,
        substitution_policy: SubstitutionPolicy | str | None = None,
        substitution_preference: SubstitutionPolicy | str | None = None,
        delivery_location: Coordinates | None = None,
        customer_lat: float | int | None = None,
        customer_lon: float | int | None = None,
        order_time: datetime | None = None,
        status: OrderState | str = OrderState.RECEIVED,
        allocated_store_id: str | None = None,
        store_id: str | None = None,
        promised_eta: datetime | None = None,
        delayed_promise: bool = False,
        slow_delivery: bool = False,
        delivery_fee: Decimal | int | float | str | None = None,
        surge_fee: Decimal | int | float | str | None = None,
        pricing_frozen: bool = False,
        actual_delivery_time: datetime | None = None,
        refund_amount: Decimal | int | float | str = Decimal("0.00"),
        cancellation_fee: Decimal | int | float | str = Decimal("0.00"),
        wallet_credit: Decimal | int | float | str = Decimal("0.00"),
        free_coupon: bool = False,
        **kwargs,
    ) -> None:
        # Validate identifiers
        for field_name, value in [
            ("Order ID", order_id),
            ("Customer ID", customer_id),
        ]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        self.order_id = order_id
        self.customer_id = customer_id

        # Positional arguments unpacking if passed
        pos_items = None
        pos_policy = None
        pos_lat = None
        pos_lon = None
        pos_time = None

        if args:
            if isinstance(args[0], (list, tuple)) or args[0] is None:
                pos_items = list(args[0]) if args[0] is not None else []
                if len(args) >= 2:
                    pos_policy = args[1]
            elif isinstance(args[0], (int, float)):
                pos_lat = float(args[0])
                if len(args) >= 2 and isinstance(args[1], (int, float)):
                    pos_lon = float(args[1])
                if len(args) >= 3 and isinstance(args[2], datetime):
                    pos_time = args[2]
                if len(args) >= 4 and isinstance(args[3], (list, tuple)):
                    pos_items = list(args[3])
                if len(args) >= 5:
                    pos_policy = args[4]

        # Items list
        resolved_items = items if items is not None else (pos_items if pos_items is not None else [])
        if not isinstance(resolved_items, (list, tuple)):
            raise TypeError(
                f"Items must be a list of OrderItem objects, got {type(resolved_items).__name__}."
            )
        self.items = list(resolved_items)
        for item in self.items:
            if not isinstance(item, OrderItem):
                raise TypeError(
                    f"All order items must be OrderItem instances, got {type(item).__name__}."
                )

        # Substitution policy / preference
        raw_policy = (
            substitution_preference
            if substitution_preference is not None
            else (substitution_policy if substitution_policy is not None else pos_policy)
        )
        if raw_policy is None:
            self._substitution_policy = SubstitutionPolicy.NONE
        elif isinstance(raw_policy, str):
            try:
                self._substitution_policy = SubstitutionPolicy(raw_policy)
            except ValueError:
                try:
                    self._substitution_policy = SubstitutionPolicy[raw_policy.upper()]
                except (KeyError, AttributeError):
                    raise ValueError(f"Invalid substitution policy: {raw_policy}")
        elif isinstance(raw_policy, SubstitutionPolicy):
            self._substitution_policy = raw_policy
        else:
            raise TypeError(
                f"substitution_policy must be SubstitutionPolicy or str, got {type(raw_policy).__name__}."
            )

        # Delivery location and coordinates
        if delivery_location is not None and not isinstance(delivery_location, Coordinates):
            raise TypeError(
                f"delivery_location must be a Coordinates instance or None, got {type(delivery_location).__name__}."
            )

        resolved_lat = customer_lat if customer_lat is not None else (pos_lat if pos_lat is not None else None)
        resolved_lon = customer_lon if customer_lon is not None else (pos_lon if pos_lon is not None else None)

        if delivery_location is not None:
            self.delivery_location = delivery_location
            self.customer_lat = float(delivery_location.latitude)
            self.customer_lon = float(delivery_location.longitude)
        elif resolved_lat is not None or resolved_lon is not None:
            self.customer_lat = float(resolved_lat or 0.0)
            self.customer_lon = float(resolved_lon or 0.0)
            if self.customer_lat != 0.0 or self.customer_lon != 0.0:
                self.delivery_location = Coordinates(latitude=self.customer_lat, longitude=self.customer_lon)
            else:
                self.delivery_location = None
        else:
            self.customer_lat = 0.0
            self.customer_lon = 0.0
            self.delivery_location = None

        # Datetime fields
        resolved_order_time = order_time if order_time is not None else pos_time
        for field_name, value in [
            ("order_time", resolved_order_time),
            ("promised_eta", promised_eta),
            ("actual_delivery_time", actual_delivery_time),
        ]:
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, datetime):
                    raise TypeError(
                        f"{field_name} must be a datetime or None, got {type(value).__name__}."
                    )
        self.order_time = resolved_order_time
        self.promised_eta = promised_eta
        self.actual_delivery_time = actual_delivery_time

        # Timezone consistency check
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

        # Status validation
        if isinstance(status, str) and not isinstance(status, OrderState):
            try:
                self.status = OrderState(status)
            except ValueError:
                try:
                    self.status = OrderState[status.upper()]
                except (KeyError, AttributeError):
                    raise ValueError(f"Invalid order status: {status}")
        elif isinstance(status, OrderState):
            self.status = status
        else:
            raise TypeError(
                f"status must be an OrderState or valid str, got {type(status).__name__}."
            )

        # Allocated store ID / store_id
        resolved_store = allocated_store_id if allocated_store_id is not None else store_id
        if resolved_store is not None:
            if isinstance(resolved_store, bool) or not isinstance(resolved_store, str):
                raise TypeError(
                    f"allocated_store_id must be a string or None, got {type(resolved_store).__name__}."
                )
            if not resolved_store.strip():
                raise ValueError("allocated_store_id must not be empty.")
        self.allocated_store_id = resolved_store

        # Boolean flags
        if not isinstance(delayed_promise, bool):
            raise TypeError(
                f"delayed_promise must be a boolean, got {type(delayed_promise).__name__}."
            )
        self.delayed_promise = delayed_promise

        if not isinstance(slow_delivery, bool):
            raise TypeError(
                f"slow_delivery must be a boolean, got {type(slow_delivery).__name__}."
            )
        self.slow_delivery = slow_delivery

        if not isinstance(free_coupon, bool):
            raise TypeError(
                f"free_coupon must be a boolean, got {type(free_coupon).__name__}."
            )
        self.free_coupon = free_coupon

        if not isinstance(pricing_frozen, bool):
            raise TypeError(
                f"pricing_frozen must be a boolean, got {type(pricing_frozen).__name__}."
            )
        self.pricing_frozen = pricing_frozen

        # Fees
        for fee_name, fee_val in [
            ("delivery_fee", delivery_fee),
            ("surge_fee", surge_fee),
        ]:
            if fee_val is None:
                resolved_fee = Decimal("0.00")
            else:
                if isinstance(fee_val, bool):
                    raise TypeError(f"{fee_name} cannot be a boolean, got bool.")
                if not isinstance(fee_val, Decimal):
                    try:
                        resolved_fee = Decimal(str(fee_val))
                    except (InvalidOperation, TypeError, ValueError) as err:
                        raise ValueError(f"Invalid {fee_name}: {fee_val}") from err
                else:
                    resolved_fee = fee_val
                if not resolved_fee.is_finite():
                    raise ValueError(
                        f"{fee_name} must be a finite number, got {resolved_fee}."
                    )
                if resolved_fee < Decimal("0"):
                    raise ValueError(f"{fee_name} cannot be negative, got {resolved_fee}.")
            setattr(self, fee_name, resolved_fee)

        # Other financial fields
        for fee_name, fee_val in [
            ("refund_amount", refund_amount),
            ("cancellation_fee", cancellation_fee),
            ("wallet_credit", wallet_credit),
        ]:
            if isinstance(fee_val, bool):
                raise TypeError(f"{fee_name} cannot be a boolean, got bool.")
            if not isinstance(fee_val, Decimal):
                try:
                    resolved_fee = Decimal(str(fee_val))
                except (InvalidOperation, TypeError, ValueError) as err:
                    raise ValueError(f"Invalid {fee_name}: {fee_val}") from err
            else:
                resolved_fee = fee_val
            if not resolved_fee.is_finite():
                raise ValueError(
                    f"{fee_name} must be a finite number, got {resolved_fee}."
                )
            if resolved_fee < Decimal("0"):
                raise ValueError(f"{fee_name} cannot be negative, got {resolved_fee}.")
            setattr(self, fee_name, resolved_fee)

    @property
    def substitution_policy(self) -> SubstitutionPolicy:
        return self._substitution_policy

    @substitution_policy.setter
    def substitution_policy(self, value: SubstitutionPolicy | str) -> None:
        if isinstance(value, str):
            try:
                self._substitution_policy = SubstitutionPolicy(value)
            except ValueError:
                try:
                    self._substitution_policy = SubstitutionPolicy[value.upper()]
                except (KeyError, AttributeError):
                    raise ValueError(f"Invalid substitution policy: {value}")
        elif isinstance(value, SubstitutionPolicy):
            self._substitution_policy = value
        else:
            raise TypeError(
                f"substitution_policy must be SubstitutionPolicy or str, got {type(value).__name__}."
            )

    @property
    def substitution_preference(self) -> SubstitutionPolicy:
        return self.substitution_policy

    @substitution_preference.setter
    def substitution_preference(self, value: SubstitutionPolicy | str) -> None:
        self.substitution_policy = value

    @property
    def store_id(self) -> str | None:
        return self.allocated_store_id

    @store_id.setter
    def store_id(self, value: str | None) -> None:
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"allocated_store_id must be a string or None, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError("allocated_store_id must not be empty.")
        self.allocated_store_id = value

    @property
    def original_total_value(self) -> Decimal:
        return sum((item.original_line_value for item in self.items), Decimal("0.00"))

    @property
    def fulfilled_total_value(self) -> Decimal:
        return sum((item.fulfilled_line_value for item in self.items), Decimal("0.00"))

    @property
    def total_delivery_fee(self) -> Decimal:
        return (self.delivery_fee or Decimal("0.00")) + (self.surge_fee or Decimal("0.00"))
