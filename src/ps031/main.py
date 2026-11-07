"""Command-line interface entry point for the PS-031 Quick Commerce Engine.

Supports:
- Optional input file or reading from stdin
- Optional output file or streaming to stdout
- Strict separation: JSONL records sent to stdout, diagnostic messages to stderr
"""

import argparse
from pathlib import Path
import sys
from typing import TextIO

from ps031.command_processor import JSONLCommandProcessor, serialize_record


def build_parser() -> argparse.ArgumentParser:
    """Build and configure the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ps031",
        description="PS-031 Quick Commerce Engine — JSONL Command Processor",
    )
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
    return parser


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


def main() -> None:
    """Entry point when executed as a module or script."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
