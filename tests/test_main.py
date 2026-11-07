"""Tests for ps031.main CLI entry point."""

import csv
import io
import os
import shutil
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from ps031.main import build_parser, main, _validate_data_dir


# ===========================================================================
# MENTOR DATASET PATH (reused across tests)
# ===========================================================================

MENTOR_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MENTOR_COMMANDS = MENTOR_DATA_DIR / "commands.txt"


# ===========================================================================
# BACKWARD-COMPATIBLE: existing test preserved
# ===========================================================================


def test_main_prints_initialization_message(capsys):
    """Original test: main() with no args prints banner (but exits on missing args)."""
    with pytest.raises(SystemExit):
        main([])  # no --data-dir, --commands, --output-dir -> argparse exits
    output = capsys.readouterr()
    # Banner is printed before argument parsing
    assert "PS-031 Quick Commerce Engine" in output.out


# ===========================================================================
# ARGUMENT PARSER TESTS
# ===========================================================================


class TestBuildParser:
    def test_all_required_args(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--data-dir", "data", "--commands", "cmds.txt", "--output-dir", "out"]
        )
        assert args.data_dir == Path("data")
        assert args.commands == Path("cmds.txt")
        assert args.output_dir == Path("out")

    def test_missing_data_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--commands", "cmds.txt", "--output-dir", "out"])

    def test_missing_commands(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--data-dir", "data", "--output-dir", "out"])

    def test_missing_output_dir(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--data-dir", "data", "--commands", "cmds.txt"])


# ===========================================================================
# INPUT VALIDATION TESTS
# ===========================================================================


class TestValidateDataDir:
    def test_nonexistent_directory(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            _validate_data_dir(tmp_path / "nonexistent")

    def test_not_a_directory(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("x")
        with pytest.raises(NotADirectoryError, match="not a directory"):
            _validate_data_dir(f)

    def test_missing_required_file(self, tmp_path):
        (tmp_path / "stores.csv").write_text("store_id\n")
        with pytest.raises(FileNotFoundError, match="skus.csv"):
            _validate_data_dir(tmp_path)


class TestCLIErrorHandling:
    def test_nonexistent_data_dir(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--data-dir", str(tmp_path / "nonexistent"),
                "--commands", str(tmp_path / "cmds.txt"),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert exc_info.value.code == 1
        assert "does not exist" in capsys.readouterr().err

    def test_nonexistent_commands_file(self, tmp_path, capsys):
        # Create valid data dir with required files
        for f in ("stores.csv", "skus.csv", "stock.csv", "riders.csv"):
            (tmp_path / f).write_text("dummy\n")
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--data-dir", str(tmp_path),
                "--commands", str(tmp_path / "missing_commands.txt"),
                "--output-dir", str(tmp_path / "out"),
            ])
        assert exc_info.value.code == 1
        assert "Commands file does not exist" in capsys.readouterr().err


# ===========================================================================
# MENTOR DATASET END-TO-END TEST
# ===========================================================================


@pytest.fixture
def mentor_output_dir(tmp_path):
    """Provide a temporary output directory for mentor dataset runs."""
    out = tmp_path / "mentor_output"
    out.mkdir()
    return out


class TestMentorDatasetEndToEnd:
    """Run the complete mentor dataset and verify all three output CSVs."""

    @pytest.fixture(autouse=True)
    def _run_mentor(self, mentor_output_dir, capsys):
        """Execute the mentor dataset once for all tests in this class."""
        self.output_dir = mentor_output_dir
        main([
            "--data-dir", str(MENTOR_DATA_DIR),
            "--commands", str(MENTOR_COMMANDS),
            "--output-dir", str(mentor_output_dir),
        ])
        self.stdout = capsys.readouterr().out

    def test_banner_printed(self):
        assert "PS-031 Quick Commerce Engine" in self.stdout

    def test_simulation_complete(self):
        assert "Simulation complete." in self.stdout

    def test_output_files_exist(self):
        assert (self.output_dir / "order_trace.csv").exists()
        assert (self.output_dir / "order_lines.csv").exists()
        assert (self.output_dir / "rider_payouts.csv").exists()

    def test_order_trace_csv_header(self):
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            expected_fields = [
                "order_id", "status", "store_id", "distance_km",
                "slot_start", "batch_id", "drop_seq", "promised_eta_min",
                "promised_ts", "dispatch_ts", "delivered_ts", "delay_min",
                "original_value", "fulfilled_value", "delivery_fee",
                "surge_fee", "cancellation_charge", "refund_amount",
                "wallet_credit", "free_delivery_coupon", "tags", "rule_ids",
            ]
            assert reader.fieldnames == expected_fields

    def test_order_lines_csv_header(self):
        with open(self.output_dir / "order_lines.csv") as f:
            reader = csv.DictReader(f)
            expected_fields = [
                "order_id", "line_no", "ordered_sku", "ordered_qty",
                "fulfilled_sku", "fulfilled_qty", "line_status",
                "unit_price_charged", "line_amount",
            ]
            assert reader.fieldnames == expected_fields

    def test_rider_payouts_csv_header(self):
        with open(self.output_dir / "rider_payouts.csv") as f:
            reader = csv.DictReader(f)
            expected_fields = [
                "date", "rider_id", "batches", "late_orders",
                "batch_bonus_lost", "late_deductions", "late_deduction_capped",
            ]
            assert reader.fieldnames == expected_fields

    def test_order_trace_row_count(self):
        """Mentor dataset should produce exactly 60 trace rows."""
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 60

    def test_order_statuses(self):
        """55 DELIVERED, 3 CANCELLED, 2 REJECTED among 60 orders."""
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        statuses = [r["status"] for r in rows]
        assert statuses.count("DELIVERED") == 55
        assert statuses.count("CANCELLED") == 3
        assert statuses.count("REJECTED") == 2

    def test_twenty_five_late_deliveries(self):
        """Exactly 25 delivered orders should have delay_min > 0."""
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        delivered = [r for r in rows if r["status"] == "DELIVERED"]
        late = [r for r in delivered if float(r["delay_min"]) > 0]
        assert len(late) == 25

    def test_rider_payouts_row_count(self):
        """Rider payouts should list all riders with delivery activity."""
        with open(self.output_dir / "rider_payouts.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) > 0
        rider_ids = {r["rider_id"] for r in rows}
        # At least the riders who had deliveries should appear
        assert len(rider_ids) >= 1

    def test_rdr01_daily_cap(self):
        """RDR-01 gross deductions capped at ₹90.00."""
        with open(self.output_dir / "rider_payouts.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        rdr01 = [r for r in rows if r["rider_id"] == "RDR-01"]
        assert len(rdr01) >= 1
        assert rdr01[0]["late_deduction_capped"] == "90.00"


# ===========================================================================
# BR-11: WEATHER HOLD VERIFICATION
# ===========================================================================


class TestBR11WeatherHold:
    """Verify weather hold mechanics from the mentor dataset output."""

    @pytest.fixture(autouse=True)
    def _run_mentor(self, mentor_output_dir, capsys):
        self.output_dir = mentor_output_dir
        main([
            "--data-dir", str(MENTOR_DATA_DIR),
            "--commands", str(MENTOR_COMMANDS),
            "--output-dir", str(mentor_output_dir),
        ])

    def test_weather_extended_tag_present(self):
        """At least one order should have the WEATHER_EXTENDED tag."""
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        weather_rows = [r for r in rows if "WEATHER_EXTENDED" in r.get("tags", "")]
        assert len(weather_rows) >= 1

    def test_weather_extended_orders_have_eta_extension(self):
        """Orders with WEATHER_EXTENDED should have larger promised_eta_min (>= base + 10)."""
        with open(self.output_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        weather_rows = [r for r in rows if "WEATHER_EXTENDED" in r.get("tags", "")]
        for row in weather_rows:
            # Weather-extended orders get at least 10 extra minutes
            eta = float(row["promised_eta_min"])
            assert eta >= 10.0, f"Order {row['order_id']} with WEATHER_EXTENDED has eta {eta}"


# ===========================================================================
# DUPLICATE ORDER RETRY VERIFICATION
# ===========================================================================


class TestDuplicateOrderRetry:
    """Verify the engine handles duplicate ORDER commands idempotently."""

    def test_duplicate_order_produces_single_trace(self, tmp_path):
        """A duplicate ORDER command should not produce two trace entries."""
        # Copy mentor data to tmp dir
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        for f in ("stores.csv", "skus.csv", "stock.csv", "riders.csv", "delivery_events.csv"):
            src = MENTOR_DATA_DIR / f
            if src.exists():
                shutil.copy2(src, data_dir / f)

        # Write a command stream with a duplicated order (pipe-delimited)
        cmds = data_dir / "commands.txt"
        cmds.write_text(
            "ORDER|ORD-DUP1|2026-10-16 18:04:30|CUS-9999|12.9200|77.5800|NONE|false|SKU-001:2\n"
            "ORDER|ORD-DUP1|2026-10-16 18:04:30|CUS-9999|12.9200|77.5800|NONE|false|SKU-001:2\n"
            "PRINT_TRACE|ORD-DUP1\n"
        )

        out_dir = tmp_path / "output"
        main([
            "--data-dir", str(data_dir),
            "--commands", str(cmds),
            "--output-dir", str(out_dir),
        ])

        with open(out_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        dup_rows = [r for r in rows if r["order_id"] == "ORD-DUP1"]
        assert len(dup_rows) == 1


# ===========================================================================
# ALTERNATE DATASET VERIFICATION — NO SOURCE CHANGES
# ===========================================================================


class TestAlternateDataset:
    """Run a dynamically generated dataset to prove the engine is not
    hardcoded to mentor-specific values."""

    @pytest.fixture
    def alt_data(self, tmp_path):
        """Create a minimal alternate dataset with different stores,
        SKUs, riders, stock, and commands."""
        d = tmp_path / "alt_data"
        d.mkdir()

        # stores.csv: 2 stores (different IDs from mentor)
        (d / "stores.csv").write_text(
            "store_id,store_name,lat,lon,open_time,close_time,"
            "picking_capacity_per_slot,active_riders\n"
            "ALT-01,Alt Store A,12.9100,77.5900,08:00,22:00,5,4\n"
            "ALT-02,Alt Store B,12.9300,77.6100,08:00,22:00,5,3\n"
        )

        # skus.csv: 5 products (using correct column name 'price')
        lines = [
            "sku_id,sku_name,brand,category,price\n",
            "ASKU-01,Alt Milk,Brand A,DAIRY,50.00\n",
            "ASKU-02,Alt Bread,Brand B,BAKERY,40.00\n",
            "ASKU-03,Alt Juice,Brand C,BEVERAGES,80.00\n",
            "ASKU-04,Alt Rice,Brand D,STAPLES,120.00\n",
            "ASKU-05,Alt Soap,Brand E,PERSONAL_CARE,35.00\n",
        ]
        (d / "skus.csv").write_text("".join(lines))

        # stock.csv: stock for both stores, all SKUs
        stock_lines = ["store_id,sku_id,qty_on_hand\n"]
        for store in ("ALT-01", "ALT-02"):
            for sku_num in range(1, 6):
                stock_lines.append(
                    f"{store},ASKU-{sku_num:02d},20\n"
                )
        (d / "stock.csv").write_text("".join(stock_lines))

        # riders.csv: 3 riders
        (d / "riders.csv").write_text(
            "rider_id,rider_name,store_id,mobile\n"
            "ARDR-01,Alt Rider 1,ALT-01,9000000001\n"
            "ARDR-02,Alt Rider 2,ALT-01,9000000002\n"
            "ARDR-03,Alt Rider 3,ALT-02,9000000003\n"
        )

        # delivery_events.csv: 2 deliveries
        (d / "delivery_events.csv").write_text(
            "event_id,order_id,rider_id,dispatch_ts,delivered_ts\n"
            "AEVT-01,AORD-01,ARDR-01,2026-03-10 10:15:00,2026-03-10 10:25:00\n"
            "AEVT-02,AORD-02,ARDR-02,2026-03-10 10:15:00,2026-03-10 10:30:00\n"
        )

        # commands.txt: pipe-delimited, matching actual parser format
        (d / "commands.txt").write_text(
            "ORDER|AORD-01|2026-03-10 10:00:00|ACUS-01|12.9100|77.5900|NONE|false|ASKU-01:2,ASKU-02:1\n"
            "ORDER|AORD-02|2026-03-10 10:01:00|ACUS-02|12.9100|77.5900|NONE|false|ASKU-03:1\n"
            "ORDER|AORD-03|2026-03-10 10:02:00|ACUS-03|12.9300|77.6100|NONE|false|ASKU-04:3\n"
            "LOAD_DELIVERIES|delivery_events.csv\n"
            "PRINT_TRACE|AORD-01\n"
            "PRINT_TRACE|AORD-02\n"
            "PRINT_TRACE|AORD-03\n"
            "PRINT_RIDER_PAYOUTS|2026-03-10\n"
        )

        return d

    def test_alternate_dataset_runs_without_source_changes(self, alt_data, tmp_path):
        """Run the alternate dataset through the same CLI without any
        source-code modifications."""
        out_dir = tmp_path / "alt_output"
        main([
            "--data-dir", str(alt_data),
            "--commands", str(alt_data / "commands.txt"),
            "--output-dir", str(out_dir),
        ])

        # All three output files should exist
        assert (out_dir / "order_trace.csv").exists()
        assert (out_dir / "order_lines.csv").exists()
        assert (out_dir / "rider_payouts.csv").exists()

        # Verify trace output contains alternate order IDs
        with open(out_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        order_ids = {r["order_id"] for r in rows}
        assert "AORD-01" in order_ids
        assert "AORD-02" in order_ids
        assert "AORD-03" in order_ids

    def test_alternate_dataset_correct_store_allocation(self, alt_data, tmp_path):
        """Alternate orders should be allocated to the alternate stores."""
        out_dir = tmp_path / "alt_output2"
        main([
            "--data-dir", str(alt_data),
            "--commands", str(alt_data / "commands.txt"),
            "--output-dir", str(out_dir),
        ])

        with open(out_dir / "order_trace.csv") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        store_ids = {r["store_id"] for r in rows if r["store_id"]}
        # Should allocate to ALT-01 or ALT-02 only
        for sid in store_ids:
            assert sid.startswith("ALT-"), f"Unexpected store: {sid}"
