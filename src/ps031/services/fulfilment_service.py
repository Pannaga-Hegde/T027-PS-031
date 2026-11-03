from dataclasses import dataclass
from decimal import Decimal

from ps031.models.order import Order


@dataclass(frozen=True)
class FulfilmentEvaluation:
    original_value: Decimal
    fulfilled_value: Decimal
    fulfilled_ratio: Decimal
    is_cancelled: bool
    refund_amount: Decimal
    delivery_fee_charged: Decimal


class FulfilmentService:
    """
    Implements BR-06: Partial Fulfilment and Cancellation Threshold.

    Rule:
    If fulfilled value drops below 60% (< 0.60) of original order value,
    cancel before dispatch with a full refund and no delivery fee.

    Threshold Boundary:
    - Strictly below 60% (< 0.60, e.g. 59.9%): order is cancelled.
    - Exactly 60.0% (0.60) or above (>= 0.60): order proceeds (not cancelled).

    Payment Model Assumption (Documented):
    - The PS-031 brief specifies 'full refund and no delivery fee' on pre-dispatch cancellation.
    - Under the upfront digital checkout model standard in quick commerce, full refund
      reimburses all customer charges collected (items value plus any delivery/surge fees).
    - If `amount_paid` is explicitly supplied, `refund_amount = amount_paid`.
    - If `amount_paid` is not supplied, `refund_amount = original_value + delivery_fee` on cancellation,
      and for non-cancelled orders, `refund_amount = original_value - fulfilled_value` for unfulfilled items.
    """

    CANCELLATION_THRESHOLD = Decimal("0.60")

    def evaluate_order(
        self,
        order: Order,
        amount_paid: Decimal | None = None,
    ) -> FulfilmentEvaluation:
        """
        Pure decision evaluation of an Order instance against the 60% threshold.
        Does not mutate order lifecycle state or batches.
        """
        return self.evaluate_fulfilment(
            original_value=order.original_total_value,
            fulfilled_value=order.fulfilled_total_value,
            delivery_fee=order.total_delivery_fee,
            amount_paid=amount_paid,
        )

    def evaluate_fulfilment(
        self,
        original_value: Decimal,
        fulfilled_value: Decimal,
        delivery_fee: Decimal = Decimal("0.00"),
        amount_paid: Decimal | None = None,
    ) -> FulfilmentEvaluation:
        """
        Pure decision method evaluating fulfilment values against the 60% threshold.

        Validations:
        - Rejects negative monetary inputs with ValueError.
        - Safely handles zero original order value without division by zero.
        """
        orig = Decimal(str(original_value))
        fulfilled = Decimal(str(fulfilled_value))
        fee = Decimal(str(delivery_fee))

        # Validate that negative monetary inputs are not silently accepted
        if orig < Decimal("0.00"):
            raise ValueError(f"original_value cannot be negative: {orig}")
        if fulfilled < Decimal("0.00"):
            raise ValueError(f"fulfilled_value cannot be negative: {fulfilled}")
        if fee < Decimal("0.00"):
            raise ValueError(f"delivery_fee cannot be negative: {fee}")
        if amount_paid is not None:
            paid = Decimal(str(amount_paid))
            if paid < Decimal("0.00"):
                raise ValueError(f"amount_paid cannot be negative: {paid}")
        else:
            paid = None

        # Handle zero original order value safely without division by zero
        if orig == Decimal("0.00"):
            ratio = Decimal("0.00")
            is_cancelled = True
            total_collected = paid if paid is not None else fee
            return FulfilmentEvaluation(
                original_value=orig,
                fulfilled_value=fulfilled,
                fulfilled_ratio=ratio,
                is_cancelled=is_cancelled,
                refund_amount=total_collected,
                delivery_fee_charged=Decimal("0.00"),
            )

        ratio = fulfilled / orig

        # Cancel strictly when fulfilled ratio is below 60% (< 0.60)
        if ratio < self.CANCELLATION_THRESHOLD:
            is_cancelled = True
            delivery_fee_charged = Decimal("0.00")
            # Full refund: 100% of collected money refunded, no delivery fee retained
            total_collected = paid if paid is not None else (orig + fee)
            refund_amount = total_collected
        else:
            is_cancelled = False
            delivery_fee_charged = fee
            # Order proceeds: refund only unfulfilled shortfall if paid upfront
            if paid is not None:
                expected_total = fulfilled + delivery_fee_charged
                refund_amount = max(Decimal("0.00"), paid - expected_total)
            else:
                refund_amount = max(Decimal("0.00"), orig - fulfilled)

        return FulfilmentEvaluation(
            original_value=orig,
            fulfilled_value=fulfilled,
            fulfilled_ratio=ratio,
            is_cancelled=is_cancelled,
            refund_amount=refund_amount,
            delivery_fee_charged=delivery_fee_charged,
        )
