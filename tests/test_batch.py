from datetime import datetime, timezone
import pytest

from ps031.models.batch import Batch, BatchStatus
from ps031.models.order import Order


@pytest.fixture
def sample_orders():
    return [
        Order(order_id="ord_01", customer_id="cust_01"),
        Order(order_id="ord_02", customer_id="cust_02"),
        Order(order_id="ord_03", customer_id="cust_03"),
    ]


# ---------------------------------------------------------------------------
# Construction (Positional & Keyword)
# ---------------------------------------------------------------------------


def test_batch_valid_positional_construction_with_defaults():
    """Verify positional construction with required arguments only."""
    now = datetime(2026, 11, 3, 11, 0)
    b = Batch("batch_pos_01", "rider_01", now)

    assert b.batch_id == "batch_pos_01"
    assert b.rider_id == "rider_01"
    assert b.created_at == now
    assert b.orders == []
    assert b.dispatched_at is None
    assert b.status == BatchStatus.CREATED
    assert b.status == "CREATED"


def test_batch_valid_positional_construction_full(sample_orders):
    """Verify positional construction with all arguments provided."""
    now = datetime(2026, 11, 3, 11, 0)
    disp = datetime(2026, 11, 3, 11, 5)
    b = Batch("batch_pos_02", "rider_02", now, sample_orders, disp, "DISPATCHED")

    assert b.batch_id == "batch_pos_02"
    assert b.rider_id == "rider_02"
    assert b.created_at == now
    assert len(b.orders) == 3
    assert b.orders[0].order_id == "ord_01"
    assert b.dispatched_at == disp
    assert b.status == BatchStatus.DISPATCHED
    assert b.status == "DISPATCHED"


def test_batch_valid_keyword_construction_with_defaults():
    """Verify keyword construction with required arguments and defaults."""
    now = datetime(2026, 11, 3, 11, 0)
    b = Batch(batch_id="batch_kw_01", rider_id="rider_01", created_at=now)

    assert b.batch_id == "batch_kw_01"
    assert b.rider_id == "rider_01"
    assert b.created_at == now
    assert b.orders == []
    assert b.dispatched_at is None
    assert b.status == BatchStatus.CREATED
    assert b.status == "CREATED"


def test_batch_valid_keyword_construction_full(sample_orders):
    """Verify keyword construction with all arguments provided."""
    now = datetime(2026, 11, 3, 11, 0)
    disp = datetime(2026, 11, 3, 11, 5)
    b = Batch(
        batch_id="batch_kw_02",
        rider_id="rider_02",
        created_at=now,
        orders=sample_orders,
        dispatched_at=disp,
        status="DISPATCHED",
    )
    assert b.batch_id == "batch_kw_02"
    assert b.rider_id == "rider_02"
    assert b.created_at == now
    assert len(b.orders) == 3
    assert b.dispatched_at == disp
    assert b.status == BatchStatus.DISPATCHED
    assert b.status == "DISPATCHED"


def test_batch_status_enum_members():
    """Batch accepts all defined BatchStatus enum members directly."""
    now = datetime(2026, 11, 3, 11, 0)
    for status_val in [
        BatchStatus.CREATED,
        BatchStatus.DISPATCHED,
        BatchStatus.DELIVERED,
        BatchStatus.CANCELLED,
    ]:
        b = Batch("b1", "r1", now, status=status_val)
        assert b.status is status_val


def test_batch_status_string_normalization():
    """Batch normalizes case-insensitive valid status strings."""
    now = datetime(2026, 11, 3, 11, 0)
    assert Batch("b1", "r1", now, status="created").status is BatchStatus.CREATED
    assert Batch("b1", "r1", now, status="Dispatched").status is BatchStatus.DISPATCHED
    assert Batch("b1", "r1", now, status="delivered").status is BatchStatus.DELIVERED
    assert Batch("b1", "r1", now, status="CANCELLED").status is BatchStatus.CANCELLED


def test_batch_rejects_invalid_status_strings():
    """Batch rejects unrecognized status strings."""
    now = datetime(2026, 11, 3, 11, 0)
    for invalid in ["IN_TRANSIT", "SHIPPED", "PENDING", "UNKNOWN"]:
        with pytest.raises(ValueError, match="Invalid batch status"):
            Batch("b1", "r1", now, status=invalid)


def test_batch_rejects_invalid_status_types():
    """Batch rejects non-string and non-BatchStatus status types."""
    now = datetime(2026, 11, 3, 11, 0)
    for bad_status in [123, True, False, ["CREATED"], {"status": "CREATED"}]:
        with pytest.raises(TypeError, match="Status must be a string"):
            Batch("b1", "r1", now, status=bad_status)


# ---------------------------------------------------------------------------
# created_at Required Check
# ---------------------------------------------------------------------------


def test_batch_requires_created_at_argument():
    """Omitting created_at from constructor raises TypeError."""
    with pytest.raises(TypeError):
        Batch(batch_id="b1", rider_id="r1")  # pylint: disable=no-value-for-parameter

    with pytest.raises(TypeError):
        Batch("b1", "r1")  # pylint: disable=no-value-for-parameter


def test_batch_rejects_none_created_at():
    """Passing None for created_at raises TypeError."""
    with pytest.raises(TypeError, match="created_at is required"):
        Batch(batch_id="b1", rider_id="r1", created_at=None)

    with pytest.raises(TypeError, match="created_at is required"):
        Batch("b1", "r1", None)


# ---------------------------------------------------------------------------
# Order List Isolation & Limits
# ---------------------------------------------------------------------------


def test_batch_orders_list_isolation(sample_orders):
    """Mutating caller list does not affect batch orders list."""
    now = datetime(2026, 11, 3, 11, 0)
    caller_orders = list(sample_orders[:2])
    b = Batch("b1", "r1", now, orders=caller_orders)

    caller_orders.append(sample_orders[2])
    assert len(b.orders) == 2


def test_batch_permits_arbitrary_order_counts_structurally():
    """Batch model structurally permits orders without enforcing BR-08 operational limits.

    BR-08 operational capacity (maximum 3 orders) is enforced by BatchingService.
    """
    now = datetime(2026, 11, 3, 11, 0)
    four_orders = [
        Order(order_id="o1", customer_id="c1"),
        Order(order_id="o2", customer_id="c2"),
        Order(order_id="o3", customer_id="c3"),
        Order(order_id="o4", customer_id="c4"),
    ]
    b = Batch("b1", "r1", now, orders=four_orders)
    assert len(b.orders) == 4


def test_batch_rejects_duplicate_order_ids():
    """Duplicate order IDs within one batch must raise ValueError."""
    now = datetime(2026, 11, 3, 11, 0)
    duplicate_orders = [
        Order(order_id="dup_ord", customer_id="c1"),
        Order(order_id="dup_ord", customer_id="c2"),
    ]
    with pytest.raises(ValueError, match="Duplicate order ID"):
        Batch("b1", "r1", now, orders=duplicate_orders)


def test_batch_rejects_non_order_items():
    """Non-Order objects in orders list raise TypeError."""
    now = datetime(2026, 11, 3, 11, 0)
    with pytest.raises(TypeError, match="Order instances"):
        Batch("b1", "r1", now, orders=["not_an_order"])


# ---------------------------------------------------------------------------
# Validation of Identifiers and Timestamps
# ---------------------------------------------------------------------------


def test_batch_rejects_empty_identifiers():
    """Empty batch_id or rider_id raises ValueError."""
    now = datetime(2026, 11, 3, 11, 0)
    with pytest.raises(ValueError, match="must not be empty"):
        Batch("", "r1", now)

    with pytest.raises(ValueError, match="must not be empty"):
        Batch("b1", "   ", now)

    with pytest.raises(ValueError, match="must not be empty"):
        Batch("b1", "r1", now, status="")


def test_batch_rejects_invalid_timestamp_types():
    """created_at and dispatched_at must be datetime."""
    with pytest.raises(TypeError, match="datetime"):
        Batch("b1", "r1", "2026-11-03 11:00:00")

    now = datetime(2026, 11, 3, 11, 0)
    with pytest.raises(TypeError, match="datetime"):
        Batch("b1", "r1", now, dispatched_at="11:05")

    with pytest.raises(TypeError, match="datetime"):
        Batch("b1", "r1", now, dispatched_at=True)


def test_batch_rejects_dispatch_earlier_than_creation():
    """dispatched_at cannot be earlier than created_at."""
    created = datetime(2026, 11, 3, 11, 10)
    dispatched = datetime(2026, 11, 3, 11, 5)
    with pytest.raises(ValueError, match="earlier than created_at"):
        Batch("b1", "r1", created, dispatched_at=dispatched)


def test_batch_rejects_timezone_awareness_mismatch():
    """Comparing timezone-aware and timezone-naive timestamps raises TypeError."""
    naive_created = datetime(2026, 11, 3, 11, 0)
    aware_dispatched = datetime(2026, 11, 3, 11, 5, tzinfo=timezone.utc)
    with pytest.raises(TypeError, match="timezone-aware and timezone-naive"):
        Batch("b1", "r1", naive_created, dispatched_at=aware_dispatched)

    aware_created = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
    naive_dispatched = datetime(2026, 11, 3, 11, 5)
    with pytest.raises(TypeError, match="timezone-aware and timezone-naive"):
        Batch("b1", "r1", aware_created, dispatched_at=naive_dispatched)


def test_batch_accepts_timezone_aware_timestamps():
    """Both created_at and dispatched_at can be timezone-aware."""
    created = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
    dispatched = datetime(2026, 11, 3, 11, 5, tzinfo=timezone.utc)
    b = Batch("b1", "r1", created, dispatched_at=dispatched)
    assert b.created_at == created
    assert b.dispatched_at == dispatched
