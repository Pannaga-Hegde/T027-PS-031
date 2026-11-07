"""JSON Lines (JSONL) command processor for the PS-031 Quick Commerce Engine.

Handles:
- Line-by-line reading with blank-line skipping
- Preservation of physical input line numbers
- Envelope validation (command, timestamp, payload)
- Safe command dispatching to ApplicationOrchestrator
- Fault isolation: error on one line does not prevent processing subsequent lines
- Deterministic, Decimal-safe JSON output serialization
"""

from collections.abc import Iterable, Iterator
from datetime import date, datetime, time
from decimal import Decimal
import inspect
import json
from pathlib import Path
import sys
import traceback
from typing import Any

from ps031.models.order_state import InvalidOrderTransitionError
from ps031.orchestrator import ApplicationOrchestrator


# Mapping commands to their event timestamp parameter name
_COMMAND_TIMESTAMP_PARAM = {
    "SUBMIT_ORDER": "order_time",
    "APPLY_WEATHER_HOLD": "hold_start",
    "CREATE_BATCH": "created_at",
    "ADD_ORDER_TO_BATCH": "current_time",
    "DISPATCH_BATCH": "dispatched_at",
    "COMPLETE_ORDER_DELIVERY": "delivered_at",
    "COMPLETE_BATCH_DELIVERY": "delivered_at",
    "EVALUATE_RIDER_PENALTIES": "delivered_at",
    "CANCEL_ORDER": "cancelled_at",
}

_COMMAND_METHODS = {
    "REGISTER_PRODUCT": "register_product",
    "REGISTER_STORE": "register_store",
    "REGISTER_RIDER": "register_rider",
    "SUBMIT_ORDER": "submit_order",
    "START_PICKING": "start_picking",
    "FULFIL_ORDER": "fulfil_order",
    "APPLY_WEATHER_HOLD": "apply_weather_hold",
    "CREATE_BATCH": "create_batch",
    "ADD_ORDER_TO_BATCH": "add_order_to_batch",
    "DISPATCH_BATCH": "dispatch_batch",
    "COMPLETE_ORDER_DELIVERY": "complete_order_delivery",
    "COMPLETE_BATCH_DELIVERY": "complete_batch_delivery",
    "EVALUATE_RIDER_PENALTIES": "evaluate_rider_penalties",
    "CANCEL_ORDER": "cancel_order",
    "GET_ORDER_TRACE": "get_order_trace",
}


def serialize_record(record: dict[str, Any]) -> str:
    """Serialize a command result dictionary deterministically with Decimal safety."""

    def _default_serializer(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return f"{obj:.2f}"
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()
        if isinstance(obj, set):
            return sorted(list(obj))
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return json.dumps(record, default=_default_serializer, sort_keys=True, ensure_ascii=False)


class JSONLCommandProcessor:
    """Processes JSONL command streams against an ApplicationOrchestrator instance."""

    def __init__(self, orchestrator: ApplicationOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or ApplicationOrchestrator()

    def process_line(self, line: str, line_number: int) -> dict[str, Any] | None:
        """Process a single physical input line. Returns None if the line is blank."""
        stripped = line.strip()
        if not stripped:
            return None

        # 1. Parse JSON syntax
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as err:
            return {
                "line_number": line_number,
                "command": "UNKNOWN",
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "MALFORMED_JSON",
                    "message": f"Malformed JSON syntax on line {line_number}: {err.msg}",
                },
            }

        # 2. Validate envelope structure
        if not isinstance(data, dict):
            return {
                "line_number": line_number,
                "command": "UNKNOWN",
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ENVELOPE",
                    "message": "Top-level command must be a JSON object.",
                },
            }

        command = data.get("command")
        if not command or not isinstance(command, str) or not command.strip():
            return {
                "line_number": line_number,
                "command": str(command) if command is not None else "UNKNOWN",
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ENVELOPE",
                    "message": "Missing or empty 'command' field in envelope.",
                },
            }
        command_str = command.strip().upper()

        timestamp_str = data.get("timestamp")
        if not timestamp_str or not isinstance(timestamp_str, str):
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ENVELOPE",
                    "message": "Missing or empty 'timestamp' field in envelope.",
                },
            }
        try:
            datetime.fromisoformat(timestamp_str.strip())
        except ValueError as err:
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ENVELOPE",
                    "message": f"Invalid ISO 8601 timestamp '{timestamp_str}': {err}",
                },
            }

        payload = data.get("payload")
        if payload is None or not isinstance(payload, dict):
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ENVELOPE",
                    "message": "Missing or invalid 'payload' field, expected JSON object.",
                },
            }

        # 3. Check command registration
        method_name = _COMMAND_METHODS.get(command_str)
        if not method_name or not hasattr(self.orchestrator, method_name):
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "UNKNOWN_COMMAND",
                    "message": f"Unrecognized command '{command_str}'.",
                },
            }
        handler = getattr(self.orchestrator, method_name)

        # 4. Prepare arguments and bind against handler signature
        kwargs = dict(payload)
        ts_param = _COMMAND_TIMESTAMP_PARAM.get(command_str)
        if ts_param:
            if ts_param in kwargs and kwargs[ts_param] is not None:
                if not isinstance(kwargs[ts_param], (str, datetime)):
                    return {
                        "line_number": line_number,
                        "command": command_str,
                        "status": "ERROR",
                        "result": None,
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": f"Explicit '{ts_param}' must be an ISO 8601 string, got {type(kwargs[ts_param]).__name__}.",
                        },
                    }
                if isinstance(kwargs[ts_param], str):
                    try:
                        datetime.fromisoformat(kwargs[ts_param].strip())
                    except ValueError as ts_err:
                        return {
                            "line_number": line_number,
                            "command": command_str,
                            "status": "ERROR",
                            "result": None,
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": f"Invalid ISO 8601 '{ts_param}' '{kwargs[ts_param]}': {ts_err}",
                            },
                        }
            else:
                kwargs[ts_param] = timestamp_str.strip()

        sig = inspect.signature(handler)
        try:
            sig.bind(**kwargs)
        except TypeError as arg_err:
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": str(arg_err),
                },
            }

        # 5. Dispatch command to orchestrator with domain error mapping
        try:
            result = handler(**kwargs)
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "SUCCESS",
                "result": result,
                "error": None,
            }
        except KeyError as err:
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "ENTITY_NOT_FOUND",
                    "message": str(err).strip("'"),
                },
            }
        except InvalidOrderTransitionError as err:
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "LIFECYCLE_ERROR",
                    "message": str(err),
                },
            }
        except ValueError as err:
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(err),
                },
            }
        except Exception as err:
            sys.stderr.write(
                f"[Line {line_number}] Unexpected internal error in {command_str}:\n{traceback.format_exc()}\n"
            )
            return {
                "line_number": line_number,
                "command": command_str,
                "status": "ERROR",
                "result": None,
                "error": {
                    "code": "EXECUTION_ERROR",
                    "message": f"Internal execution error: {err}",
                },
            }

    def process_stream(self, input_stream: Iterable[str]) -> Iterator[dict[str, Any]]:
        """Process an iterable of lines, preserving physical line numbers and yielding results."""
        for line_num, line in enumerate(input_stream, start=1):
            res = self.process_line(line, line_number=line_num)
            if res is not None:
                yield res

    def process_file(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Process an entire input file and optionally write JSONL results to output_path."""
        in_p = Path(input_path)
        if not in_p.exists():
            raise FileNotFoundError(f"Input command file not found: {input_path}")

        results: list[dict[str, Any]] = []
        with in_p.open("r", encoding="utf-8") as in_f:
            for record in self.process_stream(in_f):
                results.append(record)

        if output_path is not None:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with out_p.open("w", encoding="utf-8") as out_f:
                for record in results:
                    out_f.write(serialize_record(record) + "\n")

        return results
