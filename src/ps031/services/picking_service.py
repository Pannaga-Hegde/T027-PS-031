from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Protocol, runtime_checkable

from ps031.models.coordinates import Coordinates
from ps031.models.order import Order
from ps031.models.store import Store
from ps031.utils.distance import haversine_distance
from ps031.utils.time_utils import SLOT_DURATION_MINUTES, get_slot_start

DEFAULT_PICKING_CAPACITY: int = 12
PICKING_TIME_MINUTES: int = 2
TRAVEL_TIME_PER_KM: float = 1.5
ADDITIONAL_DROP_DELAY_MINUTES: int = 2
MAX_NORMAL_DELIVERY_MINUTES: int = 10


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


def _resolve_effective_time(
    order: Order, reference_time: datetime | None, operation_name: str
) -> datetime:
    """Resolve the effective evaluation timestamp following reference_time precedence.

    Precedence:
    1. Explicitly provided reference_time.
    2. order.order_time.
    3. If neither exists, raise ValueError.
    Validates timezone-awareness parity if both timestamps are present.
    """
    if reference_time is not None:
        if isinstance(reference_time, bool) or not isinstance(reference_time, datetime):
            raise TypeError(
                f"reference_time must be a datetime, got {type(reference_time).__name__}."
            )
        if order.order_time is not None:
            if isinstance(order.order_time, bool) or not isinstance(
                order.order_time, datetime
            ):
                raise TypeError(
                    f"order.order_time must be a datetime, got {type(order.order_time).__name__}."
                )
            _validate_timezone_parity(
                reference_time, order.order_time, "reference_time", "order.order_time"
            )
        return reference_time

    if order.order_time is not None:
        if isinstance(order.order_time, bool) or not isinstance(
            order.order_time, datetime
        ):
            raise TypeError(
                f"order.order_time must be a datetime, got {type(order.order_time).__name__}."
            )
        return order.order_time

    if operation_name == "picking scheduling":
        raise ValueError(
            "An arrival timestamp is required to schedule picking (order.order_time or reference_time is required)."
        )
    raise ValueError(
        f"order.order_time or reference_time is required for {operation_name}."
    )


@dataclass(frozen=True)
class PickingScheduleResult:
    """Structured outcome of picking slot scheduling under BR-03."""

    order_id: str
    store_id: str
    initial_slot_start: datetime
    assigned_slot_start: datetime
    overflow_count: int
    slot_delay_minutes: int


@dataclass(frozen=True)
class ETAResult:
    """Detailed breakdown and promised ETA outcome under BR-04."""

    order_id: str
    picking_minutes: int
    travel_minutes: int
    earlier_drops_minutes: int
    slot_delay_minutes: int
    total_duration_minutes: int
    promised_eta: datetime
    is_delayed_promise: bool


@runtime_checkable
class PickingServiceInterface(Protocol):
    """Protocol defining the dark store picking scheduling and ETA service interface."""

    def get_slot_load(self, store_id: str, timestamp: datetime) -> int:
        """Return the current number of orders scheduled in the slot containing timestamp."""
        ...

    def schedule_picking(
        self,
        store: Store,
        order: Order,
        reference_time: datetime | None = None,
    ) -> PickingScheduleResult:
        """Schedule an order into the earliest available picking slot for the store."""
        ...

    def calculate_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int = 0,
        slot_delay_minutes: int = 0,
        reference_time: datetime | None = None,
        assigned_slot_start: datetime | None = None,
    ) -> ETAResult:
        """Compute the estimated delivery time for an order under BR-04."""
        ...

    def process_order_picking_and_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int = 0,
        reference_time: datetime | None = None,
    ) -> tuple[PickingScheduleResult, ETAResult]:
        """Schedule picking and calculate ETA in a single coordinated operation."""
        ...

    def recalculate_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int,
        reference_time: datetime | None = None,
    ) -> ETAResult:
        """Recalculate ETA preserving any prior picking slot delay (e.g. during batching)."""
        ...


class PickingService:
    """Implements BR-03 (Picking Capacity) and BR-04 (ETA Calculation)."""

    def __init__(self) -> None:
        # Maps (store_id, slot_start) -> list of order_ids
        self._slot_orders: dict[tuple[str, datetime], list[str]] = defaultdict(list)
        # Maps (store_id, order_id) -> PickingScheduleResult (for store-level lookup)
        self._order_assignments: dict[tuple[str, str], PickingScheduleResult] = {}
        # Maps order_id -> PickingScheduleResult (for global order uniqueness)
        self._order_schedules: dict[str, PickingScheduleResult] = {}

    def get_slot_load(self, store_id: str, timestamp: datetime) -> int:
        """Return the current number of orders scheduled in the slot containing timestamp."""
        if isinstance(store_id, bool) or not isinstance(store_id, str):
            raise TypeError(f"store_id must be a string, got {type(store_id).__name__}.")
        if not store_id.strip():
            raise ValueError("store_id must not be empty.")
        if isinstance(timestamp, bool) or not isinstance(timestamp, datetime):
            raise TypeError(
                f"timestamp must be a datetime, got {type(timestamp).__name__}."
            )

        slot_start = get_slot_start(timestamp)
        return len(self._slot_orders.get((store_id, slot_start), []))

    def schedule_picking(
        self,
        store: Store,
        order: Order,
        reference_time: datetime | None = None,
    ) -> PickingScheduleResult:
        """Schedule an order into the earliest available picking slot for the store.

        Under BR-03:
        - Capacity is store.picking_capacity (default 12 orders per 10-minute slot).
        - Slots are clock-aligned [slot_start, slot_end).
        - When a slot is full, the order rolls forward by 10 minutes until capacity is found.
        - Cascading overflow is supported.
        - An order has a single active picking assignment globally across all stores.
        - Scheduling the same order for the same store is idempotent.
        - Scheduling the same order for a different store raises a ValueError.
        - Slot overflow past store closing is permitted for already accepted orders (DEC-10).
        """
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(f"order must be a Order instance, got {type(order).__name__}.")

        arrival_time = _resolve_effective_time(
            order, reference_time, "picking scheduling"
        )

        # Global order uniqueness check:
        # If order is already scheduled, it must be for the same store (idempotent).
        # Scheduling at a different store is rejected.
        if order.order_id in self._order_schedules:
            existing = self._order_schedules[order.order_id]
            if existing.store_id == store.store_id:
                return existing
            raise ValueError(
                f"Order '{order.order_id}' is already scheduled at store '{existing.store_id}' "
                f"and cannot be scheduled at store '{store.store_id}'."
            )

        capacity = store.picking_capacity
        initial_slot_start = get_slot_start(arrival_time)
        current_slot = initial_slot_start
        overflow_count = 0

        while len(self._slot_orders.get((store.store_id, current_slot), [])) >= capacity:
            current_slot = current_slot + timedelta(minutes=SLOT_DURATION_MINUTES)
            overflow_count += 1

        assigned_slot_start = current_slot
        slot_delay_minutes = overflow_count * SLOT_DURATION_MINUTES

        self._slot_orders[(store.store_id, assigned_slot_start)].append(order.order_id)
        result = PickingScheduleResult(
            order_id=order.order_id,
            store_id=store.store_id,
            initial_slot_start=initial_slot_start,
            assigned_slot_start=assigned_slot_start,
            overflow_count=overflow_count,
            slot_delay_minutes=slot_delay_minutes,
        )
        self._order_assignments[(store.store_id, order.order_id)] = result
        self._order_schedules[order.order_id] = result
        return result

    def calculate_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int = 0,
        slot_delay_minutes: int = 0,
        reference_time: datetime | None = None,
        assigned_slot_start: datetime | None = None,
    ) -> ETAResult:
        """Compute the estimated delivery time for an order under BR-04.

        Formula:
        picking_start = max(base_time, assigned_slot_start) if assigned_slot_start is provided
                        else base_time + timedelta(minutes=slot_delay_minutes)
        picking_end = picking_start + timedelta(minutes=PICKING_TIME_MINUTES)
        promised_eta = picking_end + timedelta(minutes=travel_minutes + earlier_drops_minutes)
        total_duration_minutes = ceil((promised_eta - base_time) / 60)
        delayed_promise = (total_duration_minutes > 10) and not order.slow_delivery
        """
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(f"order must be a Order instance, got {type(order).__name__}.")

        if isinstance(earlier_drops, bool) or not isinstance(earlier_drops, int):
            raise TypeError(
                f"earlier_drops must be an integer, got {type(earlier_drops).__name__}."
            )
        if earlier_drops < 0:
            raise ValueError(
                f"earlier_drops cannot be negative, got {earlier_drops}."
            )

        if isinstance(slot_delay_minutes, bool) or not isinstance(slot_delay_minutes, int):
            raise TypeError(
                f"slot_delay_minutes must be an integer, got {type(slot_delay_minutes).__name__}."
            )
        if slot_delay_minutes < 0:
            raise ValueError(
                f"slot_delay_minutes cannot be negative, got {slot_delay_minutes}."
            )

        if assigned_slot_start is not None:
            if isinstance(assigned_slot_start, bool) or not isinstance(
                assigned_slot_start, datetime
            ):
                raise TypeError(
                    f"assigned_slot_start must be a datetime, got {type(assigned_slot_start).__name__}."
                )

        if order.delivery_location is None:
            raise ValueError("order.delivery_location is required to calculate ETA.")
        if isinstance(order.delivery_location, bool) or not isinstance(
            order.delivery_location, Coordinates
        ):
            raise TypeError(
                f"order.delivery_location must be a Coordinates instance, got {type(order.delivery_location).__name__}."
            )

        base_time = _resolve_effective_time(
            order, reference_time, "ETA calculation"
        )

        if assigned_slot_start is not None:
            _validate_timezone_parity(
                base_time, assigned_slot_start, "base_time", "assigned_slot_start"
            )

        distance_km = haversine_distance(store.location, order.delivery_location)
        travel_minutes = math.ceil(TRAVEL_TIME_PER_KM * distance_km)
        picking_minutes = PICKING_TIME_MINUTES
        earlier_drops_minutes = ADDITIONAL_DROP_DELAY_MINUTES * earlier_drops

        if assigned_slot_start is not None:
            picking_start = max(base_time, assigned_slot_start)
            if slot_delay_minutes == 0:
                initial_slot = get_slot_start(base_time)
                if assigned_slot_start > initial_slot:
                    slot_delay_minutes = max(
                        0,
                        int((assigned_slot_start - initial_slot).total_seconds() // 60),
                    )
        else:
            picking_start = base_time + timedelta(minutes=slot_delay_minutes)

        picking_end = picking_start + timedelta(minutes=picking_minutes)
        promised_eta = picking_end + timedelta(
            minutes=travel_minutes + earlier_drops_minutes
        )

        total_duration_minutes = int(
            math.ceil((promised_eta - base_time).total_seconds() / 60.0)
        )
        is_delayed = (
            total_duration_minutes > MAX_NORMAL_DELIVERY_MINUTES
        ) and not order.slow_delivery

        order.promised_eta = promised_eta
        order.delayed_promise = is_delayed

        return ETAResult(
            order_id=order.order_id,
            picking_minutes=picking_minutes,
            travel_minutes=travel_minutes,
            earlier_drops_minutes=earlier_drops_minutes,
            slot_delay_minutes=slot_delay_minutes,
            total_duration_minutes=total_duration_minutes,
            promised_eta=promised_eta,
            is_delayed_promise=is_delayed,
        )

    def _rollback_scheduling(self, schedule_res: PickingScheduleResult) -> None:
        """Roll back a newly created picking schedule assignment and slot occupancy."""
        slot_key = (schedule_res.store_id, schedule_res.assigned_slot_start)
        if slot_key in self._slot_orders:
            orders = self._slot_orders[slot_key]
            if schedule_res.order_id in orders:
                orders.remove(schedule_res.order_id)
            if not orders:
                del self._slot_orders[slot_key]
        self._order_assignments.pop((schedule_res.store_id, schedule_res.order_id), None)
        self._order_schedules.pop(schedule_res.order_id, None)

    def release_order_schedule(self, order_id: str) -> None:
        """Release picking slot reservation and scheduled assignment for an order."""
        schedule_res = self._order_schedules.get(order_id)
        if schedule_res is not None:
            self._rollback_scheduling(schedule_res)

    def process_order_picking_and_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int = 0,
        reference_time: datetime | None = None,
    ) -> tuple[PickingScheduleResult, ETAResult]:
        """Schedule picking and calculate ETA in a single coordinated operation.

        Validates ETA inputs upfront and rolls back newly created scheduling state
        if ETA calculation fails unexpectedly.
        """
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(f"order must be a Order instance, got {type(order).__name__}.")

        # Validate ETA-required inputs upfront before mutating slot occupancy
        if order.delivery_location is None:
            raise ValueError("order.delivery_location is required to calculate ETA.")
        if isinstance(order.delivery_location, bool) or not isinstance(
            order.delivery_location, Coordinates
        ):
            raise TypeError(
                f"order.delivery_location must be a Coordinates instance, got {type(order.delivery_location).__name__}."
            )

        if isinstance(earlier_drops, bool) or not isinstance(earlier_drops, int):
            raise TypeError(
                f"earlier_drops must be an integer, got {type(earlier_drops).__name__}."
            )
        if earlier_drops < 0:
            raise ValueError(f"earlier_drops cannot be negative, got {earlier_drops}.")

        # Validate timestamp upfront
        _resolve_effective_time(order, reference_time, "process_order_picking_and_eta")

        was_already_scheduled = order.order_id in self._order_schedules
        schedule_res = self.schedule_picking(store, order, reference_time=reference_time)

        try:
            eta_res = self.calculate_eta(
                store=store,
                order=order,
                earlier_drops=earlier_drops,
                slot_delay_minutes=schedule_res.slot_delay_minutes,
                reference_time=reference_time,
                assigned_slot_start=schedule_res.assigned_slot_start,
            )
        except Exception:
            if not was_already_scheduled:
                self._rollback_scheduling(schedule_res)
            raise

        return schedule_res, eta_res

    def recalculate_eta(
        self,
        store: Store,
        order: Order,
        earlier_drops: int,
        reference_time: datetime | None = None,
    ) -> ETAResult:
        """Recalculate ETA preserving any prior picking slot delay (e.g. during batching)."""
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(f"store must be a Store instance, got {type(store).__name__}.")
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(f"order must be a Order instance, got {type(order).__name__}.")

        assignment = self._order_schedules.get(order.order_id)
        if assignment is not None:
            if assignment.store_id != store.store_id:
                raise ValueError(
                    f"Order '{order.order_id}' is scheduled at store '{assignment.store_id}', "
                    f"cannot recalculate ETA for store '{store.store_id}'."
                )
            slot_delay_minutes = assignment.slot_delay_minutes
            assigned_slot_start = assignment.assigned_slot_start
        else:
            slot_delay_minutes = 0
            assigned_slot_start = None

        return self.calculate_eta(
            store=store,
            order=order,
            earlier_drops=earlier_drops,
            slot_delay_minutes=slot_delay_minutes,
            reference_time=reference_time,
            assigned_slot_start=assigned_slot_start,
        )
