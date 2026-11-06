"""Comprehensive unit and integration tests for BR-10: Rider Penalties."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.rider import Rider, RiderStatus
from ps031.services.rider_penalty_service import (
    BATCH_BONUS_AMOUNT,
    DAILY_DEDUCTION_CAP,
    ORDER_DEDUCTION_AMOUNT,
    BatchPenaltyResult,
    OrderPenaltyDetail,
    RiderPenaltyService,
    RiderPenaltyServiceInterface,
)


def make_test_rider(
    rider_id: str = "RIDER-01",
    name: str = "Ramesh Kumar",
    status: RiderStatus = RiderStatus.AVAILABLE,
    daily_penalty: Decimal = Decimal("0.00"),
    daily_penalty_date: date | None = None,
) -> Rider:
    return Rider(
        rider_id=rider_id,
        name=name,
        status=status,
        daily_penalty=daily_penalty,
        daily_penalty_date=daily_penalty_date,
    )


def make_test_order(
    order_id: str,
    promised_eta: datetime | None = None,
    actual_delivery_time: datetime | None = None,
    status: OrderState = OrderState.DELIVERED,
    order_time: datetime | None = None,
) -> Order:
    base_time = order_time or datetime(2026, 11, 6, 10, 0)
    items = [
        OrderItem(
            sku_id="SKU-MILK-01",
            category="Dairy",
            brand="Amul",
            unit_price=Decimal("50.00"),
            requested_qty=1,
            fulfilled_qty=1,
        )
    ]
    return Order(
        order_id=order_id,
        customer_id="CUST-01",
        customer_lat=12.9716,
        customer_lon=77.5946,
        order_time=base_time,
        items=items,
        substitution_preference=SubstitutionPolicy.NONE,
        status=status,
        promised_eta=promised_eta,
        actual_delivery_time=actual_delivery_time,
    )


def make_delivered_batch(
    batch_id: str = "BATCH-01",
    rider_id: str = "RIDER-01",
    orders: list[Order] | None = None,
    created_at: datetime | None = None,
    dispatched_at: datetime | None = None,
    status: BatchStatus = BatchStatus.DELIVERED,
) -> Batch:
    t_created = created_at or datetime(2026, 11, 6, 10, 0)
    t_dispatched = dispatched_at or (t_created + timedelta(minutes=3))
    return Batch(
        batch_id=batch_id,
        rider_id=rider_id,
        created_at=t_created,
        dispatched_at=t_dispatched,
        status=status,
        orders=orders or [],
    )


# ---------------------------------------------------------------------------
# 1. Protocol and Type Check Tests
# ---------------------------------------------------------------------------


def test_service_satisfies_protocol():
    service = RiderPenaltyService()
    assert isinstance(service, RiderPenaltyServiceInterface)


def test_invalid_argument_types():
    service = RiderPenaltyService()
    rider = make_test_rider()
    batch = make_delivered_batch()

    with pytest.raises(TypeError, match="batch must be a Batch instance"):
        service.calculate_batch_penalties("not-a-batch")  # type: ignore

    with pytest.raises(TypeError, match="batch must be a Batch instance"):
        service.calculate_batch_penalties(True)  # type: ignore

    with pytest.raises(TypeError, match="rider must be a Rider instance"):
        service.apply_batch_penalties(batch, "not-a-rider")  # type: ignore

    with pytest.raises(TypeError, match="rider must be a Rider instance"):
        service.apply_batch_penalties(batch, True)  # type: ignore

    with pytest.raises(TypeError, match="batch_id must be a string"):
        service.get_batch_penalty(123)  # type: ignore

    with pytest.raises(ValueError, match="batch_id must not be empty"):
        service.get_batch_penalty("   ")

    with pytest.raises(TypeError, match="rider_id must be a string"):
        service.get_daily_penalty(123, date(2026, 11, 6))  # type: ignore

    with pytest.raises(ValueError, match="rider_id must not be empty"):
        service.get_daily_penalty("   ", date(2026, 11, 6))

    with pytest.raises(TypeError, match="penalty_date must be a date, not a datetime"):
        service.get_daily_penalty("R1", datetime(2026, 11, 6, 10, 0))  # type: ignore

    with pytest.raises(TypeError, match="penalty_date must be a date"):
        service.get_daily_penalty("R1", True)  # type: ignore


# ---------------------------------------------------------------------------
# 2. BR-10 Batch Bonus Rules (Zero, One, Two, Three Late Orders)
# ---------------------------------------------------------------------------


def test_zero_late_orders_retains_twenty_rupee_batch_bonus():
    """A batch with 0 late orders retains the ₹20 batch bonus."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # 3 orders delivered on time or early
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta - timedelta(minutes=2))
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta)

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.calculate_batch_penalties(batch, rider)

    assert res.total_orders == 3
    assert res.delivered_orders_count == 3
    assert res.late_orders_count == 0
    assert res.bonus_lost is False
    assert res.batch_bonus_awarded == BATCH_BONUS_AMOUNT
    assert res.total_calculated_deduction == Decimal("0.00")
    assert res.total_applied_deduction == Decimal("0.00")


def test_one_late_order_retains_twenty_rupee_batch_bonus():
    """A batch with exactly 1 late order retains the ₹20 batch bonus."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # 1 order 2 minutes late, 2 orders on time
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=2))
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta)
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta)

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.calculate_batch_penalties(batch, rider)

    assert res.late_orders_count == 1
    assert res.bonus_lost is False
    assert res.batch_bonus_awarded == BATCH_BONUS_AMOUNT
    assert res.total_calculated_deduction == Decimal("0.00")


def test_two_late_orders_loses_batch_bonus():
    """A batch with 2 late orders loses the ₹20 batch bonus."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # 2 orders 1 second late, 1 on time
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1))
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=30))
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta)

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.calculate_batch_penalties(batch, rider)

    assert res.late_orders_count == 2
    assert res.bonus_lost is True
    assert res.batch_bonus_awarded == Decimal("0.00")
    # Neither order is >15 min late, so no disciplinary deduction
    assert res.total_calculated_deduction == Decimal("0.00")


def test_three_late_orders_loses_batch_bonus_only_once():
    """A batch with 3 late orders loses the ₹20 bonus only once (not ₹40 or ₹60)."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=1))
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=2))
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=3))

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.calculate_batch_penalties(batch, rider)

    assert res.late_orders_count == 3
    assert res.bonus_lost is True
    assert res.batch_bonus_awarded == Decimal("0.00")


# ---------------------------------------------------------------------------
# 3. Severe Lateness Threshold Boundaries (15 Minutes = 900 Seconds)
# ---------------------------------------------------------------------------


def test_boundary_zero_lateness_not_late():
    """Exact on-time delivery (diff = 0) is not late."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
    batch = make_delivered_batch(orders=[o1])
    res = service.calculate_batch_penalties(batch, rider)

    detail = res.order_penalties[0]
    assert detail.is_late is False
    assert detail.is_severely_late is False
    assert detail.calculated_deduction == Decimal("0.00")


def test_boundary_fourteen_minutes_fifty_nine_seconds_late():
    """14m 59s late (899s): late for bonus counting, but no ₹30 deduction."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=899))
    batch = make_delivered_batch(orders=[o1])
    res = service.calculate_batch_penalties(batch, rider)

    detail = res.order_penalties[0]
    assert detail.is_late is True
    assert detail.is_severely_late is False
    assert detail.calculated_deduction == Decimal("0.00")


def test_boundary_exactly_fifteen_minutes_late_no_deduction():
    """CRITICAL BOUNDARY: Exactly 15 minutes late (900.0s) incurs NO ₹30 deduction."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # Exactly 900 seconds
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=900))
    batch = make_delivered_batch(orders=[o1])
    res = service.calculate_batch_penalties(batch, rider)

    detail = res.order_penalties[0]
    assert detail.lateness_seconds == 900.0
    assert detail.is_late is True
    assert detail.is_severely_late is False
    assert detail.calculated_deduction == Decimal("0.00")
    assert detail.applied_deduction == Decimal("0.00")


def test_boundary_subsecond_over_fifteen_minutes_incurs_thirty_rupee_deduction():
    """More than 15 minutes (900.001s) strictly incurs ₹30 deduction."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(microseconds=900001000))
    batch = make_delivered_batch(orders=[o1])
    res = service.calculate_batch_penalties(batch, rider)

    detail = res.order_penalties[0]
    assert detail.lateness_seconds > 900.0
    assert detail.is_late is True
    assert detail.is_severely_late is True
    assert detail.calculated_deduction == ORDER_DEDUCTION_AMOUNT
    assert detail.applied_deduction == ORDER_DEDUCTION_AMOUNT


def test_boundary_fifteen_minutes_one_second_incurs_thirty_rupee_deduction():
    """15 minutes 1 second late (901s) incurs ₹30 deduction."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=901))
    batch = make_delivered_batch(orders=[o1])
    res = service.calculate_batch_penalties(batch, rider)

    detail = res.order_penalties[0]
    assert detail.lateness_seconds == 901.0
    assert detail.is_severely_late is True
    assert detail.calculated_deduction == ORDER_DEDUCTION_AMOUNT
    assert detail.applied_deduction == ORDER_DEDUCTION_AMOUNT


# ---------------------------------------------------------------------------
# 4. Daily Cap Enforcement (₹90 per Delivery Calendar Day)
# ---------------------------------------------------------------------------


def test_daily_cap_single_batch_three_severely_late_orders():
    """3 severely late orders in 1 batch: 3 * ₹30 = ₹90 (cap reached)."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=25))

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.apply_batch_penalties(batch, rider)

    assert res.total_calculated_deduction == Decimal("90.00")
    assert res.total_applied_deduction == Decimal("90.00")
    assert rider.daily_penalty == Decimal("90.00")
    assert rider.daily_penalty_date == date(2026, 11, 6)
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("90.00")


def test_daily_cap_single_batch_exceeding_ninety_rupees():
    """4 severely late orders (hypothetical): calculated ₹120, applied capped at ₹90."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    orders = [
        make_test_order(f"O{i}", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
        for i in range(1, 5)
    ]
    batch = make_delivered_batch(orders=orders)
    res = service.apply_batch_penalties(batch, rider)

    assert res.total_calculated_deduction == Decimal("120.00")
    assert res.total_applied_deduction == Decimal("90.00")
    assert res.order_penalties[0].applied_deduction == Decimal("30.00")
    assert res.order_penalties[1].applied_deduction == Decimal("30.00")
    assert res.order_penalties[2].applied_deduction == Decimal("30.00")
    assert res.order_penalties[3].applied_deduction == Decimal("0.00")  # Capped out
    assert rider.daily_penalty == Decimal("90.00")


def test_daily_cap_across_multiple_batches_same_day():
    """Multiple batches for same rider on the same day cap at ₹90 total."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # Batch 1: 2 severely late orders = ₹60
    b1_o1 = make_test_order("B1-O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    b1_o2 = make_test_order("B1-O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    batch1 = make_delivered_batch("B1", orders=[b1_o1, b1_o2])

    res1 = service.apply_batch_penalties(batch1, rider)
    assert res1.total_applied_deduction == Decimal("60.00")
    assert rider.daily_penalty == Decimal("60.00")

    # Batch 2: 2 severely late orders. Cap capacity remaining is ₹30.
    # Order 1 receives ₹30, Order 2 receives ₹0.
    t_eta2 = datetime(2026, 11, 6, 14, 0)
    b2_o1 = make_test_order("B2-O1", promised_eta=t_eta2, actual_delivery_time=t_eta2 + timedelta(minutes=16))
    b2_o2 = make_test_order("B2-O2", promised_eta=t_eta2, actual_delivery_time=t_eta2 + timedelta(minutes=16))
    batch2 = make_delivered_batch("B2", orders=[b2_o1, b2_o2], created_at=datetime(2026, 11, 6, 13, 50))

    res2 = service.apply_batch_penalties(batch2, rider)
    assert res2.total_calculated_deduction == Decimal("60.00")
    assert res2.total_applied_deduction == Decimal("30.00")
    assert rider.daily_penalty == Decimal("90.00")

    # Batch 3: 1 severely late order on the same day receives ₹0
    t_eta3 = datetime(2026, 11, 6, 18, 0)
    b3_o1 = make_test_order("B3-O1", promised_eta=t_eta3, actual_delivery_time=t_eta3 + timedelta(minutes=16))
    batch3 = make_delivered_batch("B3", orders=[b3_o1], created_at=datetime(2026, 11, 6, 17, 50))

    res3 = service.apply_batch_penalties(batch3, rider)
    assert res3.total_calculated_deduction == Decimal("30.00")
    assert res3.total_applied_deduction == Decimal("0.00")
    assert rider.daily_penalty == Decimal("90.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("90.00")


def test_daily_cap_seeding_from_existing_rider_penalty_matching_date():
    """Existing rider penalty with matching date seeds ledger and consumes capacity."""
    service = RiderPenaltyService()
    # Rider already has ₹45 penalty recorded for 2026-11-06
    rider = make_test_rider(
        daily_penalty=Decimal("45.00"),
        daily_penalty_date=date(2026, 11, 6),
    )
    t_eta = datetime(2026, 11, 6, 10, 10)

    # 2 severely late orders = ₹60 calculated
    # Remaining capacity = 90 - 45 = ₹45
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    batch = make_delivered_batch(orders=[o1, o2])

    res = service.apply_batch_penalties(batch, rider)
    assert res.total_calculated_deduction == Decimal("60.00")
    assert res.total_applied_deduction == Decimal("45.00")
    assert rider.daily_penalty == Decimal("90.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("90.00")


def test_daily_cap_does_not_seed_from_previous_day_rider_penalty():
    """Existing penalty from a previous day does NOT consume the next day's cap allowance."""
    service = RiderPenaltyService()
    # Rider has ₹90 penalty from yesterday (2026-11-05)
    rider = make_test_rider(
        daily_penalty=Decimal("90.00"),
        daily_penalty_date=date(2026, 11, 5),
    )
    # Batch delivered on today (2026-11-06)
    t_eta = datetime(2026, 11, 6, 10, 10)
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    batch = make_delivered_batch(orders=[o1])

    res = service.apply_batch_penalties(batch, rider)
    assert res.total_applied_deduction == Decimal("30.00")
    assert rider.daily_penalty == Decimal("30.00")
    assert rider.daily_penalty_date == date(2026, 11, 6)
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("30.00")


# ---------------------------------------------------------------------------
# 5. Progressive Delivery and Midnight Straddles
# ---------------------------------------------------------------------------


def test_progressive_delivery_each_order_uses_own_actual_delivery_timestamp():
    """Progressive delivery evaluates each order using its distinct delivery timestamp."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_dispatch = datetime(2026, 11, 6, 10, 0)

    # Order 1 promised 10:10, delivered 10:08 (on time)
    o1 = make_test_order("O1", promised_eta=datetime(2026, 11, 6, 10, 10), actual_delivery_time=datetime(2026, 11, 6, 10, 8))
    # Order 2 promised 10:14, delivered 10:16 (2m late -> not severely late)
    o2 = make_test_order("O2", promised_eta=datetime(2026, 11, 6, 10, 14), actual_delivery_time=datetime(2026, 11, 6, 10, 16))
    # Order 3 promised 10:18, delivered 10:35 (17m late -> severely late)
    o3 = make_test_order("O3", promised_eta=datetime(2026, 11, 6, 10, 18), actual_delivery_time=datetime(2026, 11, 6, 10, 35))

    batch = make_delivered_batch(orders=[o1, o2, o3], dispatched_at=t_dispatch)
    res = service.apply_batch_penalties(batch, rider)

    assert res.late_orders_count == 2  # O2 and O3 are late
    assert res.bonus_lost is True      # Lost bonus
    assert res.order_penalties[0].calculated_deduction == Decimal("0.00")
    assert res.order_penalties[1].calculated_deduction == Decimal("0.00")
    assert res.order_penalties[2].calculated_deduction == Decimal("30.00")
    assert res.total_applied_deduction == Decimal("30.00")
    assert rider.daily_penalty == Decimal("30.00")


def test_batch_straddling_midnight_allocates_deductions_to_respective_dates():
    """A batch crossing midnight allocates order deductions to corresponding delivery dates."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_created = datetime(2026, 11, 4, 23, 20)
    t_dispatch = datetime(2026, 11, 4, 23, 30)

    # Order 1: delivered 23:55 on 2026-11-04 (16m late -> ₹30 on Nov 4)
    o1 = make_test_order(
        "O1",
        promised_eta=datetime(2026, 11, 4, 23, 39),
        actual_delivery_time=datetime(2026, 11, 4, 23, 55),
        order_time=t_created,
    )
    # Order 2: delivered 00:20 on 2026-11-05 (16m late -> ₹30 on Nov 5)
    o2 = make_test_order(
        "O2",
        promised_eta=datetime(2026, 11, 5, 0, 4),
        actual_delivery_time=datetime(2026, 11, 5, 0, 20),
        order_time=t_created,
    )

    batch = make_delivered_batch(orders=[o1, o2], created_at=t_created, dispatched_at=t_dispatch)
    res = service.apply_batch_penalties(batch, rider)

    assert res.late_orders_count == 2
    assert res.bonus_lost is True
    assert res.total_applied_deduction == Decimal("60.00")

    # Dates are tracked independently in daily ledger
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 4)) == Decimal("30.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 5)) == Decimal("30.00")

    # Rider reflects latest delivery date
    assert rider.daily_penalty == Decimal("30.00")
    assert rider.daily_penalty_date == date(2026, 11, 5)


# ---------------------------------------------------------------------------
# 6. Cancelled Orders in Batches
# ---------------------------------------------------------------------------


def test_cancelled_order_in_batch_does_not_count_as_late_or_incur_penalty():
    """Cancelled order in a batch is excluded from lateness and penalty evaluation."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # Order 1: Delivered 20m late
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20), status=OrderState.DELIVERED)
    # Order 2: Cancelled during transit (no delivery time)
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.CANCELLED)
    # Order 3: Delivered on time
    o3 = make_test_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta, status=OrderState.DELIVERED)

    batch = make_delivered_batch(orders=[o1, o2, o3])
    res = service.apply_batch_penalties(batch, rider)

    assert res.total_orders == 3
    assert res.delivered_orders_count == 2
    assert res.cancelled_orders_count == 1
    assert res.late_orders_count == 1  # Only O1 is late
    assert res.bonus_lost is False     # Retains bonus because only 1 delivered order was late!
    assert res.batch_bonus_awarded == BATCH_BONUS_AMOUNT
    assert res.total_applied_deduction == Decimal("30.00")  # Only O1 incurred ₹30
    assert rider.daily_penalty == Decimal("30.00")

    # Check details of cancelled order
    cancelled_detail = [d for d in res.order_penalties if d.order_id == "O2"][0]
    assert cancelled_detail.is_cancelled is True
    assert cancelled_detail.is_late is False
    assert cancelled_detail.is_severely_late is False
    assert cancelled_detail.calculated_deduction == Decimal("0.00")


# ---------------------------------------------------------------------------
# 7. Idempotency and Fingerprinting
# ---------------------------------------------------------------------------


def test_idempotent_repeated_application_returns_identical_result_without_double_deduction():
    """Repeated calls for the same batch do not re-apply deductions or mutate rider again."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    batch = make_delivered_batch(orders=[o1])

    res1 = service.apply_batch_penalties(batch, rider)
    assert res1.total_applied_deduction == Decimal("30.00")
    assert rider.daily_penalty == Decimal("30.00")

    # Second call
    res2 = service.apply_batch_penalties(batch, rider)
    assert res2 is res1
    assert rider.daily_penalty == Decimal("30.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("30.00")


def test_repeated_application_rejects_altered_parameters_with_same_batch_id():
    """Reusing batch ID with altered parameters or different rider raises ValueError."""
    service = RiderPenaltyService()
    rider1 = make_test_rider("R1")
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=16))
    batch1 = make_delivered_batch("BATCH-SAME", rider_id="R1", orders=[o1])
    service.apply_batch_penalties(batch1, rider1)

    # Attempt to apply with different rider object or different delivery time
    rider2 = make_test_rider("R2")
    batch_altered = make_delivered_batch("BATCH-SAME", rider_id="R2", orders=[o1])

    with pytest.raises(ValueError, match="already processed with different parameters/fingerprint"):
        service.apply_batch_penalties(batch_altered, rider2)


# ---------------------------------------------------------------------------
# 8. Strict Atomicity and Precondition Failures
# ---------------------------------------------------------------------------


def test_atomicity_invalid_order_leaves_rider_and_ledger_untouched():
    """If one order in a batch has invalid data, nothing is modified in ledger or rider."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    # O1 is valid and severely late
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
    # O2 is DELIVERED but missing actual_delivery_time
    o2 = make_test_order("O2", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.DELIVERED)

    batch = make_delivered_batch(orders=[o1, o2])

    with pytest.raises(ValueError, match="has no actual_delivery_time"):
        service.apply_batch_penalties(batch, rider)

    # Verify no state mutated
    assert rider.daily_penalty == Decimal("0.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("0.00")
    assert service.get_batch_penalty(batch.batch_id) is None


def test_batch_with_non_delivered_status_rejected():
    """Batch not in DELIVERED status is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    batch = make_delivered_batch(status=BatchStatus.DISPATCHED)

    with pytest.raises(ValueError, match="batch must be in DELIVERED status"):
        service.calculate_batch_penalties(batch, rider)


def test_batch_with_non_completed_order_status_rejected():
    """Delivered batch containing order in active lifecycle state (e.g. PICKING) is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    o1 = make_test_order("O1", status=OrderState.PICKING)
    batch = make_delivered_batch(orders=[o1])

    with pytest.raises(ValueError, match="non-completed status 'PICKING'"):
        service.calculate_batch_penalties(batch, rider)


def test_batch_rider_id_mismatch_rejected():
    """Rider ID mismatch between batch and Rider instance is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider("RIDER-A")
    batch = make_delivered_batch(rider_id="RIDER-B")

    with pytest.raises(ValueError, match="Batch rider ID 'RIDER-B' does not match provided rider 'RIDER-A'"):
        service.calculate_batch_penalties(batch, rider)


def test_timezone_mismatch_rejected():
    """Timezone-aware delivery time compared to naive promised_eta raises TypeError."""
    service = RiderPenaltyService()
    rider = make_test_rider()

    t_naive = datetime(2026, 11, 6, 10, 10)
    t_aware = datetime(2026, 11, 6, 10, 20, tzinfo=timezone.utc)

    o1 = make_test_order("O1", promised_eta=t_naive, actual_delivery_time=None)
    batch = make_delivered_batch(orders=[o1])

    with pytest.raises(TypeError, match="Cannot compare timezone-aware.*timezone-naive"):
        service.calculate_batch_penalties(batch, rider, delivered_at=t_aware)


def test_delivery_earlier_than_dispatch_rejected():
    """actual_delivery_time earlier than batch dispatched_at is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_dispatch = datetime(2026, 11, 6, 10, 15)

    o1 = make_test_order("O1", promised_eta=datetime(2026, 11, 6, 10, 30), actual_delivery_time=t_dispatch - timedelta(minutes=5))
    batch = make_delivered_batch(orders=[o1], dispatched_at=t_dispatch)

    with pytest.raises(ValueError, match="cannot be earlier than batch dispatched_at"):
        service.calculate_batch_penalties(batch, rider)


# ---------------------------------------------------------------------------
# 9. Date Regression Prevention Tests (Requirement 1)
# ---------------------------------------------------------------------------


def test_rider_date_regression_prevented_when_processing_older_batch():
    """Processing a Nov 6 batch then a Nov 5 batch must NOT regress Rider date to Nov 5."""
    service = RiderPenaltyService()
    rider = make_test_rider()

    # Batch on Nov 6 with 1 severely late order -> ₹30 on Nov 6
    t_nov6_dispatch = datetime(2026, 11, 6, 10, 0)
    o_nov6 = make_test_order(
        "ORD-NOV6",
        promised_eta=datetime(2026, 11, 6, 10, 10),
        actual_delivery_time=datetime(2026, 11, 6, 10, 30),
        order_time=datetime(2026, 11, 6, 9, 50),
    )
    b_nov6 = make_delivered_batch(
        "B-NOV6",
        orders=[o_nov6],
        created_at=datetime(2026, 11, 6, 9, 50),
        dispatched_at=t_nov6_dispatch,
    )
    res_nov6 = service.apply_batch_penalties(b_nov6, rider)
    assert res_nov6.total_applied_deduction == Decimal("30.00")
    assert rider.daily_penalty == Decimal("30.00")
    assert rider.daily_penalty_date == date(2026, 11, 6)

    # Now process an older batch on Nov 5 with 2 severely late orders -> ₹60 on Nov 5
    t_nov5_dispatch = datetime(2026, 11, 5, 14, 0)
    o_nov5_1 = make_test_order(
        "ORD-NOV5-1",
        promised_eta=datetime(2026, 11, 5, 14, 10),
        actual_delivery_time=datetime(2026, 11, 5, 14, 30),
        order_time=datetime(2026, 11, 5, 13, 50),
    )
    o_nov5_2 = make_test_order(
        "ORD-NOV5-2",
        promised_eta=datetime(2026, 11, 5, 14, 15),
        actual_delivery_time=datetime(2026, 11, 5, 14, 35),
        order_time=datetime(2026, 11, 5, 13, 50),
    )
    b_nov5 = make_delivered_batch(
        "B-NOV5",
        orders=[o_nov5_1, o_nov5_2],
        created_at=datetime(2026, 11, 5, 13, 50),
        dispatched_at=t_nov5_dispatch,
    )
    res_nov5 = service.apply_batch_penalties(b_nov5, rider)
    assert res_nov5.total_applied_deduction == Decimal("60.00")

    # Confirm the Nov 5 and Nov 6 ledger entries are independently correct
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 5)) == Decimal("60.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("30.00")

    # Confirm Rider.daily_penalty_date remains Nov 6
    assert rider.daily_penalty_date == date(2026, 11, 6)
    # Confirm Rider.daily_penalty still represents the Nov 6 ledger total
    assert rider.daily_penalty == Decimal("30.00")


def test_rider_date_preserved_when_batch_spans_multiple_earlier_dates():
    """A batch spanning earlier dates (Nov 3-4) preserves Rider date when Rider is already at Nov 7."""
    service = RiderPenaltyService()
    # Rider already has state for Nov 7
    rider = make_test_rider(
        daily_penalty=Decimal("40.00"),
        daily_penalty_date=date(2026, 11, 7),
    )

    t_nov3_created = datetime(2026, 11, 3, 23, 20)
    t_nov3_dispatch = datetime(2026, 11, 3, 23, 30)

    # Drop 1 on Nov 3, drop 2 on Nov 4
    o1 = make_test_order(
        "O-NOV3",
        promised_eta=datetime(2026, 11, 3, 23, 35),
        actual_delivery_time=datetime(2026, 11, 3, 23, 55),
        order_time=t_nov3_created,
    )
    o2 = make_test_order(
        "O-NOV4",
        promised_eta=datetime(2026, 11, 4, 0, 5),
        actual_delivery_time=datetime(2026, 11, 4, 0, 25),
        order_time=t_nov3_created,
    )
    batch = make_delivered_batch(
        "B-SPAN",
        orders=[o1, o2],
        created_at=t_nov3_created,
        dispatched_at=t_nov3_dispatch,
    )

    service.apply_batch_penalties(batch, rider)

    # Ledger entries for Nov 3 and Nov 4 are recorded
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 3)) == Decimal("30.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 4)) == Decimal("30.00")

    # Rider fields are preserved as Nov 7 and ₹40.00
    assert rider.daily_penalty_date == date(2026, 11, 7)
    assert rider.daily_penalty == Decimal("40.00")


# ---------------------------------------------------------------------------
# 10. Strengthened Idempotency Fingerprint Tests (Requirement 2)
# ---------------------------------------------------------------------------


def test_idempotency_rejects_changed_dispatched_at_on_repeat():
    """Reusing batch ID with altered dispatched_at is rejected, even when orders have actual_delivery_time."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_created = datetime(2026, 11, 6, 10, 0)
    t_dispatch1 = datetime(2026, 11, 6, 10, 3)

    o1 = make_test_order(
        "O1",
        promised_eta=datetime(2026, 11, 6, 10, 15),
        actual_delivery_time=datetime(2026, 11, 6, 10, 35),
        order_time=t_created,
    )
    batch1 = make_delivered_batch(
        "B-DISPATCH-TEST",
        orders=[o1],
        created_at=t_created,
        dispatched_at=t_dispatch1,
    )
    service.apply_batch_penalties(batch1, rider)

    # Same batch ID, same order, but changed dispatched_at (10:05 instead of 10:03)
    t_dispatch2 = datetime(2026, 11, 6, 10, 5)
    batch2 = make_delivered_batch(
        "B-DISPATCH-TEST",
        orders=[o1],
        created_at=t_created,
        dispatched_at=t_dispatch2,
    )
    with pytest.raises(ValueError, match="already processed with different parameters/fingerprint"):
        service.apply_batch_penalties(batch2, rider)


def test_idempotency_rejects_changed_delivered_at_argument_on_repeat():
    """Reusing batch ID with altered delivered_at argument is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_created = datetime(2026, 11, 6, 10, 0)
    t_dispatch = datetime(2026, 11, 6, 10, 3)

    o1 = make_test_order(
        "O1",
        promised_eta=datetime(2026, 11, 6, 10, 15),
        actual_delivery_time=datetime(2026, 11, 6, 10, 35),
        order_time=t_created,
    )
    batch = make_delivered_batch(
        "B-DELIVERED-AT-TEST",
        orders=[o1],
        created_at=t_created,
        dispatched_at=t_dispatch,
    )

    t_del_arg1 = datetime(2026, 11, 6, 10, 35)
    service.apply_batch_penalties(batch, rider, delivered_at=t_del_arg1)

    # Re-apply with changed delivered_at argument
    t_del_arg2 = datetime(2026, 11, 6, 10, 40)
    with pytest.raises(ValueError, match="already processed with different parameters/fingerprint"):
        service.apply_batch_penalties(batch, rider, delivered_at=t_del_arg2)


# ---------------------------------------------------------------------------
# 11. Mutable Rider Penalty Validation Tests (Requirement 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_penalty, expected_err, match_str",
    [
        (Decimal("-10.00"), ValueError, "cannot be negative"),
        (True, TypeError, "cannot be a boolean"),
        (Decimal("NaN"), ValueError, "finite number"),
        (Decimal("Infinity"), ValueError, "finite number"),
        (30, TypeError, "must be a Decimal"),
        ("30.00", TypeError, "must be a Decimal"),
        (30.5, TypeError, "must be a Decimal"),
    ],
)
def test_rejects_post_construction_invalid_daily_penalty(invalid_penalty, expected_err, match_str):
    """Mutating rider.daily_penalty to invalid values post-construction is rejected before state changes."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    rider.daily_penalty = invalid_penalty  # type: ignore

    t_eta = datetime(2026, 11, 6, 10, 10)
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
    batch = make_delivered_batch(orders=[o1])

    with pytest.raises(expected_err, match=match_str):
        service.apply_batch_penalties(batch, rider)

    # Ensure ledger and batch records are untouched
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("0.00")
    assert service.get_batch_penalty(batch.batch_id) is None


@pytest.mark.parametrize(
    "invalid_date, expected_err, match_str",
    [
        (True, TypeError, "cannot be a boolean"),
        (datetime(2026, 11, 6, 10, 0), TypeError, "must be a date, not a datetime"),
        ("2026-11-06", TypeError, "must be a date instance or None"),
        (123, TypeError, "must be a date instance or None"),
    ],
)
def test_rejects_post_construction_invalid_daily_penalty_date(invalid_date, expected_err, match_str):
    """Mutating rider.daily_penalty_date to invalid values post-construction is rejected."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    rider.daily_penalty_date = invalid_date  # type: ignore

    t_eta = datetime(2026, 11, 6, 10, 10)
    o1 = make_test_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
    batch = make_delivered_batch(orders=[o1])

    with pytest.raises(expected_err, match=match_str):
        service.apply_batch_penalties(batch, rider)

    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("0.00")
    assert service.get_batch_penalty(batch.batch_id) is None


# ---------------------------------------------------------------------------
# 12. Duplicate Order IDs Validation Tests (Requirement 4)
# ---------------------------------------------------------------------------


def test_rejects_duplicate_order_ids_in_batch():
    """Batches containing duplicate order IDs are rejected before any state changes."""
    service = RiderPenaltyService()
    rider = make_test_rider()
    t_eta = datetime(2026, 11, 6, 10, 10)

    o1 = make_test_order("ORD-DUP", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
    batch = make_delivered_batch(orders=[o1])
    # Manually append duplicate order to bypass Batch constructor validation
    batch.orders.append(o1)

    with pytest.raises(ValueError, match="Duplicate order ID 'ORD-DUP' found in batch"):
        service.apply_batch_penalties(batch, rider)

    # Ensure ledger, rider, and records are completely untouched
    assert rider.daily_penalty == Decimal("0.00")
    assert service.get_daily_penalty(rider.rider_id, date(2026, 11, 6)) == Decimal("0.00")
    assert service.get_batch_penalty(batch.batch_id) is None
