from datetime import datetime, timedelta

SLOT_DURATION_MINUTES: int = 10


def get_slot_start(dt: datetime, slot_minutes: int = SLOT_DURATION_MINUTES) -> datetime:
    """Return the clock-aligned floor boundary of the slot interval for a given datetime.

    For default 10-minute slots, minutes are rounded down to 0, 10, 20, 30, 40, or 50,
    and seconds/microseconds are zeroed out.
    Preserves timezone awareness of input datetime.
    """
    if isinstance(dt, bool) or not isinstance(dt, datetime):
        raise TypeError(f"dt must be a datetime instance, got {type(dt).__name__}.")
    if isinstance(slot_minutes, bool) or not isinstance(slot_minutes, int) or slot_minutes <= 0:
        raise ValueError(f"slot_minutes must be a positive integer, got {slot_minutes}.")

    slot_minute = (dt.minute // slot_minutes) * slot_minutes
    return dt.replace(minute=slot_minute, second=0, microsecond=0)


def get_slot_end(dt: datetime, slot_minutes: int = SLOT_DURATION_MINUTES) -> datetime:
    """Return the exclusive ceiling boundary (slot_start + slot_minutes) for a given datetime."""
    start = get_slot_start(dt, slot_minutes=slot_minutes)
    return start + timedelta(minutes=slot_minutes)


def get_slot_range(
    dt: datetime, slot_minutes: int = SLOT_DURATION_MINUTES
) -> tuple[datetime, datetime]:
    """Return the half-open interval [slot_start, slot_end) for a given datetime."""
    start = get_slot_start(dt, slot_minutes=slot_minutes)
    return start, start + timedelta(minutes=slot_minutes)
