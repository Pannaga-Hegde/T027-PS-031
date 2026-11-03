from enum import Enum


class OrderState(str, Enum):
    """Lifecycle states of an order as specified in state-diagram.mmd."""

    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"
    ALLOCATED = "ALLOCATED"
    PICKING = "PICKING"
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class InvalidOrderTransitionError(ValueError):
    """Raised when an invalid order lifecycle transition is attempted."""

    def __init__(
        self,
        message: str = "",
        from_state: str | OrderState | None = None,
        to_state: str | OrderState | None = None,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        if not message and from_state is not None and to_state is not None:
            from_str = getattr(from_state, "value", str(from_state))
            to_str = getattr(to_state, "value", str(to_state))
            message = f"Cannot transition order from {from_str} to {to_str}."
        super().__init__(message)
