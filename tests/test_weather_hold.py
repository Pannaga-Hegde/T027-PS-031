from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem
from ps031.models.order_state import OrderState
from ps031.models.store import Store
from ps031.services.allocation_service import AllocationService
from ps031.services.compensation_service import CompensationService
from ps031.services.weather_hold_service import (
    ETA_EXTENSION_MINUTES,
    HOLD_DURATION_MINUTES,
    WeatherHoldResult,
    WeatherHoldService,
    WeatherHoldServiceInterface,
)

_DEFAULT_ETA = object()


def make_store(
    store_id: str = "STORE-01",
    name: str = "Indiranagar Dark Store",
    weather_hold_until: datetime | None = None,
    lat: float = 12.9716,
    lon: float = 77.5946,
) -> Store:
    """Helper to construct a Store instance for weather hold testing."""
    return Store(
        store_id=store_id,
        name=name,
        location=Coordinates(latitude=lat, longitude=lon),
        operating_hours=OperatingHours(open_time=time(6, 0), close_time=time(23, 0)),
        inventory={"SKU-MILK": 100, "SKU-BREAD": 100},
        weather_hold_until=weather_hold_until,
    )


def make_order(
    order_id: str = "ORD-01",
    customer_id: str = "CUST-01",
    allocated_store_id: str | None = "STORE-01",
    status: OrderState = OrderState.ALLOCATED,
    order_time: datetime | None = None,
    promised_eta: Any = _DEFAULT_ETA,
    delayed_promise: bool = False,
    slow_delivery: bool = False,
    delivery_lat: float = 12.9720,
    delivery_lon: float = 77.5950,
) -> Order:
    """Helper to construct an Order instance for weather hold testing."""
    t0 = (
        order_time
        if order_time is not None
        else datetime(2026, 11, 5, 10, 0)
    )
    eta = (
        t0 + timedelta(minutes=15)
        if promised_eta is _DEFAULT_ETA
        else promised_eta
    )
    return Order(
        order_id=order_id,
        customer_id=customer_id,
        order_time=t0,
        promised_eta=eta,
        status=status,
        allocated_store_id=allocated_store_id,
        delayed_promise=delayed_promise,
        slow_delivery=slow_delivery,
        delivery_location=Coordinates(latitude=delivery_lat, longitude=delivery_lon),
        items=[
            OrderItem(
                product_id="SKU-MILK",
                quantity=1,
                unit_price=Decimal("50.00"),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Protocol and Conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Tests verifying WeatherHoldServiceInterface conformance."""

    def test_service_satisfies_protocol(self) -> None:
        service = WeatherHoldService()
        assert isinstance(service, WeatherHoldServiceInterface)


# ---------------------------------------------------------------------------
# Core BR-11 Business Requirements (17 Specified Tests)
# ---------------------------------------------------------------------------


class TestWeatherHoldCoreRequirements:
    """Verification of BR-11 requirements and edge cases."""

    def test_hold_activates_for_exactly_twenty_minutes(self) -> None:
        """Requirement 1: Weather hold blocks allocations for exactly 20 minutes."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)

        result = service.apply_weather_hold(store, started_at)

        expected_expiry = started_at + timedelta(minutes=20)
        assert store.weather_hold_until == expected_expiry
        assert result.hold_until == expected_expiry
        assert result.is_active is True
        assert result.store_id == "STORE-01"
        assert result.orders_extended_count == 0
        assert service.is_hold_active(store, started_at + timedelta(minutes=10)) is True

    def test_new_allocation_is_blocked_during_hold(self) -> None:
        """Requirement 2: New order allocations are blocked while hold is active."""
        weather_service = WeatherHoldService()
        allocation_service = AllocationService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)

        weather_service.apply_weather_hold(store, started_at)

        during_hold_time = started_at + timedelta(minutes=10)
        new_order = make_order(
            order_id="ORD-NEW",
            allocated_store_id=None,
            status=OrderState.RECEIVED,
            order_time=during_hold_time,
            promised_eta=None,
        )

        assert (
            allocation_service.is_serviceable(
                store, new_order, reference_time=during_hold_time
            )
            is False
        )
        allocation_res = allocation_service.allocate_store(
            new_order, [store], reference_time=during_hold_time
        )
        assert allocation_res.is_allocated is False
        assert allocation_res.selected_store is None

    def test_allocation_allowed_exactly_when_hold_expires(self) -> None:
        """Requirement 3: Allocation allowed exactly at the 20-minute expiry boundary."""
        weather_service = WeatherHoldService()
        allocation_service = AllocationService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)

        weather_service.apply_weather_hold(store, started_at)

        expiry_time = started_at + timedelta(minutes=20)
        assert weather_service.is_hold_active(store, expiry_time) is False

        new_order = make_order(
            order_id="ORD-EXPIRED",
            allocated_store_id=None,
            status=OrderState.RECEIVED,
            order_time=expiry_time,
            promised_eta=None,
        )

        assert (
            allocation_service.is_serviceable(
                store, new_order, reference_time=expiry_time
            )
            is True
        )
        allocation_res = allocation_service.allocate_store(
            new_order, [store], reference_time=expiry_time
        )
        assert allocation_res.is_allocated is True
        assert allocation_res.selected_store == store

    def test_existing_allocated_order_receives_ten_minutes_extension(self) -> None:
        """Requirement 4: Existing ALLOCATED order receives +10 min ETA extension."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)
        order = make_order(
            order_id="ORD-ALLOC",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(store, started_at, orders=[order])

        assert order.promised_eta == original_eta + timedelta(minutes=10)
        assert result.extended_order_ids == ["ORD-ALLOC"]
        assert result.skipped_order_ids == []
        assert result.orders_extended_count == 1

    def test_existing_picking_order_receives_ten_minutes_extension(self) -> None:
        """Requirement 5: Existing PICKING order receives +10 min ETA extension."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 20)
        order = make_order(
            order_id="ORD-PICK",
            status=OrderState.PICKING,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(store, started_at, orders=[order])

        assert order.promised_eta == original_eta + timedelta(minutes=10)
        assert result.extended_order_ids == ["ORD-PICK"]
        assert result.orders_extended_count == 1

    def test_existing_ready_for_dispatch_order_receives_ten_minutes_extension(
        self,
    ) -> None:
        """Requirement 6: READY_FOR_DISPATCH order receives +10 min ETA extension."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 18)
        order = make_order(
            order_id="ORD-READY",
            status=OrderState.READY_FOR_DISPATCH,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(store, started_at, orders=[order])

        assert order.promised_eta == original_eta + timedelta(minutes=10)
        assert result.extended_order_ids == ["ORD-READY"]
        assert result.orders_extended_count == 1

    def test_dispatched_order_is_unchanged(self) -> None:
        """Requirement 7: DISPATCHED order is in transit and must not be extended."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)
        order = make_order(
            order_id="ORD-DISPATCHED",
            status=OrderState.DISPATCHED,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(store, started_at, orders=[order])

        assert order.promised_eta == original_eta
        assert result.extended_order_ids == []
        assert result.skipped_order_ids == ["ORD-DISPATCHED"]
        assert result.orders_extended_count == 0

    def test_delivered_and_cancelled_orders_are_unchanged(self) -> None:
        """Requirement 8: DELIVERED and CANCELLED orders are skipped and unchanged."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        eta1 = datetime(2026, 11, 5, 10, 15)
        eta2 = datetime(2026, 11, 5, 10, 20)
        delivered_order = make_order(
            order_id="ORD-DELIVERED",
            status=OrderState.DELIVERED,
            promised_eta=eta1,
        )
        cancelled_order = make_order(
            order_id="ORD-CANCELLED",
            status=OrderState.CANCELLED,
            promised_eta=eta2,
        )

        result = service.apply_weather_hold(
            store, started_at, orders=[delivered_order, cancelled_order]
        )

        assert delivered_order.promised_eta == eta1
        assert cancelled_order.promised_eta == eta2
        assert result.extended_order_ids == []
        assert set(result.skipped_order_ids) == {
            "ORD-DELIVERED",
            "ORD-CANCELLED",
        }
        assert result.orders_extended_count == 0

    def test_orders_allocated_to_another_store_are_unchanged(self) -> None:
        """Requirement 9: Orders allocated to a different store are not extended."""
        service = WeatherHoldService()
        store = make_store(store_id="STORE-01")
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)
        other_store_order = make_order(
            order_id="ORD-STORE-02",
            allocated_store_id="STORE-02",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(
            store, started_at, orders=[other_store_order]
        )

        assert other_store_order.promised_eta == original_eta
        assert result.extended_order_ids == []
        assert result.skipped_order_ids == ["ORD-STORE-02"]
        assert result.orders_extended_count == 0

    def test_eligible_order_without_promised_eta_raises_value_error_and_aborts(
        self,
    ) -> None:
        """Requirement 10: Missing promised_eta on eligible order raises ValueError atomically."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        valid_order = make_order(
            order_id="ORD-VALID",
            status=OrderState.ALLOCATED,
            promised_eta=datetime(2026, 11, 5, 10, 15),
        )
        invalid_order = make_order(
            order_id="ORD-INVALID",
            status=OrderState.ALLOCATED,
            promised_eta=None,
        )

        with pytest.raises(ValueError, match="has no promised_eta"):
            service.apply_weather_hold(
                store, started_at, orders=[valid_order, invalid_order]
            )

        # Atomic guarantee: Neither store nor valid order is mutated
        assert store.weather_hold_until is None
        assert valid_order.promised_eta == datetime(2026, 11, 5, 10, 15)

    def test_same_event_processed_twice_does_not_double_extend(self) -> None:
        """Requirement 11: Idempotency guarantees orders are not double-extended."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)
        order = make_order(
            order_id="ORD-IDEMPOTENT",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta,
        )

        result1 = service.apply_weather_hold(
            store, started_at, orders=[order], event_id="WEATHER-EV-01"
        )
        expected_eta = original_eta + timedelta(minutes=10)
        assert order.promised_eta == expected_eta
        assert result1.orders_extended_count == 1

        # Second invocation with same event_id
        result2 = service.apply_weather_hold(
            store, started_at, orders=[order], event_id="WEATHER-EV-01"
        )
        assert order.promised_eta == expected_eta  # Not extended to +20 min!
        assert result2 is result1

    def test_different_hold_events_are_handled_independently(self) -> None:
        """Requirement 12: Distinct hold events process independently."""
        service = WeatherHoldService()
        store = make_store()
        t1 = datetime(2026, 11, 5, 10, 0)
        order = make_order(
            order_id="ORD-INDEP",
            status=OrderState.ALLOCATED,
            promised_eta=datetime(2026, 11, 5, 10, 15),
        )

        res1 = service.apply_weather_hold(
            store, t1, orders=[order], event_id="WEATHER-EV-1"
        )
        assert order.promised_eta == datetime(2026, 11, 5, 10, 25)
        assert res1.event_id == "WEATHER-EV-1"

        t2 = datetime(2026, 11, 5, 10, 30)
        res2 = service.apply_weather_hold(
            store, t2, orders=[order], event_id="WEATHER-EV-2"
        )
        assert order.promised_eta == datetime(2026, 11, 5, 10, 35)
        assert res2.event_id == "WEATHER-EV-2"
        assert store.weather_hold_until == datetime(2026, 11, 5, 10, 50)

    def test_overlapping_holds_do_not_shorten_existing_hold(self) -> None:
        """Requirement 13: A new hold expiring earlier must not shorten existing hold."""
        service = WeatherHoldService()
        existing_expiry = datetime(2026, 11, 5, 10, 45)
        store = make_store(weather_hold_until=existing_expiry)
        started_at = datetime(2026, 11, 5, 10, 10)
        # proposed expiry = 10:10 + 20 min = 10:30, which is earlier than 10:45

        order = make_order(
            order_id="ORD-OVERLAP",
            status=OrderState.ALLOCATED,
            promised_eta=datetime(2026, 11, 5, 10, 20),
        )

        result = service.apply_weather_hold(
            store, started_at, orders=[order], event_id="EV-OVERLAP"
        )

        assert store.weather_hold_until == existing_expiry
        assert result.hold_until == existing_expiry
        assert order.promised_eta == datetime(2026, 11, 5, 10, 30)

    def test_timezone_aware_and_naive_mismatches_raise_type_error(self) -> None:
        """Requirement 14: Timezone awareness mismatches raise TypeError atomically."""
        service = WeatherHoldService()
        utc = timezone.utc
        naive_store = make_store()
        aware_started = datetime(2026, 11, 5, 10, 0, tzinfo=utc)

        # Store with naive hold vs aware started_at
        naive_store.weather_hold_until = datetime(2026, 11, 5, 10, 30)
        with pytest.raises(TypeError, match="Cannot compare"):
            service.apply_weather_hold(naive_store, aware_started)

        # Aware store vs naive order promised_eta
        aware_store = make_store(
            weather_hold_until=datetime(2026, 11, 5, 10, 30, tzinfo=utc)
        )
        naive_order = make_order(
            order_id="ORD-NAIVE",
            status=OrderState.ALLOCATED,
            promised_eta=datetime(2026, 11, 5, 10, 15),  # naive
        )
        with pytest.raises(TypeError, match="Cannot compare"):
            service.apply_weather_hold(
                aware_store, aware_started, orders=[naive_order]
            )
        # No mutations applied
        assert naive_order.promised_eta == datetime(2026, 11, 5, 10, 15)

    def test_invalid_order_does_not_cause_partial_mutation(self) -> None:
        """Requirement 15: Atomicity guarantee across multiple orders."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        eta1 = datetime(2026, 11, 5, 10, 15)

        order1 = make_order(
            order_id="ORD-1",
            status=OrderState.ALLOCATED,
            promised_eta=eta1,
        )
        order2 = make_order(
            order_id="ORD-2",
            status=OrderState.ALLOCATED,
            promised_eta=None,  # Invalid!
        )

        with pytest.raises(ValueError):
            service.apply_weather_hold(
                store, started_at, orders=[order1, order2]
            )

        assert store.weather_hold_until is None
        assert order1.promised_eta == eta1

    def test_datetime_overflow_on_later_order_leaves_store_and_earlier_order_unmutated(
        self,
    ) -> None:
        """Requirement 1: Complete atomicity regression test with datetime.max."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta1 = datetime(2026, 11, 5, 10, 15)

        order1 = make_order(
            order_id="ORD-VALID-EARLY",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta1,
        )
        order2 = make_order(
            order_id="ORD-OVERFLOW-LATE",
            status=OrderState.ALLOCATED,
            promised_eta=datetime.max,
        )

        with pytest.raises(OverflowError):
            service.apply_weather_hold(
                store, started_at, orders=[order1, order2]
            )

        # Complete atomicity: Neither store nor earlier valid order was mutated
        assert store.weather_hold_until is None
        assert order1.promised_eta == original_eta1

    def test_invalid_store_weather_hold_until_type_raises_type_error(self) -> None:
        """Requirement 3: Explicit validation of store.weather_hold_until type."""
        service = WeatherHoldService()
        store = make_store()
        store.weather_hold_until = "invalid-datetime-string"  # type: ignore
        t = datetime(2026, 11, 5, 10, 0)

        with pytest.raises(
            TypeError, match="store.weather_hold_until must be a datetime or None"
        ):
            service.apply_weather_hold(store, t)

        with pytest.raises(
            TypeError, match="store.weather_hold_until must be a datetime or None"
        ):
            service.is_hold_active(store, t)

        with pytest.raises(
            TypeError, match="store.weather_hold_until must be a datetime or None"
        ):
            service.get_hold_expiry(store)

    def test_eligible_order_with_non_datetime_promised_eta_raises_type_error(
        self,
    ) -> None:
        """Requirement 3: Explicit validation of eligible order.promised_eta before timezone check."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta1 = datetime(2026, 11, 5, 10, 15)

        order1 = make_order(
            order_id="ORD-1",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta1,
        )
        order2 = make_order(
            order_id="ORD-2",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta1,
        )
        order2.promised_eta = "2026-11-05T10:15:00"  # type: ignore (mutated post-construction)

        with pytest.raises(
            TypeError, match="promised_eta must be a datetime"
        ):
            service.apply_weather_hold(store, started_at, orders=[order1, order2])

        # Atomicity preserved: earlier valid order and store are unchanged
        assert store.weather_hold_until is None
        assert order1.promised_eta == original_eta1

    def test_existing_delayed_promise_and_slow_delivery_flags_preserved(
        self,
    ) -> None:
        """Requirement 16: Weather hold preserves existing delayed_promise/slow_delivery flags."""
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        order = make_order(
            order_id="ORD-FLAGS",
            status=OrderState.ALLOCATED,
            promised_eta=datetime(2026, 11, 5, 10, 20),
            delayed_promise=True,
            slow_delivery=True,
        )

        result = service.apply_weather_hold(store, started_at, orders=[order])

        assert order.delayed_promise is True
        assert order.slow_delivery is True
        assert order.promised_eta == datetime(2026, 11, 5, 10, 30)
        assert result.orders_extended_count == 1

    def test_delivery_at_extended_eta_does_not_receive_sla_compensation(
        self,
    ) -> None:
        """Requirement 17: Delivery at extended ETA evaluates lateness against new ETA (no SLA penalty)."""
        weather_service = WeatherHoldService()
        comp_service = CompensationService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)

        order = make_order(
            order_id="ORD-SLA",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta,
        )

        weather_service.apply_weather_hold(store, started_at, orders=[order])

        extended_eta = datetime(2026, 11, 5, 10, 25)
        assert order.promised_eta == extended_eta

        # Delivered on-time relative to the extended ETA
        order.status = OrderState.DELIVERED
        order.actual_delivery_time = extended_eta

        comp_result = comp_service.calculate_compensation(order)
        assert comp_result.is_late is False
        assert comp_result.lateness_seconds == 0.0
        assert comp_result.wallet_credit == Decimal("0.00")
        assert comp_result.free_coupon is False

        # Delivered 4 minutes late relative to extended ETA (less than 5 min threshold) -> no compensation
        comp_result_4m = comp_service.calculate_compensation(
            order, actual_delivery_time=extended_eta + timedelta(minutes=4)
        )
        assert comp_result_4m.is_late is True
        assert comp_result_4m.wallet_credit == Decimal("0.00")

        # Delivered 7 minutes late relative to extended ETA -> ₹25 tier 1 compensation
        comp_result_7m = comp_service.calculate_compensation(
            order, actual_delivery_time=extended_eta + timedelta(minutes=7)
        )
        assert comp_result_7m.is_late is True
        assert comp_result_7m.wallet_credit == Decimal("25.00")


# ---------------------------------------------------------------------------
# Additional Edge Cases, Validations, and Service Methods
# ---------------------------------------------------------------------------


class TestWeatherHoldServiceDetails:
    """Tests covering input validations, duplicate orders, and helper methods."""

    def test_deterministic_event_id_generation(self) -> None:
        service = WeatherHoldService()
        store = make_store(store_id="STORE-A")
        started_at = datetime(2026, 11, 5, 12, 0)

        result = service.apply_weather_hold(store, started_at)
        expected_id = f"STORE-A:{started_at.isoformat()}"
        assert result.event_id == expected_id
        assert service.get_hold_event(expected_id) == result

    def test_duplicate_orders_in_sequence_deduplicated(self) -> None:
        service = WeatherHoldService()
        store = make_store()
        started_at = datetime(2026, 11, 5, 10, 0)
        original_eta = datetime(2026, 11, 5, 10, 15)
        order = make_order(
            order_id="ORD-DUP",
            status=OrderState.ALLOCATED,
            promised_eta=original_eta,
        )

        result = service.apply_weather_hold(
            store, started_at, orders=[order, order]
        )
        assert result.extended_order_ids == ["ORD-DUP"]
        assert result.orders_extended_count == 1
        assert order.promised_eta == original_eta + timedelta(minutes=10)

    def test_invalid_types_raise_type_error(self) -> None:
        service = WeatherHoldService()
        store = make_store()
        t = datetime(2026, 11, 5, 10, 0)

        with pytest.raises(TypeError, match="store must be a Store instance"):
            service.apply_weather_hold("not-a-store", t)  # type: ignore

        with pytest.raises(TypeError, match="started_at must be a datetime"):
            service.apply_weather_hold(store, "not-a-datetime")  # type: ignore

        with pytest.raises(TypeError, match="event_id must be a string"):
            service.apply_weather_hold(store, t, event_id=123)  # type: ignore

        with pytest.raises(TypeError, match="orders must be a sequence"):
            service.apply_weather_hold(store, t, orders="not-orders")  # type: ignore

        with pytest.raises(TypeError, match="All items in orders must be Order instances"):
            service.apply_weather_hold(store, t, orders=["invalid"])  # type: ignore

    def test_empty_event_id_raises_value_error(self) -> None:
        service = WeatherHoldService()
        store = make_store()
        t = datetime(2026, 11, 5, 10, 0)

        with pytest.raises(ValueError, match="event_id must not be empty"):
            service.apply_weather_hold(store, t, event_id="   ")

    def test_reusing_event_id_for_different_store_raises_value_error(self) -> None:
        service = WeatherHoldService()
        store1 = make_store(store_id="STORE-1")
        store2 = make_store(store_id="STORE-2")
        t = datetime(2026, 11, 5, 10, 0)

        service.apply_weather_hold(store1, t, event_id="EV-100")

        with pytest.raises(ValueError, match="already processed for store"):
            service.apply_weather_hold(store2, t, event_id="EV-100")

    def test_same_event_id_with_different_timestamp_raises_value_error(
        self,
    ) -> None:
        """Requirement 2: Same event ID with different started_at raises ValueError."""
        service = WeatherHoldService()
        store = make_store(store_id="STORE-1")
        t1 = datetime(2026, 11, 5, 10, 0)
        t2 = datetime(2026, 11, 5, 10, 30)

        service.apply_weather_hold(store, t1, event_id="EV-PAYLOAD-CHECK")

        with pytest.raises(ValueError, match="already processed with started_at"):
            service.apply_weather_hold(store, t2, event_id="EV-PAYLOAD-CHECK")

    def test_exact_duplicate_invocation_preserves_idempotency(self) -> None:
        """Requirement 2: Exact duplicate invocation returns cached result cleanly."""
        service = WeatherHoldService()
        store = make_store(store_id="STORE-1")
        t = datetime(2026, 11, 5, 10, 0)
        order = make_order(status=OrderState.ALLOCATED)

        res1 = service.apply_weather_hold(
            store, t, orders=[order], event_id="EV-EXACT"
        )
        res2 = service.apply_weather_hold(
            store, t, orders=[order], event_id="EV-EXACT"
        )

        assert res1 is res2
        assert res2.event_id == "EV-EXACT"
        assert res2.store_id == "STORE-1"
        assert res2.started_at == t

    def test_get_hold_expiry_and_is_hold_active_helpers(self) -> None:
        service = WeatherHoldService()
        store = make_store()
        assert service.get_hold_expiry(store) is None
        assert service.is_hold_active(store, datetime(2026, 11, 5, 10, 0)) is False

        t0 = datetime(2026, 11, 5, 10, 0)
        service.apply_weather_hold(store, t0)

        assert service.get_hold_expiry(store) == t0 + timedelta(minutes=20)
        assert service.is_hold_active(store, t0 + timedelta(minutes=19)) is True
        assert service.is_hold_active(store, t0 + timedelta(minutes=20)) is False
        assert service.is_hold_active(store, t0 + timedelta(minutes=21)) is False

    def test_ineligible_order_without_promised_eta_does_not_abort(self) -> None:
        """RECEIVED orders don't have promised_eta yet, and being ineligible should not abort hold activation."""
        service = WeatherHoldService()
        store = make_store()
        t0 = datetime(2026, 11, 5, 10, 0)
        received_order = make_order(
            order_id="ORD-RECV",
            allocated_store_id=None,
            status=OrderState.RECEIVED,
            promised_eta=None,
        )

        result = service.apply_weather_hold(
            store, t0, orders=[received_order]
        )
        assert result.orders_extended_count == 0
        assert result.skipped_order_ids == ["ORD-RECV"]
        assert store.weather_hold_until == t0 + timedelta(minutes=20)
