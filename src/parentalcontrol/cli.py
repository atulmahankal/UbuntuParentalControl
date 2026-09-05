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
    print(f"Checking parental control access for user '{user}' on device '{device}' at {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}...")

    if not config.is_user_targeted(user):
        print(f"✅ User '{user}' is exempt from parental control.")
        return

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
        print(f"❌ Error fetching schedule: {e}")
        sys.exit(1)

    result = evaluate_access(
        user=user,
        rules=rules,
        check_dt=datetime.now(),
        device=device,
        is_cached=is_cached,
        cache_age_seconds=age,
    )

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


def cmd_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Display current parental control status and schedule."""
    user = args.user or getpass.getuser()
    url = args.url or config.google_sheet.url
    device = getattr(args, "device", None) or config.effective_device_name

    print(f"\n================ PARENTAL CONTROL STATUS ================")
    print(f"Current User:        {user}")
    print(f"Current Device:      {device}")
    print(f"Is Targeted:         {'Yes' if config.is_user_targeted(user) else 'No (Exempt)'}")
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
        cache_path=config.cache_file_path,
    )

    try:
        rules, is_cached, age = client.fetch_rules(use_cache_on_failure=False)
        print(f"✅ Successfully fetched and parsed {len(rules)} schedule rules!\n")
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
        print(f"❌ Failed to fetch/parse sheet: {e}")
        sys.exit(1)


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
        description="Parental Control login guard and system service daemon for Ubuntu via Google Sheets.",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

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

    # Command: check
    p_check = subparsers.add_parser("check", parents=[config_parent_parser], help="Check login permission for a user")
    p_check.add_argument("--user", help="Username to check (defaults to current user)")
    p_check.add_argument("--device", help="Device name/hostname to check against")
    p_check.add_argument("--url", help="Override Google Sheet URL")
    p_check.add_argument("--dry-run", action="store_true", help="Dry-run test check")

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
    else:
        cmd_service_status(args, config)


if __name__ == "__main__":
    main()
