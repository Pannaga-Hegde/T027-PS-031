"""Tests for the standalone execution_monitor module.

Verifies:
1. CSV simulation mode execution and rich console reporting.
2. Exact byte-for-byte parity of output CSV files with official engine output.
3. Strict separation of machine-readable stdout and human-readable stderr in JSONL mode.
4. CLI argument handling, error reporting, and `-o` alias support.
5. Terminal styling behaviour on TTY vs non-TTY streams.
"""

from __future__ import annotations

import filecmp
import io
import json
import os
from pathlib import Path
import shutil
import sys
import pytest

from execution_monitor import (
    CSVSimulationMonitor,
    JSONLStreamMonitor,
    MonitoredCLIParser,
    TerminalStyler,
    build_parser,
    main as monitor_main,
)
from ps031.main import _run_csv_simulation


# Path constants
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COMMANDS_PATH = DATA_DIR / "commands.txt"
HAPPY_PATH_JSONL = Path(__file__).resolve().parent.parent / "examples" / "happy_path.jsonl"


# ==============================================================================
# 1. CSV SIMULATION MONITOR TESTS
# ==============================================================================


def test_csv_monitored_simulation_runs_and_emits_output(tmp_path: Path):
    """Verify CSV simulation completes with returncode 0 and generates all reports."""
    output_dir = tmp_path / "csv_out"
    log_stream = io.StringIO()
    monitor = CSVSimulationMonitor(out_stream=log_stream)

    exit_code = monitor.run(
        data_dir=DATA_DIR,
        commands_path=COMMANDS_PATH,
        output_dir=output_dir,
    )

    assert exit_code == 0
    output_text = log_stream.getvalue()

    # Verify banner and sections
    assert "QUICK COMMERCE ORDER PROCESSING" in output_text
    assert "TEAM T027" in output_text
    assert "DATASET INITIALIZATION" in output_text
    assert "COMMAND EXECUTION" in output_text
    assert "REPORT GENERATION" in output_text
    assert "EXECUTION SUMMARY" in output_text

    # Verify specific loading lines
    assert "Successfully loaded 4 stores" in output_text
    assert "Successfully loaded 40 SKUs" in output_text
    assert "Successfully loaded 160 stock records" in output_text
    assert "Successfully loaded 15 riders" in output_text
    assert "Successfully parsed 130 commands" in output_text

    # Verify LOAD_DELIVERIES display and events loaded
    assert "LOAD_DELIVERIES | EXCEPTION" not in output_text
    assert "Loaded 55 actual delivery events from delivery_events.csv" in output_text
    assert "Delivery events loaded   : 55" in output_text

    # Verify report files were generated
    assert (output_dir / "order_trace.csv").exists()
    assert (output_dir / "order_lines.csv").exists()
    assert (output_dir / "rider_payouts.csv").exists()


def test_csv_load_deliveries_command_display(tmp_path: Path):
    """Regression test: verify LOAD_DELIVERIES displays filename and loaded count without exception."""
    output_dir = tmp_path / "csv_out"
    log_stream = io.StringIO()
    monitor = CSVSimulationMonitor(out_stream=log_stream)

    exit_code = monitor.run(
        data_dir=DATA_DIR,
        commands_path=COMMANDS_PATH,
        output_dir=output_dir,
    )

    assert exit_code == 0
    output_text = log_stream.getvalue()

    # Must not contain any LoadDeliveriesCommand attribute exception
    assert "'LoadDeliveriesCommand' object has no attribute" not in output_text
    assert "LOAD_DELIVERIES | EXCEPTION" not in output_text
    # Must correctly format filename and loaded count
    assert "Loaded 55 actual delivery events from delivery_events.csv" in output_text
    assert "Delivery events loaded   : 55" in output_text


def test_csv_report_parity_with_main(tmp_path: Path):
    """Verify generated CSV reports are 100% byte-for-byte identical to main.py."""
    dir_main = tmp_path / "main_out"
    dir_mon = tmp_path / "mon_out"

    # 1. Run official engine
    ret_main = _run_csv_simulation(
        data_dir=DATA_DIR,
        commands_path=COMMANDS_PATH,
        output_dir=dir_main,
    )
    assert ret_main == 0

    # 2. Run monitor
    monitor = CSVSimulationMonitor(out_stream=io.StringIO())
    ret_mon = monitor.run(
        data_dir=DATA_DIR,
        commands_path=COMMANDS_PATH,
        output_dir=dir_mon,
    )
    assert ret_mon == 0

    # 3. Compare reports byte-for-byte
    for fname in ["order_trace.csv", "order_lines.csv", "rider_payouts.csv"]:
        f_main = dir_main / fname
        f_mon = dir_mon / fname
        assert f_main.exists()
        assert f_mon.exists()
        assert filecmp.cmp(f_main, f_mon, shallow=False), f"Mismatch in {fname}"


def test_csv_missing_dataset_directory(tmp_path: Path):
    """Verify monitor handles non-existent dataset directory gracefully."""
    output_dir = tmp_path / "csv_out"
    log_stream = io.StringIO()
    monitor = CSVSimulationMonitor(out_stream=log_stream)

    exit_code = monitor.run(
        data_dir=tmp_path / "non_existent_data",
        commands_path=COMMANDS_PATH,
        output_dir=output_dir,
    )

    assert exit_code == 1
    assert "ERROR" in log_stream.getvalue()


# ==============================================================================
# 2. JSONL STREAMING MONITOR TESTS
# ==============================================================================


def test_jsonl_stream_monitor_strict_separation():
    """Verify stdout receives pure JSONL only and stderr receives monitor telemetry."""
    in_text = (
        '{"command":"REGISTER_PRODUCT","timestamp":"2026-11-06T09:00:00","payload":{"sku":"SKU-1","name":"A","category":"C","brand":"B","price":"10.00"}}\n'
        '{"command":"REGISTER_STORE","timestamp":"2026-11-06T09:01:00","payload":{"store_id":"S1","name":"Store 1","location":{"latitude":12.9,"longitude":77.6},"operating_hours":{"open_time":"06:00:00","close_time":"23:00:00"}}}\n'
    )
    in_stream = io.StringIO(in_text)
    out_stream = io.StringIO()
    mon_stream = io.StringIO()

    monitor = JSONLStreamMonitor(monitor_stream=mon_stream)
    exit_code = monitor.process_stream(in_stream, out_stream)

    assert exit_code == 0

    # Verify stdout: pure JSONL only, every line must parse cleanly
    out_lines = [l.strip() for l in out_stream.getvalue().strip().split("\n") if l.strip()]
    assert len(out_lines) == 2
    for l in out_lines:
        rec = json.loads(l)
        assert rec["status"] == "SUCCESS"

    # Verify stderr: contains human-readable monitor messages
    mon_text = mon_stream.getvalue()
    assert "PS-031 JSONL STREAM PROCESSOR" in mon_text
    assert "REAL-TIME MONITOR" in mon_text
    assert "REGISTER_PRODUCT" in mon_text
    assert "REGISTER_STORE" in mon_text
    assert "STREAMING EXECUTION SUMMARY" in mon_text


def test_jsonl_stream_monitor_handles_error_envelopes():
    """Verify monitor handles malformed JSON line and reports error on stderr."""
    in_text = (
        '{"invalid json syntax\n'
        '{"command":"UNKNOWN_COMMAND","timestamp":"2026-11-06T09:00:00","payload":{}}\n'
    )
    in_stream = io.StringIO(in_text)
    out_stream = io.StringIO()
    mon_stream = io.StringIO()

    monitor = JSONLStreamMonitor(monitor_stream=mon_stream)
    exit_code = monitor.process_stream(in_stream, out_stream)

    # Returns 1 when errors are encountered
    assert exit_code == 1

    out_lines = [l.strip() for l in out_stream.getvalue().strip().split("\n") if l.strip()]
    assert len(out_lines) == 2

    # Verify records were emitted with status ERROR
    r1 = json.loads(out_lines[0])
    assert r1["status"] == "ERROR"
    assert r1["error"]["code"] == "MALFORMED_JSON"

    r2 = json.loads(out_lines[1])
    assert r2["status"] == "ERROR"
    assert r2["error"]["code"] == "UNKNOWN_COMMAND"

    # Verify stderr contains error indicators
    mon_text = mon_stream.getvalue()
    assert "MALFORMED_JSON" in mon_text
    assert "UNKNOWN_COMMAND" in mon_text
    assert "Errors encountered   : 2" in mon_text


# ==============================================================================
# 3. CLI ARGUMENT PARSER TESTS
# ==============================================================================


def test_cli_parser_csv_mode_requires_all_three_arguments():
    """Verify that providing incomplete CSV flags raises argument error."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--data-dir", "data"])


def test_cli_parser_output_dir_alias():
    """Verify -o / --output serves as alias for output_dir in CSV mode."""
    parser = build_parser()
    parsed = parser.parse_args([
        "--data-dir", "data",
        "--commands", "data/commands.txt",
        "-o", "custom_output",
    ])
    assert parsed.output_dir == Path("custom_output")


def test_cli_parser_jsonl_arguments():
    """Verify JSONL arguments are parsed correctly."""
    parser = build_parser()
    parsed = parser.parse_args(["-i", "input.jsonl", "-o", "output.jsonl"])
    assert parsed.input_opt == "input.jsonl"
    assert parsed.output_file == "output.jsonl"


# ==============================================================================
# 4. TERMINAL STYLER TESTS
# ==============================================================================


def test_terminal_styler_strips_ansi_on_non_tty():
    """Verify ANSI escape codes are omitted when stream is not a TTY."""
    stream = io.StringIO()
    styler = TerminalStyler(stream)
    assert styler.is_tty is False

    text = "Hello World"
    assert styler.bold(text) == text
    assert styler.green(text) == text
    assert styler.red(text) == text
    assert styler.header(text) == text
    assert "\033[" not in styler.bold(text)
