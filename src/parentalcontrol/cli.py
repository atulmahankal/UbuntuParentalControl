"""Command-line interface for Parental Control."""

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from tabulate import tabulate

from parentalcontrol.autostart import (
    get_executable_path,
    install_system_autostart,
    install_user_autostart,
    uninstall_autostart,
)
from parentalcontrol.config import (
    AppConfig,
    load_config,
    save_config,
    get_default_config_path,
)
from parentalcontrol.daemon import (
    ParentalControlMonitor,
    check_and_enforce_login,
    setup_logging,
)
from parentalcontrol.evaluator import evaluate_access
from parentalcontrol.sheet_client import GoogleSheetClient


def cmd_check(args: argparse.Namespace, config: AppConfig) -> None:
    """Run one-off login access check."""
    user = args.user or getpass.getuser()
    print(f"Checking parental control access for user '{user}' at {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}...")

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
        is_cached=is_cached,
        cache_age_seconds=age,
    )

    if result.is_allowed:
        print(f"✅ ACCESS GRANTED")
        print(f"   Active Time Slot: {result.active_slot.formatted_range() if result.active_slot else 'N/A'}")
        print(f"   Remaining Time: {int(result.remaining_minutes)} minutes")
        if result.is_cached_schedule:
            print(f"   (Using cached schedule, age: {result.cache_age_seconds:.0f}s)")
    else:
        print(f"⛔ ACCESS DENIED")
        print(f"   Reason: {result.reason}")
        if result.allowed_slots_today:
            slots_str = ", ".join(s.formatted_range() for s in result.allowed_slots_today)
            print(f"   Allowed Hours Today: {slots_str}")
        if result.next_slot:
            print(f"   Next Allowed Window: {result.next_slot.formatted_range()}")

        if not args.dry_run:
            from parentalcontrol.daemon import _handle_login_denial
            _handle_login_denial(config, result)
        else:
            print("   [Dry-run mode: Session termination skipped]")
            sys.exit(1)


def cmd_monitor(args: argparse.Namespace, config: AppConfig) -> None:
    """Start continuous session monitor daemon."""
    user = args.user or getpass.getuser()
    monitor = ParentalControlMonitor(config=config, username=user)
    monitor.start()


def cmd_status(args: argparse.Namespace, config: AppConfig) -> None:
    """Display current parental control status and schedule."""
    user = args.user or getpass.getuser()
    url = args.url or config.google_sheet.url
    print(f"\n================ PARENTAL CONTROL STATUS ================")
    print(f"Current User:        {user}")
    print(f"Is Targeted:         {'Yes' if config.is_user_targeted(user) else 'No (Exempt)'}")
    print(f"Google Sheet Source: {url or config.google_sheet.service_account_path or '(Not configured)'}")
    print(f"Current Date/Time:   {datetime.now().strftime('%A, %Y-%m-%d %I:%M:%S %p')}")
    print(f"=========================================================\n")

    if not url and not config.google_sheet.service_account_path:
        print("⚠️ No Google Sheet configured. Run 'parentalcontrol setup' to configure.\n")
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
            r.day,
            f"{r.start_time.strftime('%I:%M %p').lstrip('0')} - {r.end_time.strftime('%I:%M %p').lstrip('0')}",
            "✅ Yes" if r.allowed else "❌ No",
            f"{r.max_minutes} min" if r.max_minutes else "-",
            r.message or "",
        ]
        for r in rules
    ]
    headers = ["User", "Day", "Time Slot", "Allowed", "Daily Limit", "Notes"]
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
                r.day,
                r.start_time.strftime("%I:%M %p").lstrip("0"),
                r.end_time.strftime("%I:%M %p").lstrip("0"),
                "✅ True" if r.allowed else "❌ False",
                f"{r.max_minutes}m" if r.max_minutes else "-",
                r.message or "",
            ]
            for r in rules
        ]
        headers = ["User", "Day", "Start Time", "End Time", "Allowed", "Max Quota", "Message"]
        print(tabulate(table, headers=headers, tablefmt="fancy_grid"))
    except Exception as e:
        print(f"❌ Failed to fetch/parse sheet: {e}")
        sys.exit(1)


def cmd_create_template(args: argparse.Namespace) -> None:
    """Generate a sample CSV template for Google Sheets."""
    csv_content = """User,Day,Start Time,End Time,Allowed,Max Minutes,Message
*,Monday-Friday,16:00,20:00,TRUE,120,Weekday homework & screen time
*,Saturday-Sunday,10:00,12:30,TRUE,150,Weekend morning session
*,Saturday-Sunday,16:00,20:30,TRUE,180,Weekend evening session
child1,Friday,15:00,21:00,TRUE,180,Friday reward extended time
child2,Sunday,14:00,19:00,TRUE,120,Sunday afternoon
*,*,21:00,07:00,FALSE,,Bedtime - No computer access
"""
    out_path = Path(args.out) if args.out else Path.cwd() / "parental_control_schedule_template.csv"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(csv_content)

    print(f"✅ Sample template generated at: {out_path.resolve()}")
    print("\nHow to use with Google Sheets:")
    print("1. Open Google Sheets (https://sheets.new)")
    print("2. Click File -> Import -> Upload, and choose this CSV file.")
    print("3. Click 'Share' (top right) -> 'General access' -> 'Anyone with the link' (Viewer).")
    print("4. Copy the link and run: parentalcontrol setup --url '<COPIED_LINK>'")


def cmd_setup(args: argparse.Namespace, config: AppConfig) -> None:
    """Interactive or automated setup wizard."""
    print("\n" + "=" * 55)
    print("      PARENTAL CONTROL FOR UBUNTU - SETUP WIZARD")
    print("=" * 55 + "\n")

    url = args.url
    if not url:
        print("Please enter your Google Sheet URL (Shared as 'Anyone with link can view'):")
        url = input("Google Sheet URL: ").strip()

    if url:
        config.google_sheet.url = url

    target_users = args.target_users
    if not target_users:
        default_user = getpass.getuser()
        print(f"\nEnter usernames of children to protect (comma-separated, e.g. child1,child2):")
        print(f"Press Enter to protect current user ('{default_user}') or '*' for all children:")
        inp = input(f"Target users [{default_user}]: ").strip()
        if inp:
            config.rules.target_users = [u.strip() for u in inp.split(",") if u.strip()]
        else:
            config.rules.target_users = [default_user]

    exempt_users = args.exempt_users
    if exempt_users:
        config.rules.exempt_users = [u.strip() for u in exempt_users.split(",") if u.strip()]

    config_path = save_config(config)
    print(f"\n✅ Configuration saved to: {config_path}")

    # Autostart setup
    if args.autostart or input("\nInstall autostart for target users so it runs on login? (Y/n): ").strip().lower() != "n":
        for u in config.rules.target_users:
            if u not in ("*", "all"):
                try:
                    df = install_user_autostart(target_user=u)
                    print(f"✅ Autostart desktop entry installed for user '{u}' at: {df}")
                except Exception as e:
                    print(f"⚠️ Could not install autostart for user '{u}': {e}")
            else:
                df = install_user_autostart()
                print(f"✅ Autostart desktop entry installed at: {df}")

    print("\n🎉 Setup complete! You can test your configuration using:")
    print("   parentalcontrol test-sheet")
    print("   parentalcontrol status")
    print("   parentalcontrol check --dry-run\n")


def cmd_install_autostart(args: argparse.Namespace, config: AppConfig) -> None:
    """Install autostart entry."""
    if args.system:
        df = install_system_autostart()
        print(f"✅ Installed system autostart: {df}")
    else:
        df = install_user_autostart(target_user=args.user)
        print(f"✅ Installed user autostart: {df}")


def cmd_uninstall_autostart(args: argparse.Namespace, config: AppConfig) -> None:
    """Uninstall autostart entry."""
    removed = uninstall_autostart(target_user=args.user)
    if removed:
        print(f"✅ Autostart entry successfully removed.")
    else:
        print(f"ℹ️ No autostart entry found to remove.")


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="parentalcontrol",
        description="Parental Control login guard and session monitor for Ubuntu via Google Sheets.",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to custom config.yaml file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: check
    p_check = subparsers.add_parser("check", help="Check login permission for a user")
    p_check.add_argument("--user", help="Username to check (defaults to current user)")
    p_check.add_argument("--url", help="Override Google Sheet URL")
    p_check.add_argument("--dry-run", action="store_true", help="Do not terminate session on denial")

    # Command: monitor
    p_monitor = subparsers.add_parser("monitor", help="Start background session monitoring daemon")
    p_monitor.add_argument("--user", help="Username to monitor (defaults to current user)")

    # Command: status
    p_status = subparsers.add_parser("status", help="Show current status and schedule")
    p_status.add_argument("--user", help="Username to check")
    p_status.add_argument("--url", help="Override Google Sheet URL")

    # Command: test-sheet
    p_test = subparsers.add_parser("test-sheet", help="Test fetching and parsing Google Sheet")
    p_test.add_argument("--url", help="Google Sheet URL to test")
    p_test.add_argument("--sheet", help="Sheet tab name")

    # Command: setup
    p_setup = subparsers.add_parser("setup", help="Run setup wizard")
    p_setup.add_argument("--url", help="Google Sheet URL")
    p_setup.add_argument("--target-users", help="Comma-separated target usernames")
    p_setup.add_argument("--exempt-users", help="Comma-separated exempt usernames")
    p_setup.add_argument("--autostart", action="store_true", help="Auto-install autostart entry")

    # Command: create-template
    p_template = subparsers.add_parser("create-template", help="Generate sample CSV template")
    p_template.add_argument("-o", "--out", help="Output file path (default: parental_control_schedule_template.csv)")

    # Command: install-autostart
    p_inst = subparsers.add_parser("install-autostart", help="Install autostart desktop entry")
    p_inst.add_argument("--user", help="Target username")
    p_inst.add_argument("--system", action="store_true", help="Install system-wide (/etc/xdg/autostart)")

    # Command: uninstall-autostart
    p_uninst = subparsers.add_parser("uninstall-autostart", help="Remove autostart entry")
    p_uninst.add_argument("--user", help="Target username")

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    if args.command == "check":
        cmd_check(args, config)
    elif args.command == "monitor" or args.command is None and len(sys.argv) > 1 and sys.argv[1] == "monitor":
        cmd_monitor(args, config)
    elif args.command == "status":
        cmd_status(args, config)
    elif args.command == "test-sheet":
        cmd_test_sheet(args, config)
    elif args.command == "setup":
        cmd_setup(args, config)
    elif args.command == "create-template":
        cmd_create_template(args)
    elif args.command == "install-autostart":
        cmd_install_autostart(args, config)
    elif args.command == "uninstall-autostart":
        cmd_uninstall_autostart(args, config)
    else:
        # Default: if run with no args, show status
        cmd_status(args, config)


if __name__ == "__main__":
    main()
