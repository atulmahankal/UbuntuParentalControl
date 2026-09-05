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


def test_system_sessions_skipped_in_list_active_sessions():
    from parentalcontrol.system_service import list_active_sessions

    fake_list = (
        "c1 128 gdm seat0\n"
        "2 1001 himanshu seat0\n"
        "3 0 root seat0\n"
    )
    with patch("shutil.which", return_value="/bin/loginctl"), \
         patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=fake_list, returncode=0),
            MagicMock(stdout="Type=wayland\nState=active\nName=gdm\n", returncode=0),
            MagicMock(stdout="Type=wayland\nState=active\nName=himanshu\n", returncode=0),
            MagicMock(stdout="Type=tty\nState=active\nName=root\n", returncode=0),
        ]
        sessions = list_active_sessions()
        # gdm (uid 128) and root (uid 0) must be skipped, only himanshu (uid 1001) should be returned
        assert len(sessions) == 1
        assert sessions[0].username == "himanshu"
        assert sessions[0].uid == 1001


def test_terminate_session_refuses_system_users():
    from parentalcontrol.system_service import terminate_session_by_id_or_user

    with patch("subprocess.run") as mock_run:
        # Refuse to terminate gdm
        terminate_session_by_id_or_user("c1", "gdm")
        mock_run.assert_not_called()

        # Refuse to terminate lightdm
        terminate_session_by_id_or_user("c2", "lightdm")
        mock_run.assert_not_called()

        # Refuse to terminate root
        terminate_session_by_id_or_user("c3", "root")
        mock_run.assert_not_called()


def test_system_daemon_stops_on_wildcard_target_users():
    cfg = AppConfig(
        rules=RulesConfig(target_users=["*"], exempt_users=["root", "atul"]),
    )
    daemon = SystemParentalControlDaemon(config=cfg)

    with pytest.raises(SystemExit) as exc_info:
        daemon.start()

    assert exc_info.value.code == 1


def test_install_launcher_wrapper(tmp_path, monkeypatch):
    from parentalcontrol.system_service import install_launcher_wrapper

    target_wrapper = tmp_path / "parentalcontrol"
    monkeypatch.setattr("parentalcontrol.system_service.WRAPPER_SCRIPT_PATH", target_wrapper)
    installed_path = install_launcher_wrapper(install_dir=tmp_path / "opt")
    assert installed_path.exists()
    content = installed_path.read_text(encoding="utf-8")
    assert "#!/usr/bin/env bash" in content
    assert "venv --clear --python" in content
    assert 'exec "${VENV_PYTHON}" -m parentalcontrol' in content
