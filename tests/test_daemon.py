from datetime import datetime, time
from unittest.mock import MagicMock, patch
import pytest
from parentalcontrol.config import AppConfig, GoogleSheetConfig, RulesConfig, WarningsConfig
from parentalcontrol.models import ScheduleRule
from parentalcontrol.daemon import ParentalControlMonitor, check_and_enforce_login

def test_check_login_exempt_user():
    cfg = AppConfig(
        rules=RulesConfig(target_users=["child1"], exempt_users=["admin_parent"])
    )
    result = check_and_enforce_login(cfg, username="admin_parent")
    assert result.is_allowed is True
    assert "exempt" in result.reason.lower()

def test_monitor_milestone_trigger():
    cfg = AppConfig(
        google_sheet=GoogleSheetConfig(url="https://dummy.url"),
        rules=RulesConfig(target_users=["alex"], exempt_users=[]),
        warnings=WarningsConfig(intervals_minutes=[30, 20, 10, 5, 2], show_modal_prompts=False, play_sound=False),
    )
    monitor = ParentalControlMonitor(config=cfg, username="alex")
    
    with patch.object(monitor, "_trigger_warning_prompt") as mock_trigger:
        # Simulate 25 minutes remaining (crosses 30 threshold)
        rem_mins = 25.0
        end_str = "08:00 PM"
        for threshold in sorted(cfg.warnings.intervals_minutes, reverse=True):
            if rem_mins <= threshold and threshold not in monitor.notified_thresholds:
                monitor.notified_thresholds.add(threshold)
                monitor._trigger_warning_prompt(threshold, rem_mins, end_str)

        assert 30 in monitor.notified_thresholds
        assert 20 not in monitor.notified_thresholds
        mock_trigger.assert_called_once_with(30, 25.0, "08:00 PM")

    # Simulate next check at 18 minutes remaining (crosses 20 threshold)
    with patch.object(monitor, "_trigger_warning_prompt") as mock_trigger:
        rem_mins = 18.0
        for threshold in sorted(cfg.warnings.intervals_minutes, reverse=True):
            if rem_mins <= threshold and threshold not in monitor.notified_thresholds:
                monitor.notified_thresholds.add(threshold)
                monitor._trigger_warning_prompt(threshold, rem_mins, end_str)

        assert 20 in monitor.notified_thresholds
        assert 10 not in monitor.notified_thresholds
        mock_trigger.assert_called_once_with(20, 18.0, "08:00 PM")

    # Simulate next check at 9 minutes remaining (crosses 10 threshold)
    with patch.object(monitor, "_trigger_warning_prompt") as mock_trigger:
        rem_mins = 9.0
        for threshold in sorted(cfg.warnings.intervals_minutes, reverse=True):
            if rem_mins <= threshold and threshold not in monitor.notified_thresholds:
                monitor.notified_thresholds.add(threshold)
                monitor._trigger_warning_prompt(threshold, rem_mins, end_str)

        assert 10 in monitor.notified_thresholds
        mock_trigger.assert_called_once_with(10, 9.0, "08:00 PM")
