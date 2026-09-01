"""Daemon and session monitoring loop for Parental Control."""

import getpass
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Optional, Set

from parentalcontrol.config import AppConfig
from parentalcontrol.evaluator import evaluate_access
from parentalcontrol.models import AccessResult
from parentalcontrol.notifier import (
    play_alert_sound,
    send_notification,
    show_countdown_dialog,
    show_warning_dialog,
)
from parentalcontrol.session import terminate_session
from parentalcontrol.sheet_client import GoogleSheetClient

logger = logging.getLogger(__name__)


def setup_logging(config: AppConfig) -> None:
    """Configure file and console logging."""
    try:
        config.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(config.log_file_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def check_and_enforce_login(
    config: AppConfig,
    username: Optional[str] = None,
    client: Optional[GoogleSheetClient] = None,
) -> AccessResult:
    """Check login permission on startup. If denied, show warning and terminate session."""
    user = username or getpass.getuser()

    if not config.is_user_targeted(user):
        logger.info(f"User '{user}' is exempt from parental control.")
        return AccessResult(
            is_allowed=True,
            reason="User is exempt from parental control.",
            user=user,
            current_time=datetime.now(),
        )

    if not config.google_sheet.url and not config.google_sheet.service_account_path:
        logger.warning("No Google Sheet configured. Allowing login by default.")
        return AccessResult(
            is_allowed=True,
            reason="Google Sheet not configured.",
            user=user,
            current_time=datetime.now(),
        )

    sheet_client = client or GoogleSheetClient(
        sheet_url=config.google_sheet.url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        cache_path=config.cache_file_path,
    )

    try:
        rules, is_cached, cache_age = sheet_client.fetch_rules(use_cache_on_failure=True)
    except Exception as e:
        logger.error(f"Failed to fetch rules: {e}")
        if config.rules.offline_policy == "block":
            eval_res = AccessResult(
                is_allowed=False,
                reason="Unable to connect to parental control server and offline policy is set to block.",
                user=user,
                current_time=datetime.now(),
            )
            _handle_login_denial(config, eval_res)
            return eval_res
        # Allow grace if no cache
        return AccessResult(
            is_allowed=True,
            reason="Offline grace period granted.",
            user=user,
            current_time=datetime.now(),
        )

    result = evaluate_access(
        user=user,
        rules=rules,
        check_dt=datetime.now(),
        is_cached=is_cached,
        cache_age_seconds=cache_age,
    )

    if not result.is_allowed:
        logger.warning(f"Login denied for user '{user}'. Reason: {result.reason}")
        _handle_login_denial(config, result)
    else:
        logger.info(f"Login permitted for user '{user}'. Active slot: {result.active_slot.formatted_range() if result.active_slot else 'N/A'}")
        if config.warnings.show_notifications:
            end_str = result.active_slot.end_time.strftime("%I:%M %p").lstrip("0") if result.active_slot else ""
            rem_mins = int(result.remaining_minutes)
            send_notification(
                title="Parental Control Active",
                message=f"Welcome {user}! Screen time is allowed until {end_str} ({rem_mins} minutes remaining).",
                urgency="normal",
                icon="dialog-information",
            )

    return result


def _handle_login_denial(config: AppConfig, result: AccessResult) -> None:
    """Display denial UI countdown and terminate the session."""
    now_str = result.current_time.strftime("%I:%M %p").lstrip("0")
    allowed_str = ", ".join(s.formatted_range() for s in result.allowed_slots_today) if result.allowed_slots_today else "No hours scheduled today"
    custom_msg = f"\nNote: {result.custom_message}" if result.custom_message else ""

    msg = (
        f"⛔ SCREEN TIME RESTRICTED\n\n"
        f"Login is not permitted for '{result.user}' right now.\n"
        f"Current Time: {now_str}\n"
        f"Reason: {result.reason}\n"
        f"Allowed Hours Today: {allowed_str}"
        f"{custom_msg}"
    )

    if config.warnings.play_sound:
        play_alert_sound("dialog-warning")

    if config.warnings.show_notifications:
        send_notification(
            title="Access Restricted",
            message=f"Screen time not allowed right now. Allowed today: {allowed_str}",
            urgency="critical",
            icon="dialog-error",
        )

    # Show countdown window
    grace_sec = config.enforcement.login_denial_grace_seconds
    show_countdown_dialog(
        title="Access Restricted - Parental Control",
        message_prefix=msg,
        countdown_seconds=grace_sec,
    )

    # Terminate session
    terminate_session(config.enforcement.logout_command)
    sys.exit(1)


class ParentalControlMonitor:
    """Continuous session monitoring daemon."""

    def __init__(self, config: AppConfig, username: Optional[str] = None):
        self.config = config
        self.user = username or getpass.getuser()
        self.client = GoogleSheetClient(
            sheet_url=config.google_sheet.url,
            service_account_path=config.google_sheet.service_account_path,
            sheet_name=config.google_sheet.sheet_name,
            cache_path=config.cache_file_path,
        )
        self.notified_thresholds: Set[int] = set()
        self.last_sync_time = 0.0
        self.cached_rules = []
        self._running = True

    def start(self) -> None:
        """Start the monitoring loop."""
        setup_logging(self.config)
        logger.info(f"Starting Parental Control Monitor for user '{self.user}'...")

        # Trap termination signals
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        # 1. Initial Login Check
        result = check_and_enforce_login(self.config, self.user, self.client)
        if not result.is_allowed:
            return

        if not self.config.is_user_targeted(self.user):
            logger.info(f"User '{self.user}' is exempt. Exiting monitor.")
            return

        # 2. Main Monitoring Loop
        check_interval = 15  # Check every 15 seconds
        sync_interval_sec = max(60, self.config.google_sheet.sync_interval_minutes * 60)

        while self._running:
            try:
                now = datetime.now()

                # Periodic sheet refresh
                if time.time() - self.last_sync_time > sync_interval_sec or not self.cached_rules:
                    try:
                        rules, is_cached, age = self.client.fetch_rules(use_cache_on_failure=True)
                        self.cached_rules = rules
                        self.last_sync_time = time.time()
                    except Exception as e:
                        logger.warning(f"Error syncing rules during monitor: {e}")

                # Evaluate current access
                eval_res = evaluate_access(
                    user=self.user,
                    rules=self.cached_rules,
                    check_dt=now,
                )

                if not eval_res.is_allowed:
                    logger.info(f"Access expired or revoked for user '{self.user}'. Reason: {eval_res.reason}")
                    self._handle_time_expired(eval_res)
                    break

                # Check remaining time and warning milestones
                rem_mins = eval_res.remaining_minutes
                active_slot = eval_res.active_slot
                end_str = active_slot.end_time.strftime("%I:%M %p").lstrip("0") if active_slot else ""

                # Reset notified thresholds if new window started
                # Check intervals e.g. [30, 20, 10, 5, 2]
                for threshold in sorted(self.config.warnings.intervals_minutes, reverse=True):
                    if rem_mins <= threshold and threshold not in self.notified_thresholds:
                        self.notified_thresholds.add(threshold)
                        self._trigger_warning_prompt(threshold, rem_mins, end_str)

                # If remaining time is 0 or negative
                if rem_mins <= 0.05:  # within a few seconds of end
                    self._handle_time_expired(eval_res)
                    break

            except Exception as e:
                logger.error(f"Unexpected error in monitor loop: {e}", exc_info=True)

            time.sleep(check_interval)

    def _trigger_warning_prompt(self, threshold: int, rem_mins: float, end_time_str: str) -> None:
        """Trigger prompt and notification when a countdown milestone is reached."""
        logger.info(f"Screen time milestone reached: {threshold} minutes remaining (ends at {end_time_str}).")

        title = f"⏳ Screen Time Warning: {threshold} Minutes Left"
        if threshold <= 10:
            title = f"⚠️ Screen Time Ending Soon: {threshold} Minutes Left"

        msg = (
            f"Your allowed screen time will end at {end_time_str} "
            f"({int(round(rem_mins))} minutes remaining).\n"
            f"Please wrap up your activities and make sure to save your open work!"
        )

        if self.config.warnings.play_sound:
            play_alert_sound("alarm-clock-elapsed" if threshold <= 10 else "dialog-warning")

        if self.config.warnings.show_notifications:
            urgency = "critical" if threshold <= 10 else "normal"
            send_notification(
                title=title,
                message=msg,
                urgency=urgency,
                icon="dialog-warning",
                expire_time_ms=15000,
            )

        # Show interactive modal popup at 10 minutes (or configurable thresholds <= 10)
        if self.config.warnings.show_modal_prompts and threshold in (10, 5, 2):
            show_warning_dialog(
                title=f"Parental Control - {threshold} Minutes Remaining",
                text=(
                    f"⚠️ TIME WARNING: You have {threshold} minutes left of computer time today!\n\n"
                    f"Allowed screen time ends at {end_time_str}.\n\n"
                    f"Please finish what you are doing and save all games and documents now.\n"
                    f"The computer will automatically sign out when time expires."
                ),
                timeout_seconds=20,
            )

    def _handle_time_expired(self, eval_res: AccessResult) -> None:
        """Handle screen time expiration: countdown dialog + auto signout."""
        logger.info("Time expired! Initiating signout countdown...")

        if self.config.warnings.play_sound:
            play_alert_sound("alarm-clock-elapsed")

        if self.config.warnings.show_notifications:
            send_notification(
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

        show_countdown_dialog(
            title="Parental Control - Screen Time Expired",
            message_prefix=msg,
            countdown_seconds=grace_seconds,
        )

        terminate_session(self.config.enforcement.logout_command)

    def _handle_signal(self, signum, frame) -> None:
        logger.info(f"Received signal {signum}. Terminating monitor...")
        self._running = False
        sys.exit(0)
