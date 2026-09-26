"""System-level multi-session monitoring daemon for Ubuntu."""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

from parentalcontrol.app_enforcer import AppEnforcer
from parentalcontrol.app_monitor import matches_process, scan_user_processes
from parentalcontrol.app_usage_store import AppUsageStore
from parentalcontrol.config import AppConfig, SYSTEM_EXEMPT_USERS, load_config
from parentalcontrol.evaluator import evaluate_access
from parentalcontrol.models import AccessResult, AppLimitRule, AppUsageRecord, ScheduleRule
from parentalcontrol.sheet_client import GoogleSheetClient
from parentalcontrol.system_service import (
    UserSession,
    list_active_sessions,
    play_user_sound,
    run_in_user_session,
    send_user_notification,
    show_user_countdown_dialog,
    show_user_warning_dialog,
    terminate_session_by_id_or_user,
)

logger = logging.getLogger(__name__)


@dataclass
class MonitoredSession:
    """State tracking for an active child user session."""
    session_id: str
    username: str
    uid: int
    login_time: datetime
    initial_check_passed: bool = False
    notified_thresholds: Set[int] = field(default_factory=set)


class SystemParentalControlDaemon:
    """System-level daemon running as root, monitoring all active desktop sessions."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.client = GoogleSheetClient(
            sheet_url=self.config.google_sheet.url,
            service_account_path=self.config.google_sheet.service_account_path,
            sheet_name=self.config.google_sheet.sheet_name,
            screen_time_sheet_name=self.config.google_sheet.screen_time_sheet_name,
            apps_limit_sheet_name=self.config.google_sheet.apps_limit_sheet_name,
            apps_usage_sheet_name=self.config.google_sheet.apps_usage_sheet_name,
            cache_path=self.config.cache_file_path,
            app_limits_cache_path=self.config.app_limits_cache_file_path,
        )
        self.active_monitored: Dict[str, MonitoredSession] = {}
        self.cached_rules: List[ScheduleRule] = []
        self.cached_app_rules: List[AppLimitRule] = []
        self.usage_store = AppUsageStore(self.config.app_usage_file_path)
        self.app_enforcer = AppEnforcer(self.usage_store)
        self.last_sync_time: float = 0.0
        self.last_usage_sync_time: float = 0.0
        self._last_process_check_ts: float = time.time()
        self._running: bool = True
        self.ipc_server = None

    def start(self) -> None:
        """Start the system-wide service daemon loop."""
        self._setup_logging()
        logger.info("=======================================================")
        logger.info("Parental Control System Service Daemon starting...")
        logger.info(f"Target users: {self.config.rules.target_users}")
        logger.info(f"Exempt users: {self.config.rules.exempt_users}")
        logger.info(f"Config path: {self.config.config_file_path}")
        logger.info("=======================================================")

        # Safety Check: Disallow wildcard '*' in service mode to prevent locking out GDM/system accounts
        if self.config.has_wildcard_target_users():
            err_msg = (
                "CRITICAL CONFIGURATION ERROR: 'target_users: [*]' is not permitted in system service mode. "
                "Wildcard targeting can match system display managers (such as GDM) and cause login lockout loops. "
                f"Please specify explicit child usernames in {self.config.config_file_path or '/etc/parental-control/config.yaml'} "
                "(e.g. target_users: ['himanshu', 'himanshi']). "
                "The Parental Control service is safely stopping."
            )
            logger.critical(err_msg)
            print(f"\n❌ {err_msg}\n", file=sys.stderr)
            sys.exit(1)

        # Start IPC Server for parent override authentication
        try:
            from parentalcontrol.ipc import ParentalControlIPCServer
            self.ipc_server = ParentalControlIPCServer(
                exempt_users=self.config.rules.exempt_users,
                on_override=self._on_override_granted,
                on_logout=self._on_logout_requested,
                on_poweroff=self._on_poweroff_requested,
            )
            self.ipc_server.start()
        except Exception as e:
            logger.warning(f"Could not start IPC server: {e}")

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Initial schedule fetch
        self._refresh_rules()

        check_interval = 10  # Check every 10 seconds
        sync_interval_sec = max(60, self.config.google_sheet.sync_interval_minutes * 60)
        usage_sync_interval_sec = max(60, self.config.google_sheet.usage_sync_interval_minutes * 60)

        while self._running:
            try:
                now_ts = time.time()
                # Periodic Google Sheet schedule sync
                if now_ts - self.last_sync_time > sync_interval_sec:
                    self._refresh_rules()

                # Periodic App Usage sync to Google Sheets (Apps Usages tab)
                if now_ts - self.last_usage_sync_time > usage_sync_interval_sec:
                    self._sync_app_usages()

                # Discover active sessions
                sessions = list_active_sessions()
                current_session_ids = {s.session_id for s in sessions}

                # Clean up ended sessions
                ended_ids = set(self.active_monitored.keys()) - current_session_ids
                for sid in ended_ids:
                    logger.info(f"Session {sid} ({self.active_monitored[sid].username}) ended.")
                    del self.active_monitored[sid]
                if ended_ids:
                    self._sync_app_usages()

                # Inspect each active session
                for session in sessions:
                    self._process_session(session)

                self._last_process_check_ts = time.time()

            except Exception as e:
                logger.error(f"Error in system daemon loop: {e}", exc_info=True)

            time.sleep(check_interval)

    def _setup_logging(self) -> None:
        """Setup logging to file and journal/stdout."""
        try:
            self.config.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] [SystemDaemon] %(message)s",
                handlers=[
                    logging.FileHandler(self.config.log_file_path, encoding="utf-8"),
                    logging.StreamHandler(sys.stdout),
                ],
            )
        except Exception:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] [SystemDaemon] %(message)s",
            )

    def _refresh_rules(self) -> None:
        """Fetch updated schedule rules and app limit rules from Google Sheets."""
        try:
            rules, is_cached, age = self.client.fetch_rules(use_cache_on_failure=True)
            self.cached_rules = rules
            self.last_sync_time = time.time()
            logger.info(f"Schedule rules refreshed ({len(rules)} rules). Cached: {is_cached}")
        except Exception as e:
            logger.warning(f"Failed to refresh Google Sheet schedule rules: {e}")

        try:
            app_rules, app_cached, app_age = self.client.fetch_app_rules(use_cache_on_failure=True)
            self.cached_app_rules = app_rules
            logger.info(f"App limit rules refreshed ({len(app_rules)} rules). Cached: {app_cached}")
        except Exception as e:
            logger.warning(f"Failed to refresh Google Sheet app limit rules: {e}")

    def _sync_app_usages(self) -> None:
        """Push tracked app usage statistics to Google Sheets ('Apps Usages' tab)."""
        self.last_usage_sync_time = time.time()
        try:
            records = self.usage_store.get_usage_records_for_sync(device=self.config.effective_device_name)
            if records:
                success = self.client.push_app_usages(records)
                if success:
                    logger.info(f"Successfully synced {len(records)} app usage records to Google Sheets.")
        except Exception as e:
            logger.warning(f"Error syncing app usages to Google Sheets: {e}")

    def _process_session(self, session: UserSession) -> None:
        """Evaluate access and enforce rules for a single user session."""
        username = session.username
        uid = session.uid
        sid = session.session_id

        # Skip system accounts and display managers (UID < 1000, gdm, lightdm, etc.)
        if uid < 1000 or username.lower().strip() in SYSTEM_EXEMPT_USERS:
            return

        # Skip exempt accounts (e.g. root, parent admin)
        if not self.config.is_user_targeted(username):
            return

        now = datetime.now()

        # Evaluate rules for this user and device
        eval_res = evaluate_access(
            user=username,
            rules=self.cached_rules,
            check_dt=now,
            device=self.config.effective_device_name,
            exact_username_matching=self.config.rules.exact_username_matching,
        )


        # 1. New Session Initial Check
        if sid not in self.active_monitored:
            logger.info(f"New session detected: ID={sid}, User={username}, Type={session.session_type}")
            if not eval_res.is_allowed:
                logger.warning(f"Denying login for '{username}' (Session {sid}). Reason: {eval_res.reason}")
                self._handle_login_denial(session, eval_res)
                return

            # Login permitted -> Register session and send welcome notification
            logger.info(f"Access allowed for '{username}'. Active slot: {eval_res.active_slot.formatted_range() if eval_res.active_slot else 'N/A'}")
            mon_sess = MonitoredSession(
                session_id=sid,
                username=username,
                uid=uid,
                login_time=now,
                initial_check_passed=True,
            )
            self.active_monitored[sid] = mon_sess

            if self.config.warnings.show_notifications and eval_res.active_slot:
                end_str = eval_res.active_slot.end_time.strftime("%I:%M %p").lstrip("0")
                rem_mins = int(eval_res.remaining_minutes)
                if session.session_type in ("wayland", "x11"):
                    from parentalcontrol.system_service import wait_for_user_display
                    wait_for_user_display(uid, username, timeout_seconds=20.0)
                send_user_notification(
                    uid=uid,
                    username=username,
                    title="Parental Control Active",
                    message=f"Screen time is permitted until {end_str} ({rem_mins} min remaining).",
                    urgency="normal",
                    icon="dialog-information",
                )
            return

        # 2. Existing Session Monitoring
        mon_sess = self.active_monitored[sid]

        if not eval_res.is_allowed or eval_res.remaining_minutes <= 0.05:
            logger.info(f"Session {sid} for user '{username}' expired or revoked. Reason: {eval_res.reason}")
            self._handle_session_expired(session, eval_res)
            del self.active_monitored[sid]
            return

        # Milestone alerts (30 min, 20 min, 10 min, 5 min, 2 min)
        rem_mins = eval_res.remaining_minutes
        active_slot = eval_res.active_slot
        end_str = active_slot.end_time.strftime("%I:%M %p").lstrip("0") if active_slot else ""

        for threshold in sorted(self.config.warnings.intervals_minutes, reverse=True):
            if rem_mins <= threshold and threshold not in mon_sess.notified_thresholds:
                mon_sess.notified_thresholds.add(threshold)
                self._trigger_warning_milestone(session, threshold, rem_mins, end_str)

        # Enforce application limits, time windows, and track running application durations
        self._enforce_app_limits_for_session(session)

    def _enforce_app_limits_for_session(self, session: UserSession) -> None:
        """Scan running processes for child session, record active application usage, and enforce limits."""
        try:
            procs = scan_user_processes(session.uid)
            if not procs:
                return

            now_ts = time.time()
            elapsed_sec = int(min(60, max(1, now_ts - self._last_process_check_ts)))

            applicable_rules = self.app_enforcer.filter_applicable_rules(
                username=session.username,
                rules=self.cached_app_rules,
                device=self.config.effective_device_name,
                exact_user_matching=self.config.rules.exact_username_matching,
            )

            # Record usage for any running processes that match configured app rules
            # To avoid duplicate counting across multiple processes/threads of the same app, group by rule
            matched_rules_seen = set()
            for proc in procs:
                for rule in applicable_rules:
                    if rule.app_name in matched_rules_seen:
                        continue
                    if matches_process(proc, rule):
                        matched_rules_seen.add(rule.app_name)
                        exe_path = proc.appimage_path or proc.exe
                        binary_label = os.path.basename(exe_path) if exe_path else proc.name
                        self.usage_store.record_usage(
                            user=session.username,
                            app_key=rule.app_name,
                            app_name=rule.app_name,
                            binary_pattern=binary_label,
                            exe_path=exe_path,
                            elapsed_seconds=elapsed_sec,
                            daily_limit=rule.daily_limit_minutes,
                        )
                        break

            # Now run enforcement (blocks, time windows, quotas, milestone warnings)
            self.app_enforcer.enforce(
                username=session.username,
                uid=session.uid,
                running_procs=procs,
                rules=self.cached_app_rules,
                device=self.config.effective_device_name,
                exact_user_matching=self.config.rules.exact_username_matching,
            )

            # Persist usage state to local store
            self.usage_store.save()

        except Exception as e:
            logger.error(f"Error enforcing app limits for user '{session.username}': {e}", exc_info=True)

    def _trigger_warning_milestone(self, session: UserSession, threshold: int, rem_mins: float, end_str: str) -> None:
        """Send desktop notification and modal prompt into child's session."""
        logger.info(f"Warning milestone triggered: User '{session.username}', {threshold} mins left (ends at {end_str})")

        title = f"⏳ Screen Time Warning: {threshold} Minutes Left"
        if threshold <= 10:
            title = f"⚠️ Screen Time Ending Soon: {threshold} Minutes Left"

        msg = (
            f"Your allowed screen time will end at {end_str} "
            f"({int(round(rem_mins))} minutes remaining).\n"
            f"Please wrap up your activities and make sure to save your work!"
        )

        if self.config.warnings.play_sound:
            play_user_sound(session.uid, session.username, "alarm-clock-elapsed" if threshold <= 10 else "dialog-warning")

        if self.config.warnings.show_notifications:
            urgency = "critical" if threshold <= 10 else "normal"
            send_user_notification(
                uid=session.uid,
                username=session.username,
                title=title,
                message=msg,
                urgency=urgency,
                icon="dialog-warning",
                expire_time_ms=15000,
            )

        # Modal prompt dialog at 10 min, 5 min, 2 min
        if self.config.warnings.show_modal_prompts and threshold in (10, 5, 2):
            show_user_warning_dialog(
                uid=session.uid,
                username=session.username,
                title=f"Parental Control - {threshold} Minutes Remaining",
                text=(
                    f"⚠️ TIME WARNING: You have {threshold} minutes left of computer time today!\n\n"
                    f"Allowed screen time ends at {end_str}.\n\n"
                    f"Please finish what you are doing and save all games and documents now.\n"
                    f"The computer will automatically sign out when time expires."
                ),
                timeout_seconds=20,
            )

    def _on_override_granted(self, child_user: str, parent_user: str, duration: int) -> None:
        logger.info(f"IPC Server: Parent override approved for '{child_user}' by '{parent_user}' for {duration}m")

    def _on_logout_requested(self, session_id: Optional[str], child_user: str) -> None:
        logger.info(f"IPC Server: Voluntary logout requested for '{child_user}' (session {session_id})")
        sid = session_id or ""
        if not sid:
            for s in list_active_sessions():
                if s.username.lower() == child_user.lower():
                    sid = s.session_id
                    break
        terminate_session_by_id_or_user(sid, child_user)

    def _on_poweroff_requested(self) -> None:
        logger.info("IPC Server: Voluntary power off requested from lockout screen.")
        from parentalcontrol.system_service import poweroff_system
        poweroff_system()

    def _handle_login_denial(self, session: UserSession, result: AccessResult) -> None:
        """Display lockout overlay on child's screen and allow parent override, 5m extension, or logout."""
        now_str = result.current_time.strftime("%I:%M %p").lstrip("0")
        allowed_str = ", ".join(s.formatted_range() for s in result.allowed_slots_today) if result.allowed_slots_today else "No hours scheduled today"

        next_str = ""
        if result.next_slot:
            next_start = result.next_slot.start_time.strftime("%I:%M %p").lstrip("0")
            next_end = result.next_slot.end_time.strftime("%I:%M %p").lstrip("0")
            next_str = f"Next allowed session today: {next_start} - {next_end}"
        else:
            next_str = f"Allowed schedule today: {allowed_str}"

        # Wait for GUI display server to be ready before firing notifications, sounds, and GUI
        if session.session_type in ("wayland", "x11"):
            logger.info(f"Waiting for GUI display server before enforcing login denial for '{session.username}'...")
            from parentalcontrol.system_service import wait_for_user_display
            wait_for_user_display(session.uid, session.username, timeout_seconds=35.0)

        if self.config.warnings.play_sound:
            play_user_sound(session.uid, session.username, "dialog-warning")

        if self.config.warnings.show_notifications:
            send_user_notification(
                uid=session.uid,
                username=session.username,
                title="Access Restricted",
                message=f"Screen time not allowed right now. {next_str}",
                urgency="critical",
                icon="dialog-error",
            )

        self._enforce_lockout_overlay(
            session=session,
            reason=f"Login is not permitted right now ({now_str}). {result.reason}",
            next_session_info=next_str,
            is_login_denial=True,
        )

    def _handle_session_expired(self, session: UserSession, eval_res: AccessResult) -> None:
        """Handle session expiration by presenting lockout overlay with override option."""
        if self.config.warnings.play_sound:
            play_user_sound(session.uid, session.username, "alarm-clock-elapsed")

        # Determine if there is another scheduled session later today
        next_session_info = ""
        if eval_res.next_slot:
            next_start = eval_res.next_slot.start_time.strftime("%I:%M %p").lstrip("0")
            next_end = eval_res.next_slot.end_time.strftime("%I:%M %p").lstrip("0")
            next_session_info = f"Next allowed session today: {next_start} - {next_end}"
            notif_msg = f"Current session has ended. Next session today: {next_start} - {next_end}."
        else:
            notif_msg = "Current screen time session has ended."

        if self.config.warnings.show_notifications:
            send_user_notification(
                uid=session.uid,
                username=session.username,
                title="⏰ Session Ended",
                message=notif_msg,
                urgency="critical",
                icon="dialog-error",
            )

        self._enforce_lockout_overlay(
            session=session,
            reason="Your permitted screen time for this session is over.",
            next_session_info=next_session_info,
            is_login_denial=False,
        )

    def _enforce_lockout_overlay(
        self,
        session: UserSession,
        reason: str,
        next_session_info: str = "",
        is_login_denial: bool = False,
    ) -> None:
        """Display the always-on-top lockout overlay with anti-tamper supervision."""
        from parentalcontrol.override_manager import get_active_override

        exempt_list = ",".join(
            u for u in self.config.rules.exempt_users
            if u.lower().strip() not in ("gdm", "gdm3", "lightdm", "sddm", "daemon", "nobody", "*", "all")
        ) or "atul"

        cmd = [
            "parentalcontrol",
            "lockout-screen",
            "--user",
            session.username,
            "--exempt-users",
            exempt_list,
            "--reason",
            reason,
        ]
        if next_session_info:
            cmd.extend(["--next-session", next_session_info])
        if session.session_id:
            cmd.extend(["--session-id", session.session_id])
        if is_login_denial:
            cmd.append("--login-denial")

        logger.info(f"Launching lockout overlay for user '{session.username}' (Session {session.session_id})...")
        t0 = time.time()
        proc = run_in_user_session(session.uid, session.username, cmd, async_proc=True, wait_display=True)

        if not proc:
            logger.warning(f"Could not launch GUI overlay for {session.username}. Falling back to modal warning dialog.")
            show_user_countdown_dialog(
                uid=session.uid,
                username=session.username,
                title="Parental Control - Access Restricted",
                message_prefix=f"⏰ SCREEN TIME RESTRICTED\n\n{reason}\n{next_session_info}",
                countdown_seconds=self.config.enforcement.login_denial_grace_seconds,
            )
            terminate_session_by_id_or_user(session.session_id, session.username)
            return

        # Supervise the lockout process
        ret_code = proc.wait()
        elapsed = time.time() - t0
        logger.info(f"Lockout overlay for user '{session.username}' exited with code {ret_code} after {elapsed:.1f}s.")

        # Check if parent override or 5m work extension was granted during lockout
        active_override = get_active_override(session.username)
        if active_override and active_override.get("expires_at", 0) > time.time():
            granted_by = active_override.get("granted_by", "")
            if is_login_denial and "Save Work" in granted_by:
                logger.warning(f"Rejecting self-service extension for '{session.username}' during login denial.")
            else:
                logger.info(f"Override verified for '{session.username}' (by {granted_by})! Session preserved safely.")
                if session.session_id not in self.active_monitored:
                    self.active_monitored[session.session_id] = MonitoredSession(
                        session_id=session.session_id,
                        username=session.username,
                        uid=session.uid,
                        login_time=datetime.now(),
                        initial_check_passed=True,
                    )
                return

        # If the GUI exited with error or closed unexpectedly, show countdown dialog so user sees reason
        from parentalcontrol.lockout_gui import EXIT_ERROR, EXIT_UNLOCKED
        if ret_code == EXIT_ERROR or (ret_code != EXIT_UNLOCKED and elapsed < 5.0):
            logger.warning(f"Lockout overlay closed or failed ({elapsed:.1f}s, code {ret_code}). Presenting fallback warning dialog.")
            show_user_countdown_dialog(
                uid=session.uid,
                username=session.username,
                title="Parental Control - Access Restricted",
                message_prefix=f"⏰ ACCESS RESTRICTED\n\n{reason}\n{next_session_info}",
                countdown_seconds=self.config.enforcement.login_denial_grace_seconds,
            )
            # Re-check override after countdown dialog in case parent intervened
            active_override = get_active_override(session.username)
            if active_override and active_override.get("expires_at", 0) > time.time():
                logger.info(f"Parent override verified for '{session.username}' after dialog.")
                return

        # If no override granted (clicked Logout or tampered/killed), terminate session
        logger.warning(f"No valid override for '{session.username}' (code {ret_code}). Terminating session.")
        terminate_session_by_id_or_user(session.session_id, session.username)

    def _handle_signal(self, signum, frame) -> None:
        logger.info(f"Received signal {signum}. Stopping System Parental Control Daemon...")
        self._running = False
        try:
            self.usage_store.save()
            self._sync_app_usages()
        except Exception:
            pass
        if self.ipc_server:
            try:
                self.ipc_server.stop()
            except Exception:
                pass
        sys.exit(0)
