"""PS-031 Quick Commerce Simulation CLI.

Usage:
    python -m ps031.main --data-dir data --commands data/commands.txt --output-dir output

Loads input datasets, parses a command stream, executes the simulation,
and writes the three required output CSV files.
"""

import argparse
import sys
from pathlib import Path

from ps031.command_parser import CommandParseError, parse_commands
from ps031.execution_engine import ExecutionEngine
from ps031.loaders import (
    load_delivery_events,
    load_riders,
    load_skus,
    load_stock,
    load_stores,
)


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="ps031",
        description="PS-031 Dark-Store Quick Commerce Simulation Engine",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Directory containing stores.csv, skus.csv, stock.csv, riders.csv, and delivery_events.csv.",
    )
    parser.add_argument(
        "--commands",
        type=Path,
        required=True,
        help="Path to the simulation command stream file (e.g. commands.txt).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory where order_trace.csv, order_lines.csv, and rider_payouts.csv will be written.",
    )
    return parser


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
            raise FileNotFoundError(
                f"Required input file not found: {fpath}"
            )


def main(argv: list[str] | None = None) -> None:
    """Run the PS-031 quick-commerce simulation from the command line."""
    print("PS-031 Quick Commerce Engine")
    print("Project initialized successfully.")

    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir: Path = args.data_dir.resolve()
    commands_path: Path = args.commands.resolve()
    output_dir: Path = args.output_dir.resolve()

    # ---------------------------------------------------------------
    # Validate inputs
    # ---------------------------------------------------------------
    try:
        _validate_data_dir(data_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not commands_path.exists():
        print(
            f"Error: Commands file does not exist: {commands_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---------------------------------------------------------------
    # Load input datasets
    # ---------------------------------------------------------------
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
        sys.exit(1)

    # ---------------------------------------------------------------
    # Parse command stream
    # ---------------------------------------------------------------
    print(f"\nParsing commands from {commands_path} ...")
    try:
        commands = parse_commands(commands_path)
    except CommandParseError as exc:
        print(f"Error parsing commands: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(commands)} commands parsed")

    # ---------------------------------------------------------------
    # Initialize engine
    # ---------------------------------------------------------------
    engine = ExecutionEngine(
        stores=stores,
        products=products,
        stock=stock,
        riders=riders,
        data_dir=data_dir,
    )

    # ---------------------------------------------------------------
    # Execute commands
    # ---------------------------------------------------------------
    print("\nExecuting simulation ...")
    try:
        engine.process_commands(commands)
    except Exception as exc:
        print(f"Error during simulation: {exc}", file=sys.stderr)
        sys.exit(1)

    # ---------------------------------------------------------------
    # Write output CSV files
    # ---------------------------------------------------------------
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


if __name__ == "__main__":
    main()
