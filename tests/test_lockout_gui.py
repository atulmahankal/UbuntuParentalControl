import sys
from unittest.mock import patch, MagicMock

if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

import gi
gi.require_version("Gtk", "3.0")

from parentalcontrol.lockout_gui import (
    run_lockout_screen,
    filter_lockout_key_event,
    GnomeKeybindingSuppressor,
    EXIT_UNLOCKED,
    EXIT_LOGOUT,
)


def test_lockout_cli_fallback():
    # Simulate headless environment where Gtk.init_check returns False
    with patch("parentalcontrol.lockout_gui._run_cli_fallback", return_value=EXIT_LOGOUT) as mock_fb:
        with patch("gi.repository.Gtk.init_check", return_value=(False, None)):
            res = run_lockout_screen(
                child_user="himanshu",
                exempt_users=["atul"],
                reason="Test reason",
                testing_mode=True,
            )
            assert res == EXIT_LOGOUT
            mock_fb.assert_called_once()


def test_filter_lockout_key_event():
    # Key values:
    # Escape: 0xFF1B
    # Tab: 0xFF09, ISO_Left_Tab: 0xFE20
    # Modifiers: Shift: 0x01, Control: 0x04, Mod1 (Alt): 0x08, Super: 0x04000000

    # 1. Escape
    assert filter_lockout_key_event(0xFF1B, 0, testing_mode=True) == "exit"
    assert filter_lockout_key_event(0xFF1B, 0, testing_mode=False) == "pass"

    # 2. Tab combinations:
    # Plain Tab and Shift+Tab navigate form widgets safely
    assert filter_lockout_key_event(0xFF09, 0) == "pass"
    assert filter_lockout_key_event(0xFF09, 0x01) == "pass"  # Shift+Tab
    # Ctrl+Tab, Alt+Tab, Super+Tab MUST be blocked
    assert filter_lockout_key_event(0xFF09, 0x04) == "block"  # Ctrl+Tab
    assert filter_lockout_key_event(0xFF09, 0x08) == "block"  # Alt+Tab
    assert filter_lockout_key_event(0xFF09, 0x04 | 0x08) == "block"  # Ctrl+Alt+Tab
    assert filter_lockout_key_event(0xFF09, 0x04000000) == "block"  # Super+Tab

    # 3. Alt and Super shortcuts
    assert filter_lockout_key_event(ord('q'), 0x08) == "block"  # Alt+Q
    assert filter_lockout_key_event(ord('d'), 0x04000000) == "block"  # Super+D
    assert filter_lockout_key_event(ord('h'), 0x04000000) == "block"  # Super+H

    # 4. Function keys (F1 to F12)
    assert filter_lockout_key_event(0xFFBE, 0) == "block"  # F1
    assert filter_lockout_key_event(0xFFC1, 0x08) == "block"  # Alt+F4
    assert filter_lockout_key_event(0xFFC9, 0) == "block"  # F12

    # 5. Control shortcuts
    # Safe text editing allowed
    assert filter_lockout_key_event(ord('a'), 0x04) == "pass"  # Ctrl+A
    assert filter_lockout_key_event(ord('c'), 0x04) == "pass"  # Ctrl+C
    assert filter_lockout_key_event(ord('v'), 0x04) == "pass"  # Ctrl+V
    assert filter_lockout_key_event(ord('x'), 0x04) == "pass"  # Ctrl+X
    assert filter_lockout_key_event(0xFF08, 0x04) == "pass"  # Ctrl+Backspace
    # Dangerous Ctrl shortcuts blocked
    assert filter_lockout_key_event(ord('q'), 0x04) == "block"  # Ctrl+Q
    assert filter_lockout_key_event(ord('t'), 0x04) == "block"  # Ctrl+T
    assert filter_lockout_key_event(ord('n'), 0x04) == "block"  # Ctrl+N
    assert filter_lockout_key_event(ord('w'), 0x04 | 0x08) == "block"  # Ctrl+Alt+W

    # 6. Standard typing characters
    assert filter_lockout_key_event(ord('p'), 0) == "pass"
    assert filter_lockout_key_event(ord('1'), 0) == "pass"
    assert filter_lockout_key_event(0xFF0D, 0) == "pass"  # Enter


def test_gnome_keybinding_suppressor():
    suppressor = GnomeKeybindingSuppressor()
    with patch("subprocess.run") as mock_run:
        # Mock get returning valid values
        def fake_run(cmd, *args, **kwargs):
            m = MagicMock()
            if cmd[1] == "get":
                m.returncode = 0
                m.stdout = "['<Alt>Tab']\n"
            else:
                m.returncode = 0
            return m

        mock_run.side_effect = fake_run

        with suppressor:
            assert suppressor._active is True
            assert len(suppressor._backup) > 0

        assert suppressor._active is False
        assert len(suppressor._backup) == 0
