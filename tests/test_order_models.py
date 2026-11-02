from decimal import Decimal

import pytest

from ps031.models.order import Order, OrderItem, SubstitutionPolicy


def test_order_item_creation():
    item = OrderItem(product_id="prod_milk_1l", quantity=2, unit_price=Decimal("65.00"))

    assert item.product_id == "prod_milk_1l"
    assert item.quantity == 2
    assert item.unit_price == Decimal("65.00")
    assert isinstance(item.unit_price, Decimal)


def test_order_item_converts_numeric_price_to_decimal():
    item = OrderItem(product_id="prod_bread", quantity=1, unit_price=40)

    assert item.unit_price == Decimal("40")
    assert isinstance(item.unit_price, Decimal)


def test_substitution_policy_values():
    assert SubstitutionPolicy.ALLOW == "ALLOW"
    assert SubstitutionPolicy.SIMILAR_ONLY == "SIMILAR_ONLY"
    assert SubstitutionPolicy.NONE == "NONE"
    assert set(SubstitutionPolicy) == {
        SubstitutionPolicy.ALLOW,
        SubstitutionPolicy.SIMILAR_ONLY,
        SubstitutionPolicy.NONE,
    }


def test_order_creation_default_values():
    order = Order(order_id="ord_1001", customer_id="cust_2001")

    assert order.order_id == "ord_1001"
    assert order.customer_id == "cust_2001"
    assert order.items == []
    assert order.substitution_policy == SubstitutionPolicy.NONE


def test_order_creation_explicit_values():
    item = OrderItem(product_id="prod_apples", quantity=4, unit_price=Decimal("30.00"))
    order = Order(
        order_id="ord_1002",
        customer_id="cust_2002",
        items=[item],
        substitution_policy=SubstitutionPolicy.ALLOW,
    )

    assert order.order_id == "ord_1002"
    assert order.customer_id == "cust_2002"
    assert len(order.items) == 1
    assert order.items[0].product_id == "prod_apples"
    assert order.substitution_policy == SubstitutionPolicy.ALLOW


def test_order_accepts_string_substitution_policy():
    order = Order(
        order_id="ord_1003",
        customer_id="cust_2003",
        substitution_policy="SIMILAR_ONLY",
    )

    assert order.substitution_policy == SubstitutionPolicy.SIMILAR_ONLY


def test_separate_orders_do_not_share_default_item_lists():
    order_a = Order(order_id="ord_a", customer_id="cust_a")
    order_b = Order(order_id="ord_b", customer_id="cust_b")

    assert order_a.items is not order_b.items

    item = OrderItem(product_id="prod_chips", quantity=1, unit_price=Decimal("20.00"))
    order_a.items.append(item)

    assert len(order_a.items) == 1
    assert len(order_b.items) == 0
    assert order_b.items == []


def test_order_item_rejects_zero_and_negative_quantity():
    with pytest.raises(ValueError, match="positive integer"):
        OrderItem(product_id="prod_milk", quantity=0, unit_price=Decimal("50.00"))

    with pytest.raises(ValueError, match="positive integer"):
        OrderItem(product_id="prod_milk", quantity=-1, unit_price=Decimal("50.00"))

    with pytest.raises(ValueError, match="positive integer"):
        OrderItem(product_id="prod_milk", quantity=-10, unit_price=Decimal("50.00"))


def test_order_item_rejects_boolean_quantity():
    with pytest.raises(TypeError, match="integer"):
        OrderItem(product_id="prod_milk", quantity=True, unit_price=Decimal("50.00"))

    with pytest.raises(TypeError, match="integer"):
        OrderItem(product_id="prod_milk", quantity=False, unit_price=Decimal("50.00"))


def test_order_item_rejects_string_and_float_quantities():
    with pytest.raises(TypeError, match="integer"):
        OrderItem(product_id="prod_milk", quantity="2", unit_price=Decimal("50.00"))

    with pytest.raises(TypeError, match="integer"):
        OrderItem(product_id="prod_milk", quantity=2.5, unit_price=Decimal("50.00"))


def test_order_item_rejects_negative_unit_price():
    with pytest.raises(ValueError, match="negative"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=Decimal("-10.00"))

    with pytest.raises(ValueError, match="negative"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=-10)

    with pytest.raises(ValueError, match="negative"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price="-0.01")


def test_order_item_rejects_nan_and_infinite_unit_prices():
    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=Decimal("NaN"))

    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=Decimal("Infinity"))

    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=Decimal("-Infinity"))

    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=float("nan"))

    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=float("inf"))

    with pytest.raises(ValueError, match="finite"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price="NaN")


def test_order_item_rejects_boolean_and_malformed_unit_prices():
    with pytest.raises(TypeError, match="boolean"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price=True)

    with pytest.raises(ValueError, match="Invalid unit price"):
        OrderItem(product_id="prod_milk", quantity=1, unit_price="not-a-number")


def test_order_item_accepts_zero_price():
    item = OrderItem(product_id="prod_promo", quantity=1, unit_price=Decimal("0.00"))
    assert item.unit_price == Decimal("0.00")

