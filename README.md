# PS-031 Quick Commerce Engine

A Python-based simulation engine for managing quick-commerce operations, including dark-store allocation, inventory, order fulfilment, rider batching, and delivery dispatch under a 10-minute SLA.


![Python](https://img.shields.io/badge/Python-3.11-blue)
![Tests](https://img.shields.io/badge/Tests-1%2C136%20Passed-success)
![Dependencies](https://img.shields.io/badge/Runtime%20Dependencies-None-brightgreen)

## 1. Project Overview

The Quick Commerce Engine simulates the order fulfilment process used by hyperlocal grocery delivery systems.

It handles the following workflow:

1. Receive customer orders.
2. Select an eligible dark store.
3. Check and reserve inventory.
4. Schedule order picking.
5. Handle unavailable products and substitutions.
6. Calculate delivery fees and estimated delivery times.
7. Assign riders and combine eligible orders.
8. Track delivery performance and calculate compensation.

## 2. Key Features

- Dark-store selection based on distance, availability, and workload.
- Inventory checking and stock reservation.
- Picking-slot scheduling with capacity limits.
- Product substitution with customer price protection.
- Automatic cancellation when fulfilment falls below 60%.
- Delivery fee and surge pricing calculations.
- Rider batching for nearby orders.
- SLA-based customer compensation.
- Rider penalties and daily payout calculation.
- Weather-based dispatch holds.
- Execution monitoring through a standalone terminal interface.

## 3. Repository Structure

```text
T027-PS-031/
│
├── data/
│   ├── commands.txt
│   ├── delivery_events.csv
│   ├── riders.csv
│   ├── skus.csv
│   ├── stock.csv
│   └── stores.csv
│
├── docs/
│   ├── business-rules.md
│   ├── decision-log.md
│   ├── design-document.md
│   ├── glossary.md
│   ├── test-plan.md
│   ├── class-diagram.mmd
│   └── state-diagram.mmd
│
├── examples/
│   └── happy_path.jsonl
│
├── src/
│   └── ps031/
│       ├── models/
│       ├── services/
│       ├── utils/
│       ├── command_parser.py
│       ├── command_processor.py
│       ├── execution_engine.py
│       ├── loaders.py
│       ├── main.py
│       └── orchestrator.py
│
├── tests/
├── execution_monitor.py
├── pyproject.toml
├── requirements.txt
└── README.md
```

## 4. Installation

### Prerequisites

- Python 3.11 or later
- Git

### Clone the repository

```bash
git clone https://github.com/Pannaga-Hegde/T027-PS-031.git
cd T027-PS-031
```

### Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Install development dependencies

```bash
pip install -r requirements.txt
```

### Configure the Python path

**Windows PowerShell:**

```powershell
$env:PYTHONPATH = "src"
```

**Linux / macOS:**

```bash
export PYTHONPATH="src"
```

## 5. Running the Project

### Option A: Execution Monitor (Recommended)

Run the simulation with terminal progress and execution summaries:

```bash
python execution_monitor.py --data-dir data --commands data/commands.txt --output-dir output
```

The monitor displays command progress, processing results, and generated reports.

### Running with an External Dataset

The engine can also process datasets stored outside the repository. Provide the external dataset directory and its command file using the appropriate paths.

**Example (Windows PowerShell):**

```
python execution_monitor.py `
  --data-dir "C:\path\to\external-dataset" `
  --commands "C:\path\to\external-dataset\commands.txt" `
  --output-dir "execution_results"
```

Replace the example paths with the actual location of your dataset.

**Path details:**

| Argument | Description |
|---|---|
| `--data-dir` | Directory containing the external dataset files. |
| `--commands` | Path to the command file (commands.txt). |
| `--output-dir` | Directory where generated reports will be saved. |

The output directory is created automatically if it does not already exist. The original external dataset remains unchanged.

The generated reports include:

- `order_trace.csv`
- `order_lines.csv`
- `rider_payouts.csv`

### Option B: Original CLI

Run the reference engine directly:

```bash
python -m ps031.main --data-dir data --commands data/commands.txt --output-dir output
```

### Option C: JSONL Streaming

Run the provided sample workflow:

```bash
python -m ps031.main examples/happy_path.jsonl -o output.jsonl
```

The execution monitor can also process JSONL:

```bash
python execution_monitor.py examples/happy_path.jsonl -o output.jsonl
```

## 6. Generated Outputs

CSV simulation generates three main reports:

| File | Purpose |
|---|---|
| `order_trace.csv` | Order lifecycle and delivery information |
| `order_lines.csv` | Item fulfilment and substitution details |
| `rider_payouts.csv` | Rider earnings, bonuses, and penalties |

The output directory is created when the simulation runs.

## 7. Business Rules

The engine implements 12 core business rules:

| Rule | Description |
|---|---|
| BR-01 | Dark-store serviceability |
| BR-02 | Deterministic store allocation |
| BR-03 | Picking-slot scheduling |
| BR-04 | Delivery ETA calculation |
| BR-05 | Product substitution |
| BR-06 | 60% fulfilment threshold |
| BR-07 | Delivery and surge pricing |
| BR-08 | Rider batching and dispatch |
| BR-09 | SLA compensation |
| BR-10 | Rider penalties and earnings |
| BR-11 | Weather holds |
| BR-12 | Customer cancellation and refunds |

Detailed specifications are available in `docs/business-rules.md`.

## 8. Testing

```bash
python -m pytest
```

Run tests with detailed output:

```bash
python -m pytest -v
```

## 9. Limitations

- Application state is maintained in memory and is not persistent across restarts.
- Execution is single-process and sequential.
- Delivery distance uses the Haversine formula rather than live road-routing data.
- The system simulates quick-commerce operations; it does not connect to a production ordering or rider-dispatch platform.

## 10. Project Documentation

Additional documentation is available in the `docs/` directory:

- `design-document.md` — System design and architecture.
- `business-rules.md` — Business rule specifications.
- `decision-log.md` — Technical and design decisions.
- `test-plan.md` — Testing approach.
- `glossary.md` — Project terminology.

## 11. Submission Materials

All project submission documents and supporting materials are available in the following Google Drive folder:
https://drive.google.com/drive/folders/1a6yFDXUcs9eWeViIWxTsZNAoCryv1oIs?usp=sharing


## 12. Team Information
Team ID: T027
Project: PS-031 Quick Commerce Engine
