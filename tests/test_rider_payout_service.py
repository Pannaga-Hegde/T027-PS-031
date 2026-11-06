from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.services.rider_payout_service import (
    BATCH_BONUS_AMOUNT,
    DAILY_DEDUCTION_CAP,
    PERMITTED_DELIVERY_EVENT_STATES,
    SEVERE_LATENESS_DEDUCTION,
    BatchFingerprint,
    BatchPayoutEvaluation,
    DeliveredOrderAudit,
    OrderFingerprint,
    RiderDailyPayout,
    RiderPayoutService,
    RiderPayoutServiceInterface,
)


def make_order(
    order_id: str = "ORD-01",
    customer_id: str = "CUST-01",
    promised_eta: datetime | None = None,
    actual_delivery_time: datetime | None = None,
    status: OrderState = OrderState.DELIVERED,
) -> Order:
    """Helper to construct an Order instance for payout tests."""
    t0 = datetime(2026, 11, 4, 10, 0)
    eta = promised_eta if promised_eta is not None else t0 + timedelta(minutes=10)
    return Order(
        order_id=order_id,
        customer_id=customer_id,
        order_time=t0,
        promised_eta=eta,
        actual_delivery_time=actual_delivery_time,
        status=status,
        delivery_location=Coordinates(latitude=12.9352, longitude=77.6245),
    )


def make_batch(
    batch_id: str = "B-01",
    rider_id: str = "R-01",
    created_at: datetime | None = None,
    dispatched_at: datetime | None = None,
    orders: list[Order] | None = None,
    status: BatchStatus = BatchStatus.DELIVERED,
) -> Batch:
    """Helper to construct a Batch instance for payout tests."""
    t_created = created_at if created_at is not None else datetime(2026, 11, 4, 10, 0)
    t_dispatched = (
        dispatched_at
        if dispatched_at is not None
        else t_created + timedelta(minutes=5)
    )
    return Batch(
        batch_id=batch_id,
        rider_id=rider_id,
        created_at=t_created,
        dispatched_at=t_dispatched,
        orders=orders or [],
        status=status,
    )


# ---------------------------------------------------------------------------
# Category 1: Protocol and Interface Compliance
# ---------------------------------------------------------------------------
class TestProtocolAndInterface:
    def test_service_satisfies_protocol(self) -> None:
        service = RiderPayoutService()
        assert isinstance(service, RiderPayoutServiceInterface)


# ---------------------------------------------------------------------------
# Category 2: Batch Bonus Lateness Threshold (Delta t > 0s)
# ---------------------------------------------------------------------------
class TestBatchBonusThreshold:
    def test_all_orders_on_time_retains_bonus(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 15)

        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta - timedelta(minutes=2))
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta - timedelta(minutes=1))
        o3 = make_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta)  # Exactly on time (Delta t == 0)

        batch = make_batch("B1", "R1", orders=[o1, o2, o3])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 0
        assert eval_res.is_bonus_lost is False
        assert eval_res.uncapped_deductions == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 0
        assert daily.batch_bonus_lost == 0
        assert daily.late_deductions == Decimal("0.00")
        assert daily.late_deduction_capped == Decimal("0.00")

    def test_single_order_late_retains_bonus(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 15)

        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1))  # Late (1s)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta)  # On time

        batch = make_batch("B1", "R1", orders=[o1, o2])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 1
        assert eval_res.is_bonus_lost is False  # Retained: 1 < 2
        assert eval_res.uncapped_deductions == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 1
        assert daily.batch_bonus_lost == 0

    def test_exactly_two_orders_late_forfeits_bonus(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 15)

        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1))
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=10))

        batch = make_batch("B1", "R1", orders=[o1, o2])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 2
        assert eval_res.is_bonus_lost is True  # Forfeited: >= 2
        assert eval_res.uncapped_deductions == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 2
        assert daily.batch_bonus_lost == 1

    def test_three_orders_late_forfeits_bonus(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 15)

        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=5))
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=10))
        o3 = make_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=15))

        batch = make_batch("B1", "R1", orders=[o1, o2, o3])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 3
        assert eval_res.is_bonus_lost is True
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batch_bonus_lost == 1

    def test_single_order_batch_late_retains_bonus(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 15)

        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=5))
        batch = make_batch("B1", "R1", orders=[o1])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 1
        assert eval_res.is_bonus_lost is False  # 1 < 2 -> bonus retained
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batch_bonus_lost == 0


# ---------------------------------------------------------------------------
# Category 3: Severe Lateness Boundaries (Delta t > 900s)
# ---------------------------------------------------------------------------
class TestSevereLatenessBoundaries:
    def test_boundary_under_15_minutes_no_deduction(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # 14 minutes and 59 seconds late: Delta t = 899s
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=899))
        batch = make_batch("B1", "R1", orders=[o1])

        eval_res = service.record_batch_payout(batch)
        assert eval_res.late_orders_count == 1
        assert eval_res.severe_late_orders_count == 0
        assert eval_res.uncapped_deductions == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("0.00")
        assert daily.late_deduction_capped == Decimal("0.00")

    def test_boundary_exactly_15_minutes_no_deduction(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # Exactly 15 minutes late: Delta t = 900.0s
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=15))
        batch = make_batch("B1", "R1", orders=[o1])

        eval_res = service.record_batch_payout(batch)
        assert eval_res.late_orders_count == 1
        assert eval_res.severe_late_orders_count == 0
        assert eval_res.uncapped_deductions == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("0.00")
        assert daily.late_deduction_capped == Decimal("0.00")

    def test_boundary_over_15_minutes_triggers_30_deduction(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # 15 minutes and 1 second late: Delta t = 901.0s
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=901))
        batch = make_batch("B1", "R1", orders=[o1])

        eval_res = service.record_batch_payout(batch)
        assert eval_res.late_orders_count == 1
        assert eval_res.severe_late_orders_count == 1
        assert eval_res.uncapped_deductions == Decimal("30.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("30.00")
        assert daily.late_deduction_capped == Decimal("30.00")

    def test_two_severe_late_orders_triggers_60_deduction(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=25))
        batch = make_batch("B1", "R1", orders=[o1, o2])

        eval_res = service.record_batch_payout(batch)
        assert eval_res.late_orders_count == 2
        assert eval_res.severe_late_orders_count == 2
        assert eval_res.is_bonus_lost is True
        assert eval_res.uncapped_deductions == Decimal("60.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batch_bonus_lost == 1
        assert daily.late_deductions == Decimal("60.00")
        assert daily.late_deduction_capped == Decimal("60.00")


# ---------------------------------------------------------------------------
# Category 4: Daily Cap Enforcement (₹90 Cap per Rider per Date)
# ---------------------------------------------------------------------------
class TestDailyCapEnforcement:
    def test_daily_cap_exactly_90(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # 3 orders * ₹30 = ₹90
        orders = [
            make_order(f"O{i}", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
            for i in range(1, 4)
        ]
        batch = make_batch("B1", "R1", orders=orders)
        service.record_batch_payout(batch)

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("90.00")
        assert daily.late_deduction_capped == Decimal("90.00")

    def test_daily_cap_exceeded_capped_at_90(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # 4 orders * ₹30 = ₹120 -> capped at ₹90
        orders = [
            make_order(f"O{i}", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
            for i in range(1, 5)
        ]
        batch = make_batch("B1", "R1", orders=orders)
        eval_res = service.record_batch_payout(batch)

        assert eval_res.uncapped_deductions == Decimal("120.00")
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("120.00")
        assert daily.late_deduction_capped == Decimal("90.00")

    def test_daily_cap_across_multiple_batches_same_day(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)

        # Batch 1: 2 severe orders (₹60)
        b1_orders = [
            make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20)),
            make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20)),
        ]
        service.record_batch_payout(make_batch("B1", "R1", orders=b1_orders))

        # Batch 2: 3 severe orders (₹90) -> Total uncapped = ₹150, capped = ₹90
        b2_orders = [
            make_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20)),
            make_order("O4", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20)),
            make_order("O5", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20)),
        ]
        service.record_batch_payout(make_batch("B2", "R1", orders=b2_orders))

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 2
        assert daily.batch_bonus_lost == 2
        assert daily.late_orders == 5
        assert daily.late_deductions == Decimal("150.00")
        assert daily.late_deduction_capped == Decimal("90.00")


# ---------------------------------------------------------------------------
# Category 5: Date Attribution & Midnight Crossing
# ---------------------------------------------------------------------------
class TestDateAttributionAndMidnightCrossing:
    def test_single_day_batch_attribution(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=5))
        batch = make_batch("B1", "R1", orders=[o1])

        service.record_batch_payout(batch)
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 1

        # No records on next day
        daily_next = service.get_daily_payout("R1", "2026-11-05")
        assert daily_next.batches == 0
        assert daily_next.late_orders == 0

    def test_batch_crossing_midnight_attribution(self) -> None:
        service = RiderPayoutService()
        # Dispatched Nov 4 23:40
        t_disp = datetime(2026, 11, 4, 23, 40)

        # Order 1: Promised Nov 4 23:50, delivered Nov 4 23:55 (+5m -> late, not severe)
        o1 = make_order(
            "O1",
            promised_eta=datetime(2026, 11, 4, 23, 50),
            actual_delivery_time=datetime(2026, 11, 4, 23, 55),
        )

        # Order 2: Promised Nov 5 00:05, delivered Nov 5 00:25 (+20m -> severe)
        o2 = make_order(
            "O2",
            promised_eta=datetime(2026, 11, 5, 0, 5),
            actual_delivery_time=datetime(2026, 11, 5, 0, 25),
        )

        batch = make_batch(
            "B1",
            "R1",
            created_at=datetime(2026, 11, 4, 23, 30),
            dispatched_at=t_disp,
            orders=[o1, o2],
        )

        eval_res = service.record_batch_payout(batch)
        assert eval_res.completion_date == "2026-11-05"
        assert eval_res.is_bonus_lost is True  # Both orders were late

        # Day 1 ledger: Order 1 delivered on Nov 4
        d1 = service.get_daily_payout("R1", "2026-11-04")
        assert d1.batches == 0
        assert d1.late_orders == 1
        assert d1.batch_bonus_lost == 0
        assert d1.late_deductions == Decimal("0.00")
        assert d1.late_deduction_capped == Decimal("0.00")

        # Day 2 ledger: Order 2 delivered on Nov 5, batch completed on Nov 5
        d2 = service.get_daily_payout("R1", "2026-11-05")
        assert d2.batches == 1
        assert d2.late_orders == 1
        assert d2.batch_bonus_lost == 1
        assert d2.late_deductions == Decimal("30.00")
        assert d2.late_deduction_capped == Decimal("30.00")

    def test_daily_cap_resets_across_midnight(self) -> None:
        service = RiderPayoutService()
        # Day 1: 4 severe late orders (₹120 -> capped at ₹90)
        d1_time = datetime(2026, 11, 4, 14, 0)
        d1_orders = [
            make_order(f"D1_O{i}", promised_eta=d1_time, actual_delivery_time=d1_time + timedelta(minutes=20))
            for i in range(1, 5)
        ]
        service.record_batch_payout(make_batch("B1", "R1", created_at=d1_time - timedelta(minutes=10), orders=d1_orders))

        # Day 2: 3 severe late orders (₹90 -> capped at ₹90)
        d2_time = datetime(2026, 11, 5, 14, 0)
        d2_orders = [
            make_order(f"D2_O{i}", promised_eta=d2_time, actual_delivery_time=d2_time + timedelta(minutes=20))
            for i in range(1, 4)
        ]
        service.record_batch_payout(make_batch("B2", "R1", created_at=d2_time - timedelta(minutes=10), orders=d2_orders))

        payout_d1 = service.get_daily_payout("R1", "2026-11-04")
        assert payout_d1.late_deductions == Decimal("120.00")
        assert payout_d1.late_deduction_capped == Decimal("90.00")

        payout_d2 = service.get_daily_payout("R1", "2026-11-05")
        assert payout_d2.late_deductions == Decimal("90.00")
        assert payout_d2.late_deduction_capped == Decimal("90.00")


# ---------------------------------------------------------------------------
# Category 6: Idempotency & Conflict Prevention
# ---------------------------------------------------------------------------
class TestIdempotencyAndConflictPrevention:
    def test_repeated_batch_recording_is_idempotent(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
        batch = make_batch("B1", "R1", orders=[o1])

        eval1 = service.record_batch_payout(batch)
        eval2 = service.record_batch_payout(batch)

        assert eval1 == eval2
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 1
        assert daily.late_deductions == Decimal("30.00")

    def test_repeated_delivery_event_is_idempotent(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=20)
        order = make_order("O1", promised_eta=t_eta)

        audit1 = service.record_delivery_event(order, "R1", "B1", t_del)
        audit2 = service.record_delivery_event(order, "R1", "B1", t_del)

        assert audit1 == audit2
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_orders == 1
        assert daily.late_deductions == Decimal("30.00")

    def test_delivery_events_followed_by_batch_completion(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta)
        o2 = make_order("O2", promised_eta=t_eta)

        # Step 1: Record delivery events progressively
        t1 = t_eta + timedelta(minutes=5)   # Late (+5m)
        t2 = t_eta + timedelta(minutes=20)  # Severe (+20m)
        service.record_delivery_event(o1, "R1", "B1", t1)
        service.record_delivery_event(o2, "R1", "B1", t2)

        # Step 2: Finalize batch completion
        batch = make_batch("B1", "R1", orders=[o1, o2])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.late_orders_count == 2
        assert eval_res.is_bonus_lost is True
        assert eval_res.uncapped_deductions == Decimal("30.00")

        # Zero double-counting: exactly 1 batch, 2 late orders, ₹30 deduction
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 2
        assert daily.batch_bonus_lost == 1
        assert daily.late_deductions == Decimal("30.00")
        assert daily.late_deduction_capped == Decimal("30.00")

    def test_conflicting_batch_rider_raises_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
        batch1 = make_batch("B1", "R1", orders=[o1])
        service.record_batch_payout(batch1)

        batch2 = make_batch("B1", "R2", orders=[o1])
        with pytest.raises(ValueError, match="rider_id mismatch"):
            service.record_batch_payout(batch2)

    def test_conflicting_batch_completed_at_raises_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
        batch = make_batch("B1", "R1", orders=[o1])

        t_comp1 = datetime(2026, 11, 4, 10, 30)
        t_comp2 = datetime(2026, 11, 4, 11, 0)

        service.record_batch_payout(batch, completed_at=t_comp1)
        with pytest.raises(ValueError, match="completed_at mismatch"):
            service.record_batch_payout(batch, completed_at=t_comp2)

    def test_conflicting_batch_orders_raises_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta)

        service.record_batch_payout(make_batch("B1", "R1", orders=[o1]))
        with pytest.raises(ValueError, match="orders or order timestamps mismatch"):
            service.record_batch_payout(make_batch("B1", "R1", orders=[o1, o2]))

    def test_conflicting_batch_delivery_overrides_raises_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_comp = datetime(2026, 11, 4, 10, 30)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta)
        batch = make_batch("B1", "R1", orders=[o1])

        service.record_batch_payout(
            batch, completed_at=t_comp, delivery_times={"O1": t_eta}
        )
        with pytest.raises(ValueError, match="delivery_times overrides mismatch"):
            service.record_batch_payout(
                batch,
                completed_at=t_comp,
                delivery_times={"O1": t_eta + timedelta(minutes=5)},
            )

    def test_conflicting_delivery_event_raises_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta)

        service.record_delivery_event(o1, "R1", "B1", t_eta)
        with pytest.raises(ValueError, match="Conflicting delivery event"):
            service.record_delivery_event(o1, "R1", "B1", t_eta + timedelta(minutes=10))


# ---------------------------------------------------------------------------
# Category 7: Incomplete Batch & Validation Rejections
# ---------------------------------------------------------------------------
class TestIncompleteBatchAndValidation:
    def test_batch_in_created_status_raises_value_error(self) -> None:
        service = RiderPayoutService()
        o1 = make_order("O1", actual_delivery_time=datetime(2026, 11, 4, 10, 10))
        batch = make_batch("B1", "R1", orders=[o1], status=BatchStatus.CREATED)

        with pytest.raises(ValueError, match="batch is in CREATED status"):
            service.record_batch_payout(batch)

    def test_batch_with_dispatched_order_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta, status=OrderState.DELIVERED)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.DISPATCHED)
        batch = make_batch("B1", "R1", orders=[o1, o2], status=BatchStatus.DISPATCHED)

        with pytest.raises(ValueError, match="non-terminal status 'DISPATCHED'"):
            service.record_batch_payout(batch)

    def test_delivered_order_missing_actual_delivery_time_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.DELIVERED)
        batch = make_batch("B1", "R1", orders=[o1], status=BatchStatus.DELIVERED)

        with pytest.raises(ValueError, match="has no actual_delivery_time"):
            service.record_batch_payout(batch)

    def test_empty_batch_raises_value_error(self) -> None:
        service = RiderPayoutService()
        batch = make_batch("B1", "R1", orders=[])
        with pytest.raises(ValueError, match="contains no orders"):
            service.record_batch_payout(batch)

    def test_timezone_parity_mismatch_raises_type_error(self) -> None:
        service = RiderPayoutService()
        t_naive = datetime(2026, 11, 4, 10, 0)
        t_aware = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)

        o1 = make_order("O1", promised_eta=t_naive, actual_delivery_time=t_naive)
        batch = make_batch("B1", "R1", orders=[o1])

        # Test in record_batch_payout via delivery_times override
        with pytest.raises(TypeError, match="Cannot compare"):
            service.record_batch_payout(
                batch, delivery_times={"O1": t_aware}
            )

        # Test in record_delivery_event
        with pytest.raises(TypeError, match="Cannot compare"):
            service.record_delivery_event(o1, "R1", "B1", t_aware)

    def test_invalid_argument_types_raise_type_error(self) -> None:
        service = RiderPayoutService()
        with pytest.raises(TypeError):
            service.record_batch_payout(True)  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            service.record_delivery_event("not_an_order", "R1", "B1", datetime.now())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Category 8: Cancellation & Partial Batches
# ---------------------------------------------------------------------------
class TestCancellationAndPartialBatches:
    def test_partially_delivered_batch_bonus_lost(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)

        # 3 orders: 1 cancelled, 2 delivered late (+1s)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.CANCELLED)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1), status=OrderState.DELIVERED)
        o3 = make_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1), status=OrderState.DELIVERED)

        batch = make_batch("B1", "R1", orders=[o1, o2, o3])
        t_comp = t_eta + timedelta(minutes=10)
        eval_res = service.record_batch_payout(batch, completed_at=t_comp)

        # 2 delivered orders were late -> bonus lost
        assert eval_res.total_delivered_orders == 2
        assert eval_res.late_orders_count == 2
        assert eval_res.is_bonus_lost is True

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 2
        assert daily.batch_bonus_lost == 1

    def test_partially_delivered_batch_bonus_retained(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)

        # 3 orders: 2 cancelled, 1 delivered late (+1s)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.CANCELLED)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=None, status=OrderState.CANCELLED)
        o3 = make_order("O3", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(seconds=1), status=OrderState.DELIVERED)

        batch = make_batch("B1", "R1", orders=[o1, o2, o3])
        t_comp = t_eta + timedelta(minutes=10)
        eval_res = service.record_batch_payout(batch, completed_at=t_comp)

        # 1 delivered order was late -> 1 < 2 -> bonus retained
        assert eval_res.total_delivered_orders == 1
        assert eval_res.late_orders_count == 1
        assert eval_res.is_bonus_lost is False

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 1
        assert daily.batch_bonus_lost == 0

    def test_partially_cancelled_batch_requires_explicit_completed_at(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, status=OrderState.CANCELLED)
        o2 = make_order("O2", promised_eta=t_eta, actual_delivery_time=t_eta, status=OrderState.DELIVERED)
        batch = make_batch("B1", "R1", orders=[o1, o2])

        with pytest.raises(ValueError, match="explicit 'completed_at' timestamp is required"):
            service.record_batch_payout(batch, completed_at=None)

    def test_cancelled_order_excluded_from_deductions(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        # Cancelled order should incur ₹0 deduction
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=30), status=OrderState.CANCELLED)
        audit = service.record_delivery_event(o1, "R1", "B1", t_eta + timedelta(minutes=30))

        assert audit.is_late is False
        assert audit.is_severe_late is False
        assert audit.deduction == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_deductions == Decimal("0.00")

    def test_fully_cancelled_batch_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, status=OrderState.CANCELLED)
        o2 = make_order("O2", promised_eta=t_eta, status=OrderState.CANCELLED)
        batch = make_batch("B1", "R1", orders=[o1, o2], status=BatchStatus.CANCELLED)

        with pytest.raises(ValueError, match="zero delivered orders"):
            service.record_batch_payout(batch)


# ---------------------------------------------------------------------------
# Category 9: Multi-Rider & Query Utilities
# ---------------------------------------------------------------------------
class TestMultiRiderAndLedgerQueries:
    def test_multiple_riders_independent_caps(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)

        # Rider 1: 4 severe orders (₹120 -> capped at ₹90)
        r1_orders = [
            make_order(f"R1_O{i}", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
            for i in range(1, 5)
        ]
        service.record_batch_payout(make_batch("B1", "R1", orders=r1_orders))

        # Rider 2: 4 severe orders (₹120 -> capped at ₹90)
        r2_orders = [
            make_order(f"R2_O{i}", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
            for i in range(1, 5)
        ]
        service.record_batch_payout(make_batch("B2", "R2", orders=r2_orders))

        p1 = service.get_daily_payout("R1", "2026-11-04")
        p2 = service.get_daily_payout("R2", "2026-11-04")

        assert p1.late_deductions == Decimal("120.00")
        assert p1.late_deduction_capped == Decimal("90.00")

        assert p2.late_deductions == Decimal("120.00")
        assert p2.late_deduction_capped == Decimal("90.00")

    def test_get_daily_payout_for_inactive_rider_returns_zeros(self) -> None:
        service = RiderPayoutService()
        p = service.get_daily_payout("INACTIVE_RIDER", "2026-11-04")
        assert p.rider_id == "INACTIVE_RIDER"
        assert p.batches == 0
        assert p.late_orders == 0
        assert p.batch_bonus_lost == 0
        assert p.late_deductions == Decimal("0.00")
        assert p.late_deduction_capped == Decimal("0.00")

    def test_get_all_daily_payouts_sorting_and_filtering(self) -> None:
        service = RiderPayoutService()
        t1 = datetime(2026, 11, 4, 10, 0)
        t2 = datetime(2026, 11, 5, 10, 0)

        # Insert out of order
        o_r2_d2 = make_order("O1", promised_eta=t2, actual_delivery_time=t2)
        service.record_batch_payout(make_batch("B_R2_D2", "R2", created_at=t2 - timedelta(minutes=5), orders=[o_r2_d2]))

        o_r1_d1 = make_order("O2", promised_eta=t1, actual_delivery_time=t1)
        service.record_batch_payout(make_batch("B_R1_D1", "R1", created_at=t1 - timedelta(minutes=5), orders=[o_r1_d1]))

        o_r2_d1 = make_order("O3", promised_eta=t1, actual_delivery_time=t1)
        service.record_batch_payout(make_batch("B_R2_D1", "R2", created_at=t1 - timedelta(minutes=5), orders=[o_r2_d1]))

        # Get all: sorted by (date asc, rider_id asc)
        all_payouts = service.get_all_daily_payouts()
        expected_tuples = [
            ("2026-11-04", "R1"),
            ("2026-11-04", "R2"),
            ("2026-11-05", "R2"),
        ]
        actual_tuples = [(p.date, p.rider_id) for p in all_payouts]
        assert actual_tuples == expected_tuples

        # Filter by specific date
        filtered = service.get_all_daily_payouts("2026-11-04")
        assert len(filtered) == 2
        assert [(p.date, p.rider_id) for p in filtered] == [("2026-11-04", "R1"), ("2026-11-04", "R2")]

    def test_reset_clears_all_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        o1 = make_order("O1", promised_eta=t_eta, actual_delivery_time=t_eta + timedelta(minutes=20))
        service.record_batch_payout(make_batch("B1", "R1", orders=[o1]))

        assert len(service.get_all_daily_payouts()) == 1

        service.reset()
        assert len(service.get_all_daily_payouts()) == 0
        p = service.get_daily_payout("R1", "2026-11-04")
        assert p.batches == 0
        assert p.late_orders == 0
        assert p.late_deductions == Decimal("0.00")


# ---------------------------------------------------------------------------
# Category 10: Delivery Event & Batch Reconciliation
# ---------------------------------------------------------------------------
class TestDeliveryEventAndBatchReconciliation:
    def test_reconciliation_mismatch_rider_id_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        o1 = make_order("O1", promised_eta=t_eta)

        service.record_delivery_event(o1, rider_id="R1", batch_id="B1", delivered_at=t_del)

        # Reconciling with a batch assigned to a different rider must fail
        batch = make_batch("B1", rider_id="R2", orders=[o1])
        with pytest.raises(ValueError, match="existing delivery audit rider_id 'R1' does not match batch rider_id 'R2'"):
            service.record_batch_payout(batch)

    def test_reconciliation_mismatch_batch_id_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        o1 = make_order("O1", promised_eta=t_eta)

        service.record_delivery_event(o1, rider_id="R1", batch_id="B1", delivered_at=t_del)

        # Reconciling with a different batch_id must fail
        batch = make_batch("B2", rider_id="R1", orders=[o1])
        with pytest.raises(ValueError, match="existing delivery audit batch_id 'B1' does not match batch batch_id 'B2'"):
            service.record_batch_payout(batch)

    def test_reconciliation_mismatch_actual_delivery_time_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del1 = t_eta + timedelta(minutes=5)
        t_del2 = t_eta + timedelta(minutes=10)
        o1 = make_order("O1", promised_eta=t_eta)

        service.record_delivery_event(o1, rider_id="R1", batch_id="B1", delivered_at=t_del1)

        # Reconciling with a different delivery timestamp via delivery_times override must fail
        batch = make_batch("B1", rider_id="R1", orders=[o1])
        with pytest.raises(ValueError, match="existing delivery audit actual_delivery_time"):
            service.record_batch_payout(batch, delivery_times={"O1": t_del2})

    def test_reconciliation_mismatch_promised_eta_raises_value_error(self) -> None:
        service = RiderPayoutService()
        t_eta1 = datetime(2026, 11, 4, 10, 0)
        t_eta2 = datetime(2026, 11, 4, 10, 15)
        t_del = datetime(2026, 11, 4, 10, 5)

        o1_event = make_order("O1", promised_eta=t_eta1)
        service.record_delivery_event(o1_event, rider_id="R1", batch_id="B1", delivered_at=t_del)

        # Reconciling with an order instance that has an altered promised_eta must fail
        o1_batch = make_order("O1", promised_eta=t_eta2, actual_delivery_time=t_del)
        batch = make_batch("B1", rider_id="R1", orders=[o1_batch])
        with pytest.raises(ValueError, match="existing delivery audit promised_eta"):
            service.record_batch_payout(batch)

    def test_reconciliation_valid_matching_delivery_event_then_batch_completion(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del1 = t_eta + timedelta(minutes=5)   # Late (+5m)
        t_del2 = t_eta + timedelta(minutes=20)  # Severe late (+20m)

        o1 = make_order("O1", promised_eta=t_eta)
        o2 = make_order("O2", promised_eta=t_eta)

        # Record progressive events
        service.record_delivery_event(o1, rider_id="R1", batch_id="B1", delivered_at=t_del1)
        service.record_delivery_event(o2, rider_id="R1", batch_id="B1", delivered_at=t_del2)

        # Finalize batch with matching values
        batch = make_batch("B1", rider_id="R1", orders=[o1, o2])
        eval_res = service.record_batch_payout(batch)

        assert eval_res.batch_id == "B1"
        assert eval_res.rider_id == "R1"
        assert eval_res.late_orders_count == 2
        assert eval_res.is_bonus_lost is True
        assert eval_res.severe_late_orders_count == 1
        assert eval_res.uncapped_deductions == Decimal("30.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 1
        assert daily.late_orders == 2
        assert daily.batch_bonus_lost == 1
        assert daily.late_deductions == Decimal("30.00")
        assert daily.late_deduction_capped == Decimal("30.00")

    def test_reconciliation_mismatch_does_not_mutate_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        o1 = make_order("O1", promised_eta=t_eta)

        service.record_delivery_event(o1, rider_id="R1", batch_id="B1", delivered_at=t_del)

        # Verify state before failed batch reconciliation
        payout_before = service.get_daily_payout("R1", "2026-11-04")
        assert payout_before.batches == 0
        assert payout_before.late_orders == 1
        assert payout_before.late_deductions == Decimal("0.00")

        # Attempt conflicting batch submission (rider_id mismatch)
        batch = make_batch("B1", rider_id="R2", orders=[o1])
        with pytest.raises(ValueError, match="Reconciliation error"):
            service.record_batch_payout(batch)

        # Verify state after failure: strictly unchanged!
        payout_after_r1 = service.get_daily_payout("R1", "2026-11-04")
        assert payout_after_r1.batches == 0
        assert payout_after_r1.late_orders == 1

        payout_after_r2 = service.get_daily_payout("R2", "2026-11-04")
        assert payout_after_r2.batches == 0
        assert payout_after_r2.late_orders == 0


# ---------------------------------------------------------------------------
# Category 11: Delivery Event Order State Validation
# ---------------------------------------------------------------------------
class TestDeliveryEventStateValidation:
    def test_record_delivery_event_rejects_received_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        order = make_order("ORD-RCV", promised_eta=t_eta, status=OrderState.RECEIVED)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        msg = str(exc_info.value)
        assert "ORD-RCV" in msg
        assert "RECEIVED" in msg

    def test_record_delivery_event_rejects_allocated_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        order = make_order("ORD-ALC", promised_eta=t_eta, status=OrderState.ALLOCATED)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        msg = str(exc_info.value)
        assert "ORD-ALC" in msg
        assert "ALLOCATED" in msg

    def test_record_delivery_event_rejects_picking_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        order = make_order("ORD-PCK", promised_eta=t_eta, status=OrderState.PICKING)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        msg = str(exc_info.value)
        assert "ORD-PCK" in msg
        assert "PICKING" in msg

    def test_record_delivery_event_rejects_ready_for_dispatch_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        order = make_order("ORD-RFD", promised_eta=t_eta, status=OrderState.READY_FOR_DISPATCH)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        msg = str(exc_info.value)
        assert "ORD-RFD" in msg
        assert "READY_FOR_DISPATCH" in msg

    def test_record_delivery_event_rejects_rejected_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)
        order = make_order("ORD-REJ", promised_eta=t_eta, status=OrderState.REJECTED)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        msg = str(exc_info.value)
        assert "ORD-REJ" in msg
        assert "REJECTED" in msg

    @pytest.mark.parametrize(
        "rejected_state",
        [
            OrderState.RECEIVED,
            OrderState.ALLOCATED,
            OrderState.PICKING,
            OrderState.READY_FOR_DISPATCH,
            OrderState.REJECTED,
        ],
    )
    def test_all_pre_dispatch_states_rejected_with_descriptive_error(
        self, rejected_state: OrderState
    ) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        order = make_order(f"ORD-{rejected_state.value}", promised_eta=t_eta, status=rejected_state)

        with pytest.raises(ValueError) as exc_info:
            service.record_delivery_event(order, "R1", "B1", t_eta)

        msg = str(exc_info.value)
        assert order.order_id in msg
        assert rejected_state.value in msg

    def test_rejected_state_does_not_mutate_ledger_or_audit(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=20)
        order = make_order("ORD-PCK-01", promised_eta=t_eta, status=OrderState.PICKING)

        with pytest.raises(ValueError):
            service.record_delivery_event(order, "R1", "B1", t_del)

        # Confirm zero audit records stored
        assert "ORD-PCK-01" not in service._delivered_orders
        assert len(service._delivered_orders) == 0

        # Confirm zero ledger mutations
        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.batches == 0
        assert daily.late_orders == 0
        assert daily.batch_bonus_lost == 0
        assert daily.late_deductions == Decimal("0.00")
        assert daily.late_deduction_capped == Decimal("0.00")
        assert len(service.get_all_daily_payouts()) == 0

    def test_record_delivery_event_permits_dispatched_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=20)  # Severe late (+20m)
        order = make_order("ORD-DISP", promised_eta=t_eta, status=OrderState.DISPATCHED)

        audit = service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        assert audit.order_id == "ORD-DISP"
        assert audit.batch_id == "B1"
        assert audit.rider_id == "R1"
        assert audit.is_late is True
        assert audit.is_severe_late is True
        assert audit.deduction == Decimal("30.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_orders == 1
        assert daily.late_deductions == Decimal("30.00")

        # Idempotency check: re-recording returns identical audit without double-counting
        audit_again = service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)
        assert audit_again == audit
        daily_again = service.get_daily_payout("R1", "2026-11-04")
        assert daily_again.late_orders == 1
        assert daily_again.late_deductions == Decimal("30.00")

    def test_record_delivery_event_permits_delivered_state(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=5)  # Late (+5m)
        order = make_order("ORD-DLV", promised_eta=t_eta, status=OrderState.DELIVERED)

        audit = service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        assert audit.order_id == "ORD-DLV"
        assert audit.batch_id == "B1"
        assert audit.rider_id == "R1"
        assert audit.is_late is True
        assert audit.is_severe_late is False
        assert audit.deduction == Decimal("0.00")

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_orders == 1
        assert daily.late_deductions == Decimal("0.00")

    def test_record_delivery_event_permits_cancelled_state_with_zero_penalties(self) -> None:
        service = RiderPayoutService()
        t_eta = datetime(2026, 11, 4, 10, 0)
        t_del = t_eta + timedelta(minutes=30)  # >15m late, but cancelled
        order = make_order("ORD-CNC", promised_eta=t_eta, status=OrderState.CANCELLED)

        audit = service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)

        # BR-10: Cancelled orders are excluded from late metrics, severe deductions, and bonuses
        assert audit.order_id == "ORD-CNC"
        assert audit.is_late is False
        assert audit.is_severe_late is False
        assert audit.deduction == Decimal("0.00")
        assert audit.lateness_seconds == 0.0

        daily = service.get_daily_payout("R1", "2026-11-04")
        assert daily.late_orders == 0
        assert daily.late_deductions == Decimal("0.00")
        assert daily.late_deduction_capped == Decimal("0.00")
        assert daily.batch_bonus_lost == 0

        # Preserve idempotency for cancelled order
        audit_again = service.record_delivery_event(order, rider_id="R1", batch_id="B1", delivered_at=t_del)
        assert audit_again == audit
        daily_again = service.get_daily_payout("R1", "2026-11-04")
        assert daily_again.late_orders == 0
        assert daily_again.late_deductions == Decimal("0.00")
