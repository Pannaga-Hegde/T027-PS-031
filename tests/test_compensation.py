from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.services.compensation_service import (
    TIER1_LATENESS_MINUTES,
    TIER1_LATENESS_SECONDS,
    TIER1_WALLET_CREDIT,
    TIER2_LATENESS_MINUTES,
    TIER2_LATENESS_SECONDS,
    TIER2_WALLET_CREDIT,
    ZERO_WALLET_CREDIT,
    CompensationResult,
    CompensationService,
    CompensationServiceInterface,
)


def make_order(
    order_id: str = "ORD-01",
    customer_id: str = "CUST-01",
    promised_eta: datetime | None = None,
    actual_delivery_time: datetime | None = None,
    status: OrderState = OrderState.DELIVERED,
    delayed_promise: bool = False,
    slow_delivery: bool = False,
    wallet_credit: Decimal | int | float | str = Decimal("0.00"),
    free_coupon: bool = False,
) -> Order:
    """Helper to create a test order with explicit delivery and SLA attributes."""
    t0 = datetime(2026, 11, 4, 10, 0)
    eta = promised_eta if promised_eta is not None else t0 + timedelta(minutes=10)
    return Order(
        order_id=order_id,
        customer_id=customer_id,
        order_time=t0,
        promised_eta=eta,
        actual_delivery_time=actual_delivery_time,
        status=status,
        delayed_promise=delayed_promise,
        slow_delivery=slow_delivery,
        wallet_credit=wallet_credit,
        free_coupon=free_coupon,
        delivery_location=Coordinates(latitude=12.9352, longitude=77.6245),
    )


class TestLatenessCalculation:
    """Tests covering core lateness brackets and monetary amounts for standard orders."""

    def test_on_time_delivery_receives_no_compensation(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_eta)

        res = service.calculate_compensation(order)

        assert res.order_id == "ORD-01"
        assert res.is_late is False
        assert res.lateness_seconds == 0.0
        assert res.lateness_minutes == 0.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False
        assert "on time or early" in res.reason

    def test_early_delivery_receives_no_compensation(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta - timedelta(minutes=3)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is False
        assert res.lateness_seconds == 0.0
        assert res.lateness_minutes == 0.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False

    def test_four_minutes_fifty_nine_seconds_late_receives_no_compensation(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=4, seconds=59)  # 299 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 299.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False
        assert "below 5-minute compensation threshold" in res.reason

    def test_subsecond_four_minutes_fifty_nine_point_nine_nine_nine_seconds_late(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(seconds=299.999)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 299.999
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False

    def test_exactly_five_minutes_late_receives_twenty_five_wallet_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=5)  # exactly 300 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 300.0
        assert res.lateness_minutes == 5.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False
        assert "eligible for ₹25 wallet credit" in res.reason

    def test_seven_minutes_late_receives_twenty_five_wallet_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=7)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 420.0
        assert res.lateness_minutes == 7.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False

    def test_exactly_ten_minutes_late_receives_twenty_five_wallet_credit_no_coupon(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=10)  # exactly 600 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 600.0
        assert res.lateness_minutes == 10.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False  # Exactly 10 min late does NOT get coupon
        assert "eligible for ₹25 wallet credit" in res.reason

    def test_ten_minutes_one_second_late_receives_fifty_wallet_credit_and_coupon(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=10, seconds=1)  # 601 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 601.0
        assert res.wallet_credit == Decimal("50.00")
        assert res.free_coupon is True
        assert "eligible for ₹50 wallet credit and free-delivery coupon" in res.reason

    def test_subsecond_ten_minutes_point_zero_zero_one_seconds_late(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(seconds=600.001)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 600.001
        assert res.wallet_credit == Decimal("50.00")
        assert res.free_coupon is True

    def test_fifteen_minutes_late_receives_fifty_wallet_credit_and_coupon(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=15)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 900.0
        assert res.lateness_minutes == 15.0
        assert res.wallet_credit == Decimal("50.00")
        assert res.free_coupon is True


class TestDelayedPromiseExemption:
    """Tests covering delayed-promise grace period and exemption behaviour under BR-09."""

    def test_delayed_promise_early_or_on_time_receives_no_compensation(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)  # Extended promise
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_eta, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is False
        assert res.lateness_seconds == 0.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False

    def test_delayed_promise_four_minutes_late_receives_no_compensation(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=4)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 240.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is True
        assert "delayed-promise grace period; exempt from compensation" in res.reason

    def test_delayed_promise_exactly_five_minutes_late_is_exempt(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=5)  # 300 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        # Standard order gets ₹25 at 5m 0s, but delayed-promise order is exempt
        assert res.is_late is True
        assert res.lateness_seconds == 300.0
        assert res.lateness_minutes == 5.0
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is True
        assert "delayed-promise grace period; exempt from compensation" in res.reason

    def test_delayed_promise_five_minutes_one_second_late_receives_twenty_five_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=5, seconds=1)  # 301 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        # Beyond 5-minute exemption: falls into standard 5–10 min bracket without shifting
        assert res.is_late is True
        assert res.lateness_seconds == 301.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False

    def test_delayed_promise_seven_minutes_late_receives_twenty_five_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=7)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 420.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False

    def test_delayed_promise_exactly_ten_minutes_late_receives_twenty_five_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=10)  # 600 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 600.0
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False

    def test_delayed_promise_ten_minutes_one_second_late_receives_fifty_credit_and_coupon(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=10, seconds=1)  # 601 seconds
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 601.0
        assert res.wallet_credit == Decimal("50.00")
        assert res.free_coupon is True
        assert res.is_delayed_promise_exempt is False

    def test_delayed_promise_fifteen_minutes_late_receives_fifty_credit_and_coupon(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=15)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual, delayed_promise=True)

        res = service.calculate_compensation(order)

        assert res.is_late is True
        assert res.lateness_seconds == 900.0
        assert res.wallet_credit == Decimal("50.00")
        assert res.free_coupon is True

    def test_slow_delivery_order_does_not_receive_delayed_promise_exemption(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 15)
        t_actual = t_eta + timedelta(minutes=5)  # 300 seconds
        # Customer opted for slow delivery: delayed_promise is False
        order = make_order(
            promised_eta=t_eta,
            actual_delivery_time=t_actual,
            delayed_promise=False,
            slow_delivery=True,
        )

        res = service.calculate_compensation(order)

        # Standard 5-minute late rule applies: ₹25 awarded
        assert res.is_late is True
        assert res.wallet_credit == Decimal("25.00")
        assert res.free_coupon is False
        assert res.is_delayed_promise_exempt is False


class TestIdempotencyAndDuplicatePrevention:
    """Tests covering repeated application, wallet credit preservation, and idempotency."""

    def test_apply_compensation_mutates_order_and_records_in_ledger(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=6)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res = service.apply_compensation(order)

        assert res.wallet_credit == Decimal("25.00")
        assert order.wallet_credit == Decimal("25.00")
        assert order.free_coupon is False

        # Ledger lookup
        stored = service.get_compensation(order.order_id)
        assert stored is res

    def test_duplicate_apply_compensation_does_not_double_award_wallet_credit(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=7)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        res1 = service.apply_compensation(order)
        assert order.wallet_credit == Decimal("25.00")

        # Second call on same order
        res2 = service.apply_compensation(order)

        assert res2 is res1
        # Wallet credit MUST NOT be double-counted to 50.00
        assert order.wallet_credit == Decimal("25.00")

    def test_preserves_existing_wallet_credit_balance(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=7)
        # Order already has an existing wallet balance of ₹15.50
        order = make_order(
            promised_eta=t_eta,
            actual_delivery_time=t_actual,
            wallet_credit=Decimal("15.50"),
        )

        service.apply_compensation(order)

        # ₹15.50 + ₹25.00 = ₹40.50
        assert order.wallet_credit == Decimal("40.50")

    def test_preserves_existing_wallet_credit_on_fifty_rupees_award(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=12)
        order = make_order(
            promised_eta=t_eta,
            actual_delivery_time=t_actual,
            wallet_credit=Decimal("10.00"),
            free_coupon=False,
        )

        service.apply_compensation(order)

        # ₹10.00 + ₹50.00 = ₹60.00
        assert order.wallet_credit == Decimal("60.00")
        assert order.free_coupon is True

    def test_free_coupon_already_present_remains_true(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=6)  # Tier 1 does not award coupon
        order = make_order(
            promised_eta=t_eta,
            actual_delivery_time=t_actual,
            free_coupon=True,  # already had a coupon from promo
        )

        service.apply_compensation(order)

        assert order.wallet_credit == Decimal("25.00")
        assert order.free_coupon is True  # preserved

    def test_get_compensation_returns_none_for_unrecorded_order(self) -> None:
        service = CompensationService()
        assert service.get_compensation("UNKNOWN-ORD") is None

    def test_multiple_orders_tracked_independently(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)

        o1 = make_order(
            order_id="ORD-01",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=6),
        )
        o2 = make_order(
            order_id="ORD-02",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=15),
        )

        res1 = service.apply_compensation(o1)
        res2 = service.apply_compensation(o2)

        assert res1.wallet_credit == Decimal("25.00")
        assert res1.free_coupon is False
        assert o1.wallet_credit == Decimal("25.00")

        assert res2.wallet_credit == Decimal("50.00")
        assert res2.free_coupon is True
        assert o2.wallet_credit == Decimal("50.00")

        assert service.get_compensation("ORD-01") is res1
        assert service.get_compensation("ORD-02") is res2


class TestDataValidationAndParity:
    """Tests covering preconditions, state requirements, and datetime parity."""

    def test_calculate_compensation_does_not_mutate_order(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=15)
        order = make_order(
            promised_eta=t_eta,
            actual_delivery_time=t_actual,
            wallet_credit=Decimal("0.00"),
            free_coupon=False,
        )

        service.calculate_compensation(order)

        # None of the order fields should be mutated by calculate_compensation
        assert order.wallet_credit == Decimal("0.00")
        assert order.free_coupon is False
        assert service.get_compensation(order.order_id) is None

    def test_apply_compensation_requires_delivered_status(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=7)

        for non_delivered_state in [
            OrderState.RECEIVED,
            OrderState.ALLOCATED,
            OrderState.PICKING,
            OrderState.READY_FOR_DISPATCH,
            OrderState.DISPATCHED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
        ]:
            order = make_order(
                promised_eta=t_eta,
                actual_delivery_time=t_actual,
                status=non_delivered_state,
            )
            with pytest.raises(ValueError, match="order must be in DELIVERED status"):
                service.apply_compensation(order)

            assert order.wallet_credit == Decimal("0.00")
            assert service.get_compensation(order.order_id) is None

    def test_missing_promised_eta_raises_value_error(self) -> None:
        service = CompensationService()
        t_actual = datetime(2026, 11, 4, 10, 15)
        order = make_order(actual_delivery_time=t_actual)
        order.promised_eta = None

        with pytest.raises(ValueError, match="has no promised_eta"):
            service.calculate_compensation(order)

    def test_missing_actual_delivery_time_raises_value_error(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        order = make_order(promised_eta=t_eta, actual_delivery_time=None)

        with pytest.raises(ValueError, match="has no actual_delivery_time"):
            service.calculate_compensation(order)

    def test_explicit_actual_delivery_time_argument_overrides_order_attribute(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        order = make_order(promised_eta=t_eta, actual_delivery_time=None)

        res = service.calculate_compensation(
            order, actual_delivery_time=t_eta + timedelta(minutes=6)
        )

        assert res.wallet_credit == Decimal("25.00")

    def test_timezone_naive_and_aware_mismatch_raises_type_error(self) -> None:
        service = CompensationService()
        t_naive = datetime(2026, 11, 4, 10, 10)
        t_aware = datetime(2026, 11, 4, 10, 20, tzinfo=timezone.utc)

        order = make_order(promised_eta=t_naive, actual_delivery_time=None)

        with pytest.raises(
            TypeError,
            match="Cannot compare timezone-aware actual_delivery_time with timezone-naive promised_eta",
        ):
            service.calculate_compensation(order, actual_delivery_time=t_aware)

    def test_invalid_order_argument_raises_type_error(self) -> None:
        service = CompensationService()
        with pytest.raises(TypeError, match="order must be an Order instance"):
            service.calculate_compensation(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="order must be an Order instance"):
            service.calculate_compensation(True)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="order must be an Order instance"):
            service.calculate_compensation("ORD-01")  # type: ignore[arg-type]

    def test_invalid_delivery_time_argument_raises_type_error(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        order = make_order(promised_eta=t_eta)

        with pytest.raises(TypeError, match="actual_delivery_time must be a datetime"):
            service.calculate_compensation(order, actual_delivery_time=True)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="actual_delivery_time must be a datetime"):
            service.calculate_compensation(order, actual_delivery_time="2026-11-04")  # type: ignore[arg-type]

    def test_invalid_order_id_in_get_compensation_raises_type_error(self) -> None:
        service = CompensationService()
        with pytest.raises(TypeError, match="order_id must be a string"):
            service.get_compensation(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="order_id must be a string"):
            service.get_compensation(True)  # type: ignore[arg-type]


class TestProtocolAndIntegration:
    """Tests covering protocol compliance, batch compensation, and weather hold integration."""

    def test_compensation_service_satisfies_protocol(self) -> None:
        service = CompensationService()
        assert isinstance(service, CompensationServiceInterface)

    def test_wallet_credit_and_coupon_recorded_separately(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_actual = t_eta + timedelta(minutes=12)
        order = make_order(promised_eta=t_eta, actual_delivery_time=t_actual)

        service.apply_compensation(order)

        # Confirm distinct separation per glossary note 3
        assert order.wallet_credit == Decimal("50.00")
        assert order.free_coupon is True
        assert isinstance(order.wallet_credit, Decimal)
        assert isinstance(order.free_coupon, bool)

    def test_weather_hold_extended_eta_evaluates_against_extended_promise(self) -> None:
        service = CompensationService()
        t0 = datetime(2026, 11, 4, 10, 0)
        # Original ETA was 10:10, but a 10-minute weather hold extended it to 10:20
        extended_eta = t0 + timedelta(minutes=20)
        t_actual = t0 + timedelta(minutes=20)  # Delivered at 10:20

        order = make_order(promised_eta=extended_eta, actual_delivery_time=t_actual)

        res = service.calculate_compensation(order)

        # Weather hold extension is not compensable lateness
        assert res.is_late is False
        assert res.wallet_credit == Decimal("0.00")
        assert res.free_coupon is False

    def test_process_batch_compensation_applies_to_all_orders_in_batch(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_delivered = t_eta + timedelta(minutes=6)

        o1 = make_order("ORD-01", promised_eta=t_eta)
        o2 = make_order("ORD-02", promised_eta=t_eta)
        o3 = make_order("ORD-03", promised_eta=t_eta, delayed_promise=True)

        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1, o2, o3],
            status=BatchStatus.DELIVERED,
        )

        results = service.process_batch_compensation(batch, delivered_at=t_delivered)

        assert len(results) == 3
        assert results[0].wallet_credit == Decimal("25.00")
        assert o1.wallet_credit == Decimal("25.00")

        assert results[1].wallet_credit == Decimal("25.00")
        assert o2.wallet_credit == Decimal("25.00")

        # o3 was delayed-promise and delivered 6m late -> beyond 5m grace -> ₹25
        assert results[2].wallet_credit == Decimal("25.00")
        assert o3.wallet_credit == Decimal("25.00")

        # Confirm all 3 in ledger
        assert service.get_compensation("ORD-01") is not None
        assert service.get_compensation("ORD-02") is not None
        assert service.get_compensation("ORD-03") is not None

    def test_process_batch_compensation_type_check(self) -> None:
        service = CompensationService()
        with pytest.raises(TypeError, match="batch must be a Batch instance"):
            service.process_batch_compensation("BATCH-01")  # type: ignore[arg-type]


class TestAtomicBatchCompensation:
    """Tests covering atomic batch validation, failure rollback, and idempotency."""

    def test_non_delivered_batch_statuses_rejected_without_modifying_orders(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_delivered = t_eta + timedelta(minutes=6)

        for non_delivered_status in [
            BatchStatus.CREATED,
            BatchStatus.DISPATCHED,
            BatchStatus.CANCELLED,
        ]:
            o1 = make_order("ORD-01", promised_eta=t_eta, wallet_credit=Decimal("10.00"))
            batch = Batch(
                batch_id="BATCH-01",
                rider_id="RIDER-01",
                created_at=t_eta - timedelta(minutes=15),
                orders=[o1],
                status=non_delivered_status,
            )

            with pytest.raises(ValueError, match="batch must be in DELIVERED status"):
                service.process_batch_compensation(batch, delivered_at=t_delivered)

            # Assert order is unchanged and ledger has no records
            assert o1.wallet_credit == Decimal("10.00")
            assert o1.free_coupon is False
            assert service._compensations == {}

    def test_batch_with_missing_promised_eta_leaves_all_orders_and_ledger_unchanged(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_delivered = t_eta + timedelta(minutes=6)

        # o1 is valid and eligible for ₹25
        o1 = make_order("ORD-01", promised_eta=t_eta, wallet_credit=Decimal("5.00"))
        # o2 has missing promised_eta
        o2 = make_order("ORD-02", promised_eta=None, wallet_credit=Decimal("0.00"))
        o2.promised_eta = None
        # o3 is valid
        o3 = make_order("ORD-03", promised_eta=t_eta, wallet_credit=Decimal("0.00"))

        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1, o2, o3],
            status=BatchStatus.DELIVERED,
        )

        with pytest.raises(ValueError, match="has no promised_eta"):
            service.process_batch_compensation(batch, delivered_at=t_delivered)

        # Assert no orders were mutated (o1 was NOT credited ₹25)
        assert o1.wallet_credit == Decimal("5.00")
        assert o2.wallet_credit == Decimal("0.00")
        assert o3.wallet_credit == Decimal("0.00")
        # Assert ledger is empty
        assert service._compensations == {}

    def test_batch_with_missing_actual_delivery_time_leaves_all_orders_and_ledger_unchanged(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)

        # o1 has actual_delivery_time (6m late)
        o1 = make_order(
            "ORD-01",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=6),
            wallet_credit=Decimal("5.00"),
        )
        # o2 has actual_delivery_time=None
        o2 = make_order("ORD-02", promised_eta=t_eta, actual_delivery_time=None)
        # o3 has actual_delivery_time (12m late)
        o3 = make_order(
            "ORD-03",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=12),
        )

        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1, o2, o3],
            status=BatchStatus.DELIVERED,
        )

        # Pass delivered_at=None so orders rely on their actual_delivery_time
        with pytest.raises(ValueError, match="has no actual_delivery_time"):
            service.process_batch_compensation(batch, delivered_at=None)

        # Assert no order received compensation
        assert o1.wallet_credit == Decimal("5.00")
        assert o1.free_coupon is False
        assert o2.wallet_credit == Decimal("0.00")
        assert o3.wallet_credit == Decimal("0.00")
        assert o3.free_coupon is False
        # Assert ledger is empty
        assert service._compensations == {}

    def test_batch_with_non_delivered_order_status_leaves_all_orders_and_ledger_unchanged(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        t_delivered = t_eta + timedelta(minutes=6)

        # o1 is DELIVERED
        o1 = make_order("ORD-01", promised_eta=t_eta, wallet_credit=Decimal("10.00"), status=OrderState.DELIVERED)
        # o2 is in DISPATCHED state
        o2 = make_order("ORD-02", promised_eta=t_eta, status=OrderState.DISPATCHED)
        # o3 is DELIVERED
        o3 = make_order("ORD-03", promised_eta=t_eta, status=OrderState.DELIVERED)

        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1, o2, o3],
            status=BatchStatus.DELIVERED,
        )

        with pytest.raises(ValueError, match="order must be in DELIVERED status"):
            service.process_batch_compensation(batch, delivered_at=t_delivered)

        # Assert o1 was NOT mutated
        assert o1.wallet_credit == Decimal("10.00")
        assert o2.wallet_credit == Decimal("0.00")
        assert o3.wallet_credit == Decimal("0.00")
        assert service._compensations == {}

    def test_successful_batch_compensation_and_idempotent_repetition(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)

        # o1: 6m late -> ₹25 wallet credit
        o1 = make_order(
            "ORD-01",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=6),
            wallet_credit=Decimal("10.00"),  # existing credit
        )
        # o2: 12m late -> ₹50 wallet credit + free coupon
        o2 = make_order(
            "ORD-02",
            promised_eta=t_eta,
            actual_delivery_time=t_eta + timedelta(minutes=12),
        )
        # o3: on time -> ₹0
        o3 = make_order(
            "ORD-03",
            promised_eta=t_eta,
            actual_delivery_time=t_eta,
        )

        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1, o2, o3],
            status=BatchStatus.DELIVERED,
        )

        # First call: succeeds and applies compensation
        results1 = service.process_batch_compensation(batch)

        assert len(results1) == 3
        # o1: 10 + 25 = 35
        assert o1.wallet_credit == Decimal("35.00")
        assert o1.free_coupon is False
        assert results1[0].wallet_credit == Decimal("25.00")

        # o2: 50 + coupon
        assert o2.wallet_credit == Decimal("50.00")
        assert o2.free_coupon is True
        assert results1[1].wallet_credit == Decimal("50.00")
        assert results1[1].free_coupon is True

        # o3: 0, no coupon
        assert o3.wallet_credit == Decimal("0.00")
        assert o3.free_coupon is False

        # All 3 in ledger
        assert "ORD-01" in service._compensations
        assert "ORD-02" in service._compensations
        assert "ORD-03" in service._compensations

        # Second call on same batch: MUST NOT double credit
        results2 = service.process_batch_compensation(batch)

        assert len(results2) == 3
        assert o1.wallet_credit == Decimal("35.00")  # NOT 60.00
        assert o2.wallet_credit == Decimal("50.00")  # NOT 100.00
        assert o3.wallet_credit == Decimal("0.00")
        assert results2[0] is results1[0]
        assert results2[1] is results1[1]
        assert results2[2] is results1[2]

    def test_batch_with_invalid_delivered_at_type_raises_type_error(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        o1 = make_order("ORD-01", promised_eta=t_eta)
        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1],
            status=BatchStatus.DELIVERED,
        )

        with pytest.raises(TypeError, match="delivered_at must be a datetime"):
            service.process_batch_compensation(batch, delivered_at=True)  # type: ignore[arg-type]

    def test_batch_with_non_order_item_raises_type_error(self) -> None:
        service = CompensationService()
        t_eta = datetime(2026, 11, 4, 10, 10)
        o1 = make_order("ORD-01", promised_eta=t_eta, actual_delivery_time=t_eta)
        batch = Batch(
            batch_id="BATCH-01",
            rider_id="RIDER-01",
            created_at=t_eta - timedelta(minutes=15),
            orders=[o1],
            status=BatchStatus.DELIVERED,
        )
        # Mutate batch.orders post-init to simulate non-Order object injection
        batch.orders.append("NOT-AN-ORDER")  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="Order in batch must be an Order instance"):
            service.process_batch_compensation(batch)
