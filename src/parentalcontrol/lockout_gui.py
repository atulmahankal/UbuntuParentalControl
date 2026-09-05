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


class GnomeKeybindingSuppressor:
    """Temporarily suppresses GNOME window-switching and overview keybindings.

    Backs up original values and restores them upon exit, signal, or exception.
    """

    BASE_KEYS = [
        ("org.gnome.desktop.wm.keybindings", "switch-windows"),
        ("org.gnome.desktop.wm.keybindings", "switch-windows-backward"),
        ("org.gnome.desktop.wm.keybindings", "switch-applications"),
        ("org.gnome.desktop.wm.keybindings", "switch-applications-backward"),
        ("org.gnome.desktop.wm.keybindings", "switch-panels"),
        ("org.gnome.desktop.wm.keybindings", "switch-panels-backward"),
        ("org.gnome.desktop.wm.keybindings", "switch-group"),
        ("org.gnome.desktop.wm.keybindings", "switch-group-backward"),
        ("org.gnome.desktop.wm.keybindings", "cycle-windows"),
        ("org.gnome.desktop.wm.keybindings", "cycle-windows-backward"),
        ("org.gnome.desktop.wm.keybindings", "cycle-panels"),
        ("org.gnome.desktop.wm.keybindings", "cycle-panels-backward"),
        ("org.gnome.desktop.wm.keybindings", "cycle-group"),
        ("org.gnome.desktop.wm.keybindings", "cycle-group-backward"),
        ("org.gnome.desktop.wm.keybindings", "panel-run-dialog"),
        ("org.gnome.desktop.wm.keybindings", "show-desktop"),
        ("org.gnome.desktop.wm.keybindings", "activate-window-menu"),
        ("org.gnome.desktop.wm.keybindings", "minimize"),
        ("org.gnome.desktop.wm.keybindings", "toggle-maximized"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-left"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-right"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-up"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-down"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-1"),
        ("org.gnome.desktop.wm.keybindings", "switch-to-workspace-last"),
        ("org.gnome.mutter", "overlay-key"),
        ("org.gnome.shell.keybindings", "toggle-overview"),
        ("org.gnome.shell.keybindings", "toggle-application-view"),
        ("org.gnome.shell.keybindings", "toggle-quick-settings"),
        ("org.gnome.shell.keybindings", "toggle-message-tray"),
        ("org.gnome.shell.keybindings", "focus-active-notification"),
    ]

    def __init__(self):
        self._backup = {}
        self._active = False
        self._target_keys = list(self.BASE_KEYS)
        for i in range(1, 10):
            self._target_keys.append(("org.gnome.shell.keybindings", f"switch-to-application-{i}"))
            self._target_keys.append(("org.gnome.shell.keybindings", f"open-new-window-application-{i}"))
        for i in range(1, 11):
            self._target_keys.append(("org.gnome.shell.extensions.dash-to-dock", f"app-hotkey-{i}"))
            self._target_keys.append(("org.gnome.shell.extensions.dash-to-dock", f"app-ctrl-hotkey-{i}"))
            self._target_keys.append(("org.gnome.shell.extensions.dash-to-dock", f"app-shift-hotkey-{i}"))

    def __enter__(self):
        self.suppress()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.restore()

    def suppress(self):
        if self._active:
            return
        import subprocess
        for schema, key in self._target_keys:
            try:
                res = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=1)
                if res.returncode == 0:
                    val = res.stdout.strip()
                    self._backup[(schema, key)] = val
                    empty_val = "''" if key == "overlay-key" else "[]"
                    subprocess.run(["gsettings", "set", schema, key, empty_val], capture_output=True, timeout=1)
            except Exception:
                pass
        self._active = True
        logger.info(f"Suppressed {len(self._backup)} GNOME keybindings for lockout overlay.")

    def restore(self):
        if not self._active:
            return
        import subprocess
        for (schema, key), val in self._backup.items():
            try:
                subprocess.run(["gsettings", "set", schema, key, val], capture_output=True, timeout=1)
            except Exception:
                pass
        self._backup.clear()
        self._active = False
        logger.info("Restored desktop window-switching keybindings.")


def filter_lockout_key_event(event_keyval: int, event_state: int, testing_mode: bool = False) -> str:
    """Evaluate key event security. Returns 'exit', 'block', or 'pass'."""
    # Escape in test mode
    if testing_mode and event_keyval == 0xFF1B:  # Gdk.KEY_Escape
        return "exit"

    # Tab with any modifier (Ctrl+Tab, Alt+Tab, Super+Tab, etc.) -> BLOCK
    # Gdk.KEY_Tab = 0xFF09, KEY_ISO_Left_Tab = 0xFE20, KEY_KP_Tab = 0xFF89
    if event_keyval in (0xFF09, 0xFE20, 0xFF89):
        # 0x01 = SHIFT_MASK, 0x04 = CONTROL_MASK, 0x08 = MOD1_MASK (Alt), 0x04000000 = SUPER_MASK
        if event_state & (0x04 | 0x08 | 0x04000000):
            return "block"
        return "pass"

    # Block all Alt and Super combinations
    if event_state & (0x08 | 0x04000000):
        return "block"

    # Block function keys F1-F12 (0xFFBE to 0xFFC9)
    if 0xFFBE <= event_keyval <= 0xFFC9:
        return "block"

    # Filter Control combinations: allow only safe text editing
    if event_state & 0x04:  # CONTROL_MASK
        # Allowed: a, c, v, x, z, u, w, BackSpace (0xFF08), Delete (0xFFFF), Left/Right/Home/End
        allowed_keys = {
            ord('a'), ord('A'),
            ord('c'), ord('C'),
            ord('v'), ord('V'),
            ord('x'), ord('X'),
            ord('z'), ord('Z'),
            ord('u'), ord('U'),
            ord('w'), ord('W'),
            0xFF08,  # BackSpace
            0xFFFF,  # Delete
            0xFF51,  # Left
            0xFF53,  # Right
            0xFF50,  # Home
            0xFF57,  # End
        }
        if event_keyval in allowed_keys:
            return "pass"
        return "block"

    return "pass"


def run_lockout_screen(
    child_user: str,
    exempt_users: List[str],
    reason: str = "Your permitted screen time for this session is over.",
    next_session_info: Optional[str] = None,
    session_id: Optional[str] = None,
    testing_mode: bool = False,
) -> int:
    """Launch the GTK3 fullscreen lockout overlay with global device grab."""
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

    # Clean exempt users list: exclude system accounts, prefer human parent accounts (e.g. atul)
    exclude_users = {"gdm", "gdm3", "lightdm", "sddm", "daemon", "nobody", "root", "*", "all"}
    human_exempt = [
        u.strip() for u in exempt_users
        if u.lower().strip() not in exclude_users and u.strip()
    ]
    if not human_exempt:
        # Fallback to current sudo or login user
        current_login = os.environ.get("SUDO_USER") or os.environ.get("USER") or "atul"
        display_exempt = [current_login]
    else:
        display_exempt = human_exempt

    # Suppress desktop window-switching keybindings during lockout
    import atexit
    suppressor = GnomeKeybindingSuppressor()
    suppressor.suppress()
    atexit.register(suppressor.restore)

    exit_code = [EXIT_LOGOUT]
    backdrop_windows = []
    pwd_ref = [None]

    # Create Main Window
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_title("Parental Control - Access Restricted")
    window.set_decorated(False)
    window.set_keep_above(True)
    window.set_modal(True)
    window.stick()
    window.set_urgency_hint(True)
    window.set_position(Gtk.WindowPosition.CENTER_ALWAYS)

    # Find primary monitor and fullscreen
    disp = Gdk.Display.get_default()
    primary_idx = 0
    if disp:
        for i in range(disp.get_n_monitors()):
            if disp.get_monitor(i).is_primary():
                primary_idx = i
                break
        window.fullscreen_on_monitor(window.get_screen(), primary_idx)
    else:
        window.fullscreen()

    # Block Alt+F4 / window closing
    window.connect("delete-event", lambda *args: True)

    # Clicking background refocuses password entry
    window.connect(
        "button-press-event",
        lambda w, e: (pwd_ref[0].grab_focus() if pwd_ref[0] and pwd_ref[0].is_sensitive() else None, False)[1]
    )

    # Device Grab (captures pointer and keyboard events)
    def grab_devices(widget):
        gdk_win = widget.get_window()
        if gdk_win:
            disp_dev = Gdk.Display.get_default()
            if disp_dev:
                seat = disp_dev.get_default_seat()
                if seat:
                    try:
                        seat.grab(gdk_win, Gdk.SeatCapabilities.ALL, True, None, None, None)
                    except Exception as e:
                        logger.warning(f"Could not grab seat: {e}")

    def ungrab_devices(widget=None):
        disp_dev = Gdk.Display.get_default()
        if disp_dev:
            seat = disp_dev.get_default_seat()
            if seat:
                try:
                    seat.ungrab()
                except Exception:
                    pass

    window.connect("map-event", lambda w, e: (grab_devices(w), False)[1])
    window.connect("unmap-event", lambda w, e: (ungrab_devices(w), False)[1])
    window.connect("destroy", lambda w: ungrab_devices())

    # Focus watchdog: re-assert focus if desktop manager tries to focus another window
    def on_focus_out(widget, event):
        def reassert():
            window.fullscreen()
            window.set_keep_above(True)
            window.present()
            if pwd_ref[0] and pwd_ref[0].is_sensitive():
                pwd_ref[0].grab_focus()
            return False
        GLib.idle_add(reassert)
        return False

    window.connect("focus-out-event", on_focus_out)

    def enforce_top():
        if not window.is_active() or not window.has_toplevel_focus():
            window.set_keep_above(True)
            window.present()
            if pwd_ref[0] and pwd_ref[0].is_sensitive() and not pwd_ref[0].is_focus():
                pwd_ref[0].grab_focus()
        return True

    GLib.timeout_add(200, enforce_top)

    # Keyboard handling
    def on_key(widget, event):
        action = filter_lockout_key_event(event.keyval, int(event.state), testing_mode)
        if action == "exit":
            exit_code[0] = EXIT_UNLOCKED
            ungrab_devices()
            Gtk.main_quit()
            return True
        elif action == "block":
            return True
        return False

    window.connect("key-press-event", on_key)

    # High-Contrast Modern CSS
    css_provider = Gtk.CssProvider()
    css = b"""
    window {
        background-color: rgba(15, 23, 42, 0.98);
    }
    .main-card {
        background-color: #1e293b;
        border: 2px solid #475569;
        border-radius: 16px;
        padding: 32px 40px;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
    }
    .test-banner {
        background-color: #f59e0b;
        color: #0f172a;
        font-weight: bold;
        font-size: 13px;
        border-radius: 6px;
        padding: 6px 14px;
    }
    .override-box {
        background-color: #0f172a;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 20px 24px;
    }
    .title-label {
        color: #ffffff;
        font-size: 26px;
        font-weight: bold;
    }
    .subtitle-label {
        color: #cbd5e1;
        font-size: 14px;
    }
    .safety-label {
        color: #38bdf8;
        font-size: 13px;
        font-weight: 600;
    }
    .session-info {
        color: #fbbf24;
        font-size: 15px;
        font-weight: bold;
    }
    .section-title {
        color: #f8fafc;
        font-size: 16px;
        font-weight: bold;
    }
    .field-label {
        color: #f8fafc;
        font-size: 14px;
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
    entry {
        background-color: #ffffff;
        color: #0f172a;
        border: 2px solid #94a3b8;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 14px;
        font-weight: 500;
    }
    entry:focus {
        border-color: #38bdf8;
    }
    combobox button {
        background-color: #ffffff;
        color: #0f172a;
        border: 2px solid #94a3b8;
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 14px;
        font-weight: 500;
    }
    .btn-unlock {
        background: #10b981;
        color: #ffffff;
        font-weight: bold;
        font-size: 14px;
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
    .btn-test-exit {
        background: #475569;
        color: #ffffff;
        font-weight: bold;
        padding: 8px 16px;
        border-radius: 8px;
        border: none;
    }
    .btn-test-exit:hover {
        background: #334155;
    }
    """
    css_provider.load_from_data(css)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    # Root Box centering the card on the fullscreen dark backdrop
    root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    root_box.set_valign(Gtk.Align.CENTER)
    root_box.set_halign(Gtk.Align.CENTER)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    card.get_style_context().add_class("main-card")
    card.set_size_request(600, -1)

    # 0. Testing Mode Banner if active
    if testing_mode:
        banner = Gtk.Label(label="🧪 TEST MODE: All windows blocked. Press Esc to exit.")
        banner.get_style_context().add_class("test-banner")
        card.pack_start(banner, False, False, 0)

    # 1. Header with Title
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
    override_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    override_card.get_style_context().add_class("override-box")

    override_title = Gtk.Label(label="🔑 Parent / Admin Temporary Extension")
    override_title.get_style_context().add_class("section-title")
    override_title.set_halign(Gtk.Align.START)
    override_card.pack_start(override_title, False, False, 0)

    # Grid for options
    grid = Gtk.Grid()
    grid.set_column_spacing(20)
    grid.set_row_spacing(12)

    # Row 0: Parent User
    lbl_parent = Gtk.Label(label="Parent Account:")
    lbl_parent.get_style_context().add_class("field-label")
    lbl_parent.set_halign(Gtk.Align.START)
    parent_combo = Gtk.ComboBoxText()
    for u in display_exempt:
        parent_combo.append_text(u)
    parent_combo.set_active(0)
    grid.attach(lbl_parent, 0, 0, 1, 1)
    grid.attach(parent_combo, 1, 0, 1, 1)

    # Row 1: Extension Duration
    lbl_dur = Gtk.Label(label="Grant Extra Time:")
    lbl_dur.get_style_context().add_class("field-label")
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
    lbl_pwd.get_style_context().add_class("field-label")
    lbl_pwd.set_halign(Gtk.Align.START)
    pwd_entry = Gtk.Entry()
    pwd_entry.set_visibility(False)
    pwd_entry.set_placeholder_text("Enter parent password")
    pwd_entry.set_hexpand(True)
    pwd_ref[0] = pwd_entry
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
            ungrab_devices()
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

    # 5. Bottom Actions (Logout & Test Mode Exit)
    bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    bottom_box.set_halign(Gtk.Align.CENTER)

    btn_logout = Gtk.Button(label="🚪 Log Out Now")
    btn_logout.get_style_context().add_class("btn-logout")

    def do_logout(*args):
        exit_code[0] = EXIT_LOGOUT
        ungrab_devices()
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

    if testing_mode:
        btn_exit_test = Gtk.Button(label="❌ Exit Test (Esc)")
        btn_exit_test.get_style_context().add_class("btn-test-exit")
        def do_exit_test(*args):
            exit_code[0] = EXIT_UNLOCKED
            ungrab_devices()
            Gtk.main_quit()
        btn_exit_test.connect("clicked", do_exit_test)
        bottom_box.pack_start(btn_exit_test, False, False, 0)

    card.pack_start(bottom_box, False, False, 0)

    root_box.pack_start(card, True, True, 0)
    window.add(root_box)

    # Multi-monitor backdrop coverage (cover all non-primary monitors)
    disp = Gdk.Display.get_default()
    if disp and disp.get_n_monitors() > 1:
        for i in range(disp.get_n_monitors()):
            if i != primary_idx:
                try:
                    b_win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
                    b_win.set_decorated(False)
                    b_win.set_keep_above(True)
                    b_win.stick()
                    b_win.fullscreen_on_monitor(window.get_screen(), i)
                    b_win.connect("delete-event", lambda *args: True)
                    b_win.connect("button-press-event", lambda *args: (window.present(), True)[1])
                    backdrop_windows.append(b_win)
                    b_win.show_all()
                except Exception as e:
                    logger.warning(f"Could not create backdrop on monitor {i}: {e}")

    window.show_all()
    pwd_entry.grab_focus()

    try:
        Gtk.main()
    finally:
        suppressor.restore()
        ungrab_devices()
        for bw in backdrop_windows:
            try:
                bw.destroy()
            except Exception:
                pass
        try:
            window.destroy()
        except Exception:
            pass

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
