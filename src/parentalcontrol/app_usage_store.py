"""Persistent local application usage storage and daily rollover manager."""

import json
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from parentalcontrol.models import AppUsageRecord

logger = logging.getLogger(__name__)


class AppUsageStore:
    """Tracks and persists second-by-second application usage per user."""

    def __init__(self, storage_path: Path):
        self.storage_path = Path(storage_path)
        self.current_date_str = str(date.today())
        # Structure: { user: { app_key: { ... } } }
        self.usage_data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.load()

    def _get_app_entry(
        self,
        user: str,
        app_key: str,
        app_name: str = "",
        binary_pattern: str = "",
        exe_path: str = "",
        daily_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieve or initialize an application tracking record."""
        u_clean = user.lower().strip()
        k_clean = app_key.lower().strip()
        user_dict = self.usage_data.setdefault(u_clean, {})

        if k_clean not in user_dict:
            user_dict[k_clean] = {
                "app_name": app_name or app_key,
                "binary_pattern": binary_pattern,
                "exe_path": exe_path,
                "total_seconds": 0,
                "daily_limit_minutes": daily_limit,
                "status": "Active",
                "last_active": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
                "warnings_sent": [],
            }
        else:
            entry = user_dict[k_clean]
            if app_name and not entry.get("app_name"):
                entry["app_name"] = app_name
            if binary_pattern:
                entry["binary_pattern"] = binary_pattern
            if exe_path:
                entry["exe_path"] = exe_path
            if daily_limit is not None:
                entry["daily_limit_minutes"] = daily_limit

        return user_dict[k_clean]

    def record_usage(
        self,
        user: str,
        app_key: str,
        app_name: str,
        binary_pattern: str,
        exe_path: str,
        elapsed_seconds: int,
        daily_limit: Optional[int] = None,
    ) -> int:
        """Add elapsed running seconds to an application record."""
        self._check_midnight_rollover()
        entry = self._get_app_entry(user, app_key, app_name, binary_pattern, exe_path, daily_limit)
        entry["total_seconds"] += max(0, elapsed_seconds)
        entry["last_active"] = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        return entry["total_seconds"]

    def get_seconds_used(self, user: str, app_key: str) -> int:
        """Get cumulative running seconds today for user's app."""
        self._check_midnight_rollover()
        u_clean = user.lower().strip()
        k_clean = app_key.lower().strip()
        return self.usage_data.get(u_clean, {}).get(k_clean, {}).get("total_seconds", 0)

    def get_minutes_used(self, user: str, app_key: str) -> float:
        """Get cumulative running minutes today."""
        return round(self.get_seconds_used(user, app_key) / 60.0, 1)

    def is_warning_sent(self, user: str, app_key: str, milestone_mins: int) -> bool:
        """Check if a specific remaining minutes warning milestone was already notified."""
        entry = self._get_app_entry(user, app_key)
        return milestone_mins in entry.get("warnings_sent", [])

    def mark_warning_sent(self, user: str, app_key: str, milestone_mins: int) -> None:
        """Record that a warning milestone notification has been delivered."""
        entry = self._get_app_entry(user, app_key)
        warnings = entry.setdefault("warnings_sent", [])
        if milestone_mins not in warnings:
            warnings.append(milestone_mins)

    def set_status(self, user: str, app_key: str, status: str) -> None:
        """Update app status (e.g. 'Active', 'Quota Reached', 'Restricted')."""
        entry = self._get_app_entry(user, app_key)
        entry["status"] = status

    def get_status(self, user: str, app_key: str) -> str:
        """Get current status string for an app."""
        entry = self._get_app_entry(user, app_key)
        return entry.get("status", "Active")

    def _check_midnight_rollover(self) -> None:
        """Reset usage counters if the calendar day has crossed midnight."""
        today_str = str(date.today())
        if today_str != self.current_date_str:
            logger.info(f"Day rollover detected ({self.current_date_str} -> {today_str}). Resetting daily app usage.")
            self.current_date_str = today_str
            self.usage_data = {}
            self.save()

    def get_usage_records_for_sync(self, device: str) -> List[AppUsageRecord]:
        """Convert current daily tracking data into AppUsageRecord objects for Google Sheets sync."""
        self._check_midnight_rollover()
        records: List[AppUsageRecord] = []

        for user, apps in self.usage_data.items():
            for key, info in apps.items():
                secs = info.get("total_seconds", 0)
                # Only sync apps that have actually run (> 0 seconds)
                if secs <= 0:
                    continue
                rec = AppUsageRecord(
                    date=self.current_date_str,
                    user=user,
                    device=device,
                    app_name=info.get("app_name") or key,
                    binary_or_pattern=info.get("binary_pattern") or key,
                    exe_path=info.get("exe_path") or "",
                    total_seconds=secs,
                    daily_limit_minutes=info.get("daily_limit_minutes"),
                    status=info.get("status", "Active"),
                    last_active=info.get("last_active", ""),
                )
                records.append(rec)

        return records

    def load(self) -> None:
        """Load state from disk."""
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            saved_date = payload.get("date", "")
            today_str = str(date.today())

            if saved_date == today_str:
                self.current_date_str = today_str
                self.usage_data = payload.get("users", {})
            else:
                # Different day: start fresh for today
                self.current_date_str = today_str
                self.usage_data = {}
                self.save()
        except Exception as e:
            logger.warning(f"Failed to load app usage from {self.storage_path}: {e}")

    def save(self) -> None:
        """Atomically persist tracking data to disk."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "date": self.current_date_str,
                "users": self.usage_data,
            }
            # Atomic write via temporary file
            dir_name = str(self.storage_path.parent)
            with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
                json.dump(payload, tf, indent=2)
                temp_name = tf.name

            os.replace(temp_name, str(self.storage_path))
        except Exception as e:
            logger.warning(f"Failed to save app usage to {self.storage_path}: {e}")
