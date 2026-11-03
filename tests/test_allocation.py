from datetime import datetime, time, timezone
from decimal import Decimal
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem
from ps031.models.order_state import InvalidOrderTransitionError, OrderState
from ps031.models.store import Store
from ps031.services.allocation_service import (
    AllocationResult,
    AllocationService,
    AllocationServiceInterface,
    StoreEvaluation,
)
from ps031.utils.distance import EARTH_RADIUS_KM


def _coord_north(origin: Coordinates, km: float) -> Coordinates:
    """Helper to produce coordinates exactly `km` north of origin."""
    import math

    dlat = (km * 180.0) / (EARTH_RADIUS_KM * math.pi)
    return Coordinates(latitude=origin.latitude + dlat, longitude=origin.longitude)


@pytest.fixture
def store_hours():
    return OperatingHours(open_time=time(6, 0), close_time=time(23, 0))


@pytest.fixture
def store_loc():
    return Coordinates(latitude=12.9716, longitude=77.5946)


@pytest.fixture
def base_store(store_loc, store_hours):
    return Store(
        store_id="store_01",
        name="Indiranagar Dark Store",
        location=store_loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10, "SKU-BREAD": 10},
    )


# ---------------------------------------------------------------------------
# Protocol Adherence
# ---------------------------------------------------------------------------


def test_allocation_service_implements_protocol():
    """AllocationService satisfies AllocationServiceInterface protocol."""
    service = AllocationService()
    assert isinstance(service, AllocationServiceInterface)


# ---------------------------------------------------------------------------
# BR-01: Serviceability Tests
# ---------------------------------------------------------------------------


def test_serviceability_opening_time_inclusive(base_store, store_loc):
    """Store is serviceable exactly at opening time (open_time inclusive)."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 6, 0, 0),
    )
    assert service.is_serviceable(base_store, order) is True


def test_serviceability_closing_time_exclusive(base_store, store_loc):
    """Store is unserviceable exactly at closing time (close_time exclusive)."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 23, 0, 0),
    )
    assert service.is_serviceable(base_store, order) is False


def test_serviceability_before_opening(base_store, store_loc):
    """Store is unserviceable one second before opening time."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 5, 59, 59),
    )
    assert service.is_serviceable(base_store, order) is False


def test_serviceability_after_closing(base_store, store_loc):
    """Store is unserviceable after closing time."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 23, 30, 0),
    )
    assert service.is_serviceable(base_store, order) is False


def test_serviceability_within_operating_hours(base_store, store_loc):
    """Store is serviceable within regular operating hours."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 14, 30, 0),
    )
    assert service.is_serviceable(base_store, order) is True


def test_serviceability_distance_thresholds(base_store, store_loc):
    """Stores within 3.00 km are serviceable; beyond 3.00 km are not."""
    service = AllocationService()

    # 3.000 km -> serviceable
    order_3km = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=_coord_north(store_loc, 3.000),
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    assert service.is_serviceable(base_store, order_3km) is True

    # 3.004 km -> rounds to 3.00 km -> serviceable
    order_3_004km = Order(
        order_id="o2",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=_coord_north(store_loc, 3.004),
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    assert service.is_serviceable(base_store, order_3_004km) is True

    # 3.005 km -> rounds to 3.01 km -> unserviceable
    order_3_005km = Order(
        order_id="o3",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=_coord_north(store_loc, 3.005),
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    assert service.is_serviceable(base_store, order_3_005km) is False


def test_serviceability_active_weather_hold(base_store, store_loc):
    """Store under active weather hold is unserviceable."""
    service = AllocationService()
    base_store.weather_hold_until = datetime(2026, 11, 3, 11, 0)

    # Order during weather hold
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 30),
    )
    assert service.is_serviceable(base_store, order) is False


def test_serviceability_weather_hold_expiring_exactly_at_eval_time(
    base_store, store_loc
):
    """Store is serviceable when weather hold expires exactly at evaluation time."""
    service = AllocationService()
    base_store.weather_hold_until = datetime(2026, 11, 3, 11, 0)

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 11, 0),
    )
    assert service.is_serviceable(base_store, order) is True


def test_serviceability_missing_evaluation_timestamp(base_store, store_loc):
    """Missing evaluation timestamp raises ValueError."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=None,
    )
    with pytest.raises(ValueError, match="Evaluation timestamp is required"):
        service.is_serviceable(base_store, order)


def test_serviceability_missing_delivery_location(base_store):
    """Missing order delivery location raises ValueError."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=None,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    with pytest.raises(ValueError, match="delivery_location is required"):
        service.is_serviceable(base_store, order)


def test_serviceability_reference_time_override(base_store, store_loc):
    """reference_time takes precedence over order.order_time."""
    service = AllocationService()
    # order_time is outside operating hours (03:00)
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 3, 0),
    )
    assert service.is_serviceable(base_store, order) is False

    # Overridden by daytime reference_time
    ref_time = datetime(2026, 11, 3, 12, 0)
    assert service.is_serviceable(base_store, order, reference_time=ref_time) is True


def test_serviceability_rejects_aware_eval_time_with_naive_hours(store_loc, base_store):
    """Aware evaluation timestamp with naive store operating hours raises TypeError."""
    service = AllocationService()
    aware_order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(TypeError, match="timezone-aware.*timezone-naive"):
        service.is_serviceable(base_store, aware_order)

    with pytest.raises(TypeError, match="timezone-aware.*timezone-naive"):
        service.evaluate_store(base_store, aware_order)

    with pytest.raises(TypeError, match="timezone-aware.*timezone-naive"):
        service.allocate_store(aware_order, [base_store])


def test_serviceability_rejects_naive_eval_time_with_aware_hours(store_loc):
    """Naive evaluation timestamp with aware store operating hours raises TypeError."""
    service = AllocationService()
    aware_hours = OperatingHours(
        open_time=time(6, 0, tzinfo=timezone.utc),
        close_time=time(23, 0, tzinfo=timezone.utc),
    )
    store = Store(
        store_id="s_aware",
        name="Aware Store",
        location=store_loc,
        operating_hours=aware_hours,
        inventory={"SKU-MILK": 10},
    )
    naive_order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    with pytest.raises(TypeError, match="timezone-naive.*timezone-aware"):
        service.is_serviceable(store, naive_order)

    with pytest.raises(TypeError, match="timezone-naive.*timezone-aware"):
        service.evaluate_store(store, naive_order)

    with pytest.raises(TypeError, match="timezone-naive.*timezone-aware"):
        service.allocate_store(naive_order, [store])


def test_serviceability_matching_timezone_aware_succeeds(store_loc):
    """Both eval_time and operating hours being timezone-aware evaluates successfully."""
    service = AllocationService()
    aware_hours = OperatingHours(
        open_time=time(6, 0, tzinfo=timezone.utc),
        close_time=time(23, 0, tzinfo=timezone.utc),
    )
    store = Store(
        store_id="s_aware",
        name="Aware Store",
        location=store_loc,
        operating_hours=aware_hours,
        inventory={"SKU-MILK": 10},
    )
    aware_order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc),
    )
    assert service.is_serviceable(store, aware_order) is True
    eval_res = service.evaluate_store(store, aware_order)
    assert eval_res.is_serviceable is True
    alloc_res = service.allocate_store(aware_order, [store])
    assert alloc_res.is_allocated is True
    assert alloc_res.selected_store.store_id == "s_aware"


# ---------------------------------------------------------------------------
# BR-02: Store Selection & Tie-Breaking Tests
# ---------------------------------------------------------------------------


def test_selection_100_percent_stock_beats_closer_incomplete_stock(
    store_loc, store_hours
):
    """Store with 100% stock is preferred over closer store with partial stock."""
    service = AllocationService()

    # Store A: Closer (0.5 km), partial stock
    store_a = Store(
        store_id="store_a",
        name="Closer Partial",
        location=_coord_north(store_loc, 0.5),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 1},  # Needs 2
    )
    # Store B: Farther (2.5 km), 100% stock
    store_b = Store(
        store_id="store_b",
        name="Farther Full",
        location=_coord_north(store_loc, 2.5),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 2, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    result = service.allocate_store(order, [store_a, store_b])
    assert result.is_allocated is True
    assert result.selected_store is not None
    assert result.selected_store.store_id == "store_b"
    assert order.status == OrderState.ALLOCATED
    assert order.allocated_store_id == "store_b"


def test_selection_highest_line_fill_when_no_store_has_100_percent(
    store_loc, store_hours
):
    """When no store has 100% stock, store with highest line-fill ratio is selected."""
    service = AllocationService()

    # Order needs: SKU-MILK: 4, SKU-BREAD: 6 (Total = 10 units)
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[
            OrderItem("SKU-MILK", 4, Decimal("30.00")),
            OrderItem("SKU-BREAD", 6, Decimal("40.00")),
        ],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    # Store A: MILK 4, BREAD 2 -> 6 / 10 = 0.60
    store_a = Store(
        store_id="store_a",
        name="Store A",
        location=_coord_north(store_loc, 1.0),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 4, "SKU-BREAD": 2},
    )
    # Store B: MILK 4, BREAD 5 -> 9 / 10 = 0.90
    store_b = Store(
        store_id="store_b",
        name="Store B",
        location=_coord_north(store_loc, 2.0),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 4, "SKU-BREAD": 5},
    )

    result = service.allocate_store(order, [store_a, store_b])
    assert result.is_allocated is True
    assert result.selected_store.store_id == "store_b"


def test_selection_distance_tie_break(store_loc, store_hours):
    """When stores tie on stock, closest store is selected."""
    service = AllocationService()

    store_a = Store(
        store_id="store_a",
        name="Store A",
        location=_coord_north(store_loc, 2.0),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )
    store_b = Store(
        store_id="store_b",
        name="Store B",
        location=_coord_north(store_loc, 1.2),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 2, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    result = service.allocate_store(order, [store_a, store_b])
    assert result.selected_store.store_id == "store_b"


def test_selection_slot_load_tie_break(store_loc, store_hours):
    """When stores tie on stock and distance, lowest slot load is selected."""
    service = AllocationService()

    # Both stores at identical 1.5 km distance
    loc = _coord_north(store_loc, 1.5)
    store_a = Store(
        store_id="store_a",
        name="Store A",
        location=loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )
    store_b = Store(
        store_id="store_b",
        name="Store B",
        location=loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 2, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    # store_a has load 8, store_b has load 3
    slot_loads = {"store_a": 8, "store_b": 3}
    result = service.allocate_store(order, [store_a, store_b], slot_loads=slot_loads)
    assert result.selected_store.store_id == "store_b"


def test_selection_deterministic_store_id_tie_break(store_loc, store_hours):
    """When stores tie on stock, distance, and load, lexicographically lowest store_id wins."""
    service = AllocationService()

    loc = _coord_north(store_loc, 1.5)
    store_z = Store(
        store_id="store_z",
        name="Store Z",
        location=loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )
    store_a = Store(
        store_id="store_a",
        name="Store A",
        location=loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 2, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    # Pass in [store_z, store_a]; store_a must win deterministically
    result = service.allocate_store(
        order, [store_z, store_a], slot_loads={"store_z": 2, "store_a": 2}
    )
    assert result.selected_store.store_id == "store_a"


def test_selection_duplicate_sku_lines_aggregated_correctly(store_loc, store_hours):
    """Duplicate SKU lines in an order are aggregated before checking available inventory."""
    service = AllocationService()

    # Store has 4 units of MILK
    store = Store(
        store_id="s1",
        name="Store 1",
        location=store_loc,
        operating_hours=store_hours,
        inventory={"SKU-MILK": 4},
    )

    # Order requests MILK twice: qty 3 + qty 2 = 5 total
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[
            OrderItem("SKU-MILK", 3, Decimal("30.00")),
            OrderItem("SKU-MILK", 2, Decimal("30.00")),
        ],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    eval_res = service.evaluate_store(store, order)
    # Available is 4, total requested is 5 -> line_fill_ratio is 4/5 = 0.80, NOT 1.0!
    assert eval_res.has_100_percent_stock is False
    assert eval_res.line_fill_ratio == 0.8


def test_selection_missing_slot_load_defaults_to_zero(store_loc, store_hours):
    """Stores missing from slot_loads mapping default to 0 load."""
    service = AllocationService()

    loc = _coord_north(store_loc, 1.0)
    store_a = Store("s_a", "Store A", loc, store_hours, inventory={"SKU-MILK": 5})
    store_b = Store("s_b", "Store B", loc, store_hours, inventory={"SKU-MILK": 5})

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 2, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    # Only store_a is in slot_loads with load 2. store_b defaults to 0, so store_b wins!
    result = service.allocate_store(order, [store_a, store_b], slot_loads={"s_a": 2})
    assert result.selected_store.store_id == "s_b"


def test_selection_rejects_empty_orders(base_store, store_loc):
    """Empty orders must raise ValueError and cannot be allocated."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    with pytest.raises(ValueError, match="at least one item"):
        service.allocate_store(order, [base_store])


def test_selection_no_serviceable_store_transitions_to_rejected(store_loc, store_hours):
    """When no serviceable store exists, order transitions to REJECTED."""
    service = AllocationService()

    # Store is 10.0 km away (out of reach)
    store_far = Store(
        store_id="s_far",
        name="Far Store",
        location=_coord_north(store_loc, 10.0),
        operating_hours=store_hours,
        inventory={"SKU-MILK": 10},
    )

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    assert order.status == OrderState.RECEIVED

    result = service.allocate_store(order, [store_far])
    assert result.is_allocated is False
    assert result.selected_store is None
    assert order.status == OrderState.REJECTED
    assert order.allocated_store_id is None


def test_selection_successful_allocation_transitions_to_allocated(
    base_store, store_loc
):
    """Successful allocation transitions order from RECEIVED to ALLOCATED and sets allocated_store_id."""
    service = AllocationService()

    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    assert order.status == OrderState.RECEIVED

    result = service.allocate_store(order, [base_store])
    assert result.is_allocated is True
    assert order.status == OrderState.ALLOCATED
    assert order.allocated_store_id == "store_01"


def test_selection_mutation_safety_on_incompatible_order_state(base_store, store_loc):
    """Attempting to allocate an order in an invalid state raises error and does NOT mutate order."""
    service = AllocationService()

    # Order is already DELIVERED
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
        status=OrderState.DELIVERED,
    )

    with pytest.raises(InvalidOrderTransitionError):
        service.allocate_store(order, [base_store])

    # Assert order attributes were untouched
    assert order.status == OrderState.DELIVERED
    assert order.allocated_store_id is None


def test_selection_rejects_invalid_slot_loads(base_store, store_loc):
    """Invalid slot_load values (booleans, negatives, non-integers) raise exceptions."""
    service = AllocationService()
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[OrderItem("SKU-MILK", 1, Decimal("30.00"))],
        delivery_location=store_loc,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    with pytest.raises(TypeError, match="integer"):
        service.allocate_store(order, [base_store], slot_loads={"store_01": True})

    with pytest.raises(TypeError, match="integer"):
        service.allocate_store(order, [base_store], slot_loads={"store_01": "5"})

    with pytest.raises(ValueError, match="negative"):
        service.allocate_store(order, [base_store], slot_loads={"store_01": -1})

    with pytest.raises(TypeError, match="Mapping"):
        service.allocate_store(order, [base_store], slot_loads=["not_a_mapping"])
