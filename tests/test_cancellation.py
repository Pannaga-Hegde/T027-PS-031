from datetime import datetime, time, timedelta
from decimal import Decimal
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.product import Product
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store
from ps031.services.batching_service import BatchingService
from ps031.services.cancellation_service import (
    CancellationOperationState,
    CancellationResult,
    CancellationService,
    CancellationServiceInterface,
    RefundRecord,
)
from ps031.services.inventory_service import (
    InventoryService,
    Reservation,
    ReservationStatus,
)
from ps031.services.lifecycle import LifecycleTransitionService


def make_test_store(store_id: str = "STORE-01") -> Store:
    return Store(
        store_id=store_id,
        name="Indiranagar Dark Store",
        location=Coordinates(latitude=12.9716, longitude=77.5946),
        operating_hours=OperatingHours(
            open_time=time(6, 0),
            close_time=time(23, 0),
        ),
        picking_capacity=12,
        active_riders=5,
        inventory={
            "SKU-MILK-AMUL": 20,
            "SKU-MILK-NANDINI": 20,
            "SKU-BREAD-WHEAT": 20,
            "SKU-EGGS-6PK": 20,
        },
    )


def make_test_products() -> list[Product]:
    return [
        Product(
            sku="SKU-MILK-AMUL",
            name="Amul Fresh Milk 1L",
            category="Dairy",
            brand="Amul",
            price=Decimal("60.00"),
        ),
        Product(
            sku="SKU-MILK-NANDINI",
            name="Nandini Fresh Milk 1L",
            category="Dairy",
            brand="Nandini",
            price=Decimal("55.00"),
        ),
        Product(
            sku="SKU-MILK-AMUL-GOLD",
            name="Amul Gold Milk 1L",
            category="Dairy",
            brand="Amul",
            price=Decimal("66.00"),
        ),
        Product(
            sku="SKU-BREAD-WHEAT",
            name="Brown Bread 400g",
            category="Bakery",
            brand="Modern",
            price=Decimal("40.00"),
        ),
        Product(
            sku="SKU-EGGS-6PK",
            name="Farm Eggs 6pk",
            category="Poultry",
            brand="Eggoz",
            price=Decimal("50.00"),
        ),
    ]


def make_test_order(
    order_id: str = "ORD-01",
    customer_id: str = "CUST-01",
    order_time: datetime | None = None,
    status: OrderState = OrderState.RECEIVED,
    substitution_policy: SubstitutionPolicy = SubstitutionPolicy.ALLOW,
    delivery_fee: Decimal = Decimal("25.00"),
    surge_fee: Decimal = Decimal("10.00"),
    items: list[OrderItem] | None = None,
    promised_eta: datetime | None = None,
    allocated_store_id: str = "STORE-01",
) -> Order:
    if items is None:
        items = [
            OrderItem(
                product_id="SKU-MILK-AMUL",
                quantity=2,
                unit_price=Decimal("60.00"),
                category="Dairy",
                brand="Amul",
                fulfilled_quantity=2,
            )
        ]
    t0 = order_time or datetime(2026, 11, 4, 10, 0)
    eta = promised_eta or (t0 + timedelta(minutes=3))
    return Order(
        order_id=order_id,
        customer_id=customer_id,
        items=items,
        order_time=t0,
        status=status,
        allocated_store_id=allocated_store_id,
        promised_eta=eta,
        substitution_policy=substitution_policy,
        delivery_fee=delivery_fee,
        surge_fee=surge_fee,
        delivery_location=Coordinates(latitude=12.9730, longitude=77.5950),
    )


def make_test_rider(rider_id: str = "RIDER-01") -> Rider:
    return Rider(
        rider_id=rider_id,
        name="Test Rider",
        status=RiderStatus.AVAILABLE,
    )


class TestCancellationBasicsAndProtocol:
    def test_protocol_conformance(self) -> None:
        service = CancellationService()
        assert isinstance(service, CancellationServiceInterface)

    def test_free_cancellation_before_picking_received(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.RECEIVED)
        assert order.original_total_value == Decimal("120.00")
        assert order.total_delivery_fee == Decimal("35.00")

        res = service.cancel_order(order, reason="Changed mind")
        assert res.cancellation_fee == Decimal("0.00")
        assert res.refund_amount == Decimal("155.00")
        assert res.timing_stage == "PRE_PICKING"
        assert res.status == OrderState.CANCELLED
        assert order.status == OrderState.CANCELLED
        assert order.cancellation_fee == Decimal("0.00")
        assert order.refund_amount == Decimal("155.00")

    def test_free_cancellation_before_picking_allocated(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.ALLOCATED)
        res = service.cancel_order(order, reservation_ids=(), reason="User cancel")
        assert res.cancellation_fee == Decimal("0.00")
        assert res.refund_amount == Decimal("155.00")
        assert res.timing_stage == "PRE_PICKING"
        assert res.status == OrderState.CANCELLED

    def test_fee_during_picking_state(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.PICKING)
        res = service.cancel_order(order, reservation_ids=(), reason="Late order")
        assert res.cancellation_fee == Decimal("20.00")
        assert res.refund_amount == Decimal("135.00")  # 155 - 20
        assert res.timing_stage == "PICKING_PRE_DISPATCH"
        assert res.status == OrderState.CANCELLED
        assert order.cancellation_fee == Decimal("20.00")
        assert order.refund_amount == Decimal("135.00")

    def test_fee_during_ready_for_dispatch_state(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.READY_FOR_DISPATCH)
        res = service.cancel_order(order, reservation_ids=(), reason="No longer needed")
        assert res.cancellation_fee == Decimal("20.00")
        assert res.refund_amount == Decimal("135.00")
        assert res.timing_stage == "PICKING_PRE_DISPATCH"
        assert res.status == OrderState.CANCELLED

    def test_fee_when_cancelled_at_exceeds_picking_start(self) -> None:
        """When an explicit picking_start is provided and cancelled_at >= picking_start, fee is charged even if order state transition was pending."""
        service = CancellationService()
        t0 = datetime(2026, 11, 4, 10, 0)
        order = make_test_order(status=OrderState.ALLOCATED, order_time=t0)
        t_pick = t0 + timedelta(minutes=5)
        t_cancel = t0 + timedelta(minutes=6)

        res = service.cancel_order(
            order,
            cancelled_at=t_cancel,
            picking_start=t_pick,
            reservation_ids=(),
            reason="Cancelled after slot started",
        )
        assert res.cancellation_fee == Decimal("20.00")
        assert res.refund_amount == Decimal("135.00")
        assert res.timing_stage == "PICKING_PRE_DISPATCH"


class TestExactRefundCalculations:
    def test_exact_refund_calculation_with_delivery_and_surge_fee(self) -> None:
        service = CancellationService()
        order = make_test_order(
            status=OrderState.RECEIVED,
            delivery_fee=Decimal("40.00"),
            surge_fee=Decimal("15.50"),
        )
        # items: 2 * 60 = 120.00; total = 120 + 40 + 15.50 = 175.50
        fee, refund = service.calculate_refund(order)
        assert fee == Decimal("0.00")
        assert refund == Decimal("175.50")

    def test_exact_refund_with_amount_paid_override(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.PICKING)
        # Explicit amount_paid = 200.00, fee = 20.00 -> refund = 180.00
        fee, refund = service.calculate_refund(
            order, amount_paid=Decimal("200.00")
        )
        assert fee == Decimal("20.00")
        assert refund == Decimal("180.00")

    def test_collected_amount_less_than_cancellation_fee(self) -> None:
        """If collected amount is less than ₹20, refund is ₹0 (never negative)."""
        service = CancellationService()
        # Item total = 10.00, delivery fee = 0
        items = [
            OrderItem(
                product_id="SKU-CHOC",
                quantity=1,
                unit_price=Decimal("10.00"),
                category="Snacks",
                brand="Cadbury",
                fulfilled_quantity=1,
            )
        ]
        order = make_test_order(
            status=OrderState.PICKING,
            delivery_fee=Decimal("0.00"),
            surge_fee=Decimal("0.00"),
            items=items,
        )
        fee, refund = service.calculate_refund(order)
        assert fee == Decimal("20.00")
        assert refund == Decimal("0.00")


class TestPostDispatchSubstitutions:
    def test_post_dispatch_allow_substitution_returns_zero_refund(self) -> None:
        service = CancellationService()
        # Order with substituted item under ALLOW policy
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=2,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=2,
            substituted_product_sku="SKU-MILK-NANDINI",
            substituted_quantity=2,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.ALLOW,
            items=[item],
        )
        res = service.cancel_order(order, reason="Post-dispatch cancel")
        assert res.cancellation_fee == Decimal("0.00")
        assert res.refund_amount == Decimal("0.00")
        assert res.timing_stage == "POST_DISPATCH"

    def test_post_dispatch_none_substitution_refunds_substituted_quantities(
        self,
    ) -> None:
        service = CancellationService()
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=2,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=2,
            substituted_product_sku="SKU-MILK-NANDINI",
            substituted_quantity=2,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.NONE,
            items=[item],
        )
        res = service.cancel_order(order, reason="Post-dispatch cancel unconsented")
        assert res.cancellation_fee == Decimal("0.00")
        # 2 units * 60.00 = 120.00
        assert res.refund_amount == Decimal("120.00")
        assert order.refund_amount == Decimal("120.00")

    def test_post_dispatch_similar_only_compliant_substitution_returns_zero_refund(
        self,
    ) -> None:
        products = {p.sku: p for p in make_test_products()}
        service = CancellationService(product_catalog=products)
        # Amul Gold Milk has same category (Dairy), same brand (Amul), price 66 <= 60 * 1.15 = 69
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=2,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=2,
            substituted_product_sku="SKU-MILK-AMUL-GOLD",
            substituted_quantity=2,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.SIMILAR_ONLY,
            items=[item],
        )
        res = service.cancel_order(order, reason="Post-dispatch cancel similar")
        assert res.cancellation_fee == Decimal("0.00")
        assert res.refund_amount == Decimal("0.00")

    def test_post_dispatch_similar_only_violating_brand_refunded(self) -> None:
        products = {p.sku: p for p in make_test_products()}
        service = CancellationService(product_catalog=products)
        # Nandini Milk has different brand (Nandini != Amul) -> violates SIMILAR_ONLY
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=2,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=2,
            substituted_product_sku="SKU-MILK-NANDINI",
            substituted_quantity=2,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.SIMILAR_ONLY,
            items=[item],
        )
        res = service.cancel_order(order, reason="Post-dispatch brand violation")
        assert res.cancellation_fee == Decimal("0.00")
        assert res.refund_amount == Decimal("120.00")

    def test_post_dispatch_similar_only_violating_category_refunded(self) -> None:
        products = {p.sku: p for p in make_test_products()}
        service = CancellationService(product_catalog=products)
        # Bread has different category (Bakery != Dairy) -> violates SIMILAR_ONLY
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=1,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=1,
            substituted_product_sku="SKU-BREAD-WHEAT",
            substituted_quantity=1,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.SIMILAR_ONLY,
            items=[item],
        )
        res = service.cancel_order(order, reason="Post-dispatch category violation")
        assert res.refund_amount == Decimal("60.00")

    def test_post_dispatch_similar_only_missing_catalog_metadata_presumes_compliance(
        self,
    ) -> None:
        """When catalog metadata is missing, SIMILAR_ONLY substitutions presume compliance (₹0 refund)."""
        service = CancellationService(product_catalog={})
        item = OrderItem(
            product_id="SKU-MILK-AMUL",
            quantity=2,
            unit_price=Decimal("60.00"),
            category="Dairy",
            brand="Amul",
            fulfilled_quantity=2,
            substituted_product_sku="SKU-UNKNOWN-SUB",
            substituted_quantity=2,
        )
        order = make_test_order(
            status=OrderState.DISPATCHED,
            substitution_policy=SubstitutionPolicy.SIMILAR_ONLY,
            items=[item],
        )
        res = service.cancel_order(order, reason="Missing catalog")
        assert res.refund_amount == Decimal("0.00")


class TestInventoryReservationHandling:
    def test_reservation_auto_discovery_via_inventory_service(self) -> None:
        store = make_test_store()
        products = make_test_products()
        inv_service = InventoryService(stores=[store], products=products)
        service = CancellationService(inventory_service=inv_service)

        # Create reservations
        r1 = inv_service.reserve_stock(store.store_id, "ORD-AUTO", "SKU-MILK-AMUL", 2)
        r2 = inv_service.reserve_stock(store.store_id, "ORD-AUTO", "SKU-BREAD-WHEAT", 3)
        assert store.inventory["SKU-MILK-AMUL"] == 18
        assert store.inventory["SKU-BREAD-WHEAT"] == 17

        order = make_test_order(order_id="ORD-AUTO", status=OrderState.ALLOCATED)
        res = service.cancel_order(order)
        assert set(res.released_reservations) == {r1.reservation_id, r2.reservation_id}
        assert store.inventory["SKU-MILK-AMUL"] == 20
        assert store.inventory["SKU-BREAD-WHEAT"] == 20

    def test_explicit_empty_reservation_list_accepted(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)
        order = make_test_order(status=OrderState.ALLOCATED)

        res = service.cancel_order(order, reservation_ids=())
        assert res.released_reservations == ()
        assert res.skipped_reservations == ()

    def test_anti_silent_skip_raises_value_error_when_no_reservations_found(
        self,
    ) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)
        order = make_test_order(status=OrderState.ALLOCATED)

        with pytest.raises(
            ValueError,
            match="inventory service holds no active reservations and none were provided",
        ):
            service.cancel_order(order)

    def test_pending_reservation_release_restores_store_stock(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        r = inv_service.reserve_stock(store.store_id, "ORD-01", "SKU-MILK-AMUL", 5)
        assert store.inventory["SKU-MILK-AMUL"] == 15

        order = make_test_order(status=OrderState.PICKING)
        res = service.cancel_order(order, reservation_ids=[r.reservation_id])
        assert res.released_reservations == (r.reservation_id,)
        assert store.inventory["SKU-MILK-AMUL"] == 20

    def test_already_released_reservation_skipped(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        r = inv_service.reserve_stock(store.store_id, "ORD-01", "SKU-MILK-AMUL", 5)
        inv_service.release_reservation(r.reservation_id)
        assert store.inventory["SKU-MILK-AMUL"] == 20

        order = make_test_order(status=OrderState.PICKING)
        res = service.cancel_order(order, reservation_ids=[r.reservation_id])
        assert res.released_reservations == ()
        assert res.skipped_reservations == ((r.reservation_id, "ALREADY_RELEASED"),)
        # Stock not restored twice
        assert store.inventory["SKU-MILK-AMUL"] == 20

    def test_committed_reservation_skipped(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        r = inv_service.reserve_stock(store.store_id, "ORD-01", "SKU-MILK-AMUL", 5)
        inv_service.commit_reservation(r.reservation_id)
        assert store.inventory["SKU-MILK-AMUL"] == 15

        order = make_test_order(status=OrderState.PICKING)
        res = service.cancel_order(order, reservation_ids=[r.reservation_id])
        assert res.released_reservations == ()
        assert res.skipped_reservations == ((r.reservation_id, "COMMITTED"),)
        assert store.inventory["SKU-MILK-AMUL"] == 15

    def test_unknown_reservation_id_skipped(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        order = make_test_order(status=OrderState.PICKING)
        res = service.cancel_order(order, reservation_ids=["res_nonexistent_999"])
        assert res.released_reservations == ()
        assert res.skipped_reservations == (("res_nonexistent_999", "UNKNOWN"),)

    def test_post_dispatch_does_not_release_reservations(self) -> None:
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        r = inv_service.reserve_stock(store.store_id, "ORD-01", "SKU-MILK-AMUL", 5)
        order = make_test_order(status=OrderState.DISPATCHED)

        res = service.cancel_order(order, reservation_ids=[r.reservation_id])
        assert res.released_reservations == ()
        # Stock untouched
        assert store.inventory["SKU-MILK-AMUL"] == 15


class TestBatchingAndRiderInteractions:
    def test_pre_dispatch_batch_cancellation(self) -> None:
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        batching = BatchingService()
        service = CancellationService(batching_service=batching)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = batching.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        batching.add_order_to_batch(batch, o1, store, ready_at=t0)
        assert len(batch.orders) == 1
        assert rider.status == RiderStatus.AVAILABLE

        res = service.cancel_order(o1, batch=batch, reservation_ids=())
        assert res.batch_id == "BATCH-01"
        assert res.rider_released is True
        assert len(batch.orders) == 0
        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE

    def test_post_dispatch_multi_order_batch_keeps_rider_busy(self) -> None:
        store = make_test_store()
        rider = make_test_rider("RIDER-01")
        batching = BatchingService()
        service = CancellationService(batching_service=batching)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = batching.create_batch("BATCH-01", store, rider, created_at=t0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        o2 = make_test_order("ORD-02", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        batching.add_order_to_batch(batch, o1, store, ready_at=t0)
        batching.add_order_to_batch(batch, o2, store, ready_at=t0)

        batching.dispatch_batch(
            batch, store, rider, dispatched_at=t0 + timedelta(minutes=3)
        )
        assert rider.status == RiderStatus.BUSY
        assert batch.status == BatchStatus.DISPATCHED

        # Cancel O1 post-dispatch
        res1 = service.cancel_order(o1, batch=batch, reservation_ids=())
        assert res1.rider_released is False
        assert o1.status == OrderState.CANCELLED
        assert o2.status == OrderState.DISPATCHED
        assert batch.status == BatchStatus.DISPATCHED
        assert rider.status == RiderStatus.BUSY

        # Cancel O2 post-dispatch -> now all orders terminal -> batch closes, rider released
        res2 = service.cancel_order(o2, batch=batch, reservation_ids=())
        assert res2.rider_released is True
        assert o2.status == OrderState.CANCELLED
        assert batch.status == BatchStatus.CANCELLED
        assert rider.status == RiderStatus.AVAILABLE


class TestOperationalStateAndRetries:
    def test_object_identity_validation_rejects_different_order_instance(
        self,
    ) -> None:
        service = CancellationService()
        o1 = make_test_order("ORD-01", status=OrderState.RECEIVED)
        service.cancel_order(o1)

        diff_o1 = make_test_order("ORD-01", status=OrderState.RECEIVED)
        assert diff_o1 is not o1
        with pytest.raises(
            ValueError,
            match="Provided order object does not match the order object previously registered",
        ):
            service.cancel_order(diff_o1)

    def test_changed_request_fingerprint_rejection(self) -> None:
        service = CancellationService()
        store = make_test_store()
        rider = make_test_rider()
        batch1 = Batch("BATCH-01", rider.rider_id, datetime.now())
        batch2 = Batch("BATCH-02", rider.rider_id, datetime.now())

        o1 = make_test_order("ORD-01", status=OrderState.RECEIVED)
        service.cancel_order(
            o1,
            batch=batch1,
            amount_paid=Decimal("100.00"),
            reservation_ids=(),
            reason="Initial",
        )

        # 1. Changed amount_paid
        with pytest.raises(ValueError, match="altered amount_paid"):
            service.cancel_order(
                o1,
                batch=batch1,
                amount_paid=Decimal("150.00"),
                reservation_ids=(),
                reason="Initial",
            )

        # 2. Changed batch
        with pytest.raises(ValueError, match="batch object previously registered"):
            service.cancel_order(
                o1,
                batch=batch2,
                amount_paid=Decimal("100.00"),
                reservation_ids=(),
                reason="Initial",
            )

        # 3. Changed reservation_ids
        with pytest.raises(ValueError, match="altered reservation IDs"):
            service.cancel_order(
                o1,
                batch=batch1,
                amount_paid=Decimal("100.00"),
                reservation_ids=["res_diff"],
                reason="Initial",
            )

        # 4. Changed reason
        with pytest.raises(ValueError, match="altered cancellation reason"):
            service.cancel_order(
                o1,
                batch=batch1,
                amount_paid=Decimal("100.00"),
                reservation_ids=(),
                reason="Different reason",
            )

    def test_timestamp_validation_and_retry_consistency(self) -> None:
        service = CancellationService()
        t0 = datetime(2026, 11, 4, 10, 0)
        o1 = make_test_order("ORD-01", order_time=t0, status=OrderState.RECEIVED)

        # cancelled_at earlier than order_time rejected
        with pytest.raises(ValueError, match="cancelled_at cannot be earlier"):
            service.cancel_order(o1, cancelled_at=t0 - timedelta(minutes=1))

        # Initial call
        t_cancel = t0 + timedelta(minutes=5)
        t_pick = t0 + timedelta(minutes=2)
        service.cancel_order(
            o1, cancelled_at=t_cancel, picking_start=t_pick, reservation_ids=()
        )

        # Omitting timestamps on retry reuses original
        res_retry = service.cancel_order(o1, reservation_ids=())
        assert res_retry.is_idempotent is True

        # Altering cancelled_at rejected
        with pytest.raises(ValueError, match="altered cancelled_at timestamp"):
            service.cancel_order(
                o1,
                cancelled_at=t_cancel + timedelta(minutes=1),
                reservation_ids=(),
            )

        # Altering picking_start rejected
        with pytest.raises(ValueError, match="altered picking_start timestamp"):
            service.cancel_order(
                o1,
                cancelled_at=t_cancel,
                picking_start=t_pick + timedelta(minutes=1),
                reservation_ids=(),
            )

    def test_repeated_successful_cancellation_is_idempotent(self) -> None:
        service = CancellationService()
        o1 = make_test_order("ORD-01", status=OrderState.RECEIVED)
        res1 = service.cancel_order(o1, reservation_ids=())
        assert res1.is_idempotent is False

        # Repeated call returns cached result with is_idempotent=True
        res2 = service.cancel_order(o1, reservation_ids=())
        assert res2.is_idempotent is True
        assert res2.refund_amount == res1.refund_amount
        assert res2.cancellation_fee == res1.cancellation_fee

    def test_delivered_and_rejected_order_cancellation_rejected(self) -> None:
        service = CancellationService()
        o_del = make_test_order("ORD-DEL", status=OrderState.DELIVERED)
        with pytest.raises(ValueError, match="terminal status 'DELIVERED'"):
            service.cancel_order(o_del)

        o_rej = make_test_order("ORD-REJ", status=OrderState.REJECTED)
        with pytest.raises(ValueError, match="terminal status 'REJECTED'"):
            service.cancel_order(o_rej)

    def test_currency_validation_and_two_decimal_precision(self) -> None:
        service = CancellationService()
        order = make_test_order(status=OrderState.RECEIVED)

        # Reject booleans as amount_paid
        with pytest.raises(TypeError, match="must be a Decimal instance"):
            service.cancel_order(order, amount_paid=True)

        # Reject non-Decimal
        with pytest.raises(TypeError, match="must be a Decimal instance"):
            service.cancel_order(order, amount_paid=100.50)

        # Reject negative
        with pytest.raises(ValueError, match="cannot be negative"):
            service.cancel_order(order, amount_paid=Decimal("-10.00"))

        # Reject non-finite
        with pytest.raises(ValueError, match="must be a finite number"):
            service.cancel_order(order, amount_paid=Decimal("NaN"))


class TestFailureAndRetryResumption:
    def test_partial_reservation_release_failure_and_retry(self) -> None:
        """Failure during reservation release preserves refund and resumes without double release."""
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        service = CancellationService(inventory_service=inv_service)

        r1 = inv_service.reserve_stock(store.store_id, "ORD-FAIL", "SKU-MILK-AMUL", 2)
        r2 = inv_service.reserve_stock(store.store_id, "ORD-FAIL", "SKU-BREAD-WHEAT", 2)
        assert store.inventory["SKU-MILK-AMUL"] == 18
        assert store.inventory["SKU-BREAD-WHEAT"] == 18

        order = make_test_order("ORD-FAIL", status=OrderState.ALLOCATED)

        # Determine which reservation is processed first (by normalized sorting)
        first_id, second_id = sorted([r1.reservation_id, r2.reservation_id])
        first_sku = "SKU-MILK-AMUL" if first_id == r1.reservation_id else "SKU-BREAD-WHEAT"
        second_sku = "SKU-BREAD-WHEAT" if first_sku == "SKU-MILK-AMUL" else "SKU-MILK-AMUL"

        # Simulate failure when releasing the second reservation
        orig_release = inv_service.release_reservation

        def flaky_release(res_id: str):
            if res_id == second_id:
                raise RuntimeError("Simulated network timeout releasing second reservation")
            return orig_release(res_id)

        inv_service.release_reservation = flaky_release

        with pytest.raises(RuntimeError, match="Simulated network timeout"):
            service.cancel_order(order, reservation_ids=[r1.reservation_id, r2.reservation_id])

        # Step 3 (refund recorded) is preserved
        assert service.get_refund("ORD-FAIL") == Decimal("155.00")
        assert order.refund_amount == Decimal("155.00")
        # First reservation was released, stock restored
        assert store.inventory[first_sku] == 20
        # Second reservation was not yet released
        assert store.inventory[second_sku] == 18
        # Operation state is in progress
        op = service._operation_state["ORD-FAIL"]
        assert op.status == "IN_PROGRESS"
        assert first_id in op.released_reservations

        # Fix inventory service and retry
        inv_service.release_reservation = orig_release
        res = service.cancel_order(order, reservation_ids=[r1.reservation_id, r2.reservation_id])

        assert res.status == OrderState.CANCELLED
        # Both reservations released
        assert set(res.released_reservations) == {r1.reservation_id, r2.reservation_id}
        # First reservation stock not restored twice
        assert store.inventory[first_sku] == 20
        assert store.inventory[second_sku] == 20
        assert op.status == "COMPLETED"

    def test_lifecycle_failure_and_retry(self) -> None:
        """Failure during lifecycle transition preserves refund and reservations."""
        store = make_test_store()
        inv_service = InventoryService(stores=[store])
        lifecycle = LifecycleTransitionService()
        service = CancellationService(
            inventory_service=inv_service, lifecycle_service=lifecycle
        )

        r1 = inv_service.reserve_stock(store.store_id, "ORD-LC", "SKU-MILK-AMUL", 2)
        order = make_test_order("ORD-LC", status=OrderState.ALLOCATED)

        # Simulate failure in lifecycle transition
        orig_transition = lifecycle.transition

        def flaky_transition(ord_obj, target):
            raise RuntimeError("Simulated DB error during state transition")

        lifecycle.transition = flaky_transition

        with pytest.raises(RuntimeError, match="Simulated DB error"):
            service.cancel_order(order, reservation_ids=[r1.reservation_id])

        # Refund preserved
        assert service.get_refund("ORD-LC") == Decimal("155.00")
        # Reservation release preserved
        assert store.inventory["SKU-MILK-AMUL"] == 20
        assert r1.reservation_id in service._operation_state["ORD-LC"].released_reservations

        # Restore transition and retry
        lifecycle.transition = orig_transition
        res = service.cancel_order(order, reservation_ids=[r1.reservation_id])
        assert res.status == OrderState.CANCELLED
        assert order.status == OrderState.CANCELLED
        # Stock not restored twice
        assert store.inventory["SKU-MILK-AMUL"] == 20

    def test_batch_update_failure_and_retry(self) -> None:
        """Failure during batch update preserves refund, reservations, and lifecycle."""
        store = make_test_store()
        rider = make_test_rider()
        batching = BatchingService()
        service = CancellationService(batching_service=batching)
        t0 = datetime(2026, 11, 4, 10, 0)

        batch = batching.create_batch("BATCH-FAIL", store, rider, created_at=t0)
        order = make_test_order("ORD-BATCH", order_time=t0, status=OrderState.READY_FOR_DISPATCH)
        batching.add_order_to_batch(batch, order, store, ready_at=t0)

        orig_cancel = batching.cancel_order_in_batch

        def flaky_batch_cancel(b, o, reason=""):
            raise RuntimeError("Simulated concurrency error updating batch")

        batching.cancel_order_in_batch = flaky_batch_cancel

        with pytest.raises(RuntimeError, match="Simulated concurrency error"):
            service.cancel_order(order, batch=batch, reservation_ids=())

        # Refund preserved
        assert service.get_refund("ORD-BATCH") == Decimal("135.00")
        # Order already transitioned to CANCELLED
        assert order.status == OrderState.CANCELLED
        op = service._operation_state["ORD-BATCH"]
        assert op.lifecycle_completed is True
        assert op.batch_cancelled is False

        # Restore batching and retry
        batching.cancel_order_in_batch = orig_cancel
        res = service.cancel_order(order, batch=batch, reservation_ids=())
        assert res.status == OrderState.CANCELLED
        assert res.batch_id == "BATCH-FAIL"
        assert res.rider_released is True
        assert batch.status == BatchStatus.CANCELLED
