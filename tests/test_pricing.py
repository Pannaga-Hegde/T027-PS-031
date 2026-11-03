from datetime import datetime, time
from decimal import Decimal
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.store import Store
from ps031.services.pricing_service import (
    FREE_DELIVERY_FEE,
    FREE_DELIVERY_THRESHOLD,
    STANDARD_DELIVERY_FEE,
    SURGE_FEE,
    SURGE_RIDER_THRESHOLD,
    ZERO_SURGE_FEE,
    DeliveryPricingService,
    PricingResult,
    PricingService,
    PricingServiceInterface,
)


@pytest.fixture
def service() -> PricingService:
    return PricingService()


@pytest.fixture
def sample_location() -> Coordinates:
    return Coordinates(latitude=12.9352, longitude=77.6245)


@pytest.fixture
def sample_operating_hours() -> OperatingHours:
    return OperatingHours(open_time=time(6, 0), close_time=time(23, 0))


@pytest.fixture
def sample_store(
    sample_location: Coordinates, sample_operating_hours: OperatingHours
) -> Store:
    return Store(
        store_id="store_koramangala",
        name="Koramangala Pod",
        location=sample_location,
        operating_hours=sample_operating_hours,
        picking_capacity=15,
        active_riders=5,
        inventory={"SKU_MILK": 10, "SKU_BREAD": 10},
    )


# ---------------------------------------------------------------------------
# Protocol and Constants
# ---------------------------------------------------------------------------


def test_pricing_service_implements_protocol(service: PricingService):
    assert isinstance(service, PricingServiceInterface)


def test_delivery_pricing_service_alias():
    assert DeliveryPricingService is PricingService
    instance = DeliveryPricingService()
    assert isinstance(instance, PricingService)


def test_pricing_service_constants(service: PricingService):
    assert service.FREE_DELIVERY_THRESHOLD == Decimal("199.00")
    assert service.STANDARD_DELIVERY_FEE == Decimal("25.00")
    assert service.FREE_DELIVERY_FEE == Decimal("0.00")
    assert service.SURGE_FEE == Decimal("15.00")
    assert service.ZERO_SURGE_FEE == Decimal("0.00")
    assert service.SURGE_RIDER_THRESHOLD == 3

    assert FREE_DELIVERY_THRESHOLD == Decimal("199.00")
    assert STANDARD_DELIVERY_FEE == Decimal("25.00")
    assert FREE_DELIVERY_FEE == Decimal("0.00")
    assert SURGE_FEE == Decimal("15.00")
    assert ZERO_SURGE_FEE == Decimal("0.00")
    assert SURGE_RIDER_THRESHOLD == 3


# ---------------------------------------------------------------------------
# BR-07 Rule 1 & Rule 2: Order Value Calculation & Free Delivery
# ---------------------------------------------------------------------------


def test_order_value_using_post_substitution_fulfilled_lines(service: PricingService):
    """
    BR-07 Rule 1: Calculate order value using post-substitution fulfilled order lines
    and original customer-charged unit prices.
    """
    item1 = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("40.00"),
        requested_qty=3,
        fulfilled_qty=2,  # 2 fulfilled @ 40 = 80
    )
    item2 = OrderItem(
        sku_id="SKU_BREAD",
        unit_price=Decimal("35.00"),
        requested_qty=4,
        fulfilled_qty=3,  # 3 fulfilled @ 35 = 105
    )
    order = Order(order_id="ord_val", customer_id="c1", items=[item1, item2])

    order_val = service.calculate_order_value(order)
    assert order_val == Decimal("185.00")
    assert isinstance(order_val, Decimal)


def test_post_substitution_value_exactly_199_delivers_free(service: PricingService):
    """
    BR-07 Rule 2: If post-substitution order value is >= ₹199, delivery fee = ₹0.
    Boundary case: exactly ₹199.00.
    """
    item = OrderItem(
        sku_id="SKU_GHEE",
        unit_price=Decimal("199.00"),
        requested_qty=1,
        fulfilled_qty=1,
    )
    order = Order(order_id="ord_199", customer_id="c1", items=[item])

    fee = service.calculate_delivery_fee(order)
    assert fee == Decimal("0.00")
    assert isinstance(fee, Decimal)

    # Also test passing Decimal directly
    assert service.calculate_delivery_fee(Decimal("199.00")) == Decimal("0.00")


def test_post_substitution_value_above_199_delivers_free(service: PricingService):
    """
    BR-07 Rule 2: Values strictly greater than ₹199 qualify for free delivery.
    """
    assert service.calculate_delivery_fee(Decimal("199.01")) == Decimal("0.00")
    assert service.calculate_delivery_fee(Decimal("200.00")) == Decimal("0.00")
    assert service.calculate_delivery_fee(Decimal("1500.00")) == Decimal("0.00")


def test_substituted_items_charged_at_original_unit_price(service: PricingService):
    """
    BR-05 & BR-07: Substituted items are charged at the original product's unit price.
    Even if the substitute product costs more, order value calculation uses the original price.
    """
    # Original item: 2 units requested @ ₹100.00 each = ₹200.00
    # Both units fulfilled via substitute product SKU_PREMIUM
    item = OrderItem(
        sku_id="SKU_BASIC",
        category="Dairy",
        brand="BrandX",
        unit_price=Decimal("100.00"),
        requested_qty=2,
        fulfilled_qty=2,
        substituted_sku_id="SKU_PREMIUM",
        charged_unit_price=Decimal("100.00"),  # Customer pays original price
    )
    order = Order(order_id="ord_sub", customer_id="c1", items=[item])

    order_val = service.calculate_order_value(order)
    # 2 units @ ₹100.00 = ₹200.00 (qualifies for free delivery >= ₹199)
    assert order_val == Decimal("200.00")
    assert service.calculate_delivery_fee(order) == Decimal("0.00")


# ---------------------------------------------------------------------------
# BR-07 Rule 3: Standard Delivery Fee Below ₹199
# ---------------------------------------------------------------------------


def test_value_below_199_charges_standard_delivery_fee(service: PricingService):
    """
    BR-07 Rule 3: If order value is below ₹199, delivery fee = ₹25.
    Boundary case: ₹198.99.
    """
    assert service.calculate_delivery_fee(Decimal("198.99")) == Decimal("25.00")
    assert service.calculate_delivery_fee(Decimal("100.00")) == Decimal("25.00")
    assert service.calculate_delivery_fee(Decimal("25.00")) == Decimal("25.00")
    assert service.calculate_delivery_fee(Decimal("0.01")) == Decimal("25.00")
    assert service.calculate_delivery_fee(Decimal("0.00")) == Decimal("25.00")


def test_partial_fulfilment_order_value_drops_below_199(service: PricingService):
    """
    If requested order value was >= ₹199, but post-substitution fulfilled value
    drops below ₹199 due to partial fulfillment, standard ₹25 delivery fee applies.
    """
    # Requested 3 units @ ₹100 = ₹300 (would have been free)
    # But only 1 unit fulfilled = ₹100 (< ₹199)
    item = OrderItem(
        sku_id="SKU_PANEER",
        unit_price=Decimal("100.00"),
        requested_qty=3,
        fulfilled_qty=1,
    )
    order = Order(order_id="ord_partial", customer_id="c1", items=[item])

    order_val = service.calculate_order_value(order)
    assert order_val == Decimal("100.00")
    assert service.calculate_delivery_fee(order) == Decimal("25.00")


def test_zero_fulfilled_items_order_value_is_zero(service: PricingService):
    """When all items are completely unfulfilled, order value is ₹0 and delivery fee is ₹25."""
    item = OrderItem(
        sku_id="SKU_OUT_OF_STOCK",
        unit_price=Decimal("250.00"),
        requested_qty=2,
        fulfilled_qty=0,
    )
    order = Order(order_id="ord_zero", customer_id="c1", items=[item])

    assert service.calculate_order_value(order) == Decimal("0.00")
    assert service.calculate_delivery_fee(order) == Decimal("25.00")


def test_empty_order_value_is_zero(service: PricingService):
    """An order with no items has value ₹0.00 and charges standard ₹25 delivery fee."""
    order = Order(order_id="ord_empty", customer_id="c1", items=[])
    assert service.calculate_order_value(order) == Decimal("0.00")
    assert service.calculate_delivery_fee(order) == Decimal("25.00")


# ---------------------------------------------------------------------------
# BR-07 Rule 4 & Rule 5: Surge Fee Calculation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("riders", [0, 1, 2])
def test_fewer_than_three_active_riders_adds_surge(
    service: PricingService,
    sample_location: Coordinates,
    sample_operating_hours: OperatingHours,
    riders: int,
):
    """
    BR-07 Rule 4: Add a ₹15 surge fee if store has fewer than 3 active riders (< 3)
    at allocation time.
    """
    store = Store(
        store_id="s1",
        name="Store 1",
        location=sample_location,
        operating_hours=sample_operating_hours,
        active_riders=riders,
    )
    surge = service.calculate_surge_fee(store)
    assert surge == Decimal("15.00")
    assert isinstance(surge, Decimal)

    # Also test passing integer directly
    assert service.calculate_surge_fee(riders) == Decimal("15.00")


@pytest.mark.parametrize("riders", [3, 4, 10, 50])
def test_three_or_more_active_riders_no_surge(
    service: PricingService,
    sample_location: Coordinates,
    sample_operating_hours: OperatingHours,
    riders: int,
):
    """
    BR-07 Rule 5: If active riders are 3 or more (>= 3), surge fee = ₹0.
    Boundary case: exactly 3 active riders.
    """
    store = Store(
        store_id="s1",
        name="Store 1",
        location=sample_location,
        operating_hours=sample_operating_hours,
        active_riders=riders,
    )
    surge = service.calculate_surge_fee(store)
    assert surge == Decimal("0.00")
    assert isinstance(surge, Decimal)

    # Also test passing integer directly
    assert service.calculate_surge_fee(riders) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Combined Pricing Evaluation (calculate_pricing)
# ---------------------------------------------------------------------------


def test_full_pricing_free_delivery_no_surge(
    service: PricingService, sample_store: Store
):
    """Order value >= ₹199 and active riders >= 3 -> delivery fee = 0, surge = 0, total = 0."""
    sample_store.active_riders = 4
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("200.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item])

    result = service.calculate_pricing(order, store=sample_store)

    assert result.order_value == Decimal("200.00")
    assert result.delivery_fee == Decimal("0.00")
    assert result.surge_fee == Decimal("0.00")
    assert result.total_delivery_fee == Decimal("0.00")
    assert result.is_free_delivery is True
    assert result.is_surge_applied is False


def test_full_pricing_free_delivery_with_surge(
    service: PricingService, sample_store: Store
):
    """Order value >= ₹199 and active riders < 3 -> delivery fee = 0, surge = 15, total = 15."""
    sample_store.active_riders = 2
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("199.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item])

    result = service.calculate_pricing(order, store=sample_store)

    assert result.order_value == Decimal("199.00")
    assert result.delivery_fee == Decimal("0.00")
    assert result.surge_fee == Decimal("15.00")
    assert result.total_delivery_fee == Decimal("15.00")
    assert result.is_free_delivery is True
    assert result.is_surge_applied is True


def test_full_pricing_standard_delivery_no_surge(
    service: PricingService, sample_store: Store
):
    """Order value < ₹199 and active riders >= 3 -> delivery fee = 25, surge = 0, total = 25."""
    sample_store.active_riders = 3
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("150.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item])

    result = service.calculate_pricing(order, store=sample_store)

    assert result.order_value == Decimal("150.00")
    assert result.delivery_fee == Decimal("25.00")
    assert result.surge_fee == Decimal("0.00")
    assert result.total_delivery_fee == Decimal("25.00")
    assert result.is_free_delivery is False
    assert result.is_surge_applied is False


def test_full_pricing_standard_delivery_with_surge(
    service: PricingService, sample_store: Store
):
    """Order value < ₹199 and active riders < 3 -> delivery fee = 25, surge = 15, total = 40."""
    sample_store.active_riders = 1
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("50.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item])

    result = service.calculate_pricing(order, store=sample_store)

    assert result.order_value == Decimal("50.00")
    assert result.delivery_fee == Decimal("25.00")
    assert result.surge_fee == Decimal("15.00")
    assert result.total_delivery_fee == Decimal("40.00")
    assert result.is_free_delivery is False
    assert result.is_surge_applied is True


def test_calculate_pricing_is_pure_and_does_not_mutate_order(
    service: PricingService, sample_store: Store
):
    """calculate_pricing is a pure evaluation and must not mutate order attributes."""
    sample_store.active_riders = 1
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("50.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[item],
        delivery_fee=Decimal("0.00"),
        surge_fee=Decimal("0.00"),
    )

    res = service.calculate_pricing(order, store=sample_store)
    assert res.delivery_fee == Decimal("25.00")
    assert res.surge_fee == Decimal("15.00")

    # Order attributes remain unmutated
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")


# ---------------------------------------------------------------------------
# BR-07 Rule 6: Freezing Fees at Allocation
# ---------------------------------------------------------------------------


def test_apply_pricing_rejects_unallocated_order(
    service: PricingService, sample_store: Store
):
    """
    BR-07: Calling apply_pricing on an unallocated (RECEIVED) order raises ValueError.
    Pricing can only be applied and frozen once allocated.
    """
    sample_store.active_riders = 2
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(
        order_id="ord_unallocated",
        customer_id="c1",
        items=[item],
        status=OrderState.RECEIVED,
    )

    with pytest.raises(ValueError, match="unallocated"):
        service.apply_pricing(order, store=sample_store)


def test_apply_pricing_assigns_and_freezes_fees(
    service: PricingService, sample_store: Store
):
    """apply_pricing writes calculated fees onto the order and marks them frozen."""
    sample_store.active_riders = 2
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item], status=OrderState.ALLOCATED, allocated_store_id=sample_store.store_id)

    res = service.apply_pricing(order, store=sample_store)

    assert res.delivery_fee == Decimal("25.00")
    assert res.surge_fee == Decimal("15.00")
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("15.00")
    assert order.total_delivery_fee == Decimal("40.00")
    assert service.is_fee_frozen(order) is True


def test_fee_remains_frozen_when_rider_availability_changes(
    service: PricingService, sample_store: Store
):
    """
    BR-07 Rule 6: Later changes in rider availability must not silently recalculate
    an already allocated order's fees.
    """
    # At allocation time: only 1 rider available -> surge fee ₹15.00 applied
    sample_store.active_riders = 1
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("250.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="ord_frozen", customer_id="c1", items=[item], status=OrderState.ALLOCATED, allocated_store_id=sample_store.store_id)

    service.apply_pricing(order, store=sample_store)
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("15.00")
    assert order.total_delivery_fee == Decimal("15.00")

    # Later: 10 riders become active at the store
    sample_store.active_riders = 10

    # The order's fees MUST NOT change!
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("15.00")
    assert order.total_delivery_fee == Decimal("15.00")


def test_fee_remains_frozen_when_order_contents_change(
    service: PricingService, sample_store: Store
):
    """
    BR-07 Rule 6: Later changes in order contents must not silently recalculate
    an already allocated order's fees.
    """
    # At allocation time: order value ₹100.00 (< ₹199) -> delivery fee ₹25.00 applied
    sample_store.active_riders = 5
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="ord_frozen_items", customer_id="c1", items=[item], status=OrderState.ALLOCATED, allocated_store_id=sample_store.store_id)

    service.apply_pricing(order, store=sample_store)
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("0.00")

    # Later: item fulfilled quantity increases so order value becomes ₹500.00 (>= ₹199)
    item.fulfilled_qty = 5

    # The order's delivery fee MUST NOT silently drop to ₹0.00!
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.total_delivery_fee == Decimal("25.00")


def test_apply_pricing_rejects_recalculation_on_already_frozen_order(
    service: PricingService, sample_store: Store
):
    """Calling apply_pricing on an already frozen order raises ValueError."""
    sample_store.active_riders = 5
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="ord_reject_recalc", customer_id="c1", items=[item], status=OrderState.ALLOCATED, allocated_store_id=sample_store.store_id)

    service.apply_pricing(order, store=sample_store)
    assert order.pricing_frozen is True

    with pytest.raises(ValueError, match="already frozen"):
        service.apply_pricing(order, store=sample_store)


def test_apply_pricing_rejects_recalculation_when_pricing_frozen_true(
    service: PricingService, sample_store: Store
):
    """An order with pricing_frozen=True cannot have fees recalculated."""
    sample_store.active_riders = 5
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(
        order_id="ord_frozen_state",
        customer_id="c1",
        items=[item],
        status=OrderState.ALLOCATED,
        allocated_store_id=sample_store.store_id,
        pricing_frozen=True,
    )

    with pytest.raises(ValueError, match="already frozen"):
        service.apply_pricing(order, store=sample_store)


def test_apply_pricing_strictly_forbids_recalculation_without_force_bypass(
    service: PricingService, sample_store: Store
):
    """There is no force=True parameter bypass; frozen pricing is immutable."""
    sample_store.active_riders = 1
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("100.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="ord_no_force", customer_id="c1", items=[item], status=OrderState.ALLOCATED, allocated_store_id=sample_store.store_id)

    service.apply_pricing(order, store=sample_store)
    assert order.surge_fee == Decimal("15.00")
    assert order.pricing_frozen is True

    sample_store.active_riders = 5
    # Attempting to pass force=True raises TypeError since force was removed
    with pytest.raises(TypeError):
        service.apply_pricing(order, store=sample_store, force=True)  # type: ignore

    # Regular call still strictly raises ValueError
    with pytest.raises(ValueError, match="already frozen"):
        service.apply_pricing(order, store=sample_store)


# ---------------------------------------------------------------------------
# Store Registry and Active Riders Resolution
# ---------------------------------------------------------------------------


def test_pricing_with_registered_store(
    sample_location: Coordinates, sample_operating_hours: OperatingHours
):
    """PricingService can resolve active riders via registered store lookup by store_id."""
    store = Store(
        store_id="store_reg",
        name="Registered Store",
        location=sample_location,
        operating_hours=sample_operating_hours,
        active_riders=2,
    )
    service = PricingService(stores=[store])

    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("250.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(
        order_id="o1",
        customer_id="c1",
        items=[item],
        allocated_store_id="store_reg",
    )

    # Calculates without passing store explicitly
    result = service.calculate_pricing(order)
    assert result.delivery_fee == Decimal("0.00")
    assert result.surge_fee == Decimal("15.00")


def test_explicit_active_riders_overrides_store_attribute(
    service: PricingService, sample_store: Store
):
    """Explicit active_riders parameter overrides store.active_riders."""
    sample_store.active_riders = 1  # would trigger surge
    item = OrderItem(
        sku_id="SKU_1", unit_price=Decimal("200.00"), requested_qty=1, fulfilled_qty=1
    )
    order = Order(order_id="o1", customer_id="c1", items=[item])

    result = service.calculate_pricing(order, store=sample_store, active_riders=5)
    assert result.surge_fee == Decimal("0.00")


# ---------------------------------------------------------------------------
# Validation and Error Handling
# ---------------------------------------------------------------------------


def test_rejects_negative_order_value(service: PricingService):
    with pytest.raises(ValueError, match="cannot be negative"):
        service.calculate_delivery_fee(Decimal("-10.00"))


def test_rejects_boolean_order_value(service: PricingService):
    with pytest.raises(TypeError, match="cannot be a boolean"):
        service.calculate_delivery_fee(True)  # type: ignore

    with pytest.raises(TypeError, match="cannot be a boolean"):
        service.calculate_delivery_fee(False)  # type: ignore


def test_rejects_non_finite_order_value(service: PricingService):
    with pytest.raises(ValueError, match="finite"):
        service.calculate_delivery_fee(Decimal("NaN"))

    with pytest.raises(ValueError, match="finite"):
        service.calculate_delivery_fee(Decimal("Infinity"))


def test_rejects_malformed_order_value(service: PricingService):
    with pytest.raises(ValueError, match="Invalid order_value"):
        service.calculate_delivery_fee("invalid-amount")


def test_rejects_negative_active_riders(service: PricingService):
    with pytest.raises(ValueError, match="cannot be negative"):
        service.calculate_surge_fee(-1)


def test_rejects_boolean_active_riders(service: PricingService):
    with pytest.raises(TypeError, match="must be an integer"):
        service.calculate_surge_fee(True)  # type: ignore

    with pytest.raises(TypeError, match="must be an integer"):
        service.calculate_surge_fee(False)  # type: ignore


def test_rejects_float_active_riders(service: PricingService):
    with pytest.raises(TypeError, match="must be an integer"):
        service.calculate_surge_fee(2.5)  # type: ignore


def test_rejects_invalid_order_instance(service: PricingService):
    with pytest.raises(TypeError, match="must be an Order"):
        service.calculate_pricing("not_an_order")  # type: ignore

    with pytest.raises(TypeError, match="must be an Order"):
        service.apply_pricing("not_an_order")  # type: ignore


def test_rejects_invalid_store_instance(service: PricingService):
    item = OrderItem(sku_id="p1", unit_price=Decimal("100.00"), requested_qty=1)
    order = Order(order_id="o1", customer_id="c1", items=[item])

    with pytest.raises(TypeError, match="must be a Store"):
        service.calculate_pricing(order, store="not_a_store")  # type: ignore

    with pytest.raises(TypeError, match="must be a Store"):
        service.register_store("not_a_store")  # type: ignore


def test_missing_store_and_riders_raises_value_error(service: PricingService):
    item = OrderItem(sku_id="p1", unit_price=Decimal("100.00"), requested_qty=1)
    order = Order(order_id="o1", customer_id="c1", items=[item])

    with pytest.raises(ValueError, match="Either 'store' or 'active_riders'"):
        service.calculate_pricing(order)


def test_numeric_and_string_coercion_for_delivery_fee(service: PricingService):
    """calculate_delivery_fee accepts numeric and string types, converting safely."""
    assert service.calculate_delivery_fee(200) == Decimal("0.00")
    assert service.calculate_delivery_fee("200.00") == Decimal("0.00")
    assert service.calculate_delivery_fee(100) == Decimal("25.00")
    assert service.calculate_delivery_fee("100.00") == Decimal("25.00")



# ---------------------------------------------------------------------------
# BR-07 & AllocationService Integration Tests
# ---------------------------------------------------------------------------


def test_allocation_service_default_pricing_injection():
    """AllocationService defaults to PricingService when omitted."""
    from ps031.services.allocation_service import AllocationService

    alloc_service = AllocationService()
    assert isinstance(alloc_service._pricing_service, PricingService)


def test_allocation_service_freezes_delivery_and_surge_fees_at_allocation(
    sample_store: Store, sample_location: Coordinates
):
    """
    AllocationService calculates delivery and surge fees using the store's
    active_riders at allocation time and freezes them before transitioning to ALLOCATED.
    """
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 2  # < 3 riders -> surge = 15.00
    alloc_service = AllocationService()

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("50.00"),
        requested_qty=2,
        fulfilled_qty=2,  # 2 * 50 = 100 (< 199 -> delivery = 25.00)
    )
    order = Order(
        order_id="ord_alloc_pricing_1",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    assert order.status == OrderState.RECEIVED
    assert order.pricing_frozen is False
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")

    result = alloc_service.allocate_store(order, [sample_store])

    assert result.is_allocated is True
    assert order.status == OrderState.ALLOCATED
    assert order.allocated_store_id == sample_store.store_id
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("15.00")
    assert order.total_delivery_fee == Decimal("40.00")
    assert order.pricing_frozen is True

    # Changing store active riders afterwards does not change order's frozen fees
    sample_store.active_riders = 10
    assert order.surge_fee == Decimal("15.00")
    assert order.delivery_fee == Decimal("25.00")

    # Calling apply_pricing again raises ValueError
    pricing_service = PricingService()
    with pytest.raises(ValueError, match="already frozen"):
        pricing_service.apply_pricing(order, sample_store)


def test_allocation_service_free_delivery_and_zero_surge(
    sample_store: Store, sample_location: Coordinates
):
    """
    When order value >= 199 and store has >= 3 riders,
    delivery fee is 0 and surge fee is 0.
    """
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 4  # >= 3 riders -> surge = 0.00
    alloc_service = AllocationService()

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("100.00"),
        requested_qty=2,
        fulfilled_qty=2,  # 2 * 100 = 200 (>= 199 -> delivery = 0.00)
    )
    order = Order(
        order_id="ord_alloc_free",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    result = alloc_service.allocate_store(order, [sample_store])

    assert result.is_allocated is True
    assert order.status == OrderState.ALLOCATED
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.total_delivery_fee == Decimal("0.00")
    assert order.pricing_frozen is True


def test_allocation_service_zero_inventory_mutation_during_pricing(
    sample_store: Store, sample_location: Coordinates
):
    """Allocation pricing calculation must NOT mutate store inventory."""
    from ps031.services.allocation_service import AllocationService

    initial_inventory = dict(sample_store.inventory)
    alloc_service = AllocationService()

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("50.00"),
        requested_qty=2,
    )
    order = Order(
        order_id="ord_alloc_inv",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    alloc_service.allocate_store(order, [sample_store])

    # Store inventory must be completely untouched by allocation/pricing
    assert sample_store.inventory == initial_inventory


def test_allocation_service_pricing_failure_leaves_order_unmutated(
    sample_store: Store, sample_location: Coordinates
):
    """
    If pricing service calculation fails, order status, allocated_store_id,
    delivery_fee, surge_fee, and pricing_frozen remain unchanged.
    """
    from ps031.services.allocation_service import AllocationService

    class FailingPricingService:
        def calculate_pricing(self, *args, **kwargs):
            raise RuntimeError("Pricing calculation failed unexpectedly")

    alloc_service = AllocationService(pricing_service=FailingPricingService())  # type: ignore

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("50.00"),
        requested_qty=2,
    )
    order = Order(
        order_id="ord_pricing_fail",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    with pytest.raises(RuntimeError, match="Pricing calculation failed unexpectedly"):
        alloc_service.allocate_store(order, [sample_store])

    # All fields remain untouched
    assert order.status == OrderState.RECEIVED
    assert order.allocated_store_id is None
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.pricing_frozen is False


def test_allocation_service_lifecycle_transition_failure_rolls_back_pricing(
    sample_store: Store, sample_location: Coordinates
):
    """
    If lifecycle transition fails after pricing is calculated and applied,
    all 5 fields (status, allocated_store_id, delivery_fee, surge_fee, pricing_frozen)
    are rolled back to their initial snapshot.
    """
    from ps031.services.allocation_service import AllocationService
    from ps031.services.lifecycle import LifecycleTransitionService

    class FailingLifecycleService(LifecycleTransitionService):
        def transition(self, order, target_state, **kwargs):
            raise RuntimeError("Lifecycle transition failed unexpectedly")

    alloc_service = AllocationService(lifecycle_service=FailingLifecycleService())

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("50.00"),
        requested_qty=2,
    )
    order = Order(
        order_id="ord_lifecycle_fail",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    with pytest.raises(RuntimeError, match="Lifecycle transition failed unexpectedly"):
        alloc_service.allocate_store(order, [sample_store])

    # All 5 fields rolled back to their initial snapshot
    assert order.status == OrderState.RECEIVED
    assert order.allocated_store_id is None
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.pricing_frozen is False


def test_allocation_service_apply_lifecycle_transition_false_bypasses_pricing(
    sample_store: Store, sample_location: Coordinates
):
    """
    When apply_lifecycle_transition=False, store is evaluated and selected
    without state transition and without freezing pricing.
    """
    from ps031.services.allocation_service import AllocationService

    alloc_service = AllocationService()

    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("50.00"),
        requested_qty=2,
    )
    order = Order(
        order_id="ord_no_transition",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    result = alloc_service.allocate_store(
        order, [sample_store], apply_lifecycle_transition=False
    )

    assert result.is_allocated is True
    assert result.selected_store == sample_store
    assert order.status == OrderState.RECEIVED
    assert order.allocated_store_id is None
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.pricing_frozen is False


def test_allocation_service_with_substituted_items_charged_at_original_price(
    sample_store: Store, sample_location: Coordinates
):
    """
    Substituted item is charged at original product's unit price under BR-05 / BR-07,
    and qualifies for free delivery if total fulfilled value >= 199.
    """
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 3
    alloc_service = AllocationService()

    # Requested 2 units of SKU_MILK @ 100.00. 1 original fulfilled, 1 substituted with SKU_MILK_ALT
    item = OrderItem(
        sku_id="SKU_MILK",
        unit_price=Decimal("100.00"),
        requested_qty=2,
        fulfilled_qty=2,
        substituted_sku_id="SKU_MILK_ALT",
        substituted_quantity=1,
        charged_unit_price=Decimal("100.00"),
    )
    order = Order(
        order_id="ord_sub_alloc",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    result = alloc_service.allocate_store(order, [sample_store])

    assert result.is_allocated is True
    assert order.status == OrderState.ALLOCATED
    assert order.delivery_fee == Decimal("0.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.pricing_frozen is True


def test_allocation_active_riders_boundary_two_versus_three(
    sample_store: Store, sample_location: Coordinates
):
    """
    BR-07 Rule 4 & 5: Active riders 2 incurs ?15 surge fee;
    active riders 3 incurs ?0 surge fee.
    """
    from ps031.services.allocation_service import AllocationService

    alloc_service = AllocationService()

    # Case A: exactly 2 active riders -> surge fee = 15.00
    sample_store.active_riders = 2
    order_a = Order(
        order_id="ord_riders_2",
        customer_id="c1",
        items=[OrderItem("SKU_1", 1, Decimal("200.00"), fulfilled_quantity=1)],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    alloc_service.allocate_store(order_a, [sample_store])
    assert order_a.surge_fee == Decimal("15.00")
    assert order_a.delivery_fee == Decimal("0.00")
    assert order_a.total_delivery_fee == Decimal("15.00")

    # Case B: exactly 3 active riders -> surge fee = 0.00
    sample_store.active_riders = 3
    order_b = Order(
        order_id="ord_riders_3",
        customer_id="c2",
        items=[OrderItem("SKU_1", 1, Decimal("200.00"), fulfilled_quantity=1)],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    alloc_service.allocate_store(order_b, [sample_store])
    assert order_b.surge_fee == Decimal("0.00")
    assert order_b.delivery_fee == Decimal("0.00")
    assert order_b.total_delivery_fee == Decimal("0.00")


def test_allocation_post_substitution_value_boundary_198_99_versus_199_00(
    sample_store: Store, sample_location: Coordinates
):
    """
    BR-07 Rule 2 & 3: Post-substitution order value ?198.99 charges ?25 delivery fee;
    post-substitution order value ?199.00 charges ?0 delivery fee.
    """
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 3
    alloc_service = AllocationService()

    # Case A: post-substitution fulfilled value = ?198.99 (< 199.00)
    item_a = OrderItem(
        sku_id="SKU_1",
        unit_price=Decimal("198.99"),
        requested_qty=1,
        fulfilled_qty=1,
    )
    order_a = Order(
        order_id="ord_boundary_below",
        customer_id="c1",
        items=[item_a],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    alloc_service.allocate_store(order_a, [sample_store])
    assert order_a.delivery_fee == Decimal("25.00")
    assert order_a.pricing_frozen is True

    # Case B: post-substitution fulfilled value = ?199.00 (>= 199.00)
    item_b = OrderItem(
        sku_id="SKU_2",
        unit_price=Decimal("199.00"),
        requested_qty=1,
        fulfilled_qty=1,
    )
    order_b = Order(
        order_id="ord_boundary_exact",
        customer_id="c2",
        items=[item_b],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )
    alloc_service.allocate_store(order_b, [sample_store])
    assert order_b.delivery_fee == Decimal("0.00")
    assert order_b.pricing_frozen is True


def test_legacy_constructor_compatibility_with_allocation_and_pricing(
    sample_store: Store, sample_location: Coordinates
):
    """
    Backward compatibility: Orders created via legacy positional arguments
    seamlessly support pricing calculation, fee freezing, and allocation.
    """
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 5
    alloc_service = AllocationService()

    # Legacy constructor: OrderItem(product_id, quantity, unit_price)
    item = OrderItem("SKU_LEGACY", 2, Decimal("40.00"))
    item.fulfilled_quantity = 2  # 2 * 40 = 80 (< 199 -> delivery = 25.00)

    # Legacy constructor: Order(order_id, customer_id, items)
    order = Order("ord_legacy_01", "cust_legacy_01", [item])
    order.delivery_location = sample_location
    order.order_time = datetime(2026, 11, 3, 10, 0)

    alloc_service.allocate_store(order, [sample_store])

    assert order.status == OrderState.ALLOCATED
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("0.00")
    assert order.total_delivery_fee == Decimal("25.00")
    assert order.pricing_frozen is True


def test_serialized_order_preserves_pricing_frozen_and_blocks_repricing(
    sample_store: Store, sample_location: Coordinates
):
    """
    Serialization/reconstruction via dataclasses.asdict preserves pricing_frozen=True
    and blocks subsequent repricing attempts under BR-07.
    """
    from dataclasses import asdict
    from ps031.services.allocation_service import AllocationService

    sample_store.active_riders = 2
    alloc_service = AllocationService()

    item = OrderItem(
        sku_id="SKU_1",
        unit_price=Decimal("150.00"),
        requested_qty=1,
        fulfilled_qty=1,
    )
    order = Order(
        order_id="ord_serialize",
        customer_id="c1",
        items=[item],
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    alloc_service.allocate_store(order, [sample_store])
    assert order.pricing_frozen is True
    assert order.delivery_fee == Decimal("25.00")
    assert order.surge_fee == Decimal("15.00")

    # Serialize to dictionary and reconstruct
    data = asdict(order)
    assert data["pricing_frozen"] is True

    # Reconstruct nested dataclass objects properly
    data["items"] = [OrderItem(**i) for i in data["items"]]
    if data.get("delivery_location"):
        data["delivery_location"] = Coordinates(**data["delivery_location"])
    reconstructed = Order(**data)
    assert reconstructed.pricing_frozen is True
    assert reconstructed.delivery_fee == Decimal("25.00")
    assert reconstructed.surge_fee == Decimal("15.00")

    # Repricing reconstructed frozen order strictly raises ValueError
    pricing_service = PricingService()
    with pytest.raises(ValueError, match="already frozen"):
        pricing_service.apply_pricing(reconstructed, sample_store)


def test_allocation_rejects_received_order_with_pre_frozen_pricing(
    sample_store: Store, sample_location: Coordinates
):
    """
    AllocationService rejects allocating a RECEIVED order that already has pricing_frozen=True,
    preventing allocation with stale or pre-frozen fees.
    """
    from ps031.services.allocation_service import AllocationService

    alloc_service = AllocationService()
    item = OrderItem(
        sku_id="SKU_1",
        unit_price=Decimal("100.00"),
        requested_qty=1,
        fulfilled_qty=1,
    )
    order = Order(
        order_id="ord_pre_frozen",
        customer_id="c1",
        items=[item],
        status=OrderState.RECEIVED,
        pricing_frozen=True,
        delivery_fee=Decimal("10.00"),
        surge_fee=Decimal("5.00"),
        delivery_location=sample_location,
        order_time=datetime(2026, 11, 3, 10, 0),
    )

    with pytest.raises(ValueError, match="pricing_frozen=True"):
        alloc_service.allocate_store(order, [sample_store])

    # Assert order attributes remained completely untouched
    assert order.status == OrderState.RECEIVED
    assert order.allocated_store_id is None
    assert order.delivery_fee == Decimal("10.00")
    assert order.surge_fee == Decimal("5.00")
    assert order.pricing_frozen is True
