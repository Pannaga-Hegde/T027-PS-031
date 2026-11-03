from datetime import datetime, time
from decimal import Decimal
import pytest

from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.product import Product
from ps031.models.store import Store
from ps031.services.inventory_service import (
    InvalidReservationStateError,
    InventoryService,
    Reservation,
    ReservationResult,
    ReservationStatus,
)


@pytest.fixture
def sample_location() -> Coordinates:
    return Coordinates(latitude=12.9716, longitude=77.5946)


@pytest.fixture
def sample_hours() -> OperatingHours:
    return OperatingHours(open_time=time(6, 0), close_time=time(23, 0))


@pytest.fixture
def sample_products() -> list[Product]:
    return [
        Product(
            sku="SKU-MILK-AMUL",
            name="Amul Taaza 1L",
            category="Dairy",
            brand="Amul",
            price=Decimal("54.00"),
        ),
        Product(
            sku="SKU-MILK-NANDINI",
            name="Nandini Toned 1L",
            category="Dairy",
            brand="Nandini",
            price=Decimal("52.00"),
        ),
        Product(
            sku="SKU-MILK-MD",
            name="Mother Dairy 1L",
            category="Dairy",
            brand="Mother Dairy",
            price=Decimal("56.00"),
        ),
        Product(
            sku="SKU-BREAD-WHEAT",
            name="Whole Wheat Bread 400g",
            category="Bakery",
            brand="Modern",
            price=Decimal("45.00"),
        ),
    ]


@pytest.fixture
def sample_store(sample_location, sample_hours) -> Store:
    return Store(
        store_id="store_koramangala",
        name="Koramangala Dark Store",
        location=sample_location,
        operating_hours=sample_hours,
        picking_capacity=12,
        active_riders=3,
        inventory={
            "SKU-MILK-AMUL": 10,
            "SKU-MILK-NANDINI": 5,
            "SKU-MILK-MD": 0,
            "SKU-BREAD-WHEAT": 20,
        },
    )


@pytest.fixture
def inventory_service(sample_store, sample_products) -> InventoryService:
    return InventoryService(
        stores=[sample_store],
        products=sample_products,
    )


# ---------------------------------------------------------------------------
# 1. Stock Lookup Tests
# ---------------------------------------------------------------------------


def test_current_stock_lookup(inventory_service, sample_store):
    """get_stock returns current available stock from live Store.inventory."""
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 10
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-NANDINI") == 5
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-MD") == 0


def test_stock_lookup_reads_live_inventory_without_caching(
    inventory_service, sample_store
):
    """Mutating store.inventory directly is immediately reflected in get_stock."""
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 10

    # Simulate direct live update on store inventory
    sample_store.inventory["SKU-MILK-AMUL"] = 7
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 7


def test_unknown_store_raises_key_error(inventory_service):
    """Operations on unknown store_id raise KeyError."""
    with pytest.raises(KeyError, match="Store 'unknown_store' not found"):
        inventory_service.get_stock("unknown_store", "SKU-MILK-AMUL")

    with pytest.raises(KeyError, match="Store 'unknown_store' not found"):
        inventory_service.reserve_stock(
            "unknown_store", "ord_101", "SKU-MILK-AMUL", 2
        )

    with pytest.raises(KeyError, match="Store 'unknown_store' not found"):
        inventory_service.get_substitution_candidates(
            "unknown_store", "SKU-MILK-AMUL"
        )


def test_unknown_sku_raises_key_error(inventory_service):
    """Operations with an uncatalogued/unknown SKU raise KeyError."""
    with pytest.raises(KeyError, match="Product SKU 'NON_EXISTENT_SKU' not found"):
        inventory_service.get_stock("store_koramangala", "NON_EXISTENT_SKU")

    with pytest.raises(KeyError, match="Product SKU 'NON_EXISTENT_SKU' not found"):
        inventory_service.reserve_stock(
            "store_koramangala", "ord_101", "NON_EXISTENT_SKU", 2
        )

    with pytest.raises(KeyError, match="Product SKU 'NON_EXISTENT_SKU' not found"):
        inventory_service.get_substitution_candidates(
            "store_koramangala", "NON_EXISTENT_SKU"
        )


def test_sku_in_store_inventory_but_absent_from_catalog_rejected(
    sample_location, sample_hours, sample_products
):
    """
    A SKU that appears in Store.inventory but is absent from the registered Product
    catalog must not silently become valid; operations on it must raise KeyError.
    """
    store_with_rogue_sku = Store(
        store_id="store_rogue",
        name="Rogue SKU Store",
        location=sample_location,
        operating_hours=sample_hours,
        inventory={
            "SKU-MILK-AMUL": 10,
            "SKU-UNREGISTERED-ROGUE": 25,  # Exists in inventory but NOT in catalog
        },
    )

    service = InventoryService(
        stores=[store_with_rogue_sku],
        products=sample_products,  # Catalog does NOT contain SKU-UNREGISTERED-ROGUE
    )

    with pytest.raises(
        KeyError, match="Product SKU 'SKU-UNREGISTERED-ROGUE' not found in catalog"
    ):
        service.get_stock("store_rogue", "SKU-UNREGISTERED-ROGUE")

    with pytest.raises(
        KeyError, match="Product SKU 'SKU-UNREGISTERED-ROGUE' not found in catalog"
    ):
        service.reserve_stock(
            "store_rogue", "ord_rogue", "SKU-UNREGISTERED-ROGUE", 5
        )

    with pytest.raises(
        KeyError, match="Product SKU 'SKU-UNREGISTERED-ROGUE' not found in catalog"
    ):
        service.get_substitution_candidates(
            "store_rogue", "SKU-UNREGISTERED-ROGUE"
        )


def test_catalog_product_not_in_store_inventory_returns_zero_stock(
    sample_location, sample_hours
):
    """A product registered in catalog but missing key from store.inventory returns 0 stock."""
    prod = Product(
        sku="SKU-CHIPS",
        name="Potato Chips",
        category="Snacks",
        brand="Lays",
        price=Decimal("20.00"),
    )
    store = Store(
        store_id="store_empty_inv",
        name="Empty Store",
        location=sample_location,
        operating_hours=sample_hours,
        inventory={},  # No items stocked
    )
    service = InventoryService(stores=[store], products=[prod])

    assert service.get_stock("store_empty_inv", "SKU-CHIPS") == 0


# ---------------------------------------------------------------------------
# 2. Reservation Tests
# ---------------------------------------------------------------------------


def test_full_reservation(inventory_service, sample_store):
    """Reserve available stock when quantity requested <= available stock."""
    result = inventory_service.reserve_stock(
        store_id="store_koramangala",
        order_id="ord_1001",
        sku="SKU-MILK-AMUL",
        quantity=4,
    )

    assert isinstance(result, ReservationResult)
    assert result.is_fulfilled is True
    assert result.is_partial is False
    assert result.is_zero is False
    assert result.requested_quantity == 4
    assert result.reserved_quantity == 4
    assert result.status == ReservationStatus.PENDING
    assert result.reservation_id is not None
    assert result.reservation_id.startswith("res_")

    # Store inventory immediately decreased
    assert sample_store.inventory["SKU-MILK-AMUL"] == 6
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 6

    # Internal reservation record exists
    record = inventory_service.get_reservation(result.reservation_id)
    assert record.store_id == "store_koramangala"
    assert record.order_id == "ord_1001"
    assert record.sku == "SKU-MILK-AMUL"
    assert record.requested_quantity == 4
    assert record.reserved_quantity == 4
    assert record.status == ReservationStatus.PENDING


def test_partial_reservation(inventory_service, sample_store):
    """Reserve partial stock when requested quantity > available stock."""
    # Available is 5
    result = inventory_service.reserve_stock(
        store_id="store_koramangala",
        order_id="ord_1002",
        sku="SKU-MILK-NANDINI",
        quantity=8,
    )

    assert result.is_fulfilled is False
    assert result.is_partial is True
    assert result.is_zero is False
    assert result.requested_quantity == 8
    assert result.reserved_quantity == 5
    assert result.status == ReservationStatus.PENDING
    assert result.reservation_id is not None

    # Store inventory drops to zero
    assert sample_store.inventory["SKU-MILK-NANDINI"] == 0
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-NANDINI") == 0


def test_zero_stock_reservation(inventory_service, sample_store):
    """When stock is zero, return zero-reservation result without active record."""
    # Available is 0
    result = inventory_service.reserve_stock(
        store_id="store_koramangala",
        order_id="ord_1003",
        sku="SKU-MILK-MD",
        quantity=3,
    )

    assert result.is_fulfilled is False
    assert result.is_partial is False
    assert result.is_zero is True
    assert result.requested_quantity == 3
    assert result.reserved_quantity == 0
    assert result.reservation_id is None
    assert result.status is None

    # No reservation recorded
    assert len(inventory_service.reservations) == 0
    assert sample_store.inventory["SKU-MILK-MD"] == 0


def test_immediate_inventory_reduction(inventory_service, sample_store):
    """Reservation immediately reduces Store.inventory and reflects in get_stock."""
    assert inventory_service.get_stock("store_koramangala", "SKU-BREAD-WHEAT") == 20

    res1 = inventory_service.reserve_stock(
        "store_koramangala", "ord_1004", "SKU-BREAD-WHEAT", 6
    )
    assert sample_store.inventory["SKU-BREAD-WHEAT"] == 14
    assert inventory_service.get_stock("store_koramangala", "SKU-BREAD-WHEAT") == 14

    res2 = inventory_service.reserve_stock(
        "store_koramangala", "ord_1005", "SKU-BREAD-WHEAT", 4
    )
    assert sample_store.inventory["SKU-BREAD-WHEAT"] == 10
    assert inventory_service.get_stock("store_koramangala", "SKU-BREAD-WHEAT") == 10


def test_unique_ids_for_repeated_reservations(inventory_service):
    """Repeated reservations for the same order and SKU produce distinct IDs."""
    res1 = inventory_service.reserve_stock(
        "store_koramangala", "ord_repeat", "SKU-MILK-AMUL", 2
    )
    res2 = inventory_service.reserve_stock(
        "store_koramangala", "ord_repeat", "SKU-MILK-AMUL", 2
    )

    assert res1.reservation_id is not None
    assert res2.reservation_id is not None
    assert res1.reservation_id != res2.reservation_id


def test_multiple_reservations_against_same_stock(inventory_service, sample_store):
    """Multiple orders reserving against the same stock allocate sequentially."""
    # Start: 10 units of Amul Milk
    res_a = inventory_service.reserve_stock(
        "store_koramangala", "ord_A", "SKU-MILK-AMUL", 4
    )
    assert res_a.reserved_quantity == 4
    assert sample_store.inventory["SKU-MILK-AMUL"] == 6

    res_b = inventory_service.reserve_stock(
        "store_koramangala", "ord_B", "SKU-MILK-AMUL", 4
    )
    assert res_b.reserved_quantity == 4
    assert sample_store.inventory["SKU-MILK-AMUL"] == 2

    # Request 4, but only 2 remain
    res_c = inventory_service.reserve_stock(
        "store_koramangala", "ord_C", "SKU-MILK-AMUL", 4
    )
    assert res_c.is_partial is True
    assert res_c.reserved_quantity == 2
    assert sample_store.inventory["SKU-MILK-AMUL"] == 0

    # Fourth order gets 0
    res_d = inventory_service.reserve_stock(
        "store_koramangala", "ord_D", "SKU-MILK-AMUL", 2
    )
    assert res_d.is_zero is True
    assert res_d.reserved_quantity == 0
    assert sample_store.inventory["SKU-MILK-AMUL"] == 0


# ---------------------------------------------------------------------------
# 3. Commit Tests
# ---------------------------------------------------------------------------


def test_commit_changes_status_but_not_stock(inventory_service, sample_store):
    """commit_reservation transitions status to COMMITTED without changing stock."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_commit", "SKU-MILK-AMUL", 3
    )
    assert sample_store.inventory["SKU-MILK-AMUL"] == 7

    committed = inventory_service.commit_reservation(res.reservation_id)
    assert isinstance(committed, Reservation)
    assert committed.status == ReservationStatus.COMMITTED
    # Stock remains 7
    assert sample_store.inventory["SKU-MILK-AMUL"] == 7


def test_repeated_commit_idempotency(inventory_service, sample_store):
    """Committing an already committed reservation is idempotent and succeeds."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_idemp_c", "SKU-MILK-AMUL", 2
    )
    first_commit = inventory_service.commit_reservation(res.reservation_id)
    assert first_commit.status == ReservationStatus.COMMITTED

    # Repeated commit
    second_commit = inventory_service.commit_reservation(res.reservation_id)
    assert second_commit.status == ReservationStatus.COMMITTED
    assert sample_store.inventory["SKU-MILK-AMUL"] == 8


def test_commit_after_release_rejected(inventory_service):
    """Committing a RELEASED reservation raises InvalidReservationStateError."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_rel_then_com", "SKU-MILK-AMUL", 3
    )
    inventory_service.release_reservation(res.reservation_id)

    with pytest.raises(InvalidReservationStateError, match="already RELEASED"):
        inventory_service.commit_reservation(res.reservation_id)


# ---------------------------------------------------------------------------
# 4. Release Tests
# ---------------------------------------------------------------------------


def test_release_restores_stock_exactly_once(inventory_service, sample_store):
    """release_reservation transitions status to RELEASED and restores stock."""
    # 10 initial stock
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_release", "SKU-MILK-AMUL", 4
    )
    assert sample_store.inventory["SKU-MILK-AMUL"] == 6

    released = inventory_service.release_reservation(res.reservation_id)
    assert released.status == ReservationStatus.RELEASED
    # Stock restored to 10
    assert sample_store.inventory["SKU-MILK-AMUL"] == 10
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 10


def test_repeated_release_idempotency(inventory_service, sample_store):
    """Releasing an already released reservation does not restore stock twice."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_idemp_r", "SKU-MILK-AMUL", 4
    )
    assert sample_store.inventory["SKU-MILK-AMUL"] == 6

    inventory_service.release_reservation(res.reservation_id)
    assert sample_store.inventory["SKU-MILK-AMUL"] == 10

    # Second release call must be idempotent
    repeated = inventory_service.release_reservation(res.reservation_id)
    assert repeated.status == ReservationStatus.RELEASED
    assert sample_store.inventory["SKU-MILK-AMUL"] == 10


def test_release_after_commit_rejected(inventory_service):
    """Releasing a COMMITTED reservation raises InvalidReservationStateError."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_com_then_rel", "SKU-MILK-AMUL", 3
    )
    inventory_service.commit_reservation(res.reservation_id)

    with pytest.raises(InvalidReservationStateError, match="already COMMITTED"):
        inventory_service.release_reservation(res.reservation_id)


# ---------------------------------------------------------------------------
# 5. Protection of Internal Reservation Records
# ---------------------------------------------------------------------------


def test_returned_reservation_is_safe_copy_from_get_reservation(
    inventory_service,
):
    """Mutating the returned Reservation from get_reservation does not alter internal state."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_protect_get", "SKU-MILK-AMUL", 4
    )
    copy_res = inventory_service.get_reservation(res.reservation_id)

    # Mutate the returned object externally
    copy_res.reserved_quantity = 9999
    copy_res.status = ReservationStatus.COMMITTED
    copy_res.sku = "SKU-FAKE"
    copy_res.store_id = "store_fake"

    # Fetch again from service; internal record must be completely intact
    fresh = inventory_service.get_reservation(res.reservation_id)
    assert fresh.reserved_quantity == 4
    assert fresh.status == ReservationStatus.PENDING
    assert fresh.sku == "SKU-MILK-AMUL"
    assert fresh.store_id == "store_koramangala"


def test_returned_reservation_is_safe_copy_from_commit_reservation(
    inventory_service,
):
    """Mutating the returned Reservation from commit_reservation does not alter internal state."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_protect_com", "SKU-MILK-AMUL", 3
    )
    committed = inventory_service.commit_reservation(res.reservation_id)

    committed.reserved_quantity = 0
    committed.status = ReservationStatus.RELEASED

    fresh = inventory_service.get_reservation(res.reservation_id)
    assert fresh.reserved_quantity == 3
    assert fresh.status == ReservationStatus.COMMITTED


def test_returned_reservation_is_safe_copy_from_release_reservation(
    inventory_service,
):
    """Mutating the returned Reservation from release_reservation does not alter internal state."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_protect_rel", "SKU-MILK-AMUL", 3
    )
    released = inventory_service.release_reservation(res.reservation_id)

    released.reserved_quantity = 1234
    released.status = ReservationStatus.PENDING

    fresh = inventory_service.get_reservation(res.reservation_id)
    assert fresh.reserved_quantity == 3
    assert fresh.status == ReservationStatus.RELEASED


def test_mutating_returned_reservation_cannot_corrupt_stock_restoration(
    inventory_service, sample_store
):
    """
    Tampering with a returned reservation's quantity does not affect the stock restored
    when release_reservation is subsequently called.
    """
    # 10 initial stock
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_tamper", "SKU-MILK-AMUL", 4
    )
    assert sample_store.inventory["SKU-MILK-AMUL"] == 6

    # Caller gets reservation and tampers with reserved_quantity
    tampered_copy = inventory_service.get_reservation(res.reservation_id)
    tampered_copy.reserved_quantity = 99999

    # Releasing reservation must restore exactly 4 units, not 99999
    inventory_service.release_reservation(res.reservation_id)
    assert sample_store.inventory["SKU-MILK-AMUL"] == 10
    assert inventory_service.get_stock("store_koramangala", "SKU-MILK-AMUL") == 10


def test_reservations_property_returns_isolated_copies(inventory_service):
    """Mutating the reservations property dict or its values does not alter service state."""
    res = inventory_service.reserve_stock(
        "store_koramangala", "ord_prop_copy", "SKU-MILK-AMUL", 2
    )

    all_reservations = inventory_service.reservations
    assert res.reservation_id in all_reservations

    # Mutate the dictionary and the item
    all_reservations[res.reservation_id].reserved_quantity = 8888
    del all_reservations[res.reservation_id]

    fresh_all = inventory_service.reservations
    assert res.reservation_id in fresh_all
    assert fresh_all[res.reservation_id].reserved_quantity == 2


# ---------------------------------------------------------------------------
# 6. Safe Store Registration & Duplicate Prevention
# ---------------------------------------------------------------------------


def test_duplicate_store_registration_rejected(
    sample_location, sample_hours
):
    """Registering a store with an existing store_id raises ValueError."""
    s1 = Store("store_dup", "Store A", sample_location, sample_hours)
    s2 = Store("store_dup", "Store B", sample_location, sample_hours)

    service = InventoryService()
    service.register_store(s1)

    with pytest.raises(
        ValueError, match="Store with ID 'store_dup' is already registered"
    ):
        service.register_store(s2)


def test_duplicate_store_registration_in_constructor_rejected(
    sample_location, sample_hours
):
    """Passing duplicate store_ids to InventoryService constructor raises ValueError."""
    s1 = Store("store_dup", "Store A", sample_location, sample_hours)
    s2 = Store("store_dup", "Store B", sample_location, sample_hours)

    with pytest.raises(ValueError, match="already registered"):
        InventoryService(stores=[s1, s2])


def test_duplicate_store_registration_cannot_redirect_stock_restoration(
    sample_location, sample_hours, sample_products
):
    """
    An attempt to register a duplicate store cannot overwrite the store reference
    or redirect stock restoration of an existing reservation.
    """
    store_a = Store(
        store_id="store_shared_id",
        name="Store A",
        location=sample_location,
        operating_hours=sample_hours,
        inventory={"SKU-MILK-AMUL": 10},
    )
    store_b = Store(
        store_id="store_shared_id",  # Attempting to hijack store_id
        name="Store B Hijack",
        location=sample_location,
        operating_hours=sample_hours,
        inventory={"SKU-MILK-AMUL": 100},
    )

    service = InventoryService(stores=[store_a], products=sample_products)

    # Reserve 4 units from Store A
    res = service.reserve_stock(
        "store_shared_id", "ord_hijack_test", "SKU-MILK-AMUL", 4
    )
    assert store_a.inventory["SKU-MILK-AMUL"] == 6

    # Attempt to replace Store A with Store B
    with pytest.raises(ValueError, match="already registered"):
        service.register_store(store_b)

    # Release reservation; stock must be restored to Store A, not Store B
    service.release_reservation(res.reservation_id)
    assert store_a.inventory["SKU-MILK-AMUL"] == 10
    assert store_b.inventory["SKU-MILK-AMUL"] == 100  # Untouched


def test_duplicate_product_registration_rejected():
    """Registering a product with an existing SKU raises ValueError."""
    p1 = Product("SKU-DUP", "Prod 1", "Cat", "Brand", Decimal("10.00"))
    p2 = Product("SKU-DUP", "Prod 2", "Cat", "Brand", Decimal("20.00"))

    service = InventoryService()
    service.register_product(p1)

    with pytest.raises(
        ValueError, match="Product with SKU 'SKU-DUP' is already registered"
    ):
        service.register_product(p2)


def test_service_registration_methods_normal(sample_location, sample_hours):
    """Normal registration of distinct stores and products succeeds."""
    store2 = Store(
        store_id="store_indiranagar",
        name="Indiranagar Dark Store",
        location=sample_location,
        operating_hours=sample_hours,
        inventory={"SKU-BUTTER": 15},
    )
    new_product = Product(
        sku="SKU-BUTTER",
        name="Amul Butter 100g",
        category="Dairy",
        brand="Amul",
        price=Decimal("58.00"),
    )

    service = InventoryService()
    service.register_store(store2)
    service.register_product(new_product)

    assert service.get_stock("store_indiranagar", "SKU-BUTTER") == 15


# ---------------------------------------------------------------------------
# 7. Consistent Reservation ID Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_res_id, expected_exc",
    [
        (True, TypeError),
        (False, TypeError),
        (123, TypeError),
        (4.5, TypeError),
        (None, TypeError),
        ([], TypeError),
        ({}, TypeError),
        ("", ValueError),
        ("   ", ValueError),
    ],
)
def test_consistent_reservation_id_validation_across_all_methods(
    inventory_service, invalid_res_id, expected_exc
):
    """
    commit_reservation, release_reservation, and get_reservation all consistently
    reject non-string, boolean, empty, and whitespace reservation IDs with identical errors.
    """
    with pytest.raises(expected_exc):
        inventory_service.get_reservation(invalid_res_id)

    with pytest.raises(expected_exc):
        inventory_service.commit_reservation(invalid_res_id)

    with pytest.raises(expected_exc):
        inventory_service.release_reservation(invalid_res_id)


def test_unknown_reservation_id_raises_key_error(inventory_service):
    """Operations on non-existent string reservation IDs raise KeyError."""
    with pytest.raises(KeyError, match="Reservation 'non_existent_res' not found"):
        inventory_service.get_reservation("non_existent_res")

    with pytest.raises(KeyError, match="Reservation 'non_existent_res' not found"):
        inventory_service.commit_reservation("non_existent_res")

    with pytest.raises(KeyError, match="Reservation 'non_existent_res' not found"):
        inventory_service.release_reservation("non_existent_res")


# ---------------------------------------------------------------------------
# 8. Quantity and Identifier Validation Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalid_qty", [0, -1, -10])
def test_reject_zero_and_negative_quantities(inventory_service, invalid_qty):
    """reserve_stock rejects quantities <= 0 with ValueError."""
    with pytest.raises(ValueError, match="positive integer"):
        inventory_service.reserve_stock(
            "store_koramangala", "ord_bad_qty", "SKU-MILK-AMUL", invalid_qty
        )


@pytest.mark.parametrize("invalid_type_qty", [True, False, 2.5, "5", [1], None])
def test_reject_boolean_float_string_and_invalid_types(
    inventory_service, invalid_type_qty
):
    """reserve_stock rejects non-integer or boolean quantities with TypeError."""
    with pytest.raises(TypeError, match="integer"):
        inventory_service.reserve_stock(
            "store_koramangala", "ord_bad_qty", "SKU-MILK-AMUL", invalid_type_qty
        )


@pytest.mark.parametrize("invalid_id", ["", "   ", True, None, 123])
def test_reject_invalid_store_and_order_identifiers(inventory_service, invalid_id):
    """Empty, whitespace, boolean, and non-string identifiers are rejected."""
    with pytest.raises((ValueError, TypeError)):
        inventory_service.get_stock(invalid_id, "SKU-MILK-AMUL")

    with pytest.raises((ValueError, TypeError)):
        inventory_service.reserve_stock(
            "store_koramangala", invalid_id, "SKU-MILK-AMUL", 2
        )


# ---------------------------------------------------------------------------
# 9. Substitution Candidates Tests
# ---------------------------------------------------------------------------


def test_substitution_candidates_live_stock_and_exclusions(
    inventory_service, sample_store
):
    """
    get_substitution_candidates returns catalog Products with positive live stock,
    excluding the requested original SKU and zero-stock products.
    """
    candidates = inventory_service.get_substitution_candidates(
        store_id="store_koramangala",
        original_sku="SKU-MILK-AMUL",
    )

    candidate_skus = {c.sku for c in candidates}

    # Original SKU must be excluded
    assert "SKU-MILK-AMUL" not in candidate_skus

    # Zero stock product (SKU-MILK-MD) must be excluded
    assert "SKU-MILK-MD" not in candidate_skus

    # Positive stock products are included
    assert "SKU-MILK-NANDINI" in candidate_skus
    assert "SKU-BREAD-WHEAT" in candidate_skus

    # Returned items are instances of Product
    assert all(isinstance(c, Product) for c in candidates)


def test_substitution_candidates_reflect_live_stock_changes(
    inventory_service, sample_store
):
    """When a candidate's stock is fully reserved, it drops out of candidates."""
    initial_candidates = inventory_service.get_substitution_candidates(
        "store_koramangala", "SKU-MILK-AMUL"
    )
    assert any(c.sku == "SKU-MILK-NANDINI" for c in initial_candidates)

    # Reserve all 5 units of Nandini milk
    inventory_service.reserve_stock(
        "store_koramangala", "ord_sub_res", "SKU-MILK-NANDINI", 5
    )
    assert sample_store.inventory["SKU-MILK-NANDINI"] == 0

    # Live lookup now excludes SKU-MILK-NANDINI
    updated_candidates = inventory_service.get_substitution_candidates(
        "store_koramangala", "SKU-MILK-AMUL"
    )
    updated_skus = {c.sku for c in updated_candidates}
    assert "SKU-MILK-NANDINI" not in updated_skus
