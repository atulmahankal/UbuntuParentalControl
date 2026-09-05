"""System-level multi-session monitoring daemon for Ubuntu."""

import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

from parentalcontrol.config import AppConfig, load_config
from parentalcontrol.evaluator import evaluate_access
from parentalcontrol.models import AccessResult, ScheduleRule
from parentalcontrol.sheet_client import GoogleSheetClient
from parentalcontrol.system_service import (
    UserSession,
    list_active_sessions,
    play_user_sound,
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
            cache_path=self.config.cache_file_path,
        )
        self.active_monitored: Dict[str, MonitoredSession] = {}
        self.cached_rules: List[ScheduleRule] = []
        self.last_sync_time: float = 0.0
        self._running: bool = True

    def start(self) -> None:
        """Start the system-wide service daemon loop."""
        self._setup_logging()
        logger.info("=======================================================")
        logger.info("Parental Control System Service Daemon starting...")
        logger.info(f"Target users: {self.config.rules.target_users or 'ALL non-exempt'}")
        logger.info(f"Exempt users: {self.config.rules.exempt_users}")
        logger.info(f"Config path: {self.config.config_file_path}")
        logger.info("=======================================================")

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # Initial schedule fetch
        self._refresh_rules()

        check_interval = 10  # Check every 10 seconds
        sync_interval_sec = max(60, self.config.google_sheet.sync_interval_minutes * 60)

        while self._running:
            try:
                # Periodic Google Sheet sync
                if time.time() - self.last_sync_time > sync_interval_sec:
                    self._refresh_rules()

                # Discover active sessions
                sessions = list_active_sessions()
                current_session_ids = {s.session_id for s in sessions}

                # Clean up ended sessions
                ended_ids = set(self.active_monitored.keys()) - current_session_ids
                for sid in ended_ids:
                    logger.info(f"Session {sid} ({self.active_monitored[sid].username}) ended.")
                    del self.active_monitored[sid]

                # Inspect each active session
                for session in sessions:
                    self._process_session(session)

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
        """Fetch updated schedule rules from Google Sheets."""
        try:
            rules, is_cached, age = self.client.fetch_rules(use_cache_on_failure=True)
            self.cached_rules = rules
            self.last_sync_time = time.time()
            logger.info(f"Schedule rules refreshed ({len(rules)} rules). Cached: {is_cached}")
        except Exception as e:
            logger.warning(f"Failed to refresh Google Sheet rules: {e}")

    def _process_session(self, session: UserSession) -> None:
        """Evaluate access and enforce rules for a single user session."""
        username = session.username
        uid = session.uid
        sid = session.session_id

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

    def _handle_login_denial(self, session: UserSession, result: AccessResult) -> None:
        """Display lockout countdown on child's screen and terminate session."""
        now_str = result.current_time.strftime("%I:%M %p").lstrip("0")
        allowed_str = ", ".join(s.formatted_range() for s in result.allowed_slots_today) if result.allowed_slots_today else "No hours scheduled today"
        custom_msg = f"\nNote: {result.custom_message}" if result.custom_message else ""

        msg = (
            f"⛔ SCREEN TIME RESTRICTED\n\n"
            f"Login is not permitted for '{session.username}' right now.\n"
            f"Current Time: {now_str}\n"
            f"Reason: {result.reason}\n"
            f"Allowed Hours Today: {allowed_str}"
            f"{custom_msg}"
        )

        if self.config.warnings.play_sound:
            play_user_sound(session.uid, session.username, "dialog-warning")

        if self.config.warnings.show_notifications:
            send_user_notification(
                uid=session.uid,
                username=session.username,
                title="Access Restricted",
                message=f"Screen time not allowed right now. Allowed today: {allowed_str}",
                urgency="critical",
                icon="dialog-error",
            )

        grace_sec = self.config.enforcement.login_denial_grace_seconds
        show_user_countdown_dialog(
            uid=session.uid,
            username=session.username,
            title="Access Restricted - Parental Control",
            message_prefix=msg,
            countdown_seconds=grace_sec,
        )

        terminate_session_by_id_or_user(session.session_id, session.username)

    def _handle_session_expired(self, session: UserSession, eval_res: AccessResult) -> None:
        """Display final countdown on child's screen and terminate session."""
        if self.config.warnings.play_sound:
            play_user_sound(session.uid, session.username, "alarm-clock-elapsed")

        if self.config.warnings.show_notifications:
            send_user_notification(
                uid=session.uid,
                username=session.username,
                title="⏰ Screen Time Expired",
                message="Your screen time has ended. The session will sign out now.",
                urgency="critical",
                icon="dialog-error",
            )

        grace_seconds = self.config.enforcement.termination_grace_seconds
        msg = (
            f"⏰ SCREEN TIME HAS EXPIRED\n\n"
            f"Your permitted computer time for today has ended.\n"
            f"The session will automatically sign out in {grace_seconds} seconds.\n\n"
            f"Please save any unsaved work immediately!"
        )

        show_user_countdown_dialog(
            uid=session.uid,
            username=session.username,
            title="Parental Control - Screen Time Expired",
            message_prefix=msg,
            countdown_seconds=grace_seconds,
        )

        terminate_session_by_id_or_user(session.session_id, session.username)

    def _handle_signal(self, signum, frame) -> None:
        logger.info(f"Received signal {signum}. Stopping System Parental Control Daemon...")
        self._running = False
        sys.exit(0)
