"""Application limit and restriction enforcement engine."""

import logging
import os
import signal
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from parentalcontrol.app_monitor import ProcessInfo, matches_process
from parentalcontrol.app_usage_store import AppUsageStore
from parentalcontrol.evaluator import matches_day, matches_device, matches_user
from parentalcontrol.models import AppLimitRule
from parentalcontrol.system_service import play_user_sound, send_user_notification

logger = logging.getLogger(__name__)


def terminate_process(pid: int, timeout_sec: float = 3.0) -> bool:
    """Gracefully terminate a process (SIGTERM) and forcefully kill if needed (SIGKILL)."""
    try:
        try:
            pgid = os.getpgid(pid)
            if pgid > 1:
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            os.kill(pid, signal.SIGTERM)

        deadline = time.time() + max(0.5, timeout_sec)
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except OSError:
                return True

        # Send SIGKILL if process still running
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        return True
    except (ProcessLookupError, PermissionError):
        return True
    except Exception as e:
        logger.debug(f"Failed to terminate PID {pid}: {e}")
        return False


class AppEnforcer:
    """Evaluates running applications against limit rules and triggers warnings or terminations."""

    def __init__(self, usage_store: AppUsageStore):
        self.usage_store = usage_store
        # Track PID termination attempts to avoid notification loops
        self._recently_terminated_pids: Dict[int, float] = {}

    def filter_applicable_rules(
        self,
        username: str,
        rules: List[AppLimitRule],
        device: str,
        exact_user_matching: bool = True,
    ) -> List[AppLimitRule]:
        """Filter AppLimitRules that apply to this user, today's day, and this device."""
        now = datetime.now()
        check_date = now.date()

        applicable: List[AppLimitRule] = []
        for r in rules:
            if not matches_user(r.user, username, exact_matching=exact_user_matching):
                continue
            if not matches_device(r.device, device):
                continue
            if not matches_day(r.day, check_date):
                continue
            applicable.append(r)

        return applicable

    def enforce(
        self,
        username: str,
        uid: int,
        running_procs: List[ProcessInfo],
        rules: List[AppLimitRule],
        device: str,
        exact_user_matching: bool = True,
    ) -> None:
        """Scan running processes and enforce app restrictions, time windows, and quotas."""
        applicable_rules = self.filter_applicable_rules(username, rules, device, exact_user_matching)
        now_time = datetime.now().time()
        now_ts = time.time()

        # Clean old terminated PID history (> 30s)
        self._recently_terminated_pids = {
            pid: ts for pid, ts in self._recently_terminated_pids.items() if now_ts - ts < 30.0
        }

        # Track which apps are running right now: app_key -> List[ProcessInfo]
        active_apps: Dict[str, List[ProcessInfo]] = {}

        for proc in running_procs:
            for rule in applicable_rules:
                if matches_process(proc, rule):
                    app_key = rule.app_name.lower().strip()
                    active_apps.setdefault(app_key, []).append(proc)
                    break

        # Process each running matched application
        for app_key, procs in active_apps.items():
            # Find the corresponding rule
            matched_rule = next((r for r in applicable_rules if r.app_name.lower().strip() == app_key), None)
            if not matched_rule:
                continue

            # 1. Check if explicitly BLOCKED (allowed is False)
            if not matched_rule.allowed:
                self._terminate_app(
                    uid,
                    username,
                    app_key,
                    procs,
                    title="Application Restricted",
                    message=matched_rule.message or f"'{matched_rule.app_name}' is blocked by Parental Control.",
                    status="Blocked",
                )
                continue

            # 2. Check Allowed Time Window
            if not matched_rule.is_in_allowed_window(now_time):
                win_str = ""
                if matched_rule.start_time and matched_rule.end_time:
                    win_str = f" ({matched_rule.start_time.strftime('%I:%M %p').lstrip('0')} - {matched_rule.end_time.strftime('%I:%M %p').lstrip('0')})"
                self._terminate_app(
                    uid,
                    username,
                    app_key,
                    procs,
                    title="Application Restricted",
                    message=matched_rule.message or f"'{matched_rule.app_name}' is only permitted during allowed hours{win_str}.",
                    status="Outside Window",
                )
                continue

            # 3. Check Duration Quota (Daily Limit)
            if matched_rule.daily_limit_minutes is not None:
                used_mins = self.usage_store.get_minutes_used(username, app_key)
                remaining_mins = max(0.0, matched_rule.daily_limit_minutes - used_mins)

                if remaining_mins <= 0.0:
                    # Daily quota reached! Terminate application
                    self._terminate_app(
                        uid,
                        username,
                        app_key,
                        procs,
                        title="Daily Limit Reached",
                        message=f"Daily screen time limit of {matched_rule.daily_limit_minutes}m for '{matched_rule.app_name}' has been reached.",
                        status="Quota Reached",
                    )
                    continue

                # Warning Milestones: 10m, 5m, 2m
                self._check_warnings(uid, username, matched_rule, remaining_mins)

            # Application is currently permitted and active
            self.usage_store.set_status(username, app_key, "Active")

    def _check_warnings(self, uid: int, username: str, rule: AppLimitRule, remaining_mins: float) -> None:
        """Send milestone warnings to the child as time counts down."""
        app_key = rule.app_name.lower().strip()

        # 10 minute warning
        if remaining_mins <= 10.0 and not self.usage_store.is_warning_sent(username, app_key, 10):
            send_user_notification(
                uid=uid,
                username=username,
                title="Application Time Warning",
                message=f"10 minutes of screen time remaining for '{rule.app_name}'.",
                urgency="normal",
                icon="dialog-information",
            )
            play_user_sound(uid, username, "dialog-warning")
            self.usage_store.mark_warning_sent(username, app_key, 10)

        # 5 minute warning
        if remaining_mins <= 5.0 and not self.usage_store.is_warning_sent(username, app_key, 5):
            send_user_notification(
                uid=uid,
                username=username,
                title="Application Time Warning",
                message=f"5 minutes remaining for '{rule.app_name}'. Please save your progress!",
                urgency="critical",
                icon="dialog-warning",
            )
            play_user_sound(uid, username, "dialog-warning")
            self.usage_store.mark_warning_sent(username, app_key, 5)

        # 2 minute warning
        if remaining_mins <= 2.0 and not self.usage_store.is_warning_sent(username, app_key, 2):
            send_user_notification(
                uid=uid,
                username=username,
                title="Application Time Ending",
                message=f"2 minutes remaining for '{rule.app_name}'! Closing shortly.",
                urgency="critical",
                icon="dialog-error",
            )
            play_user_sound(uid, username, "dialog-warning")
            self.usage_store.mark_warning_sent(username, app_key, 2)

    def _terminate_app(
        self,
        uid: int,
        username: str,
        app_key: str,
        procs: List[ProcessInfo],
        title: str,
        message: str,
        status: str,
    ) -> None:
        """Terminate all PIDs of a blocked or expired application and alert the user."""
        self.usage_store.set_status(username, app_key, status)

        # Filter PIDs not recently notified to prevent rapid duplicate notifications
        pids_to_kill = [p.pid for p in procs]
        new_pids = [pid for pid in pids_to_kill if pid not in self._recently_terminated_pids]

        if new_pids:
            logger.info(f"Terminating application '{app_key}' for user '{username}' (PIDs: {new_pids}). Reason: {status}")
            send_user_notification(
                uid=uid,
                username=username,
                title=title,
                message=message,
                urgency="critical",
                icon="dialog-error",
            )
            play_user_sound(uid, username, "dialog-error")

        for pid in pids_to_kill:
            self._recently_terminated_pids[pid] = time.time()
            terminate_process(pid)
