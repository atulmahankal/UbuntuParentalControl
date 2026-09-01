"""Session management utilities for Ubuntu (logout, lock, termination)."""

import logging
import os
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def lock_screen(custom_command: Optional[str] = None) -> bool:
    """Lock the current desktop session."""
    if custom_command:
        try:
            subprocess.run(custom_command, shell=True, check=False)
            return True
        except Exception as e:
            logger.error(f"Failed to execute custom lock command: {e}")

    # Try loginctl lock-session
    session_id = os.environ.get("XDG_SESSION_ID")
    if session_id and shutil.which("loginctl"):
        try:
            res = subprocess.run(["loginctl", "lock-session", session_id], capture_output=True)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # Try gnome-screensaver-command -l
    if shutil.which("gnome-screensaver-command"):
        try:
            subprocess.run(["gnome-screensaver-command", "-l"], check=False)
            return True
        except Exception:
            pass

    # Try xdg-screensaver lock
    if shutil.which("xdg-screensaver"):
        try:
            subprocess.run(["xdg-screensaver", "lock"], check=False)
            return True
        except Exception:
            pass

    return False


def terminate_session(custom_command: Optional[str] = None) -> None:
    """Terminate / sign out the current user session."""
    logger.info("Executing session termination...")

    if custom_command:
        try:
            subprocess.run(custom_command, shell=True, check=False)
            return
        except Exception as e:
            logger.error(f"Failed to run custom logout command '{custom_command}': {e}")

    # 1. Try GNOME session quit
    if shutil.which("gnome-session-quit"):
        try:
            logger.info("Calling gnome-session-quit --logout --no-prompt")
            subprocess.run(["gnome-session-quit", "--logout", "--no-prompt"], check=False, timeout=5)
            return
        except Exception as e:
            logger.warning(f"gnome-session-quit failed: {e}")

    # 2. Try loginctl terminate-session
    session_id = os.environ.get("XDG_SESSION_ID")
    if session_id and shutil.which("loginctl"):
        try:
            logger.info(f"Calling loginctl terminate-session {session_id}")
            subprocess.run(["loginctl", "terminate-session", session_id], check=False, timeout=5)
            return
        except Exception as e:
            logger.warning(f"loginctl terminate-session failed: {e}")

    # 3. Try loginctl terminate-user
    user = os.environ.get("USER")
    if user and shutil.which("loginctl"):
        try:
            logger.info(f"Calling loginctl terminate-user {user}")
            subprocess.run(["loginctl", "terminate-user", user], check=False, timeout=5)
            return
        except Exception as e:
            logger.warning(f"loginctl terminate-user failed: {e}")

    # 4. Fallback: kill desktop session manager
    logger.warning("Falling back to pkill session")
    try:
        subprocess.run(["pkill", "-TERM", "-u", os.environ.get("USER", ""), "gnome-session"], check=False)
    except Exception:
        pass
