from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from ps031.models.batch import Batch, BatchStatus
from ps031.models.order import Order
from ps031.models.order_state import OrderState

# BR-10 Constants
BATCH_BONUS_AMOUNT: Decimal = Decimal("20.00")
SEVERE_LATENESS_DEDUCTION: Decimal = Decimal("30.00")
DAILY_DEDUCTION_CAP: Decimal = Decimal("90.00")
ZERO_AMOUNT: Decimal = Decimal("0.00")
SEVERE_LATENESS_THRESHOLD_SECONDS: float = 900.0  # 15 minutes

# Permitted order lifecycle states for individual delivery events
PERMITTED_DELIVERY_EVENT_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.DISPATCHED,
        OrderState.DELIVERED,
        OrderState.CANCELLED,
    }
)


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


def _validate_non_empty_str(val: Any, field_name: str) -> str:
    """Validate that val is a non-empty string and not a boolean."""
    if isinstance(val, bool) or not isinstance(val, str):
        raise TypeError(f"{field_name} must be a string, got {type(val).__name__}.")
    trimmed = val.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must not be empty.")
    return trimmed


def _format_date(val: str | date | datetime) -> str:
    """Normalize input date to YYYY-MM-DD string format."""
    if isinstance(val, bool):
        raise TypeError(f"Date cannot be a boolean, got {type(val).__name__}.")
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, str):
        trimmed = val.strip()
        if not trimmed:
            raise ValueError("Date string must not be empty.")
        # Validate format parseable as ISO date
        try:
            parsed = date.fromisoformat(trimmed)
            return parsed.isoformat()
        except ValueError as err:
            raise ValueError(
                f"Invalid date format '{val}'. Expected YYYY-MM-DD."
            ) from err
    raise TypeError(
        f"Date must be a str, date, or datetime, got {type(val).__name__}."
    )


@dataclass(frozen=True)
class OrderFingerprint:
    """Immutable identity fingerprint of a constituent order."""

    order_id: str
    status: str
    promised_eta: datetime | None
    actual_delivery_time: datetime | None


@dataclass(frozen=True)
class BatchFingerprint:
    """Complete, immutable identity fingerprint of a batch payout request."""

    batch_id: str
    rider_id: str
    completed_at: datetime
    orders: tuple[OrderFingerprint, ...]
    delivery_overrides: tuple[tuple[str, datetime], ...]


@dataclass(frozen=True)
class DeliveredOrderAudit:
    """Audit record for an individually delivered drop."""

    order_id: str
    batch_id: str
    rider_id: str
    delivery_date: str
    actual_delivery_time: datetime
    promised_eta: datetime
    lateness_seconds: float
    is_late: bool
    is_severe_late: bool
    deduction: Decimal


@dataclass(frozen=True)
class BatchPayoutEvaluation:
    """Outcome of evaluating payout penalties for a single batch."""

    batch_id: str
    rider_id: str
    completion_date: str
    total_delivered_orders: int
    late_orders_count: int
    severe_late_orders_count: int
    is_bonus_lost: bool
    uncapped_deductions: Decimal


@dataclass(frozen=True)
class RiderDailyPayout:
    """Aggregated daily payout report matching BR-10 specification."""

    date: str
    rider_id: str
    batches: int
    late_orders: int
    batch_bonus_lost: int
    late_deductions: Decimal
    late_deduction_capped: Decimal


@dataclass
class _DailyAccumulator:
    """Internal accumulator for a rider's daily financial metrics."""

    batches: int = 0
    late_orders: int = 0
    batch_bonus_lost: int = 0
    late_deductions: Decimal = Decimal("0.00")

    def to_payout(self, date_str: str, rider_id: str) -> RiderDailyPayout:
        capped = min(DAILY_DEDUCTION_CAP, self.late_deductions)
        return RiderDailyPayout(
            date=date_str,
            rider_id=rider_id,
            batches=self.batches,
            late_orders=self.late_orders,
            batch_bonus_lost=self.batch_bonus_lost,
            late_deductions=self.late_deductions,
            late_deduction_capped=capped,
        )


@runtime_checkable
class RiderPayoutServiceInterface(Protocol):
    """Protocol for BR-10 Rider Penalties and Payouts."""

    def evaluate_batch(
        self,
        batch: Batch,
        completed_at: datetime | None = None,
        delivery_times: dict[str, datetime] | None = None,
    ) -> BatchPayoutEvaluation:
        """Evaluate penalties and bonus loss for a batch without mutating ledger state."""
        ...

    def record_delivery_event(
        self,
        order: Order,
        rider_id: str,
        batch_id: str,
        delivered_at: datetime,
    ) -> DeliveredOrderAudit:
        """Record an individual drop delivery event into the daily ledger idempotently."""
        ...

    def record_batch_payout(
        self,
        batch: Batch,
        completed_at: datetime | None = None,
        delivery_times: dict[str, datetime] | None = None,
    ) -> BatchPayoutEvaluation:
        """Evaluate and finalize batch completion into the daily ledger idempotently."""
        ...

    def get_daily_payout(
        self,
        rider_id: str,
        payout_date: str | date,
    ) -> RiderDailyPayout:
        """Retrieve daily payout summary for a rider on a given date."""
        ...

    def get_all_daily_payouts(
        self,
        payout_date: str | date | None = None,
    ) -> list[RiderDailyPayout]:
        """Retrieve all daily payout summaries sorted deterministically by (date asc, rider_id asc)."""
        ...

    def seed_daily_penalty(
        self,
        rider_id: str,
        payout_date: str | date,
        penalty_amount: Decimal | str | int | float,
    ) -> Decimal:
        """Seed starting accumulated daily penalty for an existing rider on a given date."""
        ...

    def has_daily_ledger(
        self,
        rider_id: str,
        payout_date: str | date,
    ) -> bool:
        """Return True if a daily ledger exists for the given rider and date."""
        ...

    def has_delivered_order(
        self,
        order_id: str,
    ) -> bool:
        """Return True if an order delivery drop has already been recorded."""
        ...

    def get_delivered_order(
        self,
        order_id: str,
    ) -> DeliveredOrderAudit | None:
        """Retrieve audit record for a previously recorded delivery event, or None."""
        ...

    def reset(self) -> None:
        """Reset internal ledger and cache state."""
        ...


class RiderPayoutService:
    """Implements BR-10 (Rider Penalties and Payouts)."""

    def __init__(self) -> None:
        self._delivered_orders: dict[str, DeliveredOrderAudit] = {}
        self._completed_batches: dict[
            str, tuple[BatchFingerprint, BatchPayoutEvaluation]
        ] = {}
        self._daily_ledgers: dict[tuple[str, str], _DailyAccumulator] = {}

    def _get_or_create_accumulator(
        self, date_str: str, rider_id: str
    ) -> _DailyAccumulator:
        key = (date_str, rider_id)
        if key not in self._daily_ledgers:
            self._daily_ledgers[key] = _DailyAccumulator()
        return self._daily_ledgers[key]

    def record_delivery_event(
        self,
        order: Order,
        rider_id: str,
        batch_id: str,
        delivered_at: datetime,
    ) -> DeliveredOrderAudit:
        """Record an individual delivery drop into the daily ledger idempotently."""
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )
        valid_rider_id = _validate_non_empty_str(rider_id, "rider_id")
        valid_batch_id = _validate_non_empty_str(batch_id, "batch_id")

        if isinstance(delivered_at, bool) or not isinstance(
            delivered_at, datetime
        ):
            raise TypeError(
                f"delivered_at must be a datetime, got {type(delivered_at).__name__}."
            )

        if order.promised_eta is None:
            raise ValueError(f"Order '{order.order_id}' has no promised_eta.")

        _validate_timezone_parity(
            delivered_at,
            order.promised_eta,
            "delivered_at",
            "order.promised_eta",
        )

        # Validate order lifecycle state: pre-dispatch states are rejected
        if order.status not in PERMITTED_DELIVERY_EVENT_STATES:
            raise ValueError(
                f"Cannot record delivery drop for order '{order.order_id}' with "
                f"pre-dispatch status '{order.status.value}'. Must be DISPATCHED, DELIVERED, or CANCELLED."
            )

        # Idempotency and conflict detection for individual order
        if order.order_id in self._delivered_orders:
            existing = self._delivered_orders[order.order_id]
            if (
                existing.rider_id == valid_rider_id
                and existing.batch_id == valid_batch_id
                and existing.actual_delivery_time == delivered_at
            ):
                return existing
            raise ValueError(
                f"Conflicting delivery event for order '{order.order_id}': already recorded with "
                f"batch '{existing.batch_id}', rider '{existing.rider_id}', delivered_at '{existing.actual_delivery_time}' "
                f"but received batch '{valid_batch_id}', rider '{valid_rider_id}', delivered_at '{delivered_at}'."
            )

        # Cancelled orders are excluded from late metrics and deductions
        if order.status == OrderState.CANCELLED:
            audit = DeliveredOrderAudit(
                order_id=order.order_id,
                batch_id=valid_batch_id,
                rider_id=valid_rider_id,
                delivery_date=delivered_at.date().isoformat(),
                actual_delivery_time=delivered_at,
                promised_eta=order.promised_eta,
                lateness_seconds=0.0,
                is_late=False,
                is_severe_late=False,
                deduction=ZERO_AMOUNT,
            )
            self._delivered_orders[order.order_id] = audit
            return audit

        raw_diff_seconds = (delivered_at - order.promised_eta).total_seconds()
        is_late = raw_diff_seconds > 0.0
        is_severe_late = raw_diff_seconds > SEVERE_LATENESS_THRESHOLD_SECONDS
        deduction = (
            SEVERE_LATENESS_DEDUCTION if is_severe_late else ZERO_AMOUNT
        )
        lateness_seconds = max(0.0, raw_diff_seconds)
        delivery_date = delivered_at.date().isoformat()

        # Update order-level daily ledger metrics
        acc = self._get_or_create_accumulator(delivery_date, valid_rider_id)
        if is_late:
            acc.late_orders += 1
        acc.late_deductions += deduction

        audit = DeliveredOrderAudit(
            order_id=order.order_id,
            batch_id=valid_batch_id,
            rider_id=valid_rider_id,
            delivery_date=delivery_date,
            actual_delivery_time=delivered_at,
            promised_eta=order.promised_eta,
            lateness_seconds=lateness_seconds,
            is_late=is_late,
            is_severe_late=is_severe_late,
            deduction=deduction,
        )
        self._delivered_orders[order.order_id] = audit
        if order.actual_delivery_time is None:
            order.actual_delivery_time = delivered_at
        return audit

    def _validate_and_prepare_batch(
        self,
        batch: Batch,
        completed_at: datetime | None,
        delivery_times: dict[str, datetime] | None,
    ) -> tuple[
        datetime,
        list[Order],
        list[Order],
        dict[str, datetime],
        BatchFingerprint,
    ]:
        """Validate batch status, terminal states, timestamps and build fingerprint."""
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )

        if completed_at is not None and (
            isinstance(completed_at, bool)
            or not isinstance(completed_at, datetime)
        ):
            raise TypeError(
                f"completed_at must be a datetime or None, got {type(completed_at).__name__}."
            )

        overrides: dict[str, datetime] = {}
        if delivery_times is not None:
            if isinstance(delivery_times, bool) or not isinstance(
                delivery_times, dict
            ):
                raise TypeError(
                    f"delivery_times must be a dict, got {type(delivery_times).__name__}."
                )
            for k, v in delivery_times.items():
                if isinstance(k, bool) or not isinstance(k, str) or not k.strip():
                    raise TypeError("delivery_times keys must be non-empty strings.")
                if isinstance(v, bool) or not isinstance(v, datetime):
                    raise TypeError(
                        f"delivery_times values must be datetime objects, got {type(v).__name__} for key '{k}'."
                    )
                overrides[k.strip()] = v

        if not batch.orders:
            raise ValueError(f"Batch '{batch.batch_id}' contains no orders.")

        if batch.status == BatchStatus.CREATED:
            raise ValueError(
                f"Cannot finalize payout for batch '{batch.batch_id}': "
                f"batch is in CREATED status and has not departed."
            )
        if batch.status == BatchStatus.CANCELLED:
            raise ValueError(
                f"Cannot finalize payout for fully CANCELLED batch '{batch.batch_id}': "
                f"zero delivered orders."
            )

        delivered_orders: list[Order] = []
        cancelled_orders: list[Order] = []
        non_terminal_orders: list[Order] = []

        for o in batch.orders:
            if o.status == OrderState.DELIVERED:
                delivered_orders.append(o)
            elif o.status == OrderState.CANCELLED:
                cancelled_orders.append(o)
            else:
                non_terminal_orders.append(o)

        if non_terminal_orders:
            first = non_terminal_orders[0]
            raise ValueError(
                f"Cannot finalize payout for incomplete batch '{batch.batch_id}': "
                f"order '{first.order_id}' has non-terminal status '{first.status.value}'."
            )

        if not delivered_orders:
            raise ValueError(
                f"Cannot finalize payout for batch '{batch.batch_id}': "
                f"no delivered orders found."
            )

        # Resolve delivery times for all delivered orders
        resolved_delivery_times: dict[str, datetime] = {}
        for o in delivered_orders:
            dt = overrides.get(o.order_id, o.actual_delivery_time)
            if dt is None:
                raise ValueError(
                    f"Cannot finalize payout for incomplete batch '{batch.batch_id}': "
                    f"delivered order '{o.order_id}' has no actual_delivery_time."
                )
            if o.promised_eta is None:
                raise ValueError(
                    f"Order '{o.order_id}' in batch '{batch.batch_id}' has no promised_eta."
                )
            _validate_timezone_parity(
                dt,
                o.promised_eta,
                f"order '{o.order_id}' delivery time",
                f"order '{o.order_id}' promised_eta",
            )
            resolved_delivery_times[o.order_id] = dt

        # Resolve batch completion timestamp
        resolved_completed_at: datetime
        if completed_at is not None:
            resolved_completed_at = completed_at
        else:
            batch_attr_completed = getattr(batch, "completed_at", None)
            if batch_attr_completed is not None and isinstance(
                batch_attr_completed, datetime
            ):
                resolved_completed_at = batch_attr_completed
            elif not cancelled_orders:
                # Fully delivered batch safely defaults to final drop timestamp
                resolved_completed_at = max(resolved_delivery_times.values())
            else:
                # Partially cancelled batch requires explicit completion timestamp
                raise ValueError(
                    f"Batch '{batch.batch_id}' contains cancelled orders; explicit 'completed_at' "
                    f"timestamp is required to determine exact terminal completion date."
                )

        if batch.dispatched_at is not None:
            _validate_timezone_parity(
                resolved_completed_at,
                batch.dispatched_at,
                "completed_at",
                "batch.dispatched_at",
            )
        elif batch.created_at is not None:
            _validate_timezone_parity(
                resolved_completed_at,
                batch.created_at,
                "completed_at",
                "batch.created_at",
            )

        # Construct complete BatchFingerprint
        order_fingerprints: list[OrderFingerprint] = []
        for o in sorted(batch.orders, key=lambda x: x.order_id):
            dt = resolved_delivery_times.get(o.order_id, o.actual_delivery_time)
            order_fingerprints.append(
                OrderFingerprint(
                    order_id=o.order_id,
                    status=o.status.value,
                    promised_eta=o.promised_eta,
                    actual_delivery_time=dt,
                )
            )

        delivery_overrides_tuple = tuple(
            sorted(overrides.items(), key=lambda x: x[0])
        )

        fingerprint = BatchFingerprint(
            batch_id=batch.batch_id,
            rider_id=batch.rider_id,
            completed_at=resolved_completed_at,
            orders=tuple(order_fingerprints),
            delivery_overrides=delivery_overrides_tuple,
        )

        return (
            resolved_completed_at,
            delivered_orders,
            cancelled_orders,
            resolved_delivery_times,
            fingerprint,
        )

    def _reconcile_existing_orders(
        self,
        batch: Batch,
        delivered_orders: list[Order],
        resolved_delivery_times: dict[str, datetime],
    ) -> None:
        """Verify that any orders already recorded in _delivered_orders match the batch."""
        for o in delivered_orders:
            if o.order_id in self._delivered_orders:
                audit = self._delivered_orders[o.order_id]
                expected_dt = resolved_delivery_times[o.order_id]
                if audit.rider_id != batch.rider_id:
                    raise ValueError(
                        f"Reconciliation error for order '{o.order_id}': existing delivery audit rider_id "
                        f"'{audit.rider_id}' does not match batch rider_id '{batch.rider_id}'."
                    )
                if audit.batch_id != batch.batch_id:
                    raise ValueError(
                        f"Reconciliation error for order '{o.order_id}': existing delivery audit batch_id "
                        f"'{audit.batch_id}' does not match batch batch_id '{batch.batch_id}'."
                    )
                if audit.actual_delivery_time != expected_dt:
                    raise ValueError(
                        f"Reconciliation error for order '{o.order_id}': existing delivery audit actual_delivery_time "
                        f"'{audit.actual_delivery_time}' does not match batch delivery timestamp '{expected_dt}'."
                    )
                if audit.promised_eta != o.promised_eta:
                    raise ValueError(
                        f"Reconciliation error for order '{o.order_id}': existing delivery audit promised_eta "
                        f"'{audit.promised_eta}' does not match order promised_eta '{o.promised_eta}'."
                    )

    def evaluate_batch(
        self,
        batch: Batch,
        completed_at: datetime | None = None,
        delivery_times: dict[str, datetime] | None = None,
    ) -> BatchPayoutEvaluation:
        """Evaluate penalties and bonus loss for a batch without mutating ledger state."""
        (
            resolved_completed_at,
            delivered_orders,
            _,
            resolved_delivery_times,
            _,
        ) = self._validate_and_prepare_batch(
            batch, completed_at, delivery_times
        )
        self._reconcile_existing_orders(
            batch, delivered_orders, resolved_delivery_times
        )

        late_orders_count = 0
        severe_late_orders_count = 0
        uncapped_deductions = Decimal("0.00")

        for o in delivered_orders:
            dt = resolved_delivery_times[o.order_id]
            diff_seconds = (dt - o.promised_eta).total_seconds()
            if diff_seconds > 0.0:
                late_orders_count += 1
            if diff_seconds > SEVERE_LATENESS_THRESHOLD_SECONDS:
                severe_late_orders_count += 1
                uncapped_deductions += SEVERE_LATENESS_DEDUCTION

        is_bonus_lost = late_orders_count >= 2
        comp_date = resolved_completed_at.date().isoformat()

        return BatchPayoutEvaluation(
            batch_id=batch.batch_id,
            rider_id=batch.rider_id,
            completion_date=comp_date,
            total_delivered_orders=len(delivered_orders),
            late_orders_count=late_orders_count,
            severe_late_orders_count=severe_late_orders_count,
            is_bonus_lost=is_bonus_lost,
            uncapped_deductions=uncapped_deductions,
        )

    def record_batch_payout(
        self,
        batch: Batch,
        completed_at: datetime | None = None,
        delivery_times: dict[str, datetime] | None = None,
    ) -> BatchPayoutEvaluation:
        """Evaluate and finalize batch completion into daily ledger idempotently."""
        (
            resolved_completed_at,
            delivered_orders,
            _,
            resolved_delivery_times,
            fingerprint,
        ) = self._validate_and_prepare_batch(
            batch, completed_at, delivery_times
        )

        # Idempotency and conflict check
        if batch.batch_id in self._completed_batches:
            cached_fp, cached_eval = self._completed_batches[batch.batch_id]
            if fingerprint == cached_fp:
                return cached_eval

            # Detailed conflict diagnosis
            if batch.rider_id != cached_fp.rider_id:
                raise ValueError(
                    f"Conflicting batch submission for batch_id '{batch.batch_id}': "
                    f"rider_id mismatch (cached: '{cached_fp.rider_id}', incoming: '{batch.rider_id}')."
                )
            if resolved_completed_at != cached_fp.completed_at:
                raise ValueError(
                    f"Conflicting batch submission for batch_id '{batch.batch_id}': "
                    f"completed_at mismatch (cached: '{cached_fp.completed_at}', incoming: '{resolved_completed_at}')."
                )
            if fingerprint.delivery_overrides != cached_fp.delivery_overrides:
                raise ValueError(
                    f"Conflicting batch submission for batch_id '{batch.batch_id}': "
                    f"delivery_times overrides mismatch."
                )
            if fingerprint.orders != cached_fp.orders:
                raise ValueError(
                    f"Conflicting batch submission for batch_id '{batch.batch_id}': "
                    f"orders or order timestamps mismatch."
                )
            raise ValueError(
                f"Conflicting batch submission for batch_id '{batch.batch_id}': fingerprint mismatch."
            )

        # Reconcile existing delivery audits before modifying any ledger or cache state
        self._reconcile_existing_orders(
            batch, delivered_orders, resolved_delivery_times
        )

        late_orders_count = 0
        severe_late_orders_count = 0
        uncapped_deductions = Decimal("0.00")

        # Process order-level metrics into daily ledger (prevent double-counting if previously recorded via delivery events)
        for o in delivered_orders:
            dt = resolved_delivery_times[o.order_id]
            diff_seconds = (dt - o.promised_eta).total_seconds()
            is_late = diff_seconds > 0.0
            is_severe = diff_seconds > SEVERE_LATENESS_THRESHOLD_SECONDS
            order_deduction = (
                SEVERE_LATENESS_DEDUCTION if is_severe else ZERO_AMOUNT
            )

            if is_late:
                late_orders_count += 1
            if is_severe:
                severe_late_orders_count += 1
                uncapped_deductions += order_deduction

            # If order was not previously recorded via record_delivery_event, record it now
            if o.order_id not in self._delivered_orders:
                order_date = dt.date().isoformat()
                acc = self._get_or_create_accumulator(
                    order_date, batch.rider_id
                )
                if is_late:
                    acc.late_orders += 1
                acc.late_deductions += order_deduction

                self._delivered_orders[o.order_id] = DeliveredOrderAudit(
                    order_id=o.order_id,
                    batch_id=batch.batch_id,
                    rider_id=batch.rider_id,
                    delivery_date=order_date,
                    actual_delivery_time=dt,
                    promised_eta=o.promised_eta,
                    lateness_seconds=max(0.0, diff_seconds),
                    is_late=is_late,
                    is_severe_late=is_severe,
                    deduction=order_deduction,
                )
            if o.actual_delivery_time is None:
                o.actual_delivery_time = dt

        is_bonus_lost = late_orders_count >= 2
        comp_date = resolved_completed_at.date().isoformat()

        # Record batch-level metrics on the completion date
        comp_acc = self._get_or_create_accumulator(comp_date, batch.rider_id)
        comp_acc.batches += 1
        if is_bonus_lost:
            comp_acc.batch_bonus_lost += 1

        evaluation = BatchPayoutEvaluation(
            batch_id=batch.batch_id,
            rider_id=batch.rider_id,
            completion_date=comp_date,
            total_delivered_orders=len(delivered_orders),
            late_orders_count=late_orders_count,
            severe_late_orders_count=severe_late_orders_count,
            is_bonus_lost=is_bonus_lost,
            uncapped_deductions=uncapped_deductions,
        )

        self._completed_batches[batch.batch_id] = (fingerprint, evaluation)
        return evaluation

    def get_daily_payout(
        self,
        rider_id: str,
        payout_date: str | date,
    ) -> RiderDailyPayout:
        """Retrieve daily payout summary for a rider on a given date."""
        valid_rider_id = _validate_non_empty_str(rider_id, "rider_id")
        date_str = _format_date(payout_date)

        key = (date_str, valid_rider_id)
        acc = self._daily_ledgers.get(key)
        if acc is None:
            return RiderDailyPayout(
                date=date_str,
                rider_id=valid_rider_id,
                batches=0,
                late_orders=0,
                batch_bonus_lost=0,
                late_deductions=Decimal("0.00"),
                late_deduction_capped=Decimal("0.00"),
            )
        return acc.to_payout(date_str, valid_rider_id)

    def get_all_daily_payouts(
        self,
        payout_date: str | date | None = None,
    ) -> list[RiderDailyPayout]:
        """Retrieve all daily payout summaries sorted deterministically by (date asc, rider_id asc)."""
        target_date_str = (
            _format_date(payout_date) if payout_date is not None else None
        )

        results: list[RiderDailyPayout] = []
        for (d_str, r_id), acc in self._daily_ledgers.items():
            if target_date_str is not None and d_str != target_date_str:
                continue
            results.append(acc.to_payout(d_str, r_id))

        results.sort(key=lambda p: (p.date, p.rider_id))
        return results

    def seed_daily_penalty(
        self,
        rider_id: str,
        payout_date: str | date,
        penalty_amount: Decimal | str | int | float,
    ) -> Decimal:
        """Seed starting accumulated daily penalty for an existing rider on a given date."""
        valid_rider_id = _validate_non_empty_str(rider_id, "rider_id")

        if isinstance(payout_date, bool):
            raise TypeError("payout_date cannot be a boolean.")
        if isinstance(payout_date, datetime):
            raise TypeError("payout_date must be a date, not a datetime.")
        date_str = _format_date(payout_date)

        if isinstance(penalty_amount, bool):
            raise TypeError("penalty_amount cannot be a boolean.")

        if not isinstance(penalty_amount, (Decimal, str, int, float)):
            raise TypeError(
                f"penalty_amount must be a Decimal, str, int, or float, got {type(penalty_amount).__name__}."
            )

        if not isinstance(penalty_amount, Decimal):
            try:
                penalty_amount = Decimal(str(penalty_amount))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(f"Invalid penalty_amount: {penalty_amount}") from err

        if not penalty_amount.is_finite():
            raise ValueError(f"penalty_amount must be finite, got {penalty_amount}.")

        if penalty_amount < ZERO_AMOUNT:
            raise ValueError(f"penalty_amount cannot be negative, got {penalty_amount}.")

        key = (date_str, valid_rider_id)
        if key in self._daily_ledgers:
            raise ValueError(
                f"Cannot seed daily penalty: daily ledger for rider '{valid_rider_id}' on '{date_str}' already exists."
            )

        capped_penalty = min(DAILY_DEDUCTION_CAP, penalty_amount)
        self._daily_ledgers[key] = _DailyAccumulator(late_deductions=capped_penalty)
        return capped_penalty

    def has_daily_ledger(
        self,
        rider_id: str,
        payout_date: str | date,
    ) -> bool:
        """Return True if a daily ledger exists for the given rider and date."""
        valid_rider_id = _validate_non_empty_str(rider_id, "rider_id")
        if isinstance(payout_date, bool):
            raise TypeError("payout_date cannot be a boolean.")
        if isinstance(payout_date, datetime):
            raise TypeError("payout_date must be a date, not a datetime.")
        date_str = _format_date(payout_date)
        return (date_str, valid_rider_id) in self._daily_ledgers

    def has_delivered_order(self, order_id: str) -> bool:
        """Return True if an order delivery drop has already been recorded."""
        valid_order_id = _validate_non_empty_str(order_id, "order_id")
        return valid_order_id in self._delivered_orders

    def get_delivered_order(
        self, order_id: str
    ) -> DeliveredOrderAudit | None:
        """Retrieve audit record for a previously recorded delivery event, or None."""
        valid_order_id = _validate_non_empty_str(order_id, "order_id")
        return self._delivered_orders.get(valid_order_id)

    def reset(self) -> None:
        """Reset internal ledger and cache state."""
        self._delivered_orders.clear()
        self._completed_batches.clear()
        self._daily_ledgers.clear()
