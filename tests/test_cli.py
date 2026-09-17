import pytest
from unittest.mock import patch
from pathlib import Path
from parentalcontrol.autostart import create_desktop_entry_content, install_user_autostart, uninstall_autostart
from parentalcontrol.config import AppConfig, RulesConfig
from parentalcontrol.cli import get_system_users, cmd_list_users
import argparse

def test_desktop_entry_generation():
    content = create_desktop_entry_content(exec_command="/usr/bin/parentalcontrol monitor")
    assert "[Desktop Entry]" in content
    assert "Exec=/usr/bin/parentalcontrol monitor" in content
    assert "X-GNOME-Autostart-enabled=true" in content

def test_autostart_install_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop_file = install_user_autostart(exec_command="/custom/path/parentalcontrol monitor")
    assert desktop_file.exists()
    assert desktop_file.name == "parental-control.desktop"

    removed = uninstall_autostart()
    assert removed is True
    assert not desktop_file.exists()

def test_cli_update_subcommand(monkeypatch):
    from parentalcontrol.cli import cmd_update
    
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with patch("subprocess.run") as mock_sub:
        args = argparse.Namespace()
        cfg = AppConfig()
        cmd_update(args, cfg)
        assert mock_sub.call_count >= 1

def test_get_system_users():
    cfg = AppConfig(rules=RulesConfig(target_users=["himanshu"], exempt_users=["atul", "root"]))
    users = get_system_users(cfg)
    assert isinstance(users, list)
    if users:
        assert "username" in users[0]
        assert "uid" in users[0]
        assert "status" in users[0]

def test_cli_version_flag(capsys):
    from parentalcontrol import __version__
    import sys
    from parentalcontrol.cli import main

    with patch.object(sys, "argv", ["parentalcontrol", "-v"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert __version__ in captured.out or __version__ in captured.err


def test_cli_check_pam(capsys):
    import sys
    from parentalcontrol.cli import cmd_check
    from parentalcontrol.models import AccessResult, TimeSlot
    from datetime import datetime, time

    cfg = AppConfig(rules=RulesConfig(target_users=["himanshu"], exempt_users=["atul"]))

    # Test allowed
    with patch("parentalcontrol.cli.GoogleSheetClient.fetch_rules", return_value=([], False, 0)), \
         patch("parentalcontrol.cli.evaluate_access") as mock_eval:
        mock_eval.return_value = AccessResult(
            is_allowed=True,
            reason="Within scheduled slot",
            user="himanshu",
            current_time=datetime.now(),
        )
        args = argparse.Namespace(user="himanshu", device="optiplex", url=None, pam=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_check(args, cfg)
        assert exc_info.value.code == 0

    # Test denied
    with patch("parentalcontrol.cli.GoogleSheetClient.fetch_rules", return_value=([], False, 0)), \
         patch("parentalcontrol.cli.evaluate_access") as mock_eval:
        mock_eval.return_value = AccessResult(
            is_allowed=False,
            reason="Outside allowed hours",
            user="himanshu",
            current_time=datetime.now(),
            next_slot=TimeSlot(start_time=time(16, 0), end_time=time(20, 0), allowed=True),
        )
        args = argparse.Namespace(user="himanshu", device="optiplex", url=None, pam=True)
        with pytest.raises(SystemExit) as exc_info:
            cmd_check(args, cfg)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Parental Control: Computer access is restricted" in captured.out
        assert "4:00 PM - 8:00 PM" in captured.out

