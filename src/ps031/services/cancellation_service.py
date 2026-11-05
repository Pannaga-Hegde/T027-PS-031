from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ps031.models.order import Order
from ps031.models.order_state import OrderState
from ps031.services.fulfilment_service import FulfilmentEvaluation, FulfilmentService
from ps031.services.inventory_service import (
    InventoryService,
    Reservation,
    ReservationStatus,
)
from ps031.services.lifecycle import LifecycleTransitionService


@dataclass(frozen=True)
class CancellationResult:
    """Outcome of a BR-06 cancellation decision and its side effects."""

    order_id: str
    is_cancelled: bool
    reason: str
    evaluation: FulfilmentEvaluation
    refund_amount: Decimal
    released_reservation_ids: tuple[str, ...] = ()
    already_released_reservation_ids: tuple[str, ...] = ()
    skipped_committed_reservation_ids: tuple[str, ...] = ()


class CancellationService:
    """
    Coordinates BR-06 cancellation side effects.

    FulfilmentService remains the authority for the 60% decision and refund
    calculation. This service decides which pending reservations to release,
    delegates stock restoration to InventoryService, performs one lifecycle
    transition, and records one in-memory refund decision per order.

    The refund ledger is process-local and is not durable or crash-safe. It
    records the refund decision only; it does not call a payment provider.
    """

    def __init__(
        self,
        inventory_service: InventoryService,
        fulfilment_service: FulfilmentService | None = None,
        lifecycle_service: LifecycleTransitionService | None = None,
    ) -> None:
        self.inventory_service = inventory_service
        self.fulfilment_service = fulfilment_service or FulfilmentService()
        self.lifecycle_service = lifecycle_service or LifecycleTransitionService()
        self._refund_ledger: dict[str, Decimal] = {}

    @property
    def refund_ledger(self) -> dict[str, Decimal]:
        """Return a detached view of recorded order refund decisions."""
        return dict(self._refund_ledger)

    def cancel(
        self,
        order: Order,
        reservations: Sequence[Reservation],
        reason: str,
        amount_paid: Decimal | None = None,
    ) -> CancellationResult:
        if not isinstance(order, Order):
            raise TypeError("order must be an Order instance.")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string.")

        reservation_list = tuple(reservations)
        for reservation in reservation_list:
            if not isinstance(reservation, Reservation):
                raise TypeError("reservations must contain Reservation instances.")
            if reservation.order_id != order.order_id:
                raise ValueError(
                    f"Reservation '{reservation.reservation_id}' belongs to "
                    f"order '{reservation.order_id}', not '{order.order_id}'."
                )

        # A repeated call returns the original recorded refund without creating
        # another refund decision or repeating lifecycle transitions.
        if order.order_id in self._refund_ledger:
            evaluation = self.fulfilment_service.evaluate_order(order, amount_paid)
            return CancellationResult(
                order_id=order.order_id,
                is_cancelled=True,
                reason=reason,
                evaluation=evaluation,
                refund_amount=self._refund_ledger[order.order_id],
            )

        current_state = order.status
        if current_state in (OrderState.DISPATCHED, OrderState.DELIVERED):
            raise ValueError(
                "BR-06 cancellation is only permitted before dispatch; "
                f"order is currently {current_state.value}."
            )

        evaluation = self.fulfilment_service.evaluate_order(order, amount_paid)
        if current_state != OrderState.CANCELLED and not evaluation.is_cancelled:
            return CancellationResult(
                order_id=order.order_id,
                is_cancelled=False,
                reason=reason,
                evaluation=evaluation,
                refund_amount=evaluation.refund_amount,
            )

        # Validate lifecycle before making any inventory changes.
        if current_state != OrderState.CANCELLED:
            self.lifecycle_service.validate_transition(order, OrderState.CANCELLED)

        released: list[str] = []
        already_released: list[str] = []
        skipped_committed: list[str] = []

        # Read authoritative reservation state from InventoryService. The
        # service is the sole authority that restores stock.
        for supplied in reservation_list:
            current = self.inventory_service.get_reservation(
                supplied.reservation_id
            )
            if current.order_id != order.order_id:
                raise ValueError(
                    f"Reservation '{current.reservation_id}' does not belong "
                    f"to order '{order.order_id}'."
                )
            if current.status == ReservationStatus.RELEASED:
                already_released.append(current.reservation_id)
            elif current.status == ReservationStatus.COMMITTED:
                skipped_committed.append(current.reservation_id)
            elif current.status == ReservationStatus.PENDING:
                self.inventory_service.release_reservation(current.reservation_id)
                released.append(current.reservation_id)

        if current_state != OrderState.CANCELLED:
            self.lifecycle_service.transition(order, OrderState.CANCELLED)

        refund = (
            order.refund_amount
            if (order.refund_amount and order.refund_amount > Decimal("0.00"))
            else evaluation.refund_amount
        )
        order.refund_amount = refund
        self._refund_ledger[order.order_id] = refund

        return CancellationResult(
            order_id=order.order_id,
            is_cancelled=True,
            reason=reason,
            evaluation=evaluation,
            refund_amount=refund,
            released_reservation_ids=tuple(released),
            already_released_reservation_ids=tuple(already_released),
            skipped_committed_reservation_ids=tuple(skipped_committed),
        )
