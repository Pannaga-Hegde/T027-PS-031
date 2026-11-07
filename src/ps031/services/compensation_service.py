from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from ps031.models.batch import Batch, BatchStatus
from ps031.models.order import Order
from ps031.models.order_state import OrderState

# BR-09 SLA Compensation Thresholds & Monetary Amounts
TIER1_LATENESS_MINUTES: int = 5
TIER2_LATENESS_MINUTES: int = 10

TIER1_LATENESS_SECONDS: float = 300.0  # 5 * 60 seconds
TIER2_LATENESS_SECONDS: float = 600.0  # 10 * 60 seconds

TIER1_WALLET_CREDIT: Decimal = Decimal("25.00")
TIER2_WALLET_CREDIT: Decimal = Decimal("50.00")
ZERO_WALLET_CREDIT: Decimal = Decimal("0.00")


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
class CompensationResult:
    """Outcome of SLA compensation calculation for an order under BR-09."""

    order_id: str
    is_late: bool
    lateness_seconds: float
    lateness_minutes: float
    wallet_credit: Decimal
    free_coupon: bool
    is_delayed_promise_exempt: bool
    reason: str


@runtime_checkable
class CompensationServiceInterface(Protocol):
    """Protocol defining the SLA compensation service interface under BR-09."""

    def calculate_compensation(
        self,
        order: Order,
        actual_delivery_time: datetime | None = None,
    ) -> CompensationResult:
        """Calculate SLA compensation for an order without mutating its state."""
        ...

    def apply_compensation(
        self,
        order: Order,
        actual_delivery_time: datetime | None = None,
    ) -> CompensationResult:
        """Calculate and apply SLA compensation, mutating order.wallet_credit and free_coupon idempotently."""
        ...

    def get_compensation(self, order_id: str) -> CompensationResult | None:
        """Retrieve previously recorded compensation result for an order."""
        ...

    def process_batch_compensation(
        self,
        batch: Batch,
        delivered_at: datetime | None = None,
    ) -> list[CompensationResult]:
        """Apply SLA compensation to all orders in a delivered batch atomically."""
        ...


class CompensationService:
    """Implements BR-09 (SLA Compensation)."""

    def __init__(self) -> None:
        self._compensations: dict[str, CompensationResult] = {}

    def calculate_compensation(
        self,
        order: Order,
        actual_delivery_time: datetime | None = None,
    ) -> CompensationResult:
        """Calculate SLA compensation for an order without mutating its state.

        Rules:
        - Less than 5 minutes late: ₹0.
        - Exactly 5 through exactly 10 minutes late: ₹25 wallet credit.
        - More than 10 minutes late: ₹50 wallet credit and free-delivery coupon.
        - For delayed-promise orders, lateness up to and including 5 minutes receives no compensation.
        - Beyond that exemption, standard compensation brackets apply without shifting.
        """
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )

        delivery_time = (
            actual_delivery_time
            if actual_delivery_time is not None
            else order.actual_delivery_time
        )
        if delivery_time is None:
            raise ValueError(
                f"Order '{order.order_id}' has no actual_delivery_time."
            )
        if isinstance(delivery_time, bool) or not isinstance(
            delivery_time, datetime
        ):
            raise TypeError(
                f"actual_delivery_time must be a datetime, got {type(delivery_time).__name__}."
            )

        if order.promised_eta is None:
            raise ValueError(f"Order '{order.order_id}' has no promised_eta.")

        _validate_timezone_parity(
            delivery_time,
            order.promised_eta,
            "actual_delivery_time",
            "promised_eta",
        )

        raw_diff_seconds = (delivery_time - order.promised_eta).total_seconds()
        is_late = raw_diff_seconds > 0.0
        lateness_seconds = max(0.0, raw_diff_seconds)
        lateness_minutes = lateness_seconds / 60.0

        if raw_diff_seconds <= 0.0:
            wallet_credit = ZERO_WALLET_CREDIT
            free_coupon = False
            is_exempt = False
            reason = "Order delivered on time or early."
        elif order.delayed_promise and raw_diff_seconds <= TIER1_LATENESS_SECONDS:
            wallet_credit = ZERO_WALLET_CREDIT
            free_coupon = False
            is_exempt = True
            reason = (
                f"Order delivered {lateness_minutes:.2f} minutes late but within "
                f"5-minute delayed-promise grace period; exempt from compensation."
            )
        elif raw_diff_seconds < TIER1_LATENESS_SECONDS:
            wallet_credit = ZERO_WALLET_CREDIT
            free_coupon = False
            is_exempt = False
            reason = (
                f"Order delivered {lateness_minutes:.2f} minutes late; "
                f"below 5-minute compensation threshold."
            )
        elif raw_diff_seconds <= TIER2_LATENESS_SECONDS:
            wallet_credit = TIER1_WALLET_CREDIT
            free_coupon = False
            is_exempt = False
            reason = (
                f"Order delivered {lateness_minutes:.2f} minutes late; "
                f"eligible for ₹25 wallet credit."
            )
        else:
            wallet_credit = TIER2_WALLET_CREDIT
            free_coupon = True
            is_exempt = False
            reason = (
                f"Order delivered {lateness_minutes:.2f} minutes late; "
                f"eligible for ₹50 wallet credit and free-delivery coupon."
            )

        return CompensationResult(
            order_id=order.order_id,
            is_late=is_late,
            lateness_seconds=lateness_seconds,
            lateness_minutes=lateness_minutes,
            wallet_credit=wallet_credit,
            free_coupon=free_coupon,
            is_delayed_promise_exempt=is_exempt,
            reason=reason,
        )

    def apply_compensation(
        self,
        order: Order,
        actual_delivery_time: datetime | None = None,
    ) -> CompensationResult:
        """Calculate and apply SLA compensation to order, updating wallet_credit and free_coupon.

        Guarantees:
        - Order must be in DELIVERED status.
        - Existing wallet credits are preserved and added to.
        - Deduplication/idempotency enforced via internal ledger keyed by order_id.
        """
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )

        if order.status != OrderState.DELIVERED:
            status_val = (
                order.status.value
                if isinstance(order.status, OrderState)
                else str(order.status)
            )
            raise ValueError(
                f"Cannot apply compensation to order '{order.order_id}' with status '{status_val}': "
                f"order must be in DELIVERED status."
            )

        # Idempotency check: if already compensated, return cached result without double crediting
        if order.order_id in self._compensations:
            return self._compensations[order.order_id]

        result = self.calculate_compensation(
            order, actual_delivery_time=actual_delivery_time
        )

        # Preserve existing wallet credit and add newly awarded credit
        order.wallet_credit = order.wallet_credit + result.wallet_credit
        if result.free_coupon:
            order.free_coupon = True

        self._compensations[order.order_id] = result
        return result

    def get_compensation(self, order_id: str) -> CompensationResult | None:
        """Retrieve previously recorded compensation result for an order."""
        if isinstance(order_id, bool) or not isinstance(order_id, str):
            raise TypeError(
                f"order_id must be a string, got {type(order_id).__name__}."
            )
        return self._compensations.get(order_id)

    def process_batch_compensation(
        self,
        batch: Batch,
        delivered_at: datetime | None = None,
    ) -> list[CompensationResult]:
        """Apply SLA compensation to all orders in a delivered batch atomically.

        Guarantees:
        - batch must be a Batch instance and in DELIVERED status.
        - Validates all orders, timestamps, and lifecycle states before any mutation.
        - If any order validation fails, no orders and no ledger entries are modified.
        - Idempotent: repeated calls do not double-credit orders or coupons.
        """
        if isinstance(batch, bool) or not isinstance(batch, Batch):
            raise TypeError(
                f"batch must be a Batch instance, got {type(batch).__name__}."
            )

        if batch.status != BatchStatus.DELIVERED:
            status_val = (
                batch.status.value
                if isinstance(batch.status, BatchStatus)
                else str(batch.status)
            )
            raise ValueError(
                f"Cannot process compensation for batch '{batch.batch_id}' with status '{status_val}': "
                f"batch must be in DELIVERED status."
            )

        if delivered_at is not None:
            if isinstance(delivered_at, bool) or not isinstance(
                delivered_at, datetime
            ):
                raise TypeError(
                    f"delivered_at must be a datetime, got {type(delivered_at).__name__}."
                )

        # 1. Validation & calculation phase: inspect all orders without mutating any state
        planned_compensations: list[tuple[Order, CompensationResult]] = []
        for order in batch.orders:
            if isinstance(order, bool) or not isinstance(order, Order):
                raise TypeError(
                    f"Order in batch must be an Order instance, got {type(order).__name__}."
                )

            if order.status != OrderState.DELIVERED:
                order_status_val = (
                    order.status.value
                    if isinstance(order.status, OrderState)
                    else str(order.status)
                )
                raise ValueError(
                    f"Cannot apply compensation to order '{order.order_id}' with status '{order_status_val}': "
                    f"order must be in DELIVERED status."
                )

            res = self.calculate_compensation(
                order, actual_delivery_time=order.actual_delivery_time or delivered_at
            )
            planned_compensations.append((order, res))

        # 2. Mutation phase: only reached if all orders passed validation successfully
        results: list[CompensationResult] = []
        for order, res in planned_compensations:
            if order.order_id in self._compensations:
                results.append(self._compensations[order.order_id])
            else:
                order.wallet_credit = order.wallet_credit + res.wallet_credit
                if res.free_coupon:
                    order.free_coupon = True
                self._compensations[order.order_id] = res
                results.append(res)

        return results
