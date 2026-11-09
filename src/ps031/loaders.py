"""Data loaders for dark-store quick commerce simulation datasets.

Parses and validates CSV files adhering to mentor data pack specifications:
- stores.csv
- skus.csv
- stock.csv
- riders.csv
- delivery_events.csv
"""

from collections.abc import Sequence
import csv
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import io
import math
import os
from pathlib import Path
from typing import Any, TextIO

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.product import Product
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store

# Canonical category set matching mentor specification:
# Allowed values: DAIRY, BAKERY, FRUITS_VEG, SNACKS, BEVERAGES, STAPLES, PERSONAL_CARE, HOUSEHOLD
VALID_SKU_CATEGORIES: frozenset[str] = frozenset(
    {
        "DAIRY",
        "BAKERY",
        "FRUITS_VEG",
        "SNACKS",
        "BEVERAGES",
        "STAPLES",
        "PERSONAL_CARE",
        "HOUSEHOLD",
    }
)

STORES_REQUIRED_COLUMNS: tuple[str, ...] = (
    "store_id",
    "store_name",
    "lat",
    "lon",
    "open_time",
    "close_time",
    "picking_capacity_per_slot",
    "active_riders",
)

SKUS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "sku_id",
    "sku_name",
    "brand",
    "category",
    "price",
)

STOCK_REQUIRED_COLUMNS: tuple[str, ...] = (
    "store_id",
    "sku_id",
    "qty_on_hand",
)

RIDERS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "rider_id",
    "rider_name",
    "store_id",
    "mobile",
)

DELIVERY_EVENTS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "event_id",
    "order_id",
    "rider_id",
    "dispatch_ts",
    "delivered_ts",
)


@dataclass(frozen=True)
class StockRecord:
    """Record representing a single stock row from stock.csv."""

    store_id: str
    sku_id: str
    qty_on_hand: int

    @property
    def quantity(self) -> int:
        return self.qty_on_hand

    @property
    def stock(self) -> int:
        return self.qty_on_hand


@dataclass(frozen=True)
class RiderRecord:
    """Record representing a rider from riders.csv including store and mobile."""

    rider_id: str
    rider_name: str
    store_id: str
    mobile: str
    rider: Rider

    @property
    def name(self) -> str:
        return self.rider_name

    @property
    def status(self) -> RiderStatus:
        return self.rider.status

    @property
    def daily_penalty(self) -> Decimal:
        return self.rider.daily_penalty

    @property
    def daily_penalty_date(self) -> Any:
        return self.rider.daily_penalty_date


@dataclass(frozen=True)
class DeliveryEventRecord:
    """Record representing an actual delivery log entry from delivery_events.csv."""

    event_id: str
    order_id: str
    rider_id: str
    dispatch_ts: datetime
    delivered_ts: datetime


class StoreCatalog(dict[str, Store]):
    """Catalog of dark stores keyed by store_id."""

    def to_list(self) -> list[Store]:
        return list(self.values())


class ProductCatalog(dict[str, Product]):
    """Catalog of products keyed by sku_id."""

    def to_list(self) -> list[Product]:
        return list(self.values())


class RiderRoster(dict[str, RiderRecord]):
    """Roster of riders keyed by rider_id."""

    def to_list(self) -> list[RiderRecord]:
        return list(self.values())

    def to_domain_riders(self) -> dict[str, Rider]:
        return {k: v.rider for k, v in self.items()}


def _read_csv_rows(
    source: str | Path | TextIO,
    required_columns: Sequence[str],
) -> tuple[list[dict[str, str]], list[int]]:
    """Read CSV rows from path, string, or stream, validating required headers.

    Returns:
        tuple of (list of cleaned row dicts, list of 1-based data row numbers).
    """
    if isinstance(source, Path):
        with open(source, mode="r", encoding="utf-8-sig") as fh:
            text = fh.read()
        f = io.StringIO(text)
    elif isinstance(source, str):
        if os.path.exists(source) or (
            "\n" not in source
            and "\r" not in source
            and (source.endswith(".csv") or source.endswith(".txt"))
        ):
            with open(source, mode="r", encoding="utf-8-sig") as fh:
                text = fh.read()
            f = io.StringIO(text)
        else:
            f = io.StringIO(source)
    elif hasattr(source, "read"):
        content = source.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8-sig")
        f = io.StringIO(content)
    else:
        raise TypeError(
            f"Expected source to be a str, Path, or file-like object, got {type(source).__name__}."
        )

    reader = csv.DictReader(f)
    if reader.fieldnames is None:
        raise ValueError("CSV source is empty or missing header row.")

    clean_fieldnames = [
        fn.strip() for fn in reader.fieldnames if fn is not None
    ]
    missing = [col for col in required_columns if col not in clean_fieldnames]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(clean_fieldnames)}."
        )

    rows: list[dict[str, str]] = []
    row_numbers: list[int] = []

    data_row_idx = 0
    for raw_row in reader:
        data_row_idx += 1
        if not raw_row or all(v is None or not str(v).strip() for v in raw_row.values()):
            continue
        clean_row = {
            (k.strip() if k is not None else ""): (v.strip() if v is not None else "")
            for k, v in raw_row.items()
        }
        rows.append(clean_row)
        row_numbers.append(data_row_idx)

    return rows, row_numbers


def _parse_non_empty_str(val: str, field_name: str, row_num: int) -> str:
    cleaned = val.strip()
    if not cleaned:
        raise ValueError(f"Row {row_num}: '{field_name}' must not be empty.")
    return cleaned


def _parse_time(val: str, field_name: str, row_num: int) -> time:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            pass
    raise ValueError(
        f"Row {row_num}: invalid {field_name} time '{cleaned}'. Expected format 'HH:MM:SS'."
    )


def _parse_timestamp(val: str, field_name: str, row_num: int) -> datetime:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
    except ValueError as err:
        raise ValueError(
            f"Row {row_num}: invalid {field_name} timestamp '{cleaned}'. Expected format 'YYYY-MM-DD HH:MM:SS'."
        ) from err


def _parse_float(
    val: str,
    field_name: str,
    row_num: int,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    try:
        f = float(cleaned)
    except ValueError as err:
        raise ValueError(
            f"Row {row_num}: invalid {field_name} float '{cleaned}'."
        ) from err
    if not math.isfinite(f):
        raise ValueError(
            f"Row {row_num}: {field_name} must be finite, got '{cleaned}'."
        )
    if min_val is not None and f < min_val:
        raise ValueError(
            f"Row {row_num}: {field_name} must be >= {min_val}, got {f}."
        )
    if max_val is not None and f > max_val:
        raise ValueError(
            f"Row {row_num}: {field_name} must be <= {max_val}, got {f}."
        )
    return f


def _parse_int(
    val: str,
    field_name: str,
    row_num: int,
    min_val: int | None = None,
) -> int:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    try:
        i = int(cleaned)
    except ValueError as err:
        raise ValueError(
            f"Row {row_num}: invalid {field_name} integer '{cleaned}'."
        ) from err
    if min_val is not None and i < min_val:
        raise ValueError(
            f"Row {row_num}: {field_name} must be >= {min_val}, got {i}."
        )
    return i


def _parse_decimal(
    val: str,
    field_name: str,
    row_num: int,
    min_val: Decimal | None = None,
) -> Decimal:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    try:
        d = Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError) as err:
        raise ValueError(
            f"Row {row_num}: invalid {field_name} monetary decimal '{cleaned}'."
        ) from err
    if not d.is_finite():
        raise ValueError(
            f"Row {row_num}: {field_name} must be finite, got '{cleaned}'."
        )
    if min_val is not None and d < min_val:
        raise ValueError(
            f"Row {row_num}: {field_name} must be >= {min_val}, got {d}."
        )
    return d


def _parse_mobile(val: str, field_name: str, row_num: int) -> str:
    cleaned = _parse_non_empty_str(val, field_name, row_num)
    if not (cleaned.isdigit() and len(cleaned) == 10):
        raise ValueError(
            f"Row {row_num}: invalid {field_name} '{cleaned}'. Expected exactly 10 digits."
        )
    return cleaned


def load_stores(source: str | Path | TextIO) -> StoreCatalog:
    """Load dark stores from stores.csv.

    Returns:
        StoreCatalog: mapping store_id -> Store.
    """
    rows, row_nums = _read_csv_rows(source, STORES_REQUIRED_COLUMNS)
    catalog = StoreCatalog()

    for row, row_num in zip(rows, row_nums):
        store_id = _parse_non_empty_str(row["store_id"], "store_id", row_num)
        if store_id in catalog:
            raise ValueError(
                f"Row {row_num}: duplicate store_id '{store_id}' encountered."
            )
        store_name = _parse_non_empty_str(row["store_name"], "store_name", row_num)
        lat = _parse_float(row["lat"], "lat", row_num, min_val=-90.0, max_val=90.0)
        lon = _parse_float(row["lon"], "lon", row_num, min_val=-180.0, max_val=180.0)
        open_time = _parse_time(row["open_time"], "open_time", row_num)
        close_time = _parse_time(row["close_time"], "close_time", row_num)
        picking_capacity = _parse_int(
            row["picking_capacity_per_slot"],
            "picking_capacity_per_slot",
            row_num,
            min_val=1,
        )
        active_riders = _parse_int(
            row["active_riders"], "active_riders", row_num, min_val=0
        )

        try:
            hours = OperatingHours(open_time=open_time, close_time=close_time)
        except ValueError as err:
            raise ValueError(f"Row {row_num}: invalid operating hours: {err}") from err

        location = Coordinates(latitude=lat, longitude=lon)
        store = Store(
            store_id=store_id,
            name=store_name,
            location=location,
            operating_hours=hours,
            picking_capacity=picking_capacity,
            active_riders=active_riders,
        )
        catalog[store_id] = store

    return catalog


def load_stores_list(source: str | Path | TextIO) -> list[Store]:
    """Load dark stores from stores.csv as a list."""
    return load_stores(source).to_list()


def load_skus(source: str | Path | TextIO) -> ProductCatalog:
    """Load SKU catalog from skus.csv.

    Returns:
        ProductCatalog: mapping sku_id -> Product.
    """
    rows, row_nums = _read_csv_rows(source, SKUS_REQUIRED_COLUMNS)
    catalog = ProductCatalog()

    for row, row_num in zip(rows, row_nums):
        sku_id = _parse_non_empty_str(row["sku_id"], "sku_id", row_num)
        if sku_id in catalog:
            raise ValueError(
                f"Row {row_num}: duplicate sku_id '{sku_id}' encountered."
            )
        sku_name = _parse_non_empty_str(row["sku_name"], "sku_name", row_num)
        brand = _parse_non_empty_str(row["brand"], "brand", row_num)
        raw_cat = _parse_non_empty_str(row["category"], "category", row_num)
        norm_cat = raw_cat.upper()
        if norm_cat not in VALID_SKU_CATEGORIES:
            raise ValueError(
                f"Row {row_num}: invalid category '{raw_cat}'. "
                f"Allowed categories: {sorted(VALID_SKU_CATEGORIES)}."
            )
        price = _parse_decimal(
            row["price"], "price", row_num, min_val=Decimal("0.00")
        )

        product = Product(
            sku=sku_id,
            name=sku_name,
            category=norm_cat,
            brand=brand,
            price=price,
        )
        catalog[sku_id] = product

    return catalog


def load_skus_list(source: str | Path | TextIO) -> list[Product]:
    """Load SKU catalog from skus.csv as a list."""
    return load_skus(source).to_list()


def load_stock(
    source: str | Path | TextIO,
    stores: dict[str, Store] | Sequence[Store] | None = None,
    products: dict[str, Product] | Sequence[Product] | None = None,
) -> list[StockRecord]:
    """Load opening stock records from stock.csv.

    Optionally updates Store.inventory directly if stores collection is passed.

    Args:
        source: CSV file path, string, or file stream.
        stores: Optional collection of Store instances to populate inventory for.
        products: Optional collection of Product instances to validate SKU existence.

    Returns:
        list[StockRecord]: Parsed stock records.
    """
    rows, row_nums = _read_csv_rows(source, STOCK_REQUIRED_COLUMNS)
    records: list[StockRecord] = []

    store_map: dict[str, Store] | None = None
    if stores is not None:
        if isinstance(stores, dict):
            store_map = stores
        else:
            store_map = {s.store_id: s for s in stores}

    product_skus: set[str] | None = None
    if products is not None:
        if isinstance(products, dict):
            product_skus = set(products.keys())
        else:
            product_skus = {p.sku for p in products}

    seen_pairs: set[tuple[str, str]] = set()

    for row, row_num in zip(rows, row_nums):
        store_id = _parse_non_empty_str(row["store_id"], "store_id", row_num)
        sku_id = _parse_non_empty_str(row["sku_id"], "sku_id", row_num)
        qty = _parse_int(row["qty_on_hand"], "qty_on_hand", row_num, min_val=0)

        pair = (store_id, sku_id)
        if pair in seen_pairs:
            raise ValueError(
                f"Row {row_num}: duplicate stock entry for store '{store_id}' and SKU '{sku_id}'."
            )
        seen_pairs.add(pair)

        if store_map is not None:
            if store_id not in store_map:
                raise ValueError(
                    f"Row {row_num}: store_id '{store_id}' not found in provided stores."
                )
            store_map[store_id].inventory[sku_id] = qty

        if product_skus is not None:
            if sku_id not in product_skus:
                raise ValueError(
                    f"Row {row_num}: sku_id '{sku_id}' not found in provided product catalog."
                )

        records.append(
            StockRecord(store_id=store_id, sku_id=sku_id, qty_on_hand=qty)
        )

    return records


def load_stock_dict(
    source: str | Path | TextIO,
) -> dict[str, dict[str, int]]:
    """Load stock.csv as a nested dict mapping store_id -> {sku_id: qty_on_hand}."""
    records = load_stock(source)
    nested: dict[str, dict[str, int]] = {}
    for r in records:
        if r.store_id not in nested:
            nested[r.store_id] = {}
        nested[r.store_id][r.sku_id] = r.qty_on_hand
    return nested


def load_riders(
    source: str | Path | TextIO,
    stores: dict[str, Store] | Sequence[Store] | None = None,
) -> RiderRoster:
    """Load riders from riders.csv.

    Args:
        source: CSV file path, string, or file stream.
        stores: Optional collection of stores to validate rider home store assignment.

    Returns:
        RiderRoster: mapping rider_id -> RiderRecord.
    """
    rows, row_nums = _read_csv_rows(source, RIDERS_REQUIRED_COLUMNS)
    roster = RiderRoster()

    store_ids: set[str] | None = None
    if stores is not None:
        if isinstance(stores, dict):
            store_ids = set(stores.keys())
        else:
            store_ids = {s.store_id for s in stores}

    for row, row_num in zip(rows, row_nums):
        rider_id = _parse_non_empty_str(row["rider_id"], "rider_id", row_num)
        if rider_id in roster:
            raise ValueError(
                f"Row {row_num}: duplicate rider_id '{rider_id}' encountered."
            )
        rider_name = _parse_non_empty_str(row["rider_name"], "rider_name", row_num)
        store_id = _parse_non_empty_str(row["store_id"], "store_id", row_num)
        mobile = _parse_mobile(row["mobile"], "mobile", row_num)

        if store_ids is not None and store_id not in store_ids:
            raise ValueError(
                f"Row {row_num}: home store '{store_id}' for rider '{rider_id}' not found in stores."
            )

        domain_rider = Rider(
            rider_id=rider_id,
            name=rider_name,
            status=RiderStatus.AVAILABLE,
        )

        record = RiderRecord(
            rider_id=rider_id,
            rider_name=rider_name,
            store_id=store_id,
            mobile=mobile,
            rider=domain_rider,
        )
        roster[rider_id] = record

    return roster


def load_riders_list(
    source: str | Path | TextIO,
    stores: dict[str, Store] | Sequence[Store] | None = None,
) -> list[RiderRecord]:
    """Load riders from riders.csv as a list."""
    return load_riders(source, stores=stores).to_list()


def load_delivery_events(
    source: str | Path | TextIO,
) -> list[DeliveryEventRecord]:
    """Load delivery dispatch and delivery events from delivery_events.csv.

    Returns:
        list[DeliveryEventRecord]: Ordered delivery event log records.
    """
    rows, row_nums = _read_csv_rows(source, DELIVERY_EVENTS_REQUIRED_COLUMNS)
    events: list[DeliveryEventRecord] = []
    seen_event_ids: set[str] = set()

    for row, row_num in zip(rows, row_nums):
        event_id = _parse_non_empty_str(row["event_id"], "event_id", row_num)
        if event_id in seen_event_ids:
            raise ValueError(
                f"Row {row_num}: duplicate event_id '{event_id}' encountered."
            )
        seen_event_ids.add(event_id)

        order_id = _parse_non_empty_str(row["order_id"], "order_id", row_num)
        rider_id = _parse_non_empty_str(row["rider_id"], "rider_id", row_num)
        dispatch_ts = _parse_timestamp(row["dispatch_ts"], "dispatch_ts", row_num)
        delivered_ts = _parse_timestamp(row["delivered_ts"], "delivered_ts", row_num)

        if delivered_ts < dispatch_ts:
            raise ValueError(
                f"Row {row_num}: delivered_ts ({delivered_ts}) cannot be before dispatch_ts ({dispatch_ts})."
            )

        events.append(
            DeliveryEventRecord(
                event_id=event_id,
                order_id=order_id,
                rider_id=rider_id,
                dispatch_ts=dispatch_ts,
                delivered_ts=delivered_ts,
            )
        )

    return events


__all__ = [
    "DELIVERY_EVENTS_REQUIRED_COLUMNS",
    "DeliveryEventRecord",
    "ProductCatalog",
    "RIDERS_REQUIRED_COLUMNS",
    "RiderRecord",
    "RiderRoster",
    "SKUS_REQUIRED_COLUMNS",
    "STORES_REQUIRED_COLUMNS",
    "STOCK_REQUIRED_COLUMNS",
    "StockRecord",
    "StoreCatalog",
    "VALID_SKU_CATEGORIES",
    "load_delivery_events",
    "load_riders",
    "load_riders_list",
    "load_skus",
    "load_skus_list",
    "load_stock",
    "load_stock_dict",
    "load_stores",
    "load_stores_list",
]
