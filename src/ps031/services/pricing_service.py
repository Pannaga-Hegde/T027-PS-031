from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.models.store import Store

FREE_DELIVERY_THRESHOLD: Decimal = Decimal("199.00")
STANDARD_DELIVERY_FEE: Decimal = Decimal("25.00")
FREE_DELIVERY_FEE: Decimal = Decimal("0.00")
SURGE_FEE: Decimal = Decimal("15.00")
ZERO_SURGE_FEE: Decimal = Decimal("0.00")
SURGE_RIDER_THRESHOLD: int = 3


@dataclass(frozen=True)
class PricingResult:
    """Detailed pricing outcome for an order under BR-07."""

    order_value: Decimal
    delivery_fee: Decimal
    surge_fee: Decimal
    total_delivery_fee: Decimal
    is_free_delivery: bool
    is_surge_applied: bool


@runtime_checkable
class PricingServiceInterface(Protocol):
    """Protocol defining the delivery pricing service interface under BR-07."""

    def calculate_order_value(
        self,
        order: Order,
        use_fulfilled_quantities: bool = True,
    ) -> Decimal:
        """Calculate post-substitution order value under BR-07."""
        ...

    def calculate_delivery_fee(
        self,
        order_or_value: Order | Decimal | int | float | str,
        use_fulfilled_quantities: bool = True,
    ) -> Decimal:
        """Calculate delivery fee under BR-07."""
        ...

    def calculate_surge_fee(
        self,
        store_or_riders: Store | int,
    ) -> Decimal:
        """Calculate surge fee under BR-07."""
        ...

    def calculate_pricing(
        self,
        order: Order,
        store: Store | None = None,
        active_riders: int | None = None,
        use_fulfilled_quantities: bool = True,
    ) -> PricingResult:
        """Evaluate delivery pricing and surge charges for an order without mutating it."""
        ...

    def apply_pricing(
        self,
        order: Order,
        store: Store | None = None,
        active_riders: int | None = None,
        use_fulfilled_quantities: bool = True,
    ) -> PricingResult:
        """Calculate and freeze delivery and surge fees on an order."""
        ...


class PricingService:
    """
    Implements BR-07: Delivery Pricing and Surge Fee.

    Rules:
    1. Calculate order value using post-substitution fulfilled order lines and
       the original customer-charged unit prices (BR-05).
    2. If post-substitution order value is >= ₹199, delivery fee = ₹0.
    3. If order value is < ₹199, delivery fee = ₹25.
    4. Add a ₹15 surge fee if store has fewer than 3 active riders at allocation time (< 3).
    5. If active riders are 3 or more (>= 3), surge fee = ₹0.
    6. Delivery and surge fees must be frozen at allocation time. Later changes in rider
       availability or order contents must not silently recalculate an already allocated order's fees.
    """

    FREE_DELIVERY_THRESHOLD = FREE_DELIVERY_THRESHOLD
    STANDARD_DELIVERY_FEE = STANDARD_DELIVERY_FEE
    FREE_DELIVERY_FEE = FREE_DELIVERY_FEE
    SURGE_FEE = SURGE_FEE
    ZERO_SURGE_FEE = ZERO_SURGE_FEE
    SURGE_RIDER_THRESHOLD = SURGE_RIDER_THRESHOLD

    def __init__(self, stores: Sequence[Store] | None = None) -> None:
        self._stores: dict[str, Store] = {}
        if stores:
            for store in stores:
                self.register_store(store)

    def register_store(self, store: Store) -> None:
        """Register a store with the pricing service for store_id lookup."""
        if not isinstance(store, Store):
            raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
        self._stores[store.store_id] = store

    def _validate_order_value(self, value: Any) -> Decimal:
        """Validate and coerce monetary order value to Decimal."""
        if isinstance(value, bool):
            raise TypeError(f"order_value cannot be a boolean, got {type(value).__name__}.")
        if not isinstance(value, Decimal):
            try:
                resolved = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(f"Invalid order_value: {value}") from err
        else:
            resolved = value

        if not resolved.is_finite():
            raise ValueError(f"order_value must be a finite number, got {resolved}.")
        if resolved < Decimal("0.00"):
            raise ValueError(f"order_value cannot be negative: {resolved}")
        return resolved

    def _validate_active_riders(self, value: Any) -> int:
        """Validate active rider count."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"active_riders must be an integer, got {type(value).__name__}.")
        if value < 0:
            raise ValueError(f"active_riders cannot be negative: {value}")
        return value

    def _resolve_active_riders(
        self,
        order: Order,
        store: Store | None,
        active_riders: int | None,
    ) -> int:
        """Resolve active riders from explicit parameter, store instance, or registered store."""
        if active_riders is not None:
            return self._validate_active_riders(active_riders)
        if store is not None:
            if not isinstance(store, Store):
                raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
            return store.active_riders
        if order.allocated_store_id and order.allocated_store_id in self._stores:
            return self._stores[order.allocated_store_id].active_riders
        raise ValueError(
            "Either 'store' or 'active_riders' must be provided (or store registered) to determine surge fee."
        )

    def calculate_order_value(
        self,
        order: Order,
        use_fulfilled_quantities: bool = True,
    ) -> Decimal:
        """
        Calculate post-substitution order value under BR-07.
        Uses fulfilled order lines and original customer-charged unit prices (BR-05).
        """
        if not isinstance(order, Order):
            raise TypeError(f"order must be an Order instance, got {type(order).__name__}.")
        if not order.items:
            return Decimal("0.00")

        total = Decimal("0.00")
        for item in order.items:
            qty = item.fulfilled_quantity if use_fulfilled_quantities else item.quantity
            # Under BR-05, customer pays the original unit price for substituted units
            total += item.unit_price * qty
        return total

    def calculate_delivery_fee(
        self,
        order_or_value: Order | Decimal | int | float | str,
        use_fulfilled_quantities: bool = True,
    ) -> Decimal:
        """
        Calculate delivery fee under BR-07.
        - ₹0 if post-substitution order value >= ₹199.00
        - ₹25 if post-substitution order value < ₹199.00
        """
        if isinstance(order_or_value, Order):
            order_value = self.calculate_order_value(
                order_or_value, use_fulfilled_quantities=use_fulfilled_quantities
            )
        else:
            order_value = self._validate_order_value(order_or_value)

        if order_value >= self.FREE_DELIVERY_THRESHOLD:
            return self.FREE_DELIVERY_FEE
        return self.STANDARD_DELIVERY_FEE

    def calculate_surge_fee(
        self,
        store_or_riders: Store | int,
    ) -> Decimal:
        """
        Calculate surge fee under BR-07.
        - ₹15 if store has fewer than 3 active riders (< 3)
        - ₹0 if store has 3 or more active riders (>= 3)
        """
        if isinstance(store_or_riders, Store):
            active_riders = store_or_riders.active_riders
        else:
            active_riders = self._validate_active_riders(store_or_riders)

        if active_riders < self.SURGE_RIDER_THRESHOLD:
            return self.SURGE_FEE
        return self.ZERO_SURGE_FEE

    def calculate_pricing(
        self,
        order: Order,
        store: Store | None = None,
        active_riders: int | None = None,
        use_fulfilled_quantities: bool = True,
    ) -> PricingResult:
        """
        Pure decision evaluation of delivery pricing and surge charges for an order.
        Does not mutate the order.
        """
        if not isinstance(order, Order):
            raise TypeError(f"order must be an Order instance, got {type(order).__name__}.")

        riders = self._resolve_active_riders(
            order=order, store=store, active_riders=active_riders
        )
        order_value = self.calculate_order_value(
            order, use_fulfilled_quantities=use_fulfilled_quantities
        )
        delivery_fee = self.calculate_delivery_fee(order_value)
        surge_fee = self.calculate_surge_fee(riders)
        total_delivery_fee = delivery_fee + surge_fee

        return PricingResult(
            order_value=order_value,
            delivery_fee=delivery_fee,
            surge_fee=surge_fee,
            total_delivery_fee=total_delivery_fee,
            is_free_delivery=(delivery_fee == self.FREE_DELIVERY_FEE),
            is_surge_applied=(surge_fee > self.ZERO_SURGE_FEE),
        )

    def is_fee_frozen(self, order: Order) -> bool:
        """
        Check if an order's delivery and surge fees are frozen under BR-07.
        """
        if not isinstance(order, Order):
            raise TypeError(f"order must be an Order instance, got {type(order).__name__}.")
        return bool(order.pricing_frozen)

    def apply_pricing(
        self,
        order: Order,
        store: Store | None = None,
        active_riders: int | None = None,
        use_fulfilled_quantities: bool = True,
    ) -> PricingResult:
        """
        Calculate and assign delivery_fee and surge_fee to order under BR-07.
        Freezes the fees on the order (order.pricing_frozen = True).
        If fees are already frozen, raises ValueError.
        """
        if not isinstance(order, Order):
            raise TypeError(f"order must be an Order instance, got {type(order).__name__}.")

        if self.is_fee_frozen(order):
            raise ValueError(
                f"Cannot recalculate fees: order '{order.order_id}' pricing is already frozen "
                f"(delivery_fee={order.delivery_fee}, surge_fee={order.surge_fee})."
            )

        if order.status != OrderState.ALLOCATED:
            raise ValueError(
                f"Cannot apply and freeze pricing on unallocated order '{order.order_id}' "
                f"with status '{order.status.value}'. Pricing can only be applied and frozen once allocated."
            )

        pricing_result = self.calculate_pricing(
            order=order,
            store=store,
            active_riders=active_riders,
            use_fulfilled_quantities=use_fulfilled_quantities,
        )

        order.delivery_fee = pricing_result.delivery_fee
        order.surge_fee = pricing_result.surge_fee
        order.pricing_frozen = True

        return pricing_result


# Alias for explicit naming
DeliveryPricingService = PricingService
