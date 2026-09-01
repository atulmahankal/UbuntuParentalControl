"""Data models for Parental Control."""

from dataclasses import dataclass, field
from datetime import date, time, datetime
from typing import List, Optional


@dataclass
class TimeSlot:
    """Represents an allowed time window within a day."""
    start_time: time
    end_time: time
    allowed: bool = True
    max_minutes: Optional[int] = None
    message: Optional[str] = None
    raw_day: str = ""

    def contains(self, check_time: time) -> bool:
        """Check if check_time falls within [start_time, end_time]."""
        if self.start_time <= self.end_time:
            return self.start_time <= check_time <= self.end_time
        # Overnight slot e.g. 22:00 to 02:00
        return check_time >= self.start_time or check_time <= self.end_time

    def remaining_minutes(self, check_time: time) -> float:
        """Calculate minutes remaining from check_time until end_time."""
        check_dt = datetime.combine(date.today(), check_time)
        end_dt = datetime.combine(date.today(), self.end_time)
        if self.start_time > self.end_time and check_time >= self.start_time:
            # Overnight slot and we are before midnight
            end_dt = datetime.combine(date.fromordinal(date.today().toordinal() + 1), self.end_time)
        elif self.start_time > self.end_time and check_time <= self.end_time:
            # Overnight slot and we are after midnight
            pass

        diff = (end_dt - check_dt).total_seconds() / 60.0
        return max(0.0, diff)

    def formatted_range(self) -> str:
        """Return human-readable time range string."""
        return f"{self.start_time.strftime('%I:%M %p').lstrip('0')} - {self.end_time.strftime('%I:%M %p').lstrip('0')}"


@dataclass
class ScheduleRule:
    """A rule parsed from a single row of the Google Sheet."""
    user: str  # username, 'all', or '*'
    day: str   # 'Monday', 'Weekday', 'Weekend', 'All', '2026-09-01', etc.
    start_time: time
    end_time: time
    allowed: bool = True
    max_minutes: Optional[int] = None
    message: Optional[str] = None
    raw_row: dict = field(default_factory=dict)


@dataclass
class AccessResult:
    """The result of evaluating access for a user at a given time."""
    is_allowed: bool
    reason: str
    user: str
    current_time: datetime
    active_slot: Optional[TimeSlot] = None
    remaining_minutes: float = 0.0
    allowed_slots_today: List[TimeSlot] = field(default_factory=list)
    next_slot: Optional[TimeSlot] = None
    custom_message: Optional[str] = None
    is_cached_schedule: bool = False
    cache_age_seconds: Optional[float] = None
