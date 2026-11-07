from datetime import datetime, time
from decimal import Decimal

import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem
from ps031.models.order_state import OrderState
from ps031.models.product import Product
from ps031.models.store import Store
from ps031.services.fulfilment_cancellation_service import FulfilmentCancellationService
from ps031.services.inventory_service import InventoryService, ReservationStatus


@pytest.fixture
def cancellation_setup():
    product = Product(
        sku="SKU-1",
        name="Product 1",
        category="Grocery",
        brand="Brand",
        price=Decimal("10.00"),
    )
    store = Store(
        store_id="STORE-1",
        name="Store 1",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    inventory = InventoryService(stores=[store], products=[product])
    reservation_result = inventory.reserve_stock("STORE-1", "ORDER-1", "SKU-1", 4)
    reservation = inventory.get_reservation(reservation_result.reservation_id)
    order = Order(
        order_id="ORDER-1",
        customer_id="CUSTOMER-1",
        items=[
            OrderItem(
                product_id="SKU-1",
                quantity=10,
                unit_price=Decimal("10.00"),
                fulfilled_quantity=5,
            )
        ],
        status=OrderState.ALLOCATED,
    )
    service = FulfilmentCancellationService(inventory)
    return order, store, inventory, reservation, service


def test_br06_cancellation_releases_pending_reservations_and_records_refund_once(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup
    assert store.inventory["SKU-1"] == 6

    result = service.cancel(order, [reservation], "below fulfilment threshold")

    assert result.is_cancelled is True
    assert order.status == OrderState.CANCELLED
    assert result.released_reservation_ids == (reservation.reservation_id,)
    assert store.inventory["SKU-1"] == 10
    assert result.refund_amount == Decimal("100.00")
    assert order.refund_amount == Decimal("100.00")
    assert service.refund_ledger == {"ORDER-1": Decimal("100.00")}


def test_repeated_cancellation_does_not_release_or_record_refund_twice(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup

    first = service.cancel(order, [reservation], "below threshold")
    second = service.cancel(order, [reservation], "retry")

    assert first.refund_amount == second.refund_amount == Decimal("100.00")
    assert len(service.refund_ledger) == 1
    assert store.inventory["SKU-1"] == 10
    assert order.status == OrderState.CANCELLED


def test_already_released_reservation_is_not_released_again(cancellation_setup):
    order, store, inventory, reservation, service = cancellation_setup
    inventory.release_reservation(reservation.reservation_id)

    result = service.cancel(order, [reservation], "below threshold")

    assert result.already_released_reservation_ids == (reservation.reservation_id,)
    assert result.released_reservation_ids == ()
    assert store.inventory["SKU-1"] == 10


def test_committed_reservation_is_not_released(cancellation_setup):
    order, store, inventory, reservation, service = cancellation_setup
    inventory.commit_reservation(reservation.reservation_id)

    result = service.cancel(order, [reservation], "below threshold")

    assert result.skipped_committed_reservation_ids == (reservation.reservation_id,)
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.COMMITTED
    assert store.inventory["SKU-1"] == 6


def test_order_at_exactly_sixty_percent_is_not_cancelled(cancellation_setup):
    order, store, inventory, reservation, service = cancellation_setup
    order.items[0].fulfilled_quantity = 6

    result = service.cancel(order, [reservation], "threshold evaluation")

    assert result.is_cancelled is False
    assert order.status == OrderState.ALLOCATED
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.PENDING
    assert store.inventory["SKU-1"] == 6
    assert service.refund_ledger == {}


def test_dispatched_order_cannot_be_cancelled_under_br06(cancellation_setup):
    order, store, inventory, reservation, service = cancellation_setup
    order.status = OrderState.DISPATCHED

    with pytest.raises(ValueError, match="only permitted before dispatch"):
        service.cancel(order, [reservation], "late cancellation")

    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.PENDING
    assert store.inventory["SKU-1"] == 6
    assert service.refund_ledger == {}


def test_reservation_for_another_order_is_rejected(cancellation_setup):
    order, store, inventory, reservation, service = cancellation_setup
    reservation.order_id = "OTHER-ORDER"

    with pytest.raises(ValueError, match="belongs to order"):
        service.cancel(order, [reservation], "mismatched reservation")

    assert order.status == OrderState.ALLOCATED
    assert service.refund_ledger == {}


def test_already_cancelled_order_with_pending_reservations_releases_reservations_without_repeating_transition(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup
    order.status = OrderState.CANCELLED
    assert store.inventory["SKU-1"] == 6
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.PENDING

    # First call: order is already cancelled; eligible pending reservations must be released,
    # refund recorded once in ledger, and no lifecycle transition repeated.
    first = service.cancel(order, [reservation], "already cancelled upstream")

    assert first.is_cancelled is True
    assert order.status == OrderState.CANCELLED
    assert first.released_reservation_ids == (reservation.reservation_id,)
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.RELEASED
    assert store.inventory["SKU-1"] == 10
    assert first.refund_amount == Decimal("100.00")
    assert service.refund_ledger == {"ORDER-1": Decimal("100.00")}

    # Repeated call: idempotent, does not release or record again
    second = service.cancel(order, [reservation], "retry")
    assert second.is_cancelled is True
    assert second.refund_amount == Decimal("100.00")
    assert len(service.refund_ledger) == 1
    assert store.inventory["SKU-1"] == 10


def test_br06_cancellation_refunds_item_value_and_delivery_and_surge_fees(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup
    order.delivery_fee = Decimal("5.00")
    order.surge_fee = Decimal("3.00")

    result = service.cancel(order, [reservation], "below fulfilment threshold")

    assert result.is_cancelled is True
    assert result.refund_amount == Decimal("108.00")
    assert result.evaluation.delivery_fee_charged == Decimal("0.00")
    assert order.refund_amount == Decimal("108.00")
    assert service.refund_ledger["ORDER-1"] == Decimal("108.00")


def test_br06_cancellation_refunds_exact_explicit_amount_paid(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup
    order.delivery_fee = Decimal("5.00")
    order.surge_fee = Decimal("3.00")

    result = service.cancel(
        order,
        [reservation],
        "below fulfilment threshold",
        amount_paid=Decimal("112.50"),
    )

    assert result.is_cancelled is True
    assert result.refund_amount == Decimal("112.50")
    assert result.evaluation.refund_amount == Decimal("112.50")
    assert result.evaluation.delivery_fee_charged == Decimal("0.00")
    assert order.refund_amount == Decimal("112.50")
    assert service.refund_ledger["ORDER-1"] == Decimal("112.50")


def test_br06_non_cancelled_order_refunds_unfulfilled_value_after_collected_fees(
    cancellation_setup,
):
    order, store, inventory, reservation, service = cancellation_setup
    order.items[0].fulfilled_quantity = 6
    order.delivery_fee = Decimal("5.00")
    order.surge_fee = Decimal("3.00")

    result = service.cancel(
        order,
        [reservation],
        "threshold evaluation",
        amount_paid=Decimal("108.00"),
    )

    assert result.is_cancelled is False
    assert result.evaluation.fulfilled_ratio == Decimal("0.6")
    assert result.evaluation.delivery_fee_charged == Decimal("8.00")
    assert result.refund_amount == Decimal("40.00")
    assert order.status == OrderState.ALLOCATED
    assert order.refund_amount == Decimal("0.00")
    assert service.refund_ledger == {}
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.PENDING
    assert store.inventory["SKU-1"] == 6
