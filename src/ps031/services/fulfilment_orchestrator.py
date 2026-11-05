from dataclasses import dataclass
from typing import Sequence

from ps031.models.inventory import InventoryItem
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.services.cancellation_service import CancellationResult, CancellationService
from ps031.services.fulfilment_service import FulfilmentEvaluation, FulfilmentService
from ps031.services.inventory_service import (
    InventoryService,
    Reservation,
    ReservationStatus,
)
from ps031.services.substitution_service import SubstitutionResult, SubstitutionService


@dataclass(frozen=True)
class FulfilmentResult:
    """Summary of applied reservations, substitution decisions and BR-06 outcome."""

    order_id: str
    applied_reservation_ids: tuple[str, ...]
    newly_applied_reservation_ids: tuple[str, ...]
    substitution_results: tuple[SubstitutionResult, ...]
    evaluation: FulfilmentEvaluation
    cancellation_result: CancellationResult | None


class FulfilmentOrchestrator:
    """
    Coordinates reservation-backed line updates, BR-05 substitutions and BR-06.

    InventoryService is the authority for stock and reservation state. A line
    is changed only using the quantity in an actual reservation, never the
    quantity from candidate discovery. Applied reservation IDs are tracked in
    memory and recorded only after the corresponding line update succeeds.

    The applied-ID registry is process-local. It prevents duplicate updates
    when this orchestrator instance is retried, but it is not crash-safe or
    shared across multiple processes.
    """

    def __init__(
        self,
        inventory_service: InventoryService,
        substitution_service: SubstitutionService | None = None,
        fulfilment_service: FulfilmentService | None = None,
        cancellation_service: CancellationService | None = None,
    ) -> None:
        self.inventory_service = inventory_service
        self.substitution_service = substitution_service or SubstitutionService()
        self.fulfilment_service = fulfilment_service or FulfilmentService()
        self.cancellation_service = cancellation_service or CancellationService(
            inventory_service=inventory_service,
            fulfilment_service=self.fulfilment_service,
        )
        self._applied_reservation_ids: set[str] = set()
        self._reservations_by_order: dict[str, dict[str, Reservation]] = {}
        self._reservation_line_kind: dict[str, tuple[str, str]] = {}

    @property
    def applied_reservation_ids(self) -> frozenset[str]:
        return frozenset(self._applied_reservation_ids)

    def fulfil(
        self, order: Order, reservations: Sequence[Reservation]
    ) -> FulfilmentResult:
        if not isinstance(order, Order):
            raise TypeError("order must be an Order instance.")
        if not order.allocated_store_id:
            raise ValueError("Order must have an allocated store for fulfilment.")

        # Validate supplied reservations before inserting into internal tracking
        for supplied in reservations:
            if not isinstance(supplied, Reservation):
                raise TypeError("reservations must contain Reservation instances.")
            if supplied.order_id != order.order_id:
                raise ValueError(
                    f"Reservation '{supplied.reservation_id}' belongs to order "
                    f"'{supplied.order_id}', not '{order.order_id}'."
                )
            if supplied.store_id != order.allocated_store_id:
                raise ValueError(
                    f"Reservation '{supplied.reservation_id}' belongs to store "
                    f"'{supplied.store_id}', but order '{order.order_id}' is "
                    f"allocated to store '{order.allocated_store_id}'."
                )

        known = self._reservations_by_order.setdefault(order.order_id, {})
        for supplied in reservations:
            known[supplied.reservation_id] = supplied

        original_skus = {line.sku_id for line in order.items}
        if len(original_skus) != len(order.items):
            raise ValueError(
                "Order must contain at most one line per original SKU for "
                "reservation-to-line matching."
            )
        for reservation in known.values():
            if (
                reservation.sku not in original_skus
                and reservation.reservation_id not in self._reservation_line_kind
            ):
                raise ValueError(
                    f"Reservation '{reservation.reservation_id}' has SKU "
                    f"'{reservation.sku}' that is neither an original order SKU "
                    "nor a substitute reservation created by this orchestrator."
                )

        all_reservations = list(known.values())
        for reservation in all_reservations:
            if reservation.store_id != order.allocated_store_id:
                raise ValueError(
                    f"Reservation '{reservation.reservation_id}' belongs to store "
                    f"'{reservation.store_id}', but order '{order.order_id}' is "
                    f"allocated to store '{order.allocated_store_id}'."
                )
        store_id = order.allocated_store_id

        newly_applied: list[str] = []
        substitution_results: list[SubstitutionResult] = []

        if order.status == OrderState.CANCELLED:
            evaluation = self.fulfilment_service.evaluate_order(order)
            cancellation_result = self.cancellation_service.cancel(
                order,
                all_reservations,
                reason="Repeated fulfilment call for an already-cancelled order",
            )
            return FulfilmentResult(
                order_id=order.order_id,
                applied_reservation_ids=tuple(
                    sorted(self._applied_reservation_ids_for_order(order.order_id))
                ),
                newly_applied_reservation_ids=(),
                substitution_results=(),
                evaluation=evaluation,
                cancellation_result=cancellation_result,
            )

        # Apply the original-SKU reservations first so substitution is based on
        # the remaining line shortfall after actual original stock is accounted.
        # Use _reservation_line_kind to distinguish original from substitute reservations;
        # a substitute reservation must never be matched to an original order line.
        for reservation in all_reservations:
            kind_info = self._reservation_line_kind.get(reservation.reservation_id)
            if kind_info is not None and kind_info[1] != "original":
                continue
            target_sku = kind_info[0] if kind_info is not None else reservation.sku
            item = next(
                (line for line in order.items if line.sku_id == target_sku),
                None,
            )
            if item is not None:
                if kind_info is None:
                    self._reservation_line_kind[reservation.reservation_id] = (
                        item.sku_id,
                        "original",
                    )
                self._apply_reservation(order, item, reservation, "original", newly_applied)

        if order.substitution_preference != SubstitutionPolicy.NONE and store_id:
            # Resume substitute reservations created by an earlier interrupted
            # call before attempting any new stock reservation.
            for reservation in all_reservations:
                association = self._reservation_line_kind.get(reservation.reservation_id)
                if association is None or association[1] != "substitute":
                    continue
                line = next(
                    (candidate for candidate in order.items if candidate.sku_id == association[0]),
                    None,
                )
                if line is not None:
                    self._apply_reservation(
                        order, line, reservation, "substitute", newly_applied
                    )

            for item in order.items:
                shortfall = item.shortfall_quantity
                if shortfall <= 0:
                    continue

                products = self.inventory_service.get_substitution_candidates(
                    store_id, item.sku_id
                )
                if item.substituted_product_sku is not None:
                    products = [
                        product for product in products
                        if product.sku == item.substituted_product_sku
                    ]
                candidates = [
                    InventoryItem(
                        sku_id=product.sku,
                        name=product.name,
                        category=product.category,
                        brand=product.brand,
                        price=product.price,
                        stock=self.inventory_service.get_stock(store_id, product.sku),
                    )
                    for product in products
                ]
                decision = self.substitution_service.resolve_line_shortfall(
                    requested_item=item,
                    available_inventory=candidates,
                    preference=order.substitution_preference,
                    needed_qty=shortfall,
                )
                if decision.substituted_sku_id is None or decision.fulfilled_qty <= 0:
                    substitution_results.append(decision)
                    continue

                reserved = self.inventory_service.reserve_stock(
                    store_id,
                    order.order_id,
                    decision.substituted_sku_id,
                    decision.fulfilled_qty,
                )
                if reserved.reservation_id is None:
                    substitution_results.append(
                        SubstitutionResult(
                            original_sku_id=item.sku_id,
                            substituted_sku_id=None,
                            fulfilled_qty=0,
                            shortfall_qty=shortfall,
                            charged_unit_price=item.unit_price,
                        )
                    )
                    continue

                substitute_reservation = self.inventory_service.get_reservation(
                    reserved.reservation_id
                )
                known[substitute_reservation.reservation_id] = substitute_reservation
                self._reservation_line_kind[substitute_reservation.reservation_id] = (
                    item.sku_id,
                    "substitute",
                )
                self._apply_reservation(
                    order, item, substitute_reservation, "substitute", newly_applied
                )
                substitution_results.append(
                    SubstitutionResult(
                        original_sku_id=item.sku_id,
                        substituted_sku_id=decision.substituted_sku_id,
                        fulfilled_qty=reserved.reserved_quantity,
                        shortfall_qty=shortfall - reserved.reserved_quantity,
                        charged_unit_price=item.unit_price,
                    )
                )

        all_reservations = list(known.values())
        evaluation = self.fulfilment_service.evaluate_order(order)
        cancellation_result = None

        if evaluation.is_cancelled:
            cancellation_result = self.cancellation_service.cancel(
                order,
                all_reservations,
                reason="BR-06 fulfilled value is below the 60% threshold",
            )
        else:
            # Commit only after BR-06 evaluation. Pending reservations must
            # remain releasable if the order falls below the cancellation bar.
            for reservation in all_reservations:
                current = self.inventory_service.get_reservation(
                    reservation.reservation_id
                )
                if current.status == ReservationStatus.PENDING:
                    self.inventory_service.commit_reservation(current.reservation_id)

        return FulfilmentResult(
            order_id=order.order_id,
            applied_reservation_ids=tuple(
                sorted(self._applied_reservation_ids_for_order(order.order_id))
            ),
            newly_applied_reservation_ids=tuple(newly_applied),
            substitution_results=tuple(substitution_results),
            evaluation=evaluation,
            cancellation_result=cancellation_result,
        )

    def _applied_reservation_ids_for_order(self, order_id: str) -> set[str]:
        return {
            reservation_id
            for reservation_id in self._applied_reservation_ids
            if any(
                reservation.reservation_id == reservation_id
                for reservation in self._reservations_by_order.get(order_id, {}).values()
            )
        }

    def _apply_reservation(
        self,
        order: Order,
        item: OrderItem,
        supplied: Reservation,
        kind: str,
        newly_applied: list[str],
    ) -> None:
        current = self.inventory_service.get_reservation(supplied.reservation_id)
        if current.order_id != order.order_id:
            raise ValueError(
                f"Reservation '{current.reservation_id}' does not belong to "
                f"order '{order.order_id}'."
            )

        if current.reservation_id in self._applied_reservation_ids:
            return
        if current.status == ReservationStatus.RELEASED:
            return
        if current.status == ReservationStatus.COMMITTED:
            raise ValueError(
                f"Reservation '{current.reservation_id}' is COMMITTED but has no "
                "applied-update record in this orchestrator instance."
            )
        if current.reserved_quantity <= 0:
            return

        if kind == "original":
            if current.sku != item.sku_id:
                raise ValueError("Original reservation SKU does not match order line.")
            if item.fulfilled_quantity + current.reserved_quantity > item.quantity:
                raise ValueError("Original reservation would over-fulfil the order line.")
            # Direct field update avoids the compatibility setter's automatic
            # substituted_quantity adjustment.
            item.fulfilled_quantity += current.reserved_quantity
        else:
            if item.substituted_product_sku not in (None, current.sku):
                raise ValueError(
                    "An order line may use only one substitute SKU; reservation "
                    f"'{current.reservation_id}' conflicts with the existing substitute."
                )
            if item.fulfilled_quantity + current.reserved_quantity > item.quantity:
                raise ValueError("Substitute reservation would over-fulfil the order line.")
            item.substituted_product_sku = current.sku
            item.substituted_quantity += current.reserved_quantity
            item.fulfilled_quantity += current.reserved_quantity
            item.charged_unit_price = item.unit_price

        # The update has succeeded; only now record the reservation as applied.
        self._applied_reservation_ids.add(current.reservation_id)
        newly_applied.append(current.reservation_id)
