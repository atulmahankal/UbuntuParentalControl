"""Command-line interface for Parental Control."""

import argparse
import getpass
import os
import pwd
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from tabulate import tabulate

from parentalcontrol import __version__
from parentalcontrol.config import (
    AppConfig,
    load_config,
    save_config,
    get_default_config_path,
    SYSTEM_CONFIG_DIR,
)
from parentalcontrol.evaluator import evaluate_access
from parentalcontrol.sheet_client import GoogleSheetClient
from parentalcontrol.system_daemon import SystemParentalControlDaemon
from parentalcontrol.system_service import (
    install_system_service,
    uninstall_system_service,
    install_apt_upgrade_hook,
    install_launcher_wrapper,
    list_active_sessions,
)


def get_system_users(config: AppConfig) -> List[Dict[str, str]]:
    """Retrieve human user accounts (UID 1000-59999) from the system."""
    users = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and p.pw_shell not in ("/usr/sbin/nologin", "/bin/false"):
            is_targeted = config.is_user_targeted(p.pw_name)
            users.append({
                "username": p.pw_name,
                "uid": str(p.pw_uid),
                "fullname": p.pw_gecos.split(",")[0] if p.pw_gecos else p.pw_name,
                "status": "🛡️ Targeted (Monitored)" if is_targeted else "⭐ Exempt (Parent/Admin)",
                "is_targeted": is_targeted,
            })
    return sorted(users, key=lambda u: int(u["uid"]))


def cmd_list_users(args: argparse.Namespace, config: AppConfig) -> None:
    """List system user accounts to help configure Google Spreadsheet rules."""
    users = get_system_users(config)
    cur_dev = config.effective_device_name

    if args.csv:
        print("User,Device,Day,Start Time,End Time,Allowed,Max Minutes,Message")
        for u in users:
            if u["is_targeted"]:
                print(f"{u['username']},*,Monday-Friday,16:00,20:00,TRUE,120,Weekday homework & screen time")
                print(f"{u['username']},{cur_dev},Saturday-Sunday,10:00,13:00,TRUE,180,Weekend session on {cur_dev}")
        return

    print("\n================ UBUNTU USER ACCOUNTS ================")
    print("Copy these usernames into the 'User' column of your Google Spreadsheet:\n")

    table = [
        [u["username"], u["fullname"], u["uid"], u["status"]]
        for u in users
    ]
    headers = ["Username (for Sheet)", "Full Name", "UID", "Current Policy"]
    print(tabulate(table, headers=headers, tablefmt="fancy_grid"))

    print(f"\n💻 This Machine's Device Name: {cur_dev}")
    print("\n💡 Spreadsheet Tips:")
    print(f"  • In the 'Device' column, use '{cur_dev}' to restrict this specific computer.")
    print("  • Leave 'Device' empty, omit the column, or use '*' to apply rules to ALL devices.")
    print("  • Use '*' in the 'User' column to set default rules for all children.")
    print("  • To generate ready-to-copy CSV rows, run: parentalcontrol list-users --csv\n")


def cmd_run_service(args: argparse.Namespace, config: AppConfig) -> None:
    """Run the multi-session system daemon (invoked by systemd)."""
    daemon = SystemParentalControlDaemon(config=config)
    daemon.start()


def cmd_service_install(args: argparse.Namespace, config: AppConfig) -> None:
    """Install and activate parental-control systemd service."""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("❌ Error: Installing system service requires root privileges. Please run with sudo:")
        print(f"   sudo parentalcontrol service-install" + (f" --url '{args.url}'" if args.url else ""))
        sys.exit(1)

    if args.url:
        config.google_sheet.url = args.url
    if args.target_users:
        config.rules.target_users = [u.strip() for u in args.target_users.split(",") if u.strip()]
    if args.exempt_users:
        config.rules.exempt_users = [u.strip() for u in args.exempt_users.split(",") if u.strip()]

    if config.has_wildcard_target_users():
        print("❌ Error: 'target_users: [*]' is not permitted when installing the system service.")
        print("   Wildcards can match system display managers (such as GDM) and cause login lockout loops.")
        print("   Please specify explicit child usernames via --target-users (e.g. --target-users 'himanshu,himanshi').")
        sys.exit(1)

    # Save to /etc/parental-control/config.yaml
    sys_config_path = SYSTEM_CONFIG_DIR / "config.yaml"
    saved_path = save_config(config, sys_config_path)
    print(f"✅ System configuration saved to: {saved_path}")

    # Determine executable path
    venv_bin = Path(sys.prefix) / "bin" / "parentalcontrol"
    exec_path = str(venv_bin) if venv_bin.exists() else None

    try:
        service_file = install_system_service(exec_path=exec_path)
        print(f"✅ Systemd service installed at: {service_file}")
        print("✅ APT auto-upgrade hook installed at: /etc/apt/apt.conf.d/99parentalcontrol")
        print("✅ Systemd service enabled and started via 'systemctl enable --now parental-control.service'!")
        print("\nTo check service logs:")
        print("   sudo journalctl -u parental-control.service -f")
    except Exception as e:
        print(f"❌ Failed to install system service: {e}")
        sys.exit(1)


def cmd_service_uninstall(args: argparse.Namespace, config: AppConfig) -> None:
    """Uninstall and disable the systemd service."""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("❌ Error: Uninstalling system service requires root privileges. Please run with sudo:")
        print("   sudo parentalcontrol service-uninstall")
        sys.exit(1)

    try:
        removed = uninstall_system_service()
        if removed:
            print("✅ System service and APT upgrade hook removed successfully.")
        else:
            print("ℹ️ No system service file found.")
    except Exception as e:
        print(f"❌ Error uninstalling service: {e}")
        sys.exit(1)


def cmd_service_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Check system service status and view currently monitored user sessions."""
    print("\n================ SYSTEM SERVICE STATUS ================")
    try:
        res = subprocess.run(
            ["systemctl", "status", "parental-control.service", "--no-pager"],
            capture_output=True,
            text=True,
        )
        print(res.stdout if res.stdout else res.stderr)
    except Exception as e:
        print(f"Could not query systemctl: {e}")

    print("\n================ ACTIVE LOGIND SESSIONS ================")
    sessions = list_active_sessions()
    if sessions:
        table = [
            [
                s.session_id,
                s.username,
                s.uid,
                s.session_type,
                s.state,
                "🛡️ Monitored" if config.is_user_targeted(s.username) else "⭐ Exempt",
            ]
            for s in sessions
        ]
        headers = ["Session ID", "Username", "UID", "Type", "State", "Policy"]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))
    else:
        print("No active desktop sessions detected.")
    print()


def cmd_update(args: argparse.Namespace, config: AppConfig) -> None:
    """Upgrade application to the latest version from git and restart service."""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("❌ Error: Updating system service requires root privileges. Please run with sudo:")
        print("   sudo parentalcontrol update")
        sys.exit(1)

    quiet = getattr(args, "quiet", False)
    install_dir = Path("/opt/parental-control")
    if not install_dir.exists():
        install_dir = Path(__file__).resolve().parent.parent.parent

    if not quiet:
        print(f"🔄 Updating Parental Control in {install_dir}...")
    try:
        if (install_dir / ".git").exists():
            subprocess.run(["git", "-C", str(install_dir), "reset", "--hard", "HEAD", "--quiet"], check=False)
            subprocess.run(["git", "-C", str(install_dir), "pull", "--rebase", "--quiet"], check=True)
            if not quiet:
                print("✅ Git repository updated.")

        # Check if Python interpreter in .venv is broken (e.g. after Ubuntu upgrade)
        venv_python = install_dir / ".venv" / "bin" / "python3"
        recreate_venv = False
        if not venv_python.exists():
            recreate_venv = True
        else:
            try:
                test_proc = subprocess.run([str(venv_python), "--version"], capture_output=True, timeout=5)
                if test_proc.returncode != 0:
                    recreate_venv = True
            except Exception:
                recreate_venv = True

        # Find uv binary
        uv_bin = shutil.which("uv") or "/home/atul/.local/bin/uv" or "/root/.local/bin/uv"
        if os.path.exists(uv_bin):
            if recreate_venv:
                subprocess.run([uv_bin, "venv", "--clear", "--python", "/usr/bin/python3"], cwd=str(install_dir), check=False)
                if not quiet:
                    print("✅ Virtual environment repaired with system Python 3.")
            subprocess.run([uv_bin, "sync", "--quiet"], cwd=str(install_dir), check=False)
            if not quiet:
                print("✅ Dependencies synced with uv.")

        # Refresh APT upgrade hooks and resilient launcher wrapper
        install_apt_upgrade_hook()
        install_launcher_wrapper(install_dir)

        # Restart systemd service
        subprocess.run(["systemctl", "restart", "parental-control.service"], check=False)
        if not quiet:
            print("✅ Systemd service 'parental-control.service' restarted successfully.")
            print("\n🎉 Parental Control successfully upgraded to the latest version!")
    except Exception as e:
        if not quiet:
            print(f"❌ Error during update: {e}")
        sys.exit(1)


def cmd_check(args: argparse.Namespace, config: AppConfig) -> None:
    """Run one-off login access check."""
    user = args.user or getpass.getuser()
    device = getattr(args, "device", None) or config.effective_device_name
    is_pam = getattr(args, "pam", False)

    if not is_pam:
        print(f"Checking parental control access for user '{user}' on device '{device}' at {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}...")

    if not config.is_user_targeted(user):
        if not is_pam:
            print(f"✅ User '{user}' is exempt from parental control.")
        sys.exit(0)

    url = args.url or config.google_sheet.url
    client = GoogleSheetClient(
        sheet_url=url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        cache_path=config.cache_file_path,
    )

    try:
        rules, is_cached, age = client.fetch_rules(use_cache_on_failure=True)
    except Exception as e:
        if is_pam:
            print(f"Parental Control Error: {e}")
            sys.exit(1)
        print(f"❌ Error fetching schedule: {e}")
        sys.exit(1)

    exact_matching = config.rules.exact_username_matching
    if getattr(args, "exact_matching", None) is True:
        exact_matching = True
    elif getattr(args, "fuzzy_matching", None) is True:
        exact_matching = False

    result = evaluate_access(
        user=user,
        rules=rules,
        check_dt=datetime.now(),
        device=device,
        is_cached=is_cached,
        cache_age_seconds=age,
        exact_username_matching=exact_matching,
    )

    if is_pam:
        if result.is_allowed:
            sys.exit(0)
        else:
            reason = result.reason.rstrip(".")
            next_info = f" Next allowed session: {result.next_slot.formatted_range()}." if result.next_slot else ""
            print(f"Parental Control: Computer access is restricted ({reason}).{next_info}")
            sys.exit(1)

    if result.is_allowed:
        print(f"✅ ACCESS GRANTED")
        print(f"   Active Time Slot: {result.active_slot.formatted_range() if result.active_slot else 'N/A'}")
        print(f"   Remaining Time: {int(result.remaining_minutes)} minutes")
        print(f"   Device: {result.device}")
        if result.is_cached_schedule:
            print(f"   (Using cached schedule, age: {result.cache_age_seconds:.0f}s)")
    else:
        print(f"⛔ ACCESS DENIED")
        print(f"   Reason: {result.reason}")
        print(f"   Device: {result.device}")
        if result.allowed_slots_today:
            slots_str = ", ".join(s.formatted_range() for s in result.allowed_slots_today)
            print(f"   Allowed Hours Today: {slots_str}")
        if result.next_slot:
            print(f"   Next Allowed Window: {result.next_slot.formatted_range()}")
        sys.exit(1)


def cmd_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Display current parental control status and schedule."""
    user = args.user or getpass.getuser()
    url = args.url or config.google_sheet.url
    device = getattr(args, "device", None) or config.effective_device_name

    exact_matching = config.rules.exact_username_matching
    if getattr(args, "exact_matching", None) is True:
        exact_matching = True
    elif getattr(args, "fuzzy_matching", None) is True:
        exact_matching = False

    print(f"\n================ PARENTAL CONTROL STATUS ================")
    print(f"Current User:        {user}")
    print(f"Current Device:      {device}")
    print(f"Is Targeted:         {'Yes' if config.is_user_targeted(user) else 'No (Exempt)'}")
    print(f"Username Matching:   {'Exact (Strict)' if exact_matching else 'Fuzzy (Typo-tolerant)'}")
    print(f"Google Sheet Source: {url or config.google_sheet.service_account_path or '(Not configured)'}")
    print(f"Current Date/Time:   {datetime.now().strftime('%A, %Y-%m-%d %I:%M:%S %p')}")
    print(f"=========================================================\n")

    if not url and not config.google_sheet.service_account_path:
        print("⚠️ No Google Sheet configured. Run 'sudo parentalcontrol service-install' to configure.\n")
        return

    client = GoogleSheetClient(
        sheet_url=url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        cache_path=config.cache_file_path,
    )

    try:
        rules, is_cached, age = client.fetch_rules(use_cache_on_failure=True)
    except Exception as e:
        print(f"❌ Error fetching schedule: {e}\n")
        return

    result = evaluate_access(
        user=user,
        rules=rules,
        check_dt=datetime.now(),
        device=device,
        is_cached=is_cached,
        cache_age_seconds=age,
        exact_username_matching=exact_matching,
    )

    status_str = "🟢 ALLOWED" if result.is_allowed else "🔴 BLOCKED"
    print(f"Status:              {status_str}")
    print(f"Reason:              {result.reason}")
    if result.active_slot:
        print(f"Current Active Slot: {result.active_slot.formatted_range()}")
        print(f"Remaining Time:      {int(result.remaining_minutes)} minutes")
    if result.allowed_slots_today:
        print(f"Today's Schedule:    {', '.join(s.formatted_range() for s in result.allowed_slots_today)}")
    if result.next_slot:
        print(f"Next Window Today:   {result.next_slot.formatted_range()}")
    if result.is_cached_schedule:
        print(f"Schedule Source:     Local Cache (Age: {result.cache_age_seconds:.0f} seconds)")
    else:
        print(f"Schedule Source:     Live Sheet / File")

    print("\nAll Scheduled Rules:")
    table_data = [
        [
            r.user,
            r.device,
            r.day,
            f"{r.start_time.strftime('%I:%M %p').lstrip('0')} - {r.end_time.strftime('%I:%M %p').lstrip('0')}",
            "✅ Yes" if r.allowed else "❌ No",
            f"{r.max_minutes} min" if r.max_minutes else "-",
            r.message or "",
        ]
        for r in rules
    ]
    headers = ["User", "Device", "Day", "Time Slot", "Allowed", "Daily Limit", "Notes"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print()


def cmd_test_sheet(args: argparse.Namespace, config: AppConfig) -> None:
    """Test fetching and parsing Google Sheet schedule."""
    url = args.url or config.google_sheet.url
    if not url and not config.google_sheet.service_account_path:
        print("❌ Error: No Google Sheet URL provided. Specify with --url or configure in config.yaml.")
        sys.exit(1)

    print(f"Fetching schedule from: {url or config.google_sheet.service_account_path}...")
    client = GoogleSheetClient(
        sheet_url=url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=args.sheet or config.google_sheet.sheet_name,
        screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
        apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
        apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
        cache_path=config.cache_file_path,
        app_limits_cache_path=config.app_limits_cache_file_path,
    )

    try:
        rules, is_cached, age = client.fetch_rules(use_cache_on_failure=False)
        print(f"✅ Successfully fetched and parsed {len(rules)} schedule rules from '{client.screen_time_sheet_name}'!\n")
        table = [
            [
                r.user,
                r.device,
                r.day,
                r.start_time.strftime("%I:%M %p").lstrip("0"),
                r.end_time.strftime("%I:%M %p").lstrip("0"),
                "✅ True" if r.allowed else "❌ False",
                f"{r.max_minutes}m" if r.max_minutes else "-",
                r.message or "",
            ]
            for r in rules
        ]
        headers = ["User", "Device", "Day", "Start Time", "End Time", "Allowed", "Max Quota", "Message"]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))
    except Exception as e:
        print(f"❌ Failed to fetch/parse schedule rules: {e}")

    try:
        app_rules, _, _ = client.fetch_app_rules(use_cache_on_failure=False)
        if app_rules:
            print(f"\n✅ Successfully fetched and parsed {len(app_rules)} application rules from '{client.apps_limit_sheet_name}'!\n")
            app_table = [
                [
                    r.user,
                    r.app_name,
                    ", ".join(r.patterns),
                    r.device,
                    r.day,
                    "✅ True" if r.allowed else "❌ False",
                    f"{r.start_time.strftime('%I:%M %p').lstrip('0')} - {r.end_time.strftime('%I:%M %p').lstrip('0')}" if r.start_time and r.end_time else "Anytime",
                    f"{r.daily_limit_minutes}m" if r.daily_limit_minutes else "-",
                    r.message or "",
                ]
                for r in app_rules
            ]
            app_headers = ["User", "App Label", "Patterns / Binaries", "Device", "Day", "Allowed", "Time Window", "Daily Quota", "Message"]
            print(tabulate(app_table, headers=app_headers, tablefmt="fancy_grid"))
        else:
            print(f"\nℹ️ No application rules found in '{client.apps_limit_sheet_name}' tab (or tab not created yet).")
    except Exception as e:
        print(f"\n⚠️ Could not fetch application rules from '{client.apps_limit_sheet_name}': {e}")


def cmd_create_template(args: argparse.Namespace) -> None:
    """Generate a sample CSV template for Google Sheets."""
    csv_content = """User,Device,Day,Start Time,End Time,Allowed,Max Minutes,Message
*,*,Monday-Friday,16:00,20:00,TRUE,120,Weekday homework & screen time
*,*,Saturday-Sunday,10:00,12:30,TRUE,150,Weekend morning session
*,*,Saturday-Sunday,16:00,20:30,TRUE,180,Weekend evening session
himanshu,optiplex-3050,Friday,15:00,21:00,TRUE,180,Desktop gaming reward
himanshu,laptop,Friday,15:00,18:00,TRUE,60,Laptop homework only
himanshi,*,Sunday,14:00,19:00,TRUE,120,Sunday afternoon gaming
*,*,*,21:00,07:00,FALSE,,Bedtime - Access blocked
"""
    out_path = Path(args.out) if args.out else Path.cwd() / "google_spreadsheet_template.csv"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    print(f"✅ Template generated at: {out_path.resolve()}")
    print("\nHow to use with Google Sheets:")
    print("1. Open Google Sheets (https://sheets.new)")
    print("2. Click File -> Import -> Upload, and choose this CSV file.")
    print("3. Click 'Share' (top right) -> 'General access' -> 'Anyone with the link' (Viewer).")
    print("4. Copy the link and run: sudo parentalcontrol service-install --url '<COPIED_LINK>'")


def cmd_setup(args: argparse.Namespace, config: AppConfig) -> None:
    """Interactive setup wizard for system service."""
    print("\n" + "=" * 58)
    print("      PARENTAL CONTROL FOR UBUNTU - SYSTEM SERVICE SETUP")
    print("=" * 58 + "\n")

    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if not is_root:
        print("⚠️ NOTE: Running as standard user. To install as a system service,")
        print("please run this wizard with 'sudo'.\n")

    url = args.url
    if not url:
        print("Please enter your Google Sheet URL (Shared as 'Anyone with link can view'):")
        url = input("Google Sheet URL: ").strip()

    if url:
        config.google_sheet.url = url

    target_users = args.target_users
    if not target_users:
        print(f"\nEnter usernames of children to protect (comma-separated, e.g. child1,child2):")
        print(f"Press Enter for '*' (protects all non-exempt users on this computer):")
        inp = input("Target users [*]: ").strip()
        if inp:
            config.rules.target_users = [u.strip() for u in inp.split(",") if u.strip()]
        else:
            config.rules.target_users = ["*"]

    exempt_users = args.exempt_users
    if not exempt_users:
        default_exempt = "root,admin,parent"
        if os.environ.get("SUDO_USER"):
            default_exempt += f",{os.environ.get('SUDO_USER')}"
        elif getpass.getuser() != "root":
            default_exempt += f",{getpass.getuser()}"
        print(f"\nEnter exempt usernames (never restricted, e.g. {default_exempt}):")
        inp = input(f"Exempt users [{default_exempt}]: ").strip()
        if inp:
            config.rules.exempt_users = [u.strip() for u in inp.split(",") if u.strip()]
        else:
            config.rules.exempt_users = [u.strip() for u in default_exempt.split(",") if u.strip()]

    if is_root:
        cmd_service_install(args, config)
    else:
        saved_path = save_config(config)
        print(f"\n✅ User configuration saved to: {saved_path}")
        print("\nTo activate as a system service, please execute:")
        print(f"   sudo parentalcontrol service-install --url '{config.google_sheet.url}'\n")


def cmd_lockout_screen(args: argparse.Namespace, config: AppConfig) -> None:
    """Run the always-on-top lockout screen."""
    from parentalcontrol.lockout_gui import run_lockout_screen
    exempt = [u.strip() for u in args.exempt_users.split(",") if u.strip()] if args.exempt_users else config.rules.exempt_users
    code = run_lockout_screen(
        child_user=args.user or os.environ.get("USER", "child"),
        exempt_users=exempt,
        reason=args.reason or "Your permitted screen time for this session is over.",
        next_session_info=args.next_session,
        session_id=args.session_id,
        testing_mode=args.testing,
        is_login_denial=getattr(args, "login_denial", False),
    )
    sys.exit(code)


def cmd_test_lockout(args: argparse.Namespace, config: AppConfig) -> None:
    """Test the lockout overlay screen interactively (Press Esc to exit in test mode)."""
    from parentalcontrol.lockout_gui import run_lockout_screen
    user = args.user or os.environ.get("USER", "himanshu")
    exempt = config.rules.exempt_users or ["atul"]
    print("\n🚀 Launching test lockout overlay...")
    print("   • Testing mode: Press Esc or test parent password unlock to dismiss.")
    print("   • Running commands and background tasks continue unaffected.")
    code = run_lockout_screen(
        child_user=user,
        exempt_users=exempt,
        reason="Your permitted screen time for this session is over.",
        next_session_info="Next allowed session today: 4:00 PM - 8:30 PM",
        testing_mode=True,
    )
    res_str = "UNLOCKED / EXTENDED" if code == 0 else "LOGGED OUT" if code == 2 else f"CLOSED (code {code})"
    print(f"\n✅ Lockout overlay exited: {res_str}\n")


def cmd_fix_shortcuts(args: argparse.Namespace, config: AppConfig) -> None:
    """Repair and restore standard desktop keybindings (Alt+Tab, Super, workspace switching)."""
    from parentalcontrol.lockout_gui import restore_all_gnome_keybindings
    print("\n🔧 Restoring standard GNOME desktop shortcuts (Alt+Tab, Super, workspaces)...")

    target = getattr(args, "user", None)
    if not target and hasattr(os, "geteuid") and os.geteuid() == 0:
        import pwd
        for p in pwd.getpwall():
            if 1000 <= p.pw_uid < 60000:
                restore_all_gnome_keybindings(target_user=p.pw_name)
        print("✅ Restored desktop shortcuts for all system users.")
    else:
        restore_all_gnome_keybindings(target_user=target)
        print("✅ Restored desktop shortcuts for current user.")

    print("   • Alt+Tab (Switch Windows): Active")
    print("   • Super+Tab (Switch Applications): Active")
    print("   • Super Key (Overview): Active\n")



def cmd_override(args: argparse.Namespace, config: AppConfig) -> None:
    """Manage temporary parent overrides."""
    import time
    from parentalcontrol.override_manager import (
        grant_temporary_override,
        load_all_overrides,
        revoke_override,
    )

    if args.list:
        all_ov = load_all_overrides()
        if not all_ov:
            print("\nNo active temporary overrides.\n")
            return
        print("\nActive Temporary Overrides:")
        for u, inf in all_ov.items():
            rem = int(max(0, inf.get("expires_at", 0) - time.time()) / 60)
            print(f"  • {u}: Granted by '{inf.get('granted_by')}' for {inf.get('duration_minutes')}m ({rem} min remaining, expires at {inf.get('expires_at_iso')})")
        print()
        return

    if args.revoke:
        if not args.user:
            print("❌ Error: --user is required when using --revoke.")
            sys.exit(1)
        if revoke_override(args.user):
            print(f"✅ Temporary override for '{args.user}' revoked.")
        else:
            print(f"ℹ️ No active override found for '{args.user}'.")
        return

    if not args.user or not args.minutes:
        print("❌ Error: --user and --minutes are required to grant an override.")
        print("   Usage: parentalcontrol override --user <child> --minutes <mins> [--parent <parent>]")
        print("          parentalcontrol override --list")
        print("          parentalcontrol override --revoke --user <child>")
        sys.exit(1)

    parent = args.parent or os.environ.get("SUDO_USER") or os.environ.get("USER", "atul")
    rec = grant_temporary_override(args.user, parent, int(args.minutes))
    print(f"\n✅ Temporary override granted for '{args.user}' by '{parent}' for {args.minutes} minutes.")
    print(f"   Expires at: {rec.get('expires_at_iso')}\n")


def cmd_app_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Display local application usage tracking data for today."""
    from parentalcontrol.app_usage_store import AppUsageStore
    store = AppUsageStore(config.app_usage_file_path)
    records = store.get_usage_records_for_sync(device=config.effective_device_name)

    target_user = args.user.lower().strip() if getattr(args, "user", None) else None
    if target_user:
        records = [r for r in records if r.user.lower() == target_user]

    print("\n================ APPLICATION USAGES (TODAY) ================")
    print(f"Tracking Date : {store.current_date_str}")
    print(f"Device        : {config.effective_device_name}")
    gsa_status = "✅ Configured" if config.google_sheet.service_account_path and os.path.exists(config.google_sheet.service_account_path) else "❌ Not configured (local tracking only)"
    print(f"GSA Sync      : {gsa_status}\n")

    if not records:
        print("ℹ️ No application usage recorded yet today for targeted users.\n")
        return

    table = []
    for r in records:
        lim_str = f"{r.daily_limit_minutes}m" if r.daily_limit_minutes else "-"
        rem_str = f"{r.remaining_minutes}m" if r.remaining_minutes is not None else "-"
        table.append([
            r.user,
            r.app_name,
            r.binary_or_pattern,
            f"{r.minutes_used}m",
            lim_str,
            rem_str,
            r.status,
            r.last_active,
        ])

    headers = ["User", "App Label", "Binary / Pattern", "Used Today", "Daily Limit", "Remaining", "Status", "Last Active"]
    print(tabulate(table, headers=headers, tablefmt="fancy_grid"))
    print("\n💡 Run 'parentalcontrol sync-usage' to immediately push this data to Google Sheets.\n")


def cmd_sync_usage(args: argparse.Namespace, config: AppConfig) -> None:
    """Manually sync application usage data to Google Sheets 'Apps Usages' tab."""
    from parentalcontrol.app_usage_store import AppUsageStore
    store = AppUsageStore(config.app_usage_file_path)
    records = store.get_usage_records_for_sync(device=config.effective_device_name)

    if not config.google_sheet.service_account_path or not os.path.exists(config.google_sheet.service_account_path):
        print("\n❌ Error: Google Service Account key not found.")
        print(f"   Expected path: {config.google_sheet.service_account_path or '/etc/parental-control/service_account.json'}")
        print("   A Google Service Account is required to write usage logs to Google Sheets.")
        print("   Please refer to docs/GSA_SETUP_GUIDE.md to generate and install your key.\n")
        sys.exit(1)

    print(f"\n🔄 Syncing {len(records)} application usage records to '{config.google_sheet.apps_usage_sheet_name}'...")
    client = GoogleSheetClient(
        sheet_url=config.google_sheet.url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
        apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
        apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
        cache_path=config.cache_file_path,
        app_limits_cache_path=config.app_limits_cache_file_path,
    )

    success = client.push_app_usages(records)
    if success:
        print(f"✅ Successfully synced usage data to Google Sheets tab '{config.google_sheet.apps_usage_sheet_name}'!\n")
    else:
        print(f"❌ Failed to sync usage data to Google Sheets. Check logs for details.\n")
        sys.exit(1)


def cmd_test_apps(args: argparse.Namespace, config: AppConfig) -> None:
    """Inspect running processes for a user and check against Apps Limit rules."""
    from parentalcontrol.app_monitor import scan_user_processes, matches_process
    from parentalcontrol.app_enforcer import AppEnforcer
    from parentalcontrol.app_usage_store import AppUsageStore

    target_user = args.user or getpass.getuser()
    try:
        pw = pwd.getpwnam(target_user)
        target_uid = pw.pw_uid
    except KeyError:
        print(f"❌ Error: User '{target_user}' does not exist on this system.")
        sys.exit(1)

    print(f"\n🔍 Scanning running processes for user '{target_user}' (UID {target_uid})...")
    url = args.url or config.google_sheet.url
    client = GoogleSheetClient(
        sheet_url=url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
        apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
        apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
        cache_path=config.cache_file_path,
        app_limits_cache_path=config.app_limits_cache_file_path,
    )

    try:
        app_rules, is_cached, _ = client.fetch_app_rules(use_cache_on_failure=True)
        print(f"   Loaded {len(app_rules)} application rules from Google Sheet (Cached: {is_cached})")
    except Exception as e:
        print(f"   ⚠️ Could not load remote rules: {e}")
        app_rules = []

    procs = scan_user_processes(target_uid)
    print(f"   Found {len(procs)} total running processes for {target_user}.\n")

    store = AppUsageStore(config.app_usage_file_path)
    enforcer = AppEnforcer(store)
    applicable = enforcer.filter_applicable_rules(
        username=target_user,
        rules=app_rules,
        device=config.effective_device_name,
        exact_user_matching=config.rules.exact_username_matching,
    )

    now_time = datetime.now().time()
    matched_rows = []
    seen_pids = set()

    for proc in procs:
        for rule in applicable:
            if matches_process(proc, rule):
                seen_pids.add(proc.pid)
                win_str = f"{rule.start_time.strftime('%I:%M %p').lstrip('0')} - {rule.end_time.strftime('%I:%M %p').lstrip('0')}" if rule.start_time and rule.end_time else "Anytime"
                lim_str = f"{rule.daily_limit_minutes}m" if rule.daily_limit_minutes else "-"

                status_str = "✅ Permitted"
                if not rule.allowed:
                    status_str = "❌ Blocked"
                elif not rule.is_in_allowed_window(now_time):
                    status_str = "⏰ Outside Window"
                elif rule.daily_limit_minutes is not None:
                    used = store.get_minutes_used(target_user, rule.app_name)
                    if used >= rule.daily_limit_minutes:
                        status_str = "⛔ Quota Reached"

                matched_rows.append([
                    proc.pid,
                    proc.name,
                    proc.appimage_path or proc.exe,
                    rule.app_name,
                    win_str,
                    lim_str,
                    status_str,
                ])
                break

    if matched_rows:
        print("Matched Application Processes:")
        headers = ["PID", "Process", "Executable / AppImage", "Matched Rule", "Window", "Daily Limit", "Status"]
        print(tabulate(matched_rows, headers=headers, tablefmt="fancy_grid"))
    else:
        print("ℹ️ No running processes currently match any active 'Apps Limit' rules.")
    print()


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt user for yes/no if interactive, else use default."""
    if not sys.stdin.isatty():
        return default
    try:
        suffix = " [Y/n]: " if default else " [y/N]: "
        ans = input(question + suffix).strip().lower()
        if not ans:
            return default
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return default


def _verify_or_create_spreadsheet(config: AppConfig, client_email: str) -> None:
    """Connect to Google Sheet or prompt to create new one."""
    client = GoogleSheetClient(
        sheet_url=config.google_sheet.url,
        service_account_path=config.google_sheet.service_account_path,
        sheet_name=config.google_sheet.sheet_name,
        screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
        apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
        apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
    )

    if not config.google_sheet.url:
        print("\nℹ️ No Google Spreadsheet URL is currently configured.")
        if _prompt_yes_no("👉 Would you like to create a brand new Google Spreadsheet automatically?", default=True):
            try:
                new_url, sh = client.create_new_spreadsheet()
                config.google_sheet.url = new_url
                save_config(config)
                print(f"\n🎉 Successfully created brand new Google Spreadsheet!")
                print(f"   URL: {new_url}")
                print(f"\n⚠️ ACTION REQUIRED: In Google Drive, please open your spreadsheet and Share it")
                print(f"   with your personal Google account so you can view and edit the schedule.\n")
                return
            except Exception as e:
                print(f"❌ Failed to create spreadsheet: {e}")
                print("   Make sure Google Drive API and Google Sheets API are enabled in Google Cloud Console.")
                return
        else:
            if sys.stdin.isatty():
                try:
                    url_input = input("👉 Please enter your existing Google Spreadsheet URL: ").strip()
                    if url_input:
                        config.google_sheet.url = url_input
                        save_config(config)
                        client.sheet_url = url_input
                except (EOFError, KeyboardInterrupt):
                    pass

    if not config.google_sheet.url:
        return

    # Test connection
    print(f"\n🔍 Testing connection to: {config.google_sheet.url}...")
    success, sh, msg = client.check_spreadsheet_connection()
    if success and sh is not None:
        print(f"✅ Successfully connected to Google Sheet ('{sh.title}') via Service Account!")

        # Check tabs / worksheets
        existing_tabs = client.get_existing_worksheets(sh)
        print(f"   Existing tabs found: {', '.join(existing_tabs) or 'None'}")

        # Check if Sheet1 exists and Screen Time does not
        has_sheet1 = any(t.lower() == "sheet1" for t in existing_tabs)
        has_screen_named = any(t.lower() == client.screen_time_sheet_name.lower() for t in existing_tabs)

        if has_sheet1 and not has_screen_named:
            if _prompt_yes_no("👉 'Sheet1' found. Would you like to rename 'Sheet1' to 'Screen Time'?", default=True):
                try:
                    ws1 = sh.worksheet("Sheet1")
                    ws1.update_title(client.screen_time_sheet_name)
                    print(f"   ✅ Renamed tab 'Sheet1' -> '{client.screen_time_sheet_name}'")
                    existing_tabs = client.get_existing_worksheets(sh)
                except Exception as e:
                    print(f"   ⚠️ Could not rename Sheet1: {e}")

        missing = []
        for tab in [client.screen_time_sheet_name, client.apps_limit_sheet_name, client.apps_usage_sheet_name]:
            if not any(t.lower() == tab.lower() for t in existing_tabs):
                missing.append(tab)

        if missing:
            print(f"\n⚠️ Missing tabs: {', '.join(missing)}")
            if _prompt_yes_no("👉 Would you like to create the missing tabs automatically with standard headers?", default=True):
                res = client.ensure_default_worksheets(sh, create_missing=True)
                for tab_name, created in res.items():
                    if created:
                        print(f"   ✅ Created tab: '{tab_name}'")
        else:
            print("   ✅ All required tabs ('Screen Time', 'Apps Limit', 'Apps Usages') are present!")
        print("\n🚀 Setup complete! Your Parental Control is fully connected to Google Sheets.\n")
    else:
        print(f"\n❌ Could not access Google Sheet: {msg}")
        print("\n📋 REQUIRED STEP TO GRANT ACCESS:")
        print("   1. Open your Google Sheet in a browser:")
        print(f"      {config.google_sheet.url}")
        print("   2. Click the green 'Share' button (top right).")
        print("   3. Paste this Service Account email with 'Editor' permissions:")
        print(f"      👉 {client_email}")
        print("   4. Uncheck 'Notify people' and click 'Share'.")
        print("\n   After sharing, run: parentalcontrol recheck\n")

        if _prompt_yes_no("👉 Alternatively, would you like to create a brand new Google Spreadsheet instead?", default=False):
            try:
                new_url, _ = client.create_new_spreadsheet()
                config.google_sheet.url = new_url
                save_config(config)
                print(f"\n🎉 Successfully created brand new Google Spreadsheet!")
                print(f"   URL: {new_url}")
                print(f"\n⚠️ ACTION REQUIRED: In Google Drive, please open your spreadsheet and Share it")
                print(f"   with your personal Google account so you can view and edit the schedule.\n")
            except Exception as e:
                print(f"❌ Failed to create spreadsheet: {e}\n")


def cmd_gsa_status(config: AppConfig) -> None:
    """Display current Google Service Account (GSA) configuration and connection status."""
    gsa_path_str = config.google_sheet.service_account_path or "/etc/parental-control/service_account.json"
    gsa_path = Path(gsa_path_str)

    print("\n================ GOOGLE SERVICE ACCOUNT (GSA) STATUS ================\n")
    print(f"Key File Path         : {gsa_path}")

    if not gsa_path.exists():
        print("Installation Status   : ❌ Not installed")
        print("Service Account Email : -")
        print(f"Google Sheet URL      : {config.google_sheet.url or 'Not configured'}")
        print("\n💡 To install your Google Service Account key, run:")
        print("   sudo parentalcontrol gsa --file <path_to_downloaded_json>\n")
        return

    mode = oct(gsa_path.stat().st_mode)[-3:]
    perm_ok = mode in ("600", "400")
    perm_str = f"✅ Permissions {mode}" if perm_ok else f"⚠️ Permissions {mode} (Recommended: 0600 - run 'sudo chmod 600 {gsa_path}')"
    print(f"Installation Status   : ✅ Installed ({perm_str})")

    from parentalcontrol.sheet_client import validate_service_account_file
    valid, email, _ = validate_service_account_file(gsa_path)
    if valid:
        print(f"Service Account Email : 📧 {email}")
    else:
        print(f"Service Account Email : ❌ Invalid key file ({email})")
        return

    print(f"Google Sheet URL      : {config.google_sheet.url or '❌ Not configured'}")
    if not config.google_sheet.url:
        print("\n💡 Configure your sheet URL with: sudo parentalcontrol gsa --url <sheet_url>\n")
        return

    client = GoogleSheetClient(
        sheet_url=config.google_sheet.url,
        service_account_path=str(gsa_path),
        sheet_name=config.google_sheet.sheet_name,
        screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
        apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
        apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
    )

    success, sh, msg = client.check_spreadsheet_connection()
    if success and sh is not None:
        print(f"Spreadsheet Access    : ✅ Connected successfully ('{sh.title}')")
        tabs = client.get_existing_worksheets(sh)
        has_screen = any(t.lower() == client.screen_time_sheet_name.lower() or t.lower() in ("sheet1", "screen time") for t in tabs)
        has_apps = any(t.lower() == client.apps_limit_sheet_name.lower() or t.lower() in ("apps limit", "app limits") for t in tabs)
        has_usages = any(t.lower() == client.apps_usage_sheet_name.lower() or t.lower() in ("apps usages", "app usages") for t in tabs)

        print("\nWorksheets Status:")
        print(f"  • Tab '{client.screen_time_sheet_name}': {'✅ Present' if has_screen else '❌ Missing'}")
        print(f"  • Tab '{client.apps_limit_sheet_name}': {'✅ Present' if has_apps else '❌ Missing'}")
        print(f"  • Tab '{client.apps_usage_sheet_name}': {'✅ Present' if has_usages else '❌ Missing'}")

        if not has_screen or not has_apps or not has_usages:
            print("\n💡 Run 'sudo parentalcontrol gsa --recheck' to auto-create missing worksheets.")
    else:
        print(f"Spreadsheet Access    : ❌ Connection failed ({msg})")
        print("\n👉 Ensure the spreadsheet is shared with Editor permission to:")
        print(f"   {email}\n")
    print()


def cmd_gsa(args: argparse.Namespace, config: AppConfig) -> None:
    """Manage Google Service Account (GSA) key installation, validation, and spreadsheet setup."""
    if not getattr(args, "file", None):
        cmd_gsa_status(config)
        return

    file_str = str(args.file).strip().strip("'\"")
    # If run with sudo, resolve ~ to actual user's home instead of /root
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and file_str.startswith("~"):
        try:
            import pwd
            user_home = pwd.getpwnam(sudo_user).pw_dir
            file_str = os.path.join(user_home, file_str.lstrip("~/"))
        except Exception:
            pass

    key_src = Path(file_str).expanduser().resolve()
    if not key_src.exists() and sudo_user and not Path(file_str).is_absolute():
        # Fallback check relative to SUDO_USER home
        try:
            import pwd
            user_home = Path(pwd.getpwnam(sudo_user).pw_dir)
            candidate = (user_home / file_str).resolve()
            if candidate.exists():
                key_src = candidate
        except Exception:
            pass

    if not key_src.exists():
        print(f"\n❌ Error: Service Account file not found at: {key_src}")
        sys.exit(1)

    from parentalcontrol.sheet_client import validate_service_account_file
    is_valid, email_or_err, _ = validate_service_account_file(key_src)
    if not is_valid:
        print(f"\n❌ Error: Invalid Service Account file: {email_or_err}")
        print("   Please ensure you downloaded a valid JSON key from Google Cloud Console.\n")
        sys.exit(1)

    client_email = email_or_err
    print(f"\n🔑 Validated Service Account key for: {client_email}")

    # Determine destination path
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if is_root:
        dest_dir = Path("/etc/parental-control")
    else:
        dest_dir = Path.home() / ".config" / "parental-control"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "service_account.json"

    try:
        shutil.copyfile(key_src, dest_path)
        os.chmod(dest_path, 0o600)
        if is_root:
            try:
                shutil.chown(dest_path, user="root", group="root")
            except Exception:
                pass
        print(f"✅ Key installed to: {dest_path} (permissions: 0600)")
    except PermissionError:
        print(f"\n❌ Permission denied writing to {dest_path}.")
        print(f"   Please run with sudo:")
        print(f"   sudo parentalcontrol gsa --file '{key_src}'\n")
        sys.exit(1)

    # Update config
    config.google_sheet.service_account_path = str(dest_path)
    if getattr(args, "url", None):
        config.google_sheet.url = args.url

    save_config(config)
    print("✅ Configuration updated with Service Account path.")

    # Now verify connection to spreadsheet & check worksheets
    _verify_or_create_spreadsheet(config, client_email)

    # Restart background service if installed and running
    if is_root and shutil.which("systemctl"):
        try:
            res = subprocess.run(["systemctl", "is-active", "parental-control.service"], capture_output=True, text=True)
            if "active" in res.stdout:
                subprocess.run(["systemctl", "restart", "parental-control.service"], check=False)
                print("🔄 Restarted parental-control.service with updated GSA credentials.")
        except Exception:
            pass


def cmd_recheck(args: argparse.Namespace, config: AppConfig) -> None:
    """Validate system configuration, file permissions, GSA credentials, and Google Sheet connectivity."""
    print("\n================ PARENTAL CONTROL SYSTEM HEALTH CHECK ================\n")

    # 1. Config check
    cfg_path = config.config_file_path or Path("/etc/parental-control/config.yaml")
    cfg_status = f"✅ Present ({cfg_path})" if cfg_path.exists() else f"⚠️ Missing ({cfg_path})"
    print(f"Configuration File    : {cfg_status}")
    print(f"Targeted Child Users  : {config.rules.target_users}")
    print(f"Exempt Parent Users   : {config.rules.exempt_users}")
    print(f"Exact Matching        : {'✅ Strict' if config.rules.exact_username_matching else '⚠️ Fuzzy (Typo-tolerant)'}")

    # 2. GSA Key Check
    gsa_path_str = config.google_sheet.service_account_path or "/etc/parental-control/service_account.json"
    gsa_path = Path(gsa_path_str)
    print(f"\nService Account Key   : {gsa_path}")
    if gsa_path.exists():
        mode = oct(gsa_path.stat().st_mode)[-3:]
        perm_ok = mode in ("600", "400")
        perm_str = f"✅ Permissions {mode}" if perm_ok else f"⚠️ Insecure permissions {mode} (Recommended: 0600 - run 'sudo chmod 600 {gsa_path}')"
        print(f"Key File Status       : ✅ Found ({perm_str})")

        from parentalcontrol.sheet_client import validate_service_account_file
        valid, email, _ = validate_service_account_file(gsa_path)
        if valid:
            print(f"Service Account Email : 📧 {email}")
        else:
            print(f"Service Account Email : ❌ Invalid file ({email})")
    else:
        print("Key File Status       : ❌ Not installed")
        print("                        Run: sudo parentalcontrol gsa --file <path_to_json>")

    # 3. Google Sheet Connection Check
    print(f"\nGoogle Sheet URL      : {config.google_sheet.url or '❌ Not set'}")
    if config.google_sheet.url and gsa_path.exists():
        client = GoogleSheetClient(
            sheet_url=config.google_sheet.url,
            service_account_path=str(gsa_path),
            sheet_name=config.google_sheet.sheet_name,
            screen_time_sheet_name=config.google_sheet.screen_time_sheet_name,
            apps_limit_sheet_name=config.google_sheet.apps_limit_sheet_name,
            apps_usage_sheet_name=config.google_sheet.apps_usage_sheet_name,
        )
        success, sh, msg = client.check_spreadsheet_connection()
        if success and sh is not None:
            print(f"Google Sheet Access   : ✅ Connected successfully ('{sh.title}')")
            tabs = client.get_existing_worksheets(sh)
            has_screen = any(t.lower() == client.screen_time_sheet_name.lower() or t.lower() in ("sheet1", "screen time") for t in tabs)
            has_apps = any(t.lower() == client.apps_limit_sheet_name.lower() or t.lower() in ("apps limit", "app limits") for t in tabs)
            has_usages = any(t.lower() == client.apps_usage_sheet_name.lower() or t.lower() in ("apps usages", "app usages") for t in tabs)

            print(f"  • Tab '{client.screen_time_sheet_name}': {'✅ Present' if has_screen else '❌ Missing'}")
            print(f"  • Tab '{client.apps_limit_sheet_name}': {'✅ Present' if has_apps else '❌ Missing'}")
            print(f"  • Tab '{client.apps_usage_sheet_name}': {'✅ Present' if has_usages else '❌ Missing'}")

            if not has_screen or not has_apps or not has_usages:
                print("\n💡 Tip: Run 'sudo parentalcontrol gsa --recheck' to auto-create missing worksheets.")
        else:
            print(f"Google Sheet Access   : ❌ Connection failed")
            print(f"                        {msg}")
            if gsa_path.exists():
                from parentalcontrol.sheet_client import validate_service_account_file
                valid, email, _ = validate_service_account_file(gsa_path)
                if valid:
                    print(f"\n👉 Ensure the spreadsheet is shared with Editor permission to:")
                    print(f"   {email}")

    # 4. Service status check (concise 1-line check)
    print("\nService Daemon Status :", end=" ")
    if shutil.which("systemctl"):
        res = subprocess.run(["systemctl", "is-active", "parental-control.service"], capture_output=True, text=True)
        is_active = res.stdout.strip()
        status_icon = "✅ Active (Running)" if is_active == "active" else f"⚠️ {is_active.capitalize()}"
        print(status_icon)
    else:
        print("Systemd not available")
    print()


class CleanHelpFormatter(argparse.HelpFormatter):
    """Custom help formatter that renders subcommands cleanly without repeating pseudo-actions."""
    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            parts = []
            for subaction in action._get_subactions():
                parts.append(self._format_action(subaction))
            return self._join_parts(parts)
        return super()._format_action(action)


def main() -> None:
    """Main CLI entrypoint."""
    # Common argument parser for --config flag (can be used before or after any subcommand)
    config_parent_parser = argparse.ArgumentParser(add_help=False)
    config_parent_parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to custom config.yaml file",
    )

    parser = argparse.ArgumentParser(
        prog="parentalcontrol",
        parents=[config_parent_parser],
        formatter_class=CleanHelpFormatter,
        description="Parental Control login guard and system service daemon for Ubuntu via Google Sheets.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit",
    )

    subparsers = parser.add_subparsers(dest="command", title="commands", metavar="<command>")

    # Command: list-users
    p_users = subparsers.add_parser("list-users", parents=[config_parent_parser], help="List system user accounts to update into spreadsheet")
    p_users.add_argument("--csv", action="store_true", help="Output spreadsheet-ready CSV rows")

    # Command: run-service (invoked by systemd)
    subparsers.add_parser("run-service", parents=[config_parent_parser], help="Run the background system service daemon")

    # Command: service-install
    p_s_install = subparsers.add_parser("service-install", parents=[config_parent_parser], help="Install and activate systemd service (requires sudo)")
    p_s_install.add_argument("--url", help="Google Sheet URL")
    p_s_install.add_argument("--target-users", help="Comma-separated target usernames")
    p_s_install.add_argument("--exempt-users", help="Comma-separated exempt usernames")

    # Command: service-uninstall
    subparsers.add_parser("service-uninstall", parents=[config_parent_parser], help="Stop and remove systemd service (requires sudo)")

    # Command: service-status
    subparsers.add_parser("service-status", parents=[config_parent_parser], help="Check system service and active sessions")

    # Command: update
    p_update = subparsers.add_parser("update", parents=[config_parent_parser], help="Update application to latest version (requires sudo)")
    p_update.add_argument("-q", "--quiet", action="store_true", help="Run in quiet mode (used by APT hooks)")

    # Command: status
    p_status = subparsers.add_parser("status", parents=[config_parent_parser], help="Show current status and schedule")
    p_status.add_argument("--user", help="Username to check")
    p_status.add_argument("--device", help="Device name/hostname to check against")
    p_status.add_argument("--url", help="Override Google Sheet URL")
    p_status.add_argument("--exact-matching", action="store_true", default=None, help="Require exact username match (overrides config)")
    p_status.add_argument("--fuzzy-matching", action="store_true", default=None, help="Allow repeated-letter typo tolerance in username (overrides config)")

    # Command: check
    p_check = subparsers.add_parser("check", parents=[config_parent_parser], help="Check login permission for a user")
    p_check.add_argument("--user", help="Username to check (defaults to current user)")
    p_check.add_argument("--device", help="Device name/hostname to check against")
    p_check.add_argument("--url", help="Override Google Sheet URL")
    p_check.add_argument("--dry-run", action="store_true", help="Dry-run test check")
    p_check.add_argument("--pam", action="store_true", help="Format output for PAM authentication module")
    p_check.add_argument("--exact-matching", action="store_true", default=None, help="Require exact username match (overrides config)")
    p_check.add_argument("--fuzzy-matching", action="store_true", default=None, help="Allow repeated-letter typo tolerance in username (overrides config)")

    # Command: test-sheet
    p_test = subparsers.add_parser("test-sheet", parents=[config_parent_parser], help="Test fetching and parsing Google Sheet")
    p_test.add_argument("--url", help="Google Sheet URL to test")
    p_test.add_argument("--sheet", help="Sheet tab name")
    p_test.add_argument("--device", help="Device name to filter")

    # Command: setup
    p_setup = subparsers.add_parser("setup", parents=[config_parent_parser], help="Interactive system service setup wizard")
    p_setup.add_argument("--url", help="Google Sheet URL")
    p_setup.add_argument("--target-users", help="Comma-separated target usernames")
    p_setup.add_argument("--exempt-users", help="Comma-separated exempt usernames")

    # Command: create-template
    p_template = subparsers.add_parser("create-template", parents=[config_parent_parser], help="Generate sample CSV template")
    p_template.add_argument("-o", "--out", help="Output file path")

    # Command: lockout-screen (invoked by system daemon or test)
    p_lockout = subparsers.add_parser("lockout-screen", parents=[config_parent_parser], help="Display always-on-top lockout overlay")
    p_lockout.add_argument("--user", help="Target child username")
    p_lockout.add_argument("--exempt-users", help="Comma-separated exempt usernames")
    p_lockout.add_argument("--reason", help="Lockout reason text")
    p_lockout.add_argument("--next-session", help="Next allowed session text")
    p_lockout.add_argument("--session-id", help="Active loginctl session ID")
    p_lockout.add_argument("--testing", action="store_true", help="Windowed testing mode (Esc to close)")
    p_lockout.add_argument("--login-denial", action="store_true", help="Screen triggered due to login denial")

    # Command: test-lockout (user-facing quick test)
    p_tlock = subparsers.add_parser("test-lockout", parents=[config_parent_parser], help="Test the lockout screen overlay on desktop")
    p_tlock.add_argument("--user", help="Test as username (default: current or himanshu)")

    # Command: override
    p_ovr = subparsers.add_parser("override", parents=[config_parent_parser], help="Manage temporary parent overrides")
    p_ovr.add_argument("--user", help="Child username to extend")
    p_ovr.add_argument("--minutes", type=int, help="Minutes to extend")
    p_ovr.add_argument("--parent", help="Parent/admin username granting override")
    p_ovr.add_argument("--list", action="store_true", help="List all active overrides")
    p_ovr.add_argument("--revoke", action="store_true", help="Revoke active override for user")

    # Command: fix-shortcuts
    p_fix = subparsers.add_parser("fix-shortcuts", parents=[config_parent_parser], help="Repair and restore GNOME desktop shortcuts (Alt+Tab, Super)")
    p_fix.add_argument("--user", help="Specific username to restore shortcuts for")

    # Command: app-status
    p_app_stat = subparsers.add_parser("app-status", parents=[config_parent_parser], help="Show today's application usages and status")
    p_app_stat.add_argument("--user", help="Filter by specific child username")

    # Command: sync-usage
    subparsers.add_parser("sync-usage", parents=[config_parent_parser], help="Manually push local application usages to Google Sheets Apps Usages tab")

    # Command: test-apps
    p_test_apps = subparsers.add_parser("test-apps", parents=[config_parent_parser], help="Inspect running processes and test against Apps Limit rules")
    p_test_apps.add_argument("--user", help="Username whose processes to inspect (defaults to current user)")
    p_test_apps.add_argument("--url", help="Override Google Sheet URL")

    # Command: gsa
    p_gsa = subparsers.add_parser("gsa", parents=[config_parent_parser], help="Install and validate Google Service Account key (GSA)")
    p_gsa.add_argument("-f", "--file", help="Path to downloaded Service Account JSON key file")
    p_gsa.add_argument("--url", help="Google Spreadsheet URL (optional, updates config)")
    p_gsa.add_argument("--recheck", action="store_true", help="Validate GSA key, file permissions, and spreadsheet connection")

    # Command: recheck
    subparsers.add_parser("recheck", parents=[config_parent_parser], help="Validate system health, GSA key permissions, and spreadsheet connectivity")

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    if args.command == "list-users":
        cmd_list_users(args, config)
    elif args.command == "run-service":
        cmd_run_service(args, config)
    elif args.command == "service-install":
        cmd_service_install(args, config)
    elif args.command == "service-uninstall":
        cmd_service_uninstall(args, config)
    elif args.command == "service-status":
        cmd_service_status(args, config)
    elif args.command == "update":
        cmd_update(args, config)
    elif args.command == "check":
        cmd_check(args, config)
    elif args.command == "status":
        cmd_status(args, config)
    elif args.command == "test-sheet":
        cmd_test_sheet(args, config)
    elif args.command == "setup":
        cmd_setup(args, config)
    elif args.command == "create-template":
        cmd_create_template(args)
    elif args.command == "lockout-screen":
        cmd_lockout_screen(args, config)
    elif args.command == "test-lockout":
        cmd_test_lockout(args, config)
    elif args.command == "override":
        cmd_override(args, config)
    elif args.command == "fix-shortcuts":
        cmd_fix_shortcuts(args, config)
    elif args.command == "app-status":
        cmd_app_status(args, config)
    elif args.command == "sync-usage":
        cmd_sync_usage(args, config)
    elif args.command == "test-apps":
        cmd_test_apps(args, config)
    elif args.command == "gsa":
        cmd_gsa(args, config)
    elif args.command == "recheck":
        cmd_recheck(args, config)
    else:
        cmd_service_status(args, config)


if __name__ == "__main__":
    main()
