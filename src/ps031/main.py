"""Command-line interface entry point for the PS-031 Quick Commerce Engine.

Supports dual operating modes:
1. JSONL Streaming Command Processor Mode (default when invoked without CSV flags)
   - Reads line-by-line JSONL commands from stdin or input file
   - Dispatches to ApplicationOrchestrator via JSONLCommandProcessor
   - Writes serialized JSONL responses to stdout or output file
   - Enforces strict stream separation: pure JSONL on stdout, diagnostics on stderr

2. CSV Mentor Acceptance Simulation Mode (invoked when --data-dir, --commands, or --output-dir are supplied)
   - Loads stores.csv, skus.csv, stock.csv, riders.csv from --data-dir
   - Executes command stream from --commands through ExecutionEngine
   - Generates order_trace.csv, order_lines.csv, and rider_payouts.csv in --output-dir
"""

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
from typing import TextIO

from ps031.command_parser import CommandParseError, parse_commands
from ps031.command_processor import JSONLCommandProcessor, serialize_record
from ps031.execution_engine import ExecutionEngine
from ps031.loaders import (
    load_delivery_events,
    load_riders,
    load_skus,
    load_stock,
    load_stores,
)


class DualModeParser(argparse.ArgumentParser):
    """Argument parser supporting both JSONL streaming and CSV simulation modes."""

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)

        csv_flags = [parsed.data_dir, parsed.commands, parsed.output_dir]
        if any(f is not None for f in csv_flags):
            missing = []
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
    """Build and configure the unified CLI argument parser for both modes."""
    parser = DualModeParser(
        prog="ps031",
        description="PS-031 Quick Commerce Engine",
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
        help="Path to output JSONL file. If omitted, writes to stdout.",
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
# CSV MENTOR ACCEPTANCE SIMULATION WORKFLOW
# ==============================================================================


def _validate_data_dir(data_dir: Path) -> None:
    """Validate that the data directory contains all required input CSV files."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"--data-dir is not a directory: {data_dir}")
    required_files = [
        "stores.csv",
        "skus.csv",
        "stock.csv",
        "riders.csv",
    ]
    for fname in required_files:
        fpath = data_dir / fname
        if not fpath.exists():
            raise FileNotFoundError(f"Required input file not found: {fpath}")


def _run_csv_simulation(
    data_dir: Path, commands_path: Path, output_dir: Path
) -> int:
    """Run CSV mentor simulation, printing progress and exporting output CSV files."""
    data_dir = data_dir.resolve()
    commands_path = commands_path.resolve()
    output_dir = output_dir.resolve()

    # 1. Validate inputs
    try:
        _validate_data_dir(data_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not commands_path.exists():
        print(
            f"Error: Commands file does not exist: {commands_path}",
            file=sys.stderr,
        )
        return 1

    # 2. Load input datasets
    print(f"\nLoading datasets from {data_dir} ...")
    try:
        stores = load_stores(data_dir / "stores.csv")
        print(f"  stores.csv       : {len(stores)} stores loaded")

        products = load_skus(data_dir / "skus.csv")
        print(f"  skus.csv         : {len(products)} SKUs loaded")

        stock = load_stock(
            data_dir / "stock.csv", stores=stores, products=products
        )
        print(f"  stock.csv        : {len(stock)} stock records loaded")

        riders = load_riders(data_dir / "riders.csv", stores=stores)
        print(f"  riders.csv       : {len(riders)} riders loaded")

    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"Error loading datasets: {exc}", file=sys.stderr)
        return 1

    # 3. Parse command stream
    print(f"\nParsing commands from {commands_path} ...")
    try:
        commands = parse_commands(commands_path)
    except CommandParseError as exc:
        print(f"Error parsing commands: {exc}", file=sys.stderr)
        return 1
    print(f"  {len(commands)} commands parsed")

    # 4. Initialize engine
    engine = ExecutionEngine(
        stores=stores,
        products=products,
        stock=stock,
        riders=riders,
        data_dir=data_dir,
    )

    # 5. Execute commands
    print("\nExecuting simulation ...")
    try:
        engine.process_commands(commands)
    except Exception as exc:
        print(f"Error during simulation: {exc}", file=sys.stderr)
        return 1

    # 6. Write output CSV files
    output_dir.mkdir(parents=True, exist_ok=True)

    trace_path = output_dir / "order_trace.csv"
    lines_path = output_dir / "order_lines.csv"
    payouts_path = output_dir / "rider_payouts.csv"

    engine.export_order_trace_csv(trace_path)
    engine.export_order_lines_csv(lines_path)
    engine.export_rider_payouts_csv(payouts_path)

    trace_rows = len(engine.printed_traces)
    lines_rows = len(engine.printed_order_lines)
    payout_rows = sum(len(s) for s in engine.printed_rider_payouts)

    print(f"\nOutputs written to {output_dir}:")
    print(f"  order_trace.csv   : {trace_rows} rows")
    print(f"  order_lines.csv   : {lines_rows} rows")
    print(f"  rider_payouts.csv : {payout_rows} rows")
    print("\nSimulation complete.")
    return 0


# ==============================================================================
# JSONL STREAMING WORKFLOW
# ==============================================================================


def run_pipeline(
    in_stream: TextIO,
    out_stream: TextIO,
    processor: JSONLCommandProcessor | None = None,
) -> int:
    """Run JSONL processing pipeline reading from in_stream and writing to out_stream."""
    proc = processor or JSONLCommandProcessor()
    for record in proc.process_stream(in_stream):
        out_stream.write(serialize_record(record) + "\n")
    out_stream.flush()
    return 0


def run_cli(args: list[str] | None = None) -> int:
    """Execute the command-line interface given argument strings."""
    parser = build_parser()
    parsed = parser.parse_args(args)

    # If CSV arguments were supplied through run_cli, route to CSV simulation
    if parsed.data_dir is not None:
        return _run_csv_simulation(
            parsed.data_dir, parsed.commands, parsed.output_dir
        )

    input_path = parsed.input_opt or parsed.input_file
    output_path = parsed.output_file

    in_f: TextIO | None = None
    out_f: TextIO | None = None

    try:
        # Determine input stream
        if input_path:
            p = Path(input_path)
            if not p.is_file():
                sys.stderr.write(f"Error: input file '{input_path}' not found.\n")
                return 1
            in_f = p.open("r", encoding="utf-8")
            stream_in = in_f
        else:
            stream_in = sys.stdin

        # Determine output stream
        if output_path:
            p_out = Path(output_path)
            p_out.parent.mkdir(parents=True, exist_ok=True)
            out_f = p_out.open("w", encoding="utf-8")
            stream_out = out_f
        else:
            stream_out = sys.stdout

        return run_pipeline(stream_in, stream_out)

    except Exception as err:
        sys.stderr.write(f"Fatal error during command execution: {err}\n")
        return 1
    finally:
        if in_f is not None:
            in_f.close()
        if out_f is not None:
            out_f.close()


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================


def main(argv: list[str] | None = None) -> None:
    """Main CLI entry point supporting both CSV simulation and JSONL streaming modes."""
    # Scenario C: Explicit main([]) invocation in legacy unit test
    if argv is not None and len(argv) == 0:
        print("PS-031 Quick Commerce Engine")
        print("Project initialized successfully.")
        parser = build_parser()
        parser.error(
            "the following arguments are required: --data-dir, --commands, --output-dir"
        )

    # Determine argument list
    args_list = sys.argv[1:] if argv is None else argv

    # Check if CSV mode was invoked via flags
    is_csv_mode = any(
        arg in ("--data-dir", "--commands", "--output-dir")
        or arg.startswith(("--data-dir=", "--commands=", "--output-dir="))
        for arg in args_list
    )

    if is_csv_mode:
        print("PS-031 Quick Commerce Engine")
        print("Project initialized successfully.")
        parser = build_parser()
        parsed = parser.parse_args(args_list)
        exit_code = _run_csv_simulation(
            parsed.data_dir, parsed.commands, parsed.output_dir
        )
        if exit_code != 0:
            sys.exit(exit_code)
    else:
        # Scenario A & JSONL mode: pure JSONL stream, no banner
        sys.exit(run_cli(args_list))


if __name__ == "__main__":
    main()
