"""Desktop notification and UI prompts for Ubuntu."""

import logging
import os
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


def is_gui_available() -> bool:
    """Check if X11 or Wayland display session is active."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str = "dialog-warning",
    expire_time_ms: int = 10000,
) -> bool:
    """Send Ubuntu desktop notification using notify-send."""
    if not is_gui_available() and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        logger.info(f"[Notification] {title}: {message}")
        return False

    cmd = [
        "notify-send",
        f"--urgency={urgency}",
        f"--expire-time={expire_time_ms}",
        f"--app-name=Parental Control",
        f"--icon={icon}",
        title,
        message,
    ]

    try:
        subprocess.run(cmd, check=False, timeout=5, capture_output=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to send notification via notify-send: {e}")
        return False


def play_alert_sound(sound_name: str = "dialog-warning") -> bool:
    """Play desktop alert chime using native sound tools."""
    # 1. Try canberra-gtk-play (standard GNOME sound player)
    if shutil.which("canberra-gtk-play"):
        try:
            subprocess.Popen(
                ["canberra-gtk-play", "-i", sound_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass

    # 2. Try pw-play / paplay / aplay with standard sound file
    sound_paths = [
        f"/usr/share/sounds/freedesktop/stereo/{sound_name}.oga",
        "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
        "/usr/share/sounds/freedesktop/stereo/bell.oga",
        "/usr/share/sounds/freedesktop/stereo/complete.oga",
    ]

    for p in sound_paths:
        if os.path.exists(p):
            for player in ["pw-play", "paplay", "aplay"]:
                if shutil.which(player):
                    try:
                        subprocess.Popen(
                            [player, p],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return True
                    except Exception:
                        pass

    # Fallback to terminal bell
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass
    return False


def show_warning_dialog(
    title: str,
    text: str,
    timeout_seconds: Optional[int] = None,
    width: int = 480,
) -> bool:
    """Show modal warning popup using Zenity."""
    if not shutil.which("zenity"):
        logger.warning(f"[DIALOG] {title}: {text}")
        return False

    cmd = [
        "zenity",
        "--warning",
        f"--title={title}",
        f"--text={text}",
        f"--width={width}",
    ]
    if timeout_seconds:
        cmd.append(f"--timeout={timeout_seconds}")

    try:
        subprocess.run(cmd, timeout=(timeout_seconds + 5) if timeout_seconds else 60, capture_output=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to display warning dialog: {e}")
        return False


def show_info_dialog(
    title: str,
    text: str,
    timeout_seconds: Optional[int] = None,
    width: int = 450,
) -> bool:
    """Show info popup using Zenity."""
    if not shutil.which("zenity"):
        logger.info(f"[DIALOG] {title}: {text}")
        return False

    cmd = [
        "zenity",
        "--info",
        f"--title={title}",
        f"--text={text}",
        f"--width={width}",
    ]
    if timeout_seconds:
        cmd.append(f"--timeout={timeout_seconds}")

    try:
        subprocess.run(cmd, timeout=(timeout_seconds + 5) if timeout_seconds else 60, capture_output=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to display info dialog: {e}")
        return False


def show_countdown_dialog(
    title: str,
    message_prefix: str,
    countdown_seconds: int = 15,
) -> None:
    """Display an animated countdown bar using Zenity while counting down."""
    if not shutil.which("zenity") or not is_gui_available():
        print(f"\n{title}\n{message_prefix}")
        for s in range(countdown_seconds, 0, -1):
            print(f"Terminating in {s}s...", end="\r", flush=True)
            time.sleep(1)
        print()
        return

    # Start Zenity progress dialog
    cmd = [
        "zenity",
        "--progress",
        f"--title={title}",
        f"--text={message_prefix}\n\nTime remaining: {countdown_seconds}s",
        "--percentage=0",
        "--auto-close",
        "--no-cancel",
        "--width=450",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        for sec_left in range(countdown_seconds, 0, -1):
            if proc.poll() is not None:
                break
            pct = int(((countdown_seconds - sec_left) / countdown_seconds) * 100)
            try:
                if proc.stdin:
                    proc.stdin.write(f"{pct}\n")
                    proc.stdin.write(f"# {message_prefix}\n\nTerminating in {sec_left} seconds...\n")
                    proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            time.sleep(1)

        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("100\n# Terminating session now...\n")
                    proc.stdin.flush()
            except Exception:
                pass
            time.sleep(0.5)
            proc.terminate()
    except Exception as e:
        logger.warning(f"Failed to run countdown dialog: {e}")
        time.sleep(countdown_seconds)
