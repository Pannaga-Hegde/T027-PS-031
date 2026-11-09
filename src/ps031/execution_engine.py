"""Central execution engine orchestrating dark-store quick-commerce simulation.

Coordinates domain services and models across the entire order lifecycle:
- Store allocation & serviceability (BR-01, BR-02)
- Picking scheduling & promised ETA (BR-03, BR-04)
- Product substitutions & 60% fulfilment threshold (BR-05, BR-06)
- Delivery pricing & surge fees (BR-07)
- Batching & delivery tracking (BR-08)
- SLA compensation (BR-09)
- Rider payouts & severe lateness deductions (BR-10)
- Weather holds (BR-11)
- Customer cancellations (BR-12)
"""

from collections import defaultdict
from collections.abc import Sequence
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import os
from pathlib import Path
from typing import Any, TextIO

from ps031.command_parser import (
    CancelCommand,
    Command,
    CommandType,
    LoadDeliveriesCommand,
    OrderCommand,
    PrintRiderPayoutsCommand,
    PrintTraceCommand,
    RidersCommand,
    WeatherHoldCommand,
    parse_commands,
)
from ps031.loaders import (
    DeliveryEventRecord,
    RiderRecord,
    StockRecord,
    load_delivery_events,
    load_riders,
    load_skus,
    load_stock,
    load_stores,
)
from ps031.models.batch import Batch, BatchStatus
from ps031.models.coordinates import Coordinates
from ps031.models.inventory import InventoryItem
from ps031.models.order import Order, OrderItem, SubstitutionPolicy
from ps031.models.order_state import OrderState
from ps031.models.product import Product
from ps031.models.rider import Rider
from ps031.models.store import Store
from ps031.services.allocation_service import AllocationService
from ps031.services.cancellation_service import CancellationService
from ps031.services.compensation_service import CompensationService
from ps031.services.fulfilment_service import FulfilmentService
from ps031.services.inventory_service import InventoryService
from ps031.services.lifecycle import LifecycleTransitionService
from ps031.services.picking_service import PickingService
from ps031.services.pricing_service import PricingService
from ps031.services.rider_payout_service import RiderDailyPayout, RiderPayoutService
from ps031.services.substitution_service import SubstitutionService
from ps031.services.weather_hold_service import WeatherHoldService
from ps031.utils.distance import haversine_distance


@dataclass
class OrderExecutionMetadata:
    """Internal metadata tracking an order's lifecycle execution details."""

    distance_km: float | None = None
    slot_start: datetime | None = None
    picking_start: datetime | None = None
    promised_eta_min: int | None = None
    batch_id: str | None = None
    drop_seq: int | None = None
    dispatch_ts: datetime | None = None
    delivered_ts: datetime | None = None
    rejection_reason: str | None = None
    is_cancelled_partial: bool = False
    tags: set[str] = field(default_factory=set)
    rule_ids: list[str] = field(default_factory=list)
    reservation_ids: list[str] = field(default_factory=list)


class ExecutionEngine:
    """Central orchestrator executing simulation commands and coordinating services."""

    def __init__(
        self,
        stores: dict[str, Store] | Sequence[Store],
        products: dict[str, Product] | Sequence[Product],
        stock: list[StockRecord] | Sequence[StockRecord] | None = None,
        riders: dict[str, RiderRecord] | Sequence[RiderRecord] | None = None,
        delivery_events: list[DeliveryEventRecord] | Sequence[DeliveryEventRecord] | None = None,
        allocation_service: AllocationService | None = None,
        inventory_service: InventoryService | None = None,
        substitution_service: SubstitutionService | None = None,
        picking_service: PickingService | None = None,
        pricing_service: PricingService | None = None,
        compensation_service: CompensationService | None = None,
        weather_hold_service: WeatherHoldService | None = None,
        cancellation_service: CancellationService | None = None,
        rider_payout_service: RiderPayoutService | None = None,
        lifecycle_service: LifecycleTransitionService | None = None,
        fulfilment_service: FulfilmentService | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        # Normalize stores
        if isinstance(stores, dict):
            self.stores = stores
        else:
            self.stores = {s.store_id: s for s in stores}

        # Normalize products
        if isinstance(products, dict):
            self.products = products
        else:
            self.products = {p.sku: p for p in products}

        # Normalize riders
        if riders is not None:
            if isinstance(riders, dict):
                self.riders = riders
            else:
                self.riders = {r.rider_id: r for r in riders}
        else:
            self.riders = {}

        self.stock = list(stock) if stock is not None else []
        self.delivery_events = list(delivery_events) if delivery_events is not None else []
        self.data_dir = Path(data_dir) if data_dir is not None else Path("data")

        # Services
        self.lifecycle_service = lifecycle_service or LifecycleTransitionService()
        self.picking_service = picking_service or PickingService()
        self.pricing_service = pricing_service or PricingService(stores=list(self.stores.values()))
        self.allocation_service = allocation_service or AllocationService(
            lifecycle_service=self.lifecycle_service,
            pricing_service=self.pricing_service,
        )
        self.inventory_service = inventory_service or InventoryService(
            stores=self.stores, products=self.products
        )
        self.substitution_service = substitution_service or SubstitutionService()
        self.fulfilment_service = fulfilment_service or FulfilmentService()
        self.compensation_service = compensation_service or CompensationService()
        self.weather_hold_service = weather_hold_service or WeatherHoldService()
        self.cancellation_service = cancellation_service or CancellationService(
            inventory_service=self.inventory_service,
            lifecycle_service=self.lifecycle_service,
            product_catalog=self.products,
        )
        self.rider_payout_service = rider_payout_service or RiderPayoutService()

        # Engine internal state
        self.orders: dict[str, Order] = {}
        self.order_metadata: dict[str, OrderExecutionMetadata] = {}
        self._store_batch_counters: dict[str, int] = defaultdict(int)
        self.batches: dict[str, Batch] = {}

        # Capture outputs produced during command execution
        self.printed_traces: list[dict[str, Any]] = []
        self.printed_order_lines: list[dict[str, Any]] = []
        self.printed_rider_payouts: list[list[RiderDailyPayout]] = []

    @classmethod
    def from_directory(cls, dir_path: str | Path) -> "ExecutionEngine":
        """Initialize engine from a directory containing mentor CSV files."""
        d = Path(dir_path)
        stores = load_stores(d / "stores.csv")
        products = load_skus(d / "skus.csv")
        stock = load_stock(d / "stock.csv", stores=stores, products=products)
        riders = load_riders(d / "riders.csv", stores=stores)
        return cls(
            stores=stores,
            products=products,
            stock=stock,
            riders=riders,
            data_dir=d,
        )

    def process_file(self, source: str | Path | TextIO) -> None:
        """Parse and execute a simulation command file strictly in file order."""
        commands = parse_commands(source)
        self.process_commands(commands)

    def process_commands(self, commands: Sequence[Command]) -> None:
        """Process a sequence of parsed commands strictly in order."""
        for cmd in commands:
            self.process_command(cmd)

    def process_command(self, cmd: Command) -> Any:
        """Dispatch a single parsed command to its appropriate handler."""
        if cmd.command_type == CommandType.ORDER:
            assert isinstance(cmd, OrderCommand)
            return self._handle_order(cmd)
        if cmd.command_type == CommandType.RIDERS:
            assert isinstance(cmd, RidersCommand)
            return self._handle_riders(cmd)
        if cmd.command_type == CommandType.WEATHER_HOLD:
            assert isinstance(cmd, WeatherHoldCommand)
            return self._handle_weather_hold(cmd)
        if cmd.command_type == CommandType.CANCEL:
            assert isinstance(cmd, CancelCommand)
            return self._handle_cancel(cmd)
        if cmd.command_type == CommandType.LOAD_DELIVERIES:
            assert isinstance(cmd, LoadDeliveriesCommand)
            return self._handle_load_deliveries(cmd)
        if cmd.command_type == CommandType.PRINT_TRACE:
            assert isinstance(cmd, PrintTraceCommand)
            return self._handle_print_trace(cmd)
        if cmd.command_type == CommandType.PRINT_RIDER_PAYOUTS:
            assert isinstance(cmd, PrintRiderPayoutsCommand)
            return self._handle_print_rider_payouts(cmd)
        raise ValueError(f"Unsupported command type: {cmd.command_type}")

    # ==========================================================================
    # COMMAND HANDLERS
    # ==========================================================================

    def _handle_order(self, cmd: OrderCommand) -> Order:
        """Process an ORDER command adhering to BR-01 through BR-07."""
        # 1. Idempotent deduplication (e.g. EC-02 duplicate retry)
        if cmd.order_id in self.orders:
            return self.orders[cmd.order_id]

        meta = OrderExecutionMetadata()
        self.order_metadata[cmd.order_id] = meta

        # 2. Validate catalogue existence for all requested SKUs (e.g. SKU-099)
        missing_skus = [req.sku_id for req in cmd.items if req.sku_id not in self.products]
        if missing_skus:
            # Construct empty rejected order
            order = Order(
                order_id=cmd.order_id,
                customer_id=cmd.customer_id,
                items=[],
                status=OrderState.REJECTED,
                delivery_location=Coordinates(cmd.lat, cmd.lon),
                order_time=cmd.order_ts,
            )
            meta.rejection_reason = f"SKU(s) not in catalogue: {', '.join(missing_skus)}"
            meta.rule_ids.append("BR-01")
            self.orders[cmd.order_id] = order
            return order

        # 3. Construct Order and OrderItem instances
        order_items = [
            OrderItem(
                product_id=p.sku,
                quantity=req.quantity,
                unit_price=p.price,
                category=p.category,
                brand=p.brand,
            )
            for req in cmd.items
            for p in [self.products[req.sku_id]]
        ]
        order = Order(
            order_id=cmd.order_id,
            customer_id=cmd.customer_id,
            items=order_items,
            substitution_policy=cmd.substitution_pref,
            delivery_location=Coordinates(latitude=cmd.lat, longitude=cmd.lon),
            order_time=cmd.order_ts,
            slow_delivery=cmd.slow_delivery_ok,
        )

        # 4. Serviceability and Store Selection (BR-01, BR-02)
        slot_loads = {
            s.store_id: self.picking_service.get_slot_load(s.store_id, cmd.order_ts)
            for s in self.stores.values()
        }
        alloc_res = self.allocation_service.allocate_store(
            order=order,
            stores=list(self.stores.values()),
            slot_loads=slot_loads,
            reference_time=cmd.order_ts,
            apply_lifecycle_transition=False,
        )

        if not alloc_res.is_allocated or alloc_res.selected_store is None:
            # Check if a store within 3.00 km is open and was only excluded due to weather hold
            weather_held_store = None
            for eval_res in alloc_res.evaluations:
                if (
                    eval_res.distance_km <= 3.00
                    and eval_res.is_open
                    and eval_res.is_weather_held
                ):
                    weather_held_store = eval_res.store
                    break

            if weather_held_store is not None:
                selected_store = weather_held_store
                meta.tags.add("WEATHER_EXTENDED")
                meta.rule_ids.append("BR-11")
            else:
                order.status = OrderState.REJECTED
                meta.rejection_reason = alloc_res.rejection_reason
                meta.rule_ids.append("BR-01")
                self.orders[cmd.order_id] = order
                return order
        else:
            selected_store = alloc_res.selected_store

        order.allocated_store_id = selected_store.store_id
        order.store_id = selected_store.store_id
        meta.distance_km = round(
            haversine_distance(
                order.delivery_location, selected_store.location
            ),
            2,
        )
        meta.rule_ids.extend(["BR-01", "BR-02"])

        # 5. Inventory Reservation & Substitution (BR-05)
        for item in order.items:
            inv_res = self.inventory_service.reserve_stock(
                selected_store.store_id,
                order.order_id,
                item.sku_id,
                item.requested_qty,
            )
            direct = inv_res.reserved_quantity
            shortfall = item.requested_qty - direct

            if shortfall > 0 and order.substitution_policy != SubstitutionPolicy.NONE:
                # Collect available candidate products with stock > 0 in the same category
                candidates = [
                    InventoryItem(
                        sku_id=p.sku,
                        name=p.name,
                        category=p.category,
                        brand=p.brand,
                        price=p.price,
                        stock=selected_store.inventory.get(p.sku, 0),
                    )
                    for p in self.products.values()
                    if selected_store.inventory.get(p.sku, 0) > 0
                    and p.category == item.category
                ]
                sub_res = self.substitution_service.resolve_line_shortfall(
                    requested_item=item,
                    available_inventory=candidates,
                    preference=order.substitution_policy,
                    needed_qty=shortfall,
                )
                if sub_res.substituted_sku_id is not None and sub_res.fulfilled_qty > 0:
                    sub_inv_res = self.inventory_service.reserve_stock(
                        selected_store.store_id,
                        order.order_id,
                        sub_res.substituted_sku_id,
                        sub_res.fulfilled_qty,
                    )
                    item.substituted_sku_id = sub_res.substituted_sku_id
                    item.fulfilled_qty = direct + sub_inv_res.reserved_quantity
                    item.substituted_quantity = sub_inv_res.reserved_quantity
                    item.charged_unit_price = item.unit_price
                    meta.rule_ids.append("BR-05")
                else:
                    item.fulfilled_qty = direct
                    item.charged_unit_price = item.unit_price
            else:
                item.fulfilled_qty = direct
                item.charged_unit_price = item.unit_price

        # 6. Pricing & Surge Fee (BR-07)
        self.lifecycle_service.transition(order, OrderState.ALLOCATED)
        if not order.pricing_frozen:
            self.pricing_service.apply_pricing(order, store=selected_store)
        meta.rule_ids.append("BR-07")
        if order.surge_fee > Decimal("0.00"):
            meta.tags.add("SURGE")

        # 7. 60% Fulfilment Cancellation Threshold (BR-06)
        fe = self.fulfilment_service.evaluate_order(order)
        order.refund_amount = fe.refund_amount
        if fe.is_cancelled and order.substitution_policy == SubstitutionPolicy.NONE:
            # Order drops below 60% fulfilled value threshold under NONE policy
            order.status = OrderState.CANCELLED
            meta.is_cancelled_partial = True
            order.delivery_fee = Decimal("0.00")
            order.surge_fee = Decimal("0.00")
            meta.rule_ids.append("BR-06")
            reservations = self.inventory_service.get_reservations_for_order(
                order.order_id
            )
            for r in reservations:
                self.inventory_service.release_reservation(r.reservation_id)
            self.orders[cmd.order_id] = order
            return order

        # 8. Picking Scheduling & Promised ETA (BR-03, BR-04)
        pick_res, eta_res = self.picking_service.process_order_picking_and_eta(
            store=selected_store,
            order=order,
            reference_time=cmd.order_ts,
        )
        meta.slot_start = pick_res.assigned_slot_start
        meta.picking_start = max(order.order_time, pick_res.assigned_slot_start)
        meta.promised_eta_min = eta_res.total_duration_minutes
        order.promised_eta = eta_res.promised_eta
        order.delayed_promise = eta_res.is_delayed_promise
        meta.rule_ids.extend(["BR-03", "BR-04"])

        if "WEATHER_EXTENDED" in meta.tags and order.promised_eta is not None:
            order.promised_eta += timedelta(minutes=10)
            if meta.promised_eta_min is not None:
                meta.promised_eta_min += 10

        if eta_res.is_delayed_promise:
            meta.tags.add("DELAYED_PROMISE")

        # Progress order to PICKING if picking has started
        if order.order_time >= meta.picking_start:
            self.lifecycle_service.transition(order, OrderState.PICKING)
        else:
            # Remains in ALLOCATED until slot start
            pass

        self.orders[cmd.order_id] = order
        return order

    def _handle_riders(self, cmd: RidersCommand) -> None:
        """Update active rider count at a store."""
        if cmd.store_id in self.stores:
            store = self.stores[cmd.store_id]
            store.active_riders = cmd.active_riders
            self.pricing_service.register_store(store)

    def _handle_weather_hold(self, cmd: WeatherHoldCommand) -> None:
        """Freeze allocations for 20 mins and extend picking orders by 10 mins (BR-11)."""
        if cmd.store_id not in self.stores:
            return
        store = self.stores[cmd.store_id]

        # Advance status of past orders whose picking already ended before cmd.ts
        for o in self.orders.values():
            if o.allocated_store_id == store.store_id and o.status not in (
                OrderState.CANCELLED,
                OrderState.DELIVERED,
                OrderState.REJECTED,
            ):
                meta = self.order_metadata.get(o.order_id)
                if meta and meta.picking_start is not None:
                    picking_end = meta.picking_start + timedelta(minutes=2)
                    if cmd.ts > picking_end:
                        if o.status == OrderState.ALLOCATED:
                            self.lifecycle_service.transition(o, OrderState.PICKING)
                        if o.status == OrderState.PICKING:
                            self.lifecycle_service.transition(
                                o, OrderState.READY_FOR_DISPATCH
                            )
                        if o.status == OrderState.READY_FOR_DISPATCH:
                            self.lifecycle_service.transition(
                                o, OrderState.DISPATCHED
                            )

        # Identify orders allocated to this store
        store_orders = [
            o for o in self.orders.values() if o.allocated_store_id == store.store_id
        ]

        hold_res = self.weather_hold_service.apply_weather_hold(
            store=store,
            started_at=cmd.ts,
            orders=store_orders,
        )

        for oid in hold_res.extended_order_ids:
            if oid in self.order_metadata:
                self.order_metadata[oid].tags.add("WEATHER_EXTENDED")
                self.order_metadata[oid].rule_ids.append("BR-11")
                if self.order_metadata[oid].promised_eta_min is not None:
                    self.order_metadata[oid].promised_eta_min += 10

    def _handle_cancel(self, cmd: CancelCommand) -> None:
        """Process customer cancellation request (BR-12)."""
        if cmd.order_id not in self.orders:
            return
        order = self.orders[cmd.order_id]
        meta = self.order_metadata[cmd.order_id]

        # Advance order lifecycle according to physical elapsed time at cmd.ts
        if order.status not in (
            OrderState.CANCELLED,
            OrderState.DELIVERED,
            OrderState.REJECTED,
        ):
            if meta.picking_start is not None:
                # Picking duration is 2 minutes (BR-03)
                picking_end = meta.picking_start + timedelta(minutes=2)
                if cmd.ts < meta.picking_start:
                    # Cancelled before picking starts -> remains ALLOCATED
                    pass
                elif cmd.ts < picking_end:
                    # Cancelled during picking
                    if order.status == OrderState.ALLOCATED:
                        self.lifecycle_service.transition(order, OrderState.PICKING)
                else:
                    # Cancelled after picking -> dispatched
                    if order.status == OrderState.ALLOCATED:
                        self.lifecycle_service.transition(order, OrderState.PICKING)
                    if order.status == OrderState.PICKING:
                        self.lifecycle_service.transition(
                            order, OrderState.READY_FOR_DISPATCH
                        )
                    if order.status == OrderState.READY_FOR_DISPATCH:
                        self.lifecycle_service.transition(
                            order, OrderState.DISPATCHED
                        )

        cancellation_res = self.cancellation_service.cancel_order(
            order=order,
            cancelled_at=cmd.ts,
            picking_start=meta.picking_start,
        )
        meta.rule_ids.append("BR-12")

    def _handle_load_deliveries(self, cmd: LoadDeliveriesCommand) -> None:
        """Load delivery events, reconstruct batches, and complete drops progressively."""
        file_path: Path | str = cmd.file_name
        if not os.path.exists(file_path):
            alt = self.data_dir / cmd.file_name
            if alt.exists():
                file_path = alt

        events = load_delivery_events(file_path)
        self.delivery_events.extend(events)

        # Group actual dispatches: orders with same rider_id and dispatch_ts form one actual batch
        batch_groups: dict[tuple[str, datetime], list[DeliveryEventRecord]] = defaultdict(list)
        for ev in events:
            batch_groups[(ev.rider_id, ev.dispatch_ts)].append(ev)

        for (rider_id, dispatch_ts), ev_list in batch_groups.items():
            first_ev = ev_list[0]
            if first_ev.order_id not in self.orders:
                continue
            first_order = self.orders[first_ev.order_id]
            store_id = first_order.allocated_store_id or "DS-01"

            self._store_batch_counters[store_id] += 1
            seq = self._store_batch_counters[store_id]
            batch_id = f"B-{store_id}-{seq:03d}"

            batch_orders: list[Order] = []
            comp_ts = max(e.delivered_ts for e in ev_list)
            earliest_order_time = min(self.orders[e.order_id].order_time for e in ev_list if e.order_id in self.orders)

            # Sort delivery events within the batch by delivered_ts to establish drop sequence
            sorted_drops = sorted(ev_list, key=lambda e: e.delivered_ts)

            for drop_seq, ev in enumerate(sorted_drops, start=1):
                if ev.order_id not in self.orders:
                    continue
                o = self.orders[ev.order_id]
                meta = self.order_metadata[ev.order_id]

                meta.batch_id = batch_id
                meta.drop_seq = drop_seq
                meta.dispatch_ts = ev.dispatch_ts
                meta.delivered_ts = ev.delivered_ts
                o.actual_delivery_time = ev.delivered_ts
                o.status = OrderState.DELIVERED

                # Apply SLA compensation (BR-09)
                comp_res = self.compensation_service.apply_compensation(o, ev.delivered_ts)
                meta.rule_ids.append("BR-08")
                if comp_res.wallet_credit > Decimal("0.00") or comp_res.free_coupon:
                    meta.rule_ids.append("BR-09")

                # Record individual delivery event into RiderPayoutService (BR-10)
                self.rider_payout_service.record_delivery_event(o, rider_id, batch_id, ev.delivered_ts)
                meta.rule_ids.append("BR-10")
                batch_orders.append(o)

            # Record batch completion and evaluate batch bonus in RiderPayoutService
            batch = Batch(
                batch_id=batch_id,
                rider_id=rider_id,
                created_at=earliest_order_time,
                orders=batch_orders,
                dispatched_at=dispatch_ts,
                status=BatchStatus.DELIVERED,
            )
            self.batches[batch_id] = batch
            self.rider_payout_service.record_batch_payout(batch, completed_at=comp_ts)

    def _handle_print_trace(self, cmd: PrintTraceCommand) -> None:
        """Capture point-in-time trace for the specified order and its line items."""
        trace = self._build_order_trace_dict(cmd.order_id)
        self.printed_traces.append(trace)

        if cmd.order_id in self.orders:
            lines = self._build_order_lines_dicts(self.orders[cmd.order_id])
            self.printed_order_lines.extend(lines)

    def _handle_print_rider_payouts(self, cmd: PrintRiderPayoutsCommand) -> None:
        """Capture point-in-time daily payout for all riders on the specified date."""
        date_str = cmd.date_str
        payouts_list: list[RiderDailyPayout] = []

        for rider_id in sorted(self.riders.keys()):
            payout = self.rider_payout_service.get_daily_payout(rider_id, date_str)
            if payout is None:
                payout = RiderDailyPayout(
                    date=date_str,
                    rider_id=rider_id,
                    batches=0,
                    late_orders=0,
                    batch_bonus_lost=0,
                    late_deductions=Decimal("0.00"),
                    late_deduction_capped=Decimal("0.00"),
                )
            payouts_list.append(payout)

        self.printed_rider_payouts.append(payouts_list)

    # ==========================================================================
    # OUTPUT FORMATTING AND EXPORTS
    # ==========================================================================

    def _build_order_trace_dict(self, order_id: str) -> dict[str, Any]:
        """Build a trace dictionary row for order_trace.csv."""
        if order_id not in self.orders:
            return {"order_id": order_id, "status": "UNKNOWN"}

        order = self.orders[order_id]
        meta = self.order_metadata.get(order_id, OrderExecutionMetadata())

        # Determine output status string
        if meta.is_cancelled_partial:
            status_str = "CANCELLED_PARTIAL"
        elif order.status == OrderState.REJECTED:
            status_str = "REJECTED"
        elif order.status == OrderState.CANCELLED:
            status_str = "CANCELLED"
        elif order.status == OrderState.DELIVERED:
            status_str = "DELIVERED"
        else:
            status_str = order.status.value

        store_id = order.allocated_store_id or ""
        dist_str = f"{meta.distance_km:.2f}" if meta.distance_km is not None else ""
        slot_str = meta.slot_start.strftime("%Y-%m-%d %H:%M:%S") if meta.slot_start else ""
        batch_str = meta.batch_id or ""
        drop_seq_str = str(meta.drop_seq) if meta.drop_seq is not None else ""
        promised_eta_min_str = (
            str(meta.promised_eta_min) if meta.promised_eta_min is not None else ""
        )
        promised_ts_str = (
            order.promised_eta.strftime("%Y-%m-%d %H:%M:%S")
            if order.promised_eta
            else ""
        )
        dispatch_ts_str = (
            meta.dispatch_ts.strftime("%Y-%m-%d %H:%M:%S")
            if meta.dispatch_ts
            else ""
        )
        delivered_ts_str = (
            meta.delivered_ts.strftime("%Y-%m-%d %H:%M:%S")
            if meta.delivered_ts
            else ""
        )

        # delay_min: (delivered_ts - promised_ts) in minutes with 2 decimals
        delay_min_str = ""
        if meta.delivered_ts and order.promised_eta:
            delay_sec = (meta.delivered_ts - order.promised_eta).total_seconds()
            delay_min_str = f"{(delay_sec / 60.0):.2f}"

        orig_val = f"{order.original_total_value:.2f}"
        fulf_val = f"{order.fulfilled_total_value:.2f}"
        del_fee = f"{order.delivery_fee:.2f}"
        surge_fee = f"{order.surge_fee:.2f}"
        canc_charge = f"{order.cancellation_fee:.2f}"
        ref_amt = f"{order.refund_amount:.2f}"
        wallet_credit = f"{order.wallet_credit:.2f}"
        coupon_str = "true" if order.free_coupon else "false"

        tags_order = ["DELAYED_PROMISE", "SURGE", "WEATHER_EXTENDED"]
        present_tags = [t for t in tags_order if t in meta.tags]
        tags_str = ";".join(present_tags)

        # Rule IDs deduplicated preserving insertion order
        seen_rules: set[str] = set()
        clean_rules: list[str] = []
        for r in meta.rule_ids:
            if r not in seen_rules:
                seen_rules.add(r)
                clean_rules.append(r)
        rule_ids_str = ";".join(clean_rules)

        return {
            "order_id": order.order_id,
            "status": status_str,
            "store_id": store_id,
            "distance_km": dist_str,
            "slot_start": slot_str,
            "batch_id": batch_str,
            "drop_seq": drop_seq_str,
            "promised_eta_min": promised_eta_min_str,
            "promised_ts": promised_ts_str,
            "dispatch_ts": dispatch_ts_str,
            "delivered_ts": delivered_ts_str,
            "delay_min": delay_min_str,
            "original_value": orig_val,
            "fulfilled_value": fulf_val,
            "delivery_fee": del_fee,
            "surge_fee": surge_fee,
            "cancellation_charge": canc_charge,
            "refund_amount": ref_amt,
            "wallet_credit": wallet_credit,
            "free_delivery_coupon": coupon_str,
            "tags": tags_str,
            "rule_ids": rule_ids_str,
        }

    def _build_order_lines_dicts(self, order: Order) -> list[dict[str, Any]]:
        """Build lines for order_lines.csv for a single order."""
        lines: list[dict[str, Any]] = []
        for line_no, item in enumerate(order.items, start=1):
            if item.is_substituted and item.fulfilled_qty > 0:
                fulfilled_sku = item.substituted_sku_id or item.sku_id
                line_status = "SUBSTITUTED"
            elif item.fulfilled_qty == item.requested_qty and item.requested_qty > 0:
                fulfilled_sku = item.sku_id
                line_status = "FULL"
            elif item.fulfilled_qty == 0:
                fulfilled_sku = item.sku_id
                line_status = "SHORT"
            else:
                fulfilled_sku = item.sku_id
                line_status = "PARTIAL"

            charged_price = (
                item.charged_unit_price
                if item.charged_unit_price is not None
                else item.unit_price
            )
            line_amount = (Decimal(item.fulfilled_qty) * charged_price).quantize(
                Decimal("0.01")
            )

            lines.append(
                {
                    "order_id": order.order_id,
                    "line_no": line_no,
                    "ordered_sku": item.sku_id,
                    "ordered_qty": item.requested_qty,
                    "fulfilled_sku": fulfilled_sku,
                    "fulfilled_qty": item.fulfilled_qty,
                    "line_status": line_status,
                    "unit_price_charged": f"{charged_price:.2f}",
                    "line_amount": f"{line_amount:.2f}",
                }
            )
        return lines

    def export_order_trace_csv(
        self, target: str | Path | TextIO | None = None
    ) -> str:
        """Export printed order traces to order_trace.csv format."""
        fields = [
            "order_id",
            "status",
            "store_id",
            "distance_km",
            "slot_start",
            "batch_id",
            "drop_seq",
            "promised_eta_min",
            "promised_ts",
            "dispatch_ts",
            "delivered_ts",
            "delay_min",
            "original_value",
            "fulfilled_value",
            "delivery_fee",
            "surge_fee",
            "cancellation_charge",
            "refund_amount",
            "wallet_credit",
            "free_delivery_coupon",
            "tags",
            "rule_ids",
        ]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in self.printed_traces:
            writer.writerow(row)
        content = out.getvalue()

        if target is not None:
            if isinstance(target, (str, Path)):
                with open(target, mode="w", encoding="utf-8") as f:
                    f.write(content)
            elif hasattr(target, "write"):
                target.write(content)

        return content

    def export_order_lines_csv(
        self, target: str | Path | TextIO | None = None
    ) -> str:
        """Export order lines to order_lines.csv format."""
        fields = [
            "order_id",
            "line_no",
            "ordered_sku",
            "ordered_qty",
            "fulfilled_sku",
            "fulfilled_qty",
            "line_status",
            "unit_price_charged",
            "line_amount",
        ]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in self.printed_order_lines:
            writer.writerow(row)
        content = out.getvalue()

        if target is not None:
            if isinstance(target, (str, Path)):
                with open(target, mode="w", encoding="utf-8") as f:
                    f.write(content)
            elif hasattr(target, "write"):
                target.write(content)

        return content

    def export_rider_payouts_csv(
        self, target: str | Path | TextIO | None = None
    ) -> str:
        """Export rider payouts to rider_payouts.csv format."""
        fields = [
            "date",
            "rider_id",
            "batches",
            "late_orders",
            "batch_bonus_lost",
            "late_deductions",
            "late_deduction_capped",
        ]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()

        # Output rows from all executed PRINT_RIDER_PAYOUTS snapshots
        for snapshot in self.printed_rider_payouts:
            for p in snapshot:
                writer.writerow(
                    {
                        "date": p.date,
                        "rider_id": p.rider_id,
                        "batches": p.batches,
                        "late_orders": p.late_orders,
                        "batch_bonus_lost": p.batch_bonus_lost,
                        "late_deductions": f"{p.late_deductions:.2f}",
                        "late_deduction_capped": f"{p.late_deduction_capped:.2f}",
                    }
                )
        content = out.getvalue()

        if target is not None:
            if isinstance(target, (str, Path)):
                with open(target, mode="w", encoding="utf-8") as f:
                    f.write(content)
            elif hasattr(target, "write"):
                target.write(content)

        return content


__all__ = [
    "ExecutionEngine",
    "OrderExecutionMetadata",
]
