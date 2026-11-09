"""End-to-end regression test for the documented JSONL happy-path example."""

import json
from pathlib import Path

from ps031.main import run_cli


def test_documented_happy_path_through_cli(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    sample = repo_root / "examples" / "happy_path.jsonl"
    output = tmp_path / "happy_path_output.jsonl"

    assert sample.is_file(), "Happy-path example file is missing"
    assert not sample.read_bytes().startswith(b"\xef\xbb\xbf"), (
        "JSONL example must be UTF-8 without a BOM"
    )

    exit_code = run_cli([str(sample), "-o", str(output)])

    assert exit_code == 0
    assert output.is_file()

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    expected_commands = [
        "REGISTER_PRODUCT",
        "REGISTER_STORE",
        "REGISTER_RIDER",
        "SUBMIT_ORDER",
        "START_PICKING",
        "FULFIL_ORDER",
        "CREATE_BATCH",
        "ADD_ORDER_TO_BATCH",
        "DISPATCH_BATCH",
        "COMPLETE_BATCH_DELIVERY",
        "GET_ORDER_TRACE",
    ]

    assert len(records) == len(expected_commands)
    assert [record["command"] for record in records] == expected_commands

    for record in records:
        assert record["status"] == "SUCCESS", (
            f"Line {record['line_number']} ({record['command']}) failed: "
            f"{record['error']}"
        )
        assert record["error"] is None

    trace = records[-1]["result"]

    assert trace["order_id"] == "ORD-STREAM-1"
    assert trace["status"] == "DELIVERED"
    assert trace["allocated_store_id"] == "STORE-P1"
    assert trace["order_value"] == "240.00"
    assert trace["fulfilled_value"] == "240.00"
    assert trace["event_count"] == 8

    event_types = [event["event_type"] for event in trace["events"]]
    assert event_types == [
        "ORDER_SUBMITTED",
        "ORDER_ALLOCATED",
        "PICKING_SCHEDULED",
        "PICKING_STARTED",
        "ORDER_FULFILLED",
        "BATCH_ASSIGNED",
        "ORDER_DISPATCHED",
        "ORDER_DELIVERED",
    ]

    executed_rules = {
        rule
        for event in trace["events"]
        for rule in event["business_rules"]
    }
    assert {f"BR-{number:02d}" for number in range(1, 10)} <= executed_rules

    # This sample deliberately exercises the late-delivery compensation path.
    assert trace["wallet_credit"] == "50.00"
    assert trace["free_coupon"] is True
