"""Application orchestrator coordinating PS-031 domain services and managing in-memory state.

Coordinates workflows across:
- Store and Product registration (InventoryService, PricingService)
- Rider registration and status tracking
- Order submission, allocation, pricing, and ETA scheduling (BR-01, BR-02, BR-03, BR-04, BR-07)
- Picking and fulfilment (BR-05, BR-06)
- Weather hold events and ETA propagation (BR-11)
- Rider batching, sequencing, and dispatch (BR-08)
- Progressive and batch delivery completion with SLA compensation (BR-09)
- Rider penalty and bonus evaluation (BR-10)
- Customer order cancellation and refunds (BR-12)
- FR-11 execution trace integration point (Issue #28)
"""

from collections.abc import Mapping, Sequence
import copy
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import sys
from typing import Any

from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.product import Product
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store
from ps031.services.allocation_service import AllocationService
from ps031.services.batching_service import BatchingService
from ps031.services.cancellation_service import CancellationService
from ps031.services.compensation_service import CompensationService
from ps031.services.fulfilment_orchestrator import FulfilmentOrchestrator
from ps031.services.fulfilment_service import FulfilmentService
from ps031.services.inventory_service import InventoryService, ReservationStatus
from ps031.services.lifecycle import LifecycleTransitionService
from ps031.services.picking_service import PickingService
from ps031.services.pricing_service import PricingService
from ps031.services.rider_penalty_service import RiderPenaltyService
from ps031.services.substitution_service import SubstitutionService
from ps031.services.weather_hold_service import WeatherHoldService


class ReservationRollbackError(RuntimeError):
    """Raised when one or more stock reservations fail to release during order submission rollback."""

    def __init__(
        self,
        primary_error: Exception,
        rollback_failures: list[tuple[str, Exception]],
        order_id: str,
    ) -> None:
        self.primary_error = primary_error
        self.rollback_failures = rollback_failures
        self.order_id = order_id
        failed_res_ids = [fid for fid, _ in rollback_failures]
        super().__init__(
            f"Failed to roll back reservation(s) {failed_res_ids} for order '{order_id}' "
            f"after primary error: {primary_error}"
        )


class ApplicationOrchestrator:
    """Central orchestrator managing in-memory application state and domain service workflows."""

    def __init__(
        self,
        inventory_service: InventoryService | None = None,
        allocation_service: AllocationService | None = None,
        picking_service: PickingService | None = None,
        pricing_service: PricingService | None = None,
        substitution_service: SubstitutionService | None = None,
        fulfilment_service: FulfilmentService | None = None,
        fulfilment_orchestrator: FulfilmentOrchestrator | None = None,
        cancellation_service: CancellationService | None = None,
        batching_service: BatchingService | None = None,
        compensation_service: CompensationService | None = None,
        rider_penalty_service: RiderPenaltyService | None = None,
        weather_hold_service: WeatherHoldService | None = None,
        lifecycle_service: LifecycleTransitionService | None = None,
    ) -> None:
        self.lifecycle_service = lifecycle_service or LifecycleTransitionService()
        self.pricing_service = pricing_service or PricingService()
        self.inventory_service = inventory_service or InventoryService()
        self.allocation_service = allocation_service or AllocationService(
            lifecycle_service=self.lifecycle_service,
            pricing_service=self.pricing_service,
        )
        self.picking_service = picking_service or PickingService()
        self.substitution_service = substitution_service or SubstitutionService()
        self.fulfilment_service = fulfilment_service or FulfilmentService()
        self.cancellation_service = cancellation_service or CancellationService(
            inventory_service=self.inventory_service
        )
        self.fulfilment_orchestrator = fulfilment_orchestrator or FulfilmentOrchestrator(
            inventory_service=self.inventory_service,
            substitution_service=self.substitution_service,
            fulfilment_service=self.fulfilment_service,
        )
        self.batching_service = batching_service or BatchingService(
            picking_service=self.picking_service
        )
        self.compensation_service = compensation_service or CompensationService()
        self.rider_penalty_service = rider_penalty_service or RiderPenaltyService()
        self.weather_hold_service = weather_hold_service or WeatherHoldService()

        # In-memory registries
        self.products: dict[str, Product] = {}
        self.stores: dict[str, Store] = {}
        self.riders: dict[str, Rider] = {}
        self.orders: dict[str, Order] = {}
        self.batches: dict[str, Batch] = {}
        self.batch_stores: dict[str, str] = {}
        self.order_events: dict[str, list[dict[str, Any]]] = {}

    # -----------------------------------------------------------------------
    # Trace & Observability Helpers (Issue #28)
    # -----------------------------------------------------------------------

    @classmethod
    def _serialize_trace_detail(cls, val: Any) -> Any:
        """Recursively serialize event detail values to safe JSON-serializable primitives."""
        if val is None or isinstance(val, (str, int, float, bool)):
            return val
        if isinstance(val, Decimal):
            return f"{val:.2f}"
        if isinstance(val, (datetime, date, time)):
            return val.isoformat()
        if isinstance(val, Enum):
            return val.value
        if isinstance(val, Mapping):
            return {str(k): cls._serialize_trace_detail(v) for k, v in val.items()}
        if isinstance(val, set):
            try:
                sorted_items = sorted(val)
            except TypeError:
                sorted_items = sorted(val, key=lambda x: str(x))
            return [cls._serialize_trace_detail(item) for item in sorted_items]
        if isinstance(val, (list, tuple)):
            return [cls._serialize_trace_detail(item) for item in val]
        if hasattr(val, "__dict__"):
            return {
                str(k): cls._serialize_trace_detail(v)
                for k, v in val.__dict__.items()
                if not k.startswith("_")
            }
        return str(val)

    def _record_event(
        self,
        order_id: str,
        event_type: str,
        timestamp: str | datetime | None = None,
        business_rules: Sequence[str] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an immutable, structured trace event for an order."""
        if order_id not in self.order_events:
            self.order_events[order_id] = []

        history = self.order_events[order_id]
        sequence = len(history) + 1

        if isinstance(timestamp, (datetime, date, time)):
            ts_str = timestamp.isoformat()
        elif isinstance(timestamp, str) and timestamp.strip():
            ts_str = timestamp.strip()
        else:
            ts_str = datetime.now().isoformat()

        serialized_details = self._serialize_trace_detail(dict(details or {}))
        br_list = [str(r) for r in (business_rules or [])]

        event = {
            "sequence": sequence,
            "timestamp": ts_str,
            "event_type": str(event_type),
            "business_rules": br_list,
            "details": serialized_details,
        }
        history.append(event)
        return dict(event)

    # -----------------------------------------------------------------------
    # 1. Product & Catalog Registration
    # -----------------------------------------------------------------------

    def register_product(
        self,
        sku: str,
        name: str,
        category: str,
        brand: str,
        price: str | Decimal | float | int,
    ) -> dict[str, Any]:
        """Register a catalog product in the orchestrator and inventory service."""
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("Product SKU must be a non-empty string.")
        sku = sku.strip()
        if sku in self.products or self.inventory_service.has_product(sku):
            raise ValueError(f"Product with SKU '{sku}' is already registered.")

        dec_price = Decimal(str(price)) if not isinstance(price, Decimal) else price
        if dec_price < Decimal("0.00"):
            raise ValueError(f"Product price must be non-negative, got {dec_price}.")

        product = Product(
            sku=sku,
            name=name,
            category=category,
            brand=brand,
            price=dec_price,
        )
        inv_registered = False
        orch_registered = False
        try:
            self.inventory_service.register_product(product)
            inv_registered = True
            self.products[sku] = product
            orch_registered = True
        except Exception:
            if orch_registered:
                self.products.pop(sku, None)
            if inv_registered:
                self.inventory_service.unregister_product(sku)
            raise
        return {
            "sku": product.sku,
            "name": product.name,
            "category": product.category,
            "brand": product.brand,
            "price": product.price,
        }

    # -----------------------------------------------------------------------
    # 2. Store Registration
    # -----------------------------------------------------------------------

    def register_store(
        self,
        store_id: str,
        name: str,
        location: Mapping[str, Any],
        operating_hours: Mapping[str, Any],
        picking_capacity: int = 12,
        active_riders: int = 0,
        inventory: Mapping[str, int] | None = None,
    ) -> dict[str, Any]:
        """Register a dark store with coordinates, operating hours, and stock."""
        if not isinstance(store_id, str) or not store_id.strip():
            raise ValueError("store_id must be a non-empty string.")
        store_id = store_id.strip()
        if (
            store_id in self.stores
            or self.inventory_service.has_store(store_id)
            or self.pricing_service.has_store(store_id)
        ):
            raise ValueError(f"Store with ID '{store_id}' is already registered.")

        if not isinstance(location, Mapping):
            raise ValueError(f"location must be a dict, got {type(location).__name__}.")
        if "latitude" not in location or "longitude" not in location:
            raise ValueError("location must contain 'latitude' and 'longitude'.")

        coords = Coordinates(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
        )

        if not isinstance(operating_hours, Mapping):
            raise ValueError(
                f"operating_hours must be a dict, got {type(operating_hours).__name__}."
            )
        if "open_time" not in operating_hours or "close_time" not in operating_hours:
            raise ValueError("operating_hours must contain 'open_time' and 'close_time'.")

        t_open = time.fromisoformat(str(operating_hours["open_time"]))
        t_close = time.fromisoformat(str(operating_hours["close_time"]))
        hours = OperatingHours(open_time=t_open, close_time=t_close)

        inv = {str(k): int(v) for k, v in (inventory or {}).items()}

        store = Store(
            store_id=store_id,
            name=name,
            location=coords,
            operating_hours=hours,
            picking_capacity=int(picking_capacity),
            active_riders=int(active_riders),
            inventory=inv,
        )

        inv_registered = False
        pricing_registered = False
        orch_registered = False
        try:
            self.inventory_service.register_store(store)
            inv_registered = True
            self.pricing_service.register_store(store)
            pricing_registered = True
            self.stores[store_id] = store
            orch_registered = True
        except Exception:
            if orch_registered:
                self.stores.pop(store_id, None)
            if pricing_registered:
                self.pricing_service.unregister_store(store_id)
            if inv_registered:
                self.inventory_service.unregister_store(store_id)
            raise

        return {
            "store_id": store.store_id,
            "name": store.name,
            "picking_capacity": store.picking_capacity,
            "active_riders": store.active_riders,
            "inventory_sku_count": len(store.inventory),
        }

    # -----------------------------------------------------------------------
    # 3. Rider Registration
    # -----------------------------------------------------------------------

    def register_rider(
        self,
        rider_id: str,
        name: str,
        status: str | RiderStatus = RiderStatus.AVAILABLE,
        daily_penalty: str | Decimal | float | int = Decimal("0.00"),
        daily_penalty_date: str | date | None = None,
    ) -> dict[str, Any]:
        """Register a delivery rider in the orchestrator."""
        if not isinstance(rider_id, str) or not rider_id.strip():
            raise ValueError("rider_id must be a non-empty string.")
        rider_id = rider_id.strip()
        if rider_id in self.riders:
            raise ValueError(f"Rider with ID '{rider_id}' is already registered.")

        dec_penalty = (
            Decimal(str(daily_penalty))
            if not isinstance(daily_penalty, Decimal)
            else daily_penalty
        )
        p_date = (
            date.fromisoformat(str(daily_penalty_date))
            if isinstance(daily_penalty_date, str)
            else daily_penalty_date
        )

        rider = Rider(
            rider_id=rider_id,
            name=name,
            status=status,
            daily_penalty=dec_penalty,
            daily_penalty_date=p_date,
        )
        self.riders[rider_id] = rider
        return {
            "rider_id": rider.rider_id,
            "name": rider.name,
            "status": rider.status.value,
            "daily_penalty": rider.daily_penalty,
            "daily_penalty_date": rider.daily_penalty_date,
        }

    # -----------------------------------------------------------------------
    # 4. Order Submission & Allocation (BR-01, BR-02, BR-03, BR-04, BR-07)
    # -----------------------------------------------------------------------

    def submit_order(
        self,
        order_id: str,
        customer_id: str,
        customer_lat: float,
        customer_lon: float,
        items: Sequence[Mapping[str, Any]],
        substitution_preference: str | SubstitutionPolicy = "NONE",
        order_time: str | datetime | None = None,
        slow_delivery: bool = False,
    ) -> dict[str, Any]:
        """Submit and allocate a new customer order with atomic reservation safety."""
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("order_id must be a non-empty string.")
        order_id = order_id.strip()
        if order_id in self.orders:
            raise ValueError(f"Order with ID '{order_id}' has already been submitted.")

        if not items:
            raise ValueError("Order must contain at least one item.")

        order_time_dt = (
            datetime.fromisoformat(str(order_time))
            if isinstance(order_time, str)
            else (order_time or datetime.now())
        )

        # Parse and validate OrderItem lines
        order_items: list[OrderItem] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError(f"Item must be a dictionary, got {type(item).__name__}.")

            sku = str(item.get("sku_id") or item.get("sku") or item.get("product_id") or "").strip()
            if not sku:
                raise ValueError("Item is missing 'sku_id' or 'product_id'.")

            qty_val = item.get("quantity") or item.get("requested_qty")
            if qty_val is None:
                raise ValueError(f"Item '{sku}' is missing required 'quantity'.")
            try:
                qty = int(qty_val)
            except (ValueError, TypeError) as err:
                raise ValueError(f"Item '{sku}' quantity must be an integer, got '{qty_val}'.") from err

            if qty <= 0:
                raise ValueError(f"Item '{sku}' quantity must be positive, got {qty}.")

            # Auto-populate metadata from registered product if available
            catalog_prod = self.products.get(sku) or self.inventory_service.get_product(sku)
            if catalog_prod is None:
                raise ValueError(f"Product SKU '{sku}' is not registered in catalog.")

            cat = str(item.get("category") or catalog_prod.category)
            br = str(item.get("brand") or catalog_prod.brand)

            raw_price = item.get("unit_price") or item.get("price")
            if raw_price is not None:
                try:
                    u_price = Decimal(str(raw_price))
                except Exception as p_err:
                    raise ValueError(f"Item '{sku}' price '{raw_price}' is not a valid Decimal.") from p_err
            else:
                u_price = catalog_prod.price

            if u_price < Decimal("0.00"):
                raise ValueError(f"Item '{sku}' price must be non-negative, got {u_price}.")

            order_items.append(
                OrderItem(
                    product_id=sku,
                    quantity=qty,
                    unit_price=u_price,
                    category=cat,
                    brand=br,
                )
            )

        order = Order(
            order_id=order_id,
            customer_id=customer_id,
            customer_lat=float(customer_lat),
            customer_lon=float(customer_lon),
            order_time=order_time_dt,
            items=order_items,
            substitution_preference=substitution_preference,
            slow_delivery=slow_delivery,
        )

        # Evaluate Allocation (BR-01, BR-02)
        stores_list = list(self.stores.values())
        slot_loads = {
            s_id: self.picking_service.get_slot_load(s_id, order.order_time)
            for s_id in self.stores
        }

        alloc_result = self.allocation_service.allocate_store(
            order=order,
            stores=stores_list,
            slot_loads=slot_loads,
            reference_time=order.order_time,
            apply_lifecycle_transition=True,
        )

        if not alloc_result.is_allocated or alloc_result.selected_store is None:
            # Allocation failed: order transitioned to REJECTED by allocate_store
            self.orders[order_id] = order
            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_SUBMITTED",
                timestamp=order.order_time,
                business_rules=["BR-01", "BR-02"],
                details={
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "customer_lat": order.delivery_location.latitude,
                    "customer_lon": order.delivery_location.longitude,
                    "order_time": order.order_time.isoformat(),
                    "items": [
                        {
                            "sku_id": item.sku_id,
                            "quantity": item.requested_qty,
                            "unit_price": str(item.unit_price),
                            "category": item.category,
                            "brand": item.brand,
                        }
                        for item in order.items
                    ],
                    "order_value": str(order.original_total_value),
                    "status": "RECEIVED",
                },
            )
            self._record_event(
                order_id=order.order_id,
                event_type="ALLOCATION_REJECTED",
                timestamp=order.order_time,
                business_rules=["BR-01", "BR-02"],
                details={
                    "order_id": order.order_id,
                    "status": "REJECTED",
                    "rejection_reason": alloc_result.rejection_reason,
                    "evaluations": [
                        {
                            "store_id": e.store.store_id,
                            "distance_km": e.distance_km,
                            "is_open": e.is_open,
                            "is_weather_held": e.is_weather_held,
                            "is_serviceable": e.is_serviceable,
                            "has_100_percent_stock": e.has_100_percent_stock,
                            "line_fill_ratio": e.line_fill_ratio,
                            "current_slot_load": e.current_slot_load,
                        }
                        for e in alloc_result.evaluations
                    ],
                },
            )
            return {
                "order_id": order.order_id,
                "status": order.status.value,
                "allocated_store_id": None,
                "rejection_reason": alloc_result.rejection_reason,
            }

        # Allocation succeeded: create reservations with atomic compensation safety
        store = alloc_result.selected_store
        created_reservation_ids: list[str] = []

        try:
            for line in order.items:
                res = self.inventory_service.reserve_stock(
                    store_id=store.store_id,
                    order_id=order.order_id,
                    sku=line.sku_id,
                    quantity=line.requested_qty,
                )
                if res.reservation_id is not None:
                    created_reservation_ids.append(res.reservation_id)

            # Calculate picking capacity & promised ETA (BR-03, BR-04)
            pick_res, eta_res = self.picking_service.process_order_picking_and_eta(
                store=store,
                order=order,
                reference_time=order.order_time,
            )

            eval_entry = next(
                (e for e in alloc_result.evaluations if e.store.store_id == store.store_id),
                None,
            )
            dist_km = eval_entry.distance_km if eval_entry else 0.0

            # Commit order to orchestrator registry only after all operations succeed
            self.orders[order_id] = order

            # Record execution trace events (Issue #28)
            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_SUBMITTED",
                timestamp=order.order_time,
                business_rules=["BR-01", "BR-02", "BR-07"],
                details={
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "customer_lat": order.delivery_location.latitude,
                    "customer_lon": order.delivery_location.longitude,
                    "order_time": order.order_time.isoformat(),
                    "items": [
                        {
                            "sku_id": item.sku_id,
                            "quantity": item.requested_qty,
                            "unit_price": str(item.unit_price),
                            "category": item.category,
                            "brand": item.brand,
                        }
                        for item in order.items
                    ],
                    "order_value": str(order.original_total_value),
                    "status": "RECEIVED",
                },
            )

            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_ALLOCATED",
                timestamp=order.order_time,
                business_rules=["BR-01", "BR-02", "BR-07"],
                details={
                    "order_id": order.order_id,
                    "status": "ALLOCATED",
                    "allocated_store_id": store.store_id,
                    "store_name": store.name,
                    "distance_km": dist_km,
                    "delivery_fee": str(order.delivery_fee),
                    "surge_fee": str(order.surge_fee),
                    "pricing_frozen": order.pricing_frozen,
                    "evaluations": [
                        {
                            "store_id": e.store.store_id,
                            "distance_km": e.distance_km,
                            "is_serviceable": e.is_serviceable,
                            "has_100_percent_stock": e.has_100_percent_stock,
                            "line_fill_ratio": e.line_fill_ratio,
                            "current_slot_load": e.current_slot_load,
                        }
                        for e in alloc_result.evaluations
                    ],
                },
            )

            self._record_event(
                order_id=order.order_id,
                event_type="PICKING_SCHEDULED",
                timestamp=order.order_time,
                business_rules=["BR-03", "BR-04"],
                details={
                    "order_id": order.order_id,
                    "store_id": store.store_id,
                    "assigned_slot_start": pick_res.assigned_slot_start.isoformat(),
                    "overflow_count": pick_res.overflow_count,
                    "slot_delay_minutes": pick_res.slot_delay_minutes,
                    "promised_eta": eta_res.promised_eta.isoformat(),
                    "delayed_promise": eta_res.is_delayed_promise,
                    "picking_minutes": eta_res.picking_minutes,
                    "travel_minutes": eta_res.travel_minutes,
                },
            )

            return {
                "order_id": order.order_id,
                "status": order.status.value,
                "allocated_store_id": store.store_id,
                "distance_km": dist_km,
                "promised_eta": order.promised_eta.isoformat() if order.promised_eta else None,
                "delayed_promise": order.delayed_promise,
                "delivery_fee": order.delivery_fee,
                "surge_fee": order.surge_fee,
                "order_value": order.original_total_value,
            }

        except Exception as err:
            # 1. Roll back picking slot schedule if it was assigned
            try:
                self.picking_service.release_order_schedule(order_id)
            except Exception as pick_rb_err:
                sys.stderr.write(
                    f"Warning: Failed to roll back picking schedule for order '{order_id}': {pick_rb_err}\n"
                )

            # 2. Reset order lifecycle and allocation state
            order.status = OrderState.REJECTED
            order.allocated_store_id = None
            order.pricing_frozen = False
            order.delivery_fee = Decimal("0.00")
            order.surge_fee = Decimal("0.00")
            order.promised_eta = None
            order.delayed_promise = False

            # 3. Roll back all created reservations
            rollback_failures: list[tuple[str, Exception]] = []
            for res_id in reversed(created_reservation_ids):
                try:
                    self.inventory_service.release_reservation(res_id)
                except Exception as rel_err:
                    rollback_failures.append((res_id, rel_err))

            # 4. Ensure orphan order is not retained in registry and clear partial trace events
            self.orders.pop(order_id, None)
            self.order_events.pop(order_id, None)

            if rollback_failures:
                sys.stderr.write(
                    f"CRITICAL ROLLBACK FAILURE for order '{order_id}': {rollback_failures}\n"
                )
                raise ReservationRollbackError(
                    primary_error=err,
                    rollback_failures=rollback_failures,
                    order_id=order_id,
                ) from err

            raise err

    # -----------------------------------------------------------------------
    # 5. Picking & Fulfilment (BR-05, BR-06)
    # -----------------------------------------------------------------------

    def start_picking(
        self,
        order_id: str,
        started_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Transition order to PICKING status."""
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")
        order = self.orders[order_id]
        self.lifecycle_service.transition(order, OrderState.PICKING)

        started_dt = (
            datetime.fromisoformat(str(started_at))
            if isinstance(started_at, str)
            else (started_at or datetime.now())
        )

        self._record_event(
            order_id=order.order_id,
            event_type="PICKING_STARTED",
            timestamp=started_dt,
            business_rules=["BR-03", "BR-04"],
            details={
                "order_id": order.order_id,
                "status": "PICKING",
                "allocated_store_id": order.allocated_store_id,
            },
        )

        return {
            "order_id": order.order_id,
            "status": order.status.value,
        }

    def fulfil_order(
        self,
        order_id: str,
        amount_paid: str | Decimal | float | int | None = None,
        fulfilled_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Perform stock fulfillment, substitutions, and 60% cancellation check."""
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")
        order = self.orders[order_id]

        fulfil_dt = (
            datetime.fromisoformat(str(fulfilled_at))
            if isinstance(fulfilled_at, str)
            else (fulfilled_at or datetime.now())
        )

        reservations = [
            r
            for r in self.inventory_service.get_reservations_for_order(
                order.order_id, status=None
            )
            if r.status in (ReservationStatus.PENDING, ReservationStatus.COMMITTED)
        ]
        fulfil_res = self.fulfilment_orchestrator.fulfil(
            order=order, reservations=reservations
        )

        is_cancelled = fulfil_res.cancellation_result is not None or order.status == OrderState.CANCELLED
        if is_cancelled:
            # Order was cancelled under BR-06 (< 60% fulfilled value)
            refund = (
                fulfil_res.cancellation_result.refund_amount
                if fulfil_res.cancellation_result
                else order.refund_amount
            )
            existing_canc = [
                e
                for e in self.order_events.get(order.order_id, [])
                if e["event_type"] == "FULFILMENT_CANCELLED"
            ]
            if not existing_canc:
                self._record_event(
                    order_id=order.order_id,
                    event_type="FULFILMENT_CANCELLED",
                    timestamp=fulfil_dt,
                    business_rules=["BR-05", "BR-06"],
                    details={
                        "order_id": order.order_id,
                        "status": "CANCELLED",
                        "fulfilled_ratio": fulfil_res.evaluation.fulfilled_ratio,
                        "fulfilled_value": str(fulfil_res.evaluation.fulfilled_value),
                        "is_cancelled": True,
                        "refund_amount": str(refund),
                        "reason": "Fulfilment value below 60% threshold",
                    },
                )
            return {
                "order_id": order.order_id,
                "status": order.status.value,
                "fulfilled_ratio": fulfil_res.evaluation.fulfilled_ratio,
                "fulfilled_value": fulfil_res.evaluation.fulfilled_value,
                "is_cancelled": True,
                "refund_amount": refund,
                "delivery_fee_charged": False,
            }

        # Fulfilment passed threshold: commit applied reservations
        # 1. Pre-validate that all applied reservations exist and are committable
        for res_id in fulfil_res.applied_reservation_ids:
            res = self.inventory_service.get_reservation(res_id)
            if res.status == ReservationStatus.RELEASED:
                raise ValueError(
                    f"Cannot commit reservation '{res_id}' for order '{order_id}': already RELEASED."
                )

        # 2. Commit all applied reservations that are still PENDING without blanket swallow
        for res_id in fulfil_res.applied_reservation_ids:
            res = self.inventory_service.get_reservation(res_id)
            if res.status == ReservationStatus.PENDING:
                self.inventory_service.commit_reservation(res_id)

        # 3. Only transition to READY_FOR_DISPATCH after all commits succeed
        if order.status != OrderState.READY_FOR_DISPATCH:
            self.lifecycle_service.transition(order, OrderState.READY_FOR_DISPATCH)

        existing_fulfil = [
            e
            for e in self.order_events.get(order.order_id, [])
            if e["event_type"] == "ORDER_FULFILLED"
        ]
        if not existing_fulfil:
            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_FULFILLED",
                timestamp=fulfil_dt,
                business_rules=["BR-05", "BR-06"],
                details={
                    "order_id": order.order_id,
                    "status": order.status.value,
                    "fulfilled_ratio": fulfil_res.evaluation.fulfilled_ratio,
                    "fulfilled_value": str(fulfil_res.evaluation.fulfilled_value),
                    "is_cancelled": False,
                    "refund_amount": "0.00",
                    "delivery_fee_charged": True,
                    "substitutions": [
                        {
                            "original_sku": item.sku_id,
                            "substitute_sku": item.substituted_sku_id,
                            "quantity": item.substituted_quantity,
                        }
                        for item in order.items
                        if item.is_substituted
                    ],
                    "shortfalls": [
                        {
                            "sku": item.product_id,
                            "requested_qty": item.requested_qty,
                            "fulfilled_qty": item.fulfilled_quantity,
                        }
                        for item in order.items
                        if item.fulfilled_quantity < item.requested_qty
                    ],
                },
            )

        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "fulfilled_ratio": fulfil_res.evaluation.fulfilled_ratio,
            "fulfilled_value": fulfil_res.evaluation.fulfilled_value,
            "is_cancelled": False,
            "refund_amount": Decimal("0.00"),
            "delivery_fee_charged": True,
        }

    # -----------------------------------------------------------------------
    # 6. Weather Holds (BR-11)
    # -----------------------------------------------------------------------

    def apply_weather_hold(
        self,
        event_id: str,
        store_id: str,
        hold_start: str | datetime,
    ) -> dict[str, Any]:
        """Apply a 20-minute weather hold to store and extend ETAs by 10 minutes."""
        if store_id not in self.stores:
            raise KeyError(f"Store '{store_id}' not found.")
        store = self.stores[store_id]

        hold_start_dt = (
            datetime.fromisoformat(str(hold_start))
            if isinstance(hold_start, str)
            else hold_start
        )

        # Active allocated orders for this store
        allocated_orders = [
            o
            for o in self.orders.values()
            if o.allocated_store_id == store_id
            and o.status not in (OrderState.DELIVERED, OrderState.CANCELLED, OrderState.REJECTED)
        ]

        hold_res = self.weather_hold_service.apply_weather_hold(
            store=store,
            started_at=hold_start_dt,
            orders=allocated_orders,
            event_id=event_id,
        )

        for o in allocated_orders:
            existing_event = [
                e
                for e in self.order_events.get(o.order_id, [])
                if e["event_type"] in ("WEATHER_HOLD_APPLIED", "WEATHER_HOLD_SKIPPED")
                and e["details"].get("event_id") == hold_res.event_id
            ]
            if existing_event:
                continue

            was_extended = o.order_id in hold_res.extended_order_ids
            self._record_event(
                order_id=o.order_id,
                event_type="WEATHER_HOLD_APPLIED" if was_extended else "WEATHER_HOLD_SKIPPED",
                timestamp=hold_start_dt,
                business_rules=["BR-11"],
                details={
                    "event_id": hold_res.event_id,
                    "store_id": hold_res.store_id,
                    "hold_start": hold_res.started_at.isoformat(),
                    "hold_expiry": hold_res.hold_until.isoformat(),
                    "eta_extended": was_extended,
                    "promised_eta": o.promised_eta.isoformat() if o.promised_eta else None,
                    "extension_minutes": 10 if was_extended else 0,
                },
            )

        return {
            "event_id": hold_res.event_id,
            "store_id": hold_res.store_id,
            "hold_start": hold_res.started_at.isoformat(),
            "hold_expiry": hold_res.hold_until.isoformat(),
            "extended_orders_count": hold_res.orders_extended_count,
            "extended_order_ids": hold_res.extended_order_ids,
        }

    # -----------------------------------------------------------------------
    # 7. Rider Batching & Dispatch (BR-08)
    # -----------------------------------------------------------------------

    def create_batch(
        self,
        batch_id: str,
        store_id: str,
        rider_id: str,
        created_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Create a delivery batch grouping orders for an assigned rider."""
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("batch_id must be a non-empty string.")
        batch_id = batch_id.strip()

        if batch_id in self.batches or self.batching_service.has_batch(batch_id):
            raise ValueError(f"Batch with ID '{batch_id}' already exists.")
        if store_id not in self.stores:
            raise KeyError(f"Store '{store_id}' not found.")
        if rider_id not in self.riders:
            raise KeyError(f"Rider '{rider_id}' not found.")

        store = self.stores[store_id]
        rider = self.riders[rider_id]
        created_at_dt = (
            datetime.fromisoformat(str(created_at))
            if isinstance(created_at, str)
            else (created_at or datetime.now())
        )

        batch = self.batching_service.create_batch(
            batch_id=batch_id,
            store=store,
            rider=rider,
            created_at=created_at_dt,
        )
        self.batches[batch_id] = batch
        self.batch_stores[batch_id] = store_id

        return {
            "batch_id": batch.batch_id,
            "store_id": store.store_id,
            "rider_id": rider.rider_id,
            "status": batch.status.value,
        }

    def add_order_to_batch(
        self,
        batch_id: str,
        order_id: str,
        current_time: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Add an eligible order to a batch after verifying drop compatibility."""
        if batch_id not in self.batches:
            raise KeyError(f"Batch '{batch_id}' not found.")
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")

        batch = self.batches[batch_id]
        order = self.orders[order_id]

        batch_store_id = self.batch_stores.get(batch_id)
        if not batch_store_id or batch_store_id not in self.stores:
            raise KeyError(f"Authoritative store for batch '{batch_id}' not found.")
        store = self.stores[batch_store_id]

        for o in batch.orders:
            if o.allocated_store_id != batch_store_id:
                raise ValueError(
                    f"Contaminated batch '{batch_id}': order '{o.order_id}' belongs to store '{o.allocated_store_id}', "
                    f"which does not match batch store '{batch_store_id}'."
                )

        # Validate order allocation
        if not order.allocated_store_id:
            raise ValueError(f"Cannot add unallocated order '{order_id}' to batch '{batch_id}'.")

        # Validate store matching (no cross-store orders)
        if order.allocated_store_id != batch_store_id:
            raise ValueError(
                f"Order '{order_id}' allocated to store '{order.allocated_store_id}', "
                f"which does not match batch store '{batch_store_id}'."
            )

        # Validate order lifecycle state
        if order.status != OrderState.READY_FOR_DISPATCH:
            raise ValueError(
                f"Order '{order_id}' has status '{order.status.value}'; "
                f"only orders in READY_FOR_DISPATCH can be added to a batch."
            )

        # Validate order not in an incompatible active batch
        active_batch_id = self.batching_service.get_active_batch_for_order(order.order_id)
        if active_batch_id is not None and active_batch_id != batch_id:
            raise ValueError(
                f"Order '{order_id}' is already assigned to active batch '{active_batch_id}'."
            )

        curr_time = (
            datetime.fromisoformat(str(current_time))
            if isinstance(current_time, str)
            else (current_time or datetime.now())
        )

        self.batching_service.add_order_to_batch(
            batch=batch,
            order=order,
            store=store,
            ready_at=curr_time,
        )

        existing_assign = [
            e
            for e in self.order_events.get(order.order_id, [])
            if e["event_type"] == "BATCH_ASSIGNED"
            and e["details"].get("batch_id") == batch.batch_id
        ]
        if not existing_assign:
            self._record_event(
                order_id=order.order_id,
                event_type="BATCH_ASSIGNED",
                timestamp=curr_time,
                business_rules=["BR-08"],
                details={
                    "batch_id": batch.batch_id,
                    "store_id": store.store_id,
                    "rider_id": batch.rider_id,
                    "orders_in_batch": [o.order_id for o in batch.orders],
                },
            )

        return {
            "batch_id": batch.batch_id,
            "order_id": order.order_id,
            "is_compatible": True,
            "orders_in_batch": [o.order_id for o in batch.orders],
        }

    def dispatch_batch(
        self,
        batch_id: str,
        dispatched_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Dispatch a delivery batch, transitioning orders to DISPATCHED."""
        if batch_id not in self.batches:
            raise KeyError(f"Batch '{batch_id}' not found.")
        batch = self.batches[batch_id]
        if not batch.orders:
            raise ValueError(f"Cannot dispatch empty batch '{batch_id}'.")

        batch_store_id = self.batch_stores.get(batch_id)
        if not batch_store_id or batch_store_id not in self.stores:
            raise KeyError(f"Authoritative store for batch '{batch_id}' not found.")
        store = self.stores[batch_store_id]

        for o in batch.orders:
            if o.allocated_store_id != batch_store_id:
                raise ValueError(
                    f"Contaminated batch '{batch_id}': order '{o.order_id}' belongs to store '{o.allocated_store_id}', "
                    f"which does not match batch store '{batch_store_id}'."
                )

        rider = self.riders[batch.rider_id]
        disp_dt = (
            datetime.fromisoformat(str(dispatched_at))
            if isinstance(dispatched_at, str)
            else (dispatched_at or datetime.now())
        )

        dispatch_res = self.batching_service.dispatch_batch(
            batch=batch,
            store=store,
            rider=rider,
            dispatched_at=disp_dt,
        )

        for o in batch.orders:
            existing_disp = [
                e
                for e in self.order_events.get(o.order_id, [])
                if e["event_type"] == "ORDER_DISPATCHED"
                and e["details"].get("batch_id") == batch.batch_id
            ]
            if not existing_disp:
                self._record_event(
                    order_id=o.order_id,
                    event_type="ORDER_DISPATCHED",
                    timestamp=disp_dt,
                    business_rules=["BR-08"],
                    details={
                        "batch_id": batch.batch_id,
                        "rider_id": rider.rider_id,
                        "dispatched_at": disp_dt.isoformat(),
                        "drop_sequence": list(dispatch_res.order_ids),
                        "status": "DISPATCHED",
                    },
                )

        return {
            "batch_id": batch.batch_id,
            "dispatched_at": (
                dispatch_res.dispatched_at.isoformat()
                if dispatch_res.dispatched_at
                else None
            ),
            "rider_id": rider.rider_id,
            "order_ids": list(dispatch_res.order_ids),
            "drop_sequence": list(dispatch_res.order_ids),
        }

    # -----------------------------------------------------------------------
    # 8. Delivery & SLA Compensation (BR-08, BR-09)
    # -----------------------------------------------------------------------

    def complete_order_delivery(
        self,
        batch_id: str,
        order_id: str,
        delivered_at: str | datetime,
    ) -> dict[str, Any]:
        """Progressively complete delivery for an individual order drop in a batch."""
        if batch_id not in self.batches:
            raise KeyError(f"Batch '{batch_id}' not found.")
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")

        batch = self.batches[batch_id]
        order = self.orders[order_id]
        rider = self.riders[batch.rider_id]
        del_dt = (
            datetime.fromisoformat(str(delivered_at))
            if isinstance(delivered_at, str)
            else delivered_at
        )

        # Idempotent return if order was already delivered
        if order.status == OrderState.DELIVERED:
            if not any(o.order_id == order_id for o in batch.orders):
                raise ValueError(f"Order '{order_id}' is not part of batch '{batch_id}'.")

            # Complete or retrieve existing compensation (idempotent)
            comp_res = self.compensation_service.apply_compensation(
                order=order,
                actual_delivery_time=order.actual_delivery_time or del_dt,
            )
            is_batch_complete = all(
                o.status in (OrderState.DELIVERED, OrderState.CANCELLED)
                for o in batch.orders
            )
            return {
                "batch_id": batch.batch_id,
                "order_id": order.order_id,
                "delivered_at": (
                    order.actual_delivery_time.isoformat()
                    if order.actual_delivery_time
                    else del_dt.isoformat()
                ),
                "is_batch_complete": is_batch_complete,
                "wallet_credit": comp_res.wallet_credit,
                "free_coupon": comp_res.free_coupon,
            }

        del_res = self.batching_service.complete_order_delivery(
            batch=batch,
            order=order,
            rider=rider,
            delivered_at=del_dt,
        )

        # Apply customer SLA compensation (BR-09)
        comp_res = self.compensation_service.apply_compensation(
            order=order,
            actual_delivery_time=order.actual_delivery_time or del_dt,
        )

        existing_del = [
            e
            for e in self.order_events.get(order.order_id, [])
            if e["event_type"] == "ORDER_DELIVERED"
        ]
        if not existing_del:
            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_DELIVERED",
                timestamp=del_dt,
                business_rules=["BR-09"],
                details={
                    "batch_id": batch.batch_id,
                    "status": "DELIVERED",
                    "delivered_at": (order.actual_delivery_time or del_dt).isoformat(),
                    "promised_eta": order.promised_eta.isoformat() if order.promised_eta else None,
                    "wallet_credit": str(comp_res.wallet_credit),
                    "free_coupon": comp_res.free_coupon,
                    "is_late": comp_res.is_late,
                    "lateness_minutes": comp_res.lateness_minutes,
                },
            )

        return {
            "batch_id": batch.batch_id,
            "order_id": order.order_id,
            "delivered_at": (
                order.actual_delivery_time.isoformat()
                if order.actual_delivery_time
                else del_res.delivered_at.isoformat()
            ),
            "is_batch_complete": del_res.is_batch_complete,
            "wallet_credit": comp_res.wallet_credit,
            "free_coupon": comp_res.free_coupon,
        }

    def complete_batch_delivery(
        self,
        batch_id: str,
        delivered_at: str | datetime,
    ) -> dict[str, Any]:
        """Complete delivery for all remaining orders in a batch."""
        if batch_id not in self.batches:
            raise KeyError(f"Batch '{batch_id}' not found.")
        batch = self.batches[batch_id]
        rider = self.riders[batch.rider_id]
        del_dt = (
            datetime.fromisoformat(str(delivered_at))
            if isinstance(delivered_at, str)
            else delivered_at
        )

        # Idempotent return if batch was already delivered
        if batch.status == BatchStatus.DELIVERED:
            # Preserve original actual delivery timestamp from delivered orders
            delivered_times = [
                o.actual_delivery_time
                for o in batch.orders
                if o.actual_delivery_time is not None
            ]
            batch_del_time = max(delivered_times) if delivered_times else del_dt

            # Ensure batch compensation is applied (idempotent)
            self.compensation_service.process_batch_compensation(
                batch=batch,
                delivered_at=batch_del_time,
            )
            return {
                "batch_id": batch.batch_id,
                "delivered_at": batch_del_time.isoformat(),
                "delivered_order_ids": [o.order_id for o in batch.orders],
                "rider_status": rider.status.value,
            }

        del_res = self.batching_service.complete_delivery(
            batch=batch,
            rider=rider,
            delivered_at=del_dt,
        )

        # Process compensation for all orders
        self.compensation_service.process_batch_compensation(
            batch=batch,
            delivered_at=del_dt,
        )

        for o in batch.orders:
            if o.status == OrderState.DELIVERED:
                existing_del = [
                    e
                    for e in self.order_events.get(o.order_id, [])
                    if e["event_type"] == "ORDER_DELIVERED"
                ]
                if not existing_del:
                    comp = self.compensation_service.get_compensation(o.order_id)
                    w_credit = str(comp.wallet_credit) if comp else str(o.wallet_credit)
                    f_coupon = comp.free_coupon if comp else o.free_coupon
                    is_late = comp.is_late if comp else (
                        (o.actual_delivery_time > o.promised_eta)
                        if (o.actual_delivery_time and o.promised_eta)
                        else False
                    )
                    l_mins = comp.lateness_minutes if comp else 0.0

                    self._record_event(
                        order_id=o.order_id,
                        event_type="ORDER_DELIVERED",
                        timestamp=del_dt,
                        business_rules=["BR-09"],
                        details={
                            "batch_id": batch.batch_id,
                            "status": "DELIVERED",
                            "delivered_at": (o.actual_delivery_time or del_dt).isoformat(),
                            "promised_eta": o.promised_eta.isoformat() if o.promised_eta else None,
                            "wallet_credit": w_credit,
                            "free_coupon": f_coupon,
                            "is_late": is_late,
                            "lateness_minutes": l_mins,
                        },
                    )

        return {
            "batch_id": batch.batch_id,
            "delivered_at": del_res.delivered_at.isoformat(),
            "delivered_order_ids": del_res.delivered_order_ids,
            "rider_status": rider.status.value,
        }

    # -----------------------------------------------------------------------
    # 9. Rider Penalties (BR-10)
    # -----------------------------------------------------------------------

    def evaluate_rider_penalties(
        self,
        batch_id: str,
        delivered_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate rider batch bonus forfeiture and lateness deductions."""
        if batch_id not in self.batches:
            raise KeyError(f"Batch '{batch_id}' not found.")
        batch = self.batches[batch_id]
        rider = self.riders[batch.rider_id]
        del_dt = (
            datetime.fromisoformat(str(delivered_at))
            if isinstance(delivered_at, str)
            else delivered_at
        )

        penalty_res = self.rider_penalty_service.apply_batch_penalties(
            batch=batch,
            rider=rider,
            delivered_at=del_dt,
        )

        for o in batch.orders:
            existing = [
                e
                for e in self.order_events.get(o.order_id, [])
                if e["event_type"] == "RIDER_PENALTY_EVALUATED"
                and e["details"].get("batch_id") == batch.batch_id
            ]
            if not existing:
                self._record_event(
                    order_id=o.order_id,
                    event_type="RIDER_PENALTY_EVALUATED",
                    timestamp=del_dt or datetime.now(),
                    business_rules=["BR-10"],
                    details={
                        "batch_id": batch.batch_id,
                        "rider_id": rider.rider_id,
                        "late_orders_count": penalty_res.late_orders_count,
                        "bonus_lost": penalty_res.bonus_lost,
                        "batch_bonus_awarded": str(penalty_res.batch_bonus_awarded),
                        "total_applied_deduction": str(penalty_res.total_applied_deduction),
                        "rider_daily_penalty": str(rider.daily_penalty),
                        "rider_daily_penalty_date": (
                            rider.daily_penalty_date.isoformat()
                            if rider.daily_penalty_date
                            else None
                        ),
                    },
                )

        return {
            "batch_id": batch.batch_id,
            "rider_id": rider.rider_id,
            "late_orders_count": penalty_res.late_orders_count,
            "bonus_lost": penalty_res.bonus_lost,
            "batch_bonus_awarded": penalty_res.batch_bonus_awarded,
            "total_applied_deduction": penalty_res.total_applied_deduction,
            "rider_daily_penalty": rider.daily_penalty,
            "rider_daily_penalty_date": (
                rider.daily_penalty_date.isoformat()
                if rider.daily_penalty_date
                else None
            ),
        }

    # -----------------------------------------------------------------------
    # 10. Cancellation & Refunds (BR-12)
    # -----------------------------------------------------------------------

    def cancel_order(
        self,
        order_id: str,
        cancelled_at: str | datetime | None = None,
        reason: str = "Customer requested cancellation",
        amount_paid: str | Decimal | float | int | None = None,
    ) -> dict[str, Any]:
        """Cancel an order, calculate cancellation fees/refunds, and release inventory."""
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")
        order = self.orders[order_id]

        store = (
            self.stores.get(order.allocated_store_id)
            if order.allocated_store_id
            else None
        )
        batch = next(
            (b for b in self.batches.values() if any(o.order_id == order_id for o in b.orders)),
            None,
        )

        canc_dt = (
            datetime.fromisoformat(str(cancelled_at))
            if isinstance(cancelled_at, str)
            else (cancelled_at or datetime.now())
        )
        dec_paid = Decimal(str(amount_paid)) if amount_paid is not None else None

        cancel_res = self.cancellation_service.cancel_order(
            order=order,
            cancelled_at=canc_dt,
            reason=reason,
            amount_paid=dec_paid,
            batch=batch,
        )

        existing_canc = [
            e
            for e in self.order_events.get(order.order_id, [])
            if e["event_type"] == "ORDER_CANCELLED"
        ]
        if not existing_canc:
            self._record_event(
                order_id=order.order_id,
                event_type="ORDER_CANCELLED",
                timestamp=canc_dt,
                business_rules=["BR-12"],
                details={
                    "order_id": order.order_id,
                    "status": "CANCELLED",
                    "cancellation_fee": str(cancel_res.cancellation_fee),
                    "refund_amount": str(cancel_res.refund_amount),
                    "reason": cancel_res.reason,
                    "cancelled_at": canc_dt.isoformat(),
                },
            )

        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "cancellation_fee": cancel_res.cancellation_fee,
            "refund_amount": cancel_res.refund_amount,
            "reason": cancel_res.reason,
        }

    # -----------------------------------------------------------------------
    # 11. FR-11 Execution Trace Integration Point (Issue #28)
    # -----------------------------------------------------------------------

    def get_order_trace(self, order_id: str) -> dict[str, Any]:
        """Return integration trace summary and structured chronological events for an order."""
        if order_id not in self.orders:
            raise KeyError(f"Order '{order_id}' not found.")
        order = self.orders[order_id]

        events_copy = [
            {
                "sequence": e["sequence"],
                "timestamp": e["timestamp"],
                "event_type": e["event_type"],
                "business_rules": list(e["business_rules"]),
                "details": copy.deepcopy(e["details"]),
            }
            for e in self.order_events.get(order_id, [])
        ]

        return {
            "order_id": order.order_id,
            "status": order.status.value,
            "allocated_store_id": order.allocated_store_id,
            "promised_eta": order.promised_eta.isoformat() if order.promised_eta else None,
            "actual_delivery_time": (
                order.actual_delivery_time.isoformat()
                if order.actual_delivery_time
                else None
            ),
            "order_value": order.original_total_value,
            "fulfilled_value": order.fulfilled_total_value,
            "delivery_fee": order.delivery_fee,
            "surge_fee": order.surge_fee,
            "cancellation_fee": order.cancellation_fee,
            "refund_amount": order.refund_amount,
            "wallet_credit": order.wallet_credit,
            "free_coupon": order.free_coupon,
            "event_count": len(events_copy),
            "events": events_copy,
            "trace": copy.deepcopy(events_copy),
        }

    def get_order_events(self, order_id: str) -> list[dict[str, Any]]:
        """Return a chronological list of trace events recorded for an order."""
        if order_id not in self.orders and order_id not in self.order_events:
            raise KeyError(f"Order '{order_id}' not found.")
        return [
            {
                "sequence": e["sequence"],
                "timestamp": e["timestamp"],
                "event_type": e["event_type"],
                "business_rules": list(e["business_rules"]),
                "details": copy.deepcopy(e["details"]),
            }
            for e in self.order_events.get(order_id, [])
        ]
