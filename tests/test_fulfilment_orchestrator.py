from datetime import time
from decimal import Decimal

import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.product import Product
from ps031.models.store import Store
from ps031.services.fulfilment_orchestrator import FulfilmentOrchestrator
from ps031.services.inventory_service import InventoryService, ReservationStatus


@pytest.fixture
def setup_engine():
    original = Product(
        sku="SKU-ORIGINAL", name="Original", category="Dairy",
        brand="Brand-A", price=Decimal("100.00"),
    )
    substitute = Product(
        sku="SKU-SUB", name="Substitute", category="Dairy",
        brand="Brand-B", price=Decimal("105.00"),
    )
    store = Store(
        store_id="STORE-1", name="Store",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-ORIGINAL": 3, "SKU-SUB": 5},
    )
    inventory = InventoryService(stores=[store], products=[original, substitute])
    return store, inventory


def test_orchestrator_applies_actual_reservation_then_substitutes_shortfall(setup_engine):
    store, inventory = setup_engine
    order = Order(
        order_id="ORD-1", customer_id="C-1",
        items=[OrderItem(
            product_id="SKU-ORIGINAL", quantity=10,
            unit_price=Decimal("100.00"), category="Dairy", brand="Brand-A",
        )],
        substitution_preference=SubstitutionPolicy.ALLOW,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )
    original_result = inventory.reserve_stock("STORE-1", "ORD-1", "SKU-ORIGINAL", 10)
    original_reservation = inventory.get_reservation(original_result.reservation_id)

    orchestrator = FulfilmentOrchestrator(inventory)
    result = orchestrator.fulfil(order, [original_reservation])

    line = order.items[0]
    assert line.fulfilled_quantity == 8
    assert line.substituted_product_sku == "SKU-SUB"
    assert line.substituted_quantity == 5
    assert line.charged_unit_price == Decimal("100.00")
    assert result.evaluation.is_cancelled is False
    assert len(result.applied_reservation_ids) == 2
    assert inventory.get_reservation(original_reservation.reservation_id).status == ReservationStatus.COMMITTED
    substitute_id = next(
        rid for rid in result.applied_reservation_ids
        if inventory.get_reservation(rid).sku == "SKU-SUB"
    )
    assert inventory.get_reservation(substitute_id).status == ReservationStatus.COMMITTED
    assert store.inventory["SKU-ORIGINAL"] == 0
    assert store.inventory["SKU-SUB"] == 0


def test_orchestrator_retry_does_not_duplicate_order_line_updates(setup_engine):
    store, inventory = setup_engine
    order = Order(
        order_id="ORD-2", customer_id="C-2",
        items=[OrderItem(
            product_id="SKU-ORIGINAL", quantity=3,
            unit_price=Decimal("100.00"), category="Dairy", brand="Brand-A",
        )],
        substitution_preference=SubstitutionPolicy.NONE,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )
    result = inventory.reserve_stock("STORE-1", "ORD-2", "SKU-ORIGINAL", 3)
    reservation = inventory.get_reservation(result.reservation_id)
    orchestrator = FulfilmentOrchestrator(inventory)

    first = orchestrator.fulfil(order, [reservation])
    second = orchestrator.fulfil(order, [reservation])

    assert order.items[0].fulfilled_quantity == 3
    assert first.newly_applied_reservation_ids == (reservation.reservation_id,)
    assert second.newly_applied_reservation_ids == ()
    assert second.applied_reservation_ids == (reservation.reservation_id,)
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.COMMITTED


def test_orchestrator_hands_below_threshold_order_to_cancellation_service():
    product = Product(
        sku="SKU-1", name="Item", category="Grocery",
        brand="Brand", price=Decimal("10.00"),
    )
    store = Store(
        store_id="STORE-2", name="Store",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    inventory = InventoryService(stores=[store], products=[product])
    order = Order(
        order_id="ORD-3", customer_id="C-3",
        items=[OrderItem(
            product_id="SKU-1", quantity=10, unit_price=Decimal("10.00"),
        )],
        substitution_preference=SubstitutionPolicy.NONE,
        status=OrderState.PICKING,
        allocated_store_id="STORE-2",
    )
    reserved = inventory.reserve_stock("STORE-2", "ORD-3", "SKU-1", 5)
    reservation = inventory.get_reservation(reserved.reservation_id)

    result = FulfilmentOrchestrator(inventory).fulfil(order, [reservation])

    assert result.evaluation.is_cancelled is True
    assert result.cancellation_result is not None
    assert order.status == OrderState.CANCELLED
    assert order.refund_amount == Decimal("100.00")
    assert store.inventory["SKU-1"] == 10
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.RELEASED



def test_orchestrator_resumes_substitute_reservation_after_line_update_failure(setup_engine, monkeypatch):
    store, inventory = setup_engine
    order = Order(
        order_id="ORD-4", customer_id="C-4",
        items=[OrderItem(
            product_id="SKU-ORIGINAL", quantity=10,
            unit_price=Decimal("100.00"), category="Dairy", brand="Brand-A",
        )],
        substitution_preference=SubstitutionPolicy.ALLOW,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )
    reserved = inventory.reserve_stock("STORE-1", "ORD-4", "SKU-ORIGINAL", 3)
    original_reservation = inventory.get_reservation(reserved.reservation_id)
    orchestrator = FulfilmentOrchestrator(inventory)
    original_apply = orchestrator._apply_reservation
    failed = {"once": False}

    def fail_first_substitute_update(order_arg, item_arg, reservation_arg, kind, newly_applied):
        if kind == "substitute" and not failed["once"]:
            failed["once"] = True
            raise RuntimeError("simulated line update failure")
        return original_apply(order_arg, item_arg, reservation_arg, kind, newly_applied)

    monkeypatch.setattr(orchestrator, "_apply_reservation", fail_first_substitute_update)
    with pytest.raises(RuntimeError, match="simulated line update failure"):
        orchestrator.fulfil(order, [original_reservation])

    # The substitute reservation exists, but its line update was not marked applied.
    substitute_reservations = [
        reservation for reservation in inventory.reservations.values()
        if reservation.order_id == "ORD-4" and reservation.sku == "SKU-SUB"
    ]
    assert len(substitute_reservations) == 1
    assert substitute_reservations[0].reservation_id not in orchestrator.applied_reservation_ids

    monkeypatch.setattr(orchestrator, "_apply_reservation", original_apply)
    result = orchestrator.fulfil(order, [original_reservation])

    assert order.items[0].fulfilled_quantity == 8
    assert order.items[0].substituted_quantity == 5
    assert len([
        reservation for reservation in inventory.reservations.values()
        if reservation.order_id == "ORD-4" and reservation.sku == "SKU-SUB"
    ]) == 1
    assert len(result.applied_reservation_ids) == 2


def test_substitute_sku_matching_another_line_original_sku_on_retry():
    sku_a = Product(
        sku="SKU-A", name="Product A", category="Dairy",
        brand="Brand-X", price=Decimal("10.00"),
    )
    sku_b = Product(
        sku="SKU-B", name="Product B", category="Dairy",
        brand="Brand-X", price=Decimal("10.00"),
    )
    store = Store(
        store_id="STORE-1", name="Store 1",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-A": 2, "SKU-B": 5},
    )
    inventory = InventoryService(stores=[store], products=[sku_a, sku_b])
    order = Order(
        order_id="ORD-CROSS", customer_id="C-CROSS",
        items=[
            OrderItem(
                product_id="SKU-A", quantity=5, unit_price=Decimal("10.00"),
                category="Dairy", brand="Brand-X",
            ),
            OrderItem(
                product_id="SKU-B", quantity=2, unit_price=Decimal("10.00"),
                category="Dairy", brand="Brand-X",
            ),
        ],
        substitution_preference=SubstitutionPolicy.ALLOW,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )

    # Initial original reservations: Line 1 partially fulfilled (2/5), Line 2 fully fulfilled (2/2)
    res_a_result = inventory.reserve_stock("STORE-1", "ORD-CROSS", "SKU-A", 5)
    res_b_result = inventory.reserve_stock("STORE-1", "ORD-CROSS", "SKU-B", 2)
    res_a = inventory.get_reservation(res_a_result.reservation_id)
    res_b = inventory.get_reservation(res_b_result.reservation_id)

    orchestrator = FulfilmentOrchestrator(inventory)
    first_result = orchestrator.fulfil(order, [res_a, res_b])

    # Line 1 shortfall of 3 was substituted by SKU-B (which is Line 2's original SKU)
    assert order.items[0].fulfilled_quantity == 5
    assert order.items[0].substituted_quantity == 3
    assert order.items[0].substituted_product_sku == "SKU-B"
    assert order.items[1].fulfilled_quantity == 2
    assert order.items[1].substituted_quantity == 0
    assert order.items[1].substituted_product_sku is None
    assert len(first_result.applied_reservation_ids) == 3

    # On retry, Line 1's substitute reservation (SKU-B) must NOT be matched to Line 2 as original stock
    second_result = orchestrator.fulfil(order, [res_a, res_b])

    assert second_result.newly_applied_reservation_ids == ()
    assert len(second_result.applied_reservation_ids) == 3
    # Line 2 quantity must remain exactly 2, not over-fulfilled or mutated by Line 1's substitute
    assert order.items[1].fulfilled_quantity == 2
    assert order.items[1].substituted_quantity == 0
    assert order.items[1].substituted_product_sku is None
    # Line 1 remains intact
    assert order.items[0].fulfilled_quantity == 5
    assert order.items[0].substituted_quantity == 3
    assert order.items[0].substituted_product_sku == "SKU-B"


def test_orchestrator_rejects_reservation_store_mismatch_with_allocated_store():
    product = Product(
        sku="SKU-1", name="Product 1", category="Dairy",
        brand="Brand", price=Decimal("10.00"),
    )
    store1 = Store(
        store_id="STORE-1", name="Store 1",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    store2 = Store(
        store_id="STORE-2", name="Store 2",
        location=Coordinates(latitude=12.98, longitude=77.60),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    inventory = InventoryService(stores=[store1, store2], products=[product])
    order = Order(
        order_id="ORD-STORE-MISMATCH", customer_id="C-1",
        items=[OrderItem(
            product_id="SKU-1", quantity=5, unit_price=Decimal("10.00"),
            category="Dairy", brand="Brand",
        )],
        substitution_preference=SubstitutionPolicy.NONE,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )
    # Reservation created at STORE-2, but order is allocated to STORE-1
    res = inventory.reserve_stock("STORE-2", "ORD-STORE-MISMATCH", "SKU-1", 5)
    reservation = inventory.get_reservation(res.reservation_id)

    orchestrator = FulfilmentOrchestrator(inventory)
    with pytest.raises(ValueError, match="allocated to store 'STORE-1'"):
        orchestrator.fulfil(order, [reservation])

    assert order.items[0].fulfilled_quantity == 0
    assert inventory.get_reservation(reservation.reservation_id).status == ReservationStatus.PENDING


def test_orchestrator_rejects_order_without_allocated_store():
    product = Product(
        sku="SKU-1", name="Product 1", category="Dairy",
        brand="Brand", price=Decimal("10.00"),
    )
    store1 = Store(
        store_id="STORE-1", name="Store 1",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    inventory = InventoryService(stores=[store1], products=[product])
    order = Order(
        order_id="ORD-NO-STORE", customer_id="C-1",
        items=[OrderItem(
            product_id="SKU-1", quantity=5, unit_price=Decimal("10.00"),
            category="Dairy", brand="Brand",
        )],
        substitution_preference=SubstitutionPolicy.NONE,
        status=OrderState.PICKING,
        allocated_store_id=None,
    )
    res = inventory.reserve_stock("STORE-1", "ORD-NO-STORE", "SKU-1", 5)
    reservation = inventory.get_reservation(res.reservation_id)

    orchestrator = FulfilmentOrchestrator(inventory)
    with pytest.raises(ValueError, match="allocated store"):
        orchestrator.fulfil(order, [reservation])


def test_invalid_store_reservation_rejected_and_retry_with_valid_succeeds():
    product = Product(
        sku="SKU-1", name="Product 1", category="Dairy",
        brand="Brand", price=Decimal("10.00"),
    )
    store1 = Store(
        store_id="STORE-1", name="Store 1",
        location=Coordinates(latitude=12.97, longitude=77.59),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    store2 = Store(
        store_id="STORE-2", name="Store 2",
        location=Coordinates(latitude=12.98, longitude=77.60),
        operating_hours=OperatingHours(open_time=time(6), close_time=time(23)),
        inventory={"SKU-1": 10},
    )
    inventory = InventoryService(stores=[store1, store2], products=[product])
    order = Order(
        order_id="ORD-RETRY-STORE", customer_id="C-1",
        items=[OrderItem(
            product_id="SKU-1", quantity=5, unit_price=Decimal("10.00"),
            category="Dairy", brand="Brand",
        )],
        substitution_preference=SubstitutionPolicy.NONE,
        status=OrderState.PICKING,
        allocated_store_id="STORE-1",
    )
    invalid_res_result = inventory.reserve_stock("STORE-2", "ORD-RETRY-STORE", "SKU-1", 5)
    invalid_res = inventory.get_reservation(invalid_res_result.reservation_id)

    valid_res_result = inventory.reserve_stock("STORE-1", "ORD-RETRY-STORE", "SKU-1", 5)
    valid_res = inventory.get_reservation(valid_res_result.reservation_id)

    orchestrator = FulfilmentOrchestrator(inventory)

    # First attempt: invalid reservation belongs to STORE-2, order allocated to STORE-1
    with pytest.raises(ValueError, match="allocated to store 'STORE-1'"):
        orchestrator.fulfil(order, [invalid_res])

    # Ensure invalid reservation was not inserted into orchestrator's internal tracking
    assert "ORD-RETRY-STORE" not in orchestrator._reservations_by_order or (
        invalid_res.reservation_id not in orchestrator._reservations_by_order["ORD-RETRY-STORE"]
    )
    assert order.items[0].fulfilled_quantity == 0

    # Second attempt: retry with the valid reservation must succeed
    result = orchestrator.fulfil(order, [valid_res])

    assert result.evaluation.is_cancelled is False
    assert order.items[0].fulfilled_quantity == 5
    assert valid_res.reservation_id in result.applied_reservation_ids
    assert inventory.get_reservation(valid_res.reservation_id).status == ReservationStatus.COMMITTED


