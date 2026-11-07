"""Tests for command parser against mentor dataset specification and edge cases."""

from datetime import date, datetime
import io
from pathlib import Path
import pytest

from ps031.command_parser import (
    CancelCommand,
    CommandParseError,
    CommandType,
    LoadDeliveriesCommand,
    OrderCommand,
    OrderItemRequest,
    PrintRiderPayoutsCommand,
    PrintTraceCommand,
    RidersCommand,
    WeatherHoldCommand,
    is_comment_or_blank,
    parse_command_line,
    parse_commands,
)
from ps031.models.coordinates import Coordinates
from ps031.models.order import SubstitutionPolicy

COMMANDS_FILE = Path("data/commands.txt")


# ==============================================================================
# 1. ACTUAL MENTOR DATASET TESTS
# ==============================================================================


class TestMentorCommandsDataset:
    """Validate parsing of the mentor commands.txt file."""

    def test_parse_all_130_commands(self):
        commands = parse_commands(COMMANDS_FILE)
        assert len(commands) == 130

    def test_command_type_breakdown(self):
        commands = parse_commands(COMMANDS_FILE)
        counts = {}
        for c in commands:
            counts[c.command_type] = counts.get(c.command_type, 0) + 1

        assert counts[CommandType.ORDER] == 61
        assert counts[CommandType.PRINT_TRACE] == 60
        assert counts[CommandType.RIDERS] == 3
        assert counts[CommandType.CANCEL] == 3
        assert counts[CommandType.WEATHER_HOLD] == 1
        assert counts[CommandType.LOAD_DELIVERIES] == 1
        assert counts[CommandType.PRINT_RIDER_PAYOUTS] == 1

    def test_first_command_riders(self):
        commands = parse_commands(COMMANDS_FILE)
        cmd = commands[0]
        assert isinstance(cmd, RidersCommand)
        assert cmd.line_number == 2
        assert cmd.store_id == "DS-03"
        assert cmd.ts == datetime(2026, 10, 16, 17, 59, 0)
        assert cmd.active_riders == 2

    def test_first_order_command(self):
        commands = parse_commands(COMMANDS_FILE)
        cmd = commands[1]
        assert isinstance(cmd, OrderCommand)
        assert cmd.line_number == 3
        assert cmd.order_id == "ORD-0001"
        assert cmd.order_ts == datetime(2026, 10, 16, 18, 4, 30)
        assert cmd.customer_id == "CUS-3657"
        assert cmd.lat == pytest.approx(12.923400)
        assert cmd.lon == pytest.approx(77.635950)
        assert isinstance(cmd.location, Coordinates)
        assert cmd.substitution_pref == SubstitutionPolicy.ALLOW
        assert cmd.slow_delivery_ok is False
        assert len(cmd.items) == 2
        assert cmd.items[0] == OrderItemRequest(sku_id="SKU-008", quantity=1)
        assert cmd.items[1] == OrderItemRequest(sku_id="SKU-009", quantity=2)
        assert cmd.skus == ("SKU-008", "SKU-009")
        assert cmd.total_quantity == 3

    def test_idempotent_order_resent_preserves_ordering(self):
        # EC-02: ORD-0001 sent at line 3 and line 4 identically
        commands = parse_commands(COMMANDS_FILE)
        cmd1 = commands[1]
        cmd2 = commands[2]
        assert cmd1.line_number == 3
        assert cmd2.line_number == 4
        assert cmd1.order_id == "ORD-0001"
        assert cmd2.order_id == "ORD-0001"

    def test_cancel_commands_in_mentor_stream(self):
        commands = parse_commands(COMMANDS_FILE)
        cancels = [c for c in commands if isinstance(c, CancelCommand)]
        assert len(cancels) == 3

        # Line 10: CANCEL|ORD-0004|2026-10-16 18:35:00
        assert cancels[0].line_number == 10
        assert cancels[0].order_id == "ORD-0004"
        assert cancels[0].ts == datetime(2026, 10, 16, 18, 35, 0)

        # Line 22: CANCEL|ORD-0017|2026-10-16 19:06:30
        assert cancels[1].line_number == 22
        assert cancels[1].order_id == "ORD-0017"

        # Line 49: CANCEL|ORD-0040|2026-10-16 20:27:00
        assert cancels[2].line_number == 49
        assert cancels[2].order_id == "ORD-0040"

    def test_weather_hold_command(self):
        commands = parse_commands(COMMANDS_FILE)
        holds = [c for c in commands if isinstance(c, WeatherHoldCommand)]
        assert len(holds) == 1
        h = holds[0]
        assert h.line_number == 25
        assert h.store_id == "DS-02"
        assert h.ts == datetime(2026, 10, 16, 19, 12, 0)

    def test_load_deliveries_command(self):
        commands = parse_commands(COMMANDS_FILE)
        loads = [c for c in commands if isinstance(c, LoadDeliveriesCommand)]
        assert len(loads) == 1
        ld = loads[0]
        assert ld.line_number == 70
        assert ld.file_name == "delivery_events.csv"

    def test_print_trace_commands(self):
        commands = parse_commands(COMMANDS_FILE)
        traces = [c for c in commands if isinstance(c, PrintTraceCommand)]
        assert len(traces) == 60
        assert traces[0].line_number == 71
        assert traces[0].order_id == "ORD-0001"
        assert traces[-1].order_id == "ORD-0060"

    def test_print_rider_payouts_command(self):
        commands = parse_commands(COMMANDS_FILE)
        payouts = [c for c in commands if isinstance(c, PrintRiderPayoutsCommand)]
        assert len(payouts) == 1
        p = payouts[0]
        assert p.line_number == 131
        assert p.payout_date == date(2026, 10, 16)
        assert p.date_str == "2026-10-16"


# ==============================================================================
# 2. INDIVIDUAL COMMAND TYPE PARSING TESTS
# ==============================================================================


class TestCommandTypesUnit:
    """Test parse_command_line on each command type."""

    def test_order_with_similar_only_and_slow_ok_true(self):
        line = "ORDER|ORD-9999|2026-10-16 19:00:00|CUS-1111|12.9100|77.6200|SIMILAR_ONLY|true|SKU-001:5"
        cmd = parse_command_line(line, line_number=42)
        assert isinstance(cmd, OrderCommand)
        assert cmd.line_number == 42
        assert cmd.order_id == "ORD-9999"
        assert cmd.substitution_pref == SubstitutionPolicy.SIMILAR_ONLY
        assert cmd.slow_delivery_ok is True
        assert len(cmd.items) == 1
        assert cmd.items[0] == OrderItemRequest("SKU-001", 5)

    def test_order_with_none_substitution(self):
        line = "ORDER|ORD-9998|2026-10-16 19:05:00|CUS-2222|12.9100|77.6200|NONE|false|SKU-002:1,SKU-003:3"
        cmd = parse_command_line(line)
        assert isinstance(cmd, OrderCommand)
        assert cmd.substitution_pref == SubstitutionPolicy.NONE
        assert len(cmd.items) == 2

    def test_riders_zero_active(self):
        line = "RIDERS|DS-01|2026-10-16 21:00:00|0"
        cmd = parse_command_line(line, line_number=15)
        assert isinstance(cmd, RidersCommand)
        assert cmd.store_id == "DS-01"
        assert cmd.active_riders == 0
        assert cmd.timestamp == datetime(2026, 10, 16, 21, 0, 0)

    def test_weather_hold(self):
        line = "WEATHER_HOLD|DS-04|2026-10-16 18:30:00"
        cmd = parse_command_line(line, line_number=8)
        assert isinstance(cmd, WeatherHoldCommand)
        assert cmd.store_id == "DS-04"
        assert cmd.ts == datetime(2026, 10, 16, 18, 30, 0)

    def test_cancel(self):
        line = "CANCEL|ORD-0005|2026-10-16 18:32:00"
        cmd = parse_command_line(line, line_number=9)
        assert isinstance(cmd, CancelCommand)
        assert cmd.order_id == "ORD-0005"
        assert cmd.ts == datetime(2026, 10, 16, 18, 32, 0)

    def test_load_deliveries(self):
        line = "LOAD_DELIVERIES|path/to/events.csv"
        cmd = parse_command_line(line, line_number=1)
        assert isinstance(cmd, LoadDeliveriesCommand)
        assert cmd.file_name == "path/to/events.csv"

    def test_print_trace(self):
        line = "PRINT_TRACE|ORD-0010"
        cmd = parse_command_line(line, line_number=20)
        assert isinstance(cmd, PrintTraceCommand)
        assert cmd.order_id == "ORD-0010"

    def test_print_rider_payouts(self):
        line = "PRINT_RIDER_PAYOUTS|2026-10-17"
        cmd = parse_command_line(line, line_number=50)
        assert isinstance(cmd, PrintRiderPayoutsCommand)
        assert cmd.payout_date == date(2026, 10, 17)
        assert cmd.date == date(2026, 10, 17)


# ==============================================================================
# 3. SOURCE FLEXIBILITY & HELPERS
# ==============================================================================


class TestSourceFlexibilityAndHelpers:
    """Test parse_commands across sources and comment helper."""

    def test_is_comment_or_blank(self):
        assert is_comment_or_blank("") is True
        assert is_comment_or_blank("   ") is True
        assert is_comment_or_blank("# This is a comment") is True
        assert is_comment_or_blank("  # Indented comment") is True
        assert is_comment_or_blank("ORDER|ORD-1|...") is False

    def test_parse_commands_from_stringio(self):
        content = (
            "# Header comment\n"
            "\n"
            "RIDERS|DS-01|2026-10-16 18:00:00|5\n"
            "PRINT_TRACE|ORD-0001\n"
        )
        cmds = parse_commands(io.StringIO(content))
        assert len(cmds) == 2
        assert cmds[0].line_number == 3
        assert cmds[1].line_number == 4

    def test_parse_commands_from_raw_string(self):
        content = (
            "WEATHER_HOLD|DS-01|2026-10-16 18:00:00\n"
            "# blank line next\n"
            "\n"
            "CANCEL|ORD-0001|2026-10-16 18:05:00\n"
        )
        cmds = parse_commands(content)
        assert len(cmds) == 2
        assert cmds[0].line_number == 1
        assert cmds[1].line_number == 4

    def test_invalid_source_type_raises_typeerror(self):
        with pytest.raises(TypeError, match="Expected source to be a str, Path, or file-like object"):
            parse_commands(99999)  # type: ignore


# ==============================================================================
# 4. MALFORMED COMMANDS AND ERROR REPORTING
# ==============================================================================


class TestCommandParseErrors:
    """Ensure malformed commands produce helpful line-numbered errors."""

    def test_empty_line_raises_error(self):
        with pytest.raises(CommandParseError, match="Line 5: Empty command line"):
            parse_command_line("", line_number=5)

    def test_comment_line_raises_error_in_single_line_parser(self):
        with pytest.raises(CommandParseError, match="Line 3: Line is a comment"):
            parse_command_line("# comment", line_number=3)

    def test_unknown_command_name(self):
        with pytest.raises(CommandParseError, match="Line 7: Unknown command 'FOOBAR'"):
            parse_command_line("FOOBAR|arg1|arg2", line_number=7)

    def test_missing_command_name(self):
        with pytest.raises(CommandParseError, match="Line 1: Missing command name"):
            parse_command_line("|DS-01|18:00:00", line_number=1)

    # Argument count checks
    def test_order_too_few_args(self):
        with pytest.raises(CommandParseError, match="Line 2: ORDER command expects 8 arguments, got 4"):
            parse_command_line("ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0", line_number=2)

    def test_order_too_many_args(self):
        with pytest.raises(CommandParseError, match="Line 2: ORDER command expects 8 arguments, got 9"):
            parse_command_line(
                "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1:1|extra",
                line_number=2,
            )

    def test_riders_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: RIDERS command expects 3 arguments, got 2"):
            parse_command_line("RIDERS|DS-01|2026-10-16 18:00:00", line_number=4)

    def test_weather_hold_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: WEATHER_HOLD command expects 2 arguments, got 1"):
            parse_command_line("WEATHER_HOLD|DS-01", line_number=4)

    def test_cancel_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: CANCEL command expects 2 arguments, got 1"):
            parse_command_line("CANCEL|ORD-0001", line_number=4)

    def test_load_deliveries_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: LOAD_DELIVERIES command expects 1 argument, got 0"):
            parse_command_line("LOAD_DELIVERIES", line_number=4)

    def test_print_trace_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: PRINT_TRACE command expects 1 argument, got 0"):
            parse_command_line("PRINT_TRACE", line_number=4)

    def test_print_rider_payouts_wrong_arg_count(self):
        with pytest.raises(CommandParseError, match="Line 4: PRINT_RIDER_PAYOUTS command expects 1 argument, got 0"):
            parse_command_line("PRINT_RIDER_PAYOUTS", line_number=4)

    # Empty / adjacent pipe argument checks
    def test_order_missing_order_id(self):
        line = "ORDER||2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1:1"
        with pytest.raises(CommandParseError, match="Line 10: Missing required argument 'order_id'"):
            parse_command_line(line, line_number=10)

    # Type / format validation checks
    def test_invalid_timestamp_format(self):
        line = "RIDERS|DS-01|2026-10-16T18:00:00|5"
        with pytest.raises(CommandParseError, match="Line 6: Invalid ts timestamp '2026-10-16T18:00:00'"):
            parse_command_line(line, line_number=6)

    def test_invalid_date_format(self):
        line = "PRINT_RIDER_PAYOUTS|16-10-2026"
        with pytest.raises(CommandParseError, match="Line 8: Invalid date date '16-10-2026'"):
            parse_command_line(line, line_number=8)

    def test_invalid_boolean_slow_delivery(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|yes|SKU-1:1"
        with pytest.raises(CommandParseError, match="Line 9: Invalid boolean value 'yes' for 'slow_delivery_ok'"):
            parse_command_line(line, line_number=9)

    def test_invalid_substitution_pref(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ANY|false|SKU-1:1"
        with pytest.raises(CommandParseError, match="Line 9: Invalid substitution preference 'ANY'"):
            parse_command_line(line, line_number=9)

    def test_invalid_latitude_out_of_range(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|95.0|77.0|ALLOW|false|SKU-1:1"
        with pytest.raises(CommandParseError, match="Line 9: Value for 'lat' must be <= 90.0"):
            parse_command_line(line, line_number=9)

    def test_invalid_longitude_out_of_range(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|190.0|ALLOW|false|SKU-1:1"
        with pytest.raises(CommandParseError, match="Line 9: Value for 'lon' must be <= 180.0"):
            parse_command_line(line, line_number=9)

    def test_order_items_missing_colon(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1"
        with pytest.raises(CommandParseError, match="Line 9: Invalid item format 'SKU-1'. Expected 'SKU:qty'"):
            parse_command_line(line, line_number=9)

    def test_order_items_empty_sku(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|:2"
        with pytest.raises(CommandParseError, match="Line 9: Missing SKU identifier in item ':2'"):
            parse_command_line(line, line_number=9)

    def test_order_items_non_integer_quantity(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1:abc"
        with pytest.raises(CommandParseError, match="Line 9: Invalid quantity 'abc' for SKU 'SKU-1'. Must be an integer"):
            parse_command_line(line, line_number=9)

    def test_order_items_zero_quantity(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1:0"
        with pytest.raises(CommandParseError, match="Line 9: Item quantity for SKU 'SKU-1' must be positive, got 0"):
            parse_command_line(line, line_number=9)

    def test_order_items_negative_quantity(self):
        line = "ORDER|ORD-1|2026-10-16 18:00:00|CUS-1|12.0|77.0|ALLOW|false|SKU-1:-3"
        with pytest.raises(CommandParseError, match="Line 9: Item quantity for SKU 'SKU-1' must be positive, got -3"):
            parse_command_line(line, line_number=9)

    def test_riders_negative_active_riders(self):
        line = "RIDERS|DS-01|2026-10-16 18:00:00|-2"
        with pytest.raises(CommandParseError, match="Line 3: Value for 'active_riders' must be >= 0, got -2"):
            parse_command_line(line, line_number=3)

    def test_riders_non_integer_active_riders(self):
        line = "RIDERS|DS-01|2026-10-16 18:00:00|three"
        with pytest.raises(CommandParseError, match="Line 3: Invalid integer value 'three' for 'active_riders'"):
            parse_command_line(line, line_number=3)
