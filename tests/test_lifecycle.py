from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import pytest

from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import InvalidOrderTransitionError, OrderState
from ps031.services.lifecycle import (
    LifecycleTransitionInterface,
    LifecycleTransitionService,
)

# ---------------------------------------------------------------------------
# Transition Matrix Ground Truth
# ---------------------------------------------------------------------------

PERMITTED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
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

ALL_STATES = list(OrderState)

ALL_PERMITTED_PAIRS: list[tuple[OrderState, OrderState]] = [
    (from_state, to_state)
    for from_state, targets in PERMITTED_TRANSITIONS.items()
    for to_state in targets
]

ALL_PROHIBITED_PAIRS: list[tuple[OrderState, OrderState]] = [
    (from_state, to_state)
    for from_state in ALL_STATES
    for to_state in ALL_STATES
    if to_state not in PERMITTED_TRANSITIONS[from_state]
]


# ---------------------------------------------------------------------------
# Simple Mock Order for Duck Typing
# ---------------------------------------------------------------------------


@dataclass
class SimpleOrder:
    status: Any


# ---------------------------------------------------------------------------
# Protocol Conformance
# ---------------------------------------------------------------------------


def test_lifecycle_service_implements_protocol():
    """LifecycleTransitionService satisfies LifecycleTransitionInterface."""
    service = LifecycleTransitionService()
    assert isinstance(service, LifecycleTransitionInterface)


# ---------------------------------------------------------------------------
# Permitted Transitions (11 transitions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_state, to_state", ALL_PERMITTED_PAIRS)
def test_permitted_transition_validation(from_state, to_state):
    """validate_transition succeeds for every permitted transition."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=from_state)

    # validate_transition must succeed without raising
    service.validate_transition(order, to_state)
    # validate_transition must not mutate order
    assert order.status == from_state


@pytest.mark.parametrize("from_state, to_state", ALL_PERMITTED_PAIRS)
def test_permitted_transition_execution(from_state, to_state):
    """transition succeeds, mutates status, and returns the new OrderState."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=from_state)

    result = service.transition(order, to_state)
    assert result == to_state
    assert isinstance(result, OrderState)
    assert order.status == to_state


# ---------------------------------------------------------------------------
# Prohibited Transitions (53 transitions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("from_state, to_state", ALL_PROHIBITED_PAIRS)
def test_prohibited_transition_validation_raises(from_state, to_state):
    """validate_transition raises InvalidOrderTransitionError on prohibited pairs."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=from_state)

    with pytest.raises(InvalidOrderTransitionError) as exc_info:
        service.validate_transition(order, to_state)

    assert issubclass(InvalidOrderTransitionError, ValueError)
    assert exc_info.value.from_state == from_state
    assert exc_info.value.to_state == to_state
    # Order must not be mutated
    assert order.status == from_state


@pytest.mark.parametrize("from_state, to_state", ALL_PROHIBITED_PAIRS)
def test_prohibited_transition_execution_leaves_order_unchanged(from_state, to_state):
    """transition raises InvalidOrderTransitionError and leaves order unchanged."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=from_state)

    with pytest.raises(InvalidOrderTransitionError):
        service.transition(order, to_state)

    assert order.status == from_state


# ---------------------------------------------------------------------------
# Transitions from RECEIVED
# ---------------------------------------------------------------------------


def test_transitions_from_received_allowed():
    """RECEIVED can transition to ALLOCATED, REJECTED, and CANCELLED."""
    service = LifecycleTransitionService()

    order1 = Order(order_id="o1", customer_id="c1")
    assert service.transition(order1, OrderState.ALLOCATED) == OrderState.ALLOCATED
    assert order1.status == OrderState.ALLOCATED

    order2 = Order(order_id="o2", customer_id="c2")
    assert service.transition(order2, OrderState.REJECTED) == OrderState.REJECTED
    assert order2.status == OrderState.REJECTED

    order3 = Order(order_id="o3", customer_id="c3")
    assert service.transition(order3, OrderState.CANCELLED) == OrderState.CANCELLED
    assert order3.status == OrderState.CANCELLED


def test_transitions_from_received_prohibited():
    """RECEIVED cannot transition to PICKING, READY_FOR_DISPATCH, DISPATCHED, DELIVERED, or self."""
    service = LifecycleTransitionService()
    order = Order(order_id="o1", customer_id="c1")

    prohibited_targets = [
        OrderState.RECEIVED,
        OrderState.PICKING,
        OrderState.READY_FOR_DISPATCH,
        OrderState.DISPATCHED,
        OrderState.DELIVERED,
    ]
    for target in prohibited_targets:
        with pytest.raises(InvalidOrderTransitionError):
            service.transition(order, target)
        assert order.status == OrderState.RECEIVED


# ---------------------------------------------------------------------------
# Terminal States Check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_state", [OrderState.REJECTED, OrderState.DELIVERED, OrderState.CANCELLED])
def test_terminal_states_have_no_outward_transitions(terminal_state):
    """Terminal states cannot transition to any other state, including themselves."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=terminal_state)

    for target in ALL_STATES:
        with pytest.raises(InvalidOrderTransitionError, match="terminal state"):
            service.transition(order, target)
        assert order.status == terminal_state


# ---------------------------------------------------------------------------
# Preservation of Other Order Fields
# ---------------------------------------------------------------------------


def test_transition_does_not_modify_other_order_fields():
    """transition only mutates status and preserves all other fields."""
    service = LifecycleTransitionService()
    item = OrderItem(product_id="prod_milk", quantity=2, unit_price=Decimal("50.00"))
    order = Order(
        order_id="ord_preserve",
        customer_id="cust_preserve",
        items=[item],
        substitution_policy=SubstitutionPolicy.SIMILAR_ONLY,
    )

    new_state = service.transition(order, OrderState.ALLOCATED)

    assert new_state == OrderState.ALLOCATED
    assert order.status == OrderState.ALLOCATED
    assert order.order_id == "ord_preserve"
    assert order.customer_id == "cust_preserve"
    assert len(order.items) == 1
    assert order.items[0] is item
    assert order.substitution_policy == SubstitutionPolicy.SIMILAR_ONLY


# ---------------------------------------------------------------------------
# String-to-Enum Coercion
# ---------------------------------------------------------------------------


def test_transition_accepts_valid_string_target():
    """transition accepts string representation of target state."""
    service = LifecycleTransitionService()
    order = Order(order_id="o1", customer_id="c1")

    result = service.transition(order, "ALLOCATED")
    assert result == OrderState.ALLOCATED
    assert isinstance(result, OrderState)
    assert order.status == OrderState.ALLOCATED


def test_transition_accepts_case_insensitive_string_target():
    """transition accepts lowercase string target."""
    service = LifecycleTransitionService()
    order = Order(order_id="o1", customer_id="c1")

    result = service.transition(order, "allocated")
    assert result == OrderState.ALLOCATED
    assert isinstance(result, OrderState)
    assert order.status == OrderState.ALLOCATED


def test_transition_accepts_order_with_string_status():
    """transition accepts an order whose status is a valid string."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status="ALLOCATED")

    result = service.transition(order, OrderState.PICKING)
    assert result == OrderState.PICKING
    assert order.status == OrderState.PICKING


# ---------------------------------------------------------------------------
# Invalid State Handling
# ---------------------------------------------------------------------------


def test_transition_rejects_unknown_target_string():
    """Reject non-existent state strings."""
    service = LifecycleTransitionService()
    order = Order(order_id="o1", customer_id="c1")

    with pytest.raises(InvalidOrderTransitionError, match="Unknown target order state"):
        service.transition(order, "UNKNOWN_STATE")

    assert order.status == OrderState.RECEIVED


def test_transition_rejects_invalid_target_type():
    """Reject non-string, non-enum target values (e.g. numbers, None, bool)."""
    service = LifecycleTransitionService()
    order = Order(order_id="o1", customer_id="c1")

    for bad_target in [123, None, True, False, ["ALLOCATED"]]:
        with pytest.raises(InvalidOrderTransitionError, match="Invalid target state type"):
            service.transition(order, bad_target)
        assert order.status == OrderState.RECEIVED


def test_transition_rejects_order_with_unknown_current_state():
    """Reject order with unrecognized string status."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status="BOGUS_STATUS")

    with pytest.raises(InvalidOrderTransitionError, match="Unknown current order state"):
        service.transition(order, OrderState.ALLOCATED)


def test_transition_rejects_order_with_invalid_current_state_type():
    """Reject order with non-string, non-enum current status."""
    service = LifecycleTransitionService()
    order = SimpleOrder(status=999)

    with pytest.raises(InvalidOrderTransitionError, match="Invalid current state type"):
        service.transition(order, OrderState.ALLOCATED)


def test_transition_rejects_order_missing_status_attribute():
    """Reject object that does not have a status attribute."""
    service = LifecycleTransitionService()

    class NoStatusObject:
        order_id = "o_none"

    with pytest.raises(InvalidOrderTransitionError, match="has no 'status' attribute"):
        service.transition(NoStatusObject(), OrderState.ALLOCATED)
