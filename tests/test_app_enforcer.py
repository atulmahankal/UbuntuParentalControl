"""Unit tests for application enforcer and rules evaluation."""

from datetime import datetime, time
from pathlib import Path
from unittest.mock import MagicMock, patch

from parentalcontrol.app_enforcer import AppEnforcer
from parentalcontrol.app_monitor import ProcessInfo
from parentalcontrol.app_usage_store import AppUsageStore
from parentalcontrol.models import AppLimitRule


def test_filter_applicable_rules(tmp_path: Path):
    store = AppUsageStore(tmp_path / "store.json")
    enforcer = AppEnforcer(store)

    today_weekday = datetime.now().strftime("%A")

    rules = [
        AppLimitRule(user="himanshu", app_name="Chrome", patterns=["chrome"], day=today_weekday, device="*"),
        AppLimitRule(user="himanshi", app_name="Roblox", patterns=["roblox"], day=today_weekday, device="*"),
        AppLimitRule(user="himanshu", app_name="Steam", patterns=["steam"], day=today_weekday, device="laptop"),
    ]

    filtered = enforcer.filter_applicable_rules(
        username="himanshu",
        rules=rules,
        device="optiplex-3050",
        exact_user_matching=True,
    )
    assert len(filtered) == 1
    assert filtered[0].app_name == "Chrome"


def test_enforce_blocked_app(tmp_path: Path):
    store = AppUsageStore(tmp_path / "store.json")
    enforcer = AppEnforcer(store)

    rule = AppLimitRule(
        user="himanshu",
        app_name="Discord",
        patterns=["discord"],
        allowed=False,
    )

    proc = ProcessInfo(pid=9999, uid=1000, name="discord", exe="/usr/bin/discord")

    with patch("parentalcontrol.app_enforcer.terminate_process") as mock_term, \
         patch("parentalcontrol.app_enforcer.send_user_notification") as mock_notif, \
         patch("parentalcontrol.app_enforcer.play_user_sound") as mock_sound:
        
        enforcer.enforce(
            username="himanshu",
            uid=1000,
            running_procs=[proc],
            rules=[rule],
            device="optiplex-3050",
            exact_user_matching=True,
        )

        mock_term.assert_called_once_with(9999)
        mock_notif.assert_called_once()
        assert store.get_status("himanshu", "discord") == "Blocked"


def test_enforce_quota_reached(tmp_path: Path):
    store = AppUsageStore(tmp_path / "store.json")
    enforcer = AppEnforcer(store)

    rule = AppLimitRule(
        user="himanshu",
        app_name="Chrome",
        patterns=["chrome"],
        allowed=True,
        daily_limit_minutes=30,
    )

    # Record 35 minutes used (exceeds 30)
    store.record_usage(
        user="himanshu",
        app_key="chrome",
        app_name="Chrome",
        binary_pattern="chrome",
        exe_path="/usr/bin/chrome",
        elapsed_seconds=35 * 60,
        daily_limit=30,
    )

    proc = ProcessInfo(pid=8888, uid=1000, name="chrome", exe="/usr/bin/chrome")

    with patch("parentalcontrol.app_enforcer.terminate_process") as mock_term, \
         patch("parentalcontrol.app_enforcer.send_user_notification") as mock_notif, \
         patch("parentalcontrol.app_enforcer.play_user_sound") as mock_sound:

        enforcer.enforce(
            username="himanshu",
            uid=1000,
            running_procs=[proc],
            rules=[rule],
            device="optiplex-3050",
            exact_user_matching=True,
        )

        mock_term.assert_called_once_with(8888)
        assert store.get_status("himanshu", "chrome") == "Quota Reached"
