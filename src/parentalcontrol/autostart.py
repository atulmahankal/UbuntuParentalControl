"""Autostart management for Ubuntu desktop environments."""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def get_executable_path() -> str:
    """Find the path to parentalcontrol binary or python runner."""
    # Check if inside a virtual environment
    venv_bin = Path(sys.prefix) / "bin" / "parentalcontrol"
    if venv_bin.exists():
        return str(venv_bin)

    which_bin = shutil.which("parentalcontrol")
    if which_bin:
        return which_bin

    # Fallback to uv run or python module
    return f"{sys.executable} -m parentalcontrol.cli"


def create_desktop_entry_content(exec_command: Optional[str] = None) -> str:
    """Generate .desktop file contents for XDG autostart."""
    cmd = exec_command or f"{get_executable_path()} monitor"
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Parental Control Guard
GenericName=Parental Control
Comment=Google Sheets schedule enforcement and screen time monitor
Exec={cmd}
Terminal=false
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Phase=Applications
"""


def install_user_autostart(
    target_user: Optional[str] = None,
    exec_command: Optional[str] = None,
) -> Path:
    """Install autostart entry in ~/.config/autostart/parental-control.desktop."""
    if target_user:
        home_dir = Path(f"/home/{target_user}")
        autostart_dir = home_dir / ".config" / "autostart"
    else:
        autostart_dir = Path.home() / ".config" / "autostart"

    autostart_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = autostart_dir / "parental-control.desktop"

    content = create_desktop_entry_content(exec_command)
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(desktop_file, 0o755)
    return desktop_file


def install_system_autostart(exec_command: Optional[str] = None) -> Path:
    """Install system-wide autostart in /etc/xdg/autostart/."""
    sys_dir = Path("/etc/xdg/autostart")
    sys_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = sys_dir / "parental-control.desktop"

    content = create_desktop_entry_content(exec_command)
    with open(desktop_file, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(desktop_file, 0o755)
    return desktop_file


def uninstall_autostart(target_user: Optional[str] = None) -> bool:
    """Remove desktop autostart entry."""
    removed = False
    if target_user:
        p = Path(f"/home/{target_user}") / ".config" / "autostart" / "parental-control.desktop"
        if p.exists():
            p.unlink()
            removed = True
    else:
        p = Path.home() / ".config" / "autostart" / "parental-control.desktop"
        if p.exists():
            p.unlink()
            removed = True

    sys_p = Path("/etc/xdg/autostart/parental-control.desktop")
    if sys_p.exists():
        try:
            sys_p.unlink()
            removed = True
        except PermissionError:
            pass

    return removed
