from unittest.mock import patch, MagicMock
from parentalcontrol.lockout_gui import run_lockout_screen, EXIT_UNLOCKED, EXIT_LOGOUT

def test_lockout_cli_fallback(monkeypatch):
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
