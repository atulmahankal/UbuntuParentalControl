import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from parentalcontrol.system_service import (
    UserSession,
    generate_systemd_service_content,
    _get_user_env,
)
from parentalcontrol.system_daemon import (
    SystemParentalControlDaemon,
    MonitoredSession,
)
from parentalcontrol.config import AppConfig, RulesConfig, WarningsConfig
from parentalcontrol.models import ScheduleRule
from datetime import time

def test_generate_systemd_service_content():
    content = generate_systemd_service_content("/usr/local/bin/parentalcontrol")
    assert "[Unit]" in content
    assert "Description=Parental Control" in content
    assert "ExecStart=/usr/local/bin/parentalcontrol run-service" in content
    assert "User=root" in content
    assert "Restart=always" in content
    assert "WantedBy=multi-user.target" in content

def test_get_user_env():
    env = _get_user_env(uid=1000, username="alex")
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["USER"] == "alex"
    assert env["HOME"] == "/home/alex"

def test_system_daemon_milestones():
    cfg = AppConfig(
        rules=RulesConfig(target_users=["alex"], exempt_users=["parent", "root"]),
        warnings=WarningsConfig(intervals_minutes=[30, 20, 10, 5, 2], show_modal_prompts=False, play_sound=False),
    )
    daemon = SystemParentalControlDaemon(config=cfg)
    sess = UserSession(session_id="1", uid=1001, username="alex", seat="seat0", session_type="wayland", state="active")
    
    with patch("parentalcontrol.system_daemon.send_user_notification") as mock_notif:
        daemon._trigger_warning_milestone(sess, threshold=30, rem_mins=28.0, end_str="08:00 PM")
        mock_notif.assert_called_once()
        args, kwargs = mock_notif.call_args
        assert kwargs["uid"] == 1001
        assert kwargs["username"] == "alex"
        assert "30 Minutes Left" in kwargs["title"]
