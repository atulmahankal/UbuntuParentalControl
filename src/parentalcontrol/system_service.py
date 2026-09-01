"""System-level service management and user desktop session interaction."""

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/parental-control.service")
SYSTEM_CONFIG_DIR = Path("/etc/parental-control")


@dataclass
class UserSession:
    """Represents an active logind user session."""
    session_id: str
    uid: int
    username: str
    seat: str
    session_type: str  # wayland, x11, tty, etc.
    state: str         # active, online, closing


def list_active_sessions() -> List[UserSession]:
    """Query loginctl to list all active user sessions."""
    if not shutil.which("loginctl"):
        return []

    try:
        res = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except Exception as e:
        logger.warning(f"Error querying loginctl list-sessions: {e}")
        return []

    sessions: List[UserSession] = []
    lines = res.stdout.strip().splitlines()

    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            sess_id = parts[0]
            try:
                uid = int(parts[1])
            except ValueError:
                continue
            username = parts[2]
            seat = parts[3] if len(parts) > 3 else ""
            
            # Fetch detailed session properties
            sess_info = _get_session_details(sess_id, uid, username, seat)
            if sess_info and sess_info.state in ("active", "online"):
                sessions.append(sess_info)

    return sessions


def _get_session_details(session_id: str, uid: int, username: str, seat: str) -> Optional[UserSession]:
    """Retrieve detailed session properties using loginctl show-session."""
    try:
        res = subprocess.run(
            ["loginctl", "show-session", session_id],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        props: Dict[str, str] = {}
        for line in res.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()

        sess_type = props.get("Type", "unknown")
        state = props.get("State", "unknown")
        user = props.get("Name", username)

        return UserSession(
            session_id=session_id,
            uid=uid,
            username=user,
            seat=seat or props.get("Seat", ""),
            session_type=sess_type,
            state=state,
        )
    except Exception:
        return UserSession(
            session_id=session_id,
            uid=uid,
            username=username,
            seat=seat,
            session_type="unknown",
            state="active",
        )


def _get_user_env(uid: int, username: str) -> Dict[str, str]:
    """Construct environment variables required to interact with a user's desktop display."""
    env = os.environ.copy()
    runtime_dir = f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    env["USER"] = username
    env["HOME"] = f"/home/{username}"

    # Wayland or X11 display detection
    if os.path.exists(f"{runtime_dir}/wayland-0"):
        env["WAYLAND_DISPLAY"] = "wayland-0"
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"

    return env


def run_in_user_session(
    uid: int,
    username: str,
    command: List[str],
    timeout: Optional[int] = None,
    async_proc: bool = False,
) -> Optional[subprocess.Popen]:
    """Execute a command (such as notify-send or zenity) inside a user's GUI session."""
    user_env = _get_user_env(uid, username)

    # Use sudo -u <user> or su if running as root
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        full_cmd = [
            "sudo",
            "-u",
            username,
            "env",
            f"XDG_RUNTIME_DIR={user_env['XDG_RUNTIME_DIR']}",
            f"DBUS_SESSION_BUS_ADDRESS={user_env['DBUS_SESSION_BUS_ADDRESS']}",
            f"DISPLAY={user_env.get('DISPLAY', ':0')}",
        ]
        if "WAYLAND_DISPLAY" in user_env:
            full_cmd.append(f"WAYLAND_DISPLAY={user_env['WAYLAND_DISPLAY']}")
        full_cmd.extend(command)
    else:
        full_cmd = command

    try:
        if async_proc:
            return subprocess.Popen(
                full_cmd,
                env=user_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        subprocess.run(full_cmd, env=user_env, timeout=timeout, capture_output=True, check=False)
        return None
    except Exception as e:
        logger.warning(f"Failed to execute command in user session ({username}): {e}")
        return None


def send_user_notification(
    uid: int,
    username: str,
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str = "dialog-warning",
    expire_time_ms: int = 10000,
) -> None:
    """Send desktop notification to a specific user's session."""
    cmd = [
        "notify-send",
        f"--urgency={urgency}",
        f"--expire-time={expire_time_ms}",
        f"--app-name=Parental Control",
        f"--icon={icon}",
        title,
        message,
    ]
    run_in_user_session(uid, username, cmd, timeout=5)


def show_user_warning_dialog(
    uid: int,
    username: str,
    title: str,
    text: str,
    timeout_seconds: Optional[int] = None,
) -> None:
    """Display modal warning dialog on the user's screen."""
    cmd = [
        "zenity",
        "--warning",
        f"--title={title}",
        f"--text={text}",
        "--width=480",
    ]
    if timeout_seconds:
        cmd.append(f"--timeout={timeout_seconds}")

    run_in_user_session(uid, username, cmd, timeout=(timeout_seconds + 5) if timeout_seconds else 60)


def show_user_countdown_dialog(
    uid: int,
    username: str,
    title: str,
    message_prefix: str,
    countdown_seconds: int = 15,
) -> None:
    """Show an animated countdown dialog on the user's screen."""
    cmd = [
        "zenity",
        "--progress",
        f"--title={title}",
        f"--text={message_prefix}\n\nTerminating in {countdown_seconds} seconds...",
        "--percentage=0",
        "--auto-close",
        "--no-cancel",
        "--width=480",
    ]

    proc = run_in_user_session(uid, username, cmd, async_proc=True)
    if not proc:
        import time
        time.sleep(countdown_seconds)
        return

    import time
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


def play_user_sound(uid: int, username: str, sound_name: str = "dialog-warning") -> None:
    """Play alert sound in user's audio session."""
    cmd = ["canberra-gtk-play", "-i", sound_name]
    run_in_user_session(uid, username, cmd, timeout=3)


def terminate_session_by_id_or_user(session_id: str, username: str) -> None:
    """Terminate the user session using systemd loginctl."""
    logger.info(f"Terminating session {session_id} for user '{username}'...")
    if shutil.which("loginctl"):
        try:
            subprocess.run(["loginctl", "terminate-session", session_id], check=False, timeout=5)
        except Exception as e:
            logger.warning(f"Failed loginctl terminate-session {session_id}: {e}")
        try:
            subprocess.run(["loginctl", "terminate-user", username], check=False, timeout=5)
        except Exception as e:
            logger.warning(f"Failed loginctl terminate-user {username}: {e}")


def generate_systemd_service_content(exec_path: str) -> str:
    """Generate systemd service file content for parental-control.service."""
    return f"""[Unit]
Description=Parental Control Google Sheets Schedule Enforcer & Session Guard
Documentation=https://github.com/atulmahankal/ParentalControl
After=network.target network-online.target systemd-logind.service
Wants=network-online.target systemd-logind.service

[Service]
Type=simple
User=root
Group=root
ExecStart={exec_path} run-service --config /etc/parental-control/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
KillMode=process

[Install]
WantedBy=multi-user.target
"""


def install_system_service(exec_path: Optional[str] = None) -> Path:
    """Install and enable systemd service."""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("Installing as a system service requires root privileges. Please run with sudo.")

    # Determine executable path
    cmd = exec_path
    if not cmd:
        venv_bin = Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "parentalcontrol"
        if venv_bin.exists():
            cmd = str(venv_bin)
        else:
            which_bin = shutil.which("parentalcontrol")
            cmd = which_bin or "/usr/local/bin/parentalcontrol"

    content = generate_systemd_service_content(cmd)
    with open(SYSTEMD_SERVICE_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(SYSTEMD_SERVICE_PATH, 0o644)

    # Ensure /etc/parental-control directory exists
    SYSTEM_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Reload systemd and enable/start service
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "parental-control.service"], check=True)

    logger.info("Parental Control system service successfully installed and started.")
    return SYSTEMD_SERVICE_PATH


def uninstall_system_service() -> bool:
    """Disable and remove systemd service."""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError("Uninstalling the system service requires root privileges. Please run with sudo.")

    try:
        subprocess.run(["systemctl", "stop", "parental-control.service"], check=False)
        subprocess.run(["systemctl", "disable", "parental-control.service"], check=False)
    except Exception:
        pass

    if SYSTEMD_SERVICE_PATH.exists():
        SYSTEMD_SERVICE_PATH.unlink()
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        return True

    return False
