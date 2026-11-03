from decimal import Decimal
import pytest

from ps031.models.inventory import InventoryItem
from ps031.models.order import OrderItem, SubstitutionPolicy
from ps031.services.substitution_service import SubstitutionResult, SubstitutionService


@pytest.fixture
def service() -> SubstitutionService:
    return SubstitutionService()


@pytest.fixture
def sample_item() -> OrderItem:
    return OrderItem(
        sku_id="SKU_DAIRY_01",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        requested_qty=5,
    )


def test_allow_substitutes_eligible_candidate_within_15_percent(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: ALLOW permits any SKU in same category with price <= +15%.
    The customer pays the original requested item's unit price for substituted units.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DAIRY_02",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",  # Different brand is permitted under ALLOW
            price=Decimal("110.00"),  # +10% price (more expensive)
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id == "SKU_DAIRY_02"
    assert result.fulfilled_qty == 5
    assert result.shortfall_qty == 0
    # Customer pays original requested item's unit price (Rs 100.00)
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_boundary_candidate_priced_exactly_15_percent_above_original(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    EC-04: Substitute candidate priced exactly 15% above original.
    Candidate must be accepted and customer charged original requested item's unit price.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DAIRY_EXACT15",
            name="Mother Dairy Milk",
            category="Dairy",
            brand="Mother Dairy",
            price=Decimal("115.00"),  # Exactly +15.00%
            stock=5,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id == "SKU_DAIRY_EXACT15"
    assert result.fulfilled_qty == 5
    assert result.shortfall_qty == 0
    assert result.charged_unit_price == Decimal("100.00")


def test_cheaper_substitute_charged_at_original_unit_price(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: The customer pays the original requested item's unit price for substituted units,
    including when the eligible substitute candidate is cheaper.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_CHEAPER",
            name="Budget Milk",
            category="Dairy",
            brand="BudgetBrand",
            price=Decimal("85.00"),  # Cheaper candidate: Rs 85.00 vs original Rs 100.00
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id == "SKU_CHEAPER"
    assert result.fulfilled_qty == 5
    assert result.shortfall_qty == 0
    # Customer pays original requested item's unit price (Rs 100.00)
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_rejects_candidate_priced_above_15_percent(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: Candidates priced strictly above +15% are rejected.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DAIRY_TOO_EXPENSIVE",
            name="Organic Cow Milk",
            category="Dairy",
            brand="Amul",
            price=Decimal("115.01"),  # +15.01%, above threshold
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id is None
    assert result.fulfilled_qty == 0
    assert result.shortfall_qty == 5
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_rejects_candidate_in_different_category(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: Candidate must belong to the same category.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_BEV_01",
            name="Almond Drink",
            category="Beverages",  # Wrong category
            brand="Amul",
            price=Decimal("105.00"),
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id is None
    assert result.fulfilled_qty == 0
    assert result.shortfall_qty == 5


def test_similar_only_requires_same_brand(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: SIMILAR_ONLY permits only the same brand within +15% price in category.
    Customer pays original unit price.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DIFF_BRAND",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",  # Different brand
            price=Decimal("105.00"),
            stock=10,
        ),
        InventoryItem(
            sku_id="SKU_SAME_BRAND",
            name="Amul Taza",
            category="Dairy",
            brand="Amul",  # Same brand
            price=Decimal("108.00"),
            stock=10,
        ),
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.SIMILAR_ONLY,
    )

    assert result.substituted_sku_id == "SKU_SAME_BRAND"
    assert result.fulfilled_qty == 5
    assert result.shortfall_qty == 0
    assert result.charged_unit_price == Decimal("100.00")


def test_similar_only_rejects_when_no_same_brand_available(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: SIMILAR_ONLY rejects valid category candidates if brand differs.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DIFF_BRAND",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("105.00"),
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.SIMILAR_ONLY,
    )

    assert result.substituted_sku_id is None
    assert result.fulfilled_qty == 0
    assert result.shortfall_qty == 5


def test_none_preference_does_not_substitute(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: NONE converts the shortfall into a partial line (no substitution).
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_PERFECT_MATCH",
            name="Amul Taza",
            category="Dairy",
            brand="Amul",
            price=Decimal("100.00"),
            stock=10,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.NONE,
    )

    assert result.substituted_sku_id is None
    assert result.fulfilled_qty == 0
    assert result.shortfall_qty == 5
    assert result.charged_unit_price == Decimal("100.00")


def test_inventory_limits_fulfillment_and_records_shortfall(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: Do not fulfill more units than available inventory permits.
    Shortfall must be recorded.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_LOW_STOCK",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("102.00"),
            stock=3,  # Only 3 in stock, but requested 5
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id == "SKU_LOW_STOCK"
    assert result.fulfilled_qty == 3
    assert result.shortfall_qty == 2
    assert result.charged_unit_price == Decimal("100.00")


def test_candidate_with_zero_stock_is_skipped(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Candidates with zero stock cannot be selected.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_OUT_OF_STOCK",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("102.00"),
            stock=0,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id is None
    assert result.fulfilled_qty == 0
    assert result.shortfall_qty == 5


def test_oq04_deterministic_selection_among_multiple_eligible_candidates(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    OQ-04: When multiple candidates qualify, selection is deterministic:
    Candidate with minimal price delta is prioritized.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_FAR_PRICE",
            name="Dairy Far",
            category="Dairy",
            brand="BrandA",
            price=Decimal("114.00"),  # delta +14.00
            stock=10,
        ),
        InventoryItem(
            sku_id="SKU_CLOSE_PRICE",
            name="Dairy Close",
            category="Dairy",
            brand="BrandB",
            price=Decimal("102.00"),  # delta +2.00
            stock=10,
        ),
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_sku_id == "SKU_CLOSE_PRICE"


def test_apply_substitution_updates_order_item_and_fulfilled_line_value(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Verifies that apply_substitution mutates OrderItem fields correctly
    and OrderItem.fulfilled_line_value uses the customer-charged price.
    """
    inventory = [
        InventoryItem(
            sku_id="SKU_DAIRY_02",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("110.00"),  # Substitute price is Rs 110.00
            stock=4,
        )
    ]

    result = service.apply_substitution(
        item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.fulfilled_qty == 4
    assert sample_item.substituted_sku_id == "SKU_DAIRY_02"
    assert sample_item.fulfilled_qty == 4
    # Customer pays original requested item's unit price (Rs 100.00)
    assert sample_item.charged_unit_price == Decimal("100.00")
    # Line value: 4 * 100.00 = 400.00
    assert sample_item.fulfilled_line_value == Decimal("400.00")


def test_negative_price_or_stock_raises_value_error():
    """
    Validates that negative unit price or stock raises ValueError.
    """
    with pytest.raises(ValueError, match="unit_price cannot be negative"):
        OrderItem(
            sku_id="SKU_NEG",
            category="Dairy",
            brand="Amul",
            unit_price=Decimal("-10.00"),
            requested_qty=2,
        )

    with pytest.raises(ValueError, match="price cannot be negative"):
        InventoryItem(
            sku_id="INV_NEG",
            name="Bad Item",
            category="Dairy",
            brand="Amul",
            price=Decimal("-5.00"),
            stock=5,
        )

    with pytest.raises(ValueError, match="stock cannot be negative"):
        InventoryItem(
            sku_id="INV_STOCK_NEG",
            name="Bad Stock",
            category="Dairy",
            brand="Amul",
            price=Decimal("50.00"),
            stock=-2,
        )
