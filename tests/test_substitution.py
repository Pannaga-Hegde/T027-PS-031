from datetime import time
from decimal import Decimal
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.inventory import InventoryItem
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import OrderItem, SubstitutionPolicy
from ps031.models.product import Product
from ps031.models.store import Store
from ps031.services.inventory_service import InventoryService, ReservationResult
from ps031.services.substitution_service import SubstitutionResult, SubstitutionService


@pytest.fixture
def service() -> SubstitutionService:
    return SubstitutionService()


@pytest.fixture
def sample_item() -> OrderItem:
    return OrderItem(
        product_id="SKU_DAIRY_01",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
    )


# ---------------------------------------------------------------------------
# BR-05 Core Policy Tests: ALLOW
# ---------------------------------------------------------------------------


def test_allow_substitutes_eligible_candidate_within_15_percent(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: ALLOW permits any SKU in same category with price <= +15%.
    The customer pays the original requested item's unit price for substituted units.
    """
    inventory = [
        Product(
            sku="SKU_DAIRY_02",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",  # Different brand permitted under ALLOW
            price=Decimal("110.00"),  # +10% price
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku == "SKU_DAIRY_02"
    assert result.substituted_sku_id == "SKU_DAIRY_02"
    assert result.fulfilled_quantity == 5
    assert result.fulfilled_qty == 5
    assert result.shortfall_quantity == 0
    assert result.shortfall_qty == 0
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_boundary_candidate_priced_exactly_15_percent_above_original(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    EC-04 / Boundary: Candidate priced exactly 15% above original is eligible under ALLOW.
    Candidate must be accepted and customer charged original requested item's unit price.
    """
    inventory = [
        Product(
            sku="SKU_DAIRY_EXACT15",
            name="Mother Dairy Milk",
            category="Dairy",
            brand="Mother Dairy",
            price=Decimal("115.00"),  # Exactly +15.00%
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku == "SKU_DAIRY_EXACT15"
    assert result.fulfilled_quantity == 5
    assert result.shortfall_quantity == 0
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_rejects_candidate_priced_above_15_percent(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: Candidates priced strictly above +15% are rejected under ALLOW.
    """
    inventory = [
        Product(
            sku="SKU_DAIRY_TOO_EXPENSIVE",
            name="Organic Cow Milk",
            category="Dairy",
            brand="Amul",
            price=Decimal("115.01"),  # +15.01%, above threshold
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5
    assert result.charged_unit_price == Decimal("100.00")


def test_allow_rejects_candidate_in_different_category(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: ALLOW rejects candidates belonging to a different category.
    """
    inventory = [
        Product(
            sku="SKU_BEV_01",
            name="Almond Drink",
            category="Beverages",  # Wrong category
            brand="Amul",
            price=Decimal("105.00"),
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5


# ---------------------------------------------------------------------------
# BR-05 Core Policy Tests: SIMILAR_ONLY
# ---------------------------------------------------------------------------


def test_similar_only_accepts_same_brand_even_if_category_differs(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: SIMILAR_ONLY permits only a different SKU from the same brand.
    Does not additionally require the same category.
    """
    inventory = [
        Product(
            sku="SKU_AMUL_CHEESE_SPREAD",
            name="Amul Cheese Spread",
            category="Snacks",  # Different category from Dairy
            brand="Amul",  # Same brand
            price=Decimal("110.00"),
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.SIMILAR_ONLY,
    )

    assert result.substituted_product_sku == "SKU_AMUL_CHEESE_SPREAD"
    assert result.fulfilled_quantity == 5
    assert result.shortfall_quantity == 0
    assert result.charged_unit_price == Decimal("100.00")


def test_similar_only_accepts_same_brand_even_if_price_exceeds_15_percent(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: SIMILAR_ONLY permits only the same brand.
    Does not additionally require the +15% price ceiling.
    Customer still pays the original requested item's unit price.
    """
    inventory = [
        Product(
            sku="SKU_AMUL_PREMIUM_GHEE",
            name="Amul Premium Ghee",
            category="Dairy",
            brand="Amul",  # Same brand
            price=Decimal("250.00"),  # +150% above original Rs 100.00
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.SIMILAR_ONLY,
    )

    assert result.substituted_product_sku == "SKU_AMUL_PREMIUM_GHEE"
    assert result.fulfilled_quantity == 5
    assert result.shortfall_quantity == 0
    # Customer pays original unit price (Rs 100.00) even for expensive substitute
    assert result.charged_unit_price == Decimal("100.00")


def test_similar_only_rejects_different_brand(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: SIMILAR_ONLY strictly rejects candidates from different brands,
    even if category matches and price is identical.
    """
    inventory = [
        Product(
            sku="SKU_NANDINI_MILK",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",  # Different brand
            price=Decimal("100.00"),
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.SIMILAR_ONLY,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5


# ---------------------------------------------------------------------------
# BR-05 Core Policy Tests: NONE
# ---------------------------------------------------------------------------


def test_none_preference_does_not_substitute(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    BR-05: NONE converts the shortfall into a partial line (no substitution).
    """
    inventory = [
        Product(
            sku="SKU_PERFECT_MATCH",
            name="Amul Taza",
            category="Dairy",
            brand="Amul",
            price=Decimal("100.00"),
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.NONE,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5
    assert result.charged_unit_price == Decimal("100.00")


# ---------------------------------------------------------------------------
# Candidate Eligibility & Stock Checks
# ---------------------------------------------------------------------------


def test_original_sku_never_selected_as_own_substitute(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Candidate SKU cannot be the same as the original product ID.
    """
    inventory = [
        Product(
            sku="SKU_DAIRY_01",  # Same as sample_item.product_id
            name="Original Milk",
            category="Dairy",
            brand="Amul",
            price=Decimal("100.00"),
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5


def test_zero_stock_candidate_not_selected(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Candidates with zero available stock cannot be selected.
    """
    inventory = [
        Product(
            sku="SKU_OUT_OF_STOCK",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("102.00"),
        )
    ]
    stock_by_sku = {"SKU_OUT_OF_STOCK": 0}

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
        stock_by_sku=stock_by_sku,
    )

    assert result.substituted_product_sku is None
    assert result.fulfilled_quantity == 0
    assert result.shortfall_quantity == 5


def test_deterministic_tie_breaking(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Tie-break order:
    1. Smallest absolute price difference from original
    2. Lower candidate price
    3. Lexicographically smaller SKU
    """
    # 1. Price difference priority
    inv1 = [
        Product(
            sku="SKU_FAR_PRICE",
            name="Far",
            category="Dairy",
            brand="BrandA",
            price=Decimal("114.00"),  # delta +14.00
        ),
        Product(
            sku="SKU_CLOSE_PRICE",
            name="Close",
            category="Dairy",
            brand="BrandB",
            price=Decimal("102.00"),  # delta +2.00
        ),
    ]
    assert (
        service.select_candidate(inv1, sample_item).sku == "SKU_CLOSE_PRICE"
    )

    # 2. Lower candidate price priority when absolute delta ties
    inv2 = [
        Product(
            sku="SKU_HIGHER_PRICE",
            name="Higher",
            category="Dairy",
            brand="BrandA",
            price=Decimal("105.00"),  # delta 5.00
        ),
        Product(
            sku="SKU_LOWER_PRICE",
            name="Lower",
            category="Dairy",
            brand="BrandB",
            price=Decimal("95.00"),  # delta 5.00, lower price
        ),
    ]
    assert (
        service.select_candidate(inv2, sample_item).sku == "SKU_LOWER_PRICE"
    )

    # 3. Lexicographical SKU sort when price and delta tie
    inv3 = [
        Product(
            sku="SKU_Z_PRODUCT",
            name="Zebra",
            category="Dairy",
            brand="BrandA",
            price=Decimal("105.00"),
        ),
        Product(
            sku="SKU_A_PRODUCT",
            name="Apple",
            category="Dairy",
            brand="BrandB",
            price=Decimal("105.00"),
        ),
    ]
    assert service.select_candidate(inv3, sample_item).sku == "SKU_A_PRODUCT"


# ---------------------------------------------------------------------------
# Order-Line Updates & Fulfilment Accumulation
# ---------------------------------------------------------------------------


def test_original_fulfilled_quantity_preserved(
    service: SubstitutionService,
):
    """
    Units already fulfilled with original product are preserved when substitute units are added.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=3,  # 3 original units picked
    )
    assert item.original_fulfilled_quantity == 3
    assert item.shortfall_quantity == 2

    inventory = [
        Product(
            sku="SKU_NANDINI",
            name="Nandini Milk",
            category="Dairy",
            brand="Nandini",
            price=Decimal("105.00"),
        )
    ]
    stock_by_sku = {"SKU_NANDINI": 2}

    result = service.apply_substitution(
        item=item,
        available_inventory=inventory,
        preference=SubstitutionPolicy.ALLOW,
        stock_by_sku=stock_by_sku,
    )

    assert result.fulfilled_quantity == 5
    assert result.shortfall_quantity == 0
    assert item.fulfilled_quantity == 5
    assert item.substituted_product_sku == "SKU_NANDINI"
    assert item.substituted_quantity == 2
    # Crucial assertion: original fulfilled units remain exactly 3!
    assert item.original_fulfilled_quantity == 3
    assert item.charged_unit_price == Decimal("100.00")
    assert item.fulfilled_line_value == Decimal("500.00")


def test_existing_fulfilled_quantity_accumulated_correctly(
    service: SubstitutionService,
):
    """
    Substituted units are accumulated onto existing fulfilled quantity.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=10,
        fulfilled_quantity=4,  # 4 original units
    )

    # First substitution: 3 units of SKU_SUB
    service.apply_substitution(
        item=item,
        substitute_product="SKU_SUB",
        reserved_quantity=3,
    )
    assert item.fulfilled_quantity == 7
    assert item.substituted_quantity == 3
    assert item.original_fulfilled_quantity == 4

    # Second substitution of the SAME substitute SKU: 2 more units
    service.apply_substitution(
        item=item,
        substitute_product="SKU_SUB",
        reserved_quantity=2,
    )
    assert item.fulfilled_quantity == 9
    assert item.substituted_quantity == 5
    assert item.original_fulfilled_quantity == 4
    assert item.shortfall_quantity == 1


def test_no_eligible_substitute_does_not_erase_existing_fulfilment(
    service: SubstitutionService,
):
    """
    When no eligible substitute candidate is found, existing fulfilled quantity is not erased.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=3,  # 3 original units already fulfilled
    )

    # Empty candidate inventory
    result = service.apply_substitution(
        item=item,
        available_inventory=[],
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku is None
    # Crucial assertion: fulfilled_quantity is NOT wiped out to 0!
    assert item.fulfilled_quantity == 3
    assert item.substituted_quantity == 0
    assert item.original_fulfilled_quantity == 3
    assert item.shortfall_quantity == 2


def test_partial_reservation_only_adds_actual_reserved_quantity(
    service: SubstitutionService,
):
    """
    Only the actually reserved quantity is added to fulfilled quantity.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=2,  # shortfall is 3
    )

    # Reservation returned only 1 unit out of 3 requested
    result = service.apply_substitution(
        item=item,
        substitute_product="SKU_SUB_PARTIAL",
        reserved_quantity=1,
    )

    assert result.fulfilled_quantity == 3
    assert item.fulfilled_quantity == 3
    assert item.substituted_quantity == 1
    assert item.original_fulfilled_quantity == 2
    assert item.shortfall_quantity == 2


def test_over_reservation_is_rejected_without_mutating_order_line(
    service: SubstitutionService,
):
    """Excess reserved stock must be rejected, not silently capped."""
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=4,
    )

    with pytest.raises(ValueError, match="Excess reservation must be released"):
        service.apply_substitution(
            item=item,
            substitute_product="SKU_SUB_OVER",
            reserved_quantity=10,
        )

    # Rejection must leave the order line unchanged.
    assert item.fulfilled_quantity == 4
    assert item.substituted_quantity == 0
    assert item.substituted_product_sku is None
    assert item.charged_unit_price == Decimal("100.00")

def test_customer_charge_remains_original_unit_price(
    service: SubstitutionService,
):
    """
    Under BR-05, customer is charged the original requested item's unit price,
    both when substitute is cheaper and when substitute is more expensive.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=3,
    )

    # Cheaper candidate: Rs 80.00
    service.apply_substitution(
        item=item,
        substitute_product=Product(
            sku="SKU_CHEAP",
            name="Cheap",
            category="Dairy",
            brand="Amul",
            price=Decimal("80.00"),
        ),
        reserved_quantity=3,
    )

    assert item.charged_unit_price == Decimal("100.00")
    assert item.fulfilled_line_value == Decimal("300.00")


def test_decimal_boundary_comparisons_behave_correctly(
    service: SubstitutionService,
):
    """
    Decimal boundary comparisons with fractional prices behave accurately.
    """
    # Original unit price: Rs 33.33 -> 1.15 * 33.33 = 38.3295
    item = OrderItem(
        product_id="SKU_FRACTIONAL",
        category="Staples",
        brand="BrandX",
        unit_price=Decimal("33.33"),
        quantity=1,
    )

    # 38.32 <= 38.3295 -> eligible
    res_eligible = service.resolve_line_shortfall(
        requested_item=item,
        available_inventory=[
            Product(
                sku="SKU_CAND_OK",
                name="OK",
                category="Staples",
                brand="BrandY",
                price=Decimal("38.32"),
            )
        ],
        preference=SubstitutionPolicy.ALLOW,
    )
    assert res_eligible.substituted_product_sku == "SKU_CAND_OK"

    # 38.33 > 38.3295 -> rejected
    res_rejected = service.resolve_line_shortfall(
        requested_item=item,
        available_inventory=[
            Product(
                sku="SKU_CAND_HIGH",
                name="High",
                category="Staples",
                brand="BrandY",
                price=Decimal("38.33"),
            )
        ],
        preference=SubstitutionPolicy.ALLOW,
    )
    assert res_rejected.substituted_product_sku is None


# ---------------------------------------------------------------------------
# Integration with InventoryService and ReservationResult
# ---------------------------------------------------------------------------

def test_existing_different_substitute_sku_handled_safely(
    service: SubstitutionService,
):
    """
    A different substitute SKU must not overwrite one already recorded
    with a positive substituted quantity.
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=2,
        substituted_product_sku="SKU_SUB_A",
        substituted_quantity=2,
    )

    with pytest.raises(
        ValueError, match="already has a different substitute SKU recorded"
    ):
        service.apply_substitution(
            item=item,
            substitute_product="SKU_SUB_B",
            reserved_quantity=1,
        )

    assert item.substituted_product_sku == "SKU_SUB_A"
    assert item.substituted_quantity == 2
    assert item.fulfilled_quantity == 2

def test_product_instances_consumed_from_inventory_service(
    service: SubstitutionService,
):
    """
    Demonstrates that SubstitutionService directly consumes shared Product instances
    returned by InventoryService.get_substitution_candidates() without AttributeError.
    """
    store = Store(
        store_id="store_koramangala",
        name="Koramangala Dark Store",
        location=Coordinates(latitude=12.9352, longitude=77.6245),
        operating_hours=OperatingHours(
            open_time=time(6, 0), close_time=time(23, 0)
        ),
        picking_capacity=12,
        active_riders=3,
        inventory={
            "SKU-MILK-AMUL": 0,  # original out of stock
            "SKU-MILK-NANDINI": 10,  # positive stock
        },
    )
    product_orig = Product(
        sku="SKU-MILK-AMUL",
        name="Amul Taaza Milk 500ml",
        category="Dairy",
        brand="Amul",
        price=Decimal("27.00"),
    )
    product_sub = Product(
        sku="SKU-MILK-NANDINI",
        name="Nandini Toned Milk 500ml",
        category="Dairy",
        brand="Nandini",
        price=Decimal("26.00"),
    )

    inv_service = InventoryService()
    inv_service.register_store(store)
    inv_service.register_product(product_orig)
    inv_service.register_product(product_sub)

    # InventoryService returns list[Product]
    candidates = inv_service.get_substitution_candidates(
        "store_koramangala", "SKU-MILK-AMUL"
    )
    assert len(candidates) == 1
    assert isinstance(candidates[0], Product)

    item = OrderItem(
        product_id="SKU-MILK-AMUL",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("27.00"),
        quantity=2,
    )

    # SubstitutionService consumes candidates directly!
    result = service.resolve_line_shortfall(
        requested_item=item,
        available_inventory=candidates,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku == "SKU-MILK-NANDINI"
    assert result.fulfilled_quantity == 2
    assert result.charged_unit_price == Decimal("27.00")



def test_apply_reservation_convenience_method(
    service: SubstitutionService,
):
    """
    Verifies apply_reservation() handles ReservationResult from InventoryService.reserve_stock().
    """
    item = OrderItem(
        product_id="SKU_MILK",
        category="Dairy",
        brand="Amul",
        unit_price=Decimal("100.00"),
        quantity=5,
        fulfilled_quantity=2,
    )

    res_result = ReservationResult(
        reservation_id="res_12345",
        store_id="store_1",
        order_id="ord_1",
        sku="SKU_SUB_RESERVED",
        requested_quantity=3,
        reserved_quantity=2,  # partial reservation
    )

    result = service.apply_reservation(item, res_result)

    assert result.substituted_product_sku == "SKU_SUB_RESERVED"
    assert item.fulfilled_quantity == 4  # 2 original + 2 substitute
    assert item.substituted_quantity == 2
    assert item.original_fulfilled_quantity == 2
    assert item.shortfall_quantity == 1


# ---------------------------------------------------------------------------
# Backwards Compatibility with InventoryItem Fixture
# ---------------------------------------------------------------------------


def test_backwards_compatibility_with_inventory_item(
    service: SubstitutionService, sample_item: OrderItem
):
    """
    Verifies backwards compatibility when legacy InventoryItem instances are passed.
    """
    legacy_inv = [
        InventoryItem(
            sku_id="SKU_LEGACY_01",
            name="Legacy Milk",
            category="Dairy",
            brand="Amul",
            price=Decimal("105.00"),
            stock=4,
        )
    ]

    result = service.resolve_line_shortfall(
        requested_item=sample_item,
        available_inventory=legacy_inv,
        preference=SubstitutionPolicy.ALLOW,
    )

    assert result.substituted_product_sku == "SKU_LEGACY_01"
    assert result.substituted_sku_id == "SKU_LEGACY_01"
    assert result.fulfilled_quantity == 4
    assert result.fulfilled_qty == 4
    assert result.shortfall_quantity == 1
    assert result.shortfall_qty == 1


def test_negative_price_or_stock_raises_value_error():
    """
    Validates that negative unit price or stock raises ValueError.
    """
    with pytest.raises(ValueError, match="unit_price cannot be negative"):
        OrderItem(
            product_id="SKU_NEG",
            category="Dairy",
            brand="Amul",
            unit_price=Decimal("-10.00"),
            quantity=2,
        )

    with pytest.raises(ValueError, match="Price cannot be negative"):
        Product(
            sku="PROD_NEG",
            name="Bad Product",
            category="Dairy",
            brand="Amul",
            price=Decimal("-5.00"),
        )
