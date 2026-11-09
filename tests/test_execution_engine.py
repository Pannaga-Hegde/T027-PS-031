"""Comprehensive unit and integration test suite for ExecutionEngine (Issue #27, Stage 3).

Verifies the central execution engine coordinating domain services across the entire
order lifecycle for the dark-store quick-commerce simulation:
- Initialization from mentor CSV datasets or explicit collections
- Strict file-order execution of simulation commands
- All 7 command types: ORDER, RIDERS, WEATHER_HOLD, CANCEL, LOAD_DELIVERIES, PRINT_TRACE, PRINT_RIDER_PAYOUTS
- Full integration with mentor sample dataset (130 commands, 60 orders, 55 deliveries)
- Planted scenarios (EC-01 through EC-08) and extra traps
- Accurate CSV exports: order_trace.csv, order_lines.csv, rider_payouts.csv
"""

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import io
from pathlib import Path

import pytest

from ps031.command_parser import (
    CancelCommand,
    CommandType,
    LoadDeliveriesCommand,
    OrderCommand,
    OrderItemRequest,
    PrintRiderPayoutsCommand,
    PrintTraceCommand,
    RidersCommand,
    WeatherHoldCommand,
)
from ps031.execution_engine import ExecutionEngine, OrderExecutionMetadata
from ps031.loaders import (
    load_delivery_events,
    load_riders,
    load_skus,
    load_stock,
    load_stores,
)
from ps031.models.coordinates import Coordinates
from ps031.models.order import Order, OrderItem, OrderState, SubstitutionPolicy
from ps031.models.store import Store

DATA_DIR = Path("data")


@pytest.fixture(scope="module")
def full_engine() -> ExecutionEngine:
    """Fixture providing an ExecutionEngine initialized and executed on data/commands.txt."""
    engine = ExecutionEngine.from_directory(DATA_DIR)
    engine.process_file(DATA_DIR / "commands.txt")
    return engine


# ==============================================================================
# 1. INITIALIZATION TESTS
# ==============================================================================


class TestExecutionEngineInitialization:
    """Tests for ExecutionEngine constructors and container setup."""

    def test_from_directory_initializes_datasets(self) -> None:
        engine = ExecutionEngine.from_directory(DATA_DIR)
        assert len(engine.stores) == 4
        assert "DS-01" in engine.stores
        assert "DS-02" in engine.stores
        assert "DS-03" in engine.stores
        assert "DS-04" in engine.stores

        assert len(engine.products) == 40
        assert "SKU-001" in engine.products
        assert "SKU-040" in engine.products

        assert len(engine.riders) == 15
        assert "RDR-01" in engine.riders
        assert "RDR-15" in engine.riders

        assert len(engine.stock) == 160
        assert len(engine.orders) == 0
        assert len(engine.printed_traces) == 0
        assert len(engine.printed_order_lines) == 0
        assert len(engine.printed_rider_payouts) == 0

    def test_explicit_init_with_dictionaries(self) -> None:
        stores = load_stores(DATA_DIR / "stores.csv")
        products = load_skus(DATA_DIR / "skus.csv")
        stock = load_stock(DATA_DIR / "stock.csv", stores=stores, products=products)
        riders = load_riders(DATA_DIR / "riders.csv", stores=stores)

        engine = ExecutionEngine(
            stores=stores,
            products=products,
            stock=stock,
            riders=riders,
        )
        assert len(engine.stores) == 4
        assert len(engine.products) == 40
        assert len(engine.riders) == 15
        assert len(engine.stock) == 160

    def test_explicit_init_with_sequences(self) -> None:
        stores = list(load_stores(DATA_DIR / "stores.csv").values())
        products = list(load_skus(DATA_DIR / "skus.csv").values())
        riders = list(load_riders(DATA_DIR / "riders.csv").values())

        engine = ExecutionEngine(
            stores=stores,
            products=products,
            riders=riders,
        )
        assert len(engine.stores) == 4
        assert len(engine.products) == 40
        assert len(engine.riders) == 15


# ==============================================================================
# 2. FULL MENTOR DATASET INTEGRATION TESTS
# ==============================================================================


class TestMentorDatasetIntegration:
    """Verifies complete execution of all 130 commands in data/commands.txt."""

    def test_all_60_orders_accounted_for(self, full_engine: ExecutionEngine) -> None:
        assert len(full_engine.orders) == 60
        assert len(full_engine.printed_traces) == 60

    def test_status_breakdown(self, full_engine: ExecutionEngine) -> None:
        status_counts = Counter(r["status"] for r in full_engine.printed_traces)
        assert status_counts["DELIVERED"] == 55
        assert status_counts["CANCELLED"] == 3
        assert status_counts["REJECTED"] == 2

    def test_twenty_five_late_deliveries(self, full_engine: ExecutionEngine) -> None:
        late_traces = [
            r
            for r in full_engine.printed_traces
            if r["delay_min"] and float(r["delay_min"]) > 0.0
        ]
        assert len(late_traces) == 25

    def test_rider_payout_snapshot(self, full_engine: ExecutionEngine) -> None:
        assert len(full_engine.printed_rider_payouts) == 1
        payouts = full_engine.printed_rider_payouts[0]
        assert len(payouts) == 15

        payouts_by_id = {p.rider_id: p for p in payouts}
        rdr1 = payouts_by_id["RDR-01"]
        assert rdr1.batches == 4
        assert rdr1.late_orders == 4
        assert rdr1.late_deductions == Decimal("120.00")
        assert rdr1.late_deduction_capped == Decimal("90.00")

        rdr4 = payouts_by_id["RDR-04"]
        assert rdr4.batches == 3
        assert rdr4.late_orders == 3
        assert rdr4.batch_bonus_lost == 1

        rdr11 = payouts_by_id["RDR-11"]
        assert rdr11.batches == 7
        assert rdr11.late_orders == 7
        assert rdr11.batch_bonus_lost == 1
        assert rdr11.late_deductions == Decimal("30.00")
        assert rdr11.late_deduction_capped == Decimal("30.00")

    def test_printed_order_lines_count(self, full_engine: ExecutionEngine) -> None:
        # 60 printed orders with line items across the dataset
        assert len(full_engine.printed_order_lines) == 124


# ==============================================================================
# 3. PLANTED SCENARIOS (EC-01 THROUGH EC-08)
# ==============================================================================


class TestPlantedScenarios:
    """Verifies each scenario explicitly documented in data/README.md."""

    def test_ec01_order_at_exactly_3km_from_only_store(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0006 is 3.00 km from DS-04 and > 3km from others
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0006")
        assert trace["store_id"] == "DS-04"
        assert trace["distance_km"] == "3.00"
        assert trace["status"] == "DELIVERED"

    def test_ec02_midway_tie_break_and_idempotent_order(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0001 placed 18:04:30 and resent 7s later -> idempotent deduplication
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0001")
        assert trace["store_id"] == "DS-01"
        assert trace["distance_km"] == "1.81"
        assert trace["status"] == "DELIVERED"

    def test_ec03_order_placed_in_last_minute_before_closing(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0041 placed at 20:29:30, DS-04 closes 20:30:00 -> rolls to 20:30 slot
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0041")
        assert trace["store_id"] == "DS-04"
        assert trace["slot_start"] == "2026-10-16 20:30:00"
        assert trace["status"] == "DELIVERED"

    def test_ec04_substitute_candidate_priced_exactly_15_percent_above(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0013: SKU-001 (200.00) short at DS-03; SKU-002 (230.00 = +15%) substituted
        lines = [l for l in full_engine.printed_order_lines if l["order_id"] == "ORD-0013"]
        sub_line = next(l for l in lines if l["ordered_sku"] == "SKU-001")
        assert sub_line["fulfilled_sku"] == "SKU-002"
        assert sub_line["line_status"] == "SUBSTITUTED"
        assert sub_line["unit_price_charged"] == "200.00"

    def test_ec05_fulfilled_value_exactly_60_percent_proceeds(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0024: 500 total, SKU-006 0 stock, fulfilled 300 = 60.0% -> proceeds
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0024")
        assert trace["status"] == "DELIVERED"
        assert trace["original_value"] == "500.00"
        assert trace["fulfilled_value"] == "300.00"

    def test_ec07_delivery_exactly_10_minutes_late(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0003 delivered exactly 10 min 00 s late -> TIER 1 compensation
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0003")
        assert trace["delay_min"] == "10.00"
        assert trace["wallet_credit"] == "25.00"

    def test_ec08_weather_hold_eta_extension_and_diverted_order(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0018 & ORD-0019 in picking receive +10 min ETA extension with no compensation
        trace18 = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0018")
        assert "WEATHER_EXTENDED" in trace18["tags"]
        assert trace18["wallet_credit"] == "0.00"
        assert trace18["free_delivery_coupon"] == "false"

        trace19 = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0019")
        assert "WEATHER_EXTENDED" in trace19["tags"]
        assert trace19["wallet_credit"] == "0.00"

        # ORD-0021 midway cannot go to DS-02 during hold, allocated to DS-01
        trace21 = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0021")
        assert trace21["store_id"] == "DS-01"


# ==============================================================================
# 4. EXTRA TRAPS AND EDGE CASES
# ==============================================================================


class TestExtraTraps:
    """Verifies edge cases and traps documented in README lines 106-118."""

    def test_pre_picking_cancellation_is_free(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0040 cancelled before picking starts -> Rs 0 fee, 100% refund
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0040")
        assert trace["status"] == "CANCELLED"
        assert trace["cancellation_charge"] == "0.00"
        assert trace["refund_amount"] == "163.00"

    def test_picking_pre_dispatch_cancellation_charges_20(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0017 cancelled 90s after allocation -> Rs 20 charge
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0017")
        assert trace["status"] == "CANCELLED"
        assert trace["cancellation_charge"] == "20.00"
        assert trace["refund_amount"] == "280.00"

    def test_post_dispatch_cancellation_retains_delivery_fee(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0004 cancelled after dispatch
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0004")
        assert trace["status"] == "CANCELLED"
        assert trace["cancellation_charge"] == "0.00"
        assert trace["refund_amount"] == "0.00"

    def test_customer_out_of_reach_rejected(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0015 customer > 3 km from every store -> REJECTED
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0015")
        assert trace["status"] == "REJECTED"
        assert trace["store_id"] == ""
        assert trace["distance_km"] == ""

    def test_unregistered_sku_rejected(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0022 contains SKU-099 not in catalogue -> REJECTED
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0022")
        assert trace["status"] == "REJECTED"
        assert trace["original_value"] == "0.00"

    def test_surge_fee_applied_when_store_riders_drop(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0032 value exactly Rs 199.00 -> delivery fee 0, surge fee +15
        trace = next(r for r in full_engine.printed_traces if r["order_id"] == "ORD-0032")
        assert trace["original_value"] == "199.00"
        assert trace["delivery_fee"] == "0.00"
        assert trace["surge_fee"] == "15.00"
        assert "SURGE" in trace["tags"]

    def test_similar_only_brand_mismatch_becomes_partial(
        self, full_engine: ExecutionEngine
    ) -> None:
        # ORD-0029 under SIMILAR_ONLY: candidate is different brand -> partial
        lines = [l for l in full_engine.printed_order_lines if l["order_id"] == "ORD-0029"]
        line1 = next(l for l in lines if l["ordered_sku"] == "SKU-001")
        assert line1["line_status"] == "SHORT"
        assert line1["fulfilled_qty"] == 0


# ==============================================================================
# 5. CSV EXPORT METHOD TESTS
# ==============================================================================


class TestCSVExports:
    """Verifies file and string exports matching evaluation schema."""

    def test_export_order_trace_csv_returns_valid_content(
        self, full_engine: ExecutionEngine
    ) -> None:
        csv_str = full_engine.export_order_trace_csv()
        lines = [l for l in csv_str.strip().split("\n") if l]
        # 1 header + 60 order rows
        assert len(lines) == 61
        header = lines[0].split(",")
        assert header[0] == "order_id"
        assert header[-1] == "rule_ids"
        assert len(header) == 22

    def test_export_order_lines_csv_returns_valid_content(
        self, full_engine: ExecutionEngine
    ) -> None:
        csv_str = full_engine.export_order_lines_csv()
        lines = [l for l in csv_str.strip().split("\n") if l]
        assert len(lines) == 125  # 1 header + 124 lines
        header = lines[0].split(",")
        assert header[0] == "order_id"
        assert header[-1] == "line_amount"
        assert len(header) == 9

    def test_export_rider_payouts_csv_returns_valid_content(
        self, full_engine: ExecutionEngine
    ) -> None:
        csv_str = full_engine.export_rider_payouts_csv()
        lines = [l for l in csv_str.strip().split("\n") if l]
        assert len(lines) == 16  # 1 header + 15 riders
        header = lines[0].split(",")
        assert header[0] == "date"
        assert header[-1] == "late_deduction_capped"
        assert len(header) == 7

    def test_export_to_file_path(
        self, full_engine: ExecutionEngine, tmp_path: Path
    ) -> None:
        trace_file = tmp_path / "order_trace.csv"
        lines_file = tmp_path / "order_lines.csv"
        payouts_file = tmp_path / "rider_payouts.csv"

        full_engine.export_order_trace_csv(trace_file)
        full_engine.export_order_lines_csv(lines_file)
        full_engine.export_rider_payouts_csv(payouts_file)

        assert trace_file.exists()
        assert lines_file.exists()
        assert payouts_file.exists()
        assert trace_file.stat().st_size > 0
        assert lines_file.stat().st_size > 0
        assert payouts_file.stat().st_size > 0


# ==============================================================================
# 6. UNIT TESTS ON COMMAND DISPATCH AND ERROR HANDLING
# ==============================================================================


class TestCommandDispatchAndUnitBehaviors:
    """Verifies edge behaviors on individual command handlers."""

    def test_unsupported_command_raises_value_error(self) -> None:
        engine = ExecutionEngine.from_directory(DATA_DIR)

        class DummyCommand:
            command_type = "UNKNOWN"

        with pytest.raises(ValueError, match="Unsupported command type"):
            engine.process_command(DummyCommand())  # type: ignore

    def test_riders_command_updates_store_and_pricing_registry(self) -> None:
        engine = ExecutionEngine.from_directory(DATA_DIR)
        assert engine.stores["DS-01"].active_riders == 5
        cmd = RidersCommand(
            line_number=38,
            raw_command="RIDERS|DS-01|2026-10-16 20:00:00|2",
            store_id="DS-01",
            ts=datetime(2026, 10, 16, 20, 0, 0),
            active_riders=2,
        )
        engine.process_command(cmd)
        assert engine.stores["DS-01"].active_riders == 2

    def test_cancel_non_existent_order_is_noop(self) -> None:
        engine = ExecutionEngine.from_directory(DATA_DIR)
        cmd = CancelCommand(
            line_number=1,
            raw_command="CANCEL|ORD-NONEXIST|2026-10-16 19:00:00",
            order_id="ORD-NONEXIST",
            ts=datetime(2026, 10, 16, 19, 0, 0),
        )
        engine.process_command(cmd)
        assert "ORD-NONEXIST" not in engine.orders

    def test_weather_hold_on_non_existent_store_is_noop(self) -> None:
        engine = ExecutionEngine.from_directory(DATA_DIR)
        cmd = WeatherHoldCommand(
            line_number=1,
            raw_command="WEATHER_HOLD|DS-99|2026-10-16 19:00:00",
            store_id="DS-99",
            ts=datetime(2026, 10, 16, 19, 0, 0),
        )
        engine.process_command(cmd)
        assert "DS-99" not in engine.stores
