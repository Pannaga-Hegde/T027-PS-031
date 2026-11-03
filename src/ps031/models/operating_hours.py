from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class OperatingHours:
    """Represents daily operating hours for a dark store."""

    open_time: time
    close_time: time

    def __post_init__(self) -> None:
        if isinstance(self.open_time, bool) or not isinstance(self.open_time, time):
            raise TypeError(
                f"Opening time must be a datetime.time, got {type(self.open_time).__name__}."
            )
        if isinstance(self.close_time, bool) or not isinstance(self.close_time, time):
            raise TypeError(
                f"Closing time must be a datetime.time, got {type(self.close_time).__name__}."
            )

        # Validate timezone awareness consistency
        if (self.open_time.tzinfo is None) != (self.close_time.tzinfo is None):
            raise TypeError(
                "Opening time and closing time must both be naive or both be timezone-aware."
            )

        if self.open_time == self.close_time:
            raise ValueError(
                f"Opening time and closing time cannot be equal ({self.open_time})."
            )

        if self.open_time > self.close_time:
            raise ValueError(
                f"Opening time ({self.open_time}) cannot be after closing time ({self.close_time}). "
                "Overnight operating hours are not permitted."
            )

    def is_open(self, current_time: time) -> bool:
        """Check if store is open at current_time.

        Uses the interval [open_time, close_time) — opening time inclusive to closing time exclusive.
        """
        if isinstance(current_time, bool) or not isinstance(current_time, time):
            raise TypeError(
                f"current_time must be a datetime.time, got {type(current_time).__name__}."
            )

        if (self.open_time.tzinfo is None) != (current_time.tzinfo is None):
            raise TypeError(
                "Cannot compare timezone-aware and timezone-naive time values."
            )

        return self.open_time <= current_time < self.close_time
