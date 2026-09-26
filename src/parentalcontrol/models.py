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
    user: str = "*"  # username, 'all', or '*'
    day: str = "All"   # 'Monday', 'Weekday', 'Weekend', 'All', '2026-09-01', etc.
    start_time: time = field(default_factory=lambda: time(0, 0))
    end_time: time = field(default_factory=lambda: time(23, 59, 59))
    allowed: bool = True
    max_minutes: Optional[int] = None
    message: Optional[str] = None
    device: str = "*"  # device/computer hostname or '*' for all devices
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
    device: Optional[str] = None
    is_cached_schedule: bool = False
    cache_age_seconds: Optional[float] = None


@dataclass
class AppLimitRule:
    """A rule defining application restrictions or duration limits from Google Sheets."""
    user: str = "*"                     # username, 'all', or '*'
    app_name: str = ""                  # Friendly application name (e.g. 'Google Chrome')
    patterns: List[str] = field(default_factory=list)  # Binary names, process names, path globs
    device: str = "*"                   # Device hostname or '*'
    day: str = "All"                    # 'Monday-Friday', 'Weekend', 'All', etc.
    allowed: bool = True                # False = completely blocked
    start_time: Optional[time] = None   # Allowed time window start
    end_time: Optional[time] = None     # Allowed time window end
    daily_limit_minutes: Optional[int] = None    # Max cumulative daily running minutes
    session_limit_minutes: Optional[int] = None  # Max continuous running minutes
    message: Optional[str] = None       # Custom notification message
    raw_row: dict = field(default_factory=dict)

    def is_in_allowed_window(self, check_time: time) -> bool:
        """Check if check_time falls within allowed window [start_time, end_time]."""
        if self.start_time is None or self.end_time is None:
            return True
        if self.start_time <= self.end_time:
            return self.start_time <= check_time <= self.end_time
        # Overnight slot
        return check_time >= self.start_time or check_time <= self.end_time

    def remaining_window_minutes(self, check_time: time) -> float:
        """Minutes remaining in current allowed window."""
        if self.start_time is None or self.end_time is None:
            return float("inf")
        if not self.is_in_allowed_window(check_time):
            return 0.0
        check_dt = datetime.combine(date.today(), check_time)
        end_dt = datetime.combine(date.today(), self.end_time)
        if self.start_time > self.end_time and check_time >= self.start_time:
            end_dt = datetime.combine(date.fromordinal(date.today().toordinal() + 1), self.end_time)
        return max(0.0, (end_dt - check_dt).total_seconds() / 60.0)


@dataclass
class AppUsageRecord:
    """A record of tracked application usage to be synced to Google Sheets."""
    date: str                          # YYYY-MM-DD
    user: str                          # Username
    device: str                        # Device hostname
    app_name: str                      # Friendly label or process name
    binary_or_pattern: str             # Matched pattern or binary name
    exe_path: str                      # Resolved executable path
    total_seconds: int                 # Cumulative seconds used today
    daily_limit_minutes: Optional[int] = None  # Configured daily limit in minutes
    status: str = "Active"             # "Active", "Quota Reached", "Restricted", "Permitted"
    last_active: str = ""              # HH:MM:SS or ISO string

    @property
    def minutes_used(self) -> float:
        return round(self.total_seconds / 60.0, 1)

    @property
    def remaining_minutes(self) -> Optional[float]:
        if self.daily_limit_minutes is None:
            return None
        return max(0.0, round(self.daily_limit_minutes - self.minutes_used, 1))
