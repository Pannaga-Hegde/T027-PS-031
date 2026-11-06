from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum


class RiderStatus(str, Enum):
    """Operational status of a delivery rider.

    Values:
    - AVAILABLE: Active and idle at the dark store, eligible for batch assignment.
    - BUSY: Assigned to an active batch and performing deliveries.
    - OFF_DUTY: Inactive / clocked out; excluded from active store rider counts.
    """

    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    OFF_DUTY = "OFF_DUTY"


@dataclass
class Rider:
    """Represents a delivery rider."""

    rider_id: str
    name: str
    status: RiderStatus = RiderStatus.AVAILABLE
    daily_penalty: Decimal = Decimal("0.00")
    daily_penalty_date: date | None = None

    def __post_init__(self) -> None:
        for field_name, value in [
            ("Rider ID", self.rider_id),
            ("Name", self.name),
        ]:
            if isinstance(value, bool) or not isinstance(value, str):
                raise TypeError(
                    f"{field_name} must be a string, got {type(value).__name__}."
                )
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty.")

        # Validate and normalize status
        if isinstance(self.status, bool) or not isinstance(
            self.status, (str, RiderStatus)
        ):
            raise TypeError(
                f"Status must be a string, got {type(self.status).__name__}."
            )

        if isinstance(self.status, str) and not isinstance(self.status, RiderStatus):
            if not self.status.strip():
                raise ValueError("Status must not be empty.")
            try:
                self.status = RiderStatus(self.status.strip())
            except ValueError:
                try:
                    self.status = RiderStatus[self.status.strip().upper()]
                except (KeyError, AttributeError):
                    raise ValueError(
                        f"Invalid rider status: {self.status}. Allowed statuses: "
                        f"{sorted([s.value for s in RiderStatus])}."
                    )

        if isinstance(self.daily_penalty, bool):
            raise TypeError(
                f"Daily penalty cannot be a boolean, got {type(self.daily_penalty).__name__}."
            )

        if not isinstance(self.daily_penalty, Decimal):
            try:
                self.daily_penalty = Decimal(str(self.daily_penalty))
            except (InvalidOperation, TypeError, ValueError) as err:
                raise ValueError(
                    f"Invalid daily penalty: {self.daily_penalty}"
                ) from err

        if not self.daily_penalty.is_finite():
            raise ValueError(
                f"Daily penalty must be a finite number, got {self.daily_penalty}."
            )

        if self.daily_penalty < Decimal("0"):
            raise ValueError(
                f"Daily penalty cannot be negative, got {self.daily_penalty}."
            )

        if self.daily_penalty_date is not None:
            if isinstance(self.daily_penalty_date, bool):
                raise TypeError("Daily penalty date cannot be a boolean.")
            if isinstance(self.daily_penalty_date, datetime):
                raise TypeError(
                    f"Daily penalty date must be a date, not a datetime, got {type(self.daily_penalty_date).__name__}."
                )
            if isinstance(self.daily_penalty_date, str):
                if not self.daily_penalty_date.strip():
                    raise ValueError("Daily penalty date must not be empty.")
                try:
                    self.daily_penalty_date = date.fromisoformat(
                        self.daily_penalty_date.strip()
                    )
                except ValueError as err:
                    raise ValueError(
                        f"Invalid daily penalty date format: '{self.daily_penalty_date}'. Expected ISO format YYYY-MM-DD."
                    ) from err
            elif not isinstance(self.daily_penalty_date, date):
                raise TypeError(
                    f"Daily penalty date must be a date or None, got {type(self.daily_penalty_date).__name__}."
                )
