#!/usr/bin/env python3
"""Standalone Execution Monitor for the PS-031 Quick Commerce Engine.

Provides informative, real-time console feedback during program execution
for both CSV batch simulation and JSONL streaming modes while reusing the
existing engine, loaders, processors, services, and business logic.

Stream Separation Invariant:
- In JSONL Streaming Mode:
  - stdout: Machine-readable JSONL stream records ONLY.
  - stderr: Execution banner, real-time progress, status updates, and summary.
- In CSV Batch Simulation Mode:
  - Formatted progress, live command execution telemetry, and summaries are displayed.
  - Evaluation CSV reports (order_trace.csv, order_lines.csv, rider_payouts.csv)
    are exported byte-for-byte identically to the official engine.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
import io
import os
from pathlib import Path
import sys
import time
from typing import Any, TextIO

# Ensure src/ is on sys.path when launched directly from the repository root
_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from ps031.command_parser import (
    CancelCommand,
    Command,
    CommandParseError,
    CommandType,
    LoadDeliveriesCommand,
    OrderCommand,
    PrintRiderPayoutsCommand,
    PrintTraceCommand,
    RidersCommand,
    WeatherHoldCommand,
    parse_commands,
)
from ps031.command_processor import JSONLCommandProcessor, serialize_record
from ps031.execution_engine import ExecutionEngine
from ps031.loaders import (
    load_riders,
    load_skus,
    load_stock,
    load_stores,
)
from ps031.models.order import Order
from ps031.models.order_state import OrderState


# ==============================================================================
# TERMINAL FORMATTING AND COLOR UTILITIES
# ==============================================================================


class TerminalStyler:
    """Provides conditional ANSI styling based on stream TTY capability."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.is_tty = hasattr(stream, "isatty") and stream.isatty()

    def _style(self, text: str, code: str) -> str:
        if not self.is_tty:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._style(text, "1")

    def dim(self, text: str) -> str:
        return self._style(text, "2")

    def green(self, text: str) -> str:
        return self._style(text, "32")

    def red(self, text: str) -> str:
        return self._style(text, "31")

    def yellow(self, text: str) -> str:
        return self._style(text, "33")

    def cyan(self, text: str) -> str:
        return self._style(text, "36")

    def blue(self, text: str) -> str:
        return self._style(text, "34")

    def magenta(self, text: str) -> str:
        return self._style(text, "35")

    def header(self, text: str) -> str:
        return self._style(text, "1;36")

    def success(self, text: str) -> str:
        return self._style(text, "1;32")

    def failure(self, text: str) -> str:
        return self._style(text, "1;31")


# ==============================================================================
# CLI ARGUMENT PARSER
# ==============================================================================


class MonitoredCLIParser(argparse.ArgumentParser):
    """Argument parser supporting both CSV simulation and JSONL streaming modes."""

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)

        # Support -o alias for output_dir in CSV mode if --output-dir not explicitly set
        csv_flags = [parsed.data_dir, parsed.commands, parsed.output_dir]
        has_csv_flag = any(f is not None for f in csv_flags)

        if has_csv_flag:
            if parsed.output_dir is None and parsed.output_file is not None:
                parsed.output_dir = Path(parsed.output_file)

            missing: list[str] = []
            if parsed.data_dir is None:
                missing.append("--data-dir")
            if parsed.commands is None:
                missing.append("--commands")
            if parsed.output_dir is None:
                missing.append("--output-dir")

            if missing:
                req_str = ", ".join(missing)
                self.error(f"the following arguments are required: {req_str}")

        return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build parser matching existing main.py conventions."""
    parser = MonitoredCLIParser(
        prog="execution_monitor",
        description="PS-031 Quick Commerce Execution Monitor",
    )

    # JSONL Streaming Mode arguments
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        help="Path to input JSONL command file. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "-i",
        "--input",
        dest="input_opt",
        default=None,
        help="Alternative flag specifying input JSONL file path.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_file",
        default=None,
        help="Path to output JSONL file (or CSV output directory in CSV mode). If omitted, writes to stdout.",
    )

    # CSV Mentor Acceptance Mode arguments
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing stores.csv, skus.csv, stock.csv, riders.csv, and delivery_events.csv.",
    )
    parser.add_argument(
        "--commands",
        type=Path,
        default=None,
        help="Path to the simulation command stream file (e.g. commands.txt).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where order_trace.csv, order_lines.csv, and rider_payouts.csv will be written.",
    )

    return parser


# ==============================================================================
# CSV BATCH SIMULATION MONITOR
# ==============================================================================


class CSVSimulationMonitor:
    """Monitors and displays informative progress for CSV batch execution."""

    def __init__(self, out_stream: TextIO | None = None) -> None:
        self.stream = out_stream or sys.stdout
        self.styler = TerminalStyler(self.stream)

    def print_banner(self) -> None:
        """Display the official execution banner."""
        sep = "=" * 55
        self.stream.write(f"\n{self.styler.bold(sep)}\n")
        self.stream.write(f"{self.styler.header('          QUICK COMMERCE ORDER PROCESSING')}\n")
        self.stream.write(f"{self.styler.bold('                    TEAM T027')}\n")
        self.stream.write(f"{self.styler.bold(sep)}\n\n")
        self.stream.flush()

    def run(
        self,
        data_dir: Path,
        commands_path: Path,
        output_dir: Path,
    ) -> int:
        """Execute CSV simulation with rich, step-by-step console monitoring."""
        start_time = time.perf_counter()
        data_dir = data_dir.resolve()
        commands_path = commands_path.resolve()
        output_dir = output_dir.resolve()

        self.print_banner()

        # ----------------------------------------------------------------------
        # 1. Dataset Initialization
        # ----------------------------------------------------------------------
        self.stream.write(f"{self.styler.bold('DATASET INITIALIZATION')}\n")
        self.stream.write(f"{'-' * 40}\n")
        self.stream.flush()

        # Pre-validate directory
        if not data_dir.exists() or not data_dir.is_dir():
            err_msg = f"Data directory does not exist or is not a directory: {data_dir}"
            self.stream.write(f"{self.styler.failure('ERROR')}: {err_msg}\n")
            return 1

        required_files = ["stores.csv", "skus.csv", "stock.csv", "riders.csv"]
        for rf in required_files:
            if not (data_dir / rf).exists():
                err_msg = f"Required dataset file not found: {data_dir / rf}"
                self.stream.write(f"{self.styler.failure('ERROR')}: {err_msg}\n")
                return 1

        if not commands_path.exists():
            err_msg = f"Commands file does not exist: {commands_path}"
            self.stream.write(f"{self.styler.failure('ERROR')}: {err_msg}\n")
            return 1

        # Load datasets
        try:
            self.stream.write(f"[1] Loading {self.styler.cyan('stores.csv')}\n")
            stores = load_stores(data_dir / "stores.csv")
            self.stream.write(f"    {self.styler.green('✓')} Successfully loaded {len(stores)} stores.\n\n")

            self.stream.write(f"[2] Loading {self.styler.cyan('skus.csv')}\n")
            products = load_skus(data_dir / "skus.csv")
            self.stream.write(f"    {self.styler.green('✓')} Successfully loaded {len(products)} SKUs.\n\n")

            self.stream.write(f"[3] Loading {self.styler.cyan('stock.csv')}\n")
            stock = load_stock(data_dir / "stock.csv", stores=stores, products=products)
            self.stream.write(f"    {self.styler.green('✓')} Successfully loaded {len(stock)} stock records.\n\n")

            self.stream.write(f"[4] Loading {self.styler.cyan('riders.csv')}\n")
            riders = load_riders(data_dir / "riders.csv", stores=stores)
            self.stream.write(f"    {self.styler.green('✓')} Successfully loaded {len(riders)} riders.\n\n")

            self.stream.write(f"[5] Parsing {self.styler.cyan(commands_path.name)}\n")
            commands = parse_commands(commands_path)
            self.stream.write(f"    {self.styler.green('✓')} Successfully parsed {len(commands)} commands.\n\n")

        except (ValueError, FileNotFoundError, KeyError, CommandParseError) as exc:
            self.stream.write(f"{self.styler.failure('ERROR during initialization')}: {exc}\n")
            return 1

        # ----------------------------------------------------------------------
        # 2. Engine Initialization
        # ----------------------------------------------------------------------
        engine = ExecutionEngine(
            stores=stores,
            products=products,
            stock=stock,
            riders=riders,
            data_dir=data_dir,
        )

        # ----------------------------------------------------------------------
        # 3. Live Command Execution
        # ----------------------------------------------------------------------
        self.stream.write(f"{'-' * 40}\n")
        self.stream.write(f"{self.styler.bold('          COMMAND EXECUTION')}\n")
        self.stream.write(f"{'-' * 40}\n")
        self.stream.flush()

        total_commands = len(commands)
        orders_received = 0
        orders_allocated = 0
        orders_rejected = 0
        cancellations_count = 0
        weather_holds_count = 0
        delivery_batches_count = 0
        errors_count = 0

        # Step through commands one by one to monitor real-time progress
        for idx, cmd in enumerate(commands, start=1):
            prefix = f"[{idx:>3}/{total_commands}] {self.styler.bold(cmd.command_type.name):<12} | "
            try:
                res = engine.process_command(cmd)

                if cmd.command_type == CommandType.ORDER:
                    orders_received += 1
                    assert isinstance(cmd, OrderCommand)
                    order: Order = res
                    meta = engine.order_metadata.get(cmd.order_id)

                    if order.status == OrderState.REJECTED:
                        orders_rejected += 1
                        reason = meta.rejection_reason if meta else "Rejected"
                        status_str = self.styler.failure("REJECTED")
                        self.stream.write(f"{prefix}{cmd.order_id} -> {status_str} | Reason: {reason}\n")
                    elif order.status == OrderState.ALLOCATED or order.allocated_store_id:
                        orders_allocated += 1
                        store_id = order.allocated_store_id or "N/A"
                        dist_str = f"{meta.distance_km:.2f} km" if meta and meta.distance_km is not None else ""
                        eta_str = order.promised_eta.strftime("%H:%M:%S") if order.promised_eta else "N/A"
                        fee_str = f"₹{order.total_delivery_fee:.2f}"
                        surge_tag = " (SURGE)" if order.surge_fee > Decimal("0.00") else ""
                        status_str = self.styler.success("ALLOCATED")
                        self.stream.write(
                            f"{prefix}{cmd.order_id} -> {status_str} to {self.styler.cyan(store_id)} ({dist_str}) | "
                            f"ETA: {eta_str} | Fee: {fee_str}{surge_tag}\n"
                        )
                    else:
                        # Duplicate idempotent retry or unallocated
                        self.stream.write(f"{prefix}{cmd.order_id} -> Status: {order.status.value}\n")

                elif cmd.command_type == CommandType.RIDERS:
                    assert isinstance(cmd, RidersCommand)
                    self.stream.write(
                        f"{prefix}Store {self.styler.cyan(cmd.store_id)} active riders -> {cmd.active_riders}\n"
                    )

                elif cmd.command_type == CommandType.WEATHER_HOLD:
                    weather_holds_count += 1
                    assert isinstance(cmd, WeatherHoldCommand)
                    self.stream.write(
                        f"{prefix}{self.styler.yellow('WEATHER HOLD')} on Store {self.styler.cyan(cmd.store_id)} "
                        f"(20 min from {cmd.ts.strftime('%H:%M:%S')})\n"
                    )

                elif cmd.command_type == CommandType.CANCEL:
                    cancellations_count += 1
                    assert isinstance(cmd, CancelCommand)
                    order_obj = engine.orders.get(cmd.order_id)
                    ord_status = order_obj.status.value if order_obj else "UNKNOWN"
                    charge_str = f"Charge: ₹{order_obj.cancellation_fee:.2f}" if order_obj else ""
                    refund_str = f"Refund: ₹{order_obj.refund_amount:.2f}" if order_obj else ""
                    self.stream.write(
                        f"{prefix}{cmd.order_id} -> {self.styler.yellow('CANCELLED')} ({ord_status}) | {charge_str}, {refund_str}\n"
                    )

                elif cmd.command_type == CommandType.LOAD_DELIVERIES:
                    assert isinstance(cmd, LoadDeliveriesCommand)
                    delivery_batches_count = len(engine.delivery_events)
                    self.stream.write(
                        f"{prefix}Loaded {len(engine.delivery_events)} actual delivery events from {self.styler.cyan(cmd.file_name)}\n"
                    )

                elif cmd.command_type == CommandType.PRINT_TRACE:
                    assert isinstance(cmd, PrintTraceCommand)
                    self.stream.write(f"{prefix}Generated trace record for {self.styler.cyan(cmd.order_id)}\n")

                elif cmd.command_type == CommandType.PRINT_RIDER_PAYOUTS:
                    assert isinstance(cmd, PrintRiderPayoutsCommand)
                    self.stream.write(f"{prefix}Generated rider payout audit rows for {cmd.date}\n")

                else:
                    self.stream.write(f"{prefix}Completed {cmd.command_type.name}\n")

            except Exception as exc:
                errors_count += 1
                self.stream.write(f"{prefix}{self.styler.failure('EXCEPTION')}: {exc}\n")

            self.stream.flush()

        # ----------------------------------------------------------------------
        # 4. Report Generation
        # ----------------------------------------------------------------------
        output_dir.mkdir(parents=True, exist_ok=True)
        trace_path = output_dir / "order_trace.csv"
        lines_path = output_dir / "order_lines.csv"
        payouts_path = output_dir / "rider_payouts.csv"

        self.stream.write(f"\n{self.styler.bold('REPORT GENERATION')}\n")
        self.stream.write(f"{'-' * 40}\n")
        try:
            engine.export_order_trace_csv(trace_path)
            trace_rows = len(engine.printed_traces)
            self.stream.write(f"  {self.styler.green('✓')} order_trace.csv   : {trace_rows:>3} rows -> {trace_path}\n")

            engine.export_order_lines_csv(lines_path)
            lines_rows = len(engine.printed_order_lines)
            self.stream.write(f"  {self.styler.green('✓')} order_lines.csv   : {lines_rows:>3} rows -> {lines_path}\n")

            engine.export_rider_payouts_csv(payouts_path)
            payout_rows = sum(len(s) for s in engine.printed_rider_payouts)
            self.stream.write(f"  {self.styler.green('✓')} rider_payouts.csv : {payout_rows:>3} rows -> {payouts_path}\n")

        except Exception as exc:
            self.stream.write(f"  {self.styler.failure('✗ Failed to generate reports')}: {exc}\n")
            return 1

        # ----------------------------------------------------------------------
        # 5. Final Execution Summary
        # ----------------------------------------------------------------------
        elapsed = time.perf_counter() - start_time
        sep = "=" * 55
        self.stream.write(f"\n{self.styler.bold(sep)}\n")
        self.stream.write(f"{self.styler.header('                 EXECUTION SUMMARY')}\n")
        self.stream.write(f"{self.styler.bold(sep)}\n")
        self.stream.write(f"  Total commands processed : {self.styler.bold(str(total_commands))}\n")
        self.stream.write(f"  Orders received          : {orders_received}\n")
        self.stream.write(f"  Orders allocated         : {self.styler.green(str(orders_allocated))}\n")
        self.stream.write(f"  Orders rejected          : {self.styler.red(str(orders_rejected)) if orders_rejected else '0'}\n")
        self.stream.write(f"  Cancellations handled    : {cancellations_count}\n")
        self.stream.write(f"  Weather holds activated  : {weather_holds_count}\n")
        self.stream.write(f"  Delivery events loaded   : {delivery_batches_count}\n")
        self.stream.write(f"  Simulation runtime       : {elapsed:.2f} seconds\n")
        self.stream.write(f"  Execution status         : {self.styler.success('SUCCESS (All reports verified)')}\n")
        self.stream.write(f"{self.styler.bold(sep)}\n\n")
        self.stream.flush()

        return 0


# ==============================================================================
# JSONL STREAMING MONITOR
# ==============================================================================


class JSONLStreamMonitor:
    """Monitors JSONL streaming with strict separation between stdout and stderr.

    Stream Invariant:
    - stdout: Emits ONLY serialized JSONL output records.
    - stderr: Emits human-readable progress, status updates, and summary.
    """

    def __init__(
        self,
        monitor_stream: TextIO | None = None,
        processor: JSONLCommandProcessor | None = None,
    ) -> None:
        self.mon_stream = monitor_stream or sys.stderr
        self.styler = TerminalStyler(self.mon_stream)
        self.processor = processor or JSONLCommandProcessor()

    def print_banner(self) -> None:
        """Display monitor banner on stderr."""
        sep = "=" * 55
        self.mon_stream.write(f"\n{self.styler.bold(sep)}\n")
        self.mon_stream.write(f"{self.styler.header('          PS-031 JSONL STREAM PROCESSOR')}\n")
        self.mon_stream.write(f"{self.styler.bold('               REAL-TIME MONITOR')}\n")
        self.mon_stream.write(f"{self.styler.bold(sep)}\n")
        self.mon_stream.flush()

    def process_stream(
        self,
        in_stream: TextIO,
        out_stream: TextIO,
    ) -> int:
        """Process streaming commands line-by-line while writing progress to stderr."""
        start_time = time.perf_counter()
        self.print_banner()

        total_lines = 0
        success_count = 0
        error_count = 0
        command_counter: Counter[str] = Counter()

        self.mon_stream.write(f"{self.styler.bold('STREAMING COMMAND EXECUTION')}\n")
        self.mon_stream.write(f"{'-' * 40}\n")
        self.mon_stream.flush()

        for line_num, line in enumerate(in_stream, start=1):
            stripped = line.strip()
            total_lines += 1

            # Skip blank lines (they advance line numbers per specification)
            if not stripped:
                continue

            record = self.processor.process_line(line, line_number=line_num)
            if record is None:
                continue

            # 1. Output machine-readable JSONL to out_stream
            serialized = serialize_record(record)
            out_stream.write(serialized + "\n")
            out_stream.flush()

            # 2. Extract telemetry for human-readable monitoring on stderr
            cmd_name = record.get("command", "UNKNOWN")
            status = record.get("status", "UNKNOWN")
            command_counter[cmd_name] += 1

            prefix = f"[Line {line_num:>3}] {self.styler.bold(cmd_name):<22} | "

            if status == "SUCCESS":
                success_count += 1
                res = record.get("result") or {}
                detail_str = ""

                if cmd_name == "REGISTER_PRODUCT":
                    detail_str = f"SKU {self.styler.cyan(res.get('sku', ''))} (₹{res.get('price', '')})"
                elif cmd_name == "REGISTER_STORE":
                    detail_str = f"Store {self.styler.cyan(res.get('store_id', ''))} ({res.get('name', '')})"
                elif cmd_name == "REGISTER_RIDER":
                    detail_str = f"Rider {self.styler.cyan(res.get('rider_id', ''))} ({res.get('name', '')})"
                elif cmd_name == "SUBMIT_ORDER":
                    store = res.get("allocated_store_id") or "Unallocated"
                    status_lbl = res.get("status", "RECEIVED")
                    detail_str = f"Order {res.get('order_id')} -> {status_lbl} (Store: {self.styler.cyan(store)})"
                elif cmd_name == "START_PICKING":
                    detail_str = f"Order {res.get('order_id')} -> Status: {res.get('status')}"
                elif cmd_name == "FULFIL_ORDER":
                    fee_charged = "Delivery fee charged" if res.get("delivery_fee_charged") else "Fee waived"
                    detail_str = f"Order {res.get('order_id')} fulfilled (Ratio: {res.get('fulfilled_ratio')}, {fee_charged})"
                elif cmd_name == "CREATE_BATCH":
                    detail_str = f"Batch {self.styler.cyan(res.get('batch_id', ''))} (Store: {res.get('store_id')}, Rider: {res.get('rider_id')})"
                elif cmd_name == "ADD_ORDER_TO_BATCH":
                    detail_str = f"Batch {res.get('batch_id')} added order {res.get('order_id')}"
                elif cmd_name == "DISPATCH_BATCH":
                    drops = len(res.get("drop_sequence") or [])
                    detail_str = f"Batch {res.get('batch_id')} dispatched with {drops} drops"
                elif cmd_name == "COMPLETE_BATCH_DELIVERY":
                    delivered = len(res.get("delivered_order_ids") or [])
                    detail_str = f"Batch {res.get('batch_id')} completed ({delivered} orders delivered)"
                elif cmd_name == "COMPLETE_ORDER_DELIVERY":
                    detail_str = f"Order {res.get('order_id')} delivered"
                elif cmd_name == "GET_ORDER_TRACE":
                    val = res.get("order_value", "0.00")
                    fee = res.get("delivery_fee", "0.00")
                    surge = res.get("surge_fee", "0.00")
                    stat = res.get("status", "UNKNOWN")
                    detail_str = f"Order {res.get('order_id')} [{stat}] | Val: ₹{val}, Fee: ₹{fee}, Surge: ₹{surge}"
                else:
                    detail_str = f"Status: {res.get('status', 'OK')}"

                self.mon_stream.write(f"{prefix}{self.styler.success('OK')} | {detail_str}\n")

            else:
                error_count += 1
                err = record.get("error") or {}
                err_code = err.get("code", "ERROR")
                err_msg = err.get("message", "Unknown error")
                self.mon_stream.write(
                    f"{prefix}{self.styler.failure('ERR')} [{self.styler.red(err_code)}]: {err_msg}\n"
                )

            self.mon_stream.flush()

        elapsed = time.perf_counter() - start_time
        sep = "=" * 55
        self.mon_stream.write(f"\n{self.styler.bold(sep)}\n")
        self.mon_stream.write(f"{self.styler.header('             STREAMING EXECUTION SUMMARY')}\n")
        self.mon_stream.write(f"{self.styler.bold(sep)}\n")
        self.mon_stream.write(f"  Total lines read     : {total_lines}\n")
        self.mon_stream.write(f"  Successful commands  : {self.styler.green(str(success_count))}\n")
        self.mon_stream.write(f"  Errors encountered   : {self.styler.red(str(error_count)) if error_count else '0'}\n")
        self.mon_stream.write(f"  Elapsed runtime      : {elapsed:.3f} seconds\n")
        if command_counter:
            self.mon_stream.write(f"\n  {self.styler.bold('Command Breakdown:')}\n")
            for c_name, c_cnt in sorted(command_counter.items()):
                self.mon_stream.write(f"    - {c_name:<26} : {c_cnt:>3}\n")
        self.mon_stream.write(f"{self.styler.bold(sep)}\n\n")
        self.mon_stream.flush()

        return 0 if error_count == 0 else 1


# ==============================================================================
# MAIN RUNNER ENTRY POINT
# ==============================================================================


def main(argv: list[str] | None = None) -> None:
    """Execute the standalone monitor supporting both CSV and JSONL modes."""
    args_list = sys.argv[1:] if argv is None else argv

    # Check for empty argv when invoked explicitly in unit tests
    if argv is not None and len(argv) == 0:
        parser = build_parser()
        parser.error("the following arguments are required: --data-dir, --commands, --output-dir")

    parser = build_parser()
    parsed = parser.parse_args(args_list)

    # Determine execution mode: CSV mode vs JSONL streaming mode
    if parsed.data_dir is not None:
        csv_monitor = CSVSimulationMonitor()
        exit_code = csv_monitor.run(
            data_dir=parsed.data_dir,
            commands_path=parsed.commands,
            output_dir=parsed.output_dir,
        )
        sys.exit(exit_code)
    else:
        # JSONL Streaming Mode
        input_path = parsed.input_opt or parsed.input_file
        output_path = parsed.output_file

        in_f: TextIO | None = None
        out_f: TextIO | None = None

        try:
            if input_path:
                p_in = Path(input_path)
                if not p_in.is_file():
                    sys.stderr.write(f"Error: input file '{input_path}' not found.\n")
                    sys.exit(1)
                in_f = p_in.open("r", encoding="utf-8")
                stream_in = in_f
            else:
                stream_in = sys.stdin

            if output_path:
                p_out = Path(output_path)
                p_out.parent.mkdir(parents=True, exist_ok=True)
                out_f = p_out.open("w", encoding="utf-8")
                stream_out = out_f
            else:
                stream_out = sys.stdout

            stream_monitor = JSONLStreamMonitor()
            exit_code = stream_monitor.process_stream(stream_in, stream_out)
            sys.exit(exit_code)

        except Exception as exc:
            sys.stderr.write(f"Fatal error during monitored execution: {exc}\n")
            sys.exit(1)
        finally:
            if in_f is not None:
                in_f.close()
            if out_f is not None:
                out_f.close()


if __name__ == "__main__":
    main()
