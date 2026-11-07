# PS-031 Quick Commerce Engine

A Python-based command-processing engine for dark-store order allocation, picking, fulfilment, rider batching, delivery, and business-rule execution.

## Requirements

- Python 3.10+
- pytest for running tests

## Setup

From the repository root, set the source path:

    $env:PYTHONPATH = "src"

Run the test suite:

    python -m pytest -q

## End-to-End Example

The repository includes `examples/happy_path.jsonl`, demonstrating the complete order lifecycle:

1. Register a product, store, and rider.
2. Submit an order.
3. Start picking and fulfil the order.
4. Create a rider batch and add the order.
5. Dispatch the batch and complete delivery.
6. Retrieve the order execution trace.

Run the example:

    $env:PYTHONPATH = "src"
    python -m ps031.main examples/happy_path.jsonl -o output.jsonl

Inspect the results:

    Get-Content output.jsonl | ForEach-Object {
        $_ | ConvertFrom-Json
    } | Select-Object line_number, command, status

All 11 commands should complete successfully. The example deliberately demonstrates a late delivery and BR-09 customer compensation, including wallet credit and a free coupon.

## JSONL Command Interface

Each input line is an independent JSON object containing:

- `command`: command name
- `timestamp`: command timestamp
- `payload`: command-specific data

The CLI accepts an input file or standard input and writes JSONL results to an output file or standard output.

## Tests

Run all tests:

    $env:PYTHONPATH = "src"
    python -m pytest -q

Run the end-to-end example test:

    python -m pytest tests/test_happy_path.py -v