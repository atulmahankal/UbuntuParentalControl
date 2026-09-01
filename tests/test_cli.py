import pytest
from unittest.mock import patch
from pathlib import Path
from parentalcontrol.autostart import create_desktop_entry_content, install_user_autostart, uninstall_autostart
from parentalcontrol.config import AppConfig

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
    import argparse
    from parentalcontrol.cli import cmd_update
    
    monkeypatch.setattr("os.geteuid", lambda: 0)
    with patch("subprocess.run") as mock_sub:
        args = argparse.Namespace()
        cfg = AppConfig()
        cmd_update(args, cfg)
        assert mock_sub.call_count >= 1
