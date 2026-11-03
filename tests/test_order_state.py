from enum import Enum
from decimal import Decimal
import pytest

from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import InvalidOrderTransitionError, OrderState


def test_order_state_enum_members():
    """Verify all 8 defined states exist."""
    expected_states = {
        "RECEIVED",
        "REJECTED",
        "ALLOCATED",
        "PICKING",
        "READY_FOR_DISPATCH",
        "DISPATCHED",
        "DELIVERED",
        "CANCELLED",
    }
    actual_states = {state.value for state in OrderState}
    assert actual_states == expected_states
    assert len(OrderState) == 8


def test_order_state_is_str_subclass():
    """Verify OrderState members are strings and Enums."""
    assert issubclass(OrderState, str)
    assert issubclass(OrderState, Enum)
    assert isinstance(OrderState.RECEIVED, str)
    assert OrderState.RECEIVED == "RECEIVED"
    assert OrderState.ALLOCATED == "ALLOCATED"
    assert OrderState.PICKING == "PICKING"
    assert OrderState.READY_FOR_DISPATCH == "READY_FOR_DISPATCH"
    assert OrderState.DISPATCHED == "DISPATCHED"
    assert OrderState.DELIVERED == "DELIVERED"
    assert OrderState.CANCELLED == "CANCELLED"
    assert OrderState.REJECTED == "REJECTED"


def test_order_state_from_valid_string():
    """Lookup state by exact string value."""
    assert OrderState("RECEIVED") is OrderState.RECEIVED
    assert OrderState("DELIVERED") is OrderState.DELIVERED


def test_order_state_from_invalid_string():
    """Lookup with invalid string raises ValueError."""
    with pytest.raises(ValueError):
        OrderState("NON_EXISTENT_STATE")


def test_invalid_order_transition_error_inheritance():
    """InvalidOrderTransitionError must inherit from ValueError."""
    assert issubclass(InvalidOrderTransitionError, ValueError)

    err = InvalidOrderTransitionError("Custom transition failure")
    assert isinstance(err, ValueError)
    assert str(err) == "Custom transition failure"


def test_invalid_order_transition_error_attributes():
    """InvalidOrderTransitionError holds from_state and to_state attributes."""
    err = InvalidOrderTransitionError(
        from_state=OrderState.RECEIVED,
        to_state=OrderState.DELIVERED,
    )
    assert err.from_state == OrderState.RECEIVED
    assert err.to_state == OrderState.DELIVERED
    assert "Cannot transition order from RECEIVED to DELIVERED" in str(err)


def test_order_default_status_is_received():
    """A newly instantiated Order must default to OrderState.RECEIVED."""
    order = Order(order_id="ord_101", customer_id="cust_101")
    assert order.status == OrderState.RECEIVED
    assert isinstance(order.status, OrderState)


def test_order_accepts_status_as_string():
    """Order coerces a valid string status into an OrderState enum."""
    order = Order(
        order_id="ord_102",
        customer_id="cust_102",
        status="ALLOCATED",
    )
    assert order.status == OrderState.ALLOCATED
    assert isinstance(order.status, OrderState)


def test_order_rejects_invalid_string_status():
    """Order raises ValueError when initialized with an invalid status string."""
    with pytest.raises(ValueError):
        Order(order_id="ord_103", customer_id="cust_103", status="INVALID_STATUS")
