"""Always-On-Top Lockout Screen with Parent Password Override and Work Protection."""

import argparse
import logging
import os
import sys
import time
from typing import List, Optional

# Ensure system PyGObject is discoverable even in clean uv venv
if "/usr/lib/python3/dist-packages" not in sys.path:
    sys.path.append("/usr/lib/python3/dist-packages")

logger = logging.getLogger(__name__)

EXIT_UNLOCKED = 0
EXIT_ERROR = 1
EXIT_LOGOUT = 2


def run_lockout_screen(
    child_user: str,
    exempt_users: List[str],
    reason: str = "Your permitted screen time for this session is over.",
    next_session_info: Optional[str] = None,
    session_id: Optional[str] = None,
    testing_mode: bool = False,
) -> int:
    """Launch the GTK3 fullscreen lockout overlay."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk, GLib, Pango
    except Exception as e:
        logger.error(f"GTK3 is not available: {e}")
        return _run_cli_fallback(child_user, exempt_users, reason, next_session_info)

    # Initialize GTK
    if not Gtk.init_check()[0]:
        logger.warning("Cannot initialize GTK display (headless session).")
        return _run_cli_fallback(child_user, exempt_users, reason, next_session_info)

    # Clean exempt users list: exclude system accounts like gdm, lightdm, sddm, root
    display_exempt = [
        u for u in exempt_users
        if u.lower().strip() not in ("gdm", "gdm3", "lightdm", "sddm", "daemon", "nobody", "*", "all")
    ]
    if not display_exempt:
        display_exempt = ["atul", "root"]

    exit_code = [EXIT_LOGOUT]

    # Create Window
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("Parental Control - Access Restricted")
    window.set_decorated(False)
    window.set_keep_above(True)
    window.set_modal(True)
    window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)

    if not testing_mode:
        window.fullscreen()
    else:
        window.set_default_size(700, 620)

    # Block close / Alt+F4
    window.connect("delete-event", lambda *args: True)

    if testing_mode:
        # Allow Esc in test mode only
        def on_key(widget, event):
            if event.keyval == Gdk.KEY_Escape:
                exit_code[0] = EXIT_UNLOCKED
                Gtk.main_quit()
                return True
            return False
        window.connect("key-press-event", on_key)

    # Apply Modern CSS
    css_provider = Gtk.CssProvider()
    css = b"""
    window {
        background-color: rgba(18, 22, 32, 0.97);
    }
    .main-card {
        background-color: #242938;
        border: 1px solid #3b4256;
        border-radius: 16px;
        padding: 32px 40px;
    }
    .override-box {
        background-color: #1a1d29;
        border: 1px solid #2e3547;
        border-radius: 12px;
        padding: 20px 24px;
    }
    .title-label {
        color: #f1f5f9;
        font-size: 24px;
        font-weight: bold;
    }
    .subtitle-label {
        color: #94a3b8;
        font-size: 14px;
    }
    .safety-label {
        color: #38bdf8;
        font-size: 13px;
        font-weight: 500;
    }
    .session-info {
        color: #fbbf24;
        font-size: 14px;
        font-weight: 600;
    }
    .section-title {
        color: #e2e8f0;
        font-size: 15px;
        font-weight: bold;
    }
    .status-error {
        color: #f87171;
        font-size: 13px;
        font-weight: bold;
    }
    .status-success {
        color: #4ade80;
        font-size: 13px;
        font-weight: bold;
    }
    .btn-unlock {
        background: #10b981;
        color: #ffffff;
        font-weight: bold;
        padding: 10px 24px;
        border-radius: 8px;
        border: none;
    }
    .btn-unlock:hover {
        background: #059669;
    }
    .btn-logout {
        background: #ef4444;
        color: #ffffff;
        font-weight: bold;
        padding: 8px 20px;
        border-radius: 8px;
        border: none;
    }
    .btn-logout:hover {
        background: #dc2626;
    }
    """
    css_provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    # Root Box centering the card
    root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    root_box.set_valign(Gtk.Align.CENTER)
    root_box.set_halign(Gtk.Align.CENTER)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.get_style_context().add_class("main-card")
    card.set_size_request(580, -1)

    # 1. Header with Icon and Title
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    header_box.set_halign(Gtk.Align.CENTER)

    title_label = Gtk.Label(label="⏰ Screen Time Ended")
    title_label.get_style_context().add_class("title-label")
    header_box.pack_start(title_label, True, True, 0)
    card.pack_start(header_box, False, False, 0)

    # 2. Reason & Next Session Information
    reason_label = Gtk.Label(label=reason)
    reason_label.get_style_context().add_class("subtitle-label")
    reason_label.set_line_wrap(True)
    reason_label.set_justify(Gtk.Justification.CENTER)
    card.pack_start(reason_label, False, False, 0)

    if next_session_info:
        info_label = Gtk.Label(label=f"📅 {next_session_info}")
        info_label.get_style_context().add_class("session-info")
        card.pack_start(info_label, False, False, 0)

    # 3. Work Safety Assurance (Child's commands are protected)
    safety_label = Gtk.Label(
        label="🛡️ Background tasks, compiles, and downloads are running safely."
    )
    safety_label.get_style_context().add_class("safety-label")
    card.pack_start(safety_label, False, False, 4)

    # 4. Parent Override Box
    override_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    override_card.get_style_context().add_class("override-box")

    override_title = Gtk.Label(label="🔑 Parent / Admin Temporary Extension")
    override_title.get_style_context().add_class("section-title")
    override_title.set_halign(Gtk.Align.START)
    override_card.pack_start(override_title, False, False, 0)

    # Grid for options
    grid = Gtk.Grid()
    grid.set_column_spacing(16)
    grid.set_row_spacing(10)

    # Row 0: Parent User
    lbl_parent = Gtk.Label(label="Parent Account:")
    lbl_parent.set_halign(Gtk.Align.START)
    parent_combo = Gtk.ComboBoxText()
    for u in display_exempt:
        parent_combo.append_text(u)
    parent_combo.set_active(0)
    grid.attach(lbl_parent, 0, 0, 1, 1)
    grid.attach(parent_combo, 1, 0, 1, 1)

    # Row 1: Extension Duration
    lbl_dur = Gtk.Label(label="Grant Extra Time:")
    lbl_dur.set_halign(Gtk.Align.START)
    dur_combo = Gtk.ComboBoxText()
    dur_options = [
        ("15 Minutes", 15),
        ("30 Minutes (Recommended)", 30),
        ("45 Minutes", 45),
        ("1 Hour", 60),
        ("2 Hours", 120),
        ("Rest of Today", 720),
    ]
    for label, _ in dur_options:
        dur_combo.append_text(label)
    dur_combo.set_active(1)  # Default 30 min
    grid.attach(lbl_dur, 0, 1, 1, 1)
    grid.attach(dur_combo, 1, 1, 1, 1)

    # Row 2: Password Entry
    lbl_pwd = Gtk.Label(label="Parent Password:")
    lbl_pwd.set_halign(Gtk.Align.START)
    pwd_entry = Gtk.Entry()
    pwd_entry.set_visibility(False)
    pwd_entry.set_placeholder_text("Enter parent password")
    pwd_entry.set_hexpand(True)
    grid.attach(lbl_pwd, 0, 2, 1, 1)
    grid.attach(pwd_entry, 1, 2, 1, 1)

    override_card.pack_start(grid, False, False, 0)

    # Status Message Label
    status_label = Gtk.Label(label="")
    status_label.set_line_wrap(True)
    override_card.pack_start(status_label, False, False, 2)

    # Unlock Action
    def do_unlock(*args):
        parent_user = parent_combo.get_active_text() or "atul"
        selected_idx = dur_combo.get_active()
        duration = dur_options[selected_idx][1] if 0 <= selected_idx < len(dur_options) else 30
        password = pwd_entry.get_text()

        if not password:
            status_label.set_text("Please enter your parent password.")
            status_label.get_style_context().remove_class("status-success")
            status_label.get_style_context().add_class("status-error")
            return

        status_label.set_text("Verifying credentials...")
        status_label.get_style_context().remove_class("status-error")

        # Send request to daemon IPC
        success = False
        err_msg = "Could not authenticate."

        try:
            from parentalcontrol.ipc import send_ipc_request
            resp = send_ipc_request({
                "action": "authenticate_override",
                "child_user": child_user,
                "parent_user": parent_user,
                "password": password,
                "duration_minutes": duration,
            })
            if resp.get("success"):
                success = True
            else:
                err_msg = resp.get("error", "Authentication failed.")
        except Exception:
            # Fallback: direct PAM authentication
            try:
                import pam
                p = pam.pam()
                if p.authenticate(parent_user, password):
                    from parentalcontrol.override_manager import grant_temporary_override
                    grant_temporary_override(child_user, parent_user, duration)
                    success = True
                else:
                    err_msg = "Incorrect parent password. Please try again."
            except Exception as e:
                err_msg = f"Authentication error: {e}"

        if success:
            status_label.set_text(f"✅ Access approved for {duration} minutes! Resuming...")
            status_label.get_style_context().remove_class("status-error")
            status_label.get_style_context().add_class("status-success")
            pwd_entry.set_sensitive(False)
            btn_unlock.set_sensitive(False)

            # Play audio chime
            try:
                from parentalcontrol.notifier import play_alert_sound
                play_alert_sound("complete")
            except Exception:
                pass

            exit_code[0] = EXIT_UNLOCKED
            GLib.timeout_add(750, Gtk.main_quit)
        else:
            status_label.set_text(f"❌ {err_msg}")
            status_label.get_style_context().remove_class("status-success")
            status_label.get_style_context().add_class("status-error")
            pwd_entry.set_text("")
            pwd_entry.grab_focus()

    pwd_entry.connect("activate", do_unlock)

    btn_unlock = Gtk.Button(label="🟢 Unlock & Extend Time")
    btn_unlock.get_style_context().add_class("btn-unlock")
    btn_unlock.set_halign(Gtk.Align.CENTER)
    btn_unlock.connect("clicked", do_unlock)
    override_card.pack_start(btn_unlock, False, False, 4)

    card.pack_start(override_card, False, False, 0)

    # 5. Bottom Logout Action
    bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    bottom_box.set_halign(Gtk.Align.CENTER)

    btn_logout = Gtk.Button(label="🚪 Log Out Now")
    btn_logout.get_style_context().add_class("btn-logout")

    def do_logout(*args):
        exit_code[0] = EXIT_LOGOUT
        try:
            from parentalcontrol.ipc import send_ipc_request
            send_ipc_request({
                "action": "logout_request",
                "child_user": child_user,
                "session_id": session_id,
            })
        except Exception:
            pass
        Gtk.main_quit()

    btn_logout.connect("clicked", do_logout)
    bottom_box.pack_start(btn_logout, False, False, 0)
    card.pack_start(bottom_box, False, False, 0)

    root_box.pack_start(card, True, True, 0)
    window.add(root_box)

    window.show_all()
    pwd_entry.grab_focus()

    Gtk.main()
    window.destroy()
    return exit_code[0]


def _run_cli_fallback(
    child_user: str,
    exempt_users: List[str],
    reason: str,
    next_session_info: Optional[str],
) -> int:
    """Terminal fallback for environments without graphical desktop."""
    print("\n" + "=" * 60)
    print("⏰ PARENTAL CONTROL - SCREEN TIME ENDED")
    print(reason)
    if next_session_info:
        print(f"Next session: {next_session_info}")
    print("=" * 60)
    print("1. Log Out")
    print("2. Enter Parent Password to Extend")
    return EXIT_LOGOUT
