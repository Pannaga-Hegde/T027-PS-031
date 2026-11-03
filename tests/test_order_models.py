from datetime import datetime, timezone
from decimal import Decimal
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState


# ---------------------------------------------------------------------------
# OrderItem Existing Tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# OrderItem Extended Fields and Properties Tests
# ---------------------------------------------------------------------------


def test_order_item_extended_defaults():
    """Verify default values for fulfilled_quantity, substitution, and charged price."""
    item = OrderItem(product_id="prod_rice", quantity=5, unit_price=Decimal("60.00"))

    assert item.fulfilled_quantity == 0
    assert item.substituted_product_sku is None
    assert item.substituted_quantity == 0
    assert item.charged_unit_price == Decimal("60.00")
    assert item.original_fulfilled_quantity == 0
    assert item.shortfall_quantity == 5


def test_order_item_partial_fulfilment_properties():
    """Verify original_fulfilled_quantity and shortfall_quantity calculations."""
    # Partially fulfilled without substitution
    item = OrderItem(
        product_id="prod_rice",
        quantity=5,
        unit_price=Decimal("60.00"),
        fulfilled_quantity=3,
    )
    assert item.original_fulfilled_quantity == 3
    assert item.shortfall_quantity == 2

    # Fully fulfilled with 2 units substituted
    item_sub = OrderItem(
        product_id="prod_rice",
        quantity=5,
        unit_price=Decimal("60.00"),
        fulfilled_quantity=5,
        substituted_product_sku="prod_rice_premium",
        substituted_quantity=2,
    )
    assert item_sub.original_fulfilled_quantity == 3
    assert item_sub.shortfall_quantity == 0


def test_order_item_charged_unit_price_explicit_and_coerced():
    """Charged unit price accepts explicit conversion matching unit_price."""
    item = OrderItem(
        product_id="prod_rice",
        quantity=2,
        unit_price=Decimal("60.00"),
        charged_unit_price="60.00",
    )
    assert item.charged_unit_price == Decimal("60.00")
    assert isinstance(item.charged_unit_price, Decimal)


def test_order_item_rejects_unequal_charged_unit_price():
    """Under BR-05, charged unit price cannot be higher or lower than unit_price."""
    with pytest.raises(ValueError, match="must equal original unit price"):
        OrderItem("p1", 2, Decimal("60.00"), charged_unit_price="55.00")

    with pytest.raises(ValueError, match="must equal original unit price"):
        OrderItem("p1", 2, Decimal("60.00"), charged_unit_price=Decimal("70.00"))


def test_order_item_rejects_empty_product_id():
    """Product ID must not be empty or whitespace."""
    with pytest.raises(ValueError, match="Product ID must not be empty"):
        OrderItem(product_id="", quantity=1, unit_price=10)

    with pytest.raises(ValueError, match="Product ID must not be empty"):
        OrderItem(product_id="   ", quantity=1, unit_price=10)


def test_order_item_rejects_invalid_fulfilled_quantity():
    """Fulfilled quantity cannot be negative or exceed requested quantity."""
    with pytest.raises(ValueError, match="Fulfilled quantity must be between 0 and 5"):
        OrderItem("p1", 5, 20, fulfilled_quantity=-1)

    with pytest.raises(ValueError, match="Fulfilled quantity must be between 0 and 5"):
        OrderItem("p1", 5, 20, fulfilled_quantity=6)

    with pytest.raises(TypeError, match="integer"):
        OrderItem("p1", 5, 20, fulfilled_quantity=True)


def test_order_item_rejects_invalid_substituted_quantity():
    """Substituted quantity cannot be negative or exceed fulfilled quantity."""
    with pytest.raises(ValueError, match="Substituted quantity must be between 0 and 3"):
        OrderItem("p1", 5, 20, fulfilled_quantity=3, substituted_quantity=-1)

    with pytest.raises(ValueError, match="Substituted quantity must be between 0 and 3"):
        OrderItem("p1", 5, 20, fulfilled_quantity=3, substituted_quantity=4)

    with pytest.raises(TypeError, match="integer"):
        OrderItem("p1", 5, 20, fulfilled_quantity=3, substituted_quantity=False)


def test_order_item_requires_substitute_sku_when_substituted_quantity_positive():
    """Substitute SKU is required when substituted_quantity > 0."""
    with pytest.raises(ValueError, match="A substitute SKU is required"):
        OrderItem(
            "p1",
            5,
            20,
            fulfilled_quantity=2,
            substituted_quantity=2,
            substituted_product_sku=None,
        )

    with pytest.raises(ValueError, match="A substitute SKU is required"):
        OrderItem(
            "p1",
            5,
            20,
            fulfilled_quantity=2,
            substituted_quantity=2,
            substituted_product_sku="",
        )


def test_order_item_requires_substitute_sku_none_when_substituted_quantity_zero():
    """Substitute SKU must be None when substituted_quantity is zero."""
    with pytest.raises(ValueError, match="Substitute SKU must be None when substituted quantity is zero"):
        OrderItem(
            "p1",
            5,
            20,
            fulfilled_quantity=2,
            substituted_quantity=0,
            substituted_product_sku="sub_sku",
        )


def test_order_item_rejects_substitute_sku_same_as_product_id():
    """Substitute SKU cannot be identical to the original product_id."""
    with pytest.raises(ValueError, match="cannot be the same as the original product ID"):
        OrderItem(
            "prod_same",
            5,
            20,
            fulfilled_quantity=2,
            substituted_quantity=2,
            substituted_product_sku="prod_same",
        )


def test_order_item_rejects_invalid_charged_unit_price():
    """Charged unit price rejects negative, bool, and non-finite values."""
    with pytest.raises(ValueError, match="cannot be negative"):
        OrderItem("p1", 1, 20, charged_unit_price=-5)

    with pytest.raises(TypeError, match="cannot be a boolean"):
        OrderItem("p1", 1, 20, charged_unit_price=True)

    with pytest.raises(ValueError, match="finite"):
        OrderItem("p1", 1, 20, charged_unit_price=float("inf"))


# ---------------------------------------------------------------------------
# Order Extended Operational Fields Tests
# ---------------------------------------------------------------------------


def test_order_extended_default_values():
    """Verify all added operational fields have correct defaults."""
    order = Order(order_id="ord_test", customer_id="cust_test")

    assert order.delivery_location is None
    assert order.order_time is None
    assert order.status == OrderState.RECEIVED
    assert order.allocated_store_id is None
    assert order.promised_eta is None
    assert order.delayed_promise is False
    assert order.slow_delivery is False
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.actual_delivery_time is None


def test_order_extended_explicit_values():
    """Verify Order construction with all operational fields specified."""
    loc = Coordinates(latitude=12.9352, longitude=77.6245)
    ot = datetime(2026, 11, 3, 10, 0)
    eta = datetime(2026, 11, 3, 10, 10)
    deliv = datetime(2026, 11, 3, 10, 12)

    order = Order(
        order_id="ord_full",
        customer_id="cust_full",
        items=[],
        substitution_policy=SubstitutionPolicy.ALLOW,
        delivery_location=loc,
        order_time=ot,
        status=OrderState.ALLOCATED,
        allocated_store_id="store_south",
        promised_eta=eta,
        delayed_promise=True,
        slow_delivery=False,
        delivery_fee=Decimal("25.00"),
        surge_fee=Decimal("15.00"),
        actual_delivery_time=deliv,
    )

    assert order.delivery_location is loc
    assert order.order_time == ot
    assert order.status == OrderState.ALLOCATED
    assert order.allocated_store_id == "store_south"
    assert order.promised_eta == eta
    assert order.delayed_promise is True
    assert order.slow_delivery is False
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("15.00")
    assert order.actual_delivery_time == deliv


def test_order_fee_numeric_coercion():
    """delivery_fee and surge_fee coerce numbers and strings to Decimal."""
    order = Order(
        order_id="ord_fee",
        customer_id="cust_fee",
        delivery_fee=25,
        surge_fee="15.00",
    )
    assert order.delivery_fee == Decimal("25")
    assert order.surge_fee == Decimal("15.00")
    assert isinstance(order.delivery_fee, Decimal)
    assert isinstance(order.surge_fee, Decimal)


@pytest.mark.parametrize(
    "id_kwargs",
    [
        {"order_id": ""},
        {"order_id": "   "},
        {"customer_id": ""},
        {"customer_id": "   "},
    ],
)
def test_order_rejects_empty_identifiers(id_kwargs):
    """Order rejects empty order_id or customer_id."""
    kwargs = {"order_id": "o1", "customer_id": "c1"}
    kwargs.update(id_kwargs)
    with pytest.raises(ValueError, match="must not be empty"):
        Order(**kwargs)


def test_order_rejects_non_order_item_elements():
    """Items list containing non-OrderItem objects raises TypeError."""
    with pytest.raises(TypeError, match="OrderItem instances"):
        Order(order_id="o1", customer_id="c1", items=["not_an_item"])


def test_order_rejects_invalid_location_type():
    """delivery_location must be Coordinates or None."""
    with pytest.raises(TypeError, match="Coordinates"):
        Order(order_id="o1", customer_id="c1", delivery_location="12.93, 77.62")


@pytest.mark.parametrize("bad_time", ["2026-11-03 10:00:00", 12345, True])
def test_order_rejects_invalid_datetime_types(bad_time):
    """Datetime fields reject non-datetime objects."""
    with pytest.raises(TypeError, match="datetime"):
        Order(order_id="o1", customer_id="c1", order_time=bad_time)

    with pytest.raises(TypeError, match="datetime"):
        Order(order_id="o1", customer_id="c1", promised_eta=bad_time)

    with pytest.raises(TypeError, match="datetime"):
        Order(order_id="o1", customer_id="c1", actual_delivery_time=bad_time)


def test_order_rejects_mixed_timezone_awareness_timestamps():
    """Order rejects mixing timezone-aware and timezone-naive timestamps."""
    naive = datetime(2026, 11, 3, 10, 0)
    aware = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(TypeError, match="timezone awareness"):
        Order(order_id="o1", customer_id="c1", order_time=naive, promised_eta=aware)


@pytest.mark.parametrize("bad_bool", [1, 0, "True", "False", None])
def test_order_rejects_non_boolean_flags(bad_bool):
    """delayed_promise and slow_delivery strictly require bool."""
    with pytest.raises(TypeError, match="boolean"):
        Order(order_id="o1", customer_id="c1", delayed_promise=bad_bool)

    with pytest.raises(TypeError, match="boolean"):
        Order(order_id="o1", customer_id="c1", slow_delivery=bad_bool)

    with pytest.raises(TypeError, match="boolean"):
        Order(order_id="o1", customer_id="c1", pricing_frozen=bad_bool)


def test_order_rejects_invalid_fees():
    """Monetary fees reject negative, bool, and non-finite values."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Order(order_id="o1", customer_id="c1", delivery_fee=-10)

    with pytest.raises(TypeError, match="cannot be a boolean"):
        Order(order_id="o1", customer_id="c1", surge_fee=True)

    with pytest.raises(ValueError, match="finite"):
        Order(order_id="o1", customer_id="c1", delivery_fee=float("inf"))


def test_order_rejects_empty_allocated_store_id():
    """allocated_store_id when provided must not be empty."""
    with pytest.raises(ValueError, match="allocated_store_id must not be empty"):
        Order(order_id="o1", customer_id="c1", allocated_store_id="")


def test_order_pricing_frozen_default_and_explicit():
    order_default = Order(order_id="o_def", customer_id="c1")
    assert order_default.pricing_frozen is False

    order_frozen = Order(order_id="o_froz", customer_id="c1", pricing_frozen=True)
    assert order_frozen.pricing_frozen is True


def test_order_pricing_frozen_preserved_across_dataclass_reconstruction():
    from dataclasses import asdict

    order = Order(
        order_id="o_recon",
        customer_id="c1",
        delivery_fee=Decimal("25.00"),
        surge_fee=Decimal("15.00"),
        pricing_frozen=True,
    )
    data = asdict(order)
    assert data["pricing_frozen"] is True

    reconstructed = Order(**data)
    assert reconstructed.pricing_frozen is True
    assert reconstructed.delivery_fee == Decimal("25.00")
    assert reconstructed.surge_fee == Decimal("15.00")
