from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem
from ps031.models.order_state import InvalidOrderTransitionError, OrderState
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store
from ps031.services.batching_service import (
    MAX_BATCH_SIZE,
    MAX_DROP_PAIRWISE_DISTANCE_KM,
    MAX_ETA_DIFFERENCE_SECONDS,
    BatchDispatchResult,
    BatchEvaluation,
    BatchingService,
    BatchingServiceInterface,
    DeliveryCompletionResult,
    _unrounded_haversine,
)
from ps031.services.picking_service import PickingService


def make_test_store(store_id: str = "STORE-01") -> Store:
    """Create a standard dark store for testing."""
    return Store(
        store_id=store_id,
        name="Indiranagar Dark Store",
        location=Coordinates(latitude=12.9716, longitude=77.5946),
        operating_hours=OperatingHours(
            open_time=time(6, 0),
            close_time=time(23, 0),
        ),
        picking_capacity=12,
        active_riders=5,
    )


def make_test_order(
    order_id: str,
    store_id: str = "STORE-01",
    lat: float = 12.9730,
    lon: float = 77.5946,
    order_time: datetime | None = None,
    promised_eta: datetime | None = None,
    status: OrderState = OrderState.READY_FOR_DISPATCH,
) -> Order:
    """Create a standard order with delivery location and consistent ETA."""
    if promised_eta is not None and order_time is None:
        # Distance ~0.16 km -> picking (2m) + travel (1m) = 3m duration
        base_time = promised_eta - timedelta(minutes=3)
        eta = promised_eta
    elif order_time is not None and promised_eta is None:
        base_time = order_time
        eta = base_time + timedelta(minutes=3)
    elif order_time is not None and promised_eta is not None:
        base_time = order_time
        eta = promised_eta
    else:
        base_time = datetime(2026, 11, 4, 10, 0)
        eta = base_time + timedelta(minutes=3)

    order = Order(
        order_id=order_id,
        customer_id=f"CUST-{order_id}",
        delivery_location=Coordinates(latitude=lat, longitude=lon),
        allocated_store_id=store_id,
        order_time=base_time,
        promised_eta=eta,
        status=status,
    )
    order.items.append(
        OrderItem(
            product_id="SKU-MILK",
            quantity=2,
            unit_price=Decimal("45.00"),
            fulfilled_quantity=2,
        )
    )
    return order


def make_test_rider(
    rider_id: str = "RIDER-01", status: RiderStatus = RiderStatus.AVAILABLE
) -> Rider:
    """Create a standard rider for testing."""
    return Rider(rider_id=rider_id, name="Test Rider", status=status)


class TestBatchCapacity:
    """Test BR-08 batch capacity constraint (maximum 3 orders per batch)."""

    def test_single_order_batch_creation(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        order = make_test_order("ORD-01")

        batch = service.create_batch("BATCH-01", store, rider, [order])
        assert len(batch.orders) == 1
        assert batch.orders[0].order_id == "ORD-01"
        assert batch.status == BatchStatus.CREATED

    def test_two_and_three_orders_batch_capacity_allowed(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01", lat=12.9720)
        o2 = make_test_order("ORD-02", lat=12.9730)
        o3 = make_test_order("ORD-03", lat=12.9740)

        service.add_order_to_batch(batch, o1, store)
        service.add_order_to_batch(batch, o2, store)
        service.add_order_to_batch(batch, o3, store)

        assert len(batch.orders) == 3

    def test_fourth_order_rejected_due_to_capacity(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01", lat=12.9720)
        o2 = make_test_order("ORD-02", lat=12.9730)
        o3 = make_test_order("ORD-03", lat=12.9740)
        o4 = make_test_order("ORD-04", lat=12.9750)

        service.add_order_to_batch(batch, o1, store)
        service.add_order_to_batch(batch, o2, store)
        service.add_order_to_batch(batch, o3, store)

        eval_res = service.evaluate_compatibility(batch, o4, store=store)
        assert eval_res.is_compatible is False
        assert "maximum capacity" in eval_res.rejection_reason.lower()

        with pytest.raises(ValueError, match="maximum capacity"):
            service.add_order_to_batch(batch, o4, store)

        assert len(batch.orders) == 3


class TestPairwiseProximity:
    """Test BR-08 pairwise geographic distance constraint (<= 1.00 km, unrounded Haversine)."""

    def test_pairwise_distance_within_one_km_accepted(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        # ~500m apart
        o1 = make_test_order("ORD-01", lat=12.9716, lon=77.5946)
        o2 = make_test_order("ORD-02", lat=12.9760, lon=77.5946)

        eval_res = service.evaluate_compatibility(batch, o1, store=store)
        assert eval_res.is_compatible is True

        service.add_order_to_batch(batch, o1, store)
        eval_res2 = service.evaluate_compatibility(batch, o2, store=store)
        assert eval_res2.is_compatible is True
        assert eval_res2.max_pairwise_distance_km <= 1.00

    def test_pairwise_distance_exact_boundary_inclusive(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01", lat=12.9716, lon=77.5946)
        service.add_order_to_batch(batch, o1, store)

        # Delta of 0.008993216059187304 degrees corresponds to <= 1.000000 km
        delta_lat_1km = 0.008993216059187304
        o2_exact = make_test_order(
            "ORD-02", lat=12.9716 + delta_lat_1km, lon=77.5946
        )

        dist = _unrounded_haversine(o1.delivery_location, o2_exact.delivery_location)
        assert dist <= 1.00

        eval_res = service.evaluate_compatibility(batch, o2_exact, store=store)
        assert eval_res.is_compatible is True

    def test_pairwise_distance_just_beyond_one_km_rejected(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01", lat=12.9716, lon=77.5946)
        service.add_order_to_batch(batch, o1, store)

        # Delta that exceeds 1.00 km (e.g. 1.0001 km)
        delta_lat_beyond = 0.008993216059187304 + 0.000001
        o2_beyond = make_test_order(
            "ORD-02", lat=12.9716 + delta_lat_beyond, lon=77.5946
        )

        dist = _unrounded_haversine(o1.delivery_location, o2_beyond.delivery_location)
        assert dist > 1.00

        eval_res = service.evaluate_compatibility(batch, o2_beyond, store=store)
        assert eval_res.is_compatible is False
        assert "exceeds 1.00 km limit" in eval_res.rejection_reason

        with pytest.raises(ValueError, match="exceeds 1.00 km limit"):
            service.add_order_to_batch(batch, o2_beyond, store)

    def test_pairwise_triangle_constraint_enforced_across_all_pairs(self) -> None:
        """Test 3-order batch where (A, B) <= 1km and (A, C) <= 1km, but (B, C) > 1km."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        # Center drop A at 12.9716
        # Drop B is ~700m North
        # Drop C is ~700m South
        # Distance B -> C is ~1400m (> 1km)
        o_center = make_test_order("ORD-A", lat=12.9716, lon=77.5946)
        o_north = make_test_order("ORD-B", lat=12.9716 + 0.0063, lon=77.5946)
        o_south = make_test_order("ORD-C", lat=12.9716 - 0.0063, lon=77.5946)

        assert _unrounded_haversine(o_center.delivery_location, o_north.delivery_location) <= 1.00
        assert _unrounded_haversine(o_center.delivery_location, o_south.delivery_location) <= 1.00
        assert _unrounded_haversine(o_north.delivery_location, o_south.delivery_location) > 1.00

        service.add_order_to_batch(batch, o_center, store)
        service.add_order_to_batch(batch, o_north, store)

        eval_res = service.evaluate_compatibility(batch, o_south, store=store)
        assert eval_res.is_compatible is False
        assert "exceeds 1.00 km limit" in eval_res.rejection_reason


class TestETACompatibility:
    """Test BR-08 promised ETA difference constraint (diff <= 4 minutes, inclusive)."""

    def test_identical_and_near_etas_accepted(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", promised_eta=t0 + timedelta(minutes=10))
        o2 = make_test_order("ORD-02", promised_eta=t0 + timedelta(minutes=12))

        service.add_order_to_batch(batch, o1, store)
        eval_res = service.evaluate_compatibility(batch, o2, store=store)
        assert eval_res.is_compatible is True
        assert eval_res.max_eta_diff_seconds == 120.0

    def test_exact_four_minute_boundary_inclusive(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", promised_eta=t0 + timedelta(minutes=10))
        o2 = make_test_order("ORD-02", promised_eta=t0 + timedelta(minutes=14))

        service.add_order_to_batch(batch, o1, store)
        eval_res = service.evaluate_compatibility(batch, o2, store=store)
        assert eval_res.is_compatible is True
        assert eval_res.max_eta_diff_seconds == 240.0

    def test_four_minutes_one_second_difference_rejected(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", promised_eta=t0 + timedelta(minutes=10))
        o2 = make_test_order(
            "ORD-02", promised_eta=t0 + timedelta(minutes=14, seconds=1)
        )

        service.add_order_to_batch(batch, o1, store)
        eval_res = service.evaluate_compatibility(batch, o2, store=store)
        assert eval_res.is_compatible is False
        assert "exceeds the 4-minute maximum threshold" in eval_res.rejection_reason

        with pytest.raises(ValueError, match="exceeds the 4-minute maximum threshold"):
            service.add_order_to_batch(batch, o2, store)

    def test_three_orders_eta_span_evaluation(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", promised_eta=t0 + timedelta(minutes=10))
        o2 = make_test_order("ORD-02", promised_eta=t0 + timedelta(minutes=12))
        o3_valid = make_test_order(
            "ORD-03A", promised_eta=t0 + timedelta(minutes=14)
        )
        o3_invalid = make_test_order(
            "ORD-03B", promised_eta=t0 + timedelta(minutes=15)
        )

        service.add_order_to_batch(batch, o1, store)
        service.add_order_to_batch(batch, o2, store)

        assert service.evaluate_compatibility(batch, o3_valid, store=store).is_compatible is True
        assert service.evaluate_compatibility(batch, o3_invalid, store=store).is_compatible is False


class TestDataValidationAndParity:
    """Test validation of inputs, missing timestamps, and timezone-awareness parity."""

    def test_missing_promised_eta_raises_value_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        order_missing_eta = make_test_order("ORD-01")
        order_missing_eta.promised_eta = None

        with pytest.raises(ValueError, match="has no promised_eta"):
            service.evaluate_compatibility(batch, order_missing_eta, store=store)

        with pytest.raises(ValueError, match="has no promised_eta"):
            service.add_order_to_batch(batch, order_missing_eta, store)

    def test_missing_delivery_location_raises_value_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        order_missing_loc = make_test_order("ORD-01")
        order_missing_loc.delivery_location = None

        with pytest.raises(ValueError, match="has no delivery_location"):
            service.evaluate_compatibility(batch, order_missing_loc, store=store)

    def test_timezone_awareness_mismatch_raises_type_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t_naive = datetime(2026, 11, 4, 10, 0)
        t_aware = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)

        o_naive = make_test_order("ORD-01", order_time=t_naive, promised_eta=t_naive + timedelta(minutes=10))
        service.add_order_to_batch(batch, o_naive, store)

        o_aware = make_test_order("ORD-02", order_time=t_aware, promised_eta=t_aware + timedelta(minutes=10))

        with pytest.raises(TypeError, match="Cannot compare.*timezone-aware.*timezone-naive"):
            service.evaluate_compatibility(batch, o_aware, store=store)


class TestDeterministicDropSequencing:
    """Test deterministic drop sequencing and documented tie-breaking rules."""

    def test_sequencing_by_store_distance(self) -> None:
        service = BatchingService()
        store = make_test_store()

        # Near, Mid, Far drops
        o_far = make_test_order("ORD-FAR", lat=12.9800)
        o_near = make_test_order("ORD-NEAR", lat=12.9720)
        o_mid = make_test_order("ORD-MID", lat=12.9750)

        sequenced = service.sequence_drops(store, [o_far, o_near, o_mid])
        assert [o.order_id for o in sequenced] == ["ORD-NEAR", "ORD-MID", "ORD-FAR"]

    def test_sequencing_tie_breaker_promised_eta(self) -> None:
        service = BatchingService()
        store = make_test_store()

        t0 = datetime(2026, 11, 4, 10, 0)
        # Identical location, different ETAs
        o_later = make_test_order("ORD-LATER", lat=12.9750, promised_eta=t0 + timedelta(minutes=12))
        o_earlier = make_test_order("ORD-EARLIER", lat=12.9750, promised_eta=t0 + timedelta(minutes=10))

        sequenced = service.sequence_drops(store, [o_later, o_earlier])
        assert [o.order_id for o in sequenced] == ["ORD-EARLIER", "ORD-LATER"]

    def test_sequencing_tie_breaker_order_id_lexicographical(self) -> None:
        service = BatchingService()
        store = make_test_store()

        t0 = datetime(2026, 11, 4, 10, 0)
        # Identical location and identical ETA
        o_b = make_test_order("ORD-B", lat=12.9750, promised_eta=t0 + timedelta(minutes=10))
        o_a = make_test_order("ORD-A", lat=12.9750, promised_eta=t0 + timedelta(minutes=10))

        sequenced = service.sequence_drops(store, [o_b, o_a])
        assert [o.order_id for o in sequenced] == ["ORD-A", "ORD-B"]


class TestETARecalculationAndRollback:
    """Test drop-order ETA adjustments via PickingService.recalculate_eta and rollback."""

    def test_drop_order_eta_penalties_applied(self) -> None:
        picking_svc = PickingService()
        batching_svc = BatchingService(picking_service=picking_svc)
        store = make_test_store()
        rider = make_test_rider()
        batch = batching_svc.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        # 3 orders located at increasing distances
        o1 = make_test_order("ORD-01", lat=12.9720, order_time=t0, promised_eta=t0 + timedelta(minutes=3))
        o2 = make_test_order("ORD-02", lat=12.9740, order_time=t0, promised_eta=t0 + timedelta(minutes=3))
        o3 = make_test_order("ORD-03", lat=12.9760, order_time=t0, promised_eta=t0 + timedelta(minutes=3))

        batching_svc.add_order_to_batch(batch, o1, store)
        batching_svc.add_order_to_batch(batch, o2, store)
        batching_svc.add_order_to_batch(batch, o3, store)

        # Drop 0 (o1): earlier_drops = 0 -> no extra drop delay (10:03)
        # Drop 1 (o2): earlier_drops = 1 -> +2 min (10:05)
        # Drop 2 (o3): earlier_drops = 2 -> +4 min (10:07)
        assert batch.orders[0].order_id == "ORD-01"
        assert batch.orders[1].order_id == "ORD-02"
        assert batch.orders[2].order_id == "ORD-03"
        assert batch.orders[0].promised_eta == t0 + timedelta(minutes=3)
        assert batch.orders[1].promised_eta == t0 + timedelta(minutes=5)
        assert batch.orders[2].promised_eta == t0 + timedelta(minutes=7)

    def test_rollback_restores_original_etas_on_rejection(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        orig_eta1 = t0 + timedelta(minutes=3)
        o1 = make_test_order("ORD-01", order_time=t0, promised_eta=orig_eta1)

        service.add_order_to_batch(batch, o1, store)

        # Incompatible candidate (distance > 1.00 km)
        o_far = make_test_order("ORD-FAR", lat=12.9950, order_time=t0, promised_eta=t0 + timedelta(minutes=3))

        with pytest.raises(ValueError):
            service.add_order_to_batch(batch, o_far, store)

        # Original order retains its prior ETA
        service.restore_original_etas(batch.orders)
        assert o1.promised_eta == orig_eta1


class TestStoreAndGlobalConstraints:
    """Test store allocation restriction and global single-batch order/rider uniqueness."""

    def test_different_store_orders_rejected(self) -> None:
        service = BatchingService()
        store1 = make_test_store("STORE-01")
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store1, rider)

        o_store2 = make_test_order("ORD-02", store_id="STORE-02")
        eval_res = service.evaluate_compatibility(batch, o_store2, store=store1)
        assert eval_res.is_compatible is False
        assert "does not match batch store" in eval_res.rejection_reason

        with pytest.raises(ValueError, match="does not match batch store"):
            service.add_order_to_batch(batch, o_store2, store1)

    def test_order_cannot_belong_to_multiple_active_batches(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider1 = make_test_rider("RIDER-01")
        rider2 = make_test_rider("RIDER-02")

        batch1 = service.create_batch("BATCH-01", store, rider1)
        batch2 = service.create_batch("BATCH-02", store, rider2)

        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch1, o1, store)

        eval_res = service.evaluate_compatibility(batch2, o1, store=store)
        assert eval_res.is_compatible is False
        assert "already assigned to active batch" in eval_res.rejection_reason

        with pytest.raises(ValueError, match="already assigned to active batch"):
            service.add_order_to_batch(batch2, o1, store)

    def test_adding_same_order_to_same_batch_is_idempotent(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch, o1, store)
        assert len(batch.orders) == 1

        service.add_order_to_batch(batch, o1, store)
        assert len(batch.orders) == 1

    def test_rider_uniqueness_cannot_assign_busy_or_multi_batch_rider(self) -> None:
        service = BatchingService()
        store = make_test_store()

        busy_rider = make_test_rider("RIDER-BUSY", status=RiderStatus.BUSY)
        with pytest.raises(ValueError, match="not available"):
            service.create_batch("BATCH-01", store, busy_rider)

        avail_rider = make_test_rider("RIDER-01")
        service.create_batch("BATCH-01", store, avail_rider)

        with pytest.raises(ValueError, match="already assigned to active batch"):
            service.create_batch("BATCH-02", store, avail_rider)


class TestDispatchTimingAndAtomicity:
    """Test immediate vs 3-minute waiting timer dispatch and atomic transitions."""

    def test_immediate_dispatch_for_full_batch_of_three_orders(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", lat=12.9720, order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", lat=12.9730, order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o3 = make_test_order("ORD-03", lat=12.9740, order_time=t0, status=OrderState.READY_FOR_DISPATCH)

        service.add_order_to_batch(batch, o1, store)
        service.add_order_to_batch(batch, o2, store)
        service.add_order_to_batch(batch, o3, store)

        # Immediate dispatch eligibility without waiting 3 minutes
        assert service.can_dispatch(batch, current_time=t0) is True

        res = service.dispatch_batch(batch, store, rider, dispatched_at=t0)
        assert res.is_dispatched is True
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY
        assert all(o.status == OrderState.DISPATCHED for o in batch.orders)

    def test_partial_batch_waits_three_minutes_from_first_ready(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        t_ready = datetime(2026, 11, 4, 10, 5)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t_ready)

        # Before 3 minutes (e.g. 2 minutes after ready)
        t_early = t_ready + timedelta(minutes=2)
        assert service.can_dispatch(batch, current_time=t_early) is False
        with pytest.raises(ValueError, match="before 3-minute waiting timer expires"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t_early)

        # At exactly 3 minutes (inclusive boundary)
        t_dispatch = t_ready + timedelta(minutes=3)
        assert service.can_dispatch(batch, current_time=t_dispatch) is True
        res = service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)
        assert res.is_dispatched is True
        assert batch.status == BatchStatus.DISPATCHED

    def test_atomic_dispatch_failure_leaves_no_partial_mutation(self) -> None:
        """If an order is not in READY_FOR_DISPATCH, dispatch fails without partial state changes."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2_not_ready = make_test_order("ORD-02", order_time=t0, status=OrderState.PICKING)

        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        batch.orders.append(o2_not_ready)  # manually append to bypass add_order validation

        t_dispatch = t0 + timedelta(minutes=5)
        with pytest.raises(InvalidOrderTransitionError):
            service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # State must remain unchanged
        assert batch.status == BatchStatus.CREATED
        assert rider.status == RiderStatus.AVAILABLE
        assert o1.status == OrderState.READY_FOR_DISPATCH
        assert o2_not_ready.status == OrderState.PICKING


class TestDeliveryCompletionAndCancellation:
    """Test delivery completion and batch cancellation workflows."""

    def test_complete_delivery_releases_rider_and_transitions_orders(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)
        assert rider.status == RiderStatus.BUSY

        t_delivered = t_dispatch + timedelta(minutes=7)
        res = service.complete_delivery(batch, rider, delivered_at=t_delivered)

        assert res.delivered_at == t_delivered
        assert batch.status == BatchStatus.DELIVERED
        assert rider.status == RiderStatus.AVAILABLE
        assert o1.status == OrderState.DELIVERED
        assert o1.actual_delivery_time == t_delivered

        # Order and rider are freed from active batches
        rider2 = make_test_rider("RIDER-02")
        batch2 = service.create_batch("BATCH-02", store, rider)
        assert batch2.rider_id == rider.rider_id

    def test_complete_delivery_fails_for_non_dispatched_batch(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        with pytest.raises(ValueError, match="Cannot complete delivery for batch.*status 'CREATED'"):
            service.complete_delivery(batch, rider, delivered_at=datetime.now())

    def test_cancel_batch_releases_rider_and_orders(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch, o1, store)

        service.cancel_batch(batch, rider)
        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE

        # Order can now be batched elsewhere
        batch2 = service.create_batch("BATCH-02", store, rider)
        service.add_order_to_batch(batch2, o1, store)
        assert len(batch2.orders) == 1


class TestProtocolAndEdgePreconditions:
    """Test protocol conformance, argument validations, and edge preconditions."""

    def test_protocol_conformance(self) -> None:
        service = BatchingService()
        assert isinstance(service, BatchingServiceInterface)

    def test_get_batch_found_and_not_found(self) -> None:
        service = BatchingService()
        assert service.get_batch("NONEXISTENT") is None
        store = make_test_store()
        rider = make_test_rider()
        b = service.create_batch("BATCH-X", store, rider)
        assert service.get_batch("BATCH-X") is b

    def test_create_batch_invalid_inputs(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()

        with pytest.raises(TypeError, match="batch_id must be a string"):
            service.create_batch(123, store, rider)  # type: ignore

        with pytest.raises(ValueError, match="batch_id must not be empty"):
            service.create_batch("   ", store, rider)

        with pytest.raises(TypeError, match="store must be a Store instance"):
            service.create_batch("B-1", "not_store", rider)  # type: ignore

        with pytest.raises(TypeError, match="rider must be a Rider instance"):
            service.create_batch("B-1", store, "not_rider")  # type: ignore

        with pytest.raises(TypeError, match="created_at must be a datetime"):
            service.create_batch("B-1", store, rider, created_at="2026-11-04")  # type: ignore

    def test_dispatch_precondition_failures(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        # Empty batch dispatch
        with pytest.raises(ValueError, match="Cannot dispatch empty batch"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Rider mismatch
        other_rider = make_test_rider("RIDER-OTHER")
        with pytest.raises(ValueError, match="Batch rider ID.*does not match provided rider"):
            service.dispatch_batch(batch, store, other_rider, dispatched_at=t0 + timedelta(minutes=5))

        # Store mismatch
        other_store = make_test_store("STORE-OTHER")
        with pytest.raises(ValueError, match="Batch store.*does not match provided store"):
            service.dispatch_batch(batch, other_store, rider, dispatched_at=t0 + timedelta(minutes=5))

        # Dispatched at earlier than created at
        with pytest.raises(ValueError, match="dispatched_at cannot be earlier than created_at"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t0 - timedelta(minutes=1))

        # Timezone mismatch
        t_aware = datetime(2026, 11, 4, 10, 5, tzinfo=timezone.utc)
        with pytest.raises(TypeError, match="Cannot compare.*timezone-aware.*timezone-naive"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t_aware)

    def test_complete_delivery_precondition_failures(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Rider mismatch
        other_rider = make_test_rider("RIDER-OTHER")
        with pytest.raises(ValueError, match="Batch rider ID.*does not match provided rider"):
            service.complete_delivery(batch, other_rider, delivered_at=t_dispatch + timedelta(minutes=5))

        # Delivered at earlier than dispatched at
        with pytest.raises(ValueError, match="delivered_at cannot be earlier than dispatched_at"):
            service.complete_delivery(batch, rider, delivered_at=t_dispatch - timedelta(minutes=1))

        # Timezone mismatch on delivery
        t_aware = datetime(2026, 11, 4, 10, 10, tzinfo=timezone.utc)
        with pytest.raises(TypeError, match="Cannot compare.*timezone-aware.*timezone-naive"):
            service.complete_delivery(batch, rider, delivered_at=t_aware)

    def test_cancel_terminal_batch_raises_value_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.dispatch_batch(batch, store, rider, dispatched_at=t0 + timedelta(minutes=3))
        service.complete_delivery(batch, rider, delivered_at=t0 + timedelta(minutes=10))

        with pytest.raises(ValueError, match="Cannot cancel batch.*with terminal status 'DELIVERED'"):
            service.cancel_batch(batch, rider)

    def test_mark_order_ready_helper(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.PICKING)
        batch.orders.append(o1)
        t_ready = t0 + timedelta(minutes=2)
        service.mark_order_ready(batch, o1, ready_at=t_ready)

        assert o1.status == OrderState.READY_FOR_DISPATCH
        assert service._batch_first_ready[batch.batch_id] == t_ready

    def test_scheduled_picking_slot_delay_preserved_in_batch(self) -> None:
        """Verify that when an order has a picking slot delay, recalculate_eta preserves it."""
        picking_svc = PickingService()
        batching_svc = BatchingService(picking_service=picking_svc)
        store = make_test_store()
        store.picking_capacity = 1
        rider = make_test_rider()

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", order_time=t0)
        o2 = make_test_order("ORD-02", order_time=t0)

        # Schedule orders with PickingService: o1 gets slot 10:00 (0 delay), o2 overflows to 10:10 (10 min delay)
        picking_svc.process_order_picking_and_eta(store, o1)
        picking_svc.process_order_picking_and_eta(store, o2)

        assert o1.promised_eta == t0 + timedelta(minutes=3)
        assert o2.promised_eta == t0 + timedelta(minutes=13)  # 10m slot delay + 3m

        # Form a batch with o2 as drop 0 (or drop 1)
        batch = batching_svc.create_batch("BATCH-01", store, rider, created_at=t0)
        batching_svc.add_order_to_batch(batch, o2, store)

        # Slot delay of 10 min is preserved
        assert batch.orders[0].promised_eta == t0 + timedelta(minutes=13)
        assert batch.orders[0].delayed_promise is True  # total duration 13 > 10 min


class TestAtomicBatchCreation:
    """Tests covering Issue 1: Atomic batch creation and state cleanup on error."""

    def test_atomic_batch_creation_failure_allows_rider_and_batch_reuse(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")

        # Create 4 orders (exceeds capacity of 3)
        o1 = make_test_order("O-1")
        o2 = make_test_order("O-2")
        o3 = make_test_order("O-3")
        o4 = make_test_order("O-4")

        with pytest.raises(ValueError, match="exceeds maximum capacity"):
            service.create_batch(
                "BATCH-FAIL", store, rider, initial_orders=[o1, o2, o3, o4]
            )

        # Verify no batch registered
        assert service.get_batch("BATCH-FAIL") is None
        assert "RIDER-01" not in service._rider_active_batches
        assert "BATCH-FAIL" not in service._batch_stores
        assert "O-1" not in service._order_active_batches
        assert rider.status == RiderStatus.AVAILABLE

        # Proves rider and batch_id can be reused immediately
        batch_retry = service.create_batch(
            "BATCH-FAIL", store, rider, initial_orders=[o1, o2]
        )
        assert batch_retry.batch_id == "BATCH-FAIL"
        assert len(batch_retry.orders) == 2
        assert service._rider_active_batches["RIDER-01"] == "BATCH-FAIL"

    def test_atomic_batch_creation_incompatible_order_cleans_up_all_state(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")

        # Order 1 is near, Order 2 is far (>1.00 km)
        o1 = make_test_order("O-NEAR", lat=12.9716)
        o2_far = make_test_order("O-FAR", lat=12.9950)

        t_orig1 = o1.promised_eta
        t_orig2 = o2_far.promised_eta

        with pytest.raises(ValueError, match="Initial order 'O-FAR' is incompatible"):
            service.create_batch(
                "BATCH-FAIL-2", store, rider, initial_orders=[o1, o2_far]
            )

        # Batch, rider, order mappings must not exist
        assert service.get_batch("BATCH-FAIL-2") is None
        assert "RIDER-01" not in service._rider_active_batches
        assert "O-NEAR" not in service._order_active_batches
        assert "O-FAR" not in service._order_active_batches

        # ETAs must be restored
        assert o1.promised_eta == t_orig1
        assert o2_far.promised_eta == t_orig2

        # Rider is available and can create a new batch
        assert rider.status == RiderStatus.AVAILABLE
        batch_new = service.create_batch(
            "BATCH-FAIL-2", store, rider, initial_orders=[o1]
        )
        assert len(batch_new.orders) == 1

    def test_atomic_batch_creation_duplicate_order_id_rejected(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")

        o1 = make_test_order("O-DUP")
        o2_same_id = make_test_order("O-DUP")

        with pytest.raises(ValueError, match="Duplicate order ID 'O-DUP'"):
            service.create_batch(
                "BATCH-DUP", store, rider, initial_orders=[o1, o2_same_id]
            )

        assert service.get_batch("BATCH-DUP") is None
        assert "RIDER-01" not in service._rider_active_batches


class TestDispatchTimerEnforcement:
    """Tests covering Issue 2: Enforcing dispatch timer boundaries."""

    def test_dispatch_partial_batch_missing_timestamp_rejected(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        # 1 order, ready for dispatch, but no ready_at timestamp recorded
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store)

        # can_dispatch is False because no first_ready timestamp is recorded
        assert service.can_dispatch(batch, current_time=t0 + timedelta(minutes=10)) is False

        # dispatch_batch raises ValueError specifically mentioning missing timestamp
        with pytest.raises(ValueError, match="without a recorded first-ready timestamp"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t0 + timedelta(minutes=10))

    def test_dispatch_partial_batch_at_two_minutes_fifty_nine_seconds_rejected(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        t_ready = datetime(2026, 11, 4, 10, 5)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t_ready)

        # Exactly 2 minutes 59 seconds after ready
        t_2m59s = t_ready + timedelta(minutes=2, seconds=59)

        assert service.can_dispatch(batch, current_time=t_2m59s) is False
        with pytest.raises(ValueError, match="before 3-minute waiting timer expires"):
            service.dispatch_batch(batch, store, rider, dispatched_at=t_2m59s)

    def test_dispatch_partial_batch_at_exactly_three_minutes_accepted(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        t_ready = datetime(2026, 11, 4, 10, 5)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t_ready)

        # Exactly 3 minutes (180 seconds) after ready
        t_3m = t_ready + timedelta(minutes=3)

        assert service.can_dispatch(batch, current_time=t_3m) is True
        res = service.dispatch_batch(batch, store, rider, dispatched_at=t_3m)
        assert res.is_dispatched is True
        assert batch.status == BatchStatus.DISPATCHED

    def test_dispatch_full_three_orders_batch_immediate_without_first_ready(self) -> None:
        """A full 3-order batch dispatches immediately without requiring first_ready."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("O-1", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("O-2", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o3 = make_test_order("O-3", order_time=t0, status=OrderState.READY_FOR_DISPATCH)

        service.add_order_to_batch(batch, o1, store)  # no ready_at
        service.add_order_to_batch(batch, o2, store)
        service.add_order_to_batch(batch, o3, store)

        # No first_ready recorded
        assert service._batch_first_ready.get(batch.batch_id) is None

        # Eligible immediately at t0
        assert service.can_dispatch(batch, current_time=t0) is True
        res = service.dispatch_batch(batch, store, rider, dispatched_at=t0)
        assert res.is_dispatched is True
        assert batch.status == BatchStatus.DISPATCHED


class TestFirstReadyTimestampAccuracy:
    """Tests covering Issue 3: Correct handling of first-ready timestamp."""

    def test_never_uses_order_time_for_first_ready(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store)  # ready_at is None

        # Proves order.order_time was NOT used as first_ready
        assert batch.batch_id not in service._batch_first_ready

    def test_explicit_ready_at_is_recorded(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        t_ready = datetime(2026, 11, 4, 10, 4)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t_ready)

        assert service._batch_first_ready[batch.batch_id] == t_ready

    def test_mark_order_ready_records_transition_time(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        t_transition = datetime(2026, 11, 4, 10, 3)
        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.PICKING)
        batch.orders.append(o1)
        service.mark_order_ready(batch, o1, ready_at=t_transition)

        assert o1.status == OrderState.READY_FOR_DISPATCH
        assert service._batch_first_ready[batch.batch_id] == t_transition

    def test_preserve_first_orders_timestamp_when_subsequent_orders_join(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t0 = datetime(2026, 11, 4, 10, 0)
        t_ready1 = datetime(2026, 11, 4, 10, 2)
        t_ready2 = datetime(2026, 11, 4, 10, 5)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)

        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)

        service.add_order_to_batch(batch, o1, store, ready_at=t_ready1)
        assert service._batch_first_ready[batch.batch_id] == t_ready1

        # Second order arrives with a later ready_at; first order's timestamp MUST be preserved
        service.add_order_to_batch(batch, o2, store, ready_at=t_ready2)
        assert service._batch_first_ready[batch.batch_id] == t_ready1

    def test_ready_at_timezone_mismatch_raises_type_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        t_naive = datetime(2026, 11, 4, 10, 0)
        t_aware = datetime(2026, 11, 4, 10, 2, tzinfo=timezone.utc)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t_naive)
        o1 = make_test_order("ORD-01", order_time=t_naive)

        with pytest.raises(TypeError, match="Cannot compare.*timezone-aware.*timezone-naive"):
            service.add_order_to_batch(batch, o1, store, ready_at=t_aware)


class TestSafeCancellation:
    """Tests covering Issue 4: Safe cancellation with rider validation."""

    def test_cancel_batch_rejects_mismatched_rider_without_modifying_state(self) -> None:
        service = BatchingService()
        store = make_test_store()
        assigned_rider = make_test_rider("RIDER-ASSIGNED")
        wrong_rider = make_test_rider("RIDER-WRONG")

        batch = service.create_batch("BATCH-01", store, assigned_rider)
        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch, o1, store)

        with pytest.raises(ValueError, match="does not match batch assigned rider"):
            service.cancel_batch(batch, wrong_rider)

        # Verify state is untouched
        assert batch.status == BatchStatus.CREATED
        assert service._rider_active_batches["RIDER-ASSIGNED"] == "BATCH-01"
        assert "RIDER-WRONG" not in service._rider_active_batches
        assert "ORD-01" in service._order_active_batches

    def test_cancel_batch_created_status_releases_assigned_rider_and_orders(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")

        batch = service.create_batch("BATCH-01", store, rider)
        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch, o1, store)

        service.cancel_batch(batch, rider)

        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE
        assert "RIDER-01" not in service._rider_active_batches
        assert "ORD-01" not in service._order_active_batches

    def test_cancel_batch_dispatched_status_releases_busy_rider(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Dispatch batch: rider becomes BUSY
        service.dispatch_batch(batch, store, rider, dispatched_at=t0 + timedelta(minutes=3))
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY

        # Cancel the dispatched batch
        service.cancel_batch(batch, rider)

        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE  # Released!
        assert "RIDER-01" not in service._rider_active_batches
        assert "ORD-01" not in service._order_active_batches

    def test_cancel_dispatched_batch_without_rider_argument_releases_rider(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Dispatch batch: rider becomes BUSY
        service.dispatch_batch(batch, store, rider, dispatched_at=t0 + timedelta(minutes=3))
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY

        # Cancel without passing rider argument
        service.cancel_batch(batch)

        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE  # Tracked rider was found and released
        assert "RIDER-01" not in service._rider_active_batches
        assert "ORD-01" not in service._order_active_batches
        assert "BATCH-01" not in service._assigned_riders

    def test_cancel_dispatched_batch_untracked_without_rider_raises_error(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.dispatch_batch(batch, store, rider, dispatched_at=t0 + timedelta(minutes=3))

        # Clear tracked riders to simulate untracked dispatched batch
        service._assigned_riders.clear()

        with pytest.raises(
            ValueError,
            match="Assigned rider is required to cancel DISPATCHED batch 'BATCH-01'",
        ):
            service.cancel_batch(batch)

        # Batch remains DISPATCHED and state is not mutated
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY
        assert service._rider_active_batches["RIDER-01"] == "BATCH-01"
        assert service._order_active_batches["ORD-01"] == "BATCH-01"

    def test_cancel_created_batch_without_rider_argument_releases_rider(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")

        batch = service.create_batch("BATCH-01", store, rider)
        o1 = make_test_order("ORD-01")
        service.add_order_to_batch(batch, o1, store)

        service.cancel_batch(batch)

        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE
        assert "RIDER-01" not in service._rider_active_batches
        assert "ORD-01" not in service._order_active_batches
        assert "BATCH-01" not in service._assigned_riders


class TestETASnapshotCleanup:
    """Tests covering Issue 5: Cleaning up newly created ETA snapshots on failure."""

    def test_eta_snapshot_cleanup_on_recalculate_failure(self, monkeypatch) -> None:
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider()
        batch = service.create_batch("BATCH-01", store, rider)

        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", order_time=t0, promised_eta=t0 + timedelta(minutes=3))
        service.add_order_to_batch(batch, o1, store)

        # ORD-01 is recorded in _original_etas
        assert "ORD-01" in service._original_etas
        orig_o1_snapshot = service._original_etas["ORD-01"]

        o2 = make_test_order("ORD-02", order_time=t0, promised_eta=t0 + timedelta(minutes=3))
        orig_o2_eta = o2.promised_eta

        # Monkeypatch recalculate_eta on PickingService to fail on drop 1
        def fail_recalculate(store, order, earlier_drops, **kwargs):
            if order.order_id == "ORD-02":
                raise RuntimeError("Simulated recalculation failure")
            return service._picking_service.calculate_eta(store, order, earlier_drops)

        monkeypatch.setattr(service._picking_service, "recalculate_eta", fail_recalculate)

        with pytest.raises(RuntimeError, match="Simulated recalculation failure"):
            service.add_order_to_batch(batch, o2, store)

        # ORD-02 was NOT added
        assert len(batch.orders) == 1
        assert "ORD-02" not in service._order_active_batches

        # ORD-02 snapshot was CLEANED UP from _original_etas
        assert "ORD-02" not in service._original_etas

        # Prior snapshot for ORD-01 was PRESERVED
        assert service._original_etas["ORD-01"] == orig_o1_snapshot

        # o2's promised_eta was restored
        assert o2.promised_eta == orig_o2_eta


class TestRiderObjectIdentityValidation:
    """Tests covering rider object identity validation across dispatch, delivery, and cancellation."""

    def test_dispatch_batch_rejects_different_rider_object_with_same_id(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider_a = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider_a, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Create Rider object B with the same rider_id
        rider_b = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        assert rider_b is not rider_a

        with pytest.raises(
            ValueError,
            match="Provided rider object does not match assigned rider object for batch 'BATCH-01'",
        ):
            service.dispatch_batch(
                batch, store, rider_b, dispatched_at=t0 + timedelta(minutes=3)
            )

        # Confirm A remains AVAILABLE
        assert rider_a.status == RiderStatus.AVAILABLE
        # Confirm B remains AVAILABLE
        assert rider_b.status == RiderStatus.AVAILABLE
        # Confirm the batch remains CREATED
        assert batch.status == BatchStatus.CREATED
        assert batch.dispatched_at is None
        # Confirm all orders remain READY_FOR_DISPATCH
        assert o1.status == OrderState.READY_FOR_DISPATCH
        # Confirm _assigned_riders still points to A
        assert service._assigned_riders["BATCH-01"] is rider_a
        assert service._rider_active_batches["RIDER-01"] == "BATCH-01"
        assert service._order_active_batches["ORD-01"] == "BATCH-01"

        # Also verify normal operations using original Rider A succeed
        res = service.dispatch_batch(
            batch, store, rider_a, dispatched_at=t0 + timedelta(minutes=3)
        )
        assert res.is_dispatched is True
        assert batch.status == BatchStatus.DISPATCHED
        assert rider_a.status == RiderStatus.BUSY

    def test_complete_delivery_rejects_different_rider_object_with_same_id(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider_a = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider_a, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.dispatch_batch(
            batch, store, rider_a, dispatched_at=t0 + timedelta(minutes=3)
        )
        assert batch.status == BatchStatus.DISPATCHED
        assert rider_a.status == RiderStatus.BUSY

        # Create Rider object B with the same rider_id (status AVAILABLE)
        rider_b = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        assert rider_b is not rider_a

        with pytest.raises(
            ValueError,
            match="Provided rider object does not match assigned rider object for batch 'BATCH-01'",
        ):
            service.complete_delivery(
                batch, rider_b, delivered_at=t0 + timedelta(minutes=10)
            )

        # Confirm A remains BUSY
        assert rider_a.status == RiderStatus.BUSY
        # Confirm B remains AVAILABLE
        assert rider_b.status == RiderStatus.AVAILABLE
        # Confirm the batch remains DISPATCHED
        assert batch.status == BatchStatus.DISPATCHED
        # Confirm orders remain DISPATCHED
        assert o1.status == OrderState.DISPATCHED
        assert o1.actual_delivery_time is None
        # Confirm all active mappings remain intact
        assert service._rider_active_batches["RIDER-01"] == "BATCH-01"
        assert service._assigned_riders["BATCH-01"] is rider_a
        assert service._order_active_batches["ORD-01"] == "BATCH-01"

        # Also verify normal operations using original Rider A succeed
        res = service.complete_delivery(
            batch, rider_a, delivered_at=t0 + timedelta(minutes=10)
        )
        assert batch.status == BatchStatus.DELIVERED
        assert rider_a.status == RiderStatus.AVAILABLE
        assert o1.status == OrderState.DELIVERED
        assert "BATCH-01" not in service._assigned_riders

    def test_cancel_created_batch_rejects_different_rider_object_with_same_id(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider_a = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider_a, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0)
        service.add_order_to_batch(batch, o1, store)

        # Create Rider object B with the same rider_id
        rider_b = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        assert rider_b is not rider_a

        with pytest.raises(
            ValueError,
            match="Provided rider object does not match assigned rider object for batch 'BATCH-01'",
        ):
            service.cancel_batch(batch, rider_b)

        # Confirm A remains AVAILABLE
        assert rider_a.status == RiderStatus.AVAILABLE
        # Confirm B remains AVAILABLE
        assert rider_b.status == RiderStatus.AVAILABLE
        # Confirm the batch remains CREATED
        assert batch.status == BatchStatus.CREATED
        # Confirm active mappings remain intact
        assert service._rider_active_batches["RIDER-01"] == "BATCH-01"
        assert service._assigned_riders["BATCH-01"] is rider_a
        assert service._order_active_batches["ORD-01"] == "BATCH-01"

        # Also verify normal operations using original Rider A succeed
        service.cancel_batch(batch, rider_a)
        assert batch.status == BatchStatus.CANCELLED
        assert rider_a.status == RiderStatus.AVAILABLE
        assert "RIDER-01" not in service._rider_active_batches
        assert "BATCH-01" not in service._assigned_riders

    def test_cancel_dispatched_batch_rejects_different_rider_object_with_same_id(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider_orig = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider_orig, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.dispatch_batch(
            batch, store, rider_orig, dispatched_at=t0 + timedelta(minutes=3)
        )

        rider_clone = make_test_rider("RIDER-01", status=RiderStatus.BUSY)
        assert rider_clone is not rider_orig

        with pytest.raises(
            ValueError,
            match="Provided rider object does not match assigned rider object for batch 'BATCH-01'",
        ):
            service.cancel_batch(batch, rider_clone)

        # Verify state is completely untouched
        assert batch.status == BatchStatus.DISPATCHED
        assert rider_orig.status == RiderStatus.BUSY
        assert rider_clone.status == RiderStatus.BUSY
        assert service._rider_active_batches["RIDER-01"] == "BATCH-01"
        assert service._assigned_riders["BATCH-01"] is rider_orig
        assert service._order_active_batches["ORD-01"] == "BATCH-01"

        # Also verify normal operations using original Rider A succeed
        service.cancel_batch(batch, rider_orig)
        assert batch.status == BatchStatus.CANCELLED
        assert rider_orig.status == RiderStatus.AVAILABLE
        assert "RIDER-01" not in service._rider_active_batches
        assert "BATCH-01" not in service._assigned_riders

    def test_untracked_cases_preserve_support_subject_to_existing_rules(self) -> None:
        service = BatchingService()
        store = make_test_store()
        rider_a = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = service.create_batch("BATCH-01", store, rider_a, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Clear tracked rider to simulate absence of tracking entry
        service._assigned_riders.clear()

        # 1. Dispatch with matching rider_id when untracked succeeds and tracks rider
        rider_dispatch = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        service.dispatch_batch(
            batch, store, rider_dispatch, dispatched_at=t0 + timedelta(minutes=3)
        )
        assert batch.status == BatchStatus.DISPATCHED
        assert rider_dispatch.status == RiderStatus.BUSY
        assert service._assigned_riders["BATCH-01"] is rider_dispatch

        # Clear tracking entry again
        service._assigned_riders.clear()

        # 2. Complete delivery with matching rider_id when untracked succeeds
        rider_complete = make_test_rider("RIDER-01", status=RiderStatus.BUSY)
        service.complete_delivery(
            batch, rider_complete, delivered_at=t0 + timedelta(minutes=10)
        )
        assert batch.status == BatchStatus.DELIVERED
        assert rider_complete.status == RiderStatus.AVAILABLE

        # 3. Cancel created batch with matching rider_id when untracked succeeds
        batch2 = service.create_batch("BATCH-02", store, rider_a, created_at=t0)
        o2 = make_test_order("ORD-02", order_time=t0)
        service.add_order_to_batch(batch2, o2, store)
        service._assigned_riders.clear()

        rider_cancel = make_test_rider("RIDER-01", status=RiderStatus.AVAILABLE)
        service.cancel_batch(batch2, rider_cancel)
        assert batch2.status == BatchStatus.CANCELLED
        assert rider_cancel.status == RiderStatus.AVAILABLE


class TestProgressiveOrderDelivery:
    """Tests covering progressive individual-order delivery and post-dispatch cancellation (Stage 2)."""

    def test_two_order_batch_progressive_delivery_keeps_rider_busy_until_last_order(self) -> None:
        """Delivery of first order keeps rider BUSY; delivery of second releases rider."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)
        assert rider.status == RiderStatus.BUSY
        assert batch.status == BatchStatus.DISPATCHED

        # Deliver Drop 1 at t0 + 6 min
        t_del1 = t0 + timedelta(minutes=6)
        res1 = service.complete_order_delivery(batch, o1, rider, delivered_at=t_del1)

        assert res1.is_batch_complete is False
        assert res1.remaining_order_ids == ["ORD-02"]
        assert res1.order_id == "ORD-01"
        assert res1.delivered_at == t_del1
        assert o1.status == OrderState.DELIVERED
        assert o1.actual_delivery_time == t_del1
        assert o2.status == OrderState.DISPATCHED
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY
        assert service._assigned_riders.get("BATCH-01") is rider

        # Deliver Drop 2 at t0 + 9 min
        t_del2 = t0 + timedelta(minutes=9)
        res2 = service.complete_order_delivery(batch, o2, rider, delivered_at=t_del2)

        assert res2.is_batch_complete is True
        assert res2.remaining_order_ids == []
        assert res2.order_id == "ORD-02"
        assert res2.delivered_at == t_del2
        assert o2.status == OrderState.DELIVERED
        assert o2.actual_delivery_time == t_del2
        assert batch.status == BatchStatus.DELIVERED
        assert rider.status == RiderStatus.AVAILABLE
        assert "BATCH-01" not in service._assigned_riders
        assert rider.rider_id not in service._rider_active_batches

        # Rider can now be assigned to a new batch immediately
        batch_new = service.create_batch("BATCH-02", store, rider, created_at=t_del2)
        assert batch_new.rider_id == rider.rider_id

    def test_three_order_batch_progressive_delivery_three_distinct_timestamps(self) -> None:
        """3-order batch delivered across 3 distinct timestamps; each order retains its delivery time."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o3 = make_test_order("ORD-03", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)
        service.add_order_to_batch(batch, o3, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Drop 1
        t_d1 = t0 + timedelta(minutes=5)
        res1 = service.complete_order_delivery(batch, o1, rider, delivered_at=t_d1)
        assert res1.is_batch_complete is False
        assert set(res1.remaining_order_ids) == {"ORD-02", "ORD-03"}
        assert rider.status == RiderStatus.BUSY

        # Drop 2
        t_d2 = t0 + timedelta(minutes=8)
        res2 = service.complete_order_delivery(batch, o2, rider, delivered_at=t_d2)
        assert res2.is_batch_complete is False
        assert res2.remaining_order_ids == ["ORD-03"]
        assert rider.status == RiderStatus.BUSY

        # Drop 3
        t_d3 = t0 + timedelta(minutes=11)
        res3 = service.complete_order_delivery(batch, o3, rider, delivered_at=t_d3)
        assert res3.is_batch_complete is True
        assert res3.remaining_order_ids == []
        assert batch.status == BatchStatus.DELIVERED
        assert rider.status == RiderStatus.AVAILABLE

        # Verify all individual delivery timestamps
        assert o1.actual_delivery_time == t_d1
        assert o2.actual_delivery_time == t_d2
        assert o3.actual_delivery_time == t_d3

    def test_progressive_delivery_with_post_dispatch_cancellation(self) -> None:
        """Order cancelled post-dispatch does not prematurely free rider; rider freed after final delivery."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Cancel ORD-02 after dispatch
        cancel_res = service.cancel_order_in_batch(batch, o2, reason="Customer cancelled")
        assert cancel_res.order_id == "ORD-02"
        assert cancel_res.reason == "Customer cancelled"
        assert cancel_res.batch_status == BatchStatus.DISPATCHED
        assert cancel_res.rider_released is False
        assert o2.status == OrderState.CANCELLED

        # Rider is STILL busy delivering ORD-01!
        assert rider.status == RiderStatus.BUSY
        assert batch.status == BatchStatus.DISPATCHED

        # Deliver ORD-01
        t_d1 = t0 + timedelta(minutes=7)
        res = service.complete_order_delivery(batch, o1, rider, delivered_at=t_d1)
        assert res.is_batch_complete is True
        assert res.remaining_order_ids == []
        assert batch.status == BatchStatus.DELIVERED
        assert rider.status == RiderStatus.AVAILABLE

    def test_post_dispatch_cancellation_of_all_orders_cancels_batch(self) -> None:
        """When all orders in dispatched batch are cancelled, batch cancels and rider is released."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Cancel first order
        res1 = service.cancel_order_in_batch(batch, o1, reason="Shortage")
        assert res1.rider_released is False
        assert rider.status == RiderStatus.BUSY
        assert batch.status == BatchStatus.DISPATCHED

        # Cancel second order
        res2 = service.cancel_order_in_batch(batch, o2, reason="Customer request")
        assert res2.rider_released is True
        assert res2.batch_status == BatchStatus.CANCELLED
        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE

    def test_complete_order_delivery_validation_errors(self) -> None:
        """Validation errors in complete_order_delivery raise appropriate exceptions."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # 1. Non-dispatched batch
        with pytest.raises(ValueError, match="Cannot complete delivery for batch.*status 'CREATED'"):
            service.complete_order_delivery(batch, o1, rider, delivered_at=t0 + timedelta(minutes=5))

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # 2. Wrong rider ID
        other_rider = make_test_rider("RIDER-99")
        with pytest.raises(ValueError, match="Batch rider ID.*does not match provided rider"):
            service.complete_order_delivery(batch, o1, other_rider, delivered_at=t_dispatch + timedelta(minutes=5))

        # 3. Same ID, different rider object
        diff_rider_obj = make_test_rider("RIDER-01", status=RiderStatus.BUSY)
        with pytest.raises(ValueError, match="Provided rider object does not match assigned rider object"):
            service.complete_order_delivery(batch, o1, diff_rider_obj, delivered_at=t_dispatch + timedelta(minutes=5))

        # 4. Order not in batch
        o_external = make_test_order("ORD-EXT", order_time=t0)
        with pytest.raises(ValueError, match="Order 'ORD-EXT' is not part of batch"):
            service.complete_order_delivery(batch, o_external, rider, delivered_at=t_dispatch + timedelta(minutes=5))

        # 5. Same ID, different order object
        diff_o1_obj = make_test_order("ORD-01", order_time=t0, status=OrderState.DISPATCHED)
        with pytest.raises(ValueError, match="Provided order object does not match the order object in batch"):
            service.complete_order_delivery(batch, diff_o1_obj, rider, delivered_at=t_dispatch + timedelta(minutes=5))

        # 6. Delivered_at earlier than dispatched_at
        with pytest.raises(ValueError, match="delivered_at cannot be earlier than dispatched_at"):
            service.complete_order_delivery(batch, o1, rider, delivered_at=t_dispatch - timedelta(minutes=1))

        # 7. Timezone mismatch
        t_aware = datetime(2026, 11, 4, 18, 10, tzinfo=timezone.utc)
        with pytest.raises(TypeError, match="Cannot compare"):
            service.complete_order_delivery(batch, o1, rider, delivered_at=t_aware)

        # 8. Invalid argument types
        with pytest.raises(TypeError, match="batch must be a Batch"):
            service.complete_order_delivery("not-a-batch", o1, rider, delivered_at=t_dispatch + timedelta(minutes=5))  # type: ignore

        with pytest.raises(TypeError, match="order must be an Order"):
            service.complete_order_delivery(batch, "not-an-order", rider, delivered_at=t_dispatch + timedelta(minutes=5))  # type: ignore

        with pytest.raises(TypeError, match="rider must be a Rider"):
            service.complete_order_delivery(batch, o1, "not-a-rider", delivered_at=t_dispatch + timedelta(minutes=5))  # type: ignore

        with pytest.raises(TypeError, match="delivered_at must be a datetime"):
            service.complete_order_delivery(batch, o1, rider, delivered_at="2026-11-04 18:10:00")  # type: ignore

    def test_pre_dispatch_cancellation_lifecycle_and_eta_restoration(self) -> None:
        """Pre-dispatch cancellation transitions order to CANCELLED, restores ETA, and is never left active."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        assert len(batch.orders) == 2
        res2 = service.cancel_order_in_batch(batch, o2, reason="Customer cancelled before dispatch")

        # 1. Order is transitioned to CANCELLED (not left in READY_FOR_DISPATCH or another active state)
        assert o2.status == OrderState.CANCELLED
        assert o2.order_id not in service._order_active_batches
        assert o2 not in batch.orders
        assert len(batch.orders) == 1
        assert res2.batch_status == BatchStatus.CREATED
        assert res2.rider_released is False
        assert res2.reason == "Customer cancelled before dispatch"

        # 2. Cancelling the final order cancels the CREATED batch and releases rider
        res1 = service.cancel_order_in_batch(batch, o1, reason="Cancelled remaining order")
        assert o1.status == OrderState.CANCELLED
        assert o1.order_id not in service._order_active_batches
        assert len(batch.orders) == 0
        assert res1.batch_status == BatchStatus.CANCELLED
        assert res1.rider_released is True
        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE

    def test_failed_cancellation_preserves_all_tracking_mappings(self) -> None:
        """A failed cancellation aborts before mutation and preserves all batch/order/rider mappings."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Snapshot tracking state
        orig_active_orders = dict(service._order_active_batches)
        orig_assigned_riders = dict(service._assigned_riders)
        orig_rider_active = dict(service._rider_active_batches)

        # 1. Attempt to cancel an order not in batch
        o_external = make_test_order("ORD-EXT", order_time=t0)
        with pytest.raises(ValueError, match="is not part of batch"):
            service.cancel_order_in_batch(batch, o_external)

        assert service._order_active_batches == orig_active_orders
        assert service._assigned_riders == orig_assigned_riders
        assert service._rider_active_batches == orig_rider_active
        assert len(batch.orders) == 2

        # 2. Attempt to cancel with different object identity
        diff_o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.DISPATCHED)
        with pytest.raises(ValueError, match="does not match the order object in batch"):
            service.cancel_order_in_batch(batch, diff_o1)

        assert service._order_active_batches == orig_active_orders
        assert service._assigned_riders == orig_assigned_riders
        assert service._rider_active_batches == orig_rider_active
        assert o1.status == OrderState.DISPATCHED

        # 3. Deliver o1, then attempt to cancel it
        t_d1 = t_dispatch + timedelta(minutes=4)
        service.complete_order_delivery(batch, o1, rider, delivered_at=t_d1)
        assert o1.status == OrderState.DELIVERED

        active_before_cancel = dict(service._order_active_batches)
        with pytest.raises(ValueError, match="Cannot cancel order.*terminal status 'DELIVERED'"):
            service.cancel_order_in_batch(batch, o1)

        assert service._order_active_batches == active_before_cancel
        assert o1.status == OrderState.DELIVERED
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY

    def test_repeated_cancellation_is_idempotent(self) -> None:
        """Calling cancel_order_in_batch repeatedly on the same order returns the original cached result."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        # Pre-dispatch cancellation
        res1 = service.cancel_order_in_batch(batch, o1, reason="Customer cancelled")
        assert res1.order_id == "ORD-01"
        assert o1.status == OrderState.CANCELLED
        assert res1.rider_released is False
        assert res1.reason == "Customer cancelled"

        # Repeated cancellation of o1 returns exact original cached result
        res1_dup = service.cancel_order_in_batch(batch, o1, reason="Repeated cancellation")
        assert res1_dup is res1
        assert res1_dup.order_id == "ORD-01"
        assert res1_dup.rider_released is False
        assert res1_dup.reason == "Customer cancelled"
        assert len(batch.orders) == 1
        assert batch.orders[0].order_id == "ORD-02"

        # Dispatch and post-dispatch repeated cancellation
        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)
        res2 = service.cancel_order_in_batch(batch, o2, reason="Dispatched cancellation")
        assert res2.order_id == "ORD-02"
        assert res2.rider_released is True
        assert res2.reason == "Dispatched cancellation"
        assert batch.status == BatchStatus.CANCELLED

        # Repeated post-dispatch cancellation returns exact original cached result (preserving rider_released=True)
        res2_dup = service.cancel_order_in_batch(batch, o2, reason="Duplicate dispatched cancellation")
        assert res2_dup is res2
        assert res2_dup.order_id == "ORD-02"
        assert res2_dup.rider_released is True
        assert res2_dup.reason == "Dispatched cancellation"

    def test_idempotent_cancellation_identity_validation_and_result_accuracy(self) -> None:
        """Repeated cancellation validates batch/order identity and preserves accurate rider_released status."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        # Single order in CREATED batch: cancellation transitions batch to CANCELLED and releases rider
        res1 = service.cancel_order_in_batch(batch, o1, reason="Single order pre-dispatch cancel")
        assert res1.rider_released is True
        assert res1.batch_status == BatchStatus.CANCELLED
        assert res1.reason == "Single order pre-dispatch cancel"

        # 1. Repeated cancellation of the exact order object returns the cached result with rider_released=True
        res1_dup = service.cancel_order_in_batch(batch, o1, reason="Duplicate attempt")
        assert res1_dup is res1
        assert res1_dup.rider_released is True
        assert res1_dup.reason == "Single order pre-dispatch cancel"

        # 2. A different Order object with the same order_id must raise ValueError, not receive cached result
        diff_o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.CANCELLED)
        assert diff_o1 is not o1
        with pytest.raises(
            ValueError,
            match="Provided order object does not match the order object in batch 'BATCH-01'",
        ):
            service.cancel_order_in_batch(batch, diff_o1)

        # 3. A different Batch object with the same batch_id must raise ValueError
        diff_batch = Batch(
            batch_id="BATCH-01",
            rider_id=rider.rider_id,
            created_at=t0,
            orders=[],
            status=BatchStatus.CANCELLED,
        )
        assert diff_batch is not batch
        with pytest.raises(
            ValueError,
            match="Provided batch object does not match",
        ):
            service.cancel_order_in_batch(diff_batch, o1)

        # 4. Post-dispatch identity validation on idempotency
        batch2 = service.create_batch("BATCH-02", store, rider, created_at=t0)
        o2_a = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o3_a = make_test_order("ORD-03", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch2, o2_a, store, ready_at=t0)
        service.add_order_to_batch(batch2, o3_a, store, ready_at=t0)
        service.dispatch_batch(batch2, store, rider, dispatched_at=t0 + timedelta(minutes=3))

        # Cancel o2_a post-dispatch (batch remains DISPATCHED, rider remains BUSY)
        res_post = service.cancel_order_in_batch(batch2, o2_a, reason="Post-dispatch cancel")
        assert res_post.rider_released is False

        # Duplicate with same object returns exact result
        res_post_dup = service.cancel_order_in_batch(batch2, o2_a, reason="Another reason")
        assert res_post_dup is res_post
        assert res_post_dup.rider_released is False
        assert res_post_dup.reason == "Post-dispatch cancel"

        # Duplicate with different Order object for ORD-02 raises ValueError
        diff_o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.CANCELLED)
        with pytest.raises(
            ValueError,
            match="Provided order object does not match the order object in batch 'BATCH-02'",
        ):
            service.cancel_order_in_batch(batch2, diff_o2)

    def test_unsupported_batch_state_rejects_cancellation_without_mutation(self) -> None:
        """Attempting to cancel an order in a DELIVERED or CANCELLED batch is rejected with ValueError."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)
        service.complete_order_delivery(batch, o1, rider, delivered_at=t_dispatch + timedelta(minutes=5))
        assert batch.status == BatchStatus.DELIVERED

        # Batch is DELIVERED: cancellation must be rejected
        with pytest.raises(ValueError, match="Cannot cancel order in batch.*terminal status 'DELIVERED'"):
            service.cancel_order_in_batch(batch, o1)

        # Batch is CANCELLED: cancellation must be rejected
        batch2 = service.create_batch("BATCH-02", store, rider, created_at=t0)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch2, o2, store, ready_at=t0)
        service.cancel_batch(batch2, rider)
        assert batch2.status == BatchStatus.CANCELLED

        with pytest.raises(ValueError, match="Cannot cancel order in batch.*terminal status 'CANCELLED'"):
            service.cancel_order_in_batch(batch2, o2)

    def test_mixed_progressive_and_batch_delivery(self) -> None:
        """Calling complete_order_delivery for drop 1 and complete_delivery for remaining orders succeeds."""
        service = BatchingService()
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        t0 = datetime(2026, 11, 4, 18, 0)

        batch = service.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        service.add_order_to_batch(batch, o1, store, ready_at=t0)
        service.add_order_to_batch(batch, o2, store, ready_at=t0)

        t_dispatch = t0 + timedelta(minutes=3)
        service.dispatch_batch(batch, store, rider, dispatched_at=t_dispatch)

        # Progressively deliver O1
        t_d1 = t0 + timedelta(minutes=6)
        service.complete_order_delivery(batch, o1, rider, delivered_at=t_d1)
        assert o1.status == OrderState.DELIVERED
        assert o1.actual_delivery_time == t_d1

        # Deliver remainder with complete_delivery
        t_d2 = t0 + timedelta(minutes=10)
        res = service.complete_delivery(batch, rider, delivered_at=t_d2)
        assert batch.status == BatchStatus.DELIVERED
        assert rider.status == RiderStatus.AVAILABLE
        assert o2.status == OrderState.DELIVERED
        assert o2.actual_delivery_time == t_d2
        assert set(res.delivered_order_ids) == {"ORD-01", "ORD-02"}
