from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.models.store import Store

# BR-11 Weather Hold Constants
HOLD_DURATION_MINUTES: int = 20
ETA_EXTENSION_MINUTES: int = 10

ELIGIBLE_ORDER_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.ALLOCATED,
        OrderState.PICKING,
        OrderState.READY_FOR_DISPATCH,
    }
)


def _is_aware(dt: datetime) -> bool:
    """Return True if datetime is timezone-aware, False if naive."""
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


@dataclass(frozen=True)
class WeatherHoldResult:
    """Outcome of activating or retrieving a weather hold under BR-11."""

    event_id: str
    store_id: str
    started_at: datetime
    hold_until: datetime
    is_active: bool
    extended_order_ids: list[str]
    skipped_order_ids: list[str]
    orders_extended_count: int
    reason: str


@runtime_checkable
class WeatherHoldServiceInterface(Protocol):
    """Protocol defining the weather hold service interface under BR-11."""

    def apply_weather_hold(
        self,
        store: Store,
        started_at: datetime,
        orders: Sequence[Order] | None = None,
        event_id: str | None = None,
    ) -> WeatherHoldResult:
        """Apply a 20-minute weather hold to a store and extend eligible orders by 10 minutes."""
        ...

    def is_hold_active(self, store: Store, current_time: datetime) -> bool:
        """Check whether a weather hold is active on a store at current_time."""
        ...

    def get_hold_expiry(self, store: Store) -> datetime | None:
        """Get the expiration timestamp of an active weather hold for a store, or None."""
        ...

    def get_hold_event(self, event_id: str) -> WeatherHoldResult | None:
        """Retrieve recorded weather hold event by event ID."""
        ...


class WeatherHoldService:
    """Implements BR-11 (Weather Hold)."""

    def __init__(self) -> None:
        self._processed_events: dict[str, WeatherHoldResult] = {}

    def apply_weather_hold(
        self,
        store: Store,
        started_at: datetime,
        orders: Sequence[Order] | None = None,
        event_id: str | None = None,
    ) -> WeatherHoldResult:
        """Apply a 20-minute weather hold to a store and extend eligible orders by 10 minutes.

        Rules:
        - Blocks new order allocations for 20 minutes from started_at.
        - If existing store.weather_hold_until expires later, preserve the later expiry.
        - Orders allocated to store in ALLOCATED, PICKING, or READY_FOR_DISPATCH receive a 10-minute ETA extension.
        - Other states (RECEIVED, REJECTED, DISPATCHED, DELIVERED, CANCELLED) are skipped.
        - Validates all datetimes and timezone parity explicitly before calculation.
        - Pre-calculates all new timestamps before mutating any domain state (complete atomicity).
        - Repeated calls with the same event_id verify store_id and started_at consistency and return cached result.
        """
        # 1. Validate store
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )

        # 2. Validate started_at
        if isinstance(started_at, bool) or not isinstance(started_at, datetime):
            raise TypeError(
                f"started_at must be a datetime, got {type(started_at).__name__}."
            )

        # 3. Validate existing weather_hold_until type and timezone parity if present
        if store.weather_hold_until is not None:
            if isinstance(store.weather_hold_until, bool) or not isinstance(
                store.weather_hold_until, datetime
            ):
                raise TypeError(
                    f"store.weather_hold_until must be a datetime or None, got {type(store.weather_hold_until).__name__}."
                )
            _validate_timezone_parity(
                store.weather_hold_until,
                started_at,
                "store.weather_hold_until",
                "started_at",
            )

        # 4. Resolve and validate event_id
        if event_id is not None:
            if isinstance(event_id, bool) or not isinstance(event_id, str):
                raise TypeError(
                    f"event_id must be a string or None, got {type(event_id).__name__}."
                )
            if not event_id.strip():
                raise ValueError("event_id must not be empty.")
            resolved_event_id = event_id.strip()
        else:
            resolved_event_id = f"{store.store_id}:{started_at.isoformat()}"

        # 5. Idempotency check: if event already processed, verify payload consistency
        if resolved_event_id in self._processed_events:
            existing_result = self._processed_events[resolved_event_id]
            if existing_result.store_id != store.store_id:
                raise ValueError(
                    f"Event ID '{resolved_event_id}' was already processed for store "
                    f"'{existing_result.store_id}', cannot reuse for store '{store.store_id}'."
                )
            if existing_result.started_at != started_at:
                raise ValueError(
                    f"Event ID '{resolved_event_id}' was already processed with started_at "
                    f"'{existing_result.started_at.isoformat()}', cannot reuse with started_at "
                    f"'{started_at.isoformat()}'."
                )
            return existing_result

        # 6. Validate orders sequence and deduplicate
        unique_orders: list[Order] = []
        if orders is not None:
            if isinstance(orders, (str, bytes, bytearray)):
                raise TypeError(
                    f"orders must be a sequence of Order objects, got {type(orders).__name__}."
                )
            try:
                iter(orders)
            except TypeError:
                raise TypeError(
                    f"orders must be a sequence of Order objects, got {type(orders).__name__}."
                )

            seen_order_ids: set[str] = set()
            for item in orders:
                if isinstance(item, bool) or not isinstance(item, Order):
                    raise TypeError(
                        f"All items in orders must be Order instances, got {type(item).__name__}."
                    )
                if item.order_id not in seen_order_ids:
                    seen_order_ids.add(item.order_id)
                    unique_orders.append(item)

        # 7. Pre-validate eligible orders without mutating any state (atomic guarantee)
        eligible_orders: list[Order] = []
        skipped_order_ids: list[str] = []

        for ord_obj in unique_orders:
            if (
                ord_obj.allocated_store_id == store.store_id
                and ord_obj.status in ELIGIBLE_ORDER_STATES
            ):
                if ord_obj.promised_eta is None:
                    raise ValueError(
                        f"Eligible order '{ord_obj.order_id}' has no promised_eta."
                    )
                if isinstance(ord_obj.promised_eta, bool) or not isinstance(
                    ord_obj.promised_eta, datetime
                ):
                    raise TypeError(
                        f"order '{ord_obj.order_id}' promised_eta must be a datetime, "
                        f"got {type(ord_obj.promised_eta).__name__}."
                    )
                _validate_timezone_parity(
                    ord_obj.promised_eta,
                    started_at,
                    f"order '{ord_obj.order_id}' promised_eta",
                    "started_at",
                )
                eligible_orders.append(ord_obj)
            else:
                skipped_order_ids.append(ord_obj.order_id)

        # 8. Pre-calculate all new timestamps before mutating ANY domain state
        # (guarantees complete atomicity even in cases of calculation error or overflow)
        proposed_expiry = started_at + timedelta(minutes=HOLD_DURATION_MINUTES)
        if (
            store.weather_hold_until is not None
            and store.weather_hold_until > proposed_expiry
        ):
            final_hold_until = store.weather_hold_until
            expiry_preserved = True
        else:
            final_hold_until = proposed_expiry
            expiry_preserved = False

        order_updates: list[tuple[Order, datetime]] = []
        for ord_obj in eligible_orders:
            assert ord_obj.promised_eta is not None
            new_eta = ord_obj.promised_eta + timedelta(minutes=ETA_EXTENSION_MINUTES)
            order_updates.append((ord_obj, new_eta))

        # 9. All validations and calculations succeeded. Apply mutations atomically.
        if not expiry_preserved:
            store.weather_hold_until = final_hold_until

        extended_order_ids: list[str] = []
        for ord_obj, new_eta in order_updates:
            ord_obj.promised_eta = new_eta
            extended_order_ids.append(ord_obj.order_id)

        reason_suffix = (
            " (preserved existing later expiry)"
            if expiry_preserved
            else ""
        )
        reason = (
            f"Weather hold activated until {final_hold_until.isoformat()}{reason_suffix}; "
            f"{len(extended_order_ids)} order(s) extended by {ETA_EXTENSION_MINUTES} minutes."
        )

        result = WeatherHoldResult(
            event_id=resolved_event_id,
            store_id=store.store_id,
            started_at=started_at,
            hold_until=final_hold_until,
            is_active=True,
            extended_order_ids=extended_order_ids,
            skipped_order_ids=skipped_order_ids,
            orders_extended_count=len(extended_order_ids),
            reason=reason,
        )

        self._processed_events[resolved_event_id] = result
        return result

    def is_hold_active(self, store: Store, current_time: datetime) -> bool:
        """Check if a weather hold is active on the store at current_time."""
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )
        if isinstance(current_time, bool) or not isinstance(current_time, datetime):
            raise TypeError(
                f"current_time must be a datetime, got {type(current_time).__name__}."
            )
        if store.weather_hold_until is None:
            return False
        if isinstance(store.weather_hold_until, bool) or not isinstance(
            store.weather_hold_until, datetime
        ):
            raise TypeError(
                f"store.weather_hold_until must be a datetime or None, got {type(store.weather_hold_until).__name__}."
            )

        _validate_timezone_parity(
            store.weather_hold_until,
            current_time,
            "store.weather_hold_until",
            "current_time",
        )
        return store.weather_hold_until > current_time

    def get_hold_expiry(self, store: Store) -> datetime | None:
        """Get the current weather hold expiration timestamp for a store, or None."""
        if isinstance(store, bool) or not isinstance(store, Store):
            raise TypeError(
                f"store must be a Store instance, got {type(store).__name__}."
            )
        if store.weather_hold_until is not None and (
            isinstance(store.weather_hold_until, bool)
            or not isinstance(store.weather_hold_until, datetime)
        ):
            raise TypeError(
                f"store.weather_hold_until must be a datetime or None, got {type(store.weather_hold_until).__name__}."
            )
        return store.weather_hold_until

    def get_hold_event(self, event_id: str) -> WeatherHoldResult | None:
        """Retrieve recorded WeatherHoldResult by event_id."""
        if isinstance(event_id, bool) or not isinstance(event_id, str):
            raise TypeError(
                f"event_id must be a string, got {type(event_id).__name__}."
            )
        if not event_id.strip():
            raise ValueError("event_id must not be empty.")
        return self._processed_events.get(event_id.strip())
