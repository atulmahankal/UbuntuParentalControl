"""Google Sheets client for fetching and parsing parental control schedules."""

import csv
import io
import json
import logging
import os
import re
import time as time_module
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests

from parentalcontrol.models import ScheduleRule

logger = logging.getLogger(__name__)

# Standard weekday name mappings
WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def extract_sheet_id_and_gid(url_or_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract Google Spreadsheet ID and GID from URL if applicable."""
    if not url_or_id:
        return None, None

    # Check if it's already a raw ID
    if re.match(r"^[a-zA-Z0-9_-]{25,}$", url_or_id):
        return url_or_id, "0"

    # Match https://docs.google.com/spreadsheets/d/<ID>/...
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    sheet_id = match.group(1) if match else None

    # Match gid=<GID>
    gid_match = re.search(r"[?&#]gid=([0-9]+)", url_or_id)
    gid = gid_match.group(1) if gid_match else "0"

    return sheet_id, gid


def convert_to_csv_export_url(url: str, sheet_name: Optional[str] = None) -> str:
    """Convert a standard Google Sheets sharing URL to direct CSV export endpoint."""
    if not url:
        return ""

    if "output=csv" in url or "format=csv" in url or "tqx=out:csv" in url:
        return url

    sheet_id, gid = extract_sheet_id_and_gid(url)
    if sheet_id:
        if sheet_name:
            import urllib.parse
            encoded_name = urllib.parse.quote(sheet_name)
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_name}"
        if gid and gid != "0":
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"

    return url


def parse_time_str(time_str: str) -> Optional[time]:
    """Parse various time formats into datetime.time.
    Supports: 16:00, 4:00 PM, 4pm, 09:30, 9:30am, 21:30:00, etc.
    """
    if not time_str or not str(time_str).strip():
        return None

    raw = str(time_str).strip().lower()
    raw = raw.replace(".", "")  # e.g. a.m. -> am

    # Formats to try
    formats = [
        "%H:%M",
        "%H:%M:%S",
        "%I:%M %p",
        "%I:%M%p",
        "%I %p",
        "%I%p",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.time()
        except ValueError:
            continue

    # Try regex fallback for e.g. "4:30pm" or "1630"
    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)?$", raw)
    if m:
        h, mn, meridian = int(m.group(1)), int(m.group(2)), m.group(3)
        if meridian == "pm" and h < 12:
            h += 12
        elif meridian == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return time(hour=h, minute=mn)

    logger.warning(f"Could not parse time string: '{time_str}'")
    return None


def parse_duration_minutes(val: str) -> Optional[int]:
    """Parse string representation of duration into integer minutes."""
    if val is None or not str(val).strip():
        return None
    raw = str(val).strip().lower()
    try:
        # e.g. "120"
        return int(float(raw))
    except ValueError:
        pass

    # e.g. "2h", "1.5 hours", "90m", "90 mins"
    m_hour = re.match(r"^([\d.]+)\s*(?:h|hr|hrs|hours?)$", raw)
    if m_hour:
        return int(float(m_hour.group(1)) * 60)

    m_min = re.match(r"^([\d.]+)\s*(?:m|min|mins|minutes?)$", raw)
    if m_min:
        return int(float(m_min.group(1)))

    return None


def parse_boolean_str(val: str, default: bool = True) -> bool:
    """Parse boolean from string."""
    if val is None:
        return default
    s = str(val).strip().lower()
    if not s:
        return default
    if s in ("true", "yes", "1", "y", "allowed", "enable", "enabled", "ok", "active"):
        return True
    if s in ("false", "no", "0", "n", "blocked", "disable", "disabled", "disallowed", "deny"):
        return False
    return default


class GoogleSheetClient:
    """Fetches and caches parental control schedule rules from Google Sheets."""

    def __init__(
        self,
        sheet_url: str,
        service_account_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
        cache_path: Optional[Path] = None,
    ):
        self.sheet_url = sheet_url or ""
        self.service_account_path = service_account_path
        self.sheet_name = sheet_name
        self.cache_path = cache_path or (Path.home() / ".config" / "parental-control" / "schedule_cache.json")

    def fetch_rules(self, use_cache_on_failure: bool = True) -> Tuple[List[ScheduleRule], bool, Optional[float]]:
        """Fetch schedule rules from Google Sheets or local CSV.
        
        Returns:
            (rules, is_cached, cache_age_seconds)
        """
        rules = []
        fetch_error = None

        # 0. Check if local CSV file
        clean_path = self.sheet_url.replace("file://", "")
        if clean_path and os.path.exists(clean_path) and os.path.isfile(clean_path):
            try:
                with open(clean_path, "r", encoding="utf-8") as f:
                    rules = self._parse_csv_content(f.read())
                self._save_cache(rules)
                return rules, False, 0.0
            except Exception as e:
                logger.warning(f"Failed to read local CSV file: {e}")
                fetch_error = e

        # 1. Try Service Account if configured
        if self.service_account_path and os.path.exists(self.service_account_path):
            try:
                rules = self._fetch_via_service_account()
                self._save_cache(rules)
                return rules, False, 0.0
            except Exception as e:
                logger.warning(f"Failed to fetch via service account: {e}")
                fetch_error = e

        # 2. Try CSV export URL
        csv_url = convert_to_csv_export_url(self.sheet_url, self.sheet_name)
        if csv_url and csv_url.startswith("http"):
            try:
                rules = self._fetch_via_csv(csv_url)
                self._save_cache(rules)
                return rules, False, 0.0
            except Exception as e:
                logger.warning(f"Failed to fetch via CSV endpoint ({csv_url}): {e}")
                fetch_error = e

        # 3. Fallback to cache if available
        if use_cache_on_failure:
            cached_rules, age = self._load_cache()
            if cached_rules:
                logger.info(f"Using cached schedule rules (age: {age:.0f}s).")
                return cached_rules, True, age

        if fetch_error:
            raise RuntimeError(f"Unable to fetch schedule from Google Sheets and no valid cache found: {fetch_error}")

        return [], False, None

    def _fetch_via_csv(self, csv_url: str) -> List[ScheduleRule]:
        """Download and parse CSV export from Google Sheets."""
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 ParentalControl/1.0"
        }
        resp = requests.get(csv_url, headers=headers, timeout=10)
        resp.raise_for_status()

        # Parse CSV text
        csv_text = resp.text
        return self._parse_csv_content(csv_text)

    def _fetch_via_service_account(self) -> List[ScheduleRule]:
        """Fetch using gspread with Service Account JSON."""
        import gspread
        gc = gspread.service_account(filename=self.service_account_path)
        
        sheet_id, _ = extract_sheet_id_and_gid(self.sheet_url)
        if sheet_id:
            sh = gc.open_by_key(sheet_id)
        else:
            sh = gc.open_by_url(self.sheet_url)

        ws = sh.worksheet(self.sheet_name) if self.sheet_name else sh.sheet1
        rows = ws.get_all_records()
        return self._parse_dict_rows(rows)

    def _parse_csv_content(self, csv_content: str) -> List[ScheduleRule]:
        """Parse raw CSV string into ScheduleRule list."""
        reader = csv.DictReader(io.StringIO(csv_content))
        return self._parse_dict_rows(list(reader))

    def _parse_dict_rows(self, rows: List[Dict[str, str]]) -> List[ScheduleRule]:
        """Normalize columns and parse rows into ScheduleRule objects."""
        if not rows:
            return []

        # Find header mappings
        first_row = rows[0]
        header_map = {}
        for key in first_row.keys():
            k_clean = str(key).strip().lower().replace("_", " ").replace("-", " ")
            if any(w in k_clean for w in ("user", "child", "username", "account", "kid", "profile")):
                header_map["user"] = key
            elif any(w in k_clean for w in ("day", "date", "when")):
                header_map["day"] = key
            elif any(w in k_clean for w in ("start time", "start", "from", "begin", "starttime")):
                header_map["start_time"] = key
            elif any(w in k_clean for w in ("end time", "end", "to", "finish", "endtime")):
                header_map["end_time"] = key
            elif any(w in k_clean for w in ("allowed", "enabled", "active", "status", "allow", "permit", "permitted")):
                header_map["allowed"] = key
            elif any(w in k_clean for w in ("max minutes", "max_minutes", "daily limit", "limit", "max hours", "quota", "duration", "daily quota")):
                header_map["max_minutes"] = key
            elif any(w in k_clean for w in ("device", "computer", "hostname", "machine", "pc", "host")):
                header_map["device"] = key
            elif any(w in k_clean for w in ("message", "notes", "reason", "comment", "note", "msg")):
                header_map["message"] = key

        rules: List[ScheduleRule] = []

        for row in rows:
            user_val = str(row.get(header_map.get("user", "User"), "*")).strip()
            day_val = str(row.get(header_map.get("day", "Day"), "All")).strip()
            start_val = str(row.get(header_map.get("start_time", "Start Time"), "00:00")).strip()
            end_val = str(row.get(header_map.get("end_time", "End Time"), "23:59")).strip()
            allowed_val = row.get(header_map.get("allowed", "Allowed"), "True")
            max_val = row.get(header_map.get("max_minutes", "Max Minutes"), "")
            device_val = str(row.get(header_map.get("device", "Device"), "*")).strip()
            if not device_val:
                device_val = "*"
            msg_val = str(row.get(header_map.get("message", "Message"), "")).strip() or None

            # Skip empty rows
            if not day_val and not start_val and not end_val:
                continue

            start_t = parse_time_str(start_val) or time(0, 0)
            end_t = parse_time_str(end_val) or time(23, 59, 59)
            allowed = parse_boolean_str(allowed_val, default=True)
            max_mins = parse_duration_minutes(max_val)

            rule = ScheduleRule(
                user=user_val.lower() if user_val else "*",
                day=day_val,
                start_time=start_t,
                end_time=end_t,
                allowed=allowed,
                max_minutes=max_mins,
                device=device_val.lower(),
                message=msg_val,
                raw_row=dict(row),
            )
            rules.append(rule)

        return rules

    def _save_cache(self, rules: List[ScheduleRule]) -> None:
        """Serialize rules to local JSON cache."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "timestamp": time_module.time(),
                "rules": [
                    {
                        "user": r.user,
                        "day": r.day,
                        "start_time": r.start_time.strftime("%H:%M:%S"),
                        "end_time": r.end_time.strftime("%H:%M:%S"),
                        "allowed": r.allowed,
                        "max_minutes": r.max_minutes,
                        "device": r.device,
                        "message": r.message,
                        "raw_row": r.raw_row,
                    }
                    for r in rules
                ],
            }
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write cache to {self.cache_path}: {e}")

    def _load_cache(self) -> Tuple[List[ScheduleRule], float]:
        """Load schedule from local JSON cache."""
        if not self.cache_path.exists():
            return [], 0.0

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            cached_time = data.get("timestamp", 0.0)
            age = max(0.0, time_module.time() - cached_time)
            rules = []
            for item in data.get("rules", []):
                st = datetime.strptime(item["start_time"], "%H:%M:%S").time()
                et = datetime.strptime(item["end_time"], "%H:%M:%S").time()
                rule = ScheduleRule(
                    user=item.get("user", "*"),
                    day=item.get("day", "All"),
                    start_time=st,
                    end_time=et,
                    allowed=item.get("allowed", True),
                    max_minutes=item.get("max_minutes"),
                    device=item.get("device", "*"),
                    message=item.get("message"),
                    raw_row=item.get("raw_row", {}),
                )
                rules.append(rule)
            return rules, age
        except Exception as e:
            logger.warning(f"Failed to read cache from {self.cache_path}: {e}")
            return [], 0.0

