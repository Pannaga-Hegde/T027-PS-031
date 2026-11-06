"""Rider penalty service implementing BR-10.

Business Rule Specification:
- A completed batch with at least two late delivered orders loses its ₹20 batch bonus.
- An order is late when actual_delivery_time > promised_eta (any positive lateness in seconds).
- An order incurs a ₹30 deduction only when it is more than 15 minutes late (> 900.0 seconds).
- Exactly 15 minutes late (900.0 seconds) incurs no ₹30 deduction.
- Total rider deductions are capped at ₹90 per delivery calendar day.
- Cancelled orders do not count as late deliveries or incur penalties.
- Progressive delivery uses each order's own actual delivery timestamp and delivery date.
- Repeated processing must not apply deductions twice (idempotent).
- The lost ₹20 batch bonus is a forfeited incentive, not a disciplinary deduction, and is
  never added to Rider.daily_penalty.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from ps031.models.batch import Batch, BatchStatus
from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.models.rider import Rider

# BR-10 Monetary and Threshold Constants
BATCH_BONUS_AMOUNT: Decimal = Decimal("20.00")
ORDER_DEDUCTION_AMOUNT: Decimal = Decimal("30.00")
DAILY_DEDUCTION_CAP: Decimal = Decimal("90.00")
SEVERE_LATENESS_SECONDS: float = 900.0  # 15 minutes * 60 seconds


def _iso_datetime(dt: datetime | None) -> str | None:
    """Format datetime into stable ISO 8601 string preserving tzoffset, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _is_aware(dt: datetime) -> bool:
    """Return True if datetime is timezone-aware, False if naive."""
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def _validate_timezone_parity(
    dt1: datetime, dt2: datetime, name1: str, name2: str
) -> None:
    """Validate timezone-awareness parity between two datetimes."""
    is_aware1 = _is_aware(dt1)
    is_aware2 = _is_aware(dt2)
    if is_aware1 != is_aware2:
        raise TypeError(
            f"Cannot compare {'timezone-aware' if is_aware1 else 'timezone-naive'} {name1} "
            f"with {'timezone-aware' if is_aware2 else 'timezone-naive'} {name2}."
        )


@dataclass(frozen=True)
class OrderPenaltyDetail:
    """Evaluation detail for a single order in a batch under BR-10."""

    order_id: str
    promised_eta: datetime | None
    actual_delivery_time: datetime | None
    delivery_date: date | None
    lateness_seconds: float
    is_late: bool
    is_severely_late: bool
    calculated_deduction: Decimal
    applied_deduction: Decimal
    is_cancelled: bool = False


@dataclass(frozen=True)
class BatchPenaltyResult:
    """Outcome of BR-10 rider penalty and bonus evaluation for a batch."""

    batch_id: str
    rider_id: str
    total_orders: int
    delivered_orders_count: int
    cancelled_orders_count: int
    late_orders_count: int
    bonus_lost: bool
    batch_bonus_awarded: Decimal
    order_penalties: tuple[OrderPenaltyDetail, ...]
    total_calculated_deduction: Decimal
    total_applied_deduction: Decimal
    daily_penalties_by_date: tuple[tuple[date, Decimal], ...]


@runtime_checkable
class RiderPenaltyServiceInterface(Protocol):
    """Protocol defining the BR-10 Rider Penalty service interface."""

    def calculate_batch_penalties(
        self,
        batch: Batch,
        rider: Rider | None = None,
        delivered_at: datetime | None = None,
    ) -> BatchPenaltyResult:
        """Calculate penalties and bonus outcome without mutating state."""
        ...

    def apply_batch_penalties(
        self,
        batch: Batch,
        rider: Rider,
        delivered_at: datetime | None = None,
    ) -> BatchPenaltyResult:
        """Calculate and apply penalties idempotently, updating rider.daily_penalty and ledger."""
        ...

    def get_batch_penalty(self, batch_id: str) -> BatchPenaltyResult | None:
        """Retrieve previously recorded evaluation result for a batch."""
        ...

    def get_daily_penalty(self, rider_id: str, penalty_date: date) -> Decimal:
        """Retrieve accumulated daily penalty deductions for a rider on a given date."""
        ...


class RiderPenaltyService:
    """Implements BR-10: Rider Penalties and Batch Bonus Evaluation."""

    def __init__(self) -> None:
        self._daily_ledger: dict[tuple[str, date], Decimal] = {}
        self._batch_records: dict[str, BatchPenaltyResult] = {}
        self._batch_fingerprints: dict[str, tuple[Any, ...]] = {}

    def _create_fingerprint(
        self, batch: Batch, rider: Rider, delivered_at: datetime | None
    ) -> tuple[Any, ...]:
        """Create an immutable fingerprint of the input batch and order state."""
        order_fingerprints = tuple(
            (
                o.order_id,
                o.status.value if isinstance(o.status, OrderState) else str(o.status),
                _iso_datetime(o.promised_eta),
                _iso_datetime(o.actual_delivery_time),
            )
            for o in sorted(batch.orders, key=lambda x: x.order_id)
        )
        return (
            batch.batch_id,
            rider.rider_id,
            batch.status.value if isinstance(batch.status, BatchStatus) else str(batch.status),
            _iso_datetime(getattr(batch, "created_at", None)),
            _iso_datetime(getattr(batch, "dispatched_at", None)),
            _iso_datetime(delivered_at),
            order_fingerprints,
        )

    def _validate_rider_state(self, rider: Rider) -> None:
        """Validate mutable Rider penalty state before calculation or mutation."""
        if isinstance(rider, bool) or not isinstance(rider, Rider):
            raise TypeError(
                f"rider must be a Rider instance, got {type(rider).__name__}."
            )

        # Validate daily_penalty
        if isinstance(rider.daily_penalty, bool):
            raise TypeError("Rider daily_penalty cannot be a boolean.")
        if not isinstance(rider.daily_penalty, Decimal):
            raise TypeError(
                f"Rider daily_penalty must be a Decimal, got {type(rider.daily_penalty).__name__}."
            )
        if not rider.daily_penalty.is_finite():
            raise ValueError(
                f"Rider daily_penalty must be a finite number, got {rider.daily_penalty}."
            )
        if rider.daily_penalty < Decimal("0.00"):
            raise ValueError(
                f"Rider daily_penalty cannot be negative, got {rider.daily_penalty}."
            )

        # Validate daily_penalty_date
        if rider.daily_penalty_date is not None:
            if isinstance(rider.daily_penalty_date, bool):
                raise TypeError("Rider daily_penalty_date cannot be a boolean.")
            if isinstance(rider.daily_penalty_date, datetime):
                raise TypeError(
                    f"Rider daily_penalty_date must be a date, not a datetime, got {type(rider.daily_penalty_date).__name__}."
                )
            if not isinstance(rider.daily_penalty_date, date):
                raise TypeError(
                    f"Rider daily_penalty_date must be a date instance or None, got {type(rider.daily_penalty_date).__name__}."
                )

    def _validate_batch_and_orders(
        self,
        batch: Batch,
        rider: Rider | None,
        delivered_at: datetime | None = None,
    ) -> None:
        """Validate all parameters, lifecycle states, timestamps, and timezone compatibility."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )

        if rider is not None:
            self._validate_rider_state(rider)
            if batch.rider_id != rider.rider_id:
                raise ValueError(
                    f"Batch rider ID '{batch.rider_id}' does not match provided rider '{rider.rider_id}'."
                )

        if batch.status != BatchStatus.DELIVERED:
            status_val = (
                batch.status.value
                if isinstance(batch.status, BatchStatus)
                else str(batch.status)
            )
            raise ValueError(
                f"Cannot process penalties for batch '{batch.batch_id}' with status '{status_val}': "
                f"batch must be in DELIVERED status."
            )

        if delivered_at is not None:
            if isinstance(delivered_at, bool) or not isinstance(
                delivered_at, datetime
            ):
                raise TypeError(
                    f"delivered_at must be a datetime, got {type(delivered_at).__name__}."
                )

        seen_order_ids: set[str] = set()
        for order in batch.orders:
            if isinstance(order, bool) or not isinstance(order, Order):
                raise TypeError(
                    f"All batch orders must be Order instances, got {type(order).__name__}."
                )

            if order.order_id in seen_order_ids:
                raise ValueError(
                    f"Duplicate order ID '{order.order_id}' found in batch '{batch.batch_id}'."
                )
            seen_order_ids.add(order.order_id)

            if order.status not in (OrderState.DELIVERED, OrderState.CANCELLED):
                order_status_val = (
                    order.status.value
                    if isinstance(order.status, OrderState)
                    else str(order.status)
                )
                raise ValueError(
                    f"Cannot process batch '{batch.batch_id}' with order '{order.order_id}' "
                    f"in non-completed status '{order_status_val}'."
                )

            if order.status == OrderState.DELIVERED:
                del_time = (
                    order.actual_delivery_time
                    if order.actual_delivery_time is not None
                    else delivered_at
                )
                if del_time is None:
                    raise ValueError(
                        f"Order '{order.order_id}' in batch '{batch.batch_id}' is in status DELIVERED "
                        f"but has no actual_delivery_time."
                    )
                if isinstance(del_time, bool) or not isinstance(del_time, datetime):
                    raise TypeError(
                        f"actual_delivery_time for order '{order.order_id}' must be a datetime, "
                        f"got {type(del_time).__name__}."
                    )

                if order.promised_eta is None:
                    raise ValueError(
                        f"Order '{order.order_id}' in batch '{batch.batch_id}' has no promised_eta."
                    )
                if isinstance(order.promised_eta, bool) or not isinstance(
                    order.promised_eta, datetime
                ):
                    raise TypeError(
                        f"promised_eta for order '{order.order_id}' must be a datetime, "
                        f"got {type(order.promised_eta).__name__}."
                    )

                _validate_timezone_parity(
                    del_time,
                    order.promised_eta,
                    f"actual_delivery_time for order '{order.order_id}'",
                    f"promised_eta for order '{order.order_id}'",
                )

                if batch.dispatched_at is not None:
                    _validate_timezone_parity(
                        del_time,
                        batch.dispatched_at,
                        f"actual_delivery_time for order '{order.order_id}'",
                        "batch dispatched_at",
                    )
                    if del_time < batch.dispatched_at:
                        raise ValueError(
                            f"actual_delivery_time for order '{order.order_id}' cannot be earlier than batch dispatched_at."
                        )

    def calculate_batch_penalties(
        self,
        batch: Batch,
        rider: Rider | None = None,
        delivered_at: datetime | None = None,
    ) -> BatchPenaltyResult:
        """Calculate BR-10 penalties and bonus loss for a batch without mutating state."""
        self._validate_batch_and_orders(batch, rider, delivered_at=delivered_at)

        # Build a temporary view of ledger state for simulation
        sim_ledger: dict[tuple[str, date], Decimal] = dict(self._daily_ledger)

        order_details: list[OrderPenaltyDetail] = []
        delivered_count = 0
        cancelled_count = 0
        late_count = 0

        # Pre-evaluate orders and determine delivery dates
        for order in batch.orders:
            if order.status == OrderState.CANCELLED:
                cancelled_count += 1
                order_details.append(
                    OrderPenaltyDetail(
                        order_id=order.order_id,
                        promised_eta=order.promised_eta,
                        actual_delivery_time=None,
                        delivery_date=None,
                        lateness_seconds=0.0,
                        is_late=False,
                        is_severely_late=False,
                        calculated_deduction=Decimal("0.00"),
                        applied_deduction=Decimal("0.00"),
                        is_cancelled=True,
                    )
                )
                continue

            # DELIVERED order
            delivered_count += 1
            del_time = (
                order.actual_delivery_time
                if order.actual_delivery_time is not None
                else delivered_at
            )
            assert del_time is not None
            assert order.promised_eta is not None

            diff_seconds = (del_time - order.promised_eta).total_seconds()
            is_late = diff_seconds > 0.0
            if is_late:
                late_count += 1

            is_severely_late = diff_seconds > SEVERE_LATENESS_SECONDS
            calculated_deduction = (
                ORDER_DEDUCTION_AMOUNT
                if is_severely_late
                else Decimal("0.00")
            )
            del_date = del_time.date()

            # Cap simulation on this order's delivery date
            ledger_key = (batch.rider_id, del_date)
            if ledger_key not in sim_ledger:
                starting_penalty = Decimal("0.00")
                if (
                    rider is not None
                    and rider.daily_penalty_date == del_date
                    and rider.daily_penalty > Decimal("0.00")
                ):
                    starting_penalty = min(DAILY_DEDUCTION_CAP, rider.daily_penalty)
                sim_ledger[ledger_key] = starting_penalty

            current_accumulated = sim_ledger[ledger_key]
            capacity_remaining = max(
                Decimal("0.00"), DAILY_DEDUCTION_CAP - current_accumulated
            )

            applied_deduction = min(calculated_deduction, capacity_remaining)
            sim_ledger[ledger_key] = current_accumulated + applied_deduction

            order_details.append(
                OrderPenaltyDetail(
                    order_id=order.order_id,
                    promised_eta=order.promised_eta,
                    actual_delivery_time=del_time,
                    delivery_date=del_date,
                    lateness_seconds=diff_seconds,
                    is_late=is_late,
                    is_severely_late=is_severely_late,
                    calculated_deduction=calculated_deduction,
                    applied_deduction=applied_deduction,
                    is_cancelled=False,
                )
            )

        bonus_lost = late_count >= 2
        batch_bonus = Decimal("0.00") if bonus_lost else BATCH_BONUS_AMOUNT

        total_calculated = sum(
            (d.calculated_deduction for d in order_details), Decimal("0.00")
        )
        total_applied = sum(
            (d.applied_deduction for d in order_details), Decimal("0.00")
        )

        # Collect resulting daily penalty totals for all affected delivery dates
        affected_dates = sorted(
            {d.delivery_date for d in order_details if d.delivery_date is not None}
        )
        daily_penalties_by_date = tuple(
            (d, sim_ledger.get((batch.rider_id, d), Decimal("0.00")))
            for d in affected_dates
        )

        return BatchPenaltyResult(
            batch_id=batch.batch_id,
            rider_id=batch.rider_id,
            total_orders=len(batch.orders),
            delivered_orders_count=delivered_count,
            cancelled_orders_count=cancelled_count,
            late_orders_count=late_count,
            bonus_lost=bonus_lost,
            batch_bonus_awarded=batch_bonus,
            order_penalties=tuple(order_details),
            total_calculated_deduction=total_calculated,
            total_applied_deduction=total_applied,
            daily_penalties_by_date=daily_penalties_by_date,
        )

    def apply_batch_penalties(
        self,
        batch: Batch,
        rider: Rider,
        delivered_at: datetime | None = None,
    ) -> BatchPenaltyResult:
        """Calculate and atomically apply penalties to rider and daily ledger."""
        if isinstance(rider, bool) or not isinstance(rider, Rider):
            raise TypeError(
                f"rider must be a Rider instance, got {type(rider).__name__}."
            )

        # 1. Validation & fingerprint verification
        self._validate_batch_and_orders(batch, rider, delivered_at=delivered_at)
        fingerprint = self._create_fingerprint(batch, rider, delivered_at)

        if batch.batch_id in self._batch_records:
            stored_fingerprint = self._batch_fingerprints.get(batch.batch_id)
            if stored_fingerprint == fingerprint:
                return self._batch_records[batch.batch_id]
            raise ValueError(
                f"Batch '{batch.batch_id}' was already processed with different parameters/fingerprint."
            )

        # 2. Compute penalty details
        result = self.calculate_batch_penalties(
            batch=batch, rider=rider, delivered_at=delivered_at
        )

        # 3. Mutation Phase (strictly atomic: only reached after full validation)
        for d in result.order_penalties:
            if d.is_cancelled or d.delivery_date is None:
                continue
            ledger_key = (rider.rider_id, d.delivery_date)
            if ledger_key not in self._daily_ledger:
                starting_penalty = Decimal("0.00")
                if (
                    rider.daily_penalty_date == d.delivery_date
                    and rider.daily_penalty > Decimal("0.00")
                ):
                    starting_penalty = min(
                        DAILY_DEDUCTION_CAP, rider.daily_penalty
                    )
                self._daily_ledger[ledger_key] = starting_penalty

            self._daily_ledger[ledger_key] += d.applied_deduction

        # Update Rider domain model state only if no date recorded or latest_date >= current rider date
        affected_dates = [
            d.delivery_date for d in result.order_penalties if d.delivery_date is not None
        ]
        if affected_dates:
            latest_date = max(affected_dates)
            if (
                rider.daily_penalty_date is None
                or latest_date >= rider.daily_penalty_date
            ):
                rider.daily_penalty = self._daily_ledger[(rider.rider_id, latest_date)]
                rider.daily_penalty_date = latest_date

        # Record result and fingerprint for idempotency
        self._batch_records[batch.batch_id] = result
        self._batch_fingerprints[batch.batch_id] = fingerprint

        return result

    def get_batch_penalty(self, batch_id: str) -> BatchPenaltyResult | None:
        """Retrieve previously recorded evaluation result for a batch."""
        if isinstance(batch_id, bool) or not isinstance(batch_id, str):
            raise TypeError(
                f"batch_id must be a string, got {type(batch_id).__name__}."
            )
        if not batch_id.strip():
            raise ValueError("batch_id must not be empty.")
        return self._batch_records.get(batch_id)

    def get_daily_penalty(self, rider_id: str, penalty_date: date) -> Decimal:
        """Retrieve accumulated daily penalty deductions for a rider on a given date."""
        if isinstance(rider_id, bool) or not isinstance(rider_id, str):
            raise TypeError(
                f"rider_id must be a string, got {type(rider_id).__name__}."
            )
        if not rider_id.strip():
            raise ValueError("rider_id must not be empty.")

        if isinstance(penalty_date, bool) or not isinstance(penalty_date, date) or isinstance(penalty_date, datetime):
            raise TypeError(
                f"penalty_date must be a date, not a datetime, got {type(penalty_date).__name__}."
            )

        return self._daily_ledger.get(
            (rider_id, penalty_date), Decimal("0.00")
        )
