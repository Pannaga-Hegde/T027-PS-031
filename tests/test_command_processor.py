"""Unit and integration tests for JSONLCommandProcessor."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import pytest

from ps031.command_processor import JSONLCommandProcessor, serialize_record
from ps031.orchestrator import ApplicationOrchestrator


def test_blank_lines_are_skipped():
    processor = JSONLCommandProcessor()
    lines = [
        "",
        "   ",
        "\t\n",
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R1", "name": "Ramesh Kumar"}}',
        "",
        "  \n",
    ]
    results = list(processor.process_stream(lines))
    assert len(results) == 1
    assert results[0]["line_number"] == 4
    assert results[0]["status"] == "SUCCESS"
    assert results[0]["command"] == "REGISTER_RIDER"


def test_physical_line_numbers_preserved():
    processor = JSONLCommandProcessor()
    lines = [
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R1", "name": "Rider 1"}}',  # Line 1
        "",  # Line 2
        "",  # Line 3
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:01:00", "payload": {"rider_id": "R2", "name": "Rider 2"}}',  # Line 4
        "   ",  # Line 5
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:02:00", "payload": {"rider_id": "R3", "name": "Rider 3"}}',  # Line 6
    ]
    results = list(processor.process_stream(lines))
    assert len(results) == 3
    assert results[0]["line_number"] == 1
    assert results[1]["line_number"] == 4
    assert results[2]["line_number"] == 6


def test_malformed_json_syntax_returns_error():
    processor = JSONLCommandProcessor()
    line = '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {'  # Incomplete JSON
    res = processor.process_line(line, line_number=7)

    assert res is not None
    assert res["line_number"] == 7
    assert res["status"] == "ERROR"
    assert res["command"] == "UNKNOWN"
    assert res["result"] is None
    assert res["error"]["code"] == "MALFORMED_JSON"
    assert "Malformed JSON" in res["error"]["message"]


def test_invalid_envelope_structures():
    processor = JSONLCommandProcessor()

    # Top-level is not an object
    res1 = processor.process_line('["command", "data"]', 1)
    assert res1["status"] == "ERROR"
    assert res1["error"]["code"] == "INVALID_ENVELOPE"

    # Missing command
    res2 = processor.process_line('{"timestamp": "2026-11-06T10:00:00", "payload": {}}', 2)
    assert res2["status"] == "ERROR"
    assert res2["error"]["code"] == "INVALID_ENVELOPE"

    # Missing timestamp
    res3 = processor.process_line('{"command": "REGISTER_RIDER", "payload": {}}', 3)
    assert res3["status"] == "ERROR"
    assert res3["error"]["code"] == "INVALID_ENVELOPE"

    # Malformed timestamp
    res4 = processor.process_line('{"command": "REGISTER_RIDER", "timestamp": "not-iso-time", "payload": {}}', 4)
    assert res4["status"] == "ERROR"
    assert res4["error"]["code"] == "INVALID_ENVELOPE"
    assert "Invalid ISO 8601" in res4["error"]["message"]

    # Missing payload
    res5 = processor.process_line('{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00"}', 5)
    assert res5["status"] == "ERROR"
    assert res5["error"]["code"] == "INVALID_ENVELOPE"

    # Payload is not a dictionary
    res6 = processor.process_line('{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": "not-a-dict"}', 6)
    assert res6["status"] == "ERROR"
    assert res6["error"]["code"] == "INVALID_ENVELOPE"


def test_unknown_command_returns_error():
    processor = JSONLCommandProcessor()
    line = '{"command": "LAUNCH_DRONE", "timestamp": "2026-11-06T10:00:00", "payload": {"target": "rooftop"}}'
    res = processor.process_line(line, line_number=10)

    assert res["status"] == "ERROR"
    assert res["command"] == "LAUNCH_DRONE"
    assert res["error"]["code"] == "UNKNOWN_COMMAND"
    assert "Unrecognized command 'LAUNCH_DRONE'" in res["error"]["message"]


def test_missing_required_payload_field():
    processor = JSONLCommandProcessor()
    # Missing 'name' for REGISTER_RIDER
    line = '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R1"}}'
    res = processor.process_line(line, line_number=1)

    assert res["status"] == "ERROR"
    assert res["error"]["code"] == "INVALID_ARGUMENT"


def test_fault_isolation_continuation_and_state_preservation():
    """An error on line 2 must not prevent line 3 from executing or accessing line 1's state."""
    processor = JSONLCommandProcessor()
    lines = [
        # Line 1: Register product (Valid)
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T09:00:00", "payload": {"sku": "SKU-ISOLATION", "name": "Bread", "category": "Bakery", "brand": "Britannia", "price": "40.00"}}',
        # Line 2: Malformed JSON syntax (Error)
        '{"command": "INVALID_JSON_LINE", { bad syntax here',
        # Line 3: Register rider (Valid)
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T09:01:00", "payload": {"rider_id": "R-ISO", "name": "Suresh"}}',
    ]

    results = list(processor.process_stream(lines))
    assert len(results) == 3

    assert results[0]["line_number"] == 1
    assert results[0]["status"] == "SUCCESS"
    assert results[0]["command"] == "REGISTER_PRODUCT"

    assert results[1]["line_number"] == 2
    assert results[1]["status"] == "ERROR"
    assert results[1]["error"]["code"] == "MALFORMED_JSON"

    assert results[2]["line_number"] == 3
    assert results[2]["status"] == "SUCCESS"
    assert results[2]["command"] == "REGISTER_RIDER"

    # Confirm state from line 1 is still present in orchestrator
    assert "SKU-ISOLATION" in processor.orchestrator.products
    assert "R-ISO" in processor.orchestrator.riders


def test_decimal_safe_deterministic_serialization():
    record = {
        "line_number": 1,
        "command": "TEST_FINANCE",
        "status": "SUCCESS",
        "result": {
            "amount": Decimal("105.50"),
            "zero": Decimal("0.00"),
            "int_val": 42,
            "items": sorted(["beta", "alpha"]),
        },
        "error": None,
    }

    serialized = serialize_record(record)
    parsed = json.loads(serialized)

    assert parsed["result"]["amount"] == "105.50"
    assert parsed["result"]["zero"] == "0.00"

    # Determinism: serialize again and verify exact byte match
    serialized2 = serialize_record(record)
    assert serialized == serialized2


def test_process_file_roundtrip(tmp_path: Path):
    in_file = tmp_path / "in.jsonl"
    out_file = tmp_path / "out.jsonl"

    in_content = (
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R1", "name": "Ramesh"}}\n'
        "\n"
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:01:00", "payload": {"rider_id": "R2", "name": "Suresh"}}\n'
    )
    in_file.write_text(in_content, encoding="utf-8")

    processor = JSONLCommandProcessor()
    results = processor.process_file(in_file, output_path=out_file)

    assert len(results) == 2
    assert out_file.exists()

    written_lines = [json.loads(l) for l in out_file.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    assert len(written_lines) == 2
    assert written_lines[0]["result"]["rider_id"] == "R1"
    assert written_lines[1]["result"]["rider_id"] == "R2"


def test_envelope_timestamp_defaulting_and_payload_precedence():
    """Commands inherit envelope timestamp by default, but explicit payload timestamp takes precedence."""
    processor = JSONLCommandProcessor()
    # 1. Register product & store
    lines_init = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T08:00:00", "payload": {"sku": "SKU-TS", "name": "Item", "category": "Cat", "brand": "Brand", "price": "20.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T08:00:00", "payload": {"store_id": "STORE-TS", "name": "Store", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-TS": 50}}}',
    ]
    for _ in processor.process_stream(lines_init):
        pass

    # Order A: no order_time in payload -> inherits envelope timestamp
    line_a = '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:00:00", "payload": {"order_id": "ORD-TS-A", "customer_id": "C-A", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-TS", "quantity": 1}]}}'
    res_a = processor.process_line(line_a, 3)
    assert res_a["status"] == "SUCCESS"
    assert processor.orchestrator.orders["ORD-TS-A"].order_time == datetime(2026, 11, 6, 10, 0, 0)

    # Order B: explicit order_time in payload takes precedence over envelope timestamp
    line_b = '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:00:00", "payload": {"order_id": "ORD-TS-B", "customer_id": "C-B", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-TS", "quantity": 1}], "order_time": "2026-11-06T10:30:00"}}'
    res_b = processor.process_line(line_b, 4)
    assert res_b["status"] == "SUCCESS"
    assert processor.orchestrator.orders["ORD-TS-B"].order_time == datetime(2026, 11, 6, 10, 30, 0)


def test_deterministic_replay_produces_identical_output():
    """Processing identical JSONL streams across separate runs yields identical serialized output."""
    stream_content = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T09:00:00", "payload": {"sku": "SKU-DET-1", "name": "DetProd", "category": "Cat", "brand": "Brand", "price": "100.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T09:01:00", "payload": {"store_id": "STORE-DET-1", "name": "DetStore", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-DET-1": 20}}}',
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T09:02:00", "payload": {"order_id": "ORD-DET-1", "customer_id": "CUST-DET", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-DET-1", "quantity": 2}]}}',
    ]

    p1 = JSONLCommandProcessor()
    out1 = [serialize_record(r) for r in p1.process_stream(stream_content)]

    p2 = JSONLCommandProcessor()
    out2 = [serialize_record(r) for r in p2.process_stream(stream_content)]

    assert len(out1) == 3
    assert out1 == out2


def test_internal_programming_type_error_not_misclassified_as_invalid_argument(monkeypatch, capsys):
    """An internal TypeError during execution is classified as EXECUTION_ERROR and logged to stderr."""
    processor = JSONLCommandProcessor()

    def _broken_register(*args, **kwargs):
        # Simulates an internal programming bug: adding int to str
        return 42 + "string"  # Raises TypeError: unsupported operand type(s)

    monkeypatch.setattr(processor.orchestrator, "register_rider", _broken_register)

    line = '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R-TYPE", "name": "Rider"}}'
    res = processor.process_line(line, 1)

    assert res["status"] == "ERROR"
    # Must NOT be classified as INVALID_ARGUMENT (the caller provided valid arguments)
    assert res["error"]["code"] == "EXECUTION_ERROR"
    assert "Internal execution error" in res["error"]["message"]


def test_duplicate_registration_returns_validation_error():
    """Duplicate entity registration emits VALIDATION_ERROR and preserves initial entity."""
    processor = JSONLCommandProcessor()
    line1 = '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-DUP-JSONL", "name": "Original", "category": "Cat", "brand": "Brand", "price": "10.00"}}'
    line2 = '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:01:00", "payload": {"sku": "SKU-DUP-JSONL", "name": "Duplicate", "category": "Cat", "brand": "Brand", "price": "99.00"}}'

    res1 = processor.process_line(line1, 1)
    res2 = processor.process_line(line2, 2)

    assert res1["status"] == "SUCCESS"
    assert res2["status"] == "ERROR"
    assert res2["error"]["code"] == "VALIDATION_ERROR"
    assert "already registered" in res2["error"]["message"]
    assert processor.orchestrator.products["SKU-DUP-JSONL"].name == "Original"


def test_unexpected_payload_argument_returns_invalid_argument():
    """Unexpected payload parameters not accepted by the handler signature return INVALID_ARGUMENT."""
    processor = JSONLCommandProcessor()
    line = '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:00:00", "payload": {"rider_id": "R-UNEXP", "name": "Rider", "unexpected_param": "bad"}}'
    res = processor.process_line(line, line_number=1)

    assert res["status"] == "ERROR"
    assert res["error"]["code"] == "INVALID_ARGUMENT"
    assert "unexpected keyword argument 'unexpected_param'" in res["error"]["message"]


def test_invalid_explicit_timestamp_in_payload_returns_validation_error():
    """Explicit invalid or malformed timestamps in payload produce clear VALIDATION_ERROR."""
    processor = JSONLCommandProcessor()
    # 1. Non-ISO string timestamp
    line1 = '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:00:00", "payload": {"order_id": "ORD-BAD-TS", "customer_id": "C1", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-1", "quantity": 1}], "order_time": "invalid-datetime-string"}}'
    res1 = processor.process_line(line1, line_number=1)

    assert res1["status"] == "ERROR"
    assert res1["error"]["code"] == "VALIDATION_ERROR"
    assert "Invalid ISO 8601 'order_time'" in res1["error"]["message"]

    # 2. Non-string timestamp (e.g. integer)
    line2 = '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:00:00", "payload": {"order_id": "ORD-BAD-TS2", "customer_id": "C1", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-1", "quantity": 1}], "order_time": 1234567}}'
    res2 = processor.process_line(line2, line_number=2)

    assert res2["status"] == "ERROR"
    assert res2["error"]["code"] == "VALIDATION_ERROR"
    assert "Explicit 'order_time' must be an ISO 8601 string" in res2["error"]["message"]


def test_internal_error_traceback_written_to_stderr(monkeypatch, capsys):
    """Internal runtime errors output a full traceback on stderr while keeping structured JSONL error."""
    processor = JSONLCommandProcessor()

    def _crash(*args, **kwargs):
        raise RuntimeError("Fatal simulated internal engine fault")

    monkeypatch.setattr(processor.orchestrator, "register_product", _crash)

    line = '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-CRASH", "name": "Item", "category": "Cat", "brand": "Brand", "price": "10.00"}}'
    res = processor.process_line(line, line_number=5)

    assert res["status"] == "ERROR"
    assert res["error"]["code"] == "EXECUTION_ERROR"
    assert "Fatal simulated internal engine fault" in res["error"]["message"]

    captured = capsys.readouterr()
    assert "[Line 5] Unexpected internal error in REGISTER_PRODUCT:" in captured.err
    assert "Traceback (most recent call last):" in captured.err
    assert "Fatal simulated internal engine fault" in captured.err


def test_complete_e2e_deterministic_replay_produces_identical_jsonl():
    """Full lifecycle replay across two independent processor instances produces byte-for-byte identical output."""
    commands = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-REP-1", "name": "Milk", "category": "Dairy", "brand": "Amul", "price": "50.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T10:01:00", "payload": {"store_id": "STORE-REP-1", "name": "Dark Store", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-REP-1": 100}}}',
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:02:00", "payload": {"rider_id": "RIDER-REP-1", "name": "Rider One"}}',
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T10:03:00", "payload": {"order_id": "ORD-REP-1", "customer_id": "C-REP", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-REP-1", "quantity": 2}]}}',
        '{"command": "START_PICKING", "timestamp": "2026-11-06T10:04:00", "payload": {"order_id": "ORD-REP-1"}}',
        '{"command": "FULFIL_ORDER", "timestamp": "2026-11-06T10:05:00", "payload": {"order_id": "ORD-REP-1"}}',
        '{"command": "CREATE_BATCH", "timestamp": "2026-11-06T10:06:00", "payload": {"batch_id": "BATCH-REP-1", "store_id": "STORE-REP-1", "rider_id": "RIDER-REP-1"}}',
        '{"command": "ADD_ORDER_TO_BATCH", "timestamp": "2026-11-06T10:07:00", "payload": {"batch_id": "BATCH-REP-1", "order_id": "ORD-REP-1"}}',
        '{"command": "DISPATCH_BATCH", "timestamp": "2026-11-06T10:11:00", "payload": {"batch_id": "BATCH-REP-1"}}',
        '{"command": "COMPLETE_ORDER_DELIVERY", "timestamp": "2026-11-06T10:15:00", "payload": {"batch_id": "BATCH-REP-1", "order_id": "ORD-REP-1"}}',
        '{"command": "EVALUATE_RIDER_PENALTIES", "timestamp": "2026-11-06T10:16:00", "payload": {"batch_id": "BATCH-REP-1"}}',
        '{"command": "GET_ORDER_TRACE", "timestamp": "2026-11-06T10:17:00", "payload": {"order_id": "ORD-REP-1"}}',
    ]

    p1 = JSONLCommandProcessor()
    out1 = [serialize_record(r) for r in p1.process_stream(commands)]

    p2 = JSONLCommandProcessor()
    out2 = [serialize_record(r) for r in p2.process_stream(commands)]

    assert len(out1) == len(commands)
    assert out1 == out2


def test_all_nine_time_sensitive_commands_envelope_timestamp_propagation():
    """Verify that all 9 time-sensitive commands use envelope timestamp when omitted in payload."""
    proc = JSONLCommandProcessor()

    # Setup: Product, Store, Rider
    init_lines = [
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T08:00:00", "payload": {"sku": "SKU-TIME-1", "name": "Time Item", "category": "Cat", "brand": "Brand", "price": "100.00"}}',
        '{"command": "REGISTER_STORE", "timestamp": "2026-11-06T08:01:00", "payload": {"store_id": "STORE-TIME-1", "name": "Time Store", "location": {"latitude": 12.9716, "longitude": 77.5946}, "operating_hours": {"open_time": "06:00:00", "close_time": "23:00:00"}, "inventory": {"SKU-TIME-1": 50}}}',
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T08:02:00", "payload": {"rider_id": "RIDER-TIME-1", "name": "Time Rider"}}',
    ]
    for line in init_lines:
        res = proc.process_line(line, 1)
        assert res["status"] == "SUCCESS"

    # 1. SUBMIT_ORDER (order_time defaulted from envelope timestamp)
    res_sub = proc.process_line(
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T09:00:00", "payload": {"order_id": "ORD-T1", "customer_id": "C-T1", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-TIME-1", "quantity": 1}]}}',
        4,
    )
    assert res_sub["status"] == "SUCCESS"
    assert proc.orchestrator.orders["ORD-T1"].order_time == datetime(2026, 11, 6, 9, 0, 0)

    # 2. APPLY_WEATHER_HOLD (hold_start defaulted from envelope timestamp)
    res_hold = proc.process_line(
        '{"command": "APPLY_WEATHER_HOLD", "timestamp": "2026-11-06T09:05:00", "payload": {"event_id": "HOLD-EV1", "store_id": "STORE-TIME-1"}}',
        5,
    )
    assert res_hold["status"] == "SUCCESS"
    assert res_hold["result"]["hold_start"] == "2026-11-06T09:05:00"

    # 3. CREATE_BATCH (created_at defaulted from envelope timestamp)
    res_cb = proc.process_line(
        '{"command": "CREATE_BATCH", "timestamp": "2026-11-06T09:10:00", "payload": {"batch_id": "BATCH-T1", "store_id": "STORE-TIME-1", "rider_id": "RIDER-TIME-1"}}',
        6,
    )
    assert res_cb["status"] == "SUCCESS"
    assert proc.orchestrator.batches["BATCH-T1"].created_at == datetime(2026, 11, 6, 9, 10, 0)

    # Transition order to READY_FOR_DISPATCH so it can be added to batch
    proc.process_line('{"command": "START_PICKING", "timestamp": "2026-11-06T09:11:00", "payload": {"order_id": "ORD-T1"}}', 7)
    proc.process_line('{"command": "FULFIL_ORDER", "timestamp": "2026-11-06T09:12:00", "payload": {"order_id": "ORD-T1"}}', 8)

    # 4. ADD_ORDER_TO_BATCH (current_time defaulted from envelope timestamp)
    res_add = proc.process_line(
        '{"command": "ADD_ORDER_TO_BATCH", "timestamp": "2026-11-06T09:15:00", "payload": {"batch_id": "BATCH-T1", "order_id": "ORD-T1"}}',
        9,
    )
    assert res_add["status"] == "SUCCESS"

    # 5. DISPATCH_BATCH (dispatched_at defaulted from envelope timestamp, after 3-min timer: 9:15 + 3 = 9:18)
    res_disp = proc.process_line(
        '{"command": "DISPATCH_BATCH", "timestamp": "2026-11-06T09:20:00", "payload": {"batch_id": "BATCH-T1"}}',
        10,
    )
    assert res_disp["status"] == "SUCCESS"
    assert proc.orchestrator.batches["BATCH-T1"].dispatched_at == datetime(2026, 11, 6, 9, 20, 0)

    # 6. COMPLETE_ORDER_DELIVERY (delivered_at defaulted from envelope timestamp)
    res_del_ord = proc.process_line(
        '{"command": "COMPLETE_ORDER_DELIVERY", "timestamp": "2026-11-06T09:25:00", "payload": {"batch_id": "BATCH-T1", "order_id": "ORD-T1"}}',
        11,
    )
    assert res_del_ord["status"] == "SUCCESS"
    assert res_del_ord["result"]["delivered_at"] == "2026-11-06T09:25:00"
    assert proc.orchestrator.orders["ORD-T1"].actual_delivery_time == datetime(2026, 11, 6, 9, 25, 0)

    # 7. COMPLETE_BATCH_DELIVERY (delivered_at defaulted from envelope timestamp)
    res_del_b = proc.process_line(
        '{"command": "COMPLETE_BATCH_DELIVERY", "timestamp": "2026-11-06T09:25:00", "payload": {"batch_id": "BATCH-T1"}}',
        12,
    )
    assert res_del_b["status"] == "SUCCESS"

    # 8. EVALUATE_RIDER_PENALTIES (delivered_at defaulted from envelope timestamp)
    res_pen = proc.process_line(
        '{"command": "EVALUATE_RIDER_PENALTIES", "timestamp": "2026-11-06T09:26:00", "payload": {"batch_id": "BATCH-T1"}}',
        13,
    )
    assert res_pen["status"] == "SUCCESS"

    # 9. CANCEL_ORDER (cancelled_at defaulted from envelope timestamp)
    # Submit second order to cancel
    proc.process_line(
        '{"command": "SUBMIT_ORDER", "timestamp": "2026-11-06T09:30:00", "payload": {"order_id": "ORD-T2", "customer_id": "C-T2", "customer_lat": 12.9720, "customer_lon": 77.5948, "items": [{"sku_id": "SKU-TIME-1", "quantity": 1}]}}',
        14,
    )
    res_canc = proc.process_line(
        '{"command": "CANCEL_ORDER", "timestamp": "2026-11-06T09:31:00", "payload": {"order_id": "ORD-T2"}}',
        15,
    )
    assert res_canc["status"] == "SUCCESS"
    assert proc.orchestrator.orders["ORD-T2"].status.value == "CANCELLED"
    assert proc.orchestrator.cancellation_service._operation_state["ORD-T2"].cancelled_at == datetime(2026, 11, 6, 9, 31, 0)
