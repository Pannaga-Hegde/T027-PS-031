from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol, runtime_checkable

from ps031.models.batch import Batch
from ps031.models.order import Order, OrderState, SubstitutionPolicy
from ps031.models.product import Product
from ps031.services.batching_service import BatchingServiceInterface
from ps031.services.inventory_service import (
    InventoryService,
    Reservation,
    ReservationStatus,
)
from ps031.services.lifecycle import (
    LifecycleTransitionInterface,
    LifecycleTransitionService,
)

TWOPLACES = Decimal("0.01")
CANCELLATION_FEE_AMOUNT = Decimal("20.00")
PRICE_TOLERANCE_MULTIPLIER = Decimal("1.15")


def _quantize_currency(val: Decimal, name: str) -> Decimal:
    """Validate and quantize monetary amount to 2 decimal places using ROUND_HALF_UP."""
    if isinstance(val, bool) or not isinstance(val, Decimal):
        raise TypeError(
            f"{name} must be a Decimal instance, got {type(val).__name__}."
        )
    if not val.is_finite():
        raise ValueError(f"{name} must be a finite number, got {val}.")
    if val < Decimal("0.00"):
        raise ValueError(f"{name} cannot be negative, got {val}.")
    return val.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _validate_timezone_parity(
    t1: datetime, t2: datetime, name1: str, name2: str
) -> None:
    """Validate that both datetimes have matching timezone awareness."""
    if (t1.tzinfo is not None) != (t2.tzinfo is not None):
        raise TypeError(
            f"Timestamp '{name1}' and '{name2}' must be uniformly timezone-aware or timezone-naive."
        )


@dataclass(frozen=True)
class RefundRecord:
    """Immutable financial ledger record for an order cancellation."""

    order_id: str
    refund_amount: Decimal
    cancellation_fee: Decimal
    recorded_at: datetime
    timing_stage: str
    substitution_refund: Decimal = Decimal("0.00")
    reason: str = ""


@dataclass(frozen=True)
class CancellationResult:
    """Immutable structured outcome returned by CancellationService.cancel_order()."""

    order_id: str
    status: OrderState
    cancellation_fee: Decimal
    refund_amount: Decimal
    released_reservations: tuple[str, ...]
    skipped_reservations: tuple[tuple[str, str], ...] = ()
    timing_stage: str = ""
    batch_id: str | None = None
    rider_released: bool = False
    is_idempotent: bool = False
    reason: str = ""


@dataclass
class CancellationOperationState:
    """Tracks operational progress and request fingerprint for safe resumable retries."""

    order_id: str
    order_ref: Order
    batch_ref: Batch | None
    amount_paid: Decimal | None
    reservation_ids: tuple[str, ...]
    reason: str
    cancelled_at: datetime | None
    picking_start: datetime | None
    status: str = "IN_PROGRESS"  # "IN_PROGRESS" | "COMPLETED"
    refund_recorded: bool = False
    released_reservations: tuple[str, ...] = ()
    skipped_reservations: tuple[tuple[str, str], ...] = ()
    lifecycle_completed: bool = False
    batch_cancelled: bool = False
    result: CancellationResult | None = None


@runtime_checkable
class CancellationServiceInterface(Protocol):
    """Protocol defining customer cancellation operations under BR-12."""

    def cancel_order(
        self,
        order: Order,
        cancelled_at: datetime | None = None,
        batch: Batch | None = None,
        reservation_ids: Sequence[str | Reservation] | None = None,
        amount_paid: Decimal | None = None,
        picking_start: datetime | None = None,
        reason: str = "Customer cancelled",
    ) -> CancellationResult:
        """Cancel an order under BR-12 with safe retries and failure preservation."""
        ...

    def calculate_cancellation_fee(
        self,
        order: Order,
        cancelled_at: datetime | None = None,
        picking_start: datetime | None = None,
    ) -> Decimal:
        """Pure calculation of applicable cancellation fee without mutating state."""
        ...

    def calculate_refund(
        self,
        order: Order,
        amount_paid: Decimal | None = None,
        cancelled_at: datetime | None = None,
        picking_start: datetime | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Pure calculation returning (cancellation_fee, refund_amount) without mutating state."""
        ...

    def get_refund(self, order_id: str) -> Decimal | None:
        """Retrieve recorded refund amount from the immutable ledger."""
        ...

    def get_cancellation_record(self, order_id: str) -> RefundRecord | None:
        """Retrieve full immutable refund record from the ledger."""
        ...


class CancellationService:
    """Implements BR-12 Customer Cancellation with ledger persistence and safe retry semantics."""

    def __init__(
        self,
        inventory_service: InventoryService | None = None,
        batching_service: BatchingServiceInterface | None = None,
        lifecycle_service: LifecycleTransitionInterface | None = None,
        product_catalog: dict[str, Product] | None = None,
    ) -> None:
        self._inventory_service = inventory_service
        self._batching_service = batching_service
        self._lifecycle_service = (
            lifecycle_service or LifecycleTransitionService()
        )
        self._product_catalog = product_catalog or {}
        self._refund_ledger: dict[str, RefundRecord] = {}
        self._operation_state: dict[str, CancellationOperationState] = {}

    def get_refund(self, order_id: str) -> Decimal | None:
        """Retrieve recorded refund amount from the immutable ledger."""
        record = self._refund_ledger.get(order_id)
        return record.refund_amount if record is not None else None

    def get_cancellation_record(self, order_id: str) -> RefundRecord | None:
        """Retrieve full immutable refund record from the ledger."""
        return self._refund_ledger.get(order_id)

    def calculate_cancellation_fee(
        self,
        order: Order,
        cancelled_at: datetime | None = None,
        picking_start: datetime | None = None,
    ) -> Decimal:
        """Pure calculation of applicable cancellation fee under BR-12.

        - Pre-picking (RECEIVED, ALLOCATED): ₹0.00
        - Picking pre-dispatch (PICKING, READY_FOR_DISPATCH): ₹20.00
        - Post-dispatch (DISPATCHED): ₹0.00 (ordinary fees non-refundable)
        - If picking_start and cancelled_at are supplied, cancelled_at >= picking_start
          triggers the ₹20.00 fee even if the order state transition was pending.
        """
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )

        if order.status in (OrderState.DELIVERED, OrderState.REJECTED):
            raise ValueError(
                f"Cannot calculate cancellation fee for order '{order.order_id}' with terminal status '{order.status.value}'."
            )

        if order.status == OrderState.CANCELLED:
            existing = self._refund_ledger.get(order.order_id)
            if existing is not None:
                return existing.cancellation_fee
            return order.cancellation_fee

        if order.status == OrderState.DISPATCHED:
            return Decimal("0.00")

        # Check explicit picking start timestamp if provided
        if (
            picking_start is not None
            and cancelled_at is not None
            and cancelled_at >= picking_start
        ):
            return CANCELLATION_FEE_AMOUNT

        if order.status in (
            OrderState.PICKING,
            OrderState.READY_FOR_DISPATCH,
        ):
            return CANCELLATION_FEE_AMOUNT

        return Decimal("0.00")

    def calculate_refund(
        self,
        order: Order,
        amount_paid: Decimal | None = None,
        cancelled_at: datetime | None = None,
        picking_start: datetime | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Pure calculation returning (cancellation_fee, refund_amount)."""
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )

        fee = self.calculate_cancellation_fee(
            order, cancelled_at=cancelled_at, picking_start=picking_start
        )

        if amount_paid is not None:
            collected = _quantize_currency(amount_paid, "amount_paid")
        else:
            collected = _quantize_currency(
                order.original_total_value + order.total_delivery_fee,
                "collected_amount",
            )

        if order.status == OrderState.DISPATCHED:
            # Post-dispatch: no ordinary refund; delivery fee retained
            # Check substitution policy exceptions
            sub_refund = Decimal("0.00")
            if order.substitution_policy == SubstitutionPolicy.ALLOW:
                sub_refund = Decimal("0.00")
            elif order.substitution_policy == SubstitutionPolicy.NONE:
                # Customer forbade substitutions; all substituted quantities are unconsented
                for item in order.items:
                    if item.is_substituted:
                        unit_p = (
                            item.charged_unit_price
                            if item.charged_unit_price is not None
                            else item.unit_price
                        )
                        sub_refund += unit_p * Decimal(item.substituted_quantity)
            elif order.substitution_policy == SubstitutionPolicy.SIMILAR_ONLY:
                # Consent limited to same category, same brand, within +15% price
                for item in order.items:
                    if item.is_substituted:
                        sub_sku = item.substituted_product_sku
                        sub_prod = self._product_catalog.get(sub_sku) if sub_sku else None
                        if sub_prod is not None:
                            is_similar = (
                                sub_prod.category == item.category
                                and sub_prod.brand == item.brand
                                and sub_prod.price
                                <= item.unit_price * PRICE_TOLERANCE_MULTIPLIER
                            )
                            if not is_similar:
                                # Similarity violated: unconsented
                                unit_p = (
                                    item.charged_unit_price
                                    if item.charged_unit_price is not None
                                    else item.unit_price
                                )
                                sub_refund += unit_p * Decimal(
                                    item.substituted_quantity
                                )
                        else:
                            # Limitation: Catalog metadata unavailable for substitute SKU.
                            # Legitimate fulfillment under SubstitutionService enforces SIMILAR_ONLY,
                            # so compliance is presumed absent evidence of violation.
                            pass

            return fee, _quantize_currency(sub_refund, "refund_amount")

        # Pre-dispatch calculations
        refund = max(Decimal("0.00"), collected - fee)
        return fee, _quantize_currency(refund, "refund_amount")

    def cancel_order(
        self,
        order: Order,
        cancelled_at: datetime | None = None,
        batch: Batch | None = None,
        reservation_ids: Sequence[str | Reservation] | None = None,
        amount_paid: Decimal | None = None,
        picking_start: datetime | None = None,
        reason: str = "Customer cancelled",
    ) -> CancellationResult:
        """Cancel an order under BR-12 with safe retries and failure preservation."""
        # --- 1. Argument validation ---
        if isinstance(order, bool) or not isinstance(order, Order):
            raise TypeError(
                f"order must be an Order instance, got {type(order).__name__}."
            )
        if batch is not None and (
            isinstance(batch, bool) or not isinstance(batch, Batch)
        ):
            raise TypeError(
                f"batch must be a Batch instance or None, got {type(batch).__name__}."
            )
        if cancelled_at is not None and (
            isinstance(cancelled_at, bool)
            or not isinstance(cancelled_at, datetime)
        ):
            raise TypeError(
                f"cancelled_at must be a datetime or None, got {type(cancelled_at).__name__}."
            )
        if picking_start is not None and (
            isinstance(picking_start, bool)
            or not isinstance(picking_start, datetime)
        ):
            raise TypeError(
                f"picking_start must be a datetime or None, got {type(picking_start).__name__}."
            )
        if amount_paid is not None:
            amount_paid = _quantize_currency(amount_paid, "amount_paid")
        if isinstance(reason, bool) or not isinstance(reason, str):
            raise TypeError(
                f"reason must be a string, got {type(reason).__name__}."
            )

        if cancelled_at is not None and order.order_time is not None:
            _validate_timezone_parity(
                cancelled_at, order.order_time, "cancelled_at", "order.order_time"
            )
            if cancelled_at < order.order_time:
                raise ValueError(
                    "cancelled_at cannot be earlier than order.order_time."
                )

        if picking_start is not None and order.order_time is not None:
            _validate_timezone_parity(
                picking_start,
                order.order_time,
                "picking_start",
                "order.order_time",
            )

        # Normalize reservation_ids
        norm_res_ids: tuple[str, ...] | None = None
        if reservation_ids is not None:
            res_list: list[str] = []
            for r in reservation_ids:
                if isinstance(r, Reservation):
                    res_list.append(r.reservation_id)
                elif isinstance(r, str) and not isinstance(r, bool):
                    if not r.strip():
                        raise ValueError("reservation ID must not be empty or whitespace.")
                    res_list.append(r.strip())
                else:
                    raise TypeError(
                        f"reservation_ids elements must be Reservation or str, got {type(r).__name__}."
                    )
            norm_res_ids = tuple(sorted(set(res_list)))

        # --- 2. Request fingerprint validation on retries ---
        if order.order_id in self._operation_state:
            op = self._operation_state[order.order_id]
            if order is not op.order_ref:
                raise ValueError(
                    f"Provided order object does not match the order object previously registered for cancellation of order '{order.order_id}'."
                )
            if (batch is None) != (op.batch_ref is None):
                raise ValueError("Retry request changed batch parameter presence.")
            if batch is not None and batch is not op.batch_ref:
                raise ValueError(
                    f"Provided batch object does not match the batch object previously registered for order '{order.order_id}'."
                )
            if amount_paid != op.amount_paid:
                raise ValueError(
                    f"Cannot retry cancellation of order '{order.order_id}' with altered amount_paid (original: {op.amount_paid}, requested: {amount_paid})."
                )
            if norm_res_ids is not None and norm_res_ids != op.reservation_ids:
                raise ValueError(
                    f"Cannot retry cancellation of order '{order.order_id}' with altered reservation IDs."
                )
            if reason != op.reason:
                raise ValueError(
                    f"Cannot retry cancellation of order '{order.order_id}' with altered cancellation reason."
                )
            if cancelled_at is not None and cancelled_at != op.cancelled_at:
                raise ValueError(
                    f"Cannot retry cancellation of order '{order.order_id}' with altered cancelled_at timestamp (original: {op.cancelled_at}, requested: {cancelled_at})."
                )
            if picking_start is not None and picking_start != op.picking_start:
                raise ValueError(
                    f"Cannot retry cancellation of order '{order.order_id}' with altered picking_start timestamp (original: {op.picking_start}, requested: {picking_start})."
                )

            # If operation is already completed, return cached result idempotently
            if op.status == "COMPLETED" and op.result is not None:
                return replace(op.result, is_idempotent=True)
        else:
            # First invocation: reject terminal states
            if order.status in (OrderState.DELIVERED, OrderState.REJECTED):
                raise ValueError(
                    f"Cannot cancel order '{order.order_id}' with terminal status '{order.status.value}'."
                )
            if order.status == OrderState.CANCELLED:
                existing_record = self._refund_ledger.get(order.order_id)
                if existing_record is not None:
                    return CancellationResult(
                        order_id=order.order_id,
                        status=OrderState.CANCELLED,
                        cancellation_fee=existing_record.cancellation_fee,
                        refund_amount=existing_record.refund_amount,
                        released_reservations=(),
                        timing_stage=existing_record.timing_stage,
                        is_idempotent=True,
                        reason=existing_record.reason,
                    )
                raise ValueError(
                    f"Order '{order.order_id}' is already CANCELLED without an active cancellation workflow."
                )

            # Resolve reservation IDs for initial call
            resolved_res_tuple: tuple[str, ...]
            if norm_res_ids is not None:
                resolved_res_tuple = norm_res_ids
            else:
                if self._inventory_service is not None:
                    discovered = self._inventory_service.get_reservations_for_order(
                        order.order_id
                    )
                    resolved_res_tuple = tuple(sorted(r.reservation_id for r in discovered))
                else:
                    resolved_res_tuple = ()

            # Anti-silent-skip policy: active inventory-holding states require reservations
            if order.status in (
                OrderState.ALLOCATED,
                OrderState.PICKING,
                OrderState.READY_FOR_DISPATCH,
            ):
                if norm_res_ids is None and not resolved_res_tuple and self._inventory_service is not None:
                    raise ValueError(
                        f"Cannot cancel order '{order.order_id}' in state '{order.status.value}': inventory service holds no active reservations and none were provided."
                    )

            op = CancellationOperationState(
                order_id=order.order_id,
                order_ref=order,
                batch_ref=batch,
                amount_paid=amount_paid,
                reservation_ids=resolved_res_tuple,
                reason=reason,
                cancelled_at=cancelled_at,
                picking_start=picking_start,
            )
            self._operation_state[order.order_id] = op

        # --- 3. Calculate fee, refund, and timing stage ---
        fee, refund = self.calculate_refund(
            order,
            amount_paid=op.amount_paid,
            cancelled_at=op.cancelled_at,
            picking_start=op.picking_start,
        )

        timing_stage = "PRE_PICKING"
        if order.status == OrderState.DISPATCHED:
            timing_stage = "POST_DISPATCH"
        elif (
            order.status in (OrderState.PICKING, OrderState.READY_FOR_DISPATCH)
            or (
                op.picking_start is not None
                and op.cancelled_at is not None
                and op.cancelled_at >= op.picking_start
            )
        ):
            timing_stage = "PICKING_PRE_DISPATCH"

        # --- 4. Record immutable refund in ledger ---
        if not op.refund_recorded:
            if order.order_id not in self._refund_ledger:
                rec_at = op.cancelled_at or datetime.now()
                record = RefundRecord(
                    order_id=order.order_id,
                    refund_amount=refund,
                    cancellation_fee=fee,
                    recorded_at=rec_at,
                    timing_stage=timing_stage,
                    reason=op.reason,
                )
                self._refund_ledger[order.order_id] = record

            ledger_record = self._refund_ledger[order.order_id]
            order.refund_amount = ledger_record.refund_amount
            order.cancellation_fee = ledger_record.cancellation_fee
            op.refund_recorded = True

        # --- 5. Release eligible reservations ---
        if order.status != OrderState.DISPATCHED and self._inventory_service is not None:
            newly_released = list(op.released_reservations)
            newly_skipped = list(op.skipped_reservations)
            try:
                for res_id in op.reservation_ids:
                    if res_id in newly_released or any(
                        s[0] == res_id for s in newly_skipped
                    ):
                        continue
                    try:
                        res = self._inventory_service.get_reservation(res_id)
                        if res.status == ReservationStatus.COMMITTED:
                            newly_skipped.append((res_id, "COMMITTED"))
                        elif res.status == ReservationStatus.RELEASED:
                            newly_skipped.append((res_id, "ALREADY_RELEASED"))
                        elif res.status == ReservationStatus.PENDING:
                            self._inventory_service.release_reservation(res_id)
                            newly_released.append(res_id)
                    except KeyError:
                        newly_skipped.append((res_id, "UNKNOWN"))
            finally:
                op.released_reservations = tuple(newly_released)
                op.skipped_reservations = tuple(newly_skipped)

        # --- 6. Transition order lifecycle ---
        if not op.lifecycle_completed:
            if order.status != OrderState.CANCELLED:
                self._lifecycle_service.transition(order, OrderState.CANCELLED)
            op.lifecycle_completed = True

        # --- 7. Update batch through BatchingService ---
        batch_id: str | None = None
        rider_released = False
        if batch is not None:
            batch_id = batch.batch_id
            if not op.batch_cancelled:
                if self._batching_service is not None:
                    batch_res = self._batching_service.cancel_order_in_batch(
                        batch, order, reason=op.reason
                    )
                    rider_released = batch_res.rider_released
                op.batch_cancelled = True

        # --- 8. Mark operation COMPLETED and cache result ---
        result = CancellationResult(
            order_id=order.order_id,
            status=order.status,
            cancellation_fee=fee,
            refund_amount=refund,
            released_reservations=op.released_reservations,
            skipped_reservations=op.skipped_reservations,
            timing_stage=timing_stage,
            batch_id=batch_id,
            rider_released=rider_released,
            is_idempotent=False,
            reason=op.reason,
        )
        op.status = "COMPLETED"
        op.result = result
        return result
