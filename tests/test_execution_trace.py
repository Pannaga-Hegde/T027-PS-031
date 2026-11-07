"""Comprehensive test suite for GitHub Issue #28 - Execution Trace Output.

Verifies:
- Chronological, structured execution trace recording across domain workflows.
- Monotonically increasing per-order sequence numbering (1..N).
- Association of business rules (BR-01 through BR-12) to trace events.
- Rejection trace output on allocation failure.
- Fulfilment cancellation (<60% threshold, BR-06) and substitutions/shortfalls (BR-05).
- Weather hold applied vs skipped (BR-11).
- Single-order and multi-order batch delivery SLA compensation (BR-09).
- Rider penalty and bonus evaluation (BR-10).
- Customer cancellation and refunds (BR-12).
- Idempotency: repeated operations do not create duplicate events.
- Failure safety: failed operations do not record false success events; submission failure rolls back partial trace events.
- Trace immutability: deep-copy isolation against caller mutations.
- JSONL processor integration for GET_ORDER_TRACE.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import pytest

from ps031.command_processor import JSONLCommandProcessor, serialize_record
from ps031.models.order_state import OrderState
from ps031.orchestrator import ApplicationOrchestrator


@pytest.fixture
def orchestrator() -> ApplicationOrchestrator:
    """Provide a fresh orchestrator instance."""
    return ApplicationOrchestrator()


def _setup_store_and_catalog(orchestrator: ApplicationOrchestrator, inventory_qty: int = 100):
    orchestrator.register_product(
        sku="SKU-TRACE-1",
        name="Organic Milk",
        category="Dairy",
        brand="Amul",
        price="50.00",
    )
    orchestrator.register_product(
        sku="SKU-TRACE-2",
        name="Brown Bread",
        category="Bakery",
        brand="Modern",
        price="40.00",
    )
    orchestrator.register_store(
        store_id="STORE-TRACE-1",
        name="Indiranagar Dark Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-TRACE-1": inventory_qty, "SKU-TRACE-2": inventory_qty},
    )
    orchestrator.register_rider(
        rider_id="RIDER-TRACE-1",
        name="Rider Speedy",
    )


def test_full_order_lifecycle_trace_sequence_and_rules(orchestrator: ApplicationOrchestrator):
    """Happy path: order proceeds through submission, picking, fulfilment, batching, dispatch, delivery, and penalties."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    # 1. Submit order
    sub_res = orchestrator.submit_order(
        order_id="ORD-LIFECYCLE-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[
            {"sku_id": "SKU-TRACE-1", "quantity": 2},
            {"sku_id": "SKU-TRACE-2", "quantity": 1},
        ],
        order_time=t0,
    )
    assert sub_res["status"] == "ALLOCATED"

    # 2. Start picking
    t_pick = t0 + timedelta(minutes=2)
    orchestrator.start_picking("ORD-LIFECYCLE-1", started_at=t_pick)

    # 3. Fulfil order
    t_fulfil = t0 + timedelta(minutes=4)
    orchestrator.fulfil_order("ORD-LIFECYCLE-1", fulfilled_at=t_fulfil)

    # 4. Create batch & add order
    t_batch = t0 + timedelta(minutes=5)
    orchestrator.create_batch(
        batch_id="BATCH-TRACE-1",
        store_id="STORE-TRACE-1",
        rider_id="RIDER-TRACE-1",
        created_at=t_batch,
    )
    orchestrator.add_order_to_batch("BATCH-TRACE-1", "ORD-LIFECYCLE-1", current_time=t_batch)

    # 5. Dispatch batch (after 3-minute timer)
    t_disp = t0 + timedelta(minutes=9)
    orchestrator.dispatch_batch("BATCH-TRACE-1", dispatched_at=t_disp)

    # 6. Complete order delivery (deliver at 10:15, promised ETA was ~10:07 -> late by ~8 min)
    t_deliv = t0 + timedelta(minutes=15)
    orchestrator.complete_order_delivery(
        batch_id="BATCH-TRACE-1",
        order_id="ORD-LIFECYCLE-1",
        delivered_at=t_deliv,
    )

    # 7. Evaluate rider penalties
    t_pen = t0 + timedelta(minutes=16)
    orchestrator.evaluate_rider_penalties("BATCH-TRACE-1", delivered_at=t_pen)

    # Verify trace summary and event history
    trace_res = orchestrator.get_order_trace("ORD-LIFECYCLE-1")
    assert trace_res["order_id"] == "ORD-LIFECYCLE-1"
    assert trace_res["status"] == OrderState.DELIVERED.value
    assert trace_res["allocated_store_id"] == "STORE-TRACE-1"
    assert trace_res["order_value"] == Decimal("140.00")
    assert trace_res["fulfilled_value"] == Decimal("140.00")
    assert trace_res["actual_delivery_time"] == t_deliv.isoformat()
    assert trace_res["event_count"] == 9
    assert len(trace_res["events"]) == 9
    assert trace_res["events"] == trace_res["trace"]

    events = orchestrator.get_order_events("ORD-LIFECYCLE-1")
    assert len(events) == 9

    # Verify monotonic 1-based sequence numbering
    for idx, event in enumerate(events, start=1):
        assert event["sequence"] == idx

    # Sequence 1: ORDER_SUBMITTED
    e1 = events[0]
    assert e1["event_type"] == "ORDER_SUBMITTED"
    assert e1["timestamp"] == t0.isoformat()
    assert e1["business_rules"] == ["BR-01", "BR-02", "BR-07"]
    assert e1["details"]["order_id"] == "ORD-LIFECYCLE-1"
    assert e1["details"]["customer_id"] == "CUST-1"
    assert len(e1["details"]["items"]) == 2
    assert e1["details"]["order_value"] == "140.00"
    assert e1["details"]["status"] == "RECEIVED"

    # Sequence 2: ORDER_ALLOCATED
    e2 = events[1]
    assert e2["event_type"] == "ORDER_ALLOCATED"
    assert e2["timestamp"] == t0.isoformat()
    assert e2["business_rules"] == ["BR-01", "BR-02", "BR-07"]
    assert e2["details"]["allocated_store_id"] == "STORE-TRACE-1"
    assert e2["details"]["status"] == "ALLOCATED"
    assert e2["details"]["pricing_frozen"] is True
    assert "evaluations" in e2["details"]

    # Sequence 3: PICKING_SCHEDULED
    e3 = events[2]
    assert e3["event_type"] == "PICKING_SCHEDULED"
    assert e3["timestamp"] == t0.isoformat()
    assert e3["business_rules"] == ["BR-03", "BR-04"]
    assert e3["details"]["store_id"] == "STORE-TRACE-1"
    assert "assigned_slot_start" in e3["details"]
    assert "promised_eta" in e3["details"]
    assert e3["details"]["delayed_promise"] is False

    # Sequence 4: PICKING_STARTED
    e4 = events[3]
    assert e4["event_type"] == "PICKING_STARTED"
    assert e4["timestamp"] == t_pick.isoformat()
    assert e4["business_rules"] == ["BR-03", "BR-04"]
    assert e4["details"]["status"] == "PICKING"
    assert e4["details"]["allocated_store_id"] == "STORE-TRACE-1"

    # Sequence 5: ORDER_FULFILLED
    e5 = events[4]
    assert e5["event_type"] == "ORDER_FULFILLED"
    assert e5["timestamp"] == t_fulfil.isoformat()
    assert e5["business_rules"] == ["BR-05", "BR-06"]
    assert e5["details"]["status"] == "READY_FOR_DISPATCH"
    assert e5["details"]["fulfilled_ratio"] == "1.00"
    assert e5["details"]["is_cancelled"] is False
    assert e5["details"]["delivery_fee_charged"] is True

    # Sequence 6: BATCH_ASSIGNED
    e6 = events[5]
    assert e6["event_type"] == "BATCH_ASSIGNED"
    assert e6["timestamp"] == t_batch.isoformat()
    assert e6["business_rules"] == ["BR-08"]
    assert e6["details"]["batch_id"] == "BATCH-TRACE-1"
    assert e6["details"]["rider_id"] == "RIDER-TRACE-1"
    assert e6["details"]["orders_in_batch"] == ["ORD-LIFECYCLE-1"]

    # Sequence 7: ORDER_DISPATCHED
    e7 = events[6]
    assert e7["event_type"] == "ORDER_DISPATCHED"
    assert e7["timestamp"] == t_disp.isoformat()
    assert e7["business_rules"] == ["BR-08"]
    assert e7["details"]["batch_id"] == "BATCH-TRACE-1"
    assert e7["details"]["status"] == "DISPATCHED"
    assert e7["details"]["drop_sequence"] == ["ORD-LIFECYCLE-1"]

    # Sequence 8: ORDER_DELIVERED
    e8 = events[7]
    assert e8["event_type"] == "ORDER_DELIVERED"
    assert e8["timestamp"] == t_deliv.isoformat()
    assert e8["business_rules"] == ["BR-09"]
    assert e8["details"]["batch_id"] == "BATCH-TRACE-1"
    assert e8["details"]["status"] == "DELIVERED"
    assert e8["details"]["is_late"] is True
    assert Decimal(e8["details"]["wallet_credit"]) > Decimal("0.00")

    # Sequence 9: RIDER_PENALTY_EVALUATED
    e9 = events[8]
    assert e9["event_type"] == "RIDER_PENALTY_EVALUATED"
    assert e9["timestamp"] == t_pen.isoformat()
    assert e9["business_rules"] == ["BR-10"]
    assert e9["details"]["batch_id"] == "BATCH-TRACE-1"
    assert e9["details"]["rider_id"] == "RIDER-TRACE-1"
    assert e9["details"]["late_orders_count"] == 1
    assert e9["details"]["bonus_lost"] is False


def test_order_allocation_rejection_trace(orchestrator: ApplicationOrchestrator):
    """When an order cannot be allocated (e.g. out of range), ORDER_SUBMITTED and ALLOCATION_REJECTED events are recorded."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 12, 0, 0)

    # Customer at 13.0500, 77.5946 (>8 km away from 12.9716, 77.5946)
    res = orchestrator.submit_order(
        order_id="ORD-REJECT-1",
        customer_id="CUST-FAR",
        customer_lat=13.0500,
        customer_lon=77.5946,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )
    assert res["status"] == "REJECTED"
    assert res["allocated_store_id"] is None
    assert res["rejection_reason"] is not None

    trace = orchestrator.get_order_trace("ORD-REJECT-1")
    assert trace["order_id"] == "ORD-REJECT-1"
    assert trace["status"] == "REJECTED"
    assert trace["allocated_store_id"] is None
    assert trace["event_count"] == 2

    events = orchestrator.get_order_events("ORD-REJECT-1")
    assert len(events) == 2
    assert events[0]["sequence"] == 1
    assert events[0]["event_type"] == "ORDER_SUBMITTED"
    assert events[1]["sequence"] == 2
    assert events[1]["event_type"] == "ALLOCATION_REJECTED"
    assert events[1]["business_rules"] == ["BR-01", "BR-02"]
    assert events[1]["details"]["status"] == "REJECTED"
    assert "rejection_reason" in events[1]["details"]
    assert len(events[1]["details"]["evaluations"]) >= 1


def test_order_fulfilment_cancellation_trace_below_60_percent(orchestrator: ApplicationOrchestrator):
    """Fulfilment cancelling under BR-06 (<60% value) records FULFILMENT_CANCELLED with refund details."""
    orchestrator.register_product(sku="SKU-EXP-1", name="Champagne", category="Wine", brand="Moet", price="100.00")
    orchestrator.register_store(
        store_id="STORE-CANC-1",
        name="Wine Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-EXP-1": 0},
    )
    t0 = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.submit_order(
        order_id="ORD-UNDER-60",
        customer_id="CUST-CANC",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-EXP-1", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.start_picking("ORD-UNDER-60", started_at=t0 + timedelta(minutes=1))

    fulfil_res = orchestrator.fulfil_order("ORD-UNDER-60", fulfilled_at=t0 + timedelta(minutes=3))
    assert fulfil_res["is_cancelled"] is True
    assert fulfil_res["status"] == "CANCELLED"

    events = orchestrator.get_order_events("ORD-UNDER-60")
    canc_event = events[-1]
    assert canc_event["event_type"] == "FULFILMENT_CANCELLED"
    assert canc_event["business_rules"] == ["BR-05", "BR-06"]
    assert canc_event["details"]["is_cancelled"] is True
    assert canc_event["details"]["status"] == "CANCELLED"
    assert Decimal(canc_event["details"]["refund_amount"]) >= Decimal("0.00")
    assert "below 60% threshold" in canc_event["details"]["reason"]


def test_order_fulfilment_with_substitutions_and_shortfalls_trace(orchestrator: ApplicationOrchestrator):
    """Fulfilment with substitutes records substitutions and shortfalls lists in ORDER_FULFILLED details."""
    orchestrator.register_product(sku="SKU-SUB-ORIG", name="Coke 500ml", category="Beverages", brand="Coca-Cola", price="40.00")
    orchestrator.register_product(sku="SKU-SUB-ALT", name="Diet Coke 500ml", category="Beverages", brand="Coca-Cola", price="38.00")
    orchestrator.register_store(
        store_id="STORE-SUBST",
        name="Soda Store",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-SUB-ORIG": 0, "SKU-SUB-ALT": 10},
    )
    t0 = datetime(2026, 11, 6, 10, 0, 0)
    orchestrator.submit_order(
        order_id="ORD-SUBST-1",
        customer_id="CUST-SUB",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-SUB-ORIG", "quantity": 1}],
        substitution_preference="SIMILAR_ONLY",
        order_time=t0,
    )
    orchestrator.start_picking("ORD-SUBST-1", started_at=t0 + timedelta(minutes=1))
    orchestrator.fulfil_order("ORD-SUBST-1", fulfilled_at=t0 + timedelta(minutes=2))

    events = orchestrator.get_order_events("ORD-SUBST-1")
    fulfil_event = next(e for e in events if e["event_type"] == "ORDER_FULFILLED")
    assert fulfil_event["business_rules"] == ["BR-05", "BR-06"]
    assert len(fulfil_event["details"]["substitutions"]) == 1
    sub = fulfil_event["details"]["substitutions"][0]
    assert sub["original_sku"] == "SKU-SUB-ORIG"
    assert sub["substitute_sku"] == "SKU-SUB-ALT"
    assert sub["quantity"] == 1


def test_weather_hold_trace_applied_and_skipped(orchestrator: ApplicationOrchestrator):
    """Weather hold records WEATHER_HOLD_APPLIED (BR-11) for active orders and WEATHER_HOLD_SKIPPED for dispatched orders."""
    _setup_store_and_catalog(orchestrator, inventory_qty=200)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    # Order 1: stays ALLOCATED
    orchestrator.submit_order(
        order_id="ORD-WH-1",
        customer_id="CUST-WH-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )
    initial_eta_1 = orchestrator.orders["ORD-WH-1"].promised_eta

    # Order 2: progresses through dispatch before weather hold
    orchestrator.submit_order(
        order_id="ORD-WH-DISP",
        customer_id="CUST-WH-2",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.start_picking("ORD-WH-DISP", started_at=t0 + timedelta(minutes=1))
    orchestrator.fulfil_order("ORD-WH-DISP", fulfilled_at=t0 + timedelta(minutes=2))
    orchestrator.create_batch("BATCH-WH", "STORE-TRACE-1", "RIDER-TRACE-1", created_at=t0 + timedelta(minutes=3))
    orchestrator.add_order_to_batch("BATCH-WH", "ORD-WH-DISP", current_time=t0 + timedelta(minutes=3))
    orchestrator.dispatch_batch("BATCH-WH", dispatched_at=t0 + timedelta(minutes=7))

    # Apply weather hold at 10:08
    t_hold = t0 + timedelta(minutes=8)
    orchestrator.apply_weather_hold("EVT-WH-1", "STORE-TRACE-1", hold_start=t_hold)
    assert orchestrator.orders["ORD-WH-1"].promised_eta == initial_eta_1 + timedelta(minutes=10)

    # Repeat with same event_id: idempotent, does not record duplicate event
    orchestrator.apply_weather_hold("EVT-WH-1", "STORE-TRACE-1", hold_start=t_hold)

    events_1 = orchestrator.get_order_events("ORD-WH-1")
    hold_events_1 = [e for e in events_1 if "WEATHER_HOLD" in e["event_type"]]
    assert len(hold_events_1) == 1
    assert hold_events_1[0]["event_type"] == "WEATHER_HOLD_APPLIED"
    assert hold_events_1[0]["business_rules"] == ["BR-11"]
    assert hold_events_1[0]["details"]["eta_extended"] is True
    assert hold_events_1[0]["details"]["extension_minutes"] == 10

    events_2 = orchestrator.get_order_events("ORD-WH-DISP")
    hold_events_2 = [e for e in events_2 if "WEATHER_HOLD" in e["event_type"]]
    assert len(hold_events_2) == 1
    assert hold_events_2[0]["event_type"] == "WEATHER_HOLD_SKIPPED"
    assert hold_events_2[0]["business_rules"] == ["BR-11"]
    assert hold_events_2[0]["details"]["eta_extended"] is False
    assert hold_events_2[0]["details"]["extension_minutes"] == 0


def test_order_cancellation_trace_with_fees_and_refund(orchestrator: ApplicationOrchestrator):
    """Order cancellation records ORDER_CANCELLED with fee, refund, and reason (BR-12)."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    orchestrator.submit_order(
        order_id="ORD-CANC-TRACE",
        customer_id="CUST-CANC",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 2}],
        order_time=t0,
    )
    t_canc = t0 + timedelta(minutes=1)
    canc_res = orchestrator.cancel_order(
        order_id="ORD-CANC-TRACE",
        cancelled_at=t_canc,
        reason="Customer changed mind",
    )
    assert canc_res["status"] == "CANCELLED"

    events = orchestrator.get_order_events("ORD-CANC-TRACE")
    canc_event = events[-1]
    assert canc_event["event_type"] == "ORDER_CANCELLED"
    assert canc_event["business_rules"] == ["BR-12"]
    assert canc_event["timestamp"] == t_canc.isoformat()
    assert canc_event["details"]["status"] == "CANCELLED"
    assert canc_event["details"]["reason"] == "Customer changed mind"
    assert "cancellation_fee" in canc_event["details"]
    assert "refund_amount" in canc_event["details"]


def test_batch_delivery_completion_trace_for_multiple_orders(orchestrator: ApplicationOrchestrator):
    """complete_batch_delivery records ORDER_DELIVERED with BR-09 SLA compensation for every delivered order."""
    _setup_store_and_catalog(orchestrator, inventory_qty=200)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    # Submit 2 orders
    for i in (1, 2):
        orchestrator.submit_order(
            order_id=f"ORD-BATCH-{i}",
            customer_id=f"CUST-{i}",
            customer_lat=12.9720 + (i * 0.001),
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
            order_time=t0,
        )
        orchestrator.start_picking(f"ORD-BATCH-{i}", started_at=t0 + timedelta(minutes=1))
        orchestrator.fulfil_order(f"ORD-BATCH-{i}", fulfilled_at=t0 + timedelta(minutes=2))

    orchestrator.create_batch("BATCH-MULTI", "STORE-TRACE-1", "RIDER-TRACE-1", created_at=t0 + timedelta(minutes=3))
    orchestrator.add_order_to_batch("BATCH-MULTI", "ORD-BATCH-1", current_time=t0 + timedelta(minutes=3))
    orchestrator.add_order_to_batch("BATCH-MULTI", "ORD-BATCH-2", current_time=t0 + timedelta(minutes=3))

    orchestrator.dispatch_batch("BATCH-MULTI", dispatched_at=t0 + timedelta(minutes=7))

    t_deliv = t0 + timedelta(minutes=20)
    orchestrator.complete_batch_delivery("BATCH-MULTI", delivered_at=t_deliv)

    for i in (1, 2):
        events = orchestrator.get_order_events(f"ORD-BATCH-{i}")
        del_event = events[-1]
        assert del_event["event_type"] == "ORDER_DELIVERED"
        assert del_event["business_rules"] == ["BR-09"]
        assert del_event["details"]["batch_id"] == "BATCH-MULTI"
        assert del_event["details"]["status"] == "DELIVERED"
        assert del_event["details"]["delivered_at"] == t_deliv.isoformat()


def test_idempotent_calls_do_not_duplicate_trace_events(orchestrator: ApplicationOrchestrator):
    """Repeated calls to idempotent orchestrator methods do not append duplicate trace events."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    orchestrator.submit_order(
        order_id="ORD-IDEMP-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.start_picking("ORD-IDEMP-1", started_at=t0 + timedelta(minutes=1))
    orchestrator.fulfil_order("ORD-IDEMP-1", fulfilled_at=t0 + timedelta(minutes=2))
    events_after_fulfil1 = len(orchestrator.get_order_events("ORD-IDEMP-1"))

    # Repeated fulfil_order does not duplicate ORDER_FULFILLED
    orchestrator.fulfil_order("ORD-IDEMP-1", fulfilled_at=t0 + timedelta(minutes=2))
    events_after_fulfil2 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    assert events_after_fulfil2 == events_after_fulfil1

    orchestrator.create_batch("BATCH-IDEMP", "STORE-TRACE-1", "RIDER-TRACE-1", created_at=t0 + timedelta(minutes=3))

    # 1. Repeated add_order_to_batch
    orchestrator.add_order_to_batch("BATCH-IDEMP", "ORD-IDEMP-1", current_time=t0 + timedelta(minutes=3))
    events_after_add1 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    orchestrator.add_order_to_batch("BATCH-IDEMP", "ORD-IDEMP-1", current_time=t0 + timedelta(minutes=4))
    events_after_add2 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    assert events_after_add2 == events_after_add1

    # 2. Repeated dispatch_batch is rejected by batching service and does not duplicate events
    orchestrator.dispatch_batch("BATCH-IDEMP", dispatched_at=t0 + timedelta(minutes=7))
    events_after_disp1 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    with pytest.raises(ValueError, match="Cannot dispatch batch"):
        orchestrator.dispatch_batch("BATCH-IDEMP", dispatched_at=t0 + timedelta(minutes=8))
    events_after_disp2 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    assert events_after_disp2 == events_after_disp1

    # 3. Repeated complete_order_delivery
    t_del = t0 + timedelta(minutes=12)
    orchestrator.complete_order_delivery("BATCH-IDEMP", "ORD-IDEMP-1", delivered_at=t_del)
    events_after_del1 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    orchestrator.complete_order_delivery("BATCH-IDEMP", "ORD-IDEMP-1", delivered_at=t_del + timedelta(minutes=1))
    events_after_del2 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    assert events_after_del2 == events_after_del1

    # 4. Repeated evaluate_rider_penalties with identical timestamp returns cached result idempotently
    orchestrator.evaluate_rider_penalties("BATCH-IDEMP", delivered_at=t_del)
    events_after_pen1 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    orchestrator.evaluate_rider_penalties("BATCH-IDEMP", delivered_at=t_del)
    events_after_pen2 = len(orchestrator.get_order_events("ORD-IDEMP-1"))
    assert events_after_pen2 == events_after_pen1

    # Incompatible re-evaluation with different parameters raises ValueError under BR-10
    with pytest.raises(ValueError, match="already processed with different parameters"):
        orchestrator.evaluate_rider_penalties("BATCH-IDEMP", delivered_at=t_del + timedelta(minutes=1))

    # 5. Repeated cancel_order on already cancelled order with matching timestamp is idempotent
    t_canc = t0 + timedelta(minutes=1)
    orchestrator.submit_order(
        order_id="ORD-IDEMP-CANC",
        customer_id="CUST-CANC",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.cancel_order("ORD-IDEMP-CANC", cancelled_at=t_canc)
    canc_cnt_1 = len(orchestrator.get_order_events("ORD-IDEMP-CANC"))
    orchestrator.cancel_order("ORD-IDEMP-CANC", cancelled_at=t_canc)
    canc_cnt_2 = len(orchestrator.get_order_events("ORD-IDEMP-CANC"))
    assert canc_cnt_2 == canc_cnt_1

    # Incompatible retry with altered cancelled_at raises ValueError under BR-12
    with pytest.raises(ValueError, match="altered cancelled_at"):
        orchestrator.cancel_order("ORD-IDEMP-CANC", cancelled_at=t_canc + timedelta(minutes=1))


def test_failed_operations_do_not_record_false_success_events(orchestrator: ApplicationOrchestrator):
    """Failed order operations roll back partial trace events and never record false success events."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    # Simulate stock reservation failure during submit_order
    def _exploding_reserve(*args, **kwargs):
        raise RuntimeError("Simulated inventory reservation failure")

    orchestrator.inventory_service.reserve_stock = _exploding_reserve

    with pytest.raises(RuntimeError, match="Simulated inventory reservation failure"):
        orchestrator.submit_order(
            order_id="ORD-FAIL-SUB",
            customer_id="CUST-FAIL",
            customer_lat=12.9720,
            customer_lon=77.5948,
            items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
            order_time=t0,
        )

    # Order was not stored and has NO orphan trace events
    assert "ORD-FAIL-SUB" not in orchestrator.orders
    assert "ORD-FAIL-SUB" not in orchestrator.order_events
    with pytest.raises(KeyError, match="not found"):
        orchestrator.get_order_trace("ORD-FAIL-SUB")


def test_trace_immutability_and_deep_copy(orchestrator: ApplicationOrchestrator):
    """get_order_trace and get_order_events return deep copies; external mutations do not affect internal state."""
    _setup_store_and_catalog(orchestrator)
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    orchestrator.submit_order(
        order_id="ORD-MUTATE-1",
        customer_id="CUST-1",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-TRACE-1", "quantity": 1}],
        order_time=t0,
    )

    trace1 = orchestrator.get_order_trace("ORD-MUTATE-1")
    # Mutate top-level list
    trace1["events"].clear()
    # Mutate trace dict
    trace1["order_value"] = Decimal("99999.00")

    # Fetch fresh trace
    trace2 = orchestrator.get_order_trace("ORD-MUTATE-1")
    assert len(trace2["events"]) == 3
    assert trace2["order_value"] == Decimal("50.00")

    # Mutate deep nested event details
    events1 = orchestrator.get_order_events("ORD-MUTATE-1")
    events1[0]["details"]["tampered"] = True
    events1[0]["details"]["items"][0]["quantity"] = 9999

    events2 = orchestrator.get_order_events("ORD-MUTATE-1")
    assert "tampered" not in events2[0]["details"]
    assert events2[0]["details"]["items"][0]["quantity"] == 1

    # Mutating result["events"] does not mutate result["trace"], and vice-versa
    trace_indep = orchestrator.get_order_trace("ORD-MUTATE-1")
    trace_indep["events"][0]["details"]["customer_id"] = "TAMPERED_IN_EVENTS"
    assert trace_indep["trace"][0]["details"]["customer_id"] != "TAMPERED_IN_EVENTS"
    assert trace_indep["trace"][0]["details"]["customer_id"] == "CUST-1"

    trace_indep["trace"][0]["details"]["customer_id"] = "TAMPERED_IN_TRACE"
    assert trace_indep["events"][0]["details"]["customer_id"] == "TAMPERED_IN_EVENTS"

    trace_indep["events"].clear()
    assert len(trace_indep["trace"]) == 3

    # Internal state remains completely unmutated
    fresh_trace = orchestrator.get_order_trace("ORD-MUTATE-1")
    assert len(fresh_trace["events"]) == 3
    assert len(fresh_trace["trace"]) == 3
    assert fresh_trace["events"][0]["details"]["customer_id"] == "CUST-1"


def test_repeated_fulfilment_and_cancellation_idempotency(orchestrator: ApplicationOrchestrator):
    """Repeated fulfil_order calls do not duplicate ORDER_FULFILLED or FULFILMENT_CANCELLED events."""
    orchestrator.register_product(sku="SKU-IDEMP-F1", name="Bread", category="Bakery", brand="Modern", price="40.00")
    orchestrator.register_product(sku="SKU-IDEMP-F2", name="Milk", category="Dairy", brand="Amul", price="50.00")
    orchestrator.register_store(
        store_id="STORE-IDEMP-F",
        name="Store F",
        location={"latitude": 12.9716, "longitude": 77.5946},
        operating_hours={"open_time": "06:00:00", "close_time": "23:00:00"},
        inventory={"SKU-IDEMP-F1": 10, "SKU-IDEMP-F2": 0},
    )
    t0 = datetime(2026, 11, 6, 10, 0, 0)

    # 1. Successful fulfilment idempotency
    orchestrator.submit_order(
        order_id="ORD-FULFIL-IDEMP",
        customer_id="CUST-F",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-IDEMP-F1", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.start_picking("ORD-FULFIL-IDEMP", started_at=t0 + timedelta(minutes=1))

    # First fulfil call: legitimate first-time event recorded
    res1 = orchestrator.fulfil_order("ORD-FULFIL-IDEMP", fulfilled_at=t0 + timedelta(minutes=2))
    assert res1["status"] == OrderState.READY_FOR_DISPATCH.value
    events_1 = orchestrator.get_order_events("ORD-FULFIL-IDEMP")
    assert len(events_1) == 5
    assert events_1[-1]["event_type"] == "ORDER_FULFILLED"
    assert events_1[-1]["sequence"] == 5

    # Second and third fulfil calls: idempotent return without duplicate events
    res2 = orchestrator.fulfil_order("ORD-FULFIL-IDEMP", fulfilled_at=t0 + timedelta(minutes=2))
    res3 = orchestrator.fulfil_order("ORD-FULFIL-IDEMP", fulfilled_at=t0 + timedelta(minutes=3))
    assert res2 == res1
    assert res3 == res1

    events_after = orchestrator.get_order_events("ORD-FULFIL-IDEMP")
    assert len(events_after) == 5
    fulfil_events = [e for e in events_after if e["event_type"] == "ORDER_FULFILLED"]
    assert len(fulfil_events) == 1
    assert fulfil_events[0]["sequence"] == 5

    # 2. BR-06 cancellation fulfilment idempotency
    orchestrator.submit_order(
        order_id="ORD-FULFIL-CANC-IDEMP",
        customer_id="CUST-FC",
        customer_lat=12.9720,
        customer_lon=77.5948,
        items=[{"sku_id": "SKU-IDEMP-F2", "quantity": 1}],
        order_time=t0,
    )
    orchestrator.start_picking("ORD-FULFIL-CANC-IDEMP", started_at=t0 + timedelta(minutes=1))

    # First call cancels under BR-06 (<60% fulfilled value)
    c_res1 = orchestrator.fulfil_order("ORD-FULFIL-CANC-IDEMP", fulfilled_at=t0 + timedelta(minutes=2))
    assert c_res1["is_cancelled"] is True
    assert c_res1["status"] == "CANCELLED"
    events_c1 = orchestrator.get_order_events("ORD-FULFIL-CANC-IDEMP")
    assert len(events_c1) == 5
    assert events_c1[-1]["event_type"] == "FULFILMENT_CANCELLED"
    assert events_c1[-1]["sequence"] == 5

    # Second and third calls: idempotent return without duplicating FULFILMENT_CANCELLED
    c_res2 = orchestrator.fulfil_order("ORD-FULFIL-CANC-IDEMP", fulfilled_at=t0 + timedelta(minutes=2))
    c_res3 = orchestrator.fulfil_order("ORD-FULFIL-CANC-IDEMP", fulfilled_at=t0 + timedelta(minutes=3))
    assert c_res2 == c_res1
    assert c_res3 == c_res1

    events_c_after = orchestrator.get_order_events("ORD-FULFIL-CANC-IDEMP")
    assert len(events_c_after) == 5
    canc_events = [e for e in events_c_after if e["event_type"] == "FULFILMENT_CANCELLED"]
    assert len(canc_events) == 1
    assert canc_events[0]["sequence"] == 5


def test_set_serialization_is_deterministic(orchestrator: ApplicationOrchestrator):
    """Sets in trace details are sorted deterministically before recursion, preserving list/tuple order."""
    # String set
    set_str = {"zebra", "apple", "mango", "banana"}
    out_str = orchestrator._serialize_trace_detail(set_str)
    assert out_str == ["apple", "banana", "mango", "zebra"]

    # Numeric set
    set_num = {50, 10, 30, 20}
    out_num = orchestrator._serialize_trace_detail(set_num)
    assert out_num == [10, 20, 30, 50]

    # Mixed set (sorted safely via str conversion)
    set_mixed = {2, "banana", 1, "apple"}
    out_mixed = orchestrator._serialize_trace_detail(set_mixed)
    assert isinstance(out_mixed, list)
    assert len(out_mixed) == 4
    # Re-serializing produces identical deterministic sequence
    assert orchestrator._serialize_trace_detail(set_mixed) == out_mixed

    # Preserves list and tuple ordering as-is
    original_list = [3, 1, 2]
    assert orchestrator._serialize_trace_detail(original_list) == [3, 1, 2]
    original_tuple = (5, 2, 8)
    assert orchestrator._serialize_trace_detail(original_tuple) == [5, 2, 8]

    # Nested dictionary containing sets
    nested = {
        "features": {"fast", "secure", "reliable"},
        "limits": {100, 25, 50},
    }
    out_nested = orchestrator._serialize_trace_detail(nested)
    assert out_nested == {
        "features": ["fast", "reliable", "secure"],
        "limits": [25, 50, 100],
    }


def test_jsonl_command_processor_get_order_trace():
    """Verify that JSONL command processor cleanly processes GET_ORDER_TRACE and outputs formatted execution trace."""
    proc = JSONLCommandProcessor()
    lines = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-JSONL-1", "name": "Juice", "category": "Drinks", "brand": "Real", "price": "60.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T10:01:00", "payload": {"store_id": "STORE-JSONL-1", "name": "Store J", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-JSONL-1": 50}}}',
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:02:00", "payload": {"order_id": "ORD-JSONL-1", "customer_id": "C-J", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-JSONL-1", "quantity": 1}]}}',
        '{"command": "START_PICKING", "timestamp": "2026-11-06T10:03:00", "payload": {"order_id": "ORD-JSONL-1"}}',
        '{"command": "GET_ORDER_TRACE", "timestamp": "2026-11-06T10:04:00", "payload": {"order_id": "ORD-JSONL-1"}}',
    ]

    results = [proc.process_line(line, idx + 1) for idx, line in enumerate(lines)]
    for r in results:
        assert r["status"] == "SUCCESS", r

    trace_rec = results[-1]
    assert trace_rec["command"] == "GET_ORDER_TRACE"
    assert trace_rec["line_number"] == 5
    assert trace_rec["result"]["order_id"] == "ORD-JSONL-1"
    assert trace_rec["result"]["status"] == "PICKING"
    assert trace_rec["result"]["event_count"] == 4
    assert len(trace_rec["result"]["events"]) == 4

    # Verify deterministic serialization
    jsonl_str = serialize_record(trace_rec)
    assert '"status": "SUCCESS"' in jsonl_str
    assert '"event_count": 4' in jsonl_str
    assert '"event_type": "PICKING_STARTED"' in jsonl_str
