from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Sequence
import uuid

from ps031.models.product import Product
from ps031.models.store import Store


class ReservationStatus(str, Enum):
    """Lifecycle status of an inventory reservation."""

    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"


class InvalidReservationStateError(ValueError):
    """Raised when an operation is invalid for the reservation's current status."""

    pass


@dataclass
class Reservation:
    """Internal record tracking an active or terminal inventory reservation."""

    reservation_id: str
    store_id: str
    order_id: str
    sku: str
    requested_quantity: int
    reserved_quantity: int
    status: ReservationStatus = ReservationStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ReservationResult:
    """Outcome of an inventory reservation request."""

    reservation_id: str | None
    store_id: str
    order_id: str
    sku: str
    requested_quantity: int
    reserved_quantity: int
    status: ReservationStatus | None = None

    @property
    def is_fulfilled(self) -> bool:
        """Return True if the requested quantity was completely reserved."""
        return self.reserved_quantity == self.requested_quantity and self.requested_quantity > 0

    @property
    def is_partial(self) -> bool:
        """Return True if some, but not all, requested units were reserved."""
        return 0 < self.reserved_quantity < self.requested_quantity

    @property
    def is_zero(self) -> bool:
        """Return True if no units could be reserved."""
        return self.reserved_quantity == 0


class InventoryService:
    """
    Manages live dark-store inventory stock, reservations, and product queries.

    Key principles:
    - Never caches stock snapshots: always reads and updates live Store.inventory.
    - Reserves available stock up to the requested quantity (supporting partials).
    - Decreases Store.inventory immediately upon reservation.
    - Commits are idempotent; releases restore stock exactly once (also idempotent).
    - Tracks reservations internally with unique IDs and returns isolated copies.
    - Safe registration prevents accidental replacement of existing stores and products.
    """

    def __init__(
        self,
        stores: Sequence[Store] | dict[str, Store] | None = None,
        products: Sequence[Product] | dict[str, Product] | None = None,
    ) -> None:
        self._stores: dict[str, Store] = {}
        self._products: dict[str, Product] = {}
        self._reservations: dict[str, Reservation] = {}

        if stores is not None:
            if isinstance(stores, dict):
                for s in stores.values():
                    self.register_store(s)
            else:
                for s in stores:
                    self.register_store(s)

        if products is not None:
            if isinstance(products, dict):
                for p in products.values():
                    self.register_product(p)
            else:
                for p in products:
                    self.register_product(p)

    def register_store(self, store: Store) -> None:
        """
        Register a Store instance with the service.

        Raises:
            TypeError: If store is not a Store instance.
            ValueError: If a store with store_id is already registered.
        """
        if not isinstance(store, Store):
            raise TypeError(f"Expected Store instance, got {type(store).__name__}.")
        if store.store_id in self._stores:
            raise ValueError(f"Store with ID '{store.store_id}' is already registered.")
        self._stores[store.store_id] = store

    def has_store(self, store_id: str) -> bool:
        """Check whether a dark store is registered."""
        if isinstance(store_id, bool) or not isinstance(store_id, str):
            return False
        return store_id in self._stores

    def get_store(self, store_id: str) -> Store | None:
        """Retrieve a registered Store instance, or None if not registered."""
        if isinstance(store_id, bool) or not isinstance(store_id, str):
            return None
        return self._stores.get(store_id)

    def unregister_store(self, store_id: str) -> None:
        """Remove a registered store (used during registration rollback)."""
        self._stores.pop(store_id, None)

    def register_product(self, product: Product) -> None:
        """
        Register a catalog Product instance with the service.

        Raises:
            TypeError: If product is not a Product instance.
            ValueError: If a product with sku is already registered.
        """
        if not isinstance(product, Product):
            raise TypeError(f"Expected Product instance, got {type(product).__name__}.")
        if product.sku in self._products:
            raise ValueError(f"Product with SKU '{product.sku}' is already registered.")
        self._products[product.sku] = product

    def has_product(self, sku: str) -> bool:
        """Check whether a catalog product SKU is registered."""
        if isinstance(sku, bool) or not isinstance(sku, str):
            return False
        return sku in self._products

    def get_product(self, sku: str) -> Product | None:
        """Retrieve a registered Product instance, or None if not registered."""
        if isinstance(sku, bool) or not isinstance(sku, str):
            return None
        return self._products.get(sku)

    def unregister_product(self, sku: str) -> None:
        """Remove a registered product (used during registration rollback)."""
        self._products.pop(sku, None)

    def _copy_reservation(self, reservation: Reservation) -> Reservation:
        """Return a safe detached copy of the reservation to protect internal state."""
        return replace(reservation)

    def _get_store(self, store_id: str) -> Store:
        if isinstance(store_id, bool) or not isinstance(store_id, str):
            raise TypeError(f"store_id must be a string, got {type(store_id).__name__}.")
        if not store_id.strip():
            raise ValueError("store_id must not be empty.")
        if store_id not in self._stores:
            raise KeyError(f"Store '{store_id}' not found.")
        return self._stores[store_id]

    def _validate_sku(self, store: Store, sku: str) -> None:
        if isinstance(sku, bool) or not isinstance(sku, str):
            raise TypeError(f"sku must be a string, got {type(sku).__name__}.")
        if not sku.strip():
            raise ValueError("sku must not be empty.")
        if self._products:
            if sku not in self._products:
                raise KeyError(f"Product SKU '{sku}' not found in catalog.")
        else:
            if sku not in store.inventory:
                raise KeyError(f"Product SKU '{sku}' not found in store inventory.")

    def _validate_reservation_id(self, reservation_id: str) -> Reservation:
        if isinstance(reservation_id, bool) or not isinstance(reservation_id, str):
            raise TypeError(
                f"reservation_id must be a string, got {type(reservation_id).__name__}."
            )
        if not reservation_id.strip():
            raise ValueError("reservation_id must not be empty.")
        if reservation_id not in self._reservations:
            raise KeyError(f"Reservation '{reservation_id}' not found.")
        return self._reservations[reservation_id]

    def get_stock(self, store_id: str, sku: str) -> int:
        """
        Return the current available quantity from the corresponding Store.inventory.

        Always reads live Store.inventory; does not cache stock snapshots.
        Raises KeyError for an unknown store or unknown SKU.
        """
        store = self._get_store(store_id)
        self._validate_sku(store, sku)
        return store.inventory.get(sku, 0)

    def reserve_stock(
        self, store_id: str, order_id: str, sku: str, quantity: int
    ) -> ReservationResult:
        """
        Reserve as much as currently available, up to the requested quantity.

        - Quantity must be a positive integer; rejects booleans, floats, strings, <= 0.
        - Immediately decreases Store.inventory by the reserved quantity.
        - Supports partial reservations.
        - If zero stock is available, returns a zero-reservation result without creating an active reservation.
        - Each successful reservation receives a unique reservation ID.
        """
        store = self._get_store(store_id)

        if isinstance(order_id, bool) or not isinstance(order_id, str):
            raise TypeError(f"order_id must be a string, got {type(order_id).__name__}.")
        if not order_id.strip():
            raise ValueError("order_id must not be empty.")

        self._validate_sku(store, sku)

        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError(
                f"Quantity must be an integer (booleans, strings, and floats are not permitted), got {type(quantity).__name__}."
            )
        if quantity <= 0:
            raise ValueError(f"Quantity must be a positive integer, got {quantity}.")

        available = store.inventory.get(sku, 0)
        if available <= 0:
            return ReservationResult(
                reservation_id=None,
                store_id=store_id,
                order_id=order_id,
                sku=sku,
                requested_quantity=quantity,
                reserved_quantity=0,
                status=None,
            )

        reserved_qty = min(available, quantity)
        store.inventory[sku] = available - reserved_qty

        reservation_id = f"res_{uuid.uuid4().hex}"
        reservation = Reservation(
            reservation_id=reservation_id,
            store_id=store_id,
            order_id=order_id,
            sku=sku,
            requested_quantity=quantity,
            reserved_quantity=reserved_qty,
            status=ReservationStatus.PENDING,
        )
        self._reservations[reservation_id] = reservation

        return ReservationResult(
            reservation_id=reservation_id,
            store_id=store_id,
            order_id=order_id,
            sku=sku,
            requested_quantity=quantity,
            reserved_quantity=reserved_qty,
            status=ReservationStatus.PENDING,
        )

    def commit_reservation(self, reservation_id: str) -> Reservation:
        """
        Change reservation status from PENDING to COMMITTED.

        - Does not change Store.inventory again.
        - Repeated commit is idempotent.
        - Committing a RELEASED reservation raises InvalidReservationStateError.
        - Unknown reservation ID raises KeyError.
        - Returns a safe isolated copy of the Reservation.
        """
        reservation = self._validate_reservation_id(reservation_id)
        if reservation.status == ReservationStatus.COMMITTED:
            return self._copy_reservation(reservation)
        if reservation.status == ReservationStatus.RELEASED:
            raise InvalidReservationStateError(
                f"Cannot commit reservation '{reservation_id}': already RELEASED."
            )

        reservation.status = ReservationStatus.COMMITTED
        return self._copy_reservation(reservation)

    def release_reservation(self, reservation_id: str) -> Reservation:
        """
        Change reservation status from PENDING to RELEASED and restore reserved quantity.

        - Restores reserved quantity to Store.inventory exactly once.
        - Repeated release is idempotent.
        - Releasing a COMMITTED reservation raises InvalidReservationStateError.
        - Unknown reservation ID raises KeyError.
        - Returns a safe isolated copy of the Reservation.
        """
        reservation = self._validate_reservation_id(reservation_id)
        if reservation.status == ReservationStatus.RELEASED:
            return self._copy_reservation(reservation)
        if reservation.status == ReservationStatus.COMMITTED:
            raise InvalidReservationStateError(
                f"Cannot release reservation '{reservation_id}': already COMMITTED."
            )

        reservation.status = ReservationStatus.RELEASED
        store = self._stores[reservation.store_id]
        current_stock = store.inventory.get(reservation.sku, 0)
        store.inventory[reservation.sku] = current_stock + reservation.reserved_quantity

        return self._copy_reservation(reservation)

    def get_substitution_candidates(
        self, store_id: str, original_sku: str
    ) -> list[Product]:
        """
        Return catalog Product objects for SKUs with positive current available stock at that store.

        - Excludes the original SKU.
        - Uses the existing Product model and current Store.inventory.
        - Does not implement BR-05 eligibility or policy filtering here; SubstitutionService handles that.
        - Raises KeyError if store_id or original_sku is unknown.
        """
        store = self._get_store(store_id)
        self._validate_sku(store, original_sku)

        candidates: list[Product] = []
        for product in self._products.values():
            if product.sku == original_sku:
                continue
            stock = store.inventory.get(product.sku, 0)
            if stock > 0:
                candidates.append(product)

        return candidates

    def get_reservation(self, reservation_id: str) -> Reservation:
        """
        Retrieve an isolated copy of a reservation by its ID.

        Raises:
            TypeError: If reservation_id is not a string or is a boolean.
            ValueError: If reservation_id is empty or whitespace.
            KeyError: If reservation_id does not exist.
        """
        reservation = self._validate_reservation_id(reservation_id)
        return self._copy_reservation(reservation)

    def get_reservations_for_order(
        self,
        order_id: str,
        status: ReservationStatus | None = ReservationStatus.PENDING,
    ) -> list[Reservation]:
        """
        Return safe detached copies of reservations registered for the given order.

        By default, queries active reservations with status ReservationStatus.PENDING.
        If status is None, returns reservations of any lifecycle status.

        Sorting is deterministic: ordered by created_at ascending, then reservation_id.

        Raises:
            TypeError: If order_id is not a string (or is a boolean), or if status is
                       not a ReservationStatus instance (when provided).
            ValueError: If order_id is empty or whitespace, or if status string is unknown.
        """
        if isinstance(order_id, bool) or not isinstance(order_id, str):
            raise TypeError(
                f"order_id must be a string, got {type(order_id).__name__}."
            )
        if not order_id.strip():
            raise ValueError("order_id must not be empty or whitespace.")

        if status is not None:
            if isinstance(status, str) and not isinstance(status, ReservationStatus):
                try:
                    status = ReservationStatus(status)
                except ValueError:
                    raise ValueError(f"Unknown ReservationStatus '{status}'.")
            elif not isinstance(status, ReservationStatus):
                raise TypeError(
                    f"status must be a ReservationStatus or None, got {type(status).__name__}."
                )

        matching = [
            r
            for r in self._reservations.values()
            if r.order_id == order_id and (status is None or r.status == status)
        ]
        matching.sort(key=lambda r: (r.created_at, r.reservation_id))
        return [self._copy_reservation(r) for r in matching]

    @property
    def reservations(self) -> dict[str, Reservation]:
        """Return safe detached copies of all internal reservations."""
        return {k: self._copy_reservation(v) for k, v in self._reservations.items()}
