"""Tests for the PS-031 Command Processor CLI entry point (main.py)."""

import io
import json
from pathlib import Path
import sys
import pytest

from ps031.main import build_parser, run_cli, run_pipeline


def test_build_parser_options():
    parser = build_parser()
    args = parser.parse_args(["commands.jsonl", "-o", "output.jsonl"])
    assert args.input_file == "commands.jsonl"
    assert args.output_file == "output.jsonl"

    args_opt = parser.parse_args(["-i", "in.jsonl", "--output", "out.jsonl"])
    assert args_opt.input_opt == "in.jsonl"
    assert args_opt.output_file == "out.jsonl"


def test_run_pipeline_stdin_stdout_stream():
    input_text = (
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-1", "name": "Item", "category": "General", "brand": "Brand", "price": "10.00"}}\n'
    )
    in_stream = io.StringIO(input_text)
    out_stream = io.StringIO()

    exit_code = run_pipeline(in_stream, out_stream)
    assert exit_code == 0

    lines = [json.loads(line) for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 1
    assert lines[0]["command"] == "REGISTER_PRODUCT"
    assert lines[0]["status"] == "SUCCESS"
    assert lines[0]["result"]["sku"] == "SKU-1"


def test_run_cli_with_input_and_output_files(tmp_path: Path):
    in_file = tmp_path / "commands.jsonl"
    out_file = tmp_path / "results.jsonl"

    in_content = (
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-CLI", "name": "CliProd", "category": "Cat", "brand": "Brand", "price": "25.50"}}\n'
        "\n"
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:01:00", "payload": {"rider_id": "RIDER-CLI", "name": "CliRider"}}\n'
    )
    in_file.write_text(in_content, encoding="utf-8")

    exit_code = run_cli([str(in_file), "-o", str(out_file)])
    assert exit_code == 0
    assert out_file.exists()

    records = [json.loads(l) for l in out_file.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    assert len(records) == 2
    assert records[0]["line_number"] == 1
    assert records[0]["command"] == "REGISTER_PRODUCT"
    assert records[0]["result"]["price"] == "25.50"

    assert records[1]["line_number"] == 3
    assert records[1]["command"] == "REGISTER_RIDER"
    assert records[1]["result"]["rider_id"] == "RIDER-CLI"


def test_run_cli_missing_input_file_returns_error(capsys):
    exit_code = run_cli(["nonexistent_file_path.jsonl"])
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert captured.out == ""  # Stdout must remain clean


def test_cli_stdout_stderr_separation_with_runtime_error(tmp_path: Path, capsys, monkeypatch):
    """CLI stream keeps stdout strictly pure JSONL and sends traceback diagnostics only to stderr."""
    in_file = tmp_path / "mixed_commands.jsonl"
    in_content = (
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:00:00", "payload": {"sku": "SKU-MIX-1", "name": "Prod1", "category": "Cat", "brand": "Brand", "price": "10.00"}}\n'
        '{"command": "REGISTER_RIDER", "timestamp": "2026-11-06T10:01:00", "payload": {"rider_id": "RIDER-MIX-1", "name": "Rider"}}\n'
        '{"command": "REGISTER_PRODUCT", "timestamp": "2026-11-06T10:02:00", "payload": {"sku": "SKU-MIX-2", "name": "Prod2", "category": "Cat", "brand": "Brand", "price": "20.00"}}\n'
    )
    in_file.write_text(in_content, encoding="utf-8")

    # Inject simulated fault in register_rider
    from ps031.orchestrator import ApplicationOrchestrator

    def _broken_register(*args, **kwargs):
        raise RuntimeError("Fatal simulated database engine error")

    monkeypatch.setattr(ApplicationOrchestrator, "register_rider", _broken_register)

    exit_code = run_cli([str(in_file)])
    assert exit_code == 0

    captured = capsys.readouterr()

    # Verify stdout: must be pure JSONL lines only
    stdout_lines = [l.strip() for l in captured.out.strip().split("\n") if l.strip()]
    assert len(stdout_lines) == 3

    parsed_records = [json.loads(l) for l in stdout_lines]
    assert parsed_records[0]["command"] == "REGISTER_PRODUCT"
    assert parsed_records[0]["status"] == "SUCCESS"

    assert parsed_records[1]["command"] == "REGISTER_RIDER"
    assert parsed_records[1]["status"] == "ERROR"
    assert parsed_records[1]["error"]["code"] == "EXECUTION_ERROR"

    assert parsed_records[2]["command"] == "REGISTER_PRODUCT"
    assert parsed_records[2]["status"] == "SUCCESS"

    # Verify stderr: contains diagnostic log and traceback
    assert "[Line 2] Unexpected internal error in REGISTER_RIDER:" in captured.err
    assert "Fatal simulated database engine error" in captured.err
    assert "Traceback (most recent call last):" in captured.err
