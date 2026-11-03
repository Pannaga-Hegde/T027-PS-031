from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ps031.models.order import Order


class BatchStatus(str, Enum):
    """Lifecycle status of a delivery batch.

    Values:
    - CREATED: Batch has been formed and is waiting for orders/dispatch.
    - DISPATCHED: Batch has departed the store with the assigned rider.
    - DELIVERED: All orders in the batch have been delivered.
    - CANCELLED: Batch was cancelled before or during delivery.
    """

    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


@dataclass
class Batch:
    """Represents a delivery batch grouping orders for a rider."""

    batch_id: str
    rider_id: str
    created_at: datetime
    orders: list[Order] = field(default_factory=list)
    dispatched_at: datetime | None = None
    status: BatchStatus = BatchStatus.CREATED

    def __post_init__(self) -> None:
        # Validate identifiers
        for field_name, value in [
            ("Batch ID", self.batch_id),
            ("Rider ID", self.rider_id),
        ]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        # Validate status
        if isinstance(self.status, bool) or not isinstance(
            self.status, (str, BatchStatus)
        ):
            raise TypeError(
                f"Status must be a string, got {type(self.status).__name__}."
            )

        if isinstance(self.status, str) and not isinstance(self.status, BatchStatus):
            if not self.status.strip():
                raise ValueError("Status must not be empty.")
            try:
                self.status = BatchStatus(self.status.strip())
            except ValueError:
                try:
                    self.status = BatchStatus[self.status.strip().upper()]
                except (KeyError, AttributeError):
                    raise ValueError(
                        f"Invalid batch status: {self.status}. Allowed statuses: "
                        f"{sorted([s.value for s in BatchStatus])}."
                    )

        # Validate timestamps
        if self.created_at is None:
            raise TypeError("created_at is required and must be a datetime.")
        if isinstance(self.created_at, bool) or not isinstance(
            self.created_at, datetime
        ):
            raise TypeError(
                f"created_at must be a datetime, got {type(self.created_at).__name__}."
            )

        if self.dispatched_at is not None:
            if isinstance(self.dispatched_at, bool) or not isinstance(
                self.dispatched_at, datetime
            ):
                raise TypeError(
                    f"dispatched_at must be a datetime or None, got {type(self.dispatched_at).__name__}."
                )
            if (self.created_at.tzinfo is None) != (
                self.dispatched_at.tzinfo is None
            ):
                raise TypeError(
                    "Cannot compare timezone-aware and timezone-naive datetimes for created_at and dispatched_at."
                )
            if self.dispatched_at < self.created_at:
                raise ValueError(
                    "dispatched_at cannot be earlier than created_at."
                )

        # Validate and isolate orders list
        if self.orders is None:
            self.orders = []
        elif not isinstance(self.orders, (list, tuple)):
            raise TypeError(
                f"orders must be a list of Order objects, got {type(self.orders).__name__}."
            )
        else:
            self.orders = list(self.orders)

        for order in self.orders:
            if not isinstance(order, Order):
                raise TypeError(
                    f"All batch orders must be Order instances, got {type(order).__name__}."
                )

        # Check for duplicate order IDs
        seen_ids: set[str] = set()
        for order in self.orders:
            if order.order_id in seen_ids:
                raise ValueError(
                    f"Duplicate order ID '{order.order_id}' found in batch."
                )
            seen_ids.add(order.order_id)
