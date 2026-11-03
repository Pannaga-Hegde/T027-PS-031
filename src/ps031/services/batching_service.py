from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any, Protocol, runtime_checkable

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.order import Order
from ps031.models.order_state import InvalidOrderTransitionError, OrderState
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store
from ps031.services.lifecycle import (
    LifecycleTransitionInterface,
    LifecycleTransitionService,
)
from ps031.services.picking_service import (
    PickingService,
    PickingServiceInterface,
)
from ps031.utils.distance import EARTH_RADIUS_KM, haversine_distance

MAX_BATCH_SIZE: int = 3
MAX_DROP_PAIRWISE_DISTANCE_KM: float = 1.00
MAX_ETA_DIFFERENCE_SECONDS: float = 240.0
DISPATCH_WAIT_MINUTES: int = 3


def _is_aware(dt: datetime) -> bool:
    """Return True if dt is timezone-aware under Python datetime semantics."""
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _validate_timezone_parity(
    dt1: datetime, dt2: datetime, name1: str, name2: str
) -> None:
    """Validate timezone-awareness parity between two datetimes."""
    is_aware1 = _is_aware(dt1)
    is_aware2 = _is_aware(dt2)
    if is_aware1 != is_aware2:
        raise TypeError(
            f"Cannot compare {'timezone-aware' if is_aware1 else 'timezone-naive'} {name1} "
            f"with {'timezone-aware' if is_aware2 else 'timezone-naive'} {name2}."
        )


def _unrounded_haversine(coord1: Coordinates, coord2: Coordinates) -> float:
    """Calculate unrounded Haversine distance in kilometres using R = 6371.0 km."""
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

    phi1 = math.radians(coord1.latitude)
    phi2 = math.radians(coord2.latitude)
    delta_phi = math.radians(coord2.latitude - coord1.latitude)
    delta_lambda = math.radians(coord2.longitude - coord1.longitude)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * (
        math.sin(delta_lambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


@dataclass(frozen=True)
class BatchEvaluation:
    """Outcome of batch compatibility evaluation under BR-08."""

    is_compatible: bool
    rejection_reason: str | None = None
    max_pairwise_distance_km: float = 0.0
    max_eta_diff_seconds: float = 0.0


@dataclass(frozen=True)
class BatchDispatchResult:
    """Structured outcome of a batch dispatch operation."""

    batch_id: str
    is_dispatched: bool
    dispatched_at: datetime | None
    rider_id: str
    order_ids: list[str]
    reason: str = ""


@dataclass(frozen=True)
class DeliveryCompletionResult:
    """Structured outcome of a batch delivery completion operation."""

    batch_id: str
    rider_id: str
    delivered_at: datetime
    delivered_order_ids: list[str]


@runtime_checkable
class BatchingServiceInterface(Protocol):
    """Protocol defining the rider batching service interface for BR-08."""

    def evaluate_compatibility(
        self,
        batch: Batch,
        candidate: Order,
        store: Store | None = None,
    ) -> BatchEvaluation:
        """Evaluate if candidate order satisfies BR-08 batching constraints."""
        ...

    def create_batch(
        self,
        batch_id: str,
        store: Store,
        rider: Rider,
        initial_orders: list[Order] | None = None,
        created_at: datetime | None = None,
        ready_at: datetime | None = None,
    ) -> Batch:
        """Create a new delivery batch for a store and rider atomically."""
        ...

    def add_order_to_batch(
        self,
        batch: Batch,
        order: Order,
        store: Store,
        ready_at: datetime | None = None,
    ) -> Batch:
        """Add an order to an existing batch, updating drop sequence and ETAs."""
        ...

    def mark_order_ready(
        self,
        batch: Batch,
        order: Order,
        ready_at: datetime | None = None,
    ) -> None:
        """Transition order to READY_FOR_DISPATCH and track batch dispatch timer."""
        ...

    def can_dispatch(self, batch: Batch, current_time: datetime) -> bool:
        """Check if batch is eligible for dispatch at current_time."""
        ...

    def dispatch_batch(
        self,
        batch: Batch,
        store: Store,
        rider: Rider,
        dispatched_at: datetime,
    ) -> BatchDispatchResult:
        """Validate and dispatch batch, transitioning orders and rider atomically."""
        ...

    def complete_delivery(
        self,
        batch: Batch,
        rider: Rider,
        delivered_at: datetime,
    ) -> DeliveryCompletionResult:
        """Complete batch delivery, transitioning orders to DELIVERED and releasing rider."""
        ...

    def cancel_batch(
        self,
        batch: Batch,
        rider: Rider | None = None,
        reason: str = "",
    ) -> None:
        """Cancel a batch, releasing assigned rider and restoring order ETAs."""
        ...

    def sequence_drops(
        self, store: Store, orders: Sequence[Order]
    ) -> list[Order]:
        """Sequence delivery drops deterministically by store distance, ETA, and order ID."""
        ...


class BatchingService:
    """Implements BR-08 (Rider Batching)."""

    def __init__(
        self,
        picking_service: PickingServiceInterface | None = None,
        lifecycle_service: LifecycleTransitionInterface | None = None,
    ) -> None:
        self._picking_service = picking_service or PickingService()
        self._lifecycle_service = (
            lifecycle_service or LifecycleTransitionService()
        )
        self._batches: dict[str, Batch] = {}
        self._order_active_batches: dict[str, str] = {}
        self._rider_active_batches: dict[str, str] = {}
        self._batch_stores: dict[str, str] = {}
        self._batch_first_ready: dict[str, datetime] = {}
        self._original_etas: dict[str, tuple[datetime, bool]] = {}
        self._assigned_riders: dict[str, Rider] = {}

    def get_batch(self, batch_id: str) -> Batch | None:
        """Retrieve a batch by its identifier."""
        return self._batches.get(batch_id)

    def restore_original_etas(self, orders: Sequence[Order]) -> None:
        """Restore original promised ETA and delayed_promise for provided orders."""
        for order in orders:
            if order.order_id in self._original_etas:
                orig_eta, orig_delayed = self._original_etas[order.order_id]
                order.promised_eta = orig_eta
                order.delayed_promise = orig_delayed

    def evaluate_compatibility(
        self,
        batch: Batch,
        candidate: Order,
        store: Store | None = None,
    ) -> BatchEvaluation:
        """Evaluate if candidate order satisfies BR-08 batching constraints.

        Constraints checked:
        1. Batch size limit (maximum 3 orders).
        2. Candidate has non-empty delivery_location and valid promised_eta.
        3. All promised ETAs have matching timezone-awareness.
        4. Global order uniqueness: candidate not already in another active batch.
        5. Single store constraint: candidate matches batch/store allocated store.
        6. Pairwise drop distance <= 1.00 km (unrounded Haversine, inclusive).
        7. Max ETA difference <= 4 minutes (240 seconds, inclusive).
        """
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(candidate, bool) or not isinstance(candidate, Order):
            raise TypeError(
                f"candidate must be an Order instance, got {type(candidate).__name__}."
            )
        if store is not None and (
            isinstance(store, bool) or not isinstance(store, Store)
        ):
            raise TypeError(
                f"store must be a Store instance or None, got {type(store).__name__}."
            )

        # Promised ETA presence and validation
        if candidate.promised_eta is None:
            raise ValueError(
                f"Order '{candidate.order_id}' has no promised_eta required for batching."
            )
        if isinstance(candidate.promised_eta, bool) or not isinstance(
            candidate.promised_eta, datetime
        ):
            raise TypeError(
                f"candidate.promised_eta must be a datetime, got {type(candidate.promised_eta).__name__}."
            )

        for existing_order in batch.orders:
            if existing_order.promised_eta is None:
                raise ValueError(
                    f"Batch order '{existing_order.order_id}' has no promised_eta."
                )
            if isinstance(existing_order.promised_eta, bool) or not isinstance(
                existing_order.promised_eta, datetime
            ):
                raise TypeError(
                    f"Batch order '{existing_order.order_id}' promised_eta must be a datetime, got {type(existing_order.promised_eta).__name__}."
                )
            _validate_timezone_parity(
                candidate.promised_eta,
                existing_order.promised_eta,
                f"candidate '{candidate.order_id}' promised_eta",
                f"batch order '{existing_order.order_id}' promised_eta",
            )

        # Delivery location validation
        if candidate.delivery_location is None:
            raise ValueError(
                f"Order '{candidate.order_id}' has no delivery_location required for batching."
            )
        if isinstance(candidate.delivery_location, bool) or not isinstance(
            candidate.delivery_location, Coordinates
        ):
            raise TypeError(
                f"Order '{candidate.order_id}' delivery_location must be a Coordinates instance."
            )

        for existing_order in batch.orders:
            if existing_order.delivery_location is None:
                raise ValueError(
                    f"Batch order '{existing_order.order_id}' has no delivery_location."
                )
            if isinstance(
                existing_order.delivery_location, bool
            ) or not isinstance(
                existing_order.delivery_location, Coordinates
            ):
                raise TypeError(
                    f"Batch order '{existing_order.order_id}' delivery_location must be Coordinates."
                )

        # 1. Capacity check
        if len(batch.orders) >= MAX_BATCH_SIZE:
            return BatchEvaluation(
                is_compatible=False,
                rejection_reason=f"Batch has reached maximum capacity of {MAX_BATCH_SIZE} orders.",
            )

        # 2. Global order active batch check
        active_batch_id = self._order_active_batches.get(candidate.order_id)
        if active_batch_id is not None and active_batch_id != batch.batch_id:
            return BatchEvaluation(
                is_compatible=False,
                rejection_reason=(
                    f"Order '{candidate.order_id}' is already assigned to active batch '{active_batch_id}'."
                ),
            )

        # 3. Store constraint check
        expected_store_id: str | None = None
        if store is not None:
            expected_store_id = store.store_id
        elif batch.batch_id in self._batch_stores:
            expected_store_id = self._batch_stores[batch.batch_id]
        elif batch.orders:
            expected_store_id = batch.orders[0].allocated_store_id

        candidate_store_id = (
            candidate.allocated_store_id or candidate.store_id
        )
        if (
            expected_store_id is not None
            and candidate_store_id != expected_store_id
        ):
            return BatchEvaluation(
                is_compatible=False,
                rejection_reason=(
                    f"Order allocated store '{candidate_store_id}' does not match batch store '{expected_store_id}'."
                ),
            )

        # 4. Pairwise unrounded Haversine distance check across all drops
        all_candidate_orders = list(batch.orders) + [candidate]
        max_dist = 0.0
        for i in range(len(all_candidate_orders)):
            for j in range(i + 1, len(all_candidate_orders)):
                d = _unrounded_haversine(
                    all_candidate_orders[i].delivery_location,
                    all_candidate_orders[j].delivery_location,
                )
                if d > max_dist:
                    max_dist = d
                if d > MAX_DROP_PAIRWISE_DISTANCE_KM:
                    return BatchEvaluation(
                        is_compatible=False,
                        rejection_reason=(
                            f"Delivery distance {d:.4f} km between order "
                            f"'{all_candidate_orders[i].order_id}' and '{all_candidate_orders[j].order_id}' "
                            f"exceeds {MAX_DROP_PAIRWISE_DISTANCE_KM:.2f} km limit."
                        ),
                        max_pairwise_distance_km=max_dist,
                    )

        # 5. Promised ETA difference check
        all_etas = [o.promised_eta for o in all_candidate_orders]
        min_eta = min(all_etas)
        max_eta = max(all_etas)
        eta_diff_seconds = abs((max_eta - min_eta).total_seconds())

        if eta_diff_seconds > MAX_ETA_DIFFERENCE_SECONDS:
            return BatchEvaluation(
                is_compatible=False,
                rejection_reason=(
                    f"ETA difference of {eta_diff_seconds / 60.0:.2f} minutes exceeds "
                    f"the {int(MAX_ETA_DIFFERENCE_SECONDS // 60)}-minute maximum threshold."
                ),
                max_pairwise_distance_km=max_dist,
                max_eta_diff_seconds=eta_diff_seconds,
            )

        return BatchEvaluation(
            is_compatible=True,
            rejection_reason=None,
            max_pairwise_distance_km=max_dist,
            max_eta_diff_seconds=eta_diff_seconds,
        )

    def sequence_drops(
        self, store: Store, orders: Sequence[Order]
    ) -> list[Order]:
        """Sequence delivery drops deterministically.

        Order of precedence:
        1. Ascending distance from store location to customer delivery location.
        2. Earliest order.promised_eta.
        3. Lexicographically smallest order.order_id.
        """
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )

        def _sort_key(order: Order) -> tuple[float, datetime, str]:
            if order.delivery_location is None:
                raise ValueError(
                    f"Order '{order.order_id}' has no delivery_location."
                )
            dist = haversine_distance(
                store.location, order.delivery_location
            )
            return (dist, order.promised_eta, order.order_id)

        return sorted(orders, key=_sort_key)

    def create_batch(
        self,
        batch_id: str,
        store: Store,
        rider: Rider,
        initial_orders: list[Order] | None = None,
        created_at: datetime | None = None,
        ready_at: datetime | None = None,
    ) -> Batch:
        """Create a new delivery batch for a store and rider atomically.

        All initial orders and inputs are validated before registering the batch
        or reserving the rider. If any validation or recalculation fails, no state
        or mappings are retained, allowing the rider and batch_id to be reused immediately.
        """
        if isinstance(batch_id, bool) or not isinstance(batch_id, str):
            raise TypeError(
                f"batch_id must be a string, got {type(batch_id).__name__}."
            )
        if not batch_id.strip():
            raise ValueError("batch_id must not be empty.")
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )
        if isinstance(rider, bool) or not isinstance(rider, Rider):
            raise TypeError(
                f"rider must be a Rider instance, got {type(rider).__name__}."
            )

        if rider.status != RiderStatus.AVAILABLE:
            raise ValueError(
                f"Rider '{rider.rider_id}' is not available (status: {rider.status.value})."
            )

        active_batch_for_rider = self._rider_active_batches.get(rider.rider_id)
        if active_batch_for_rider is not None:
            raise ValueError(
                f"Rider '{rider.rider_id}' is already assigned to active batch '{active_batch_for_rider}'."
            )

        existing_batch = self._batches.get(batch_id)
        if existing_batch is not None and existing_batch.status in (
            BatchStatus.CREATED,
            BatchStatus.DISPATCHED,
        ):
            raise ValueError(
                f"Batch '{batch_id}' already exists and is active."
            )

        if created_at is None:
            if initial_orders and initial_orders[0].promised_eta is not None:
                tz = initial_orders[0].promised_eta.tzinfo
                created_at = (
                    datetime.now(tz) if tz is not None else datetime.now()
                )
            else:
                created_at = datetime.now()
        elif isinstance(created_at, bool) or not isinstance(created_at, datetime):
            raise TypeError(
                f"created_at must be a datetime, got {type(created_at).__name__}."
            )

        if ready_at is not None:
            if isinstance(ready_at, bool) or not isinstance(ready_at, datetime):
                raise TypeError(
                    f"ready_at must be a datetime, got {type(ready_at).__name__}."
                )
            _validate_timezone_parity(
                ready_at, created_at, "ready_at", "created_at"
            )
            if ready_at < created_at:
                raise ValueError("ready_at cannot be earlier than created_at.")

        sequenced: list[Order] = []
        if initial_orders:
            if len(initial_orders) > MAX_BATCH_SIZE:
                raise ValueError(
                    f"Initial orders count ({len(initial_orders)}) exceeds maximum capacity of {MAX_BATCH_SIZE} orders."
                )

            # Check for duplicate order IDs within initial orders
            seen_ids: set[str] = set()
            for ord_item in initial_orders:
                if ord_item.order_id in seen_ids:
                    raise ValueError(
                        f"Duplicate order ID '{ord_item.order_id}' found in initial orders."
                    )
                seen_ids.add(ord_item.order_id)

            # Validate each initial order incrementally in a dummy uncommitted batch
            dummy_batch = Batch(
                batch_id=batch_id,
                rider_id=rider.rider_id,
                created_at=created_at,
                orders=[],
                status=BatchStatus.CREATED,
            )
            for ord_item in initial_orders:
                eval_res = self.evaluate_compatibility(
                    dummy_batch, ord_item, store=store
                )
                if not eval_res.is_compatible:
                    raise ValueError(
                        f"Initial order '{ord_item.order_id}' is incompatible: {eval_res.rejection_reason}"
                    )
                dummy_batch.orders.append(ord_item)

            sequenced = self.sequence_drops(store, initial_orders)

            snapshot = {
                o.order_id: (o.promised_eta, o.delayed_promise)
                for o in sequenced
            }
            newly_recorded_etas: list[str] = []
            for o_id, vals in snapshot.items():
                if o_id not in self._original_etas:
                    self._original_etas[o_id] = vals
                    newly_recorded_etas.append(o_id)

            try:
                for drop_idx, drop_order in enumerate(sequenced):
                    self._picking_service.recalculate_eta(
                        store=store, order=drop_order, earlier_drops=drop_idx
                    )
            except Exception:
                for ord_item in sequenced:
                    orig_eta, orig_delayed = snapshot[ord_item.order_id]
                    ord_item.promised_eta = orig_eta
                    ord_item.delayed_promise = orig_delayed
                for k in newly_recorded_etas:
                    self._original_etas.pop(k, None)
                raise

        # All validations and recalculations succeeded; create and register batch atomically
        batch = Batch(
            batch_id=batch_id,
            rider_id=rider.rider_id,
            created_at=created_at,
            orders=sequenced,
            status=BatchStatus.CREATED,
        )

        self._batches[batch_id] = batch
        self._batch_stores[batch_id] = store.store_id
        self._rider_active_batches[rider.rider_id] = batch_id
        self._assigned_riders[batch_id] = rider

        for o in sequenced:
            self._order_active_batches[o.order_id] = batch_id

        if ready_at is not None:
            self._batch_first_ready[batch_id] = ready_at

        return batch

    def add_order_to_batch(
        self,
        batch: Batch,
        order: Order,
        store: Store,
        ready_at: datetime | None = None,
    ) -> Batch:
        """Add an order to an existing batch, updating drop sequence and ETAs."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )

        if batch.status != BatchStatus.CREATED:
            raise ValueError(
                f"Cannot add order to batch '{batch.batch_id}' with status '{batch.status.value}'."
            )

        # Idempotent return if order is already in this batch
        if any(o.order_id == order.order_id for o in batch.orders):
            return batch

        eval_result = self.evaluate_compatibility(batch, order, store=store)
        if not eval_result.is_compatible:
            raise ValueError(
                f"Cannot add order '{order.order_id}' to batch '{batch.batch_id}': {eval_result.rejection_reason}"
            )

        if ready_at is not None:
            if isinstance(ready_at, bool) or not isinstance(ready_at, datetime):
                raise TypeError(
                    f"ready_at must be a datetime, got {type(ready_at).__name__}."
                )
            _validate_timezone_parity(
                ready_at, batch.created_at, "ready_at", "batch.created_at"
            )
            if ready_at < batch.created_at:
                raise ValueError("ready_at cannot be earlier than created_at.")

        # Record snapshot of current ETAs for rollback on error
        snapshot: dict[str, tuple[datetime, bool]] = {
            o.order_id: (o.promised_eta, o.delayed_promise) for o in batch.orders
        }
        snapshot[order.order_id] = (order.promised_eta, order.delayed_promise)

        newly_recorded_etas: list[str] = []
        for o_id, vals in snapshot.items():
            if o_id not in self._original_etas:
                self._original_etas[o_id] = vals
                newly_recorded_etas.append(o_id)

        candidate_list = list(batch.orders) + [order]
        sequenced = self.sequence_drops(store, candidate_list)

        try:
            for drop_idx, drop_order in enumerate(sequenced):
                self._picking_service.recalculate_eta(
                    store=store, order=drop_order, earlier_drops=drop_idx
                )
        except Exception:
            for drop_order in sequenced:
                orig_eta, orig_delayed = snapshot[drop_order.order_id]
                drop_order.promised_eta = orig_eta
                drop_order.delayed_promise = orig_delayed
            for k in newly_recorded_etas:
                self._original_etas.pop(k, None)
            raise

        batch.orders = sequenced
        self._order_active_batches[order.order_id] = batch.batch_id

        # Track first ready timestamp if supplied, preserving earlier timestamp
        if ready_at is not None:
            if batch.batch_id not in self._batch_first_ready:
                self._batch_first_ready[batch.batch_id] = ready_at

        return batch

    def mark_order_ready(
        self,
        batch: Batch,
        order: Order,
        ready_at: datetime | None = None,
    ) -> None:
        """Transition order to READY_FOR_DISPATCH and track batch dispatch timer."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )

        if not any(o.order_id == order.order_id for o in batch.orders):
            raise ValueError(
                f"Order '{order.order_id}' does not belong to batch '{batch.batch_id}'."
            )

        if ready_at is not None:
            if isinstance(ready_at, bool) or not isinstance(ready_at, datetime):
                raise TypeError(
                    f"ready_at must be a datetime, got {type(ready_at).__name__}."
                )
            _validate_timezone_parity(
                ready_at, batch.created_at, "ready_at", "batch.created_at"
            )
            if ready_at < batch.created_at:
                raise ValueError("ready_at cannot be earlier than created_at.")
            transition_time = ready_at
        else:
            tz = batch.created_at.tzinfo
            transition_time = (
                datetime.now(tz) if tz is not None else datetime.now()
            )

        self._lifecycle_service.validate_transition(
            order, OrderState.READY_FOR_DISPATCH
        )
        self._lifecycle_service.transition(
            order, OrderState.READY_FOR_DISPATCH
        )

        # Preserve the first order's timestamp when subsequent orders are marked ready
        if batch.batch_id not in self._batch_first_ready:
            self._batch_first_ready[batch.batch_id] = transition_time

    def can_dispatch(self, batch: Batch, current_time: datetime) -> bool:
        """Check if batch is eligible for dispatch at current_time.

        Rules:
        - Batch must be in CREATED status and non-empty.
        - All orders in batch must be in READY_FOR_DISPATCH.
        - Immediate dispatch if batch has reached MAX_BATCH_SIZE (3).
        - Otherwise, dispatch 3 minutes after first order reaches READY_FOR_DISPATCH.
        """
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(current_time, bool) or not isinstance(
            current_time, datetime
        ):
            raise TypeError(
                f"current_time must be a datetime, got {type(current_time).__name__}."
            )

        if batch.status != BatchStatus.CREATED or not batch.orders:
            return False

        _validate_timezone_parity(
            current_time, batch.created_at, "current_time", "batch.created_at"
        )
        for o in batch.orders:
            if o.order_time is not None:
                _validate_timezone_parity(
                    current_time,
                    o.order_time,
                    "current_time",
                    f"order '{o.order_id}' order_time",
                )

        if any(o.status != OrderState.READY_FOR_DISPATCH for o in batch.orders):
            return False

        if len(batch.orders) == MAX_BATCH_SIZE:
            return True

        first_ready = self._batch_first_ready.get(batch.batch_id)
        if first_ready is None:
            return False

        _validate_timezone_parity(
            current_time, first_ready, "current_time", "first_ready"
        )
        return current_time >= first_ready + timedelta(
            minutes=DISPATCH_WAIT_MINUTES
        )

    def dispatch_batch(
        self,
        batch: Batch,
        store: Store,
        rider: Rider,
        dispatched_at: datetime,
    ) -> BatchDispatchResult:
        """Validate and dispatch batch, transitioning orders and rider atomically.

        All preconditions and lifecycle transitions are validated prior to mutating
        batch, order, or rider states.
        """
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )
        if isinstance(rider, bool) or not isinstance(rider, Rider):
            raise TypeError(
                f"rider must be a Rider instance, got {type(rider).__name__}."
            )
        if isinstance(dispatched_at, bool) or not isinstance(
            dispatched_at, datetime
        ):
            raise TypeError(
                f"dispatched_at must be a datetime, got {type(dispatched_at).__name__}."
            )

        if batch.status != BatchStatus.CREATED:
            raise ValueError(
                f"Cannot dispatch batch '{batch.batch_id}' with status '{batch.status.value}'."
            )
        if not batch.orders:
            raise ValueError(
                f"Cannot dispatch empty batch '{batch.batch_id}'."
            )
        if batch.rider_id != rider.rider_id:
            raise ValueError(
                f"Batch rider ID '{batch.rider_id}' does not match provided rider '{rider.rider_id}'."
            )

        tracked_rider = self._assigned_riders.get(batch.batch_id)
        if tracked_rider is not None and rider is not tracked_rider:
            raise ValueError(
                f"Provided rider object does not match assigned rider object for batch '{batch.batch_id}'."
            )

        batch_store_id = self._batch_stores.get(batch.batch_id)
        if batch_store_id is not None and batch_store_id != store.store_id:
            raise ValueError(
                f"Batch store '{batch_store_id}' does not match provided store '{store.store_id}'."
            )

        for o in batch.orders:
            if o.allocated_store_id != store.store_id:
                raise ValueError(
                    f"Order '{o.order_id}' store '{o.allocated_store_id}' does not match dispatch store '{store.store_id}'."
                )

        if rider.status != RiderStatus.AVAILABLE:
            raise ValueError(
                f"Rider '{rider.rider_id}' is not available (status: {rider.status.value})."
            )

        _validate_timezone_parity(
            dispatched_at,
            batch.created_at,
            "dispatched_at",
            "batch.created_at",
        )
        if dispatched_at < batch.created_at:
            raise ValueError(
                "dispatched_at cannot be earlier than created_at."
            )

        # Dispatch timer check for partial batches
        if len(batch.orders) < MAX_BATCH_SIZE:
            first_ready = self._batch_first_ready.get(batch.batch_id)
            if first_ready is None:
                raise ValueError(
                    f"Cannot dispatch partial batch '{batch.batch_id}' without a recorded first-ready timestamp."
                )
            _validate_timezone_parity(
                dispatched_at, first_ready, "dispatched_at", "first_ready"
            )
            min_dispatch = first_ready + timedelta(
                minutes=DISPATCH_WAIT_MINUTES
            )
            if dispatched_at < min_dispatch:
                raise ValueError(
                    f"Cannot dispatch batch before 3-minute waiting timer expires (first ready at {first_ready}, minimum dispatch at {min_dispatch})."
                )

        # Validate order lifecycle transitions upfront before any mutation
        for o in batch.orders:
            self._lifecycle_service.validate_transition(
                o, OrderState.DISPATCHED
            )

        # Preconditions passed; apply atomic mutations
        batch.dispatched_at = dispatched_at
        batch.status = BatchStatus.DISPATCHED

        for o in batch.orders:
            self._lifecycle_service.transition(o, OrderState.DISPATCHED)

        rider.status = RiderStatus.BUSY
        if batch.batch_id not in self._assigned_riders:
            self._assigned_riders[batch.batch_id] = rider

        return BatchDispatchResult(
            batch_id=batch.batch_id,
            is_dispatched=True,
            dispatched_at=dispatched_at,
            rider_id=rider.rider_id,
            order_ids=[o.order_id for o in batch.orders],
            reason="Batch dispatched successfully.",
        )

    def complete_delivery(
        self,
        batch: Batch,
        rider: Rider,
        delivered_at: datetime,
    ) -> DeliveryCompletionResult:
        """Complete batch delivery, transitioning orders to DELIVERED and releasing rider."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )
        if isinstance(rider, bool) or not isinstance(rider, Rider):
            raise TypeError(
                f"rider must be a Rider instance, got {type(rider).__name__}."
            )
        if isinstance(delivered_at, bool) or not isinstance(
            delivered_at, datetime
        ):
            raise TypeError(
                f"delivered_at must be a datetime, got {type(delivered_at).__name__}."
            )

        if batch.status != BatchStatus.DISPATCHED:
            raise ValueError(
                f"Cannot complete delivery for batch '{batch.batch_id}' with status '{batch.status.value}'."
            )
        if batch.rider_id != rider.rider_id:
            raise ValueError(
                f"Batch rider ID '{batch.rider_id}' does not match provided rider '{rider.rider_id}'."
            )

        tracked_rider = self._assigned_riders.get(batch.batch_id)
        if tracked_rider is not None and rider is not tracked_rider:
            raise ValueError(
                f"Provided rider object does not match assigned rider object for batch '{batch.batch_id}'."
            )

        if batch.dispatched_at is not None:
            _validate_timezone_parity(
                delivered_at,
                batch.dispatched_at,
                "delivered_at",
                "batch.dispatched_at",
            )
            if delivered_at < batch.dispatched_at:
                raise ValueError(
                    "delivered_at cannot be earlier than dispatched_at."
                )

        # Validate all order transitions before mutation
        for o in batch.orders:
            self._lifecycle_service.validate_transition(
                o, OrderState.DELIVERED
            )

        # Preconditions passed; apply atomic mutations
        for o in batch.orders:
            self._lifecycle_service.transition(o, OrderState.DELIVERED)
            o.actual_delivery_time = delivered_at
            self._order_active_batches.pop(o.order_id, None)

        batch.status = BatchStatus.DELIVERED
        rider.status = RiderStatus.AVAILABLE
        self._rider_active_batches.pop(rider.rider_id, None)
        self._assigned_riders.pop(batch.batch_id, None)

        return DeliveryCompletionResult(
            batch_id=batch.batch_id,
            rider_id=rider.rider_id,
            delivered_at=delivered_at,
            delivered_order_ids=[o.order_id for o in batch.orders],
        )

    def cancel_batch(
        self,
        batch: Batch,
        rider: Rider | None = None,
        reason: str = "",
    ) -> None:
        """Cancel a batch, releasing assigned rider and restoring order ETAs."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )

        if batch.status in (BatchStatus.DELIVERED, BatchStatus.CANCELLED):
            raise ValueError(
                f"Cannot cancel batch '{batch.batch_id}' with terminal status '{batch.status.value}'."
            )

        # Validate and resolve the rider to release
        target_rider: Rider | None = None
        if rider is not None:
            if isinstance(rider, bool) or not isinstance(rider, Rider):
                raise TypeError(
                    f"rider must be a Rider instance, got {type(rider).__name__}."
                )
            if rider.rider_id != batch.rider_id:
                raise ValueError(
                    f"Rider '{rider.rider_id}' does not match batch assigned rider '{batch.rider_id}'."
                )
            tracked_rider = self._assigned_riders.get(batch.batch_id)
            if tracked_rider is not None and rider is not tracked_rider:
                raise ValueError(
                    f"Provided rider object does not match assigned rider object for batch '{batch.batch_id}'."
                )
            target_rider = rider
        else:
            # Rider not passed: look up tracked assigned rider
            tracked_rider = self._assigned_riders.get(batch.batch_id)
            if tracked_rider is not None:
                target_rider = tracked_rider
            elif batch.status == BatchStatus.DISPATCHED:
                raise ValueError(
                    f"Assigned rider is required to cancel DISPATCHED batch '{batch.batch_id}' to ensure rider status is reset."
                )

        # Validation complete. Mutate state atomically:
        batch.status = BatchStatus.CANCELLED
        self.restore_original_etas(batch.orders)

        for o in batch.orders:
            self._order_active_batches.pop(o.order_id, None)

        assigned_rider_id = batch.rider_id
        self._rider_active_batches.pop(assigned_rider_id, None)
        self._assigned_riders.pop(batch.batch_id, None)

        if target_rider is not None:
            target_rider.status = RiderStatus.AVAILABLE
