from datetime import datetime
from decimal import Decimal
import pytest

from ps031.models.order import (
    Order,
    OrderItem,
    OrderStatus,
    SubstitutionPolicy,
)
from ps031.services.fulfilment_service import (
    FulfilmentEvaluation,
    FulfilmentService,
)


@pytest.fixture
def service() -> FulfilmentService:
    return FulfilmentService()


def test_fulfilled_value_exactly_60_percent_does_not_cancel(
    service: FulfilmentService,
):
    """
    EC-05: Fulfilled value exactly 60% of original order value.
    BR-06: Cancellation triggers only if fulfilled value drops below 60% (< 0.60).
    At exactly 60.0%, the order is NOT cancelled.
    """
    original_value = Decimal("500.00")
    fulfilled_value = Decimal("300.00")  # exactly 300 / 500 = 0.60 (60.0%)
    delivery_fee = Decimal("25.00")

    result = service.evaluate_fulfilment(
        original_value=original_value,
        fulfilled_value=fulfilled_value,
        delivery_fee=delivery_fee,
    )

    assert result.is_cancelled is False
    assert result.fulfilled_ratio == Decimal("0.60")
    assert result.delivery_fee_charged == Decimal("25.00")
    # Refund only the unfulfilled shortfall items (500 - 300 = 200)
    assert result.refund_amount == Decimal("200.00")


def test_fulfilled_value_59_point_9_percent_triggers_cancellation(
    service: FulfilmentService,
):
    """
    BR-06: Fulfilled value strictly below 60% (e.g. 59.9%) triggers cancellation before dispatch.
    Customer receives full refund and no delivery fee is retained.
    """
    original_value = Decimal("500.00")
    fulfilled_value = Decimal("299.50")  # 299.50 / 500 = 0.599 (59.9%)
    delivery_fee = Decimal("25.00")

    result = service.evaluate_fulfilment(
        original_value=original_value,
        fulfilled_value=fulfilled_value,
        delivery_fee=delivery_fee,
    )

    assert result.is_cancelled is True
    assert result.fulfilled_ratio == Decimal("0.599")
    # Full refund of original value + delivery fee
    assert result.refund_amount == Decimal("525.00")
    # No delivery fee charged on cancellation
    assert result.delivery_fee_charged == Decimal("0.00")


def test_fulfilled_value_above_60_percent_proceeds(
    service: FulfilmentService,
):
    """
    BR-06: Fulfilled value above 60% (e.g. 80%) proceeds normally.
    """
    original_value = Decimal("500.00")
    fulfilled_value = Decimal("400.00")  # 400 / 500 = 0.80 (80%)
    delivery_fee = Decimal("25.00")

    result = service.evaluate_fulfilment(
        original_value=original_value,
        fulfilled_value=fulfilled_value,
        delivery_fee=delivery_fee,
    )

    assert result.is_cancelled is False
    assert result.fulfilled_ratio == Decimal("0.80")
    assert result.delivery_fee_charged == Decimal("25.00")
    assert result.refund_amount == Decimal("100.00")


def test_zero_original_value_handled_safely(
    service: FulfilmentService,
):
    """
    Safely handles zero original order value without division by zero.
    Cancels order since 0 items cannot be fulfilled to 60%, retaining Rs 0 delivery fee.
    """
    result = service.evaluate_fulfilment(
        original_value=Decimal("0.00"),
        fulfilled_value=Decimal("0.00"),
        delivery_fee=Decimal("25.00"),
    )

    assert result.is_cancelled is True
    assert result.fulfilled_ratio == Decimal("0.00")
    assert result.delivery_fee_charged == Decimal("0.00")
    assert result.refund_amount == Decimal("25.00")


def test_negative_monetary_inputs_rejected_with_value_error(
    service: FulfilmentService,
):
    """
    Validates that negative monetary inputs are rejected with ValueError
    and not silently accepted as valid orders.
    """
    with pytest.raises(ValueError, match="original_value cannot be negative"):
        service.evaluate_fulfilment(
            original_value=Decimal("-10.00"),
            fulfilled_value=Decimal("10.00"),
        )

    with pytest.raises(ValueError, match="fulfilled_value cannot be negative"):
        service.evaluate_fulfilment(
            original_value=Decimal("10.00"),
            fulfilled_value=Decimal("-5.00"),
        )

    with pytest.raises(ValueError, match="delivery_fee cannot be negative"):
        service.evaluate_fulfilment(
            original_value=Decimal("10.00"),
            fulfilled_value=Decimal("10.00"),
            delivery_fee=Decimal("-25.00"),
        )

    with pytest.raises(ValueError, match="amount_paid cannot be negative"):
        service.evaluate_fulfilment(
            original_value=Decimal("10.00"),
            fulfilled_value=Decimal("10.00"),
            amount_paid=Decimal("-100.00"),
        )


def test_cancellation_full_refund_includes_surge_fee(
    service: FulfilmentService,
):
    """
    BR-06: When order drops below 60%, full refund includes base fee + surge fee,
    and no delivery fee is retained.
    """
    original_value = Decimal("200.00")
    fulfilled_value = Decimal("100.00")  # 100 / 200 = 50% (< 60%)
    total_delivery_fee = Decimal("40.00")  # Rs 25 base + Rs 15 surge

    result = service.evaluate_fulfilment(
        original_value=original_value,
        fulfilled_value=fulfilled_value,
        delivery_fee=total_delivery_fee,
    )

    assert result.is_cancelled is True
    # 200 + 40 = 240
    assert result.refund_amount == Decimal("240.00")
    assert result.delivery_fee_charged == Decimal("0.00")


def test_cancellation_with_explicit_amount_paid(
    service: FulfilmentService,
):
    """
    BR-06: Verifies refund when explicit customer payment amount is provided.
    """
    original_value = Decimal("500.00")
    fulfilled_value = Decimal("250.00")  # 50%
    amount_paid = Decimal("525.00")

    result = service.evaluate_fulfilment(
        original_value=original_value,
        fulfilled_value=fulfilled_value,
        delivery_fee=Decimal("25.00"),
        amount_paid=amount_paid,
    )

    assert result.is_cancelled is True
    assert result.refund_amount == Decimal("525.00")
    assert result.delivery_fee_charged == Decimal("0.00")


def test_evaluate_order_integration_with_order_model(
    service: FulfilmentService,
):
    """
    Verifies evaluate_order() directly against Order and OrderItem models.
    """
    item1 = OrderItem(
        sku_id="SKU_1",
        category="Snacks",
        brand="BrandA",
        unit_price=Decimal("100.00"),
        requested_qty=3,
        fulfilled_qty=3,
        charged_unit_price=Decimal("100.00"),
    )  # line value = 300.00
    item2 = OrderItem(
        sku_id="SKU_2",
        category="Beverages",
        brand="BrandB",
        unit_price=Decimal("100.00"),
        requested_qty=2,
        fulfilled_qty=0,  # line value = 0.00
    )

    order = Order(
        order_id="ORD_001",
        customer_id="CUST_001",
        customer_lat=12.9716,
        customer_lon=77.5946,
        order_time=datetime(2026, 10, 30, 10, 0, 0),
        items=[item1, item2],
        substitution_preference=SubstitutionPolicy.NONE,
        delivery_fee=Decimal("0.00"),
        surge_fee=Decimal("15.00"),
    )

    # original = 300 + 200 = 500.00
    # fulfilled = 300.00 (60.0%)
    assert order.original_total_value == Decimal("500.00")
    assert order.fulfilled_total_value == Decimal("300.00")
    assert order.total_delivery_fee == Decimal("15.00")

    result = service.evaluate_order(order)

    assert result.is_cancelled is False
    assert result.fulfilled_ratio == Decimal("0.60")
    assert result.delivery_fee_charged == Decimal("15.00")
    assert result.refund_amount == Decimal("200.00")
