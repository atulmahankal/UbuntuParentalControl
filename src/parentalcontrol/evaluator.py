"""Schedule evaluation engine for Parental Control."""

import re
import socket
from datetime import date, datetime, time
from typing import List, Optional, Set, Tuple

from parentalcontrol.models import AccessResult, ScheduleRule, TimeSlot
from parentalcontrol.sheet_client import WEEKDAYS


def matches_day(rule_day_str: str, check_date: date) -> bool:
    """Check if the given date matches the rule's day definition."""
    if not rule_day_str or not str(rule_day_str).strip():
        return True

    raw = str(rule_day_str).strip().lower()
    if raw in ("all", "everyday", "daily", "*", "any"):
        return True

    weekday_idx = check_date.weekday()  # 0=Monday, 6=Sunday

    if raw in ("weekday", "weekdays", "workdays") and weekday_idx in (0, 1, 2, 3, 4):
        return True

    if raw in ("weekend", "weekends") and weekday_idx in (5, 6):
        return True

    # Check comma separated list e.g. "Mon, Wed, Fri"
    tokens = [t.strip() for t in re.split(r"[,;/]+", raw) if t.strip()]
    for token in tokens:
        # Check range e.g. "Mon-Fri" or "Monday-Thursday"
        if "-" in token:
            parts = token.split("-")
            if len(parts) == 2:
                p1, p2 = parts[0].strip(), parts[1].strip()
                if p1 in WEEKDAYS and p2 in WEEKDAYS:
                    start_w, end_w = WEEKDAYS[p1], WEEKDAYS[p2]
                    if start_w <= end_w:
                        if start_w <= weekday_idx <= end_w:
                            return True
                    else:
                        # Wrap around e.g. Fri-Mon
                        if weekday_idx >= start_w or weekday_idx <= end_w:
                            return True

        if token in WEEKDAYS and WEEKDAYS[token] == weekday_idx:
            return True

        # Check explicit date e.g. "2026-09-01" or "2026/09/01"
        try:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    d = datetime.strptime(token, fmt).date()
                    if d == check_date:
                        return True
                except ValueError:
                    pass
        except Exception:
            pass

    return False


def matches_device(rule_device: str, target_device: str) -> bool:
    """Check if target_device matches rule_device (case-insensitive).
    Empty, '*', 'all', 'any', 'default' matches all devices.
    Also supports comma-separated device names (e.g. 'optiplex-3050, laptop-dell').
    """
    if not rule_device or not str(rule_device).strip():
        return True
    raw = str(rule_device).strip().lower()
    if raw in ("*", "all", "any", "default"):
        return True

    dev = (target_device or "").strip().lower()
    tokens = [t.strip().lower() for t in re.split(r"[,;/]+", raw) if t.strip()]
    return dev in tokens or "*" in tokens or "all" in tokens


def is_specific_device(rule_device: str) -> bool:
    """Check if rule defines a specific device rather than a wildcard."""
    if not rule_device or not str(rule_device).strip():
        return False
    raw = str(rule_device).strip().lower()
    return raw not in ("*", "all", "any", "default")


def is_specific_user(rule_user: str) -> bool:
    """Check if rule defines a specific user rather than a wildcard."""
    if not rule_user or not str(rule_user).strip():
        return False
    raw = str(rule_user).strip().lower()
    return raw not in ("*", "all", "any", "default")


def evaluate_access(
    user: str,
    rules: List[ScheduleRule],
    check_dt: Optional[datetime] = None,
    device: Optional[str] = None,
    is_cached: bool = False,
    cache_age_seconds: Optional[float] = None,
) -> AccessResult:
    """Evaluate access permission for user at the given datetime and device."""
    now = check_dt or datetime.now()
    target_user = user.lower().strip()
    check_date = now.date()
    check_time = now.time()

    if not device:
        try:
            target_device = socket.gethostname().lower().strip()
        except Exception:
            target_device = ""
    else:
        target_device = str(device).lower().strip()

    # Check for active temporary parent override first
    from parentalcontrol.override_manager import get_active_override
    active_override = get_active_override(target_user)
    if active_override:
        expires_at_ts = active_override.get("expires_at", 0)
        remaining_sec = expires_at_ts - now.timestamp()
        if remaining_sec > 0:
            remaining_mins = round(remaining_sec / 60.0, 1)
            expiry_dt = datetime.fromtimestamp(expires_at_ts)
            granted_by = active_override.get("granted_by", "Parent")
            time_str = expiry_dt.strftime("%I:%M %p").lstrip("0")
            return AccessResult(
                is_allowed=True,
                reason=f"Temporary access granted by {granted_by} until {time_str}.",
                user=user,
                current_time=now,
                remaining_minutes=remaining_mins,
                device=target_device,
                is_cached_schedule=is_cached,
                cache_age_seconds=cache_age_seconds,
            )

    # 1. Filter to rules that apply to this device
    matching_device_rules = [r for r in rules if matches_device(r.device, target_device)]

    # 2. Filter to rules matching today's day/date
    today_candidates = [r for r in matching_device_rules if matches_day(r.day, check_date)]

    # 3. Prioritize rules by specificity:
    # Tier 1: Specific User AND Specific Device
    tier1 = [r for r in today_candidates if r.user == target_user and is_specific_device(r.device)]
    # Tier 2: Specific User AND Wildcard Device
    tier2 = [r for r in today_candidates if r.user == target_user and not is_specific_device(r.device)]
    # Tier 3: Wildcard User AND Specific Device
    tier3 = [r for r in today_candidates if not is_specific_user(r.user) and is_specific_device(r.device)]
    # Tier 4: Wildcard User AND Wildcard Device
    tier4 = [r for r in today_candidates if not is_specific_user(r.user) and not is_specific_device(r.device)]

    today_rules = tier1 or tier2 or tier3 or tier4

    if not today_rules and not matching_device_rules:
        return AccessResult(
            is_allowed=False,
            reason=f"No schedule rules defined for device '{target_device}'.",
            user=user,
            current_time=now,
            device=target_device,
            is_cached_schedule=is_cached,
            cache_age_seconds=cache_age_seconds,
        )

    # Collect allowed time slots for today
    allowed_slots_today: List[TimeSlot] = []
    active_slot: Optional[TimeSlot] = None
    explicitly_blocked_msg: Optional[str] = None

    for r in today_rules:
        slot = TimeSlot(
            start_time=r.start_time,
            end_time=r.end_time,
            allowed=r.allowed,
            max_minutes=r.max_minutes,
            message=r.message,
            raw_day=r.day,
        )
        if r.allowed:
            allowed_slots_today.append(slot)
            if slot.contains(check_time):
                active_slot = slot
        else:
            # Explicit block rule
            if slot.contains(check_time):
                explicitly_blocked_msg = r.message or "Access has been disabled by parent."

    # Also check if there is an explicit global block rule for this device that overrides
    if not explicitly_blocked_msg:
        global_block_candidates = [
            r for r in today_candidates
            if not r.allowed and matches_device(r.device, target_device)
        ]
        for r in global_block_candidates:
            slot = TimeSlot(start_time=r.start_time, end_time=r.end_time, allowed=False)
            if slot.contains(check_time):
                explicitly_blocked_msg = r.message or "Access has been disabled by parent."
                break

    # Sort today's allowed slots by start_time
    allowed_slots_today.sort(key=lambda s: s.start_time)

    # Check if there is an active allowed slot and not explicitly blocked
    if active_slot and not explicitly_blocked_msg:
        remaining = active_slot.remaining_minutes(check_time)
        return AccessResult(
            is_allowed=True,
            reason="Within allowed schedule.",
            user=user,
            current_time=now,
            active_slot=active_slot,
            remaining_minutes=remaining,
            allowed_slots_today=allowed_slots_today,
            custom_message=active_slot.message,
            device=target_device,
            is_cached_schedule=is_cached,
            cache_age_seconds=cache_age_seconds,
        )

    # Not allowed: Find next upcoming slot today
    next_slot = None
    for s in allowed_slots_today:
        if s.start_time > check_time:
            next_slot = s
            break

    if explicitly_blocked_msg:
        reason = explicitly_blocked_msg
    elif not today_rules:
        reason = f"No allowed hours scheduled for today on device '{target_device}'."
    elif not allowed_slots_today:
        reason = "All access is disabled for today."
    else:
        reason = "Outside allowed schedule."

    return AccessResult(
        is_allowed=False,
        reason=reason,
        user=user,
        current_time=now,
        active_slot=None,
        remaining_minutes=0.0,
        allowed_slots_today=allowed_slots_today,
        next_slot=next_slot,
        custom_message=explicitly_blocked_msg,
        device=target_device,
        is_cached_schedule=is_cached,
        cache_age_seconds=cache_age_seconds,
    )
