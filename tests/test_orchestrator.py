"""End-to-end multi-service orchestration tests for ApplicationOrchestrator."""

from datetime import datetime, time, timedelta
from decimal import Decimal
import pytest

from ps031.command_processor import JSONLCommandProcessor
from ps031.models.batch import BatchStatus
from ps031.models.order_state import OrderState
from ps031.models.rider import RiderStatus
from ps031.orchestrator import ApplicationOrchestrator, ReservationRollbackError
from ps031.services.inventory_service import ReservationStatus


@pytest.fixture
def orchestrator() -> ApplicationOrchestrator:
    return ApplicationOrchestrator()


def test_end_to_end_order_lifecycle(orchestrator: ApplicationOrchestrator):
    """Verify complete order lifecycle: registration -> allocation -> picking -> fulfilment -> batching -> dispatch -> delivery -> penalties -> trace."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)

    # 1. Register Product
    prod_res = orchestrator.register_product(
        sku="SKU-MILK-1",
        name="Amul Taaza Fresh Milk",
        category="Dairy",
        brand="Amul",
        price="50.00",
    )
    assert prod_res["sku"] == "SKU-MILK-1"
    assert prod_res["price"] == Decimal("50.00")

    # 2. Register Store
    store_res = orchestrator.register_store(
        store_id="STORE-BLR-01",
        name="Indiranagar Dark Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=15,
        active_riders=5,
        inventory={"SKU-MILK-1": 100},
    )
    assert store_res["store_id"] == "STORE-BLR-01"
    assert store_res["inventory_sku_count"] == 1

    # 3. Register Rider
    rider_res = orchestrator.register_rider(
        rider_id="RIDER-01",
        name="Suresh Kumar",
        status=RiderStatus.AVAILABLE,
    )
    assert rider_res["rider_id"] == "RIDER-01"
    assert rider_res["status"] == "AVAILABLE"

    # 4. Submit Order
    order_res = orchestrator.submit_order(
        order_id="ORD-E2E-01",
        customer_id="CUST-100",
        customer_lat=12.9750,
        customer_lon=77.5950,
        items=[{"sku_id": "SKU-MILK-1", "quantity": 2}],
        order_time=base_time,
    )
    assert order_res["order_id"] == "ORD-E2E-01"
    assert order_res["status"] == OrderState.ALLOCATED.value
    assert order_res["allocated_store_id"] == "STORE-BLR-01"
    assert order_res["promised_eta"] is not None
    assert order_res["order_value"] == Decimal("100.00")

    # Inventory reserved
    assert orchestrator.inventory_service.get_stock("STORE-BLR-01", "SKU-MILK-1") == 98

    # 5. Start Picking
    picking_res = orchestrator.start_picking("ORD-E2E-01")
    assert picking_res["status"] == OrderState.PICKING.value

    # 6. Fulfil Order
    fulfil_res = orchestrator.fulfil_order("ORD-E2E-01", amount_paid="135.00")
    assert fulfil_res["status"] == OrderState.READY_FOR_DISPATCH.value
    assert fulfil_res["is_cancelled"] is False
    assert fulfil_res["fulfilled_value"] == Decimal("100.00")

    # 7. Create Batch
    batch_res = orchestrator.create_batch(
        batch_id="BATCH-E2E-01",
        store_id="STORE-BLR-01",
        rider_id="RIDER-01",
        created_at=base_time + timedelta(minutes=1),
    )
    assert batch_res["batch_id"] == "BATCH-E2E-01"

    # 8. Add Order to Batch
    add_res = orchestrator.add_order_to_batch(
        batch_id="BATCH-E2E-01",
        order_id="ORD-E2E-01",
        current_time=base_time + timedelta(minutes=2),
    )
    assert add_res["is_compatible"] is True
    assert "ORD-E2E-01" in add_res["orders_in_batch"]

    # 9. Dispatch Batch (after 3-minute waiting timer: 2 + 3 = 5)
    dispatch_res = orchestrator.dispatch_batch(
        batch_id="BATCH-E2E-01",
        dispatched_at=base_time + timedelta(minutes=5),
    )
    assert dispatch_res["batch_id"] == "BATCH-E2E-01"
    assert orchestrator.orders["ORD-E2E-01"].status == OrderState.DISPATCHED

    # 10. Complete Order Delivery
    deliv_time = base_time + timedelta(minutes=8)
    deliv_res = orchestrator.complete_order_delivery(
        batch_id="BATCH-E2E-01",
        order_id="ORD-E2E-01",
        delivered_at=deliv_time,
    )
    assert deliv_res["order_id"] == "ORD-E2E-01"
    assert orchestrator.orders["ORD-E2E-01"].status == OrderState.DELIVERED
    assert deliv_res["wallet_credit"] == Decimal("25.00")

    # 11. Evaluate Rider Penalties
    penalty_res = orchestrator.evaluate_rider_penalties(
        batch_id="BATCH-E2E-01",
        delivered_at=deliv_time,
    )
    assert penalty_res["late_orders_count"] == 1
    assert penalty_res["bonus_lost"] is False
    assert penalty_res["batch_bonus_awarded"] == Decimal("20.00")
    assert penalty_res["total_applied_deduction"] == Decimal("0.00")

    # 12. Get Order Trace
    trace_res = orchestrator.get_order_trace("ORD-E2E-01")
    assert trace_res["order_id"] == "ORD-E2E-01"
    assert trace_res["status"] == OrderState.DELIVERED.value
    assert trace_res["allocated_store_id"] == "STORE-BLR-01"
    assert trace_res["order_value"] == Decimal("100.00")
    assert trace_res["fulfilled_value"] == Decimal("100.00")
    assert trace_res["actual_delivery_time"] == deliv_time.isoformat()


def test_allocation_distance_rejection(orchestrator: ApplicationOrchestrator):
    """Orders farther than 3.0 km cannot be allocated and transition to REJECTED."""
    orchestrator.register_product(
        sku="SKU-CHIPS",
        name="Potato Chips",
        category="Snacks",
        brand="Lays",
        price="20.00",
    )
    orchestrator.register_store(
        store_id="STORE-LOCAL",
        name="Local Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-CHIPS": 50},
    )

    # Customer at 13.0500, 77.5946 (> 8 km away)
    order_res = orchestrator.submit_order(
        order_id="ORD-DIST-REJECT",
        customer_id="CUST-FAR",
        customer_lat=13.0500,
        customer_lon=77.5946,
        items=[{"sku_id": "SKU-CHIPS", "quantity": 1}],
        order_time=datetime(2026, 11, 6, 12, 0, 0),
    )

    assert order_res["status"] == OrderState.REJECTED.value
    assert order_res["allocated_store_id"] is None
    assert order_res["rejection_reason"] is not None
    assert orchestrator.orders["ORD-DIST-REJECT"].status == OrderState.REJECTED


def test_allocation_operating_hours_rejection(orchestrator: ApplicationOrchestrator):
    """Orders submitted outside dark store operating hours are REJECTED."""
    orchestrator.register_product(
        sku="SKU-BREAD",
        name="Brown Bread",
        category="Bakery",
        brand="Modern",
        price="35.00",
    )
    orchestrator.register_store(
        store_id="STORE-DAYTIME",
        name="Daytime Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "08:00:00", "close_time": "22:00:00"},
        inventory={"SKU-BREAD": 50},
    )

    # Placed at 05:00 AM
    order_res = orchestrator.submit_order(
        order_id="ORD-NIGHT-REJECT",
        customer_id="CUST-NIGHT",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-BREAD", "quantity": 1}],
        order_time=datetime(2026, 11, 6, 5, 0, 0),
    )

    assert order_res["status"] == OrderState.REJECTED.value
    assert order_res["allocated_store_id"] is None


def test_br06_auto_cancellation_under_threshold(orchestrator: ApplicationOrchestrator):
    """When fulfilled value is < 60% of original, BR-06 auto-cancels order with refund."""
    orchestrator.register_product(
        sku="SKU-JUICE",
        name="Mango Juice",
        category="Beverages",
        brand="Real",
        price="100.00",
    )
    # Store has 0 stock of SKU-JUICE
    orchestrator.register_store(
        store_id="STORE-EMPTY",
        name="Stockout Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-JUICE": 0},
    )

    order_res = orchestrator.submit_order(
        order_id="ORD-BR06-CANCEL",
        customer_id="CUST-200",
        customer_lat=12.9720,
        customer_lon=77.5950,
        items=[{"sku_id": "SKU-JUICE", "quantity": 2}],
        order_time=datetime(2026, 11, 6, 11, 0, 0),
    )
    assert order_res["status"] == OrderState.ALLOCATED.value

    orchestrator.start_picking("ORD-BR06-CANCEL")
    fulfil_res = orchestrator.fulfil_order("ORD-BR06-CANCEL", amount_paid="235.00")

    assert fulfil_res["is_cancelled"] is True
    assert fulfil_res["fulfilled_ratio"] < Decimal("0.60")
    assert orchestrator.orders["ORD-BR06-CANCEL"].status == OrderState.CANCELLED
    assert fulfil_res["refund_amount"] > Decimal("0.00")
    assert fulfil_res["delivery_fee_charged"] is False


def test_weather_hold_propagation_br11(orchestrator: ApplicationOrchestrator):
    """Applying weather hold pauses dark store and extends allocated order ETAs by 10 minutes."""
    base_time = datetime(2026, 11, 6, 14, 0, 0)
    orchestrator.register_product(
        sku="SKU-TEA",
        name="Green Tea",
        category="Beverages",
        brand="Tetley",
        price="150.00",
    )
    orchestrator.register_store(
        store_id="STORE-STORM",
        name="Storm Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-TEA": 20},
    )

    order_res = orchestrator.submit_order(
        order_id="ORD-WEATHER-01",
        customer_id="CUST-W1",
        customer_lat=12.9730,
        customer_lon=77.5940,
        items=[{"sku_id": "SKU-TEA", "quantity": 1}],
        order_time=base_time,
    )
    orig_eta = orchestrator.orders["ORD-WEATHER-01"].promised_eta
    assert orig_eta is not None

    hold_res = orchestrator.apply_weather_hold(
        event_id="RAIN-STORM-01",
        store_id="STORE-STORM",
        hold_start=base_time + timedelta(minutes=5),
    )

    assert hold_res["store_id"] == "STORE-STORM"
    assert "ORD-WEATHER-01" in hold_res["extended_order_ids"]
    assert orchestrator.orders["ORD-WEATHER-01"].promised_eta == orig_eta + timedelta(minutes=10)


def test_customer_cancellation_pre_picking_br12(orchestrator: ApplicationOrchestrator):
    """Customer cancellation before picking incurs ₹0 cancellation fee and releases reserved stock."""
    base_time = datetime(2026, 11, 6, 15, 0, 0)
    orchestrator.register_product(
        sku="SKU-COFFEE",
        name="Instant Coffee",
        category="Beverages",
        brand="Nescafe",
        price="200.00",
    )
    orchestrator.register_store(
        store_id="STORE-COFFEE",
        name="Coffee Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-COFFEE": 10},
    )

    orchestrator.submit_order(
        order_id="ORD-CANCEL-PRE",
        customer_id="CUST-CANCEL",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-COFFEE", "quantity": 2}],
        order_time=base_time,
    )
    # 2 units reserved: available = 8
    assert orchestrator.inventory_service.get_stock("STORE-COFFEE", "SKU-COFFEE") == 8

    cancel_res = orchestrator.cancel_order(
        order_id="ORD-CANCEL-PRE",
        cancelled_at=base_time + timedelta(minutes=2),
        reason="Customer changed mind",
        amount_paid="235.00",
    )

    assert cancel_res["status"] == OrderState.CANCELLED.value
    assert cancel_res["cancellation_fee"] == Decimal("0.00")
    assert cancel_res["refund_amount"] == Decimal("235.00")
    # Inventory released back
    assert orchestrator.inventory_service.get_stock("STORE-COFFEE", "SKU-COFFEE") == 10


def test_customer_cancellation_post_picking_br12(orchestrator: ApplicationOrchestrator):
    """Customer cancellation after picking starts incurs ₹20 cancellation fee."""
    base_time = datetime(2026, 11, 6, 16, 0, 0)
    orchestrator.register_product(
        sku="SKU-OIL",
        name="Olive Oil",
        category="Pantry",
        brand="Figaro",
        price="500.00",
    )
    orchestrator.register_store(
        store_id="STORE-OIL",
        name="Oil Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-OIL": 10},
    )

    orchestrator.submit_order(
        order_id="ORD-CANCEL-POST",
        customer_id="CUST-POST",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-OIL", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-CANCEL-POST")

    cancel_res = orchestrator.cancel_order(
        order_id="ORD-CANCEL-POST",
        cancelled_at=base_time + timedelta(minutes=5),
        reason="Ordered by mistake",
        amount_paid="535.00",
    )

    assert cancel_res["status"] == OrderState.CANCELLED.value
    assert cancel_res["cancellation_fee"] == Decimal("20.00")
    assert cancel_res["refund_amount"] == Decimal("515.00")


def test_complete_batch_delivery(orchestrator: ApplicationOrchestrator):
    """Completing batch delivery delivers all orders in batch and frees rider."""
    base_time = datetime(2026, 11, 6, 17, 0, 0)
    orchestrator.register_product(
        sku="SKU-SNACK",
        name="Snack Pack",
        category="Snacks",
        brand="Haldiram",
        price="60.00",
    )
    orchestrator.register_store(
        store_id="STORE-BATCH-DELIV",
        name="Batch Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-SNACK": 20},
    )
    orchestrator.register_rider(
        rider_id="RIDER-BATCH",
        name="Anil",
        status=RiderStatus.AVAILABLE,
    )

    # Submit 2 orders
    orchestrator.submit_order(
        order_id="ORD-B1",
        customer_id="CUST-B1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-SNACK", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.submit_order(
        order_id="ORD-B2",
        customer_id="CUST-B2",
        customer_lat=12.9725,
        customer_lon=77.5950,
        items=[{"sku_id": "SKU-SNACK", "quantity": 1}],
        order_time=base_time,
    )

    orchestrator.start_picking("ORD-B1")
    orchestrator.fulfil_order("ORD-B1")
    orchestrator.start_picking("ORD-B2")
    orchestrator.fulfil_order("ORD-B2")

    orchestrator.create_batch(
        batch_id="BATCH-ALL",
        store_id="STORE-BATCH-DELIV",
        rider_id="RIDER-BATCH",
        created_at=base_time + timedelta(minutes=5),
    )
    orchestrator.add_order_to_batch("BATCH-ALL", "ORD-B1", current_time=base_time + timedelta(minutes=6))
    orchestrator.add_order_to_batch("BATCH-ALL", "ORD-B2", current_time=base_time + timedelta(minutes=7))
    orchestrator.dispatch_batch("BATCH-ALL", dispatched_at=base_time + timedelta(minutes=10))

    batch_deliv = orchestrator.complete_batch_delivery(
        batch_id="BATCH-ALL",
        delivered_at=base_time + timedelta(minutes=20),
    )

    assert set(batch_deliv["delivered_order_ids"]) == {"ORD-B1", "ORD-B2"}
    assert batch_deliv["rider_status"] == RiderStatus.AVAILABLE.value
    assert orchestrator.orders["ORD-B1"].status == OrderState.DELIVERED
    assert orchestrator.orders["ORD-B2"].status == OrderState.DELIVERED


def test_command_processor_full_flow_integration():
    """Verify end-to-end execution of commands via JSONLCommandProcessor stream."""
    processor = JSONLCommandProcessor()
    lines = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T09:00:00", "payload": {"sku": "SKU-PROD-1", "name": "Apple", "category": "Produce", "brand": "Fresh", "price": "120.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T09:01:00", "payload": {"store_id": "STORE-P1", "name": "Produce Hub", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-PROD-1": 50}}}',
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T09:02:00", "payload": {"rider_id": "RIDER-P1", "name": "Kiran"}}',
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T09:03:00", "payload": {"order_id": "ORD-STREAM-1", "customer_id": "C-1", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-PROD-1", "quantity": 2}], "order_time": "2026-11-06T09:03:00"}}',
        '{"command": "START_PICKING", "timestamp": "2026-11-06T09:05:00", "payload": {"order_id": "ORD-STREAM-1"}}',
        '{"command": "FULFIL_ORDER", "timestamp": "2026-11-06T09:07:00", "payload": {"order_id": "ORD-STREAM-1", "amount_paid": "250.00"}}',
        '{"command": "GET_ORDER_TRACE", "timestamp": "2026-11-06T09:08:00", "payload": {"order_id": "ORD-STREAM-1"}}',
    ]

    results = list(processor.process_stream(lines))
    assert len(results) == 7
    for r in results:
        assert r["status"] == "SUCCESS", f"Failed command {r.get('command')}: {r.get('error')}"

    trace = results[6]["result"]
    assert trace["order_id"] == "ORD-STREAM-1"
    assert trace["status"] == "READY_FOR_DISPATCH"
    assert trace["order_value"] == Decimal("240.00")


def test_atomic_order_submission_failure_on_second_item_rolls_back(orchestrator: ApplicationOrchestrator):
    """If reservation for a second item fails, earlier reservations are rolled back and no orphan order remains."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(
        sku="SKU-ATOM-1", name="Item 1", category="Cat", brand="Brand", price="10.00"
    )
    orchestrator.register_store(
        store_id="STORE-ATOM",
        name="Atom Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-ATOM-1": 10},
    )

    # Item 1 is valid with stock, Item 2 has unknown SKU not stocked at store
    with pytest.raises(Exception):
        orchestrator.submit_order(
            order_id="ORD-ATOMIC-FAIL",
            customer_id="CUST-ATOM",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[
                {"sku_id": "SKU-ATOM-1", "quantity": 2},
                {"sku_id": "SKU-NONEXISTENT", "quantity": 1},
            ],
            order_time=base_time,
        )

    # 1. First item's inventory was restored back to 10
    assert orchestrator.inventory_service.get_stock("STORE-ATOM", "SKU-ATOM-1") == 10

    # 2. No orphan order left in orchestrator
    assert "ORD-ATOMIC-FAIL" not in orchestrator.orders


def test_atomic_order_submission_picking_eta_failure_rolls_back(orchestrator: ApplicationOrchestrator, monkeypatch):
    """If picking/ETA processing fails, all reservations are rolled back and order is not retained."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(
        sku="SKU-PICK-FAIL", name="Item", category="Cat", brand="Brand", price="15.00"
    )
    orchestrator.register_store(
        store_id="STORE-PICK-FAIL",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-PICK-FAIL": 10},
    )

    def _broken_picking(*args, **kwargs):
        raise RuntimeError("Simulated picking schedule failure")

    monkeypatch.setattr(orchestrator.picking_service, "process_order_picking_and_eta", _broken_picking)

    with pytest.raises(RuntimeError, match="Simulated picking schedule failure"):
        orchestrator.submit_order(
            order_id="ORD-PICK-FAIL",
            customer_id="CUST-FAIL",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-PICK-FAIL", "quantity": 3}],
            order_time=base_time,
        )

    # Stock is rolled back
    assert orchestrator.inventory_service.get_stock("STORE-PICK-FAIL", "SKU-PICK-FAIL") == 10
    # No orphan order
    assert "ORD-PICK-FAIL" not in orchestrator.orders


def test_reservation_commit_failure_prevents_ready_for_dispatch(orchestrator: ApplicationOrchestrator, monkeypatch):
    """If a reservation commit fails, fulfil_order fails and does not transition to READY_FOR_DISPATCH."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(
        sku="SKU-RES-FAIL", name="Item", category="Cat", brand="Brand", price="25.00"
    )
    orchestrator.register_store(
        store_id="STORE-RES-FAIL",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-RES-FAIL": 10},
    )

    orchestrator.submit_order(
        order_id="ORD-RES-FAIL",
        customer_id="CUST-RES",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-RES-FAIL", "quantity": 2}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-RES-FAIL")

    def _broken_commit(res_id: str):
        raise RuntimeError(f"Database error committing reservation {res_id}")

    monkeypatch.setattr(orchestrator.inventory_service, "commit_reservation", _broken_commit)

    with pytest.raises(RuntimeError, match="Database error committing reservation"):
        orchestrator.fulfil_order("ORD-RES-FAIL")

    # Order must remain in PICKING, not falsely transition to READY_FOR_DISPATCH
    assert orchestrator.orders["ORD-RES-FAIL"].status == OrderState.PICKING


def test_batch_eligibility_validations(orchestrator: ApplicationOrchestrator):
    """Adding orders to batches validates allocation, matching store, and lifecycle state."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(
        sku="SKU-BATCH-TEST", name="Item", category="Cat", brand="Brand", price="30.00"
    )
    orchestrator.register_store(
        store_id="STORE-A",
        name="Store A",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-BATCH-TEST": 10},
    )
    orchestrator.register_store(
        store_id="STORE-B",
        name="Store B",
        location={"latitude": 12.9800, "longitude": 77.6000},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-BATCH-TEST": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-A", name="Rider A")
    orchestrator.register_rider(rider_id="RIDER-B", name="Rider B")

    # Create batch for Store A
    orchestrator.create_batch(
        batch_id="BATCH-STORE-A",
        store_id="STORE-A",
        rider_id="RIDER-A",
        created_at=base_time,
    )

    # 1. Unallocated order cannot be added to a batch
    unalloc_order = orchestrator.submit_order(
        order_id="ORD-FAR-REJECT",
        customer_id="CUST-FAR",
        customer_lat=13.1000,
        customer_lon=77.5946,
        items=[{"sku_id": "SKU-BATCH-TEST", "quantity": 1}],
        order_time=base_time,
    )
    assert unalloc_order["status"] == OrderState.REJECTED.value
    with pytest.raises(ValueError, match="Cannot add unallocated order"):
        orchestrator.add_order_to_batch("BATCH-STORE-A", "ORD-FAR-REJECT")

    # 2. Order allocated to Store A cannot be added when not in READY_FOR_DISPATCH
    order_a = orchestrator.submit_order(
        order_id="ORD-FOR-A",
        customer_id="CUST-A",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-BATCH-TEST", "quantity": 1}],
        order_time=base_time,
    )
    assert order_a["allocated_store_id"] == "STORE-A"
    with pytest.raises(ValueError, match="only orders in READY_FOR_DISPATCH can be added"):
        orchestrator.add_order_to_batch("BATCH-STORE-A", "ORD-FOR-A")

    # 3. Cross-store order: create batch for Store B, fulfil ORD-FOR-A (Store A), try adding to Store B batch
    orchestrator.start_picking("ORD-FOR-A")
    orchestrator.fulfil_order("ORD-FOR-A")
    assert orchestrator.orders["ORD-FOR-A"].status == OrderState.READY_FOR_DISPATCH

    orchestrator.create_batch(
        batch_id="BATCH-STORE-B",
        store_id="STORE-B",
        rider_id="RIDER-B",
        created_at=base_time,
    )
    with pytest.raises(ValueError, match="does not match batch store 'STORE-B'"):
        orchestrator.add_order_to_batch("BATCH-STORE-B", "ORD-FOR-A")

    # 4. Empty batch cannot be dispatched
    with pytest.raises(ValueError, match="Cannot dispatch empty batch"):
        orchestrator.dispatch_batch("BATCH-STORE-A")


def test_duplicate_registration_rejection_and_state_preservation(orchestrator: ApplicationOrchestrator):
    """Duplicate product, store, rider, order, or batch registration is rejected without mutating state."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)

    # 1. Duplicate product
    orchestrator.register_product(
        sku="SKU-DUP-1", name="Original Name", category="Cat", brand="Brand", price="50.00"
    )
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_product(
            sku="SKU-DUP-1", name="Altered Name", category="Other", brand="Other", price="99.00"
        )
    assert orchestrator.products["SKU-DUP-1"].name == "Original Name"
    assert orchestrator.products["SKU-DUP-1"].price == Decimal("50.00")

    # 2. Duplicate store
    orchestrator.register_store(
        store_id="STORE-DUP-1",
        name="Store Original",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=10,
    )
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_store(
            store_id="STORE-DUP-1",
            name="Store Altered",
            location={"latitude": 12.9716, "longitude": 77.5946},
            operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
            picking_capacity=99,
        )
    assert orchestrator.stores["STORE-DUP-1"].name == "Store Original"
    assert orchestrator.stores["STORE-DUP-1"].picking_capacity == 10

    # 3. Duplicate rider
    orchestrator.register_rider(rider_id="RIDER-DUP-1", name="Original Rider")
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_rider(rider_id="RIDER-DUP-1", name="Altered Rider")
    assert orchestrator.riders["RIDER-DUP-1"].name == "Original Rider"

    # 4. Duplicate order
    orchestrator.submit_order(
        order_id="ORD-DUP-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-DUP-1", "quantity": 1}],
        order_time=base_time,
    )
    with pytest.raises(ValueError, match="already been submitted"):
        orchestrator.submit_order(
            order_id="ORD-DUP-1",
            customer_id="CUST-CHANGED",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-DUP-1", "quantity": 1}],
            order_time=base_time,
        )
    assert orchestrator.orders["ORD-DUP-1"].customer_id == "CUST-1"

    # 5. Duplicate batch
    orchestrator.create_batch(
        batch_id="BATCH-DUP-1",
        store_id="STORE-DUP-1",
        rider_id="RIDER-DUP-1",
        created_at=base_time,
    )
    with pytest.raises(ValueError, match="already exists"):
        orchestrator.create_batch(
            batch_id="BATCH-DUP-1",
            store_id="STORE-DUP-1",
            rider_id="RIDER-DUP-1",
            created_at=base_time,
        )


def test_delivery_idempotency_no_duplicate_compensation(orchestrator: ApplicationOrchestrator):
    """Repeated delivery execution does not duplicate customer SLA compensation credits."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(
        sku="SKU-COMP-IDEM", name="Milk", category="Dairy", brand="Amul", price="50.00"
    )
    orchestrator.register_store(
        store_id="STORE-COMP",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-COMP-IDEM": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-COMP", name="Rider")

    orchestrator.submit_order(
        order_id="ORD-COMP-IDEM",
        customer_id="CUST-COMP",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-COMP-IDEM", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-COMP-IDEM")
    orchestrator.fulfil_order("ORD-COMP-IDEM")

    orchestrator.create_batch(
        batch_id="BATCH-COMP",
        store_id="STORE-COMP",
        rider_id="RIDER-COMP",
        created_at=base_time + timedelta(minutes=1),
    )
    orchestrator.add_order_to_batch(
        "BATCH-COMP", "ORD-COMP-IDEM", current_time=base_time + timedelta(minutes=2)
    )
    orchestrator.dispatch_batch(
        "BATCH-COMP", dispatched_at=base_time + timedelta(minutes=5)
    )

    # Deliver 5 minutes late (triggers ₹25 wallet credit under BR-09)
    deliv_time = base_time + timedelta(minutes=8)
    res1 = orchestrator.complete_order_delivery(
        batch_id="BATCH-COMP", order_id="ORD-COMP-IDEM", delivered_at=deliv_time
    )
    assert res1["wallet_credit"] == Decimal("25.00")
    assert orchestrator.orders["ORD-COMP-IDEM"].wallet_credit == Decimal("25.00")

    # Second delivery call on already-delivered order: must be idempotent
    res2 = orchestrator.complete_order_delivery(
        batch_id="BATCH-COMP", order_id="ORD-COMP-IDEM", delivered_at=deliv_time
    )
    assert res2["wallet_credit"] == Decimal("25.00")
    # Wallet credit must remain exactly 25.00, not double to 50.00
    assert orchestrator.orders["ORD-COMP-IDEM"].wallet_credit == Decimal("25.00")

    # Batch delivery call on already-delivered batch: must be idempotent
    batch_res = orchestrator.complete_batch_delivery(
        batch_id="BATCH-COMP", delivered_at=deliv_time
    )
    assert "ORD-COMP-IDEM" in batch_res["delivered_order_ids"]
    assert orchestrator.orders["ORD-COMP-IDEM"].wallet_credit == Decimal("25.00")


def test_order_submission_clean_rollback_on_downstream_failure(orchestrator: ApplicationOrchestrator):
    """Clean rollback: when picking fails after reservations are created, all reservations are released."""
    orchestrator.register_product(sku="SKU-RB-1", name="Prod 1", category="Cat", brand="Brand", price="20.00")
    orchestrator.register_store(
        store_id="STORE-RB",
        name="Rollback Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-RB-1": 10},
    )

    # Force picking to fail
    def _broken_picking(*args, **kwargs):
        raise RuntimeError("Picking service simulated failure")

    orchestrator.picking_service.process_order_picking_and_eta = _broken_picking

    with pytest.raises(RuntimeError, match="Picking service simulated failure"):
        orchestrator.submit_order(
            order_id="ORD-RB-CLEAN",
            customer_id="CUST-RB",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-RB-1", "quantity": 3}],
            order_time=datetime(2026, 11, 6, 10, 0, 0),
        )

    # Order must not exist in registry
    assert "ORD-RB-CLEAN" not in orchestrator.orders

    # Reservation was released and stock restored
    res_list = orchestrator.inventory_service.get_reservations_for_order("ORD-RB-CLEAN", status=None)
    assert len(res_list) == 1
    assert res_list[0].status == ReservationStatus.RELEASED
    assert orchestrator.inventory_service.get_stock("STORE-RB", "SKU-RB-1") == 10


def test_order_submission_rollback_failure_raises_reservation_rollback_error(orchestrator: ApplicationOrchestrator):
    """Failure-injection: if release_reservation fails during rollback, ReservationRollbackError is raised."""
    orchestrator.register_product(sku="SKU-RBF-1", name="Prod 1", category="Cat", brand="Brand", price="20.00")
    orchestrator.register_store(
        store_id="STORE-RBF",
        name="Rollback Store Fail",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-RBF-1": 10},
    )

    # Force picking to fail so rollback is triggered
    orchestrator.picking_service.process_order_picking_and_eta = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("Primary picking failure")
    )

    # Force release_reservation to fail during rollback
    def _broken_release(reservation_id: str):
        raise OSError(f"Database error releasing reservation {reservation_id}")

    orchestrator.inventory_service.release_reservation = _broken_release

    with pytest.raises(ReservationRollbackError) as exc_info:
        orchestrator.submit_order(
            order_id="ORD-RB-FAIL",
            customer_id="CUST-RBF",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-RBF-1", "quantity": 2}],
            order_time=datetime(2026, 11, 6, 10, 0, 0),
        )

    err = exc_info.value
    assert err.order_id == "ORD-RB-FAIL"
    assert isinstance(err.primary_error, RuntimeError)
    assert "Primary picking failure" in str(err.primary_error)
    assert len(err.rollback_failures) == 1
    assert "Database error releasing reservation" in str(err.rollback_failures[0][1])

    # Orphan order must not be in registry
    assert "ORD-RB-FAIL" not in orchestrator.orders


def test_partial_reservation_commit_retry_recovers_cleanly(orchestrator: ApplicationOrchestrator):
    """If reservation 2 commit fails after reservation 1 committed, retry commits reservation 2 and finishes fulfilment."""
    orchestrator.register_product(sku="SKU-PRC-1", name="Prod 1", category="Cat", brand="Brand", price="30.00")
    orchestrator.register_product(sku="SKU-PRC-2", name="Prod 2", category="Cat", brand="Brand", price="40.00")
    orchestrator.register_store(
        store_id="STORE-PRC",
        name="Commit Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-PRC-1": 10, "SKU-PRC-2": 10},
    )

    orchestrator.submit_order(
        order_id="ORD-PRC-RETRY",
        customer_id="CUST-PRC",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[
            {"sku_id": "SKU-PRC-1", "quantity": 1},
            {"sku_id": "SKU-PRC-2", "quantity": 1},
        ],
        order_time=datetime(2026, 11, 6, 10, 0, 0),
    )
    orchestrator.start_picking("ORD-PRC-RETRY")

    # Get reservations for order
    all_res = orchestrator.inventory_service.get_reservations_for_order("ORD-PRC-RETRY", status=None)
    assert len(all_res) == 2
    res_id_1 = all_res[0].reservation_id
    res_id_2 = all_res[1].reservation_id

    # Simulate commit failure on the 2nd reservation
    orig_commit = orchestrator.inventory_service.commit_reservation
    commit_calls: list[str] = []

    def _flaky_commit(reservation_id: str):
        commit_calls.append(reservation_id)
        if reservation_id == res_id_2:
            raise RuntimeError("Simulated crash committing reservation 2")
        return orig_commit(reservation_id)

    orchestrator.inventory_service.commit_reservation = _flaky_commit

    with pytest.raises(RuntimeError, match="Simulated crash committing reservation 2"):
        orchestrator.fulfil_order("ORD-PRC-RETRY")

    # Order must remain in PICKING state
    assert orchestrator.orders["ORD-PRC-RETRY"].status == OrderState.PICKING

    # Reservation 1 is COMMITTED; Reservation 2 is still PENDING
    assert orchestrator.inventory_service.get_reservation(res_id_1).status == ReservationStatus.COMMITTED
    assert orchestrator.inventory_service.get_reservation(res_id_2).status == ReservationStatus.PENDING

    # Restore un-mocked commit and retry fulfilment
    orchestrator.inventory_service.commit_reservation = orig_commit

    retry_res = orchestrator.fulfil_order("ORD-PRC-RETRY")
    assert retry_res["status"] == OrderState.READY_FOR_DISPATCH.value
    assert retry_res["is_cancelled"] is False

    # Both reservations are now COMMITTED
    assert orchestrator.inventory_service.get_reservation(res_id_1).status == ReservationStatus.COMMITTED
    assert orchestrator.inventory_service.get_reservation(res_id_2).status == ReservationStatus.COMMITTED
    assert orchestrator.orders["ORD-PRC-RETRY"].status == OrderState.READY_FOR_DISPATCH


def test_batch_store_ownership_strictly_authoritative(orchestrator: ApplicationOrchestrator):
    """orchestrator.batch_stores is authoritative and does not fall back to private batching service state."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-BSO", name="Prod", category="Cat", brand="Brand", price="25.00")
    orchestrator.register_store(
        store_id="STORE-BSO-1",
        name="Store 1",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-BSO": 10},
    )
    orchestrator.register_store(
        store_id="STORE-BSO-2",
        name="Store 2",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-BSO": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-BSO", name="Rider")

    orchestrator.submit_order(
        order_id="ORD-BSO-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-BSO", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-BSO-1")
    orchestrator.fulfil_order("ORD-BSO-1")

    orchestrator.create_batch(
        batch_id="BATCH-BSO-1",
        store_id="STORE-BSO-1",
        rider_id="RIDER-BSO",
        created_at=base_time,
    )

    # 1. Authoritative check: if removed from orchestrator.batch_stores, even if in batching_service._batch_stores, it fails
    del orchestrator.batch_stores["BATCH-BSO-1"]
    with pytest.raises(KeyError, match="Authoritative store for batch 'BATCH-BSO-1' not found"):
        orchestrator.add_order_to_batch("BATCH-BSO-1", "ORD-BSO-1")

    # Restore authoritative store
    orchestrator.batch_stores["BATCH-BSO-1"] = "STORE-BSO-1"

    # 2. Unknown store in batch_stores
    orchestrator.batch_stores["BATCH-BSO-1"] = "NONEXISTENT-STORE"
    with pytest.raises(KeyError, match="Authoritative store for batch 'BATCH-BSO-1' not found"):
        orchestrator.add_order_to_batch("BATCH-BSO-1", "ORD-BSO-1")

    # Restore correct store
    orchestrator.batch_stores["BATCH-BSO-1"] = "STORE-BSO-1"
    orchestrator.add_order_to_batch("BATCH-BSO-1", "ORD-BSO-1", current_time=base_time + timedelta(minutes=1))

    # 3. Cross-store contaminated batch dispatch check
    orchestrator.submit_order(
        order_id="ORD-BSO-2",
        customer_id="CUST-2",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-BSO", "quantity": 1}],
        order_time=base_time,
    )
    # Manually tamper order's store to simulate batch contamination
    orchestrator.orders["ORD-BSO-2"].allocated_store_id = "STORE-BSO-2"
    orchestrator.batches["BATCH-BSO-1"].orders.append(orchestrator.orders["ORD-BSO-2"])

    with pytest.raises(ValueError, match="Contaminated batch"):
        orchestrator.dispatch_batch("BATCH-BSO-1", dispatched_at=base_time + timedelta(minutes=5))


def test_delivery_retry_preserves_original_actual_delivery_timestamp(orchestrator: ApplicationOrchestrator):
    """Delivery retries must preserve the original actual delivery timestamp and not replace it with the retry time."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-DEL-PRESERV", name="Milk", category="Dairy", brand="Brand", price="50.00")
    orchestrator.register_store(
        store_id="STORE-DELP",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-DEL-PRESERV": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-DELP", name="Rider")

    orchestrator.submit_order(
        order_id="ORD-DELP-1",
        customer_id="C-DELP",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-DEL-PRESERV", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-DELP-1")
    orchestrator.fulfil_order("ORD-DELP-1")

    orchestrator.create_batch("BATCH-DELP", "STORE-DELP", "RIDER-DELP", created_at=base_time)
    orchestrator.add_order_to_batch("BATCH-DELP", "ORD-DELP-1", current_time=base_time + timedelta(minutes=1))
    orchestrator.dispatch_batch("BATCH-DELP", dispatched_at=base_time + timedelta(minutes=4))

    # First delivery: actual delivery at 10:07
    first_del_time = base_time + timedelta(minutes=7)
    res1 = orchestrator.complete_order_delivery("BATCH-DELP", "ORD-DELP-1", delivered_at=first_del_time)
    assert res1["delivered_at"] == first_del_time.isoformat()
    assert orchestrator.orders["ORD-DELP-1"].actual_delivery_time == first_del_time

    # Retry delivery 30 minutes later at 10:37
    retry_time = base_time + timedelta(minutes=37)
    res2 = orchestrator.complete_order_delivery("BATCH-DELP", "ORD-DELP-1", delivered_at=retry_time)

    # Returned delivered_at MUST be the original delivery time, NOT the retry time
    assert res2["delivered_at"] == first_del_time.isoformat()
    assert orchestrator.orders["ORD-DELP-1"].actual_delivery_time == first_del_time

    # Batch delivery retry also preserves the original delivery time
    batch_res = orchestrator.complete_batch_delivery("BATCH-DELP", delivered_at=retry_time)
    assert batch_res["delivered_at"] == first_del_time.isoformat()


def test_delivery_compensation_failure_recovery_on_retry(orchestrator: ApplicationOrchestrator):
    """If compensation fails on first delivery call, retry applies compensation cleanly without corrupting state."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-COMP-RECOV", name="Milk", category="Dairy", brand="Brand", price="50.00")
    orchestrator.register_store(
        store_id="STORE-CR",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-COMP-RECOV": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-CR", name="Rider")

    orchestrator.submit_order(
        order_id="ORD-CR-1",
        customer_id="C-CR",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-COMP-RECOV", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-CR-1")
    orchestrator.fulfil_order("ORD-CR-1")

    orchestrator.create_batch("BATCH-CR", "STORE-CR", "RIDER-CR", created_at=base_time)
    orchestrator.add_order_to_batch("BATCH-CR", "ORD-CR-1", current_time=base_time + timedelta(minutes=1))
    orchestrator.dispatch_batch("BATCH-CR", dispatched_at=base_time + timedelta(minutes=4))

    # Delivery delivered at 10:12 (> 5 min late, qualifies for SLA credit)
    del_time = base_time + timedelta(minutes=12)

    # Simulate failure in CompensationService on 1st delivery call
    orig_apply = orchestrator.compensation_service.apply_compensation
    call_count = 0

    def _flaky_apply(order, actual_delivery_time=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Transient network error calling compensation ledger")
        return orig_apply(order, actual_delivery_time)

    orchestrator.compensation_service.apply_compensation = _flaky_apply

    with pytest.raises(RuntimeError, match="Transient network error"):
        orchestrator.complete_order_delivery("BATCH-CR", "ORD-CR-1", delivered_at=del_time)

    # Order was already delivered in batching service with actual delivery time recorded
    assert orchestrator.orders["ORD-CR-1"].status == OrderState.DELIVERED
    assert orchestrator.orders["ORD-CR-1"].actual_delivery_time == del_time

    # Retry delivery: compensation succeeds, wallet credit awarded, no duplicates
    retry_res = orchestrator.complete_order_delivery("BATCH-CR", "ORD-CR-1", delivered_at=del_time)
    assert retry_res["wallet_credit"] == Decimal("25.00")
    assert orchestrator.orders["ORD-CR-1"].wallet_credit == Decimal("25.00")
    assert retry_res["delivered_at"] == del_time.isoformat()


def test_picking_failure_releases_slot_capacity_and_reservations(orchestrator: ApplicationOrchestrator):
    """When picking fails after slot capacity has been assigned, slot occupancy and reservations are rolled back."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-SLOT-1", name="Bread", category="Bakery", brand="Brand", price="30.00")
    orchestrator.register_store(
        store_id="STORE-SLOT",
        name="Slot Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=10,
        inventory={"SKU-SLOT-1": 20},
    )

    # Slot load before submission is 0
    assert orchestrator.picking_service.get_slot_load("STORE-SLOT", base_time) == 0

    # Simulate failure in calculate_eta after schedule_picking has already occupied a slot
    orig_calc = orchestrator.picking_service.calculate_eta

    def _broken_calc_eta(*args, **kwargs):
        raise RuntimeError("Simulated ETA routing engine outage")

    orchestrator.picking_service.calculate_eta = _broken_calc_eta

    with pytest.raises(RuntimeError, match="Simulated ETA routing engine outage"):
        orchestrator.submit_order(
            order_id="ORD-SLOT-FAIL",
            customer_id="CUST-SLOT",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-SLOT-1", "quantity": 2}],
            order_time=base_time,
        )

    # 1. Picking slot occupancy rolled back to 0
    assert orchestrator.picking_service.get_slot_load("STORE-SLOT", base_time) == 0

    # 2. Reservation released
    res_list = orchestrator.inventory_service.get_reservations_for_order("ORD-SLOT-FAIL", status=None)
    assert len(res_list) == 1
    assert res_list[0].status == ReservationStatus.RELEASED

    # 3. Dark store stock restored to 20
    assert orchestrator.inventory_service.get_stock("STORE-SLOT", "SKU-SLOT-1") == 20

    # 4. Order popped from registry
    assert "ORD-SLOT-FAIL" not in orchestrator.orders


def test_unknown_sku_rejected_before_any_mutation(orchestrator: ApplicationOrchestrator):
    """Submitting an order containing an unknown SKU is rejected upfront before allocation or reservations."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-KNOWN", name="Known", category="Cat", brand="Brand", price="50.00")
    orchestrator.register_store(
        store_id="STORE-PREVAL",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-KNOWN": 20},
    )

    with pytest.raises(ValueError, match="Product SKU 'SKU-UNKNOWN-ITEM' is not registered in catalog"):
        orchestrator.submit_order(
            order_id="ORD-UNKNOWN-SKU",
            customer_id="CUST-PREVAL",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[
                {"sku_id": "SKU-KNOWN", "quantity": 1},
                {"sku_id": "SKU-UNKNOWN-ITEM", "quantity": 1},
            ],
            order_time=base_time,
        )

    # Dark store stock is completely untouched
    assert orchestrator.inventory_service.get_stock("STORE-PREVAL", "SKU-KNOWN") == 20

    # No reservations created
    res_list = orchestrator.inventory_service.get_reservations_for_order("ORD-UNKNOWN-SKU", status=None)
    assert len(res_list) == 0

    # Order not registered
    assert "ORD-UNKNOWN-SKU" not in orchestrator.orders


def test_store_registration_failure_atomic_rollback(orchestrator: ApplicationOrchestrator):
    """When pricing registration fails, store is rolled back from inventory service, leaving no orphan state."""
    # Force pricing service registration to fail
    def _broken_pricing_register(store):
        raise IOError("Pricing database connection timeout")

    orchestrator.pricing_service.register_store = _broken_pricing_register

    with pytest.raises(IOError, match="Pricing database connection timeout"):
        orchestrator.register_store(
            store_id="STORE-ATOMIC-FAIL",
            name="Atomic Store",
            location={"latitude": 12.9716, "longitude": 77.5946},
            operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        )

    # 1. Not in orchestrator registry
    assert "STORE-ATOMIC-FAIL" not in orchestrator.stores

    # 2. Rolled back from inventory service
    assert orchestrator.inventory_service.has_store("STORE-ATOMIC-FAIL") is False

    # 3. Restore working pricing registration and verify clean registration succeeds without duplicate error
    orchestrator.pricing_service.register_store = lambda store: type(orchestrator.pricing_service).register_store(orchestrator.pricing_service, store)
    res = orchestrator.register_store(
        store_id="STORE-ATOMIC-FAIL",
        name="Atomic Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
    )
    assert res["store_id"] == "STORE-ATOMIC-FAIL"
    assert "STORE-ATOMIC-FAIL" in orchestrator.stores


def test_partial_fulfilment_retry_with_substitutions(orchestrator: ApplicationOrchestrator):
    """Fulfilment retry after substitution commit failure resumes existing substitute reservation without duplicating."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-SUB-ORIG", name="Coke 500ml", category="Beverages", brand="Coca-Cola", price="40.00")
    orchestrator.register_product(sku="SKU-SUB-ALT", name="Diet Coke 500ml", category="Beverages", brand="Coca-Cola", price="38.00")

    # Store has 0 stock of original, but 10 of substitute
    orchestrator.register_store(
        store_id="STORE-SUB",
        name="Sub Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-SUB-ORIG": 0, "SKU-SUB-ALT": 10},
    )

    orchestrator.submit_order(
        order_id="ORD-SUB-RETRY",
        customer_id="CUST-SUB",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-SUB-ORIG", "quantity": 1}],
        substitution_preference="SIMILAR_ONLY",
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-SUB-RETRY")

    # Simulate commit failure during first fulfil call
    orig_commit = orchestrator.inventory_service.commit_reservation
    commit_failed = False

    def _flaky_commit(res_id):
        nonlocal commit_failed
        if not commit_failed:
            commit_failed = True
            raise RuntimeError("Simulated network timeout committing substitute reservation")
        return orig_commit(res_id)

    orchestrator.inventory_service.commit_reservation = _flaky_commit

    with pytest.raises(RuntimeError, match="Simulated network timeout"):
        orchestrator.fulfil_order("ORD-SUB-RETRY")

    # Order remained in PICKING
    assert orchestrator.orders["ORD-SUB-RETRY"].status == OrderState.PICKING

    # Retry fulfilment
    res2 = orchestrator.fulfil_order("ORD-SUB-RETRY")
    assert res2["status"] == OrderState.READY_FOR_DISPATCH.value
    assert res2["is_cancelled"] is False

    # Check reservations: substitute reservation exists and is COMMITTED, not duplicated
    all_res = orchestrator.inventory_service.get_reservations_for_order("ORD-SUB-RETRY", status=None)
    committed_sub_res = [r for r in all_res if r.sku == "SKU-SUB-ALT"]
    assert len(committed_sub_res) == 1
    assert committed_sub_res[0].status == ReservationStatus.COMMITTED

    # Substitute stock deducted by 1 (10 - 1 = 9)
    assert orchestrator.inventory_service.get_stock("STORE-SUB", "SKU-SUB-ALT") == 9


def test_compensation_failure_partway_through_batch_processing(orchestrator: ApplicationOrchestrator):
    """When compensation fails partway through a batch, retry safely compensates remaining orders without duplicating."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-BATCH-COMP", name="Item", category="General", brand="Brand", price="50.00")
    orchestrator.register_store(
        store_id="STORE-BC",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-BATCH-COMP": 20},
    )
    orchestrator.register_rider(rider_id="RIDER-BC", name="Rider")

    for i in (1, 2):
        orchestrator.submit_order(
            order_id=f"ORD-BC-{i}",
            customer_id=f"CUST-BC-{i}",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-BATCH-COMP", "quantity": 1}],
            order_time=base_time,
        )
        orchestrator.start_picking(f"ORD-BC-{i}")
        orchestrator.fulfil_order(f"ORD-BC-{i}")

    orchestrator.create_batch("BATCH-BC", "STORE-BC", "RIDER-BC", created_at=base_time)
    orchestrator.add_order_to_batch("BATCH-BC", "ORD-BC-1", current_time=base_time + timedelta(minutes=1))
    orchestrator.add_order_to_batch("BATCH-BC", "ORD-BC-2", current_time=base_time + timedelta(minutes=2))
    orchestrator.dispatch_batch("BATCH-BC", dispatched_at=base_time + timedelta(minutes=5))

    del_time = base_time + timedelta(minutes=15)

    # Deliver Order 1 progressively
    res_ord1 = orchestrator.complete_order_delivery("BATCH-BC", "ORD-BC-1", delivered_at=del_time)
    expected_credit_1 = res_ord1["wallet_credit"]
    assert expected_credit_1 > Decimal("0.00")
    assert orchestrator.orders["ORD-BC-1"].wallet_credit == expected_credit_1

    # Complete batch delivery: Order 1 is already DELIVERED and compensated; Order 2 gets delivered and compensated
    batch_res = orchestrator.complete_batch_delivery("BATCH-BC", delivered_at=del_time)
    assert "ORD-BC-1" in batch_res["delivered_order_ids"]
    assert "ORD-BC-2" in batch_res["delivered_order_ids"]

    # Order 1 wallet credit is still exactly expected_credit_1 (not doubled)
    assert orchestrator.orders["ORD-BC-1"].wallet_credit == expected_credit_1
    # Order 2 wallet credit is also compensated
    assert orchestrator.orders["ORD-BC-2"].wallet_credit == Decimal("25.00")

    # Retry batch delivery: no compensation values change
    batch_res2 = orchestrator.complete_batch_delivery("BATCH-BC", delivered_at=del_time)
    assert orchestrator.orders["ORD-BC-1"].wallet_credit == expected_credit_1
    assert orchestrator.orders["ORD-BC-2"].wallet_credit == Decimal("25.00")


def test_repeated_delivery_and_compensation_calls_are_strictly_idempotent(orchestrator: ApplicationOrchestrator):
    """Calling complete_order_delivery and complete_batch_delivery repeatedly yields identical results without side effects."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-IDEM-DEL", name="Milk", category="Dairy", brand="Brand", price="60.00")
    orchestrator.register_store(
        store_id="STORE-IDEM",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-IDEM-DEL": 10},
    )
    orchestrator.register_rider(rider_id="RIDER-IDEM", name="Rider")

    orchestrator.submit_order(
        order_id="ORD-IDEM-1",
        customer_id="CUST-IDEM",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-IDEM-DEL", "quantity": 1}],
        order_time=base_time,
    )
    orchestrator.start_picking("ORD-IDEM-1")
    orchestrator.fulfil_order("ORD-IDEM-1")

    orchestrator.create_batch("BATCH-IDEM", "STORE-IDEM", "RIDER-IDEM", created_at=base_time)
    orchestrator.add_order_to_batch("BATCH-IDEM", "ORD-IDEM-1", current_time=base_time + timedelta(minutes=1))
    orchestrator.dispatch_batch("BATCH-IDEM", dispatched_at=base_time + timedelta(minutes=4))

    del_time = base_time + timedelta(minutes=10)

    # 3 repeated calls to complete_order_delivery
    res1 = orchestrator.complete_order_delivery("BATCH-IDEM", "ORD-IDEM-1", delivered_at=del_time)
    res2 = orchestrator.complete_order_delivery("BATCH-IDEM", "ORD-IDEM-1", delivered_at=del_time + timedelta(minutes=20))
    res3 = orchestrator.complete_order_delivery("BATCH-IDEM", "ORD-IDEM-1", delivered_at=del_time + timedelta(minutes=40))

    assert res1 == res2 == res3
    assert orchestrator.orders["ORD-IDEM-1"].actual_delivery_time == del_time
    assert orchestrator.orders["ORD-IDEM-1"].wallet_credit == Decimal("25.00")

    # 3 repeated calls to complete_batch_delivery
    b_res1 = orchestrator.complete_batch_delivery("BATCH-IDEM", delivered_at=del_time)
    b_res2 = orchestrator.complete_batch_delivery("BATCH-IDEM", delivered_at=del_time + timedelta(minutes=30))
    b_res3 = orchestrator.complete_batch_delivery("BATCH-IDEM", delivered_at=del_time + timedelta(minutes=60))

    assert b_res1 == b_res2 == b_res3
    assert b_res1["delivered_at"] == del_time.isoformat()


def test_product_registration_failure_atomic_rollback(orchestrator: ApplicationOrchestrator):
    """Failure-injection during product registration rolls back inventory service state leaving no orphan product."""
    sku = "SKU-PROD-FAIL"

    class FailingDict(dict):
        def __setitem__(self, key, value):
            if key == sku:
                raise RuntimeError("Simulated crash storing product in orchestrator registry")
            super().__setitem__(key, value)

    orig_dict = orchestrator.products
    orchestrator.products = FailingDict(orig_dict)

    with pytest.raises(RuntimeError, match="Simulated crash storing product in orchestrator registry"):
        orchestrator.register_product(
            sku=sku,
            name="Fail Product",
            category="Snacks",
            brand="Lays",
            price="30.00",
        )

    # 1. Product is not in orchestrator registry
    assert sku not in orchestrator.products

    # 2. Product is not in inventory service
    assert orchestrator.inventory_service.has_product(sku) is False
    assert orchestrator.inventory_service.get_product(sku) is None

    # 3. Subsequent registration of the same SKU succeeds cleanly without duplicate error
    orchestrator.products = orig_dict
    res = orchestrator.register_product(
        sku=sku,
        name="Recovered Product",
        category="Snacks",
        brand="Lays",
        price="30.00",
    )
    assert res["sku"] == sku
    assert sku in orchestrator.products
    assert orchestrator.inventory_service.has_product(sku) is True


def test_picking_schedule_rollback_precision_and_idempotency(orchestrator: ApplicationOrchestrator):
    """Verify exact slot-load reversal, protection of other orders in the same slot, and idempotency of release."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-PK-PREC", name="Item", category="Cat", brand="Brand", price="25.00")
    orchestrator.register_store(
        store_id="STORE-PK-PREC",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=10,
        inventory={"SKU-PK-PREC": 50},
    )

    # 1. Submit Order 1 successfully into the 10:00 slot
    ord1_res = orchestrator.submit_order(
        order_id="ORD-PK-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-PK-PREC", "quantity": 1}],
        order_time=base_time,
    )
    assert ord1_res["status"] == OrderState.ALLOCATED.value
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 1

    # 2. Submit Order 2 into the same 10:00 slot
    ord2_res = orchestrator.submit_order(
        order_id="ORD-PK-2",
        customer_id="CUST-2",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-PK-PREC", "quantity": 1}],
        order_time=base_time,
    )
    assert ord2_res["status"] == OrderState.ALLOCATED.value
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 2

    # 3. Explicitly release Order 2's schedule
    orchestrator.picking_service.release_order_schedule("ORD-PK-2")

    # Slot load decreases from 2 to exactly 1; Order 1's capacity is NOT released
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 1
    assert "ORD-PK-1" in orchestrator.picking_service._slot_orders[("STORE-PK-PREC", base_time)]
    assert "ORD-PK-2" not in orchestrator.picking_service._slot_orders[("STORE-PK-PREC", base_time)]

    # 4. Repeated release of Order 2's schedule is idempotent and does not decrement below 1 or below 0
    orchestrator.picking_service.release_order_schedule("ORD-PK-2")
    orchestrator.picking_service.release_order_schedule("ORD-PK-2")
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 1

    # 5. Release Order 1: slot load returns to 0
    orchestrator.picking_service.release_order_schedule("ORD-PK-1")
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 0

    # 6. Additional release on empty slot does not decrement below 0
    orchestrator.picking_service.release_order_schedule("ORD-PK-1")
    orchestrator.picking_service.release_order_schedule("NONEXISTENT-ORDER")
    assert orchestrator.picking_service.get_slot_load("STORE-PK-PREC", base_time) == 0


def test_batch_compensation_mid_mutation_failure_and_retry_recovery(orchestrator: ApplicationOrchestrator):
    """When batch compensation fails partway through the mutation phase, retry recovers missing compensation without double-crediting."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-MID-COMP", name="Item", category="General", brand="Brand", price="50.00")
    orchestrator.register_store(
        store_id="STORE-MID",
        name="Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-MID-COMP": 20},
    )
    orchestrator.register_rider(rider_id="RIDER-MID", name="Rider")

    for i in (1, 2):
        orchestrator.submit_order(
            order_id=f"ORD-MID-{i}",
            customer_id=f"CUST-MID-{i}",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-MID-COMP", "quantity": 1}],
            order_time=base_time,
        )
        orchestrator.start_picking(f"ORD-MID-{i}")
        orchestrator.fulfil_order(f"ORD-MID-{i}")

    orchestrator.create_batch("BATCH-MID", "STORE-MID", "RIDER-MID", created_at=base_time)
    orchestrator.add_order_to_batch("BATCH-MID", "ORD-MID-1", current_time=base_time + timedelta(minutes=1))
    orchestrator.add_order_to_batch("BATCH-MID", "ORD-MID-2", current_time=base_time + timedelta(minutes=2))
    orchestrator.dispatch_batch("BATCH-MID", dispatched_at=base_time + timedelta(minutes=5))

    del_time = base_time + timedelta(minutes=15)

    # Complete delivery of batch in batching service directly to set status to DELIVERED
    batch = orchestrator.batches["BATCH-MID"]
    rider = orchestrator.riders["RIDER-MID"]
    orchestrator.batching_service.complete_delivery(batch, rider, delivered_at=del_time)
    assert batch.status == BatchStatus.DELIVERED

    # Preserve initial delivery timestamps
    orig_del_1 = orchestrator.orders["ORD-MID-1"].actual_delivery_time
    orig_del_2 = orchestrator.orders["ORD-MID-2"].actual_delivery_time
    assert orig_del_1 == del_time
    assert orig_del_2 == del_time

    # Simulate mid-batch failure in CompensationService:
    # Order 1 succeeds and is recorded in self._compensations, but a simulated fault occurs before Order 2 is recorded.
    comp_service = orchestrator.compensation_service
    order_1 = orchestrator.orders["ORD-MID-1"]
    order_2 = orchestrator.orders["ORD-MID-2"]

    # Pre-credit Order 1 manually to simulate partial completion before crash
    res_1 = comp_service.apply_compensation(order_1, actual_delivery_time=order_1.actual_delivery_time)
    expected_credit_1 = res_1.wallet_credit
    assert order_1.wallet_credit == expected_credit_1
    assert order_1.order_id in comp_service._compensations

    # Order 2 is not yet in compensation ledger
    assert order_2.order_id not in comp_service._compensations
    assert order_2.wallet_credit == Decimal("0.00")

    # Now retry complete_batch_delivery at a later wall-clock time
    retry_time = del_time + timedelta(minutes=45)
    retry_res = orchestrator.complete_batch_delivery("BATCH-MID", delivered_at=retry_time)

    # 1. Returned delivered_at must preserve the original delivery time, NOT retry_time
    assert retry_res["delivered_at"] == del_time.isoformat()

    # 2. Individual order delivery timestamps remain unchanged
    assert orchestrator.orders["ORD-MID-1"].actual_delivery_time == orig_del_1
    assert orchestrator.orders["ORD-MID-2"].actual_delivery_time == orig_del_2

    # 3. Order 1 was NOT double-credited
    assert orchestrator.orders["ORD-MID-1"].wallet_credit == expected_credit_1

    # 4. Order 2 received its missing compensation
    assert orchestrator.orders["ORD-MID-2"].wallet_credit == Decimal("25.00")
    assert order_2.order_id in comp_service._compensations


def test_duplicate_product_registration_rollback_safety(orchestrator: ApplicationOrchestrator):
    """Attempting to register a duplicate product SKU must fail without corrupting or deleting pre-existing product."""
    sku = "SKU-DUP-PROD"

    # 1. Register product A under SKU X
    res_a = orchestrator.register_product(
        sku=sku,
        name="Original Product A",
        category="Dairy",
        brand="Amul",
        price="50.00",
    )
    assert res_a["sku"] == sku
    assert res_a["name"] == "Original Product A"
    assert res_a["price"] == Decimal("50.00")

    # Verify present in orchestrator and inventory service
    assert sku in orchestrator.products
    assert orchestrator.products[sku].name == "Original Product A"
    assert orchestrator.products[sku].price == Decimal("50.00")
    assert orchestrator.inventory_service.has_product(sku) is True
    assert orchestrator.inventory_service.get_product(sku).price == Decimal("50.00")

    # 2. Attempt to register a different product B under the exact same SKU X
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_product(
            sku=sku,
            name="Conflicting Product B",
            category="Snacks",
            brand="Britannia",
            price="99.00",
        )

    # 3. Verify original product A remains 100% intact in both registries with attributes unchanged
    assert sku in orchestrator.products
    prod_in_orch = orchestrator.products[sku]
    assert prod_in_orch.name == "Original Product A"
    assert prod_in_orch.category == "Dairy"
    assert prod_in_orch.brand == "Amul"
    assert prod_in_orch.price == Decimal("50.00")

    assert orchestrator.inventory_service.has_product(sku) is True
    prod_in_inv = orchestrator.inventory_service.get_product(sku)
    assert prod_in_inv is not None
    assert prod_in_inv.name == "Original Product A"
    assert prod_in_inv.category == "Dairy"
    assert prod_in_inv.brand == "Amul"
    assert prod_in_inv.price == Decimal("50.00")

    # 4. Verify edge case: duplicate pre-existing directly in inventory service
    sku_inv = "SKU-DUP-INV-ONLY"
    from ps031.models.product import Product
    pre_prod = Product(sku=sku_inv, name="Pre-existing", category="Pantry", brand="Tata", price=Decimal("35.00"))
    orchestrator.inventory_service.register_product(pre_prod)
    assert orchestrator.inventory_service.has_product(sku_inv) is True
    assert sku_inv not in orchestrator.products

    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_product(
            sku=sku_inv,
            name="Orch Attempt",
            category="Pantry",
            brand="Tata",
            price="40.00",
        )

    # Rollback must NOT unregister the pre-existing inventory product
    assert orchestrator.inventory_service.has_product(sku_inv) is True
    assert orchestrator.inventory_service.get_product(sku_inv).name == "Pre-existing"
    assert sku_inv not in orchestrator.products


def test_duplicate_store_registration_rollback_safety(orchestrator: ApplicationOrchestrator):
    """Attempting to register a duplicate store ID must fail without deleting or corrupting pre-existing store."""
    store_id = "STORE-DUP-TEST"

    # 1. Register store A under store_id STORE-X
    res_a = orchestrator.register_store(
        store_id=store_id,
        name="Original Store A",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=10,
        active_riders=4,
        inventory={"SKU-1": 20},
    )
    assert res_a["store_id"] == store_id
    assert res_a["name"] == "Original Store A"
    assert res_a["picking_capacity"] == 10
    assert res_a["active_riders"] == 4

    # Verify present in orchestrator, inventory service, and pricing service
    assert store_id in orchestrator.stores
    assert orchestrator.stores[store_id].name == "Original Store A"
    assert orchestrator.inventory_service.has_store(store_id) is True
    assert orchestrator.inventory_service.get_store(store_id).name == "Original Store A"
    assert orchestrator.pricing_service.has_store(store_id) is True

    # 2. Attempt to register a different store B under the exact same store_id
    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_store(
            store_id=store_id,
            name="Conflicting Store B",
            location={"latitude": 13.0000, "longitude": 77.6000},
            operating_hours={"open_time": "08:00:00", "close_time": "20:00:00"},
            picking_capacity=50,
            active_riders=12,
            inventory={"SKU-2": 100},
        )

    # 3. Verify original store A remains 100% intact in all three registries with attributes unchanged
    assert store_id in orchestrator.stores
    store_in_orch = orchestrator.stores[store_id]
    assert store_in_orch.name == "Original Store A"
    assert store_in_orch.picking_capacity == 10
    assert store_in_orch.active_riders == 4
    assert store_in_orch.location.latitude == 12.9716

    assert orchestrator.inventory_service.has_store(store_id) is True
    store_in_inv = orchestrator.inventory_service.get_store(store_id)
    assert store_in_inv is not None
    assert store_in_inv.name == "Original Store A"
    assert store_in_inv.picking_capacity == 10

    assert orchestrator.pricing_service.has_store(store_id) is True

    # 4. Verify edge case: duplicate pre-existing directly in inventory service
    from ps031.models.coordinates import Coordinates
    from ps031.models.operating_hours import OperatingHours
    from ps031.models.store import Store
    store_id_inv = "STORE-DUP-INV-ONLY"
    pre_store = Store(
        store_id=store_id_inv,
        name="Pre-existing Store",
        location=Coordinates(12.97, 77.59),
        operating_hours=OperatingHours(time(6, 0), time(22, 0)),
        picking_capacity=8,
    )
    orchestrator.inventory_service.register_store(pre_store)
    assert orchestrator.inventory_service.has_store(store_id_inv) is True
    assert store_id_inv not in orchestrator.stores

    with pytest.raises(ValueError, match="already registered"):
        orchestrator.register_store(
            store_id=store_id_inv,
            name="Attempt Store",
            location={"latitude": 12.97, "longitude": 77.59},
            operating_hours={"open_time": "06:00:00", "close_time": "22:00:00"},
        )

    # Rollback must NOT unregister the pre-existing inventory store
    assert orchestrator.inventory_service.has_store(store_id_inv) is True
    assert orchestrator.inventory_service.get_store(store_id_inv).name == "Pre-existing Store"
    assert store_id_inv not in orchestrator.stores


def test_order_submission_failure_clears_allocation_and_lifecycle_state(orchestrator: ApplicationOrchestrator):
    """When submission fails after allocation succeeds, allocation state, lifecycle, reservations, and picking load are rolled back."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-ALLOC-RB", name="Alloc Item", category="Food", brand="Brand", price="80.00")
    orchestrator.register_store(
        store_id="STORE-ALLOC-RB",
        name="Store Alloc RB",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        picking_capacity=10,
        active_riders=4,
        inventory={"SKU-ALLOC-RB": 20},
    )

    # Confirm AllocationService has no registry attribute for orders
    assert not hasattr(orchestrator.allocation_service, "orders")
    assert not hasattr(orchestrator.allocation_service, "_orders")

    # Simulate failure during picking and ETA scheduling (after allocation and after reservation)
    def _failing_picking(store, order, reference_time=None):
        raise RuntimeError("Simulated picking failure after store allocation")

    orchestrator.picking_service.process_order_picking_and_eta = _failing_picking

    order_id = "ORD-ALLOC-FAIL"
    with pytest.raises(RuntimeError, match="Simulated picking failure after store allocation"):
        orchestrator.submit_order(
            order_id=order_id,
            customer_id="CUST-ALLOC-1",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-ALLOC-RB", "quantity": 3}],
            order_time=base_time,
        )

    # 1. No orphan order in orchestrator.orders
    assert order_id not in orchestrator.orders

    # 2. AllocationService remains completely stateless regarding stored orders
    assert not hasattr(orchestrator.allocation_service, "orders")

    # 3. Store stock has been completely restored (20, not 17)
    assert orchestrator.inventory_service.get_stock("STORE-ALLOC-RB", "SKU-ALLOC-RB") == 20

    # 4. Reservation was released
    res_list = orchestrator.inventory_service.get_reservations_for_order(order_id, status=None)
    assert len(res_list) == 1
    assert res_list[0].status == ReservationStatus.RELEASED

    # 5. Picking slot load was released and is 0
    assert orchestrator.picking_service.get_slot_load("STORE-ALLOC-RB", base_time) == 0

    # 6. Restore normal picking service and verify subsequent order submission succeeds cleanly
    orchestrator.picking_service.process_order_picking_and_eta = (
        type(orchestrator.picking_service)().process_order_picking_and_eta
    )
    res_success = orchestrator.submit_order(
        order_id=order_id,
        customer_id="CUST-ALLOC-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-ALLOC-RB", "quantity": 3}],
        order_time=base_time,
    )
    assert res_success["status"] == OrderState.ALLOCATED.value
    assert res_success["allocated_store_id"] == "STORE-ALLOC-RB"
    assert order_id in orchestrator.orders
    assert orchestrator.orders[order_id].status == OrderState.ALLOCATED
    assert orchestrator.orders[order_id].allocated_store_id == "STORE-ALLOC-RB"
    assert orchestrator.orders[order_id].pricing_frozen is True
    assert orchestrator.inventory_service.get_stock("STORE-ALLOC-RB", "SKU-ALLOC-RB") == 17


def test_order_submission_reservation_failure_clears_partial_reservations_and_allocation(orchestrator: ApplicationOrchestrator):
    """When reservation fails partway through order lines, earlier reservations are released, order is not registered, and allocation state is cleared."""
    base_time = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.register_product(sku="SKU-PART-1", name="Part 1", category="Food", brand="Brand", price="40.00")
    orchestrator.register_product(sku="SKU-PART-2", name="Part 2", category="Food", brand="Brand", price="60.00")
    orchestrator.register_store(
        store_id="STORE-PART-RB",
        name="Store Part RB",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-PART-1": 10, "SKU-PART-2": 10},
    )

    # Simulate failure on reserving the second item
    orig_reserve = orchestrator.inventory_service.reserve_stock

    def _broken_reserve(store_id, order_id, sku, quantity):
        if sku == "SKU-PART-2":
            raise RuntimeError("Database connection lost reserving SKU-PART-2")
        return orig_reserve(store_id, order_id, sku, quantity)

    orchestrator.inventory_service.reserve_stock = _broken_reserve

    order_id = "ORD-PART-FAIL"
    with pytest.raises(RuntimeError, match="Database connection lost reserving SKU-PART-2"):
        orchestrator.submit_order(
            order_id=order_id,
            customer_id="CUST-PART",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[
                {"sku_id": "SKU-PART-1", "quantity": 2},
                {"sku_id": "SKU-PART-2", "quantity": 3},
            ],
            order_time=base_time,
        )

    # 1. Order is not in orchestrator.orders
    assert order_id not in orchestrator.orders

    # 2. Reservation for SKU-PART-1 was rolled back and released
    res_list = orchestrator.inventory_service.get_reservations_for_order(order_id, status=None)
    assert len(res_list) == 1
    assert res_list[0].sku == "SKU-PART-1"
    assert res_list[0].status == ReservationStatus.RELEASED

    # 3. Stock for both items remains fully restored
    assert orchestrator.inventory_service.get_stock("STORE-PART-RB", "SKU-PART-1") == 10
    assert orchestrator.inventory_service.get_stock("STORE-PART-RB", "SKU-PART-2") == 10
