from typing import Any, Protocol, runtime_checkable

from ps031.models.order_state import InvalidOrderTransitionError, OrderState

_PERMITTED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.RECEIVED: {
        OrderState.REJECTED,
        OrderState.ALLOCATED,
        OrderState.CANCELLED,
    },
    OrderState.REJECTED: set(),
    OrderState.ALLOCATED: {
        OrderState.PICKING,
        OrderState.CANCELLED,
    },
    OrderState.PICKING: {
        OrderState.READY_FOR_DISPATCH,
        OrderState.CANCELLED,
    },
    OrderState.READY_FOR_DISPATCH: {
        OrderState.DISPATCHED,
        OrderState.CANCELLED,
    },
    OrderState.DISPATCHED: {
        OrderState.DELIVERED,
        OrderState.CANCELLED,
    },
    OrderState.DELIVERED: set(),
    OrderState.CANCELLED: set(),
}


def _coerce_state(state: Any, role: str = "target") -> OrderState:
    if isinstance(state, OrderState):
        return state
    if isinstance(state, str) and not isinstance(state, bool):
        try:
            return OrderState(state)
        except ValueError:
            try:
                return OrderState[state.upper()]
            except (KeyError, AttributeError):
                raise InvalidOrderTransitionError(
                    f"Unknown {role} order state: {state!r}."
                )
    raise InvalidOrderTransitionError(
        f"Invalid {role} state type: expected OrderState or str, got {type(state).__name__}."
    )


@runtime_checkable
class LifecycleTransitionInterface(Protocol):
    """Protocol defining the order lifecycle transition interface."""

    def validate_transition(
        self, order: Any, target_state: OrderState | str
    ) -> None:
        """Validate if the order can transition to target_state without mutating order.

        Raises InvalidOrderTransitionError if the transition is prohibited.
        """
        ...

    def transition(
        self, order: Any, target_state: OrderState | str
    ) -> OrderState:
        """Validate and transition order to target_state, updating order.status.

        Returns the new OrderState.
        Raises InvalidOrderTransitionError if the transition is prohibited.
        """
        ...


class LifecycleTransitionService:
    """Service governing order lifecycle transitions according to business rules."""

    def validate_transition(
        self, order: Any, target_state: OrderState | str
    ) -> None:
        """Validate transition from order.status to target_state without mutating order.

        Raises InvalidOrderTransitionError if transition is prohibited or states are invalid.
        """
        if not hasattr(order, "status"):
            raise InvalidOrderTransitionError(
                f"Order object of type {type(order).__name__} has no 'status' attribute."
            )

        current = _coerce_state(order.status, role="current")
        target = _coerce_state(target_state, role="target")

        permitted = _PERMITTED_TRANSITIONS.get(current, set())
        if target not in permitted:
            if not permitted:
                msg = (
                    f"Cannot transition order from terminal state {current.value} "
                    f"to {target.value}."
                )
            else:
                allowed_str = ", ".join(sorted(s.value for s in permitted))
                msg = (
                    f"Cannot transition order from {current.value} to {target.value}. "
                    f"Permitted transitions from {current.value}: [{allowed_str}]."
                )
            raise InvalidOrderTransitionError(
                message=msg,
                from_state=current,
                to_state=target,
            )

    def transition(
        self, order: Any, target_state: OrderState | str
    ) -> OrderState:
        """Validate before mutation, update order.status, and return new OrderState.

        Raises InvalidOrderTransitionError if transition is prohibited.
        """
        self.validate_transition(order, target_state)
        target = _coerce_state(target_state, role="target")
        order.status = target
        return target
