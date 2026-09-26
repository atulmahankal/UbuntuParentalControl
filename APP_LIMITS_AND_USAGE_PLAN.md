# Comprehensive Plan: User-Wise App Restriction, Duration Limits & Usage Tracking

## 1. Executive Summary

This plan outlines the architecture, data schemas, process inspection engine, and phased implementation for:
1. **User-Wise App Restriction**: Explicitly blocking, whitelisting, or scheduling allowed time windows for specific applications.
2. **User-Wise Duration Limits**: Enforcing daily and per-session runtime quotas on applications.
3. **Standalone Binary & AppImage Support**: Robust detection and enforcement for non-installed executables (AppImages, standalone portable binaries, games, scripts, and downloads).
4. **User-Wise Usage Tracking (`Apps Usages`)**: Automated synchronization of cumulative app activity into Google Sheets.

> [!IMPORTANT]
> All functionality integrates directly into the existing root background service (`parental-control.service`) so that restrictions cannot be bypassed by child users, even if processes are launched without installation or from hidden directories.

---

## 2. Google Sheets Tab Specifications

The system will interact with two dedicated tabs alongside the existing screen time schedule tab:

### A. `Apps Limit` (Configured by Parent - Read by Daemon)

Controls both **App Restrictions** (Allowed/Blocked/Time Windows) and **Duration Limits** (Daily & Session Quotas):

| Column Name | Example Value | Description |
| :--- | :--- | :--- |
| **User** | `himanshu` or `*` | Target child username (exact matching by default). |
| **App Label** | `Google Chrome` | Friendly name for notification dialogs and reports. |
| **Binary / Pattern** | `chrome, *.AppImage, */Downloads/*, game.x86_64` | Process names, executable path globs, or command-line patterns. |
| **Device** | `*` or `optiplex-3050` | Device hostname or `*` for all machines. |
| **Day** | `Monday-Friday` or `All` | Days this restriction/limit applies. |
| **Allowed** | `TRUE` or `FALSE` | **`FALSE`** = Completely blocked.<br>**`TRUE`** = Permitted subject to window & quota. |
| **Allowed Window** | `5:00 PM - 8:30 PM` | Permitted time slot during the day (leave blank for anytime). |
| **Daily Limit (Min)** | `60` | Max total minutes allowed per day (leave blank for unlimited). |
| **Session Limit (Min)**| `30` | Max continuous minutes before requiring a break (optional). |
| **Message** | `Study hours only` | Custom message shown when app is restricted or time expires. |

#### Rule Types Supported:
* **Immediate Block (Restriction)**: `Allowed: FALSE` (e.g. block `tor`, `steam`, `discord`, or any binary in `*/Downloads/*`).
* **Time-Window Restriction**: `Allowed: TRUE`, `Allowed Window: 5:00 PM - 7:00 PM` (cannot be opened outside this window).
* **Duration Limit**: `Allowed: TRUE`, `Daily Limit: 45 min` (can run anytime within screen hours, but only up to 45 minutes total).
* **Combined Window & Limit**: `Allowed: TRUE`, `Window: 4:00 PM - 8:00 PM`, `Limit: 60 min` (can run for at most 60 mins within that 4-hour window).

---

### B. `Apps Usages` (Written by Daemon to Google Sheets)

Automatically logs aggregated application activity for monitored users:

| Column Name | Example | Description |
| :--- | :--- | :--- |
| **Date** | `2026-09-26` | Tracking date (resets daily at midnight). |
| **User** | `himanshu` | Child username. |
| **Device** | `optiplex-3050` | Computer hostname. |
| **App Label** | `Google Chrome` | Friendly application name. |
| **Process / Binary**| `google-chrome` | Matched process or command name. |
| **Executable Path** | `/opt/google/chrome/chrome` | Resolved canonical executable path. |
| **Duration Used** | `42 min` | Total active running time today. |
| **Daily Limit** | `60 min` | Quota configured in `Apps Limit` (or `-`). |
| **Remaining** | `18 min` | Time left before automatic termination (or `-`). |
| **Status** | `Active` / `Quota Reached` / `Restricted` | Current state of this application today. |
| **Last Active** | `03:48 PM` | Timestamp of last detected execution. |

---

## 3. Detecting Standalone Binaries, AppImages, & Scripts

Children frequently bypass parental controls by running portable applications without installing them. The daemon runs with root privileges and inspects `/proc` directly:

```
                            Linux Kernel (/proc)
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
/proc/<pid>/comm            /proc/<pid>/exe             /proc/<pid>/cmdline
 (Short process name)        (Canonical binary path)     (Full launch command)
  e.g. "game.x86_64"          e.g. "/tmp/.mount_X/App"    e.g. "python3 ./hack.py"
```

### Detection Strategy
1. **Target User Filtering**:
   * Inspect only processes where `stat(/proc/<pid>).st_uid == child_uid`.
   * Skip kernel threads and root/system services.
2. **AppImage Resolution**:
   * AppImages mount a squashfs filesystem at `/tmp/.mount_<name>XXXXXX/` and launch `AppRun`.
   * The daemon traces the process back to its environment variable `APPIMAGE` or inspects `/proc/<pid>/environ` to identify the original file path (e.g. `/home/himanshu/Games/SuperGame.AppImage`).
3. **Standalone Binaries (ELF executables without installation)**:
   * Direct inspection of `/proc/<pid>/exe` resolves the exact physical file on disk (e.g., `/home/himanshu/Downloads/retroarch-linux-x86_64`).
   * Supports wildcards like `*/Downloads/*` or `*/Desktop/*` to restrict all unapproved portable binaries.
4. **Interpreter-Wrapped Scripts**:
   * When a child runs `python3 game.py`, `java -jar minecraft.jar`, or `bash script.sh`, `/proc/<pid>/exe` is `/usr/bin/python3`.
   * The matcher inspects `/proc/<pid>/cmdline` to match the target script/jar name.
5. **System Process Exclusion Safeguard**:
   * Hardcoded whitelist of system desktop components to **never** throttle or kill:
     `gnome-shell`, `Xwayland`, `dbus-daemon`, `pipewire`, `wireplumber`, `systemd`, `gvfsd`, `ibus-daemon`, `at-spi2-registryd`, `xdg-desktop-portal*`.

---

## 4. Enforcement Engine & User Feedback Workflow

```
[Daemon 10s Loop]
        │
        ├── Scans active processes for targeted user
        ├── Resolves binary name, exe path, cmdline
        ├── Matches against "Apps Limit" rules
        │
        ├── [Rule: Allowed == FALSE]
        │     └── Immediate SIGTERM ➔ Warning Notification ➔ Blocked
        │
        ├── [Rule: Outside Allowed Window]
        │     └── Immediate SIGTERM ➔ Prompt: "App allowed only between 5-8 PM"
        │
        └── [Rule: Duration Limit Active]
              ├── Accumulates active runtime (+10s)
              ├── Checks remaining time:
              │     ├── 10 min remaining ➔ Warning Notification + Audio Chime
              │     ├── 5 min remaining  ➔ High-priority Notification
              │     ├── 2 min remaining  ➔ Urgent Notification
              │     └── 0 min remaining  ➔ ENFORCEMENT ACTION:
              │           1. Show desktop alert dialog: "Daily limit reached for <App>."
              │           2. Send SIGTERM to process PID & process group
              │           3. Wait 5s grace period
              │           4. Send SIGKILL if process still running
              │           5. Mark rule as EXHAUSTED for today
              │
              └── [Relaunch Guard]: If child attempts to reopen exhausted app:
                    └── Instantly terminate within 2-5 seconds with dialog.
```

---

## 5. Usage Synchronization Architecture (`Apps Usages`)

To ensure reliability without relying on continuous internet connectivity:

1. **Local Persistent Cache (`/var/lib/parental-control/app_usage.json`)**:
   * Stores per-user seconds counter, daily quotas, and last active timestamps.
   * Atomic file writes (`tempfile` + `rename`) to survive sudden power cuts or reboots.
   * Auto-resets daily at 00:00:00 midnight.
2. **Two Sync Options to Google Sheets**:
   * **Option A: Google Apps Script Webhook (Zero GCP Setup - Recommended)**:
     * Parent deploys a 15-line Apps Script in the Google Spreadsheet as a Web App.
     * Daemon performs an asynchronous HTTP POST with the JSON payload.
     * Apps Script finds or inserts rows in the `Apps Usages` tab automatically.
   * **Option B: Google Cloud Service Account (`gspread`)**:
     * Direct API write access using a service account JSON file.
     * Uses `gspread` (already installed in dependencies).
3. **Sync Triggers**:
   * Every 5 minutes while user is active.
   * Immediately upon an app hitting its duration limit.
   * Upon user logout or screen lock.
   * Graceful recovery: if offline, changes queue locally and flush as soon as the network returns.

---

## 6. Implementation File Breakdown

| File | Type | Purpose |
| :--- | :--- | :--- |
| [`src/parentalcontrol/models.py`](file:///home/atul/Documents/ParentalControl/src/parentalcontrol/models.py) | Modify | Define `AppLimitRule` and `AppUsageRecord` data models. |
| [`src/parentalcontrol/config.py`](file:///home/atul/Documents/ParentalControl/src/parentalcontrol/config.py) | Modify | Add configuration parameters for sheet names, sync interval, and webhook URL. |
| [`src/parentalcontrol/sheet_client.py`](file:///home/atul/Documents/ParentalControl/src/parentalcontrol/sheet_client.py) | Modify | Add support for reading `Apps Limit` tab and pushing data to `Apps Usages`. |
| **`src/parentalcontrol/app_monitor.py`** | **New** | Fast `/proc` scanner; resolves process names, AppImages, and standalone binaries; applies glob/pattern matcher. |
| **`src/parentalcontrol/app_usage_store.py`** | **New** | Local JSON storage managing second-by-second usage tracking, daily rollover, and offline queue. |
| **`src/parentalcontrol/app_enforcer.py`** | **New** | Handles warning notifications (10m, 5m, 2m), progressive termination (`SIGTERM` ➔ `SIGKILL`), and relaunch blocking. |
| [`src/parentalcontrol/system_daemon.py`](file:///home/atul/Documents/ParentalControl/src/parentalcontrol/system_daemon.py) | Modify | Integrate `AppMonitor`, `AppUsageStore`, and `AppEnforcer` into main daemon loop and IPC. |
| [`src/parentalcontrol/cli.py`](file:///home/atul/Documents/ParentalControl/src/parentalcontrol/cli.py) | Modify | Add CLI subcommands: `parentalcontrol app-status`, `parentalcontrol test-apps`, and `parentalcontrol sync-usage`. |
| **`tests/test_app_monitor.py`** | **New** | Unit tests for binary matching, AppImage detection, and exclusion filters. |
| **`tests/test_app_usage_store.py`** | **New** | Unit tests for persistence, midnight rollover, and offline queueing. |
| **`tests/test_app_enforcer.py`** | **New** | Unit tests for milestone warnings, process termination, and relaunch blockage. |

---

## 7. Phased Implementation Roadmap

* [ ] **Phase 1: Schemas & Multi-Tab Client**:
  * Implement `AppLimitRule` and `AppUsageRecord` in `models.py`.
  * Update `sheet_client.py` to fetch `Apps Limit` tab via Google Sheets CSV export.
* [ ] **Phase 2: Process & Standalone Binary Engine**:
  * Build `app_monitor.py` to scan child processes in `/proc`.
  * Support exact names, canonical binary paths, AppImage environments, and path globs (`*/Downloads/*`).
  * Add unit tests in `tests/test_app_monitor.py`.
* [ ] **Phase 3: Usage Store & Enforcer**:
  * Build `app_usage_store.py` to record active durations locally in `/var/lib/parental-control/app_usage.json`.
  * Build `app_enforcer.py` to trigger milestone warnings (10m, 5m, 2m) and terminate processes when limits expire.
  * Add relaunch protection.
* [ ] **Phase 4: Google Sheets Usage Sync (`Apps Usages`)**:
  * Add webhook and service account upload routines in `sheet_client.py`.
  * Create a template Google Apps Script for the spreadsheet.
* [ ] **Phase 5: Daemon Integration & CLI**:
  * Wire up the components in `system_daemon.py`.
  * Add CLI commands for status inspection and testing (`parentalcontrol app-status`).
* [ ] **Phase 6: Verification & Hardening**:
  * Test with standard apps (Chrome/Firefox) and standalone binaries/AppImages.
  * Verify clean shutdown, notification delivery, and auto-sync to Google Sheets.
