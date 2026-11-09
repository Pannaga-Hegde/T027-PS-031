"""Command parser for PS-031 quick-commerce simulation command files.

Parses command streams adhering to the mentor README command specification:
- ORDER
- RIDERS
- WEATHER_HOLD
- CANCEL
- LOAD_DELIVERIES
- PRINT_TRACE
- PRINT_RIDER_PAYOUTS
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math
import os
from pathlib import Path
from typing import Any, TextIO, Union

from ps031.models.coordinates import Coordinates
from ps031.models.order import SubstitutionPolicy


class CommandType(str, Enum):
    """Enumeration of supported simulation commands."""

    ORDER = "ORDER"
    RIDERS = "RIDERS"
    WEATHER_HOLD = "WEATHER_HOLD"
    CANCEL = "CANCEL"
    LOAD_DELIVERIES = "LOAD_DELIVERIES"
    PRINT_TRACE = "PRINT_TRACE"
    PRINT_RIDER_PAYOUTS = "PRINT_RIDER_PAYOUTS"


class CommandParseError(ValueError):
    """Raised when a command line violates syntax or type constraints."""

    def __init__(self, message: str, line_number: int, raw_line: str = "") -> None:
        self.line_number = line_number
        self.raw_line = raw_line
        super().__init__(f"Line {line_number}: {message}")


@dataclass(frozen=True)
class OrderItemRequest:
    """An individual SKU:quantity request inside an ORDER command."""

    sku_id: str
    quantity: int


@dataclass(frozen=True)
class BaseCommand:
    """Base class for parsed commands containing line-tracking metadata."""

    line_number: int
    raw_command: str


@dataclass(frozen=True)
class OrderCommand(BaseCommand):
    """ORDER command: customer order placement request.

    Format: ORDER|order_id|order_ts|customer_id|lat|lon|substitution_pref|slow_delivery_ok|items
    """

    order_id: str
    order_ts: datetime
    customer_id: str
    lat: float
    lon: float
    substitution_pref: SubstitutionPolicy
    slow_delivery_ok: bool
    items: tuple[OrderItemRequest, ...]

    @property
    def command_type(self) -> CommandType:
        return CommandType.ORDER

    @property
    def ts(self) -> datetime:
        return self.order_ts

    @property
    def location(self) -> Coordinates:
        return Coordinates(latitude=self.lat, longitude=self.lon)

    @property
    def skus(self) -> tuple[str, ...]:
        return tuple(item.sku_id for item in self.items)

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)


@dataclass(frozen=True)
class RidersCommand(BaseCommand):
    """RIDERS command: active rider count adjustment.

    Format: RIDERS|store_id|ts|active_riders
    """

    store_id: str
    ts: datetime
    active_riders: int

    @property
    def command_type(self) -> CommandType:
        return CommandType.RIDERS

    @property
    def timestamp(self) -> datetime:
        return self.ts


@dataclass(frozen=True)
class WeatherHoldCommand(BaseCommand):
    """WEATHER_HOLD command: temporary store freeze for 20 minutes.

    Format: WEATHER_HOLD|store_id|ts
    """

    store_id: str
    ts: datetime

    @property
    def command_type(self) -> CommandType:
        return CommandType.WEATHER_HOLD

    @property
    def timestamp(self) -> datetime:
        return self.ts


@dataclass(frozen=True)
class CancelCommand(BaseCommand):
    """CANCEL command: customer cancellation request.

    Format: CANCEL|order_id|ts
    """

    order_id: str
    ts: datetime

    @property
    def command_type(self) -> CommandType:
        return CommandType.CANCEL

    @property
    def timestamp(self) -> datetime:
        return self.ts


@dataclass(frozen=True)
class LoadDeliveriesCommand(BaseCommand):
    """LOAD_DELIVERIES command: load actual dispatch/delivery log.

    Format: LOAD_DELIVERIES|file_name
    """

    file_name: str

    @property
    def command_type(self) -> CommandType:
        return CommandType.LOAD_DELIVERIES


@dataclass(frozen=True)
class PrintTraceCommand(BaseCommand):
    """PRINT_TRACE command: print order execution trace.

    Format: PRINT_TRACE|order_id
    """

    order_id: str

    @property
    def command_type(self) -> CommandType:
        return CommandType.PRINT_TRACE


@dataclass(frozen=True)
class PrintRiderPayoutsCommand(BaseCommand):
    """PRINT_RIDER_PAYOUTS command: print rider daily payouts.

    Format: PRINT_RIDER_PAYOUTS|date
    """

    payout_date: date

    @property
    def command_type(self) -> CommandType:
        return CommandType.PRINT_RIDER_PAYOUTS

    @property
    def date(self) -> date:
        return self.payout_date

    @property
    def date_str(self) -> str:
        return self.payout_date.isoformat()


# Union type representing any supported simulation command
Command = Union[
    OrderCommand,
    RidersCommand,
    WeatherHoldCommand,
    CancelCommand,
    LoadDeliveriesCommand,
    PrintTraceCommand,
    PrintRiderPayoutsCommand,
]


def is_comment_or_blank(line: str) -> bool:
    """Return True if line is empty, whitespace-only, or a comment starting with '#'."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _parse_non_empty_str(
    val: str, field_name: str, line_number: int, raw_line: str
) -> str:
    cleaned = val.strip()
    if not cleaned:
        raise CommandParseError(
            f"Missing required argument '{field_name}'", line_number, raw_line
        )
    return cleaned


def _parse_timestamp(
    val: str, field_name: str, line_number: int, raw_line: str
) -> datetime:
    cleaned = _parse_non_empty_str(val, field_name, line_number, raw_line)
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError as err:
        raise CommandParseError(
            f"Invalid {field_name} timestamp '{cleaned}'. Expected format 'YYYY-MM-DD HH:MM:SS'",
            line_number,
            raw_line,
        ) from err


def _parse_date(
    val: str, field_name: str, line_number: int, raw_line: str
) -> date:
    cleaned = _parse_non_empty_str(val, field_name, line_number, raw_line)
    try:
        return date.fromisoformat(cleaned)
    except ValueError as err:
        raise CommandParseError(
            f"Invalid {field_name} date '{cleaned}'. Expected format 'YYYY-MM-DD'",
            line_number,
            raw_line,
        ) from err


def _parse_float(
    val: str,
    field_name: str,
    line_number: int,
    raw_line: str,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    cleaned = _parse_non_empty_str(val, field_name, line_number, raw_line)
    try:
        f = float(cleaned)
    except ValueError as err:
        raise CommandParseError(
            f"Invalid float value '{cleaned}' for '{field_name}'",
            line_number,
            raw_line,
        ) from err
    if not math.isfinite(f):
        raise CommandParseError(
            f"Value for '{field_name}' must be finite, got '{cleaned}'",
            line_number,
            raw_line,
        )
    if min_val is not None and f < min_val:
        raise CommandParseError(
            f"Value for '{field_name}' must be >= {min_val}, got {f}",
            line_number,
            raw_line,
        )
    if max_val is not None and f > max_val:
        raise CommandParseError(
            f"Value for '{field_name}' must be <= {max_val}, got {f}",
            line_number,
            raw_line,
        )
    return f


def _parse_int(
    val: str,
    field_name: str,
    line_number: int,
    raw_line: str,
    min_val: int | None = None,
) -> int:
    cleaned = _parse_non_empty_str(val, field_name, line_number, raw_line)
    try:
        i = int(cleaned)
    except ValueError as err:
        raise CommandParseError(
            f"Invalid integer value '{cleaned}' for '{field_name}'",
            line_number,
            raw_line,
        ) from err
    if min_val is not None and i < min_val:
        raise CommandParseError(
            f"Value for '{field_name}' must be >= {min_val}, got {i}",
            line_number,
            raw_line,
        )
    return i


def _parse_bool(
    val: str, field_name: str, line_number: int, raw_line: str
) -> bool:
    cleaned = _parse_non_empty_str(val, field_name, line_number, raw_line).lower()
    if cleaned == "true":
        return True
    if cleaned == "false":
        return False
    raise CommandParseError(
        f"Invalid boolean value '{val.strip()}' for '{field_name}'. Expected 'true' or 'false'",
        line_number,
        raw_line,
    )


def _parse_substitution_pref(
    val: str, line_number: int, raw_line: str
) -> SubstitutionPolicy:
    cleaned = _parse_non_empty_str(
        val, "substitution_pref", line_number, raw_line
    ).upper()
    try:
        return SubstitutionPolicy(cleaned)
    except ValueError as err:
        allowed = sorted([p.value for p in SubstitutionPolicy])
        raise CommandParseError(
            f"Invalid substitution preference '{val.strip()}'. Expected one of {allowed}",
            line_number,
            raw_line,
        ) from err


def _parse_order_items(
    val: str, line_number: int, raw_line: str
) -> tuple[OrderItemRequest, ...]:
    cleaned = val.strip()
    if not cleaned:
        raise CommandParseError(
            "ORDER command must contain at least one item", line_number, raw_line
        )

    raw_items = [it.strip() for it in cleaned.split(",") if it.strip()]
    if not raw_items:
        raise CommandParseError(
            "ORDER command must contain at least one item", line_number, raw_line
        )

    items: list[OrderItemRequest] = []
    for item_str in raw_items:
        if ":" not in item_str:
            raise CommandParseError(
                f"Invalid item format '{item_str}'. Expected 'SKU:qty'",
                line_number,
                raw_line,
            )
        sku_part, qty_part = item_str.split(":", 1)
        sku_id = sku_part.strip()
        if not sku_id:
            raise CommandParseError(
                f"Missing SKU identifier in item '{item_str}'",
                line_number,
                raw_line,
            )
        try:
            qty = int(qty_part.strip())
        except ValueError as err:
            raise CommandParseError(
                f"Invalid quantity '{qty_part.strip()}' for SKU '{sku_id}'. Must be an integer",
                line_number,
                raw_line,
            ) from err

        if qty <= 0:
            raise CommandParseError(
                f"Item quantity for SKU '{sku_id}' must be positive, got {qty}",
                line_number,
                raw_line,
            )

        items.append(OrderItemRequest(sku_id=sku_id, quantity=qty))

    return tuple(items)


def parse_command_line(line: str, line_number: int = 1) -> Command:
    """Parse a single command line into its typed representation.

    Args:
        line: Raw command string (e.g., 'ORDER|ORD-0001|...').
        line_number: 1-based physical line number for error reporting.

    Returns:
        Command: Typed command instance.

    Raises:
        CommandParseError: If line is blank, a comment, or malformed.
    """
    raw_stripped = line.strip()
    if not raw_stripped:
        raise CommandParseError("Empty command line", line_number, line)
    if raw_stripped.startswith("#"):
        raise CommandParseError(
            f"Line is a comment ('{raw_stripped}'), not an executable command",
            line_number,
            line,
        )

    parts = [p.strip() for p in raw_stripped.split("|")]
    cmd_name = parts[0]
    if not cmd_name:
        raise CommandParseError("Missing command name", line_number, line)

    if cmd_name == CommandType.ORDER.value:
        if len(parts) != 9:
            raise CommandParseError(
                f"ORDER command expects 8 arguments, got {len(parts) - 1}",
                line_number,
                line,
            )
        order_id = _parse_non_empty_str(parts[1], "order_id", line_number, line)
        order_ts = _parse_timestamp(parts[2], "order_ts", line_number, line)
        customer_id = _parse_non_empty_str(
            parts[3], "customer_id", line_number, line
        )
        lat = _parse_float(
            parts[4], "lat", line_number, line, min_val=-90.0, max_val=90.0
        )
        lon = _parse_float(
            parts[5], "lon", line_number, line, min_val=-180.0, max_val=180.0
        )
        sub_pref = _parse_substitution_pref(parts[6], line_number, line)
        slow_ok = _parse_bool(parts[7], "slow_delivery_ok", line_number, line)
        items = _parse_order_items(parts[8], line_number, line)

        return OrderCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            order_id=order_id,
            order_ts=order_ts,
            customer_id=customer_id,
            lat=lat,
            lon=lon,
            substitution_pref=sub_pref,
            slow_delivery_ok=slow_ok,
            items=items,
        )

    if cmd_name == CommandType.RIDERS.value:
        if len(parts) != 4:
            raise CommandParseError(
                f"RIDERS command expects 3 arguments, got {len(parts) - 1}",
                line_number,
                line,
            )
        store_id = _parse_non_empty_str(parts[1], "store_id", line_number, line)
        ts = _parse_timestamp(parts[2], "ts", line_number, line)
        riders = _parse_int(
            parts[3], "active_riders", line_number, line, min_val=0
        )
        return RidersCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            store_id=store_id,
            ts=ts,
            active_riders=riders,
        )

    if cmd_name == CommandType.WEATHER_HOLD.value:
        if len(parts) != 3:
            raise CommandParseError(
                f"WEATHER_HOLD command expects 2 arguments, got {len(parts) - 1}",
                line_number,
                line,
            )
        store_id = _parse_non_empty_str(parts[1], "store_id", line_number, line)
        ts = _parse_timestamp(parts[2], "ts", line_number, line)
        return WeatherHoldCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            store_id=store_id,
            ts=ts,
        )

    if cmd_name == CommandType.CANCEL.value:
        if len(parts) != 3:
            raise CommandParseError(
                f"CANCEL command expects 2 arguments, got {len(parts) - 1}",
                line_number,
                line,
            )
        order_id = _parse_non_empty_str(parts[1], "order_id", line_number, line)
        ts = _parse_timestamp(parts[2], "ts", line_number, line)
        return CancelCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            order_id=order_id,
            ts=ts,
        )

    if cmd_name == CommandType.LOAD_DELIVERIES.value:
        if len(parts) != 2:
            raise CommandParseError(
                f"LOAD_DELIVERIES command expects 1 argument, got {len(parts) - 1}",
                line_number,
                line,
            )
        file_name = _parse_non_empty_str(parts[1], "file_name", line_number, line)
        return LoadDeliveriesCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            file_name=file_name,
        )

    if cmd_name == CommandType.PRINT_TRACE.value:
        if len(parts) != 2:
            raise CommandParseError(
                f"PRINT_TRACE command expects 1 argument, got {len(parts) - 1}",
                line_number,
                line,
            )
        order_id = _parse_non_empty_str(parts[1], "order_id", line_number, line)
        return PrintTraceCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            order_id=order_id,
        )

    if cmd_name == CommandType.PRINT_RIDER_PAYOUTS.value:
        if len(parts) != 2:
            raise CommandParseError(
                f"PRINT_RIDER_PAYOUTS command expects 1 argument, got {len(parts) - 1}",
                line_number,
                line,
            )
        payout_date = _parse_date(parts[1], "date", line_number, line)
        return PrintRiderPayoutsCommand(
            line_number=line_number,
            raw_command=raw_stripped,
            payout_date=payout_date,
        )

    supported = sorted([ct.value for ct in CommandType])
    raise CommandParseError(
        f"Unknown command '{cmd_name}'. Supported commands: {', '.join(supported)}",
        line_number,
        line,
    )


def parse_commands(source: str | Path | TextIO) -> list[Command]:
    """Parse commands from a file path, string, or file stream.

    Commands are parsed strictly in file order. Blank lines and lines starting
    with '#' are ignored.

    Args:
        source: File path, string content, or stream.

    Returns:
        list[Command]: Ordered list of parsed commands.

    Raises:
        CommandParseError: On any invalid or malformed command line.
    """
    if isinstance(source, Path):
        with open(source, mode="r", encoding="utf-8-sig") as fh:
            content = fh.read()
    elif isinstance(source, str):
        if os.path.exists(source) or (
            "\n" not in source
            and "\r" not in source
            and (source.endswith(".txt") or source.endswith(".csv"))
        ):
            with open(source, mode="r", encoding="utf-8-sig") as fh:
                content = fh.read()
        else:
            content = source
    elif hasattr(source, "read"):
        raw = source.read()
        content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    else:
        raise TypeError(
            f"Expected source to be a str, Path, or file-like object, got {type(source).__name__}."
        )

    commands: list[Command] = []
    lines = content.splitlines()

    for line_idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(parse_command_line(line, line_number=line_idx))

    return commands


__all__ = [
    "BaseCommand",
    "CancelCommand",
    "Command",
    "CommandParseError",
    "CommandType",
    "LoadDeliveriesCommand",
    "OrderCommand",
    "OrderItemRequest",
    "PrintRiderPayoutsCommand",
    "PrintTraceCommand",
    "RidersCommand",
    "WeatherHoldCommand",
    "is_comment_or_blank",
    "parse_command_line",
    "parse_commands",
]
