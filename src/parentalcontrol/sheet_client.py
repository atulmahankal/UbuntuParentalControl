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
from typing import Any, Dict, List, Optional, Tuple, Union
import requests

from parentalcontrol.models import ScheduleRule, AppLimitRule, AppUsageRecord

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


def parse_time_window(val: str) -> Tuple[Optional[time], Optional[time]]:
    """Parse time window string e.g. '5:00 PM - 8:30 PM', '17:00-20:30', '5pm to 8pm'."""
    if not val or not str(val).strip():
        return None, None
    s = str(val).strip()
    if "-" in s:
        parts = s.split("-", 1)
        st = parse_time_str(parts[0].strip())
        et = parse_time_str(parts[1].strip())
        return st, et
    elif re.search(r"\bto\b", s, flags=re.IGNORECASE):
        parts = re.split(r"\bto\b", s, flags=re.IGNORECASE)
        if len(parts) == 2:
            st = parse_time_str(parts[0].strip())
            et = parse_time_str(parts[1].strip())
            return st, et
    return None, None


def parse_patterns_str(val: str) -> List[str]:
    """Parse comma/newline/semicolon/pipe separated patterns into cleaned list."""
    if not val or not str(val).strip():
        return []
    raw = str(val).strip()
    tokens = re.split(r"[,;\n\r|]+", raw)
    patterns = []
    for t in tokens:
        clean = t.strip().strip("'\"")
        if clean:
            patterns.append(clean)
    return patterns


def validate_service_account_file(file_path: Union[str, Path]) -> Tuple[bool, str, Dict[str, Any]]:
    """Validate that a JSON file is a valid Google Service Account credentials file.

    Returns:
        (is_valid, client_email_or_error, parsed_dict)
    """
    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        return False, f"File does not exist: {p}", {}
    if not p.is_file():
        return False, f"Path is not a regular file: {p}", {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"Invalid JSON format: {e}", {}

    if not isinstance(data, dict):
        return False, "JSON content is not an object", {}
    if data.get("type") != "service_account":
        return False, f"Invalid credential type: expected 'service_account', got '{data.get('type')}'", data
    if not data.get("client_email"):
        return False, "Missing 'client_email' in service account JSON", data
    if not data.get("private_key"):
        return False, "Missing 'private_key' in service account JSON", data

    return True, str(data.get("client_email", "")), data


class GoogleSheetClient:
    """Fetches and caches parental control schedule rules from Google Sheets."""

    def __init__(
        self,
        sheet_url: str,
        service_account_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
        cache_path: Optional[Path] = None,
        screen_time_sheet_name: str = "Screen Time",
        apps_limit_sheet_name: str = "Apps Limit",
        apps_usage_sheet_name: str = "Apps Usages",
        app_limits_cache_path: Optional[Path] = None,
    ):
        self.sheet_url = sheet_url or ""
        self.service_account_path = service_account_path
        self.sheet_name = sheet_name
        self.screen_time_sheet_name = screen_time_sheet_name or sheet_name or "Screen Time"
        self.apps_limit_sheet_name = apps_limit_sheet_name or "Apps Limit"
        self.apps_usage_sheet_name = apps_usage_sheet_name or "Apps Usages"
        self.cache_path = cache_path or (Path.home() / ".config" / "parental-control" / "schedule_cache.json")
        self.app_limits_cache_path = app_limits_cache_path or (self.cache_path.parent / "app_limits_cache.json")

    def get_service_account_email(self) -> Optional[str]:
        """Extract client_email from the configured service account key."""
        if not self.service_account_path or not os.path.exists(self.service_account_path):
            return None
        valid, email_or_err, _ = validate_service_account_file(self.service_account_path)
        return email_or_err if valid else None

    def _open_sheet_via_service_account(self):
        """Open Google Spreadsheet instance via gspread and Service Account."""
        import gspread
        gc = gspread.service_account(filename=self.service_account_path)
        sheet_id, _ = extract_sheet_id_and_gid(self.sheet_url)
        if sheet_id:
            return gc.open_by_key(sheet_id)
        return gc.open_by_url(self.sheet_url)

    def check_spreadsheet_connection(self) -> Tuple[bool, Optional[Any], str]:
        """Check if spreadsheet is accessible via Service Account.

        Returns:
            (is_accessible, spreadsheet_instance, message)
        """
        if not self.service_account_path or not os.path.exists(self.service_account_path):
            return False, None, "Service Account key file not configured or missing."
        if not self.sheet_url:
            return False, None, "Google Sheet URL not configured."
        try:
            sh = self._open_sheet_via_service_account()
            return True, sh, "Connected successfully."
        except Exception as e:
            return False, None, str(e)

    def get_existing_worksheets(self, sh: Any) -> List[str]:
        """Return list of worksheet titles in the spreadsheet."""
        try:
            return [ws.title for ws in sh.worksheets()]
        except Exception:
            return []

    def create_new_spreadsheet(self, title: str = "Parental Control Schedule") -> Tuple[str, Any]:
        """Create a new Google Spreadsheet using Service Account and initialize all 3 tabs."""
        import gspread
        gc = gspread.service_account(filename=self.service_account_path)
        sh = gc.create(title)

        # 1. Setup 'Screen Time'
        ws_screen = sh.sheet1
        ws_screen.update_title(self.screen_time_sheet_name)
        screen_headers = ["User", "Device", "Day", "Start Time", "End Time", "Allowed", "Max Minutes", "Message"]
        screen_samples = [
            ["*", "*", "Monday-Friday", "16:00", "20:00", "TRUE", "120", "Weekday homework & screen time"],
            ["*", "*", "Saturday-Sunday", "10:00 AM", "12:30 PM", "TRUE", "150", "Weekend morning session"],
            ["*", "*", "Saturday-Sunday", "4:00 PM", "8:30 PM", "TRUE", "180", "Weekend evening session"],
            ["*", "*", "*", "21:00", "07:00", "FALSE", "", "Bedtime - Access blocked"],
        ]
        ws_screen.append_row(screen_headers)
        ws_screen.append_rows(screen_samples)

        # 2. Setup 'Apps Limit'
        ws_apps = sh.add_worksheet(title=self.apps_limit_sheet_name, rows=500, cols=12)
        apps_headers = ["User", "App Label", "Binary / Pattern", "Device", "Day", "Allowed", "Allowed Window", "Daily Limit (Min)", "Session Limit (Min)", "Message"]
        apps_samples = [
            ["*", "Google Chrome", "chrome, google-chrome", "*", "Monday-Friday", "TRUE", "5:00 PM - 8:30 PM", "60", "30", "Homework browsing limit"],
            ["*", "Discord", "discord", "*", "Monday-Thursday", "FALSE", "", "", "", "Discord blocked on school days"],
            ["*", "Unapproved Binaries", "*/Downloads/*, *.AppImage", "*", "All", "FALSE", "", "", "", "Unapproved portable executables are blocked"],
        ]
        ws_apps.append_row(apps_headers)
        ws_apps.append_rows(apps_samples)

        # 3. Setup 'Apps Usages'
        ws_usages = sh.add_worksheet(title=self.apps_usage_sheet_name, rows=1000, cols=15)
        usages_headers = ["Date", "User", "Device", "App Label", "Binary / Pattern", "Executable Path", "Minutes Used", "Daily Limit", "Remaining", "Status", "Last Active"]
        ws_usages.append_row(usages_headers)

        self.sheet_url = sh.url
        return sh.url, sh

    def ensure_default_worksheets(self, sh: Any, create_missing: bool = True) -> Dict[str, bool]:
        """Verify presence of Screen Time, Apps Limit, and Apps Usages tabs, optionally creating them."""
        existing = self.get_existing_worksheets(sh)
        status = {}

        # 1. Screen Time
        has_screen = any(w.lower() == self.screen_time_sheet_name.lower() or w.lower() in ("sheet1", "screen time") for w in existing)
        if not has_screen and create_missing:
            ws = sh.add_worksheet(title=self.screen_time_sheet_name, rows=500, cols=10)
            ws.append_row(["User", "Device", "Day", "Start Time", "End Time", "Allowed", "Max Minutes", "Message"])
            ws.append_rows([
                ["*", "*", "Monday-Friday", "16:00", "20:00", "TRUE", "120", "Weekday homework & screen time"],
                ["*", "*", "Saturday-Sunday", "10:00 AM", "12:30 PM", "TRUE", "150", "Weekend morning session"],
                ["*", "*", "Saturday-Sunday", "4:00 PM", "8:30 PM", "TRUE", "180", "Weekend evening session"],
                ["*", "*", "*", "21:00", "07:00", "FALSE", "", "Bedtime - Access blocked"],
            ])
            has_screen = True
        status[self.screen_time_sheet_name] = has_screen

        # 2. Apps Limit
        has_apps = any(w.lower() == self.apps_limit_sheet_name.lower() or w.lower() in ("apps limit", "app limits") for w in existing)
        if not has_apps and create_missing:
            ws = sh.add_worksheet(title=self.apps_limit_sheet_name, rows=500, cols=12)
            ws.append_row(["User", "App Label", "Binary / Pattern", "Device", "Day", "Allowed", "Allowed Window", "Daily Limit (Min)", "Session Limit (Min)", "Message"])
            ws.append_rows([
                ["*", "Google Chrome", "chrome, google-chrome", "*", "Monday-Friday", "TRUE", "5:00 PM - 8:30 PM", "60", "30", "Homework browsing limit"],
                ["*", "Discord", "discord", "*", "Monday-Thursday", "FALSE", "", "", "", "Discord blocked on school days"],
                ["*", "Unapproved Binaries", "*/Downloads/*, *.AppImage", "*", "All", "FALSE", "", "", "", "Unapproved portable executables are blocked"],
            ])
            has_apps = True
        status[self.apps_limit_sheet_name] = has_apps

        # 3. Apps Usages
        has_usages = any(w.lower() == self.apps_usage_sheet_name.lower() or w.lower() in ("apps usages", "app usages", "usage") for w in existing)
        if not has_usages and create_missing:
            ws = sh.add_worksheet(title=self.apps_usage_sheet_name, rows=1000, cols=15)
            ws.append_row(["Date", "User", "Device", "App Label", "Binary / Pattern", "Executable Path", "Minutes Used", "Daily Limit", "Remaining", "Status", "Last Active"])
            has_usages = True
        status[self.apps_usage_sheet_name] = has_usages

        return status

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
        target_tab = self.screen_time_sheet_name or self.sheet_name
        csv_url = convert_to_csv_export_url(self.sheet_url, target_tab)
        if csv_url and csv_url.startswith("http"):
            try:
                rules = self._fetch_via_csv(csv_url)
                if rules:
                    self._save_cache(rules)
                    return rules, False, 0.0
            except Exception as e:
                logger.warning(f"Failed to fetch via CSV endpoint ({csv_url}): {e}")
                fetch_error = e

        # Fallback to default first sheet if named tab wasn't found
        if target_tab and target_tab != "Sheet1":
            default_csv_url = convert_to_csv_export_url(self.sheet_url, None)
            if default_csv_url and default_csv_url != csv_url:
                try:
                    rules = self._fetch_via_csv(default_csv_url)
                    if rules:
                        self._save_cache(rules)
                        return rules, False, 0.0
                except Exception as e:
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
        sh = self._open_sheet_via_service_account()
        ws = None
        for name in [self.screen_time_sheet_name, self.sheet_name, "Screen Time", "Sheet1"]:
            if not name:
                continue
            try:
                ws = sh.worksheet(name)
                break
            except Exception:
                continue

        if ws is None:
            ws = sh.sheet1

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

    def fetch_app_rules(self, use_cache_on_failure: bool = True) -> Tuple[List[AppLimitRule], bool, Optional[float]]:
        """Fetch application limit/restriction rules from 'Apps Limit' tab.
        
        Returns:
            (rules, is_cached, cache_age_seconds)
        """
        rules = []
        fetch_error = None

        # 1. Try Service Account if configured
        if self.service_account_path and os.path.exists(self.service_account_path):
            try:
                sh = self._open_sheet_via_service_account()
                ws = None
                for name in [self.apps_limit_sheet_name, "Apps Limit", "App Limits", "Apps"]:
                    try:
                        ws = sh.worksheet(name)
                        break
                    except Exception:
                        continue

                if ws is not None:
                    rows = ws.get_all_records()
                    rules = self._parse_app_dict_rows(rows)
                    self._save_app_limits_cache(rules)
                    return rules, False, 0.0
                else:
                    logger.debug(f"Worksheet '{self.apps_limit_sheet_name}' not found via service account.")
            except Exception as e:
                logger.warning(f"Failed to fetch app rules via service account: {e}")
                fetch_error = e

        # 2. Try CSV export URL
        csv_url = convert_to_csv_export_url(self.sheet_url, self.apps_limit_sheet_name)
        if csv_url and csv_url.startswith("http"):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 ParentalControl/1.0"
                }
                resp = requests.get(csv_url, headers=headers, timeout=10)
                resp.raise_for_status()
                parsed = self._parse_app_csv_content(resp.text)
                if parsed:
                    self._save_app_limits_cache(parsed)
                    return parsed, False, 0.0
            except Exception as e:
                logger.debug(f"Failed to fetch app rules via CSV endpoint ({csv_url}): {e}")
                fetch_error = e

        # 3. Fallback to cache if available
        if use_cache_on_failure:
            cached_rules, age = self._load_app_limits_cache()
            if cached_rules:
                logger.info(f"Using cached app limit rules (age: {age:.0f}s).")
                return cached_rules, True, age

        return [], False, None

    def _parse_app_dict_rows(self, rows: List[Dict[str, str]]) -> List[AppLimitRule]:
        """Normalize columns and parse rows into AppLimitRule objects."""
        if not rows:
            return []

        first_row = rows[0]
        header_map = {}
        for key in first_row.keys():
            k_clean = str(key).strip().lower().replace("_", " ").replace("-", " ")
            if any(w in k_clean for w in ("user", "child", "username", "account", "kid")):
                header_map["user"] = key
            elif any(w in k_clean for w in ("app name", "app label", "application", "label", "app")):
                header_map["app_name"] = key
            elif any(w in k_clean for w in ("binary", "pattern", "process", "executable", "exe", "command")):
                header_map["patterns"] = key
            elif any(w in k_clean for w in ("device", "computer", "hostname", "machine", "pc", "host")):
                header_map["device"] = key
            elif any(w in k_clean for w in ("day", "date", "when")):
                header_map["day"] = key
            elif any(w in k_clean for w in ("allowed", "enabled", "active", "status", "allow", "permit")):
                header_map["allowed"] = key
            elif any(w in k_clean for w in ("allowed window", "time window", "window", "hours", "time slot")):
                header_map["window"] = key
            elif any(w in k_clean for w in ("start time", "start", "from", "begin")):
                header_map["start_time"] = key
            elif any(w in k_clean for w in ("end time", "end", "to", "finish")):
                header_map["end_time"] = key
            elif any(w in k_clean for w in ("daily limit", "daily quota", "max minutes", "limit", "duration", "quota")):
                header_map["daily_limit"] = key
            elif any(w in k_clean for w in ("session limit", "session max", "session duration")):
                header_map["session_limit"] = key
            elif any(w in k_clean for w in ("message", "notes", "reason", "comment", "note", "msg")):
                header_map["message"] = key

        rules: List[AppLimitRule] = []
        for row in rows:
            user_val = str(row.get(header_map.get("user", "User"), "*")).strip()
            app_val = str(row.get(header_map.get("app_name", "App Name"), "")).strip()
            pat_val = str(row.get(header_map.get("patterns", "Binary / Pattern"), "")).strip()
            device_val = str(row.get(header_map.get("device", "Device"), "*")).strip() or "*"
            day_val = str(row.get(header_map.get("day", "Day"), "All")).strip() or "All"
            allowed_val = row.get(header_map.get("allowed", "Allowed"), "True")
            window_val = str(row.get(header_map.get("window", "Allowed Window"), "")).strip()
            start_val = str(row.get(header_map.get("start_time", "Start Time"), "")).strip()
            end_val = str(row.get(header_map.get("end_time", "End Time"), "")).strip()
            daily_val = row.get(header_map.get("daily_limit", "Daily Limit (Min)"), "")
            session_val = row.get(header_map.get("session_limit", "Session Limit (Min)"), "")
            msg_val = str(row.get(header_map.get("message", "Message"), "")).strip() or None

            # Skip empty rows
            if not app_val and not pat_val:
                continue

            patterns = parse_patterns_str(pat_val)
            if not patterns and app_val:
                patterns = [app_val]

            allowed = parse_boolean_str(allowed_val, default=True)

            st = None
            et = None
            if window_val:
                st, et = parse_time_window(window_val)
            if st is None and start_val:
                st = parse_time_str(start_val)
            if et is None and end_val:
                et = parse_time_str(end_val)

            daily_limit = parse_duration_minutes(daily_val)
            session_limit = parse_duration_minutes(session_val)

            rule = AppLimitRule(
                user=user_val.lower() if user_val else "*",
                app_name=app_val or (patterns[0] if patterns else "Unknown App"),
                patterns=patterns,
                device=device_val.lower(),
                day=day_val,
                allowed=allowed,
                start_time=st,
                end_time=et,
                daily_limit_minutes=daily_limit,
                session_limit_minutes=session_limit,
                message=msg_val,
                raw_row=dict(row),
            )
            rules.append(rule)

        return rules

    def _parse_app_csv_content(self, csv_content: str) -> List[AppLimitRule]:
        """Parse raw CSV string into AppLimitRule list."""
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        if not rows:
            return []
        first_row = rows[0]
        header_keys = [str(k).lower().replace("_", " ").replace("-", " ") for k in first_row.keys()]
        if not any(any(w in k for w in ("app", "binary", "pattern", "process", "executable", "exe")) for k in header_keys):
            return []
        return self._parse_app_dict_rows(rows)

    def _save_app_limits_cache(self, rules: List[AppLimitRule]) -> None:
        """Serialize app limit rules to local JSON cache."""
        try:
            self.app_limits_cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "timestamp": time_module.time(),
                "rules": [
                    {
                        "user": r.user,
                        "app_name": r.app_name,
                        "patterns": r.patterns,
                        "device": r.device,
                        "day": r.day,
                        "allowed": r.allowed,
                        "start_time": r.start_time.strftime("%H:%M:%S") if r.start_time else None,
                        "end_time": r.end_time.strftime("%H:%M:%S") if r.end_time else None,
                        "daily_limit_minutes": r.daily_limit_minutes,
                        "session_limit_minutes": r.session_limit_minutes,
                        "message": r.message,
                        "raw_row": r.raw_row,
                    }
                    for r in rules
                ],
            }
            with open(self.app_limits_cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write app limits cache to {self.app_limits_cache_path}: {e}")

    def _load_app_limits_cache(self) -> Tuple[List[AppLimitRule], float]:
        """Load app limit rules from local JSON cache."""
        if not self.app_limits_cache_path.exists():
            return [], 0.0
        try:
            with open(self.app_limits_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached_time = data.get("timestamp", 0.0)
            age = max(0.0, time_module.time() - cached_time)
            rules = []
            for item in data.get("rules", []):
                st = datetime.strptime(item["start_time"], "%H:%M:%S").time() if item.get("start_time") else None
                et = datetime.strptime(item["end_time"], "%H:%M:%S").time() if item.get("end_time") else None
                rule = AppLimitRule(
                    user=item.get("user", "*"),
                    app_name=item.get("app_name", ""),
                    patterns=item.get("patterns", []),
                    device=item.get("device", "*"),
                    day=item.get("day", "All"),
                    allowed=item.get("allowed", True),
                    start_time=st,
                    end_time=et,
                    daily_limit_minutes=item.get("daily_limit_minutes"),
                    session_limit_minutes=item.get("session_limit_minutes"),
                    message=item.get("message"),
                    raw_row=item.get("raw_row", {}),
                )
                rules.append(rule)
            return rules, age
        except Exception as e:
            logger.warning(f"Failed to read app limits cache from {self.app_limits_cache_path}: {e}")
            return [], 0.0

    def push_app_usages(self, records: List[AppUsageRecord]) -> bool:
        """Push application usage records to 'Apps Usages' tab in Google Sheets using Service Account."""
        if not records:
            return True

        if not self.service_account_path or not os.path.exists(self.service_account_path):
            logger.debug("Google Service Account JSON key not configured; skipping remote sync to Apps Usages tab.")
            return False

        try:
            sh = self._open_sheet_via_service_account()
            ws = None
            for name in [self.apps_usage_sheet_name, "Apps Usages", "App Usages", "Usage"]:
                try:
                    ws = sh.worksheet(name)
                    break
                except Exception:
                    continue

            headers = [
                "Date", "User", "Device", "App Label", "Binary / Pattern",
                "Executable Path", "Minutes Used", "Daily Limit",
                "Remaining", "Status", "Last Active"
            ]

            if ws is None:
                logger.info(f"Worksheet '{self.apps_usage_sheet_name}' not found. Creating it automatically...")
                ws = sh.add_worksheet(title=self.apps_usage_sheet_name, rows=1000, cols=15)
                ws.append_row(headers)

            existing_rows = ws.get_all_values()
            row_index_map = {}
            if len(existing_rows) > 1:
                for idx, r in enumerate(existing_rows[1:], start=2):
                    if len(r) >= 4:
                        key = (r[0].strip().lower(), r[1].strip().lower(), r[2].strip().lower(), r[3].strip().lower())
                        row_index_map[key] = idx

            rows_to_append = []
            for rec in records:
                lim_str = f"{rec.daily_limit_minutes}m" if rec.daily_limit_minutes else "-"
                rem_str = f"{rec.remaining_minutes}m" if rec.remaining_minutes is not None else "-"
                used_str = f"{rec.minutes_used}m"
                row_vals = [
                    rec.date,
                    rec.user,
                    rec.device,
                    rec.app_name,
                    rec.binary_or_pattern,
                    rec.exe_path,
                    used_str,
                    lim_str,
                    rem_str,
                    rec.status,
                    rec.last_active,
                ]
                key = (rec.date.strip().lower(), rec.user.strip().lower(), rec.device.strip().lower(), rec.app_name.strip().lower())
                if key in row_index_map:
                    target_row = row_index_map[key]
                    cell_range = f"A{target_row}:K{target_row}"
                    ws.update(cell_range, [row_vals])
                else:
                    rows_to_append.append(row_vals)

            if rows_to_append:
                ws.append_rows(rows_to_append)

            logger.info(f"Successfully synced {len(records)} app usage records to Google Sheets '{self.apps_usage_sheet_name}'.")
            return True
        except Exception as e:
            logger.warning(f"Failed to push app usages to Google Sheets: {e}")
            return False

