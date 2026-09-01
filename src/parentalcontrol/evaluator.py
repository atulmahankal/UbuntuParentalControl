"""Schedule evaluation engine for Parental Control."""

import re
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


def evaluate_access(
    user: str,
    rules: List[ScheduleRule],
    check_dt: Optional[datetime] = None,
    is_cached: bool = False,
    cache_age_seconds: Optional[float] = None,
) -> AccessResult:
    """Evaluate access permission for user at the given datetime."""
    now = check_dt or datetime.now()
    target_user = user.lower().strip()
    check_date = now.date()
    check_time = now.time()

    # Find rules for today: specific user rules first, then fallback to wildcard rules
    exact_user_rules = [r for r in rules if r.user == target_user]
    wildcard_rules = [r for r in rules if r.user in ("*", "all", "default")]

    exact_today = [r for r in exact_user_rules if matches_day(r.day, check_date)]
    wildcard_today = [r for r in wildcard_rules if matches_day(r.day, check_date)]

    today_rules = exact_today if exact_today else wildcard_today

    if not today_rules and not exact_user_rules and not wildcard_rules:
        return AccessResult(
            is_allowed=False,
            reason=f"No schedule rules defined.",
            user=user,
            current_time=now,
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
        reason = "No allowed hours scheduled for today."
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
        is_cached_schedule=is_cached,
        cache_age_seconds=cache_age_seconds,
    )
