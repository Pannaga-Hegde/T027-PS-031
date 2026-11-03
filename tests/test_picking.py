from datetime import datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
import math
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem
from ps031.models.store import Store
from ps031.services.picking_service import (
    DEFAULT_PICKING_CAPACITY,
    ETAResult,
    PickingScheduleResult,
    PickingService,
    PickingServiceInterface,
)
from ps031.utils.distance import EARTH_RADIUS_KM, haversine_distance


def _coord_north(origin: Coordinates, km: float) -> Coordinates:
    """Helper to produce coordinates approximately `km` north of origin."""
    dlat = (km * 180.0) / (EARTH_RADIUS_KM * math.pi)
    return Coordinates(latitude=origin.latitude + dlat, longitude=origin.longitude)


@pytest.fixture
def store_loc():
    return Coordinates(latitude=12.9716, longitude=77.5946)


@pytest.fixture
def store_hours():
    return OperatingHours(open_time=time(6, 0), close_time=time(23, 0))


@pytest.fixture
def make_store(store_loc, store_hours):
    def _create(store_id: str = "store_01", picking_capacity: int = 12) -> Store:
        return Store(
            store_id=store_id,
            name=f"Store {store_id}",
            location=store_loc,
            operating_hours=store_hours,
            picking_capacity=picking_capacity,
        )

    return _create


@pytest.fixture
def make_order(store_loc):
    def _create(
        order_id: str = "order_01",
        delivery_location: Coordinates | None = None,
        order_time: datetime | None = None,
        slow_delivery: bool = False,
    ) -> Order:
        loc = delivery_location if delivery_location is not None else store_loc
        ot = (
            order_time
            if order_time is not None
            else datetime(2026, 11, 3, 10, 4, 0)
        )
        return Order(
            order_id=order_id,
            customer_id="cust_01",
            items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
            delivery_location=loc,
            order_time=ot,
            slow_delivery=slow_delivery,
        )

    return _create


# ---------------------------------------------------------------------------
# Protocol Adherence
# ---------------------------------------------------------------------------


def test_picking_service_implements_protocol():
    """PickingService satisfies PickingServiceInterface protocol."""
    service = PickingService()
    assert isinstance(service, PickingServiceInterface)


# ---------------------------------------------------------------------------
# BR-03: Picking Capacity & Slot Scheduling Tests
# ---------------------------------------------------------------------------


def test_default_capacity_constant():
    """Default picking capacity constant is 12."""
    assert DEFAULT_PICKING_CAPACITY == 12


def test_initial_schedule_within_capacity(make_store, make_order):
    """An order arriving within slot capacity is assigned to its arrival slot without delay."""
    service = PickingService()
    store = make_store()
    order = make_order(order_time=datetime(2026, 11, 3, 10, 4, 30))

    result = service.schedule_picking(store, order)

    assert result.order_id == "order_01"
    assert result.store_id == store.store_id
    assert result.initial_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert result.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert result.overflow_count == 0
    assert result.slot_delay_minutes == 0
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 5, 0)) == 1


def test_capacity_reached_exactly(make_store, make_order):
    """Exactly 12 orders fill the slot with 0 overflow."""
    service = PickingService()
    store = make_store(picking_capacity=12)
    slot_time = datetime(2026, 11, 3, 10, 2, 0)

    for i in range(1, 13):
        order = make_order(order_id=f"order_{i:02d}", order_time=slot_time)
        res = service.schedule_picking(store, order)
        assert res.overflow_count == 0
        assert res.slot_delay_minutes == 0
        assert res.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)

    assert service.get_slot_load(store.store_id, slot_time) == 12


def test_single_slot_overflow(make_store, make_order):
    """13th order in a full 12-capacity slot overflows to the next 10-minute slot."""
    service = PickingService()
    store = make_store(picking_capacity=12)
    slot_time = datetime(2026, 11, 3, 10, 5, 0)

    # Fill first slot
    for i in range(1, 13):
        service.schedule_picking(
            store, make_order(order_id=f"order_{i:02d}", order_time=slot_time)
        )

    # 13th order
    order_13 = make_order(order_id="order_13", order_time=slot_time)
    res_13 = service.schedule_picking(store, order_13)

    assert res_13.initial_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert res_13.assigned_slot_start == datetime(2026, 11, 3, 10, 10, 0)
    assert res_13.overflow_count == 1
    assert res_13.slot_delay_minutes == 10

    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 12
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 10, 0)) == 1


def test_cascading_overflow(make_store, make_order):
    """When subsequent slots are also full, order cascades forward until capacity is found."""
    service = PickingService()
    store = make_store(picking_capacity=2)  # small capacity for test clarity
    slot_time = datetime(2026, 11, 3, 10, 3, 0)

    # Fill [10:00, 10:10)
    service.schedule_picking(store, make_order(order_id="o1", order_time=slot_time))
    service.schedule_picking(store, make_order(order_id="o2", order_time=slot_time))

    # Fill [10:10, 10:20)
    next_slot_time = datetime(2026, 11, 3, 10, 12, 0)
    service.schedule_picking(store, make_order(order_id="o3", order_time=next_slot_time))
    service.schedule_picking(store, make_order(order_id="o4", order_time=next_slot_time))

    # Order arriving at 10:05 should cascade through [10:00, 10:10) and [10:10, 10:20) into [10:20, 10:30)
    o5 = make_order(order_id="o5", order_time=slot_time)
    res_5 = service.schedule_picking(store, o5)

    assert res_5.initial_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert res_5.assigned_slot_start == datetime(2026, 11, 3, 10, 20, 0)
    assert res_5.overflow_count == 2
    assert res_5.slot_delay_minutes == 20
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 20, 0)) == 1


def test_configured_capacity(make_store, make_order):
    """A store with a custom configured capacity (e.g. 5) respects that capacity limit."""
    service = PickingService()
    store = make_store(picking_capacity=5)
    slot_time = datetime(2026, 11, 3, 11, 0, 0)

    for i in range(1, 6):
        res = service.schedule_picking(
            store, make_order(order_id=f"o_{i}", order_time=slot_time)
        )
        assert res.overflow_count == 0

    overflow_order = make_order(order_id="o_overflow", order_time=slot_time)
    res_over = service.schedule_picking(store, overflow_order)
    assert res_over.overflow_count == 1
    assert res_over.slot_delay_minutes == 10
    assert res_over.assigned_slot_start == datetime(2026, 11, 3, 11, 10, 0)


def test_idempotent_scheduling_same_order(make_store, make_order):
    """Scheduling the same order twice for the same store returns existing assignment and does not increase slot load."""
    service = PickingService()
    store = make_store(picking_capacity=12)
    order = make_order(order_id="idempotent_order", order_time=datetime(2026, 11, 3, 10, 5, 0))

    res1 = service.schedule_picking(store, order)
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 1

    res2 = service.schedule_picking(store, order)
    assert res2 == res1
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 1


def test_independent_schedules_for_different_stores(make_store, make_order):
    """Occupancy is tracked independently per store."""
    service = PickingService()
    store_a = make_store(store_id="store_A", picking_capacity=12)
    store_b = make_store(store_id="store_B", picking_capacity=12)
    order_time = datetime(2026, 11, 3, 10, 5, 0)

    order_a = make_order(order_id="order_a", order_time=order_time)
    service.schedule_picking(store_a, order_a)

    assert service.get_slot_load(store_a.store_id, order_time) == 1
    assert service.get_slot_load(store_b.store_id, order_time) == 0


def test_overflow_near_closing_time_permitted(make_store, make_order):
    """An accepted order near closing time can overflow into slots past closing time (DEC-10)."""
    service = PickingService()
    # Store closes at 23:00
    store = make_store(picking_capacity=1)
    near_closing_time = datetime(2026, 11, 3, 22, 55, 0)

    # First order occupies [22:50, 23:00)
    o1 = make_order(order_id="o1", order_time=near_closing_time)
    res1 = service.schedule_picking(store, o1)
    assert res1.assigned_slot_start == datetime(2026, 11, 3, 22, 50, 0)

    # Second order accepted prior to closing overflows to [23:00, 23:10)
    o2 = make_order(order_id="o2", order_time=near_closing_time)
    res2 = service.schedule_picking(store, o2)
    assert res2.overflow_count == 1
    assert res2.slot_delay_minutes == 10
    assert res2.assigned_slot_start == datetime(2026, 11, 3, 23, 0, 0)


# ---------------------------------------------------------------------------
# BR-04: ETA Calculation Tests
# ---------------------------------------------------------------------------


def test_eta_zero_distance(make_store, make_order, store_loc):
    """At 0 km distance: picking is 2m, travel is 0m, drops 0m -> total 2m; not delayed."""
    service = PickingService()
    store = make_store()
    order = make_order(
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    eta_res = service.calculate_eta(store, order)

    assert eta_res.order_id == order.order_id
    assert eta_res.picking_minutes == 2
    assert eta_res.travel_minutes == 0
    assert eta_res.earlier_drops_minutes == 0
    assert eta_res.slot_delay_minutes == 0
    assert eta_res.total_duration_minutes == 2
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 2, 0)
    assert eta_res.is_delayed_promise is False
    assert order.promised_eta == datetime(2026, 11, 3, 10, 2, 0)
    assert order.delayed_promise is False


def test_eta_fractional_travel_time_rounding(make_store, make_order, store_loc):
    """ceil(1.5 * d) rounding tests."""
    service = PickingService()
    store = make_store()

    # d = 1.0 km -> 1.5 * 1.0 = 1.5 -> ceil = 2 min
    order_1km = make_order(
        order_id="o_1km",
        delivery_location=_coord_north(store_loc, 1.0),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )
    eta_1km = service.calculate_eta(store, order_1km)
    assert eta_1km.travel_minutes == 2
    assert eta_1km.total_duration_minutes == 4  # 2 picking + 2 travel

    # d = 2.0 km -> 1.5 * 2.0 = 3.0 -> ceil = 3 min
    order_2km = make_order(
        order_id="o_2km",
        delivery_location=_coord_north(store_loc, 2.0),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )
    eta_2km = service.calculate_eta(store, order_2km)
    assert eta_2km.travel_minutes == 3
    assert eta_2km.total_duration_minutes == 5  # 2 picking + 3 travel

    # d = 1.1 km -> 1.5 * 1.1 = 1.65 -> ceil = 2 min
    order_1_1km = make_order(
        order_id="o_1_1km",
        delivery_location=_coord_north(store_loc, 1.1),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )
    eta_1_1km = service.calculate_eta(store, order_1_1km)
    assert eta_1_1km.travel_minutes == 2

    # d = 2.1 km -> 1.5 * 2.1 = 3.15 -> ceil = 4 min
    order_2_1km = make_order(
        order_id="o_2_1km",
        delivery_location=_coord_north(store_loc, 2.1),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )
    eta_2_1km = service.calculate_eta(store, order_2_1km)
    assert eta_2_1km.travel_minutes == 4


def test_eta_exactly_10_minutes_not_delayed(make_store, make_order, store_loc):
    """When total duration is exactly 10 minutes, delayed_promise is False."""
    service = PickingService()
    store = make_store()
    # 2 picking + 8 travel = 10 minutes total
    # 8 travel -> ceil(1.5 * d) = 8 -> e.g. d = 5.0 km (1.5 * 5 = 7.5 -> ceil = 8)
    order = make_order(
        delivery_location=_coord_north(store_loc, 5.0),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    eta_res = service.calculate_eta(store, order)

    assert eta_res.picking_minutes == 2
    assert eta_res.travel_minutes == 8
    assert eta_res.total_duration_minutes == 10
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 10, 0)
    assert eta_res.is_delayed_promise is False
    assert order.delayed_promise is False


def test_eta_greater_than_10_minutes_delayed(make_store, make_order, store_loc):
    """When total duration is > 10 minutes and slow_delivery=False, delayed_promise is True."""
    service = PickingService()
    store = make_store()
    # 2 picking + 9 travel = 11 minutes total
    # 9 travel -> ceil(1.5 * d) = 9 -> e.g. d = 5.5 km (1.5 * 5.5 = 8.25 -> ceil = 9)
    order = make_order(
        delivery_location=_coord_north(store_loc, 5.5),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
        slow_delivery=False,
    )

    eta_res = service.calculate_eta(store, order)

    assert eta_res.picking_minutes == 2
    assert eta_res.travel_minutes == 9
    assert eta_res.total_duration_minutes == 11
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 11, 0)
    assert eta_res.is_delayed_promise is True
    assert order.delayed_promise is True


def test_eta_slow_delivery_suppresses_delayed_promise(make_store, make_order, store_loc):
    """Customer opt-in to slow delivery suppresses delayed_promise flag even when total duration > 10."""
    service = PickingService()
    store = make_store()
    # 11 minutes total duration
    order = make_order(
        delivery_location=_coord_north(store_loc, 5.5),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
        slow_delivery=True,
    )

    eta_res = service.calculate_eta(store, order)

    assert eta_res.total_duration_minutes == 11
    assert eta_res.is_delayed_promise is False
    assert order.delayed_promise is False


def test_eta_earlier_drops_adjustment(make_store, make_order, store_loc):
    """Each earlier drop adds exactly 2 minutes to total ETA."""
    service = PickingService()
    store = make_store()
    # d = 2.0 km -> 3 min travel + 2 picking = 5 min base
    order = make_order(
        delivery_location=_coord_north(store_loc, 2.0),
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    # 1 earlier drop (+2 min -> 7 min)
    eta_1 = service.calculate_eta(store, order, earlier_drops=1)
    assert eta_1.earlier_drops_minutes == 2
    assert eta_1.total_duration_minutes == 7

    # 2 earlier drops (+4 min -> 9 min)
    eta_2 = service.calculate_eta(store, order, earlier_drops=2)
    assert eta_2.earlier_drops_minutes == 4
    assert eta_2.total_duration_minutes == 9


def test_eta_based_on_order_time_not_slot_start(make_store, make_order, store_loc):
    """Promised ETA is based on order.order_time, NOT assigned_slot_start."""
    service = PickingService()
    store = make_store()
    # Order arrives at 10:04:00 (slot is 10:00:00)
    # Distance 0 km -> duration 2 min
    # Promised ETA should be 10:04:00 + 2m = 10:06:00, NOT 10:00:00 + 2m = 10:02:00
    order = make_order(
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 4, 0),
    )

    eta_res = service.calculate_eta(store, order)
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 6, 0)


def test_process_order_picking_and_eta_coordinates_both(make_store, make_order, store_loc):
    """process_order_picking_and_eta schedules picking and calculates ETA with overflow delay."""
    service = PickingService()
    store = make_store(picking_capacity=1)
    slot_time = datetime(2026, 11, 3, 10, 2, 0)

    # First order occupies slot
    o1 = make_order(order_id="o1", order_time=slot_time)
    service.process_order_picking_and_eta(store, o1)

    # Second order overflows: 10 min delay + 2 picking + 0 travel = 12 min total duration
    o2 = make_order(order_id="o2", order_time=slot_time)
    sched_res, eta_res = service.process_order_picking_and_eta(store, o2)

    assert sched_res.overflow_count == 1
    assert sched_res.slot_delay_minutes == 10
    assert eta_res.slot_delay_minutes == 10
    assert eta_res.total_duration_minutes == 12
    assert eta_res.promised_eta == slot_time + timedelta(minutes=12)
    assert eta_res.is_delayed_promise is True


def test_recalculate_eta_preserves_slot_delay(make_store, make_order, store_loc):
    """recalculate_eta preserves the prior picking overflow delay when adding earlier drops."""
    service = PickingService()
    store = make_store(picking_capacity=1)
    slot_time = datetime(2026, 11, 3, 10, 5, 0)

    # First order occupies slot
    o1 = make_order(order_id="o1", order_time=slot_time)
    service.schedule_picking(store, o1)

    # Second order has 1 overflow (10 min delay)
    o2 = make_order(
        order_id="o2",
        delivery_location=_coord_north(store_loc, 2.0),  # 3 min travel
        order_time=slot_time,
    )
    sched_res, eta_res = service.process_order_picking_and_eta(store, o2)
    assert eta_res.slot_delay_minutes == 10
    assert eta_res.total_duration_minutes == 15  # 10 delay + 2 pick + 3 travel

    # BatchingService now adds 1 earlier drop
    recalc_res = service.recalculate_eta(store, o2, earlier_drops=1)
    assert recalc_res.slot_delay_minutes == 10
    assert recalc_res.earlier_drops_minutes == 2
    assert recalc_res.total_duration_minutes == 17  # 10 delay + 2 pick + 3 travel + 2 drop
    assert o2.promised_eta == slot_time + timedelta(minutes=17)


def test_recalculate_eta_without_prior_schedule_defaults_delay_to_zero(
    make_store, make_order, store_loc
):
    """recalculate_eta on an unscheduled order proceeds with 0 slot delay."""
    service = PickingService()
    store = make_store()
    order = make_order(
        order_id="unscheduled_order",
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    res = service.recalculate_eta(store, order, earlier_drops=1)
    assert res.slot_delay_minutes == 0
    assert res.earlier_drops_minutes == 2
    assert res.total_duration_minutes == 4  # 0 delay + 2 pick + 0 travel + 2 drop


# ---------------------------------------------------------------------------
# Timezone Awareness & Parity Tests
# ---------------------------------------------------------------------------


def test_timezone_aware_preservation(make_store, make_order):
    """Timezone awareness of order_time is preserved in promised_eta."""
    service = PickingService()
    store = make_store()
    tz = timezone(timedelta(hours=5, minutes=30))
    ot = datetime(2026, 11, 3, 10, 0, 0, tzinfo=tz)
    order = make_order(order_time=ot)

    eta_res = service.calculate_eta(store, order)
    assert eta_res.promised_eta.tzinfo == tz
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 2, 0, tzinfo=tz)


def test_timezone_parity_mismatch_reference_time(make_store, make_order):
    """Mismatch between reference_time and order_time raises TypeError."""
    service = PickingService()
    store = make_store()
    tz = timezone.utc

    naive_ot = datetime(2026, 11, 3, 10, 0, 0)
    aware_ref = datetime(2026, 11, 3, 10, 0, 0, tzinfo=tz)
    order_naive = make_order(order_time=naive_ot)

    with pytest.raises(TypeError, match="Cannot compare timezone-aware reference_time"):
        service.calculate_eta(store, order_naive, reference_time=aware_ref)

    with pytest.raises(TypeError, match="Cannot compare timezone-aware reference_time"):
        service.schedule_picking(store, order_naive, reference_time=aware_ref)

    aware_ot = datetime(2026, 11, 3, 10, 0, 0, tzinfo=tz)
    naive_ref = datetime(2026, 11, 3, 10, 0, 0)
    order_aware = make_order(order_time=aware_ot)

    with pytest.raises(TypeError, match="Cannot compare timezone-naive reference_time"):
        service.calculate_eta(store, order_aware, reference_time=naive_ref)

    with pytest.raises(TypeError, match="Cannot compare timezone-naive reference_time"):
        service.schedule_picking(store, order_aware, reference_time=naive_ref)


# ---------------------------------------------------------------------------
# Input Validation & Error Handling
# ---------------------------------------------------------------------------


def test_invalid_earlier_drops(make_store, make_order):
    service = PickingService()
    store = make_store()
    order = make_order()

    with pytest.raises(TypeError, match="earlier_drops must be an integer"):
        service.calculate_eta(store, order, earlier_drops=True)  # type: ignore

    with pytest.raises(TypeError, match="earlier_drops must be an integer"):
        service.calculate_eta(store, order, earlier_drops="1")  # type: ignore

    with pytest.raises(ValueError, match="earlier_drops cannot be negative"):
        service.calculate_eta(store, order, earlier_drops=-1)


def test_invalid_slot_delay_minutes(make_store, make_order):
    service = PickingService()
    store = make_store()
    order = make_order()

    with pytest.raises(TypeError, match="slot_delay_minutes must be an integer"):
        service.calculate_eta(store, order, slot_delay_minutes=True)  # type: ignore

    with pytest.raises(ValueError, match="slot_delay_minutes cannot be negative"):
        service.calculate_eta(store, order, slot_delay_minutes=-10)


def test_missing_order_delivery_location(make_store):
    service = PickingService()
    store = make_store()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-1", 1, Decimal("10.00"))],
        delivery_location=None,
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    with pytest.raises(
        ValueError, match="order.delivery_location is required to calculate ETA"
    ):
        service.calculate_eta(store, order)


def test_missing_order_order_time(make_store, store_loc):
    service = PickingService()
    store = make_store()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-1", 1, Decimal("10.00"))],
        delivery_location=store_loc,
        order_time=None,
    )

    with pytest.raises(
        ValueError, match="order.order_time or reference_time is required"
    ):
        service.calculate_eta(store, order)

    with pytest.raises(
        ValueError, match="An arrival timestamp is required to schedule picking"
    ):
        service.schedule_picking(store, order)


def test_invalid_store_or_order_types(make_store, make_order):
    service = PickingService()
    store = make_store()
    order = make_order()

    with pytest.raises(TypeError, match="store must be a Store instance"):
        service.schedule_picking("invalid_store", order)  # type: ignore

    with pytest.raises(TypeError, match="order must be a Order instance"):
        service.schedule_picking(store, "invalid_order")  # type: ignore

    with pytest.raises(TypeError, match="store must be a Store instance"):
        service.calculate_eta("invalid_store", order)  # type: ignore

    with pytest.raises(TypeError, match="order must be a Order instance"):
        service.calculate_eta(store, "invalid_order")  # type: ignore


def test_get_slot_load_invalid_inputs(make_store):
    service = PickingService()

    with pytest.raises(TypeError, match="store_id must be a string"):
        service.get_slot_load(123, datetime.now())  # type: ignore

    with pytest.raises(ValueError, match="store_id must not be empty"):
        service.get_slot_load("  ", datetime.now())

    with pytest.raises(TypeError, match="timestamp must be a datetime"):
        service.get_slot_load("store_01", "2026-11-03")  # type: ignore


# ---------------------------------------------------------------------------
# Global Order Uniqueness Tests
# ---------------------------------------------------------------------------


def test_global_order_uniqueness_same_store(make_store, make_order):
    """An order scheduled twice at the same store returns the existing assignment and keeps occupancy at 1."""
    service = PickingService()
    store = make_store(store_id="store_01")
    order = make_order(order_id="order_unique_1")

    res1 = service.schedule_picking(store, order)
    res2 = service.schedule_picking(store, order)

    assert res1 == res2
    assert service.get_slot_load(store.store_id, order.order_time) == 1


def test_global_order_uniqueness_different_store_rejected(make_store, make_order):
    """An order already scheduled at one store cannot be scheduled at a different store."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_unique_2")

    res_a = service.schedule_picking(store_a, order)
    assert res_a.store_id == "store_A"

    with pytest.raises(
        ValueError,
        match="Order 'order_unique_2' is already scheduled at store 'store_A' and cannot be scheduled at store 'store_B'",
    ):
        service.schedule_picking(store_b, order)


def test_global_order_uniqueness_rejection_preserves_state(make_store, make_order):
    """When a second store scheduling is rejected, original occupancy and assignment remain unchanged."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_unique_3")

    res_a = service.schedule_picking(store_a, order)

    with pytest.raises(ValueError):
        service.schedule_picking(store_b, order)

    # Original store occupancy intact
    assert service.get_slot_load(store_a.store_id, order.order_time) == 1
    # Second store occupancy remains 0
    assert service.get_slot_load(store_b.store_id, order.order_time) == 0
    # Original assignment intact
    assert service._order_schedules[order.order_id] == res_a
    assert service._order_schedules[order.order_id].store_id == "store_A"


# ---------------------------------------------------------------------------
# Reference Time Precedence Tests
# ---------------------------------------------------------------------------


def test_reference_time_precedence_over_order_time(make_store, make_order, store_loc):
    """When reference_time is explicitly supplied, it is the base timestamp for ETA calculation."""
    service = PickingService()
    store = make_store()
    order_time = datetime(2026, 11, 3, 10, 0, 0)
    ref_time = datetime(2026, 11, 3, 10, 5, 0)

    order = make_order(
        delivery_location=store_loc,
        order_time=order_time,
    )

    # Distance is 0 km -> total duration is 2 minutes (picking only)
    eta_res = service.calculate_eta(store, order, reference_time=ref_time)

    # Base timestamp is reference_time (10:05), so ETA is 10:07, NOT 10:02
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 7, 0)
    assert order.promised_eta == datetime(2026, 11, 3, 10, 7, 0)


def test_scheduling_and_eta_use_same_effective_timestamp(
    make_store, make_order, store_loc
):
    """schedule_picking and calculate_eta both use reference_time as effective timestamp."""
    service = PickingService()
    store = make_store()
    order_time = datetime(2026, 11, 3, 10, 0, 0)
    ref_time = datetime(2026, 11, 3, 10, 15, 0)

    order = make_order(
        delivery_location=store_loc,
        order_time=order_time,
    )

    # Scheduling should use 10:15 -> slot [10:10, 10:20), NOT [10:00, 10:10)
    sched_res = service.schedule_picking(store, order, reference_time=ref_time)
    assert sched_res.initial_slot_start == datetime(2026, 11, 3, 10, 10, 0)
    assert sched_res.assigned_slot_start == datetime(2026, 11, 3, 10, 10, 0)

    # ETA should be 10:15 + 2m = 10:17
    eta_res = service.calculate_eta(store, order, reference_time=ref_time)
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 17, 0)


# ---------------------------------------------------------------------------
# Combined-Operation Rollback Tests
# ---------------------------------------------------------------------------


def test_process_order_picking_and_eta_upfront_validation_no_occupancy(
    make_store, make_order
):
    """If process_order_picking_and_eta fails on input validation, no occupancy is consumed."""
    service = PickingService()
    store = make_store()
    order = Order(
        order_id="order_no_loc",
        customer_id="c1",
        items=[OrderItem("SKU-1", 1, Decimal("10.00"))],
        delivery_location=None,  # missing delivery location
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    with pytest.raises(ValueError, match="order.delivery_location is required"):
        service.process_order_picking_and_eta(store, order)

    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 0
    assert "order_no_loc" not in service._order_schedules


def test_process_order_picking_and_eta_rollback_on_eta_failure(
    make_store, make_order, monkeypatch
):
    """If calculate_eta raises unexpectedly after scheduling, newly scheduled order is rolled back."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_rollback_1")

    # Monkeypatch calculate_eta to simulate an unexpected calculation crash
    def mock_calculate_eta(*args, **kwargs):
        raise RuntimeError("Unexpected failure during ETA calculation")

    monkeypatch.setattr(service, "calculate_eta", mock_calculate_eta)

    with pytest.raises(RuntimeError, match="Unexpected failure during ETA calculation"):
        service.process_order_picking_and_eta(store, order)

    # State must be rolled back: 0 load, order not in schedules
    assert service.get_slot_load(store.store_id, order.order_time) == 0
    assert order.order_id not in service._order_schedules
    assert (store.store_id, order.order_id) not in service._order_assignments


def test_process_order_picking_and_eta_does_not_rollback_preexisting_schedule(
    make_store, make_order, monkeypatch
):
    """If process_order_picking_and_eta fails on an already-scheduled order, existing assignment is preserved."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_prescheduled")

    # Order was already scheduled before calling process_order_picking_and_eta
    pre_sched = service.schedule_picking(store, order)
    assert service.get_slot_load(store.store_id, order.order_time) == 1

    # Simulate failure in calculate_eta
    def mock_calculate_eta(*args, **kwargs):
        raise RuntimeError("ETA calculation crash")

    monkeypatch.setattr(service, "calculate_eta", mock_calculate_eta)

    with pytest.raises(RuntimeError):
        service.process_order_picking_and_eta(store, order)

    # Pre-existing schedule must NOT be rolled back
    assert service.get_slot_load(store.store_id, order.order_time) == 1
    assert service._order_schedules[order.order_id] == pre_sched


# ---------------------------------------------------------------------------
# Custom Timezone Awareness (utcoffset returns None) Tests
# ---------------------------------------------------------------------------


def test_timezone_custom_tzinfo_with_none_utcoffset_treated_as_naive(
    make_store, make_order
):
    """A datetime with custom tzinfo whose utcoffset() is None is naive under Python semantics."""

    class NaiveLikeTz(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

        def tzname(self, dt):
            return "NAIVE_LIKE"

    service = PickingService()
    store = make_store()
    naive_like_tz = NaiveLikeTz()

    dt_naive_like = datetime(2026, 11, 3, 10, 0, 0, tzinfo=naive_like_tz)
    dt_naive_standard = datetime(2026, 11, 3, 10, 0, 0)
    dt_aware_utc = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)

    # Comparing naive-like datetime with standard naive datetime succeeds (both naive)
    order1 = make_order(order_id="o_naive_1", order_time=dt_naive_like)
    eta_res = service.calculate_eta(store, order1, reference_time=dt_naive_standard)
    assert eta_res.promised_eta == dt_naive_standard + timedelta(minutes=2)

    # Comparing naive-like datetime with aware UTC datetime raises TypeError
    order2 = make_order(order_id="o_naive_2", order_time=dt_naive_like)
    with pytest.raises(TypeError, match="Cannot compare timezone-aware reference_time"):
        service.calculate_eta(store, order2, reference_time=dt_aware_utc)


# ---------------------------------------------------------------------------
# Missing Regression Tests
# ---------------------------------------------------------------------------


def test_idempotent_scheduling_on_overflowed_order(make_store, make_order):
    """Re-scheduling an order that was assigned to an overflow slot returns identical assignment."""
    service = PickingService()
    store = make_store(picking_capacity=1)
    slot_time = datetime(2026, 11, 3, 10, 0, 0)

    # First order occupies slot 10:00
    o1 = make_order(order_id="o1", order_time=slot_time)
    service.schedule_picking(store, o1)

    # Second order overflows to slot 10:10
    o2 = make_order(order_id="o2", order_time=slot_time)
    res_overflow = service.schedule_picking(store, o2)
    assert res_overflow.overflow_count == 1
    assert res_overflow.slot_delay_minutes == 10
    assert res_overflow.assigned_slot_start == datetime(2026, 11, 3, 10, 10, 0)

    # Re-scheduling o2 must return the same overflowed assignment and not consume another slot
    res_repeat = service.schedule_picking(store, o2)
    assert res_repeat == res_overflow
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 1
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 10, 0)) == 1
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 20, 0)) == 0


def test_repeated_scheduling_with_different_reference_time(make_store, make_order):
    """Re-scheduling an order with a later reference_time returns original assignment without changing slots."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_repeat_ref")

    t1 = datetime(2026, 11, 3, 10, 2, 0)
    t2 = datetime(2026, 11, 3, 10, 25, 0)

    res1 = service.schedule_picking(store, order, reference_time=t1)
    assert res1.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)

    res2 = service.schedule_picking(store, order, reference_time=t2)
    assert res2 == res1
    assert res2.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert service.get_slot_load(store.store_id, t1) == 1
    assert service.get_slot_load(store.store_id, t2) == 0


def test_invalid_scheduling_input_does_not_change_occupancy(make_store, make_order):
    """Failed scheduling due to invalid inputs leaves slot occupancy at 0."""
    service = PickingService()
    store = make_store()
    slot_time = datetime(2026, 11, 3, 10, 0, 0)

    order_naive = make_order(order_id="o_naive", order_time=slot_time)
    aware_ref = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        service.schedule_picking(store, order_naive, reference_time=aware_ref)

    assert service.get_slot_load(store.store_id, slot_time) == 0


def test_recalculate_eta_cannot_retrieve_another_store_assignment(
    make_store, make_order
):
    """recalculate_eta raises ValueError if attempted with a different store than the scheduled one."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_cross_store")

    service.schedule_picking(store_a, order)

    with pytest.raises(
        ValueError,
        match="Order 'order_cross_store' is scheduled at store 'store_A', cannot recalculate ETA for store 'store_B'",
    ):
        service.recalculate_eta(store_b, order, earlier_drops=1)

# ---------------------------------------------------------------------------
# Global Order Uniqueness Tests
# ---------------------------------------------------------------------------


def test_global_order_uniqueness_same_store(make_store, make_order):
    """An order scheduled twice at the same store returns the existing assignment and keeps occupancy at 1."""
    service = PickingService()
    store = make_store(store_id="store_01")
    order = make_order(order_id="order_unique_1")

    res1 = service.schedule_picking(store, order)
    res2 = service.schedule_picking(store, order)

    assert res1 == res2
    assert service.get_slot_load(store.store_id, order.order_time) == 1


def test_global_order_uniqueness_different_store_rejected(make_store, make_order):
    """An order already scheduled at one store cannot be scheduled at a different store."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_unique_2")

    res_a = service.schedule_picking(store_a, order)
    assert res_a.store_id == "store_A"

    with pytest.raises(
        ValueError,
        match="Order 'order_unique_2' is already scheduled at store 'store_A' and cannot be scheduled at store 'store_B'",
    ):
        service.schedule_picking(store_b, order)


def test_global_order_uniqueness_rejection_preserves_state(make_store, make_order):
    """When a second store scheduling is rejected, original occupancy and assignment remain unchanged."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_unique_3")

    res_a = service.schedule_picking(store_a, order)

    with pytest.raises(ValueError):
        service.schedule_picking(store_b, order)

    # Original store occupancy intact
    assert service.get_slot_load(store_a.store_id, order.order_time) == 1
    # Second store occupancy remains 0
    assert service.get_slot_load(store_b.store_id, order.order_time) == 0
    # Original assignment intact
    assert service._order_schedules[order.order_id] == res_a
    assert service._order_schedules[order.order_id].store_id == "store_A"


# ---------------------------------------------------------------------------
# Reference Time Precedence Tests
# ---------------------------------------------------------------------------


def test_reference_time_precedence_over_order_time(make_store, make_order, store_loc):
    """When reference_time is explicitly supplied, it is the base timestamp for ETA calculation."""
    service = PickingService()
    store = make_store()
    order_time = datetime(2026, 11, 3, 10, 0, 0)
    ref_time = datetime(2026, 11, 3, 10, 5, 0)

    order = make_order(
        delivery_location=store_loc,
        order_time=order_time,
    )

    # Distance is 0 km -> total duration is 2 minutes (picking only)
    eta_res = service.calculate_eta(store, order, reference_time=ref_time)

    # Base timestamp is reference_time (10:05), so ETA is 10:07, NOT 10:02
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 7, 0)
    assert order.promised_eta == datetime(2026, 11, 3, 10, 7, 0)


def test_scheduling_and_eta_use_same_effective_timestamp(
    make_store, make_order, store_loc
):
    """schedule_picking and calculate_eta both use reference_time as effective timestamp."""
    service = PickingService()
    store = make_store()
    order_time = datetime(2026, 11, 3, 10, 0, 0)
    ref_time = datetime(2026, 11, 3, 10, 15, 0)

    order = make_order(
        delivery_location=store_loc,
        order_time=order_time,
    )

    # Scheduling should use 10:15 -> slot [10:10, 10:20), NOT [10:00, 10:10)
    sched_res = service.schedule_picking(store, order, reference_time=ref_time)
    assert sched_res.initial_slot_start == datetime(2026, 11, 3, 10, 10, 0)
    assert sched_res.assigned_slot_start == datetime(2026, 11, 3, 10, 10, 0)

    # ETA should be 10:15 + 2m = 10:17
    eta_res = service.calculate_eta(store, order, reference_time=ref_time)
    assert eta_res.promised_eta == datetime(2026, 11, 3, 10, 17, 0)


# ---------------------------------------------------------------------------
# Combined-Operation Rollback Tests
# ---------------------------------------------------------------------------


def test_process_order_picking_and_eta_upfront_validation_no_occupancy(
    make_store, make_order
):
    """If process_order_picking_and_eta fails on input validation, no occupancy is consumed."""
    service = PickingService()
    store = make_store()
    order = Order(
        order_id="order_no_loc",
        customer_id="c1",
        items=[OrderItem("SKU-1", 1, Decimal("10.00"))],
        delivery_location=None,  # missing delivery location
        order_time=datetime(2026, 11, 3, 10, 0, 0),
    )

    with pytest.raises(ValueError, match="order.delivery_location is required"):
        service.process_order_picking_and_eta(store, order)

    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 0
    assert "order_no_loc" not in service._order_schedules


def test_process_order_picking_and_eta_rollback_on_eta_failure(
    make_store, make_order, monkeypatch
):
    """If calculate_eta raises unexpectedly after scheduling, newly scheduled order is rolled back."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_rollback_1")

    # Monkeypatch calculate_eta to simulate an unexpected calculation crash
    def mock_calculate_eta(*args, **kwargs):
        raise RuntimeError("Unexpected failure during ETA calculation")

    monkeypatch.setattr(service, "calculate_eta", mock_calculate_eta)

    with pytest.raises(RuntimeError, match="Unexpected failure during ETA calculation"):
        service.process_order_picking_and_eta(store, order)

    # State must be rolled back: 0 load, order not in schedules
    assert service.get_slot_load(store.store_id, order.order_time) == 0
    assert order.order_id not in service._order_schedules
    assert (store.store_id, order.order_id) not in service._order_assignments


def test_process_order_picking_and_eta_does_not_rollback_preexisting_schedule(
    make_store, make_order, monkeypatch
):
    """If process_order_picking_and_eta fails on an already-scheduled order, existing assignment is preserved."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_prescheduled")

    # Order was already scheduled before calling process_order_picking_and_eta
    pre_sched = service.schedule_picking(store, order)
    assert service.get_slot_load(store.store_id, order.order_time) == 1

    # Simulate failure in calculate_eta
    def mock_calculate_eta(*args, **kwargs):
        raise RuntimeError("ETA calculation crash")

    monkeypatch.setattr(service, "calculate_eta", mock_calculate_eta)

    with pytest.raises(RuntimeError):
        service.process_order_picking_and_eta(store, order)

    # Pre-existing schedule must NOT be rolled back
    assert service.get_slot_load(store.store_id, order.order_time) == 1
    assert service._order_schedules[order.order_id] == pre_sched


# ---------------------------------------------------------------------------
# Custom Timezone Awareness (utcoffset returns None) Tests
# ---------------------------------------------------------------------------


def test_timezone_custom_tzinfo_with_none_utcoffset_treated_as_naive(
    make_store, make_order
):
    """A datetime with custom tzinfo whose utcoffset() is None is naive under Python semantics."""

    class NaiveLikeTz(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

        def tzname(self, dt):
            return "NAIVE_LIKE"

    service = PickingService()
    store = make_store()
    naive_like_tz = NaiveLikeTz()

    dt_naive_like = datetime(2026, 11, 3, 10, 0, 0, tzinfo=naive_like_tz)
    dt_naive_standard = datetime(2026, 11, 3, 10, 0, 0)
    dt_aware_utc = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)

    # Comparing naive-like datetime with standard naive datetime succeeds (both naive)
    order1 = make_order(order_id="o_naive_1", order_time=dt_naive_like)
    eta_res = service.calculate_eta(store, order1, reference_time=dt_naive_standard)
    assert eta_res.promised_eta == dt_naive_standard + timedelta(minutes=2)

    # Comparing naive-like datetime with aware UTC datetime raises TypeError
    order2 = make_order(order_id="o_naive_2", order_time=dt_naive_like)
    with pytest.raises(TypeError, match="Cannot compare timezone-aware reference_time"):
        service.calculate_eta(store, order2, reference_time=dt_aware_utc)


# ---------------------------------------------------------------------------
# Missing Regression Tests
# ---------------------------------------------------------------------------


def test_idempotent_scheduling_on_overflowed_order(make_store, make_order):
    """Re-scheduling an order that was assigned to an overflow slot returns identical assignment."""
    service = PickingService()
    store = make_store(picking_capacity=1)
    slot_time = datetime(2026, 11, 3, 10, 0, 0)

    # First order occupies slot 10:00
    o1 = make_order(order_id="o1", order_time=slot_time)
    service.schedule_picking(store, o1)

    # Second order overflows to slot 10:10
    o2 = make_order(order_id="o2", order_time=slot_time)
    res_overflow = service.schedule_picking(store, o2)
    assert res_overflow.overflow_count == 1
    assert res_overflow.slot_delay_minutes == 10
    assert res_overflow.assigned_slot_start == datetime(2026, 11, 3, 10, 10, 0)

    # Re-scheduling o2 must return the same overflowed assignment and not consume another slot
    res_repeat = service.schedule_picking(store, o2)
    assert res_repeat == res_overflow
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 0, 0)) == 1
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 10, 0)) == 1
    assert service.get_slot_load(store.store_id, datetime(2026, 11, 3, 10, 20, 0)) == 0


def test_repeated_scheduling_with_different_reference_time(make_store, make_order):
    """Re-scheduling an order with a later reference_time returns original assignment without changing slots."""
    service = PickingService()
    store = make_store()
    order = make_order(order_id="order_repeat_ref")

    t1 = datetime(2026, 11, 3, 10, 2, 0)
    t2 = datetime(2026, 11, 3, 10, 25, 0)

    res1 = service.schedule_picking(store, order, reference_time=t1)
    assert res1.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)

    res2 = service.schedule_picking(store, order, reference_time=t2)
    assert res2 == res1
    assert res2.assigned_slot_start == datetime(2026, 11, 3, 10, 0, 0)
    assert service.get_slot_load(store.store_id, t1) == 1
    assert service.get_slot_load(store.store_id, t2) == 0


def test_invalid_scheduling_input_does_not_change_occupancy(make_store, make_order):
    """Failed scheduling due to invalid inputs leaves slot occupancy at 0."""
    service = PickingService()
    store = make_store()
    slot_time = datetime(2026, 11, 3, 10, 0, 0)

    order_naive = make_order(order_id="o_naive", order_time=slot_time)
    aware_ref = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(TypeError):
        service.schedule_picking(store, order_naive, reference_time=aware_ref)

    assert service.get_slot_load(store.store_id, slot_time) == 0


def test_recalculate_eta_cannot_retrieve_another_store_assignment(
    make_store, make_order
):
    """recalculate_eta raises ValueError if attempted with a different store than the scheduled one."""
    service = PickingService()
    store_a = make_store(store_id="store_A")
    store_b = make_store(store_id="store_B")
    order = make_order(order_id="order_cross_store")

    service.schedule_picking(store_a, order)

    with pytest.raises(
        ValueError,
        match="Order 'order_cross_store' is scheduled at store 'store_A', cannot recalculate ETA for store 'store_B'",
    ):
        service.recalculate_eta(store_b, order, earlier_drops=1)
