from decimal import Decimal
import pytest

from ps031.models.product import Product


def test_product_valid_construction():
    """Verify valid product creation with explicit Decimal price."""
    p = Product(
        sku="SKU-MILK-001",
        name="Toned Milk 1L",
        category="Dairy",
        brand="Amul",
        price=Decimal("54.00"),
    )
    assert p.sku == "SKU-MILK-001"
    assert p.name == "Toned Milk 1L"
    assert p.category == "Dairy"
    assert p.brand == "Amul"
    assert p.price == Decimal("54.00")
    assert isinstance(p.price, Decimal)


def test_product_price_conversions():
    """Verify numeric and string values convert to Decimal."""
    p_int = Product("SKU-1", "A", "C", "B", 50)
    assert p_int.price == Decimal("50")

    p_str = Product("SKU-2", "A", "C", "B", "45.50")
    assert p_str.price == Decimal("45.50")

    p_zero = Product("SKU-3", "A", "C", "B", 0)
    assert p_zero.price == Decimal("0")


@pytest.mark.parametrize(
    "field_kwargs",
    [
        {"sku": ""},
        {"sku": "   "},
        {"name": ""},
        {"name": "   "},
        {"category": ""},
        {"category": "   "},
        {"brand": ""},
        {"brand": "   "},
    ],
)
def test_product_rejects_empty_required_strings(field_kwargs):
    """Verify empty or whitespace-only required strings raise ValueError."""
    base_kwargs = {
        "sku": "SKU-TEST",
        "name": "Test Item",
        "category": "Snacks",
        "brand": "Lays",
        "price": Decimal("20.00"),
    }
    base_kwargs.update(field_kwargs)
    with pytest.raises(ValueError, match="must not be empty"):
        Product(**base_kwargs)


@pytest.mark.parametrize(
    "field_kwargs",
    [
        {"sku": 123},
        {"sku": True},
        {"name": None},
        {"category": ["Dairy"]},
        {"brand": False},
    ],
)
def test_product_rejects_invalid_string_types(field_kwargs):
    """Verify non-string types raise TypeError."""
    base_kwargs = {
        "sku": "SKU-TEST",
        "name": "Test Item",
        "category": "Snacks",
        "brand": "Lays",
        "price": Decimal("20.00"),
    }
    base_kwargs.update(field_kwargs)
    with pytest.raises(TypeError, match="must be a string"):
        Product(**base_kwargs)


def test_product_rejects_negative_price():
    """Verify negative price raises ValueError."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Product("SKU-1", "Item", "Cat", "Brand", Decimal("-0.01"))
    with pytest.raises(ValueError, match="cannot be negative"):
        Product("SKU-1", "Item", "Cat", "Brand", -10)


def test_product_rejects_boolean_price():
    """Verify boolean price raises TypeError."""
    with pytest.raises(TypeError, match="cannot be a boolean"):
        Product("SKU-1", "Item", "Cat", "Brand", True)
    with pytest.raises(TypeError, match="cannot be a boolean"):
        Product("SKU-1", "Item", "Cat", "Brand", False)


def test_product_rejects_non_finite_price():
    """Verify NaN and Infinity raise ValueError."""
    for bad in [Decimal("NaN"), Decimal("Infinity"), float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="finite"):
            Product("SKU-1", "Item", "Cat", "Brand", bad)


def test_product_rejects_malformed_price():
    """Verify unparseable price string raises ValueError."""
    with pytest.raises(ValueError, match="Invalid price"):
        Product("SKU-1", "Item", "Cat", "Brand", "not-a-number")
