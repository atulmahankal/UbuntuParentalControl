"""Local IPC server and client over Unix Domain Socket for Parental Control."""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

PRIMARY_SOCKET_PATH = Path("/run/parental-control/daemon.sock")
FALLBACK_SOCKET_PATH = Path("/tmp/parental-control/daemon.sock")


def get_socket_path() -> Path:
    """Determine available socket path for IPC."""
    for p in [PRIMARY_SOCKET_PATH, FALLBACK_SOCKET_PATH]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.parent.chmod(0o777)
            except Exception:
                pass
            return p
        except Exception:
            continue
    return FALLBACK_SOCKET_PATH


class ParentalControlIPCServer:
    """Threaded Unix domain socket server for daemon IPC."""

    def __init__(
        self,
        exempt_users: list,
        socket_path: Optional[Path] = None,
        on_override: Optional[Callable[[str, str, int], None]] = None,
        on_logout: Optional[Callable[[Optional[str], str], None]] = None,
    ):
        self.exempt_users = [u.lower().strip() for u in exempt_users]
        self.socket_path = socket_path or get_socket_path()
        self.on_override = on_override
        self.on_logout = on_logout
        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Bind socket and begin accepting connections in background thread."""
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(str(self.socket_path))
        try:
            self.socket_path.chmod(0o666)
        except Exception:
            pass

        self._server_sock.listen(10)
        self._running = True
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        logger.info(f"IPC server listening on {self.socket_path}")

    def stop(self) -> None:
        """Stop server and clean up socket."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass

    def _serve_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except (OSError, ValueError):
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            try:
                raw = b""
                while not raw.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                if not raw:
                    return

                req = json.loads(raw.decode("utf-8").strip())
                res = self._dispatch(req)
                conn.sendall((json.dumps(res) + "\n").encode("utf-8"))
            except Exception as e:
                logger.error(f"IPC client handling error: {e}")
                try:
                    conn.sendall(json.dumps({"success": False, "error": str(e)}).encode("utf-8") + b"\n")
                except Exception:
                    pass

    def _dispatch(self, req: dict) -> dict:
        action = req.get("action")

        if action == "ping":
            return {"success": True, "message": "pong"}

        if action == "authenticate_override":
            child_user = req.get("child_user", "").strip()
            parent_user = req.get("parent_user", "").strip()
            password = req.get("password", "")
            duration = int(req.get("duration_minutes", 30))

            if not child_user or not parent_user or not password:
                return {"success": False, "error": "Missing required fields."}

            # Security check: parent_user must be in exempt_users or root/admin
            parent_lower = parent_user.lower()
            if parent_lower not in self.exempt_users and parent_lower not in ("root", "admin"):
                # Also check UID < 1000
                is_admin = False
                try:
                    import pwd
                    is_admin = pwd.getpwnam(parent_user).pw_uid < 1000
                except Exception:
                    pass
                if not is_admin:
                    return {
                        "success": False,
                        "error": f"User '{parent_user}' is not an authorized parent/admin account.",
                    }

            # Authenticate via PAM
            try:
                import pam
                p = pam.pam()
                auth_ok = p.authenticate(parent_user, password)
            except Exception as e:
                logger.error(f"PAM authentication call failed: {e}")
                auth_ok = False

            if not auth_ok:
                return {"success": False, "error": "Incorrect parent password. Please try again."}

            # Grant override
            from parentalcontrol.override_manager import grant_temporary_override
            record = grant_temporary_override(child_user, parent_user, duration)

            if self.on_override:
                try:
                    self.on_override(child_user, parent_user, duration)
                except Exception as e:
                    logger.warning(f"Error in on_override callback: {e}")

            return {
                "success": True,
                "message": f"Access approved for {duration} minutes.",
                "duration_minutes": duration,
                "expires_at": record.get("expires_at"),
            }

        if action == "logout_request":
            child_user = req.get("child_user", "")
            session_id = req.get("session_id")
            if self.on_logout:
                try:
                    self.on_logout(session_id, child_user)
                except Exception as e:
                    logger.warning(f"Error in on_logout callback: {e}")
            return {"success": True, "message": "Logout triggered."}

        return {"success": False, "error": f"Unknown action '{action}'"}


def send_ipc_request(req: dict, socket_path: Optional[Path] = None, timeout: float = 5.0) -> dict:
    """Send request to the daemon IPC socket and return parsed response."""
    paths_to_try = [socket_path] if socket_path else [PRIMARY_SOCKET_PATH, FALLBACK_SOCKET_PATH]
    sock = None
    for p in paths_to_try:
        if p and p.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout)
                s.connect(str(p))
                sock = s
                break
            except Exception:
                continue

    if not sock:
        raise ConnectionError("Could not connect to Parental Control daemon IPC socket.")

    with sock:
        payload = json.dumps(req) + "\n"
        sock.sendall(payload.encode("utf-8"))
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        if not raw:
            raise ValueError("Empty response from daemon IPC socket.")
        return json.loads(raw.decode("utf-8").strip())
