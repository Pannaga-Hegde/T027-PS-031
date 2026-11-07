"""Tests for CSV data loaders against mentor dataset specifications and domain models."""

from datetime import datetime, time
from decimal import Decimal
import io
from pathlib import Path
import pytest

from ps031.loaders import (
    DELIVERY_EVENTS_REQUIRED_COLUMNS,
    DeliveryEventRecord,
    ProductCatalog,
    RIDERS_REQUIRED_COLUMNS,
    RiderRecord,
    RiderRoster,
    SKUS_REQUIRED_COLUMNS,
    STORES_REQUIRED_COLUMNS,
    STOCK_REQUIRED_COLUMNS,
    StockRecord,
    StoreCatalog,
    VALID_SKU_CATEGORIES,
    load_delivery_events,
    load_riders,
    load_riders_list,
    load_skus,
    load_skus_list,
    load_stock,
    load_stock_dict,
    load_stores,
    load_stores_list,
)
from ps031.models.coordinates import Coordinates
from ps031.models.operating_hours import OperatingHours
from ps031.models.product import Product
from ps031.models.rider import Rider, RiderStatus
from ps031.models.store import Store

DATA_DIR = Path("data")


# ==============================================================================
# 1. ACTUAL MENTOR DATASET TESTS
# ==============================================================================


class TestMentorStoresCsv:
    """Validate loading of the actual mentor stores.csv dataset."""

    def test_load_all_stores(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        assert isinstance(stores, StoreCatalog)
        assert len(stores) == 4
        assert set(stores.keys()) == {"DS-01", "DS-02", "DS-03", "DS-04"}

    def test_store_ds01_attributes(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        s = stores["DS-01"]
        assert s.store_id == "DS-01"
        assert s.name == "Koramangala 5th Block"
        assert isinstance(s.location, Coordinates)
        assert s.location.latitude == pytest.approx(12.9352)
        assert s.location.longitude == pytest.approx(77.6245)
        assert isinstance(s.operating_hours, OperatingHours)
        assert s.operating_hours.open_time == time(6, 0, 0)
        assert s.operating_hours.close_time == time(23, 0, 0)
        assert s.picking_capacity == 12
        assert s.active_riders == 5
        assert s.inventory == {}

    def test_store_ds04_small_store_attributes(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        s = stores["DS-04"]
        assert s.store_id == "DS-04"
        assert s.name == "Jayanagar 4th Block"
        assert s.location.latitude == pytest.approx(12.9308)
        assert s.location.longitude == pytest.approx(77.5838)
        assert s.operating_hours.open_time == time(7, 0, 0)
        assert s.operating_hours.close_time == time(20, 30, 0)
        assert s.picking_capacity == 4
        assert s.active_riders == 3

    def test_load_stores_as_list(self):
        stores_list = load_stores_list(DATA_DIR / "stores.csv")
        assert isinstance(stores_list, list)
        assert len(stores_list) == 4
        assert all(isinstance(s, Store) for s in stores_list)
        ids = [s.store_id for s in stores_list]
        assert ids == ["DS-01", "DS-02", "DS-03", "DS-04"]


class TestMentorSkusCsv:
    """Validate loading of the actual mentor skus.csv dataset."""

    def test_load_all_skus(self):
        skus = load_skus(DATA_DIR / "skus.csv")
        assert isinstance(skus, ProductCatalog)
        assert len(skus) == 40
        assert "SKU-001" in skus
        assert "SKU-040" in skus

    def test_sku_attributes(self):
        skus = load_skus(DATA_DIR / "skus.csv")
        p1 = skus["SKU-001"]
        assert isinstance(p1, Product)
        assert p1.sku == "SKU-001"
        assert p1.name == "Kaveri Farms Ghee 500 ml"
        assert p1.brand == "Kaveri Farms"
        assert p1.category == "DAIRY"
        assert p1.price == Decimal("200.00")

        p2 = skus["SKU-002"]
        assert p2.sku == "SKU-002"
        assert p2.price == Decimal("230.00")
        assert p2.category == "DAIRY"

    def test_all_categories_in_valid_enum(self):
        skus = load_skus(DATA_DIR / "skus.csv")
        for sku_id, product in skus.items():
            assert product.category in VALID_SKU_CATEGORIES, f"{sku_id} has invalid category {product.category}"
            assert isinstance(product.price, Decimal)
            assert product.price >= Decimal("0.00")

    def test_load_skus_as_list(self):
        skus_list = load_skus_list(DATA_DIR / "skus.csv")
        assert isinstance(skus_list, list)
        assert len(skus_list) == 40
        assert all(isinstance(p, Product) for p in skus_list)


class TestMentorStockCsv:
    """Validate loading of the actual mentor stock.csv dataset."""

    def test_load_stock_records(self):
        records = load_stock(DATA_DIR / "stock.csv")
        assert isinstance(records, list)
        assert len(records) == 160  # 4 stores x 40 SKUs
        assert all(isinstance(r, StockRecord) for r in records)

        # Check first record: DS-01, SKU-001, 22
        r0 = records[0]
        assert r0.store_id == "DS-01"
        assert r0.sku_id == "SKU-001"
        assert r0.qty_on_hand == 22
        assert r0.quantity == 22
        assert r0.stock == 22

    def test_load_stock_populates_stores(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        assert stores["DS-01"].inventory == {}

        records = load_stock(DATA_DIR / "stock.csv", stores=stores)
        assert len(records) == 160

        # DS-01 should now have 40 SKUs in inventory
        ds01 = stores["DS-01"]
        assert len(ds01.inventory) == 40
        assert ds01.inventory["SKU-001"] == 22
        assert ds01.inventory["SKU-002"] == 10
        assert ds01.inventory["SKU-003"] == 11
        assert ds01.inventory["SKU-004"] == 12

        # Verify all 4 stores received 40 inventory entries
        for sid in ["DS-01", "DS-02", "DS-03", "DS-04"]:
            assert len(stores[sid].inventory) == 40

    def test_load_stock_validates_against_products(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        products = load_skus(DATA_DIR / "skus.csv")
        records = load_stock(DATA_DIR / "stock.csv", stores=stores, products=products)
        assert len(records) == 160

    def test_load_stock_dict(self):
        stock_map = load_stock_dict(DATA_DIR / "stock.csv")
        assert isinstance(stock_map, dict)
        assert set(stock_map.keys()) == {"DS-01", "DS-02", "DS-03", "DS-04"}
        assert len(stock_map["DS-01"]) == 40
        assert stock_map["DS-01"]["SKU-001"] == 22


class TestMentorRidersCsv:
    """Validate loading of the actual mentor riders.csv dataset."""

    def test_load_all_riders(self):
        roster = load_riders(DATA_DIR / "riders.csv")
        assert isinstance(roster, RiderRoster)
        assert len(roster) == 15
        assert set(roster.keys()) == {f"RDR-{i:02d}" for i in range(1, 16)}

    def test_rider_attributes_and_domain_model(self):
        roster = load_riders(DATA_DIR / "riders.csv")
        r1 = roster["RDR-01"]
        assert isinstance(r1, RiderRecord)
        assert r1.rider_id == "RDR-01"
        assert r1.rider_name == "Abhishek Yadav"
        assert r1.name == "Abhishek Yadav"
        assert r1.store_id == "DS-01"
        assert r1.mobile == "6854914524"
        assert r1.status == RiderStatus.AVAILABLE
        assert r1.daily_penalty == Decimal("0.00")
        assert r1.daily_penalty_date is None

        # Domain Rider model instance
        assert isinstance(r1.rider, Rider)
        assert r1.rider.rider_id == "RDR-01"
        assert r1.rider.name == "Abhishek Yadav"
        assert r1.rider.status == RiderStatus.AVAILABLE

    def test_rider_store_assignments(self):
        roster = load_riders(DATA_DIR / "riders.csv")
        # 5 at DS-01 (RDR-01 to RDR-05)
        for i in range(1, 6):
            assert roster[f"RDR-{i:02d}"].store_id == "DS-01"
        # 4 at DS-02 (RDR-06 to RDR-09)
        for i in range(6, 10):
            assert roster[f"RDR-{i:02d}"].store_id == "DS-02"
        # 3 at DS-03 (RDR-10 to RDR-12)
        for i in range(10, 13):
            assert roster[f"RDR-{i:02d}"].store_id == "DS-03"
        # 3 at DS-04 (RDR-13 to RDR-15)
        for i in range(13, 16):
            assert roster[f"RDR-{i:02d}"].store_id == "DS-04"

    def test_load_riders_validates_against_stores(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        roster = load_riders(DATA_DIR / "riders.csv", stores=stores)
        assert len(roster) == 15

    def test_load_riders_as_list(self):
        riders_list = load_riders_list(DATA_DIR / "riders.csv")
        assert isinstance(riders_list, list)
        assert len(riders_list) == 15
        assert all(isinstance(r, RiderRecord) for r in riders_list)

    def test_to_domain_riders(self):
        roster = load_riders(DATA_DIR / "riders.csv")
        domain_dict = roster.to_domain_riders()
        assert isinstance(domain_dict, dict)
        assert len(domain_dict) == 15
        assert all(isinstance(r, Rider) for r in domain_dict.values())


class TestMentorDeliveryEventsCsv:
    """Validate loading of the actual mentor delivery_events.csv dataset."""

    def test_load_all_delivery_events(self):
        events = load_delivery_events(DATA_DIR / "delivery_events.csv")
        assert isinstance(events, list)
        assert len(events) == 55
        assert all(isinstance(e, DeliveryEventRecord) for e in events)

    def test_delivery_event_attributes(self):
        events = load_delivery_events(DATA_DIR / "delivery_events.csv")
        e0 = events[0]
        assert e0.event_id == "DEL-001"
        assert e0.order_id == "ORD-0001"
        assert e0.rider_id == "RDR-02"
        assert e0.dispatch_ts == datetime(2026, 10, 16, 18, 6, 56)
        assert e0.delivered_ts == datetime(2026, 10, 16, 18, 9, 30)
        assert e0.dispatch_ts <= e0.delivered_ts

    def test_all_events_ordered_and_valid_timestamps(self):
        events = load_delivery_events(DATA_DIR / "delivery_events.csv")
        for idx, ev in enumerate(events):
            assert ev.dispatch_ts <= ev.delivered_ts, f"Event {ev.event_id} delivered before dispatch"
            if idx > 0:
                # Mentor README: file sorted by delivered_ts
                assert events[idx - 1].delivered_ts <= ev.delivered_ts


# ==============================================================================
# 2. SOURCE FLEXIBILITY TESTS
# ==============================================================================


class TestSourceFlexibility:
    """Ensure loaders accept Path, string path, StringIO, and raw string content."""

    def test_load_from_pathlib_path(self):
        stores = load_stores(Path("data/stores.csv"))
        assert len(stores) == 4

    def test_load_from_string_path(self):
        stores = load_stores("data/stores.csv")
        assert len(stores) == 4

    def test_load_from_stringio(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,77.6245,06:00:00,23:00:00,12,5\n"
        )
        stores = load_stores(io.StringIO(csv_text))
        assert len(stores) == 1
        assert "DS-01" in stores

    def test_load_from_raw_string(self):
        csv_text = (
            "sku_id,sku_name,brand,category,price\n"
            "SKU-001,Test Product,Test Brand,DAIRY,100.00\n"
        )
        skus = load_skus(csv_text)
        assert len(skus) == 1
        assert skus["SKU-001"].name == "Test Product"

    def test_invalid_source_type_raises_typeerror(self):
        with pytest.raises(TypeError, match="Expected source to be a str, Path, or file-like object"):
            load_stores(12345)  # type: ignore


# ==============================================================================
# 3. VALIDATION & ERROR HANDLING TESTS (MALFORMED ROWS)
# ==============================================================================


class TestStoresValidation:
    """Test validation errors for stores.csv."""

    def test_missing_header_raises_valueerror(self):
        with pytest.raises(ValueError, match="CSV source is empty or missing header row"):
            load_stores("")

    def test_missing_required_column_raises_valueerror(self):
        csv_text = "store_id,store_name,lat,lon\nDS-01,Test,12.0,77.0\n"
        with pytest.raises(ValueError, match="CSV is missing required column"):
            load_stores(csv_text)

    def test_empty_store_id_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            " ,Test Store,12.9352,77.6245,06:00:00,23:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: 'store_id' must not be empty"):
            load_stores(csv_text)

    def test_duplicate_store_id_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Store 1,12.9352,77.6245,06:00:00,23:00:00,12,5\n"
            "DS-01,Store 2,12.9352,77.6245,06:00:00,23:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 2: duplicate store_id 'DS-01'"):
            load_stores(csv_text)

    def test_invalid_latitude_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,999.0,77.6245,06:00:00,23:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: lat must be <= 90.0"):
            load_stores(csv_text)

    def test_invalid_longitude_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,-200.0,06:00:00,23:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: lon must be >= -180.0"):
            load_stores(csv_text)

    def test_invalid_time_format_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,77.6245,invalid-time,23:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: invalid open_time time"):
            load_stores(csv_text)

    def test_operating_hours_open_after_close_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,77.6245,23:00:00,06:00:00,12,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: invalid operating hours"):
            load_stores(csv_text)

    def test_negative_picking_capacity_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,77.6245,06:00:00,23:00:00,0,5\n"
        )
        with pytest.raises(ValueError, match="Row 1: picking_capacity_per_slot must be >= 1"):
            load_stores(csv_text)

    def test_negative_active_riders_raises_valueerror(self):
        csv_text = (
            "store_id,store_name,lat,lon,open_time,close_time,picking_capacity_per_slot,active_riders\n"
            "DS-01,Test Store,12.9352,77.6245,06:00:00,23:00:00,12,-1\n"
        )
        with pytest.raises(ValueError, match="Row 1: active_riders must be >= 0"):
            load_stores(csv_text)


class TestSkusValidation:
    """Test validation errors for skus.csv."""

    def test_empty_sku_id_raises_valueerror(self):
        csv_text = "sku_id,sku_name,brand,category,price\n ,Ghee,Amul,DAIRY,200.00\n"
        with pytest.raises(ValueError, match="Row 1: 'sku_id' must not be empty"):
            load_skus(csv_text)

    def test_duplicate_sku_id_raises_valueerror(self):
        csv_text = (
            "sku_id,sku_name,brand,category,price\n"
            "SKU-001,Ghee 1,Amul,DAIRY,200.00\n"
            "SKU-001,Ghee 2,Amul,DAIRY,210.00\n"
        )
        with pytest.raises(ValueError, match="Row 2: duplicate sku_id 'SKU-001'"):
            load_skus(csv_text)

    def test_invalid_category_raises_valueerror(self):
        csv_text = "sku_id,sku_name,brand,category,price\nSKU-001,Shirt,Puma,APPAREL,500.00\n"
        with pytest.raises(ValueError, match="Row 1: invalid category 'APPAREL'"):
            load_skus(csv_text)

    def test_case_insensitive_valid_category_accepted(self):
        csv_text = "sku_id,sku_name,brand,category,price\nSKU-001,Milk,Amul,dairy,50.00\n"
        skus = load_skus(csv_text)
        assert skus["SKU-001"].category == "DAIRY"

    def test_negative_price_raises_valueerror(self):
        csv_text = "sku_id,sku_name,brand,category,price\nSKU-001,Milk,Amul,DAIRY,-10.00\n"
        with pytest.raises(ValueError, match="Row 1: price must be >= 0.00"):
            load_skus(csv_text)

    def test_invalid_price_format_raises_valueerror(self):
        csv_text = "sku_id,sku_name,brand,category,price\nSKU-001,Milk,Amul,DAIRY,abc\n"
        with pytest.raises(ValueError, match="Row 1: invalid price monetary decimal"):
            load_skus(csv_text)


class TestStockValidation:
    """Test validation errors for stock.csv."""

    def test_negative_qty_raises_valueerror(self):
        csv_text = "store_id,sku_id,qty_on_hand\nDS-01,SKU-001,-5\n"
        with pytest.raises(ValueError, match="Row 1: qty_on_hand must be >= 0"):
            load_stock(csv_text)

    def test_duplicate_stock_entry_raises_valueerror(self):
        csv_text = (
            "store_id,sku_id,qty_on_hand\n"
            "DS-01,SKU-001,10\n"
            "DS-01,SKU-001,20\n"
        )
        with pytest.raises(ValueError, match="Row 2: duplicate stock entry"):
            load_stock(csv_text)

    def test_store_not_found_raises_valueerror(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        csv_text = "store_id,sku_id,qty_on_hand\nDS-99,SKU-001,10\n"
        with pytest.raises(ValueError, match="Row 1: store_id 'DS-99' not found in provided stores"):
            load_stock(csv_text, stores=stores)

    def test_sku_not_found_raises_valueerror(self):
        products = load_skus(DATA_DIR / "skus.csv")
        csv_text = "store_id,sku_id,qty_on_hand\nDS-01,SKU-999,10\n"
        with pytest.raises(ValueError, match="Row 1: sku_id 'SKU-999' not found in provided product catalog"):
            load_stock(csv_text, products=products)


class TestRidersValidation:
    """Test validation errors for riders.csv."""

    def test_duplicate_rider_id_raises_valueerror(self):
        csv_text = (
            "rider_id,rider_name,store_id,mobile\n"
            "RDR-01,Rider 1,DS-01,9876543210\n"
            "RDR-01,Rider 2,DS-01,9876543211\n"
        )
        with pytest.raises(ValueError, match="Row 2: duplicate rider_id 'RDR-01'"):
            load_riders(csv_text)

    def test_invalid_mobile_not_10_digits_raises_valueerror(self):
        csv_text = "rider_id,rider_name,store_id,mobile\nRDR-01,Rider,DS-01,12345\n"
        with pytest.raises(ValueError, match="Row 1: invalid mobile '12345'. Expected exactly 10 digits"):
            load_riders(csv_text)

    def test_invalid_mobile_alpha_raises_valueerror(self):
        csv_text = "rider_id,rider_name,store_id,mobile\nRDR-01,Rider,DS-01,987654321a\n"
        with pytest.raises(ValueError, match="Row 1: invalid mobile"):
            load_riders(csv_text)

    def test_home_store_not_in_stores_raises_valueerror(self):
        stores = load_stores(DATA_DIR / "stores.csv")
        csv_text = "rider_id,rider_name,store_id,mobile\nRDR-01,Rider,DS-99,9876543210\n"
        with pytest.raises(ValueError, match="Row 1: home store 'DS-99' for rider 'RDR-01' not found in stores"):
            load_riders(csv_text, stores=stores)


class TestDeliveryEventsValidation:
    """Test validation errors for delivery_events.csv."""

    def test_delivered_before_dispatch_raises_valueerror(self):
        csv_text = (
            "event_id,order_id,rider_id,dispatch_ts,delivered_ts\n"
            "DEL-001,ORD-0001,RDR-01,2026-10-16 18:10:00,2026-10-16 18:05:00\n"
        )
        with pytest.raises(ValueError, match="Row 1: delivered_ts .* cannot be before dispatch_ts"):
            load_delivery_events(csv_text)

    def test_duplicate_event_id_raises_valueerror(self):
        csv_text = (
            "event_id,order_id,rider_id,dispatch_ts,delivered_ts\n"
            "DEL-001,ORD-0001,RDR-01,2026-10-16 18:00:00,2026-10-16 18:10:00\n"
            "DEL-001,ORD-0002,RDR-02,2026-10-16 18:05:00,2026-10-16 18:15:00\n"
        )
        with pytest.raises(ValueError, match="Row 2: duplicate event_id 'DEL-001'"):
            load_delivery_events(csv_text)

    def test_invalid_timestamp_format_raises_valueerror(self):
        csv_text = (
            "event_id,order_id,rider_id,dispatch_ts,delivered_ts\n"
            "DEL-001,ORD-0001,RDR-01,2026/10/16 18:00:00,2026-10-16 18:10:00\n"
        )
        with pytest.raises(ValueError, match="Row 1: invalid dispatch_ts timestamp"):
            load_delivery_events(csv_text)

    def test_empty_order_id_raises_valueerror(self):
        csv_text = (
            "event_id,order_id,rider_id,dispatch_ts,delivered_ts\n"
            "DEL-001, ,RDR-01,2026-10-16 18:00:00,2026-10-16 18:10:00\n"
        )
        with pytest.raises(ValueError, match="Row 1: 'order_id' must not be empty"):
            load_delivery_events(csv_text)
