from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Protocol, runtime_checkable

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.models.store import Store
from ps031.services.lifecycle import (
    LifecycleTransitionInterface,
    LifecycleTransitionService,
)
from ps031.services.pricing_service import (
    PricingService,
    PricingServiceInterface,
)
from ps031.utils.distance import haversine_distance

MAX_SERVICEABILITY_DISTANCE_KM: float = 3.00


@dataclass(frozen=True)
class StoreEvaluation:
    """Detailed serviceability, stock coverage, and load evaluation for a store."""

    store: Store
    distance_km: float
    is_open: bool
    is_weather_held: bool
    is_serviceable: bool
    has_100_percent_stock: bool
    line_fill_ratio: float
    current_slot_load: int


@dataclass(frozen=True)
class AllocationResult:
    """Structured outcome of an order allocation attempt under BR-01 and BR-02."""

    selected_store: Store | None
    is_allocated: bool
    rejection_reason: str | None
    evaluations: list[StoreEvaluation]


@runtime_checkable
class AllocationServiceInterface(Protocol):
    """Protocol defining the dark store allocation service interface."""

    def is_serviceable(
        self,
        store: Store,
        order: Order,
        reference_time: datetime | None = None,
    ) -> bool:
        """Evaluate if a store is serviceable for an order under BR-01."""
        ...

    def evaluate_store(
        self,
        store: Store,
        order: Order,
        current_slot_load: int = 0,
        reference_time: datetime | None = None,
    ) -> StoreEvaluation:
        """Compute full serviceability, inventory coverage, and load evaluation for a store."""
        ...

    def allocate_store(
        self,
        order: Order,
        stores: Sequence[Store],
        slot_loads: Mapping[str, int] | None = None,
        reference_time: datetime | None = None,
        apply_lifecycle_transition: bool = True,
    ) -> AllocationResult:
        """Select the best eligible store under BR-01 and BR-02 and optionally transition order state."""
        ...


class AllocationService:
    """Implements BR-01 Store Serviceability and BR-02 Store Selection."""

    def __init__(
        self,
        lifecycle_service: LifecycleTransitionInterface | None = None,
        pricing_service: PricingServiceInterface | None = None,
    ) -> None:
        self._lifecycle_service = (
            lifecycle_service
            if lifecycle_service is not None
            else LifecycleTransitionService()
        )
        self._pricing_service = (
            pricing_service
            if pricing_service is not None
            else PricingService()
        )

    def _resolve_evaluation_time(
        self, order: Order, reference_time: datetime | None
    ) -> datetime:
        """Determine the evaluation timestamp, raising ValueError if absent."""
        eval_time = reference_time if reference_time is not None else order.order_time
        if eval_time is None:
            raise ValueError(
                "Evaluation timestamp is required (provide reference_time or set order.order_time)."
            )
        if isinstance(eval_time, bool) or not isinstance(eval_time, datetime):
            raise TypeError(
                f"Evaluation timestamp must be a datetime, got {type(eval_time).__name__}."
            )
        return eval_time

    def _resolve_order_delivery_location(self, order: Order) -> Coordinates:
        """Validate and return the order delivery coordinates."""
        if order.delivery_location is None:
            raise ValueError(
                "Order delivery_location is required to evaluate store serviceability."
            )
        if isinstance(order.delivery_location, bool) or not isinstance(
            order.delivery_location, Coordinates
        ):
            raise TypeError(
                f"delivery_location must be a Coordinates instance, got {type(order.delivery_location).__name__}."
            )
        return order.delivery_location

    def _extract_query_time(
        self, eval_time: datetime, operating_hours: OperatingHours
    ) -> time:
        """Extract evaluation time while strictly enforcing timezone-awareness parity with store hours."""
        eval_is_aware = eval_time.tzinfo is not None
        hours_is_aware = operating_hours.open_time.tzinfo is not None
        if eval_is_aware != hours_is_aware:
            raise TypeError(
                f"Cannot compare {'timezone-aware' if eval_is_aware else 'timezone-naive'} evaluation timestamp "
                f"with {'timezone-aware' if hours_is_aware else 'timezone-naive'} store operating hours."
            )
        return eval_time.timetz() if eval_is_aware else eval_time.time()

    def is_serviceable(
        self,
        store: Store,
        order: Order,
        reference_time: datetime | None = None,
    ) -> bool:
        """Evaluate whether a store meets BR-01 serviceability conditions.

        A store is serviceable when:
        1. Rounded Haversine distance is <= 3.00 km.
        2. Current time is within store operating interval [open_time, close_time).
        3. Weather hold is absent or expired (active only when weather_hold_until > eval_time).
        """
        eval_time = self._resolve_evaluation_time(order, reference_time)
        customer_loc = self._resolve_order_delivery_location(order)

        # 1. Distance check
        distance_km = haversine_distance(store.location, customer_loc)
        if distance_km > MAX_SERVICEABILITY_DISTANCE_KM:
            return False

        # 2. Operating hours check with explicit timezone parity enforcement
        query_time = self._extract_query_time(eval_time, store.operating_hours)
        if not store.operating_hours.is_open(query_time):
            return False

        # 3. Weather hold check
        if store.weather_hold_until is not None:
            if (store.weather_hold_until.tzinfo is None) != (eval_time.tzinfo is None):
                raise TypeError(
                    "Cannot compare timezone-aware and timezone-naive timestamps for weather_hold_until."
                )
            if store.weather_hold_until > eval_time:
                return False

        return True

    def evaluate_store(
        self,
        store: Store,
        order: Order,
        current_slot_load: int = 0,
        reference_time: datetime | None = None,
    ) -> StoreEvaluation:
        """Compute complete evaluation data for a store relative to an order."""
        if not order.items:
            raise ValueError("Order must contain at least one item.")

        eval_time = self._resolve_evaluation_time(order, reference_time)
        customer_loc = self._resolve_order_delivery_location(order)

        # Distance
        distance_km = haversine_distance(store.location, customer_loc)

        # Operating hours with explicit timezone parity enforcement
        query_time = self._extract_query_time(eval_time, store.operating_hours)
        is_open = store.operating_hours.is_open(query_time)

        # Weather hold
        if store.weather_hold_until is not None:
            if (store.weather_hold_until.tzinfo is None) != (eval_time.tzinfo is None):
                raise TypeError(
                    "Cannot compare timezone-aware and timezone-naive timestamps for weather_hold_until."
                )
            is_weather_held = store.weather_hold_until > eval_time
        else:
            is_weather_held = False

        is_serviceable = (
            distance_km <= MAX_SERVICEABILITY_DISTANCE_KM
            and is_open
            and not is_weather_held
        )

        # Aggregate requested quantities across all order lines by SKU
        requested_by_sku: dict[str, int] = {}
        for item in order.items:
            requested_by_sku[item.product_id] = (
                requested_by_sku.get(item.product_id, 0) + item.quantity
            )

        total_requested = sum(requested_by_sku.values())
        total_fulfillable = 0
        for sku, req_qty in requested_by_sku.items():
            avail = store.inventory.get(sku, 0)
            total_fulfillable += min(avail, req_qty)

        line_fill_ratio = (
            total_fulfillable / total_requested if total_requested > 0 else 0.0
        )
        has_100_percent = total_fulfillable == total_requested

        return StoreEvaluation(
            store=store,
            distance_km=distance_km,
            is_open=is_open,
            is_weather_held=is_weather_held,
            is_serviceable=is_serviceable,
            has_100_percent_stock=has_100_percent,
            line_fill_ratio=line_fill_ratio,
            current_slot_load=current_slot_load,
        )

    def allocate_store(
        self,
        order: Order,
        stores: Sequence[Store],
        slot_loads: Mapping[str, int] | None = None,
        reference_time: datetime | None = None,
        apply_lifecycle_transition: bool = True,
    ) -> AllocationResult:
        """Select the best eligible store under BR-01 and BR-02 and optionally transition order state."""
        if not order.items:
            raise ValueError("Order must contain at least one item.")

        # Validate slot_loads if provided
        validated_slot_loads: dict[str, int] = {}
        if slot_loads is not None:
            if not isinstance(slot_loads, Mapping):
                raise TypeError(
                    f"slot_loads must be a Mapping, got {type(slot_loads).__name__}."
                )
            for s_id, load in slot_loads.items():
                if isinstance(load, bool) or not isinstance(load, int):
                    raise TypeError(
                        f"Slot load for store '{s_id}' must be an integer, got {type(load).__name__}."
                    )
                if load < 0:
                    raise ValueError(
                        f"Slot load for store '{s_id}' cannot be negative, got {load}."
                    )
                validated_slot_loads[s_id] = load

        evaluations: list[StoreEvaluation] = []
        for store in stores:
            load = validated_slot_loads.get(store.store_id, 0)
            evaluation = self.evaluate_store(
                store=store,
                order=order,
                current_slot_load=load,
                reference_time=reference_time,
            )
            evaluations.append(evaluation)

        serviceable_evals = [e for e in evaluations if e.is_serviceable]

        if not serviceable_evals:
            if apply_lifecycle_transition:
                self._lifecycle_service.validate_transition(order, OrderState.REJECTED)
                self._lifecycle_service.transition(order, OrderState.REJECTED)
            return AllocationResult(
                selected_store=None,
                is_allocated=False,
                rejection_reason="No serviceable store available within 3.00 km, open, and free of weather holds.",
                evaluations=evaluations,
            )

        # Partition: Complete stock (100%) vs Partial line-fill
        stores_with_100 = [e for e in serviceable_evals if e.has_100_percent_stock]
        if stores_with_100:
            candidates = stores_with_100
        else:
            max_line_fill = max(e.line_fill_ratio for e in serviceable_evals)
            candidates = [
                e for e in serviceable_evals if e.line_fill_ratio == max_line_fill
            ]

        # Break ties:
        # 1. Shortest rounded distance (ascending)
        # 2. Lowest current slot load (ascending)
        # 3. Deterministic store_id (lexicographically smallest)
        candidates.sort(
            key=lambda e: (e.distance_km, e.current_slot_load, e.store.store_id)
        )

        selected_eval = candidates[0]
        selected_store = selected_eval.store

        if apply_lifecycle_transition:
            # Validate before mutation to guarantee mutation safety on invalid state
            self._lifecycle_service.validate_transition(order, OrderState.ALLOCATED)

            # Reject inconsistent state: a RECEIVED order cannot enter allocation with pre-frozen pricing
            if order.pricing_frozen:
                raise ValueError(
                    f"Cannot allocate order '{order.order_id}': order has pricing_frozen=True in "
                    f"'{order.status.value}' state. Pricing must be calculated and frozen at allocation time."
                )

            # Calculate pricing before any mutations to ensure pricing failures do not touch order
            pricing_result = None
            if self._pricing_service is not None:
                pricing_result = self._pricing_service.calculate_pricing(
                    order=order,
                    store=selected_store,
                )

            # Snapshot state for atomic rollback if lifecycle transition fails
            prev_status = order.status
            prev_store_id = order.allocated_store_id
            prev_delivery_fee = order.delivery_fee
            prev_surge_fee = order.surge_fee
            prev_pricing_frozen = order.pricing_frozen

            try:
                if pricing_result is not None:
                    order.delivery_fee = pricing_result.delivery_fee
                    order.surge_fee = pricing_result.surge_fee
                    order.pricing_frozen = True

                order.allocated_store_id = selected_store.store_id
                self._lifecycle_service.transition(order, OrderState.ALLOCATED)
            except Exception:
                order.status = prev_status
                order.allocated_store_id = prev_store_id
                order.delivery_fee = prev_delivery_fee
                order.surge_fee = prev_surge_fee
                order.pricing_frozen = prev_pricing_frozen
                raise

        return AllocationResult(
            selected_store=selected_store,
            is_allocated=True,
            rejection_reason=None,
            evaluations=evaluations,
        )
