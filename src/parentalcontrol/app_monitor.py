"""Application and standalone binary process scanner for Parental Control."""

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from parentalcontrol.models import AppLimitRule

logger = logging.getLogger(__name__)

# Essential system/desktop binaries that should never be monitored or terminated
SYSTEM_EXCLUDED_BINARIES: Set[str] = {
    "systemd",
    "(sd-pam)",
    "gnome-shell",
    "gnome-session-b",
    "gnome-session-binary",
    "gnome-session-c",
    "gnome-session-failed",
    "Xwayland",
    "Xorg",
    "dbus-daemon",
    "dbus-broker",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "pulseaudio",
    "gvfsd",
    "gvfsd-fuse",
    "gvfsd-metadata",
    "gvfsd-trash",
    "ibus-daemon",
    "ibus-dconf",
    "ibus-extension-",
    "ibus-portal",
    "at-spi-bus-laun",
    "at-spi2-registryd",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-gtk",
    "xdg-document-portal",
    "xdg-permission-store",
    "gjs",
    "mutter-x11-frames",
    "parentalcontrol",
    "zenity",
    "notify-send",
    "canberra-gtk-play",
    "bash",
    "sh",
    "systemd-user",
    "sd-pam",
    "ssh-agent",
    "gpg-agent",
}


@dataclass
class ProcessInfo:
    """Represents a running process inspected from /proc."""
    pid: int
    uid: int
    name: str                           # Short process name (comm)
    exe: str                            # Canonical executable path (/proc/<pid>/exe)
    cmdline: List[str] = field(default_factory=list)  # Arguments (/proc/<pid>/cmdline)
    appimage_path: Optional[str] = None # Original AppImage path if running from squashfs mount


def get_process_info(pid: int) -> Optional[ProcessInfo]:
    """Inspect /proc/<pid> and return ProcessInfo if accessible."""
    proc_dir = Path(f"/proc/{pid}")
    if not proc_dir.exists():
        return None

    try:
        st = proc_dir.stat()
        uid = st.st_uid
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None

    # Read comm (process name)
    try:
        with open(proc_dir / "comm", "r", encoding="utf-8", errors="replace") as f:
            name = f.read().strip()
    except Exception:
        name = ""

    # Read canonical exe link
    try:
        exe = os.readlink(str(proc_dir / "exe"))
    except Exception:
        exe = ""

    # Read cmdline arguments
    cmdline: List[str] = []
    try:
        with open(proc_dir / "cmdline", "rb") as f:
            raw_cmd = f.read()
            parts = raw_cmd.split(b"\x00")
            cmdline = [p.decode("utf-8", errors="replace").strip() for p in parts if p]
    except Exception:
        pass

    # Detect AppImage (AppImages mount under /tmp/.mount_* or /var/tmp/.mount_*)
    appimage_path: Optional[str] = None
    if ".mount_" in exe:
        # Check /proc/<pid>/environ for APPIMAGE environment variable
        try:
            with open(proc_dir / "environ", "rb") as f:
                env_raw = f.read()
                for item in env_raw.split(b"\x00"):
                    if item.startswith(b"APPIMAGE="):
                        appimage_path = item[len(b"APPIMAGE="):].decode("utf-8", errors="replace").strip()
                        break
        except Exception:
            pass

    # Fallback AppImage detection in cmdline
    if not appimage_path:
        for arg in cmdline:
            if arg.endswith(".AppImage") and os.path.exists(arg):
                appimage_path = arg
                break

    return ProcessInfo(
        pid=pid,
        uid=uid,
        name=name,
        exe=exe,
        cmdline=cmdline,
        appimage_path=appimage_path,
    )


def scan_user_processes(uid: int) -> List[ProcessInfo]:
    """Scan all processes in /proc belonging to the given user UID."""
    results: List[ProcessInfo] = []
    proc_root = Path("/proc")

    try:
        entries = os.listdir(proc_root)
    except Exception as e:
        logger.warning(f"Failed to list /proc: {e}")
        return results

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            proc = get_process_info(pid)
            if proc and proc.uid == uid:
                results.append(proc)
        except Exception:
            continue

    return results


def is_system_process(proc: ProcessInfo) -> bool:
    """Check if process is part of core desktop infrastructure."""
    if proc.name in SYSTEM_EXCLUDED_BINARIES:
        return True
    exe_name = os.path.basename(proc.exe).lower()
    if exe_name in SYSTEM_EXCLUDED_BINARIES:
        return True
    if any(s in proc.exe for s in ("/systemd/", "/pipewire", "/wireplumber", "/dbus")):
        return True
    return False


def matches_process(proc: ProcessInfo, rule: AppLimitRule) -> bool:
    """Check if a running process matches an AppLimitRule.
    
    Supports:
      - Process name (comm, e.g. 'chrome', 'steam', 'game.x86_64')
      - Executable basename (e.g. 'google-chrome', 'discord')
      - Executable path globs (e.g. '*/Downloads/*', '*/Desktop/*', '*.AppImage')
      - AppImage source path
      - Commandline arguments (e.g. for python3/java scripts)
    """
    if is_system_process(proc):
        return False

    patterns = [p.strip().lower() for p in rule.patterns if p.strip()]
    if not patterns and rule.app_name:
        patterns = [rule.app_name.strip().lower()]

    proc_name = proc.name.lower().strip()
    proc_exe = proc.exe.lower().strip()
    exe_basename = os.path.basename(proc.exe).lower().strip()
    appimage = (proc.appimage_path or "").lower().strip()
    appimage_basename = os.path.basename(appimage) if appimage else ""
    cmdline_str = " ".join(proc.cmdline).lower()

    for pat in patterns:
        # 1. Exact or glob match on process name (comm)
        if proc_name == pat or fnmatch.fnmatch(proc_name, pat):
            return True

        # 2. Match on executable basename (e.g. "google-chrome", "blender")
        if exe_basename == pat or fnmatch.fnmatch(exe_basename, pat):
            return True

        # 3. Match on full canonical executable path (e.g. "*/downloads/*", "/opt/google/chrome/*")
        if fnmatch.fnmatch(proc_exe, pat):
            return True

        # 4. Match on AppImage path or basename (e.g. "*.AppImage", "*kdenlive*")
        if appimage:
            if fnmatch.fnmatch(appimage, pat) or fnmatch.fnmatch(appimage_basename, pat):
                return True

        # 5. Match within command line arguments (e.g. script name "minecraft.jar" or "game.py")
        if any(fnmatch.fnmatch(arg.lower(), pat) for arg in proc.cmdline):
            return True
        if pat in cmdline_str:
            return True

    return False
