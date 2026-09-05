# 🛡️ Ubuntu Parental Control (System Service & Google Sheets Schedule)

A robust, system-level parental control daemon and login protection application for **Ubuntu Linux** managed with **`uv`**. It runs securely as a **systemd service** (`parental-control.service`), enforcing screen time schedules dynamically configured from a **Google Spreadsheet**.

---

## 🛑 STEP 0: MANDATORY PRE-INSTALLATION PROCESS
### (Granting Spreadsheet Access to the Application)

Before installing the application on your Ubuntu computer, you must create your Google Spreadsheet and grant the application read permissions.

#### 1. Create Spreadsheet from Template
1. Open Google Sheets at [sheets.new](https://sheets.new).
2. Go to **File ➔ Import ➔ Upload** and upload [`google_spreadsheet_template.csv`](./google_spreadsheet_template.csv) (or create the columns manually).

#### 2. Grant Read Permission (Allow Spreadsheet Access)
To allow the Parental Control application to read the schedule without requiring complicated API credentials:
1. In the top-right corner of your Google Sheet, click the blue **Share** button.
2. Under **General access**, change the dropdown from **Restricted** to **"Anyone with the link"**.
3. Ensure the permission role on the right is set to **"Viewer"** (Read-Only).
4. Click **Copy link** (e.g. `https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit?usp=sharing`).
5. Save this copied URL — you will supply it in **Step 1** during installation.

> [!IMPORTANT]
> The application only requires **Viewer (Read-Only)** access. It reads the schedule securely over HTTPS and never modifies your spreadsheet.

---

## ⚡ STEP 1: One-Line Automatic Installation

Once your Google Sheet is shared with "Anyone with the link (Viewer)", install Parental Control with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/main/install.sh | sudo SHEET_URL="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing" bash
```

*(Or simply run `curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/main/install.sh | sudo bash` to be prompted interactively for your Google Sheet URL).*

### What `install.sh` does automatically:
1. Installs all required Ubuntu packages (`python3`, `git`, `curl`, `zenity`, `libnotify-bin`, `libcanberra-gtk-module`).
2. Installs the Astral **`uv`** package manager.
3. Clones and installs the application to `/opt/parental-control`.
4. Builds the Python virtual environment and creates a global symlink at `/usr/local/bin/parentalcontrol`.
5. Saves your configuration to `/etc/parental-control/config.yaml`.
6. Configures, enables, and starts the systemd service `/etc/systemd/system/parental-control.service`.
7. Installs an **APT auto-upgrade hook** (`/etc/apt/apt.conf.d/99parentalcontrol`).

---

## 👥 STEP 2: Getting Ubuntu Usernames for Your Spreadsheet

To ensure your spreadsheet rules match the exact Linux user accounts on your computer, run:

```bash
parentalcontrol list-users
```

Or generate **ready-to-copy CSV rows** for your children:

```bash
parentalcontrol list-users --csv
```

*Example Output:*
```
╒════════════════════════╤═════════════╤═══════╤═════════════════════════╕
│ Username (for Sheet)   │ Full Name   │   UID │ Current Policy          │
╞════════════════════════╪═════════════╪═══════╪═════════════════════════╡
│ atul                   │ Atul        │  1000 │ ⭐ Exempt (Parent/Admin)│
├────────────────────────┼─────────────┼───────┼─────────────────────────┤
│ himanshu               │ Himanshu    │  1001 │ 🛡️ Targeted (Monitored) │
├────────────────────────┼─────────────┼───────┼─────────────────────────┤
│ himanshi               │ Himanshi    │  1002 │ 🛡️ Targeted (Monitored) │
╘════════════════════════╧═════════════╧═══════╧═════════════════════════╛
```

*(Alternative Linux shell command: `awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /nologin|false/ {print $1}' /etc/passwd`)*

---

## 📋 STEP 3: Google Spreadsheet Format Reference

In your Google Sheet, configure the schedule using the following column format:

| User | Device | Day | Start Time | End Time | Allowed | Max Minutes | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `*` | `*` | `Monday-Friday` | `16:00` | `20:00` | `TRUE` | `120` | Weekday homework & screen time |
| `*` | `*` | `Saturday-Sunday`| `10:00 AM` | `12:30 PM` | `TRUE` | `150` | Weekend morning session |
| `*` | `*` | `Saturday-Sunday`| `4:00 PM` | `8:30 PM` | `TRUE` | `180` | Weekend evening session |
| `himanshu` | `optiplex-3050` | `Friday` | `15:00` | `21:00` | `TRUE` | `180` | Desktop gaming extended reward |
| `himanshu` | `laptop` | `Friday` | `15:00` | `18:00` | `TRUE` | `60` | Laptop study session only |
| `himanshi` | `*` | `Sunday` | `2:00 PM` | `7:00 PM` | `TRUE` | `120` | Sunday afternoon gaming |
| `*` | `*` | `*` | `21:00` | `07:00` | `FALSE` | | Bedtime - Access blocked |

### Column Definitions:
- **`User`**: Child's Ubuntu username (e.g. `himanshu`), or `*` / `all` for all children.
- **`Device`** *(optional)*: Computer name / hostname (e.g. `optiplex-3050`, `laptop`).
  - **Omitted column, empty cell, or `*`**: Applies to **ALL devices** (100% backward-compatible).
  - **Specific device name**: Only applies when the child logs into that specific machine.
  - **Multiple devices**: Separate by commas (e.g. `optiplex-3050, study-laptop`).
  - **Finding your device name**: Run `hostname` or `parentalcontrol list-users`.
- **`Day`**: `Monday`, `Tuesday`, `Mon-Fri`, `Weekday`, `Weekend`, `Saturday,Sunday`, `All`, or date `YYYY-MM-DD`.
- **`Start Time` / `End Time`**: 24-hour (`16:00`) or 12-hour (`4:00 PM`).
- **`Allowed`**: `TRUE` (allowed) or `FALSE` (lockout).
- **`Max Minutes`** *(optional)*: Daily screen time quota in minutes or hours (e.g. `120` or `2h`).
- **`Message`** *(optional)*: Custom note displayed to the child on screen.


---

## 🔄 Automatic Upgrades via `sudo apt update` & `sudo apt upgrade`

You do **not** need to manually upgrade this application. 

The installer registers APT hooks at `/etc/apt/apt.conf.d/99parentalcontrol`. Whenever you run system updates on Ubuntu:

```bash
sudo apt update
# or
sudo apt upgrade
```

APT will **automatically**:
1. Pull the latest release from Git.
2. Sync dependencies with `uv`.
3. Seamlessly restart the `parental-control.service` system daemon.

*(Optional manual upgrade command is also available: `sudo parentalcontrol update`)*.

---

## 🔒 Features & Architecture

- 🛡️ **Tamper-Proof & Non-Bypassable**: Runs as `root` via systemd. Children cannot kill, stop, or disable the daemon from their user accounts.
- 👥 **Multi-User & Multi-Session**: Automatically discovers when a child logs in (Wayland, X11, or TTY), evaluates their permitted hours, and monitors all active user sessions simultaneously.
- ⛔ **Instant Login Block**: If outside permitted hours, displays a non-closable warning countdown and terminates the desktop session (`loginctl terminate-session`).
- 🔄 **Live Remote Sync**: Periodically re-syncs with your Google Sheet — parents can extend time, adjust rules, or lock access from their phone in real time!
- ⏳ **Native Multi-Stage Desktop Prompts**:
  - **30 minutes remaining**: Desktop notification (`⏳ 30 Minutes Remaining`).
  - **20 minutes remaining**: Desktop notification (`⏳ 20 Minutes Remaining`).
  - **10 minutes remaining**: Critical notification + audio chime + modal popup prompt to save open games and homework.
  - **5 minutes & 2 minutes**: Urgent warning + audio chime.
  - **0 minutes (Time Expired)**: 30-second animated countdown bar before auto-signout.
- 📴 **Offline Resilience & Cache**: Caches the schedule locally (`/etc/parental-control/schedule_cache.json`) so WiFi disconnects won't disrupt legitimate scheduled hours.
- ⭐ **Parent Admin Exemption**: Parent accounts (e.g. `atul`, `root`, `admin`) are never restricted.

---

## 🕹️ CLI Commands Reference

| Action | Command |
| :--- | :--- |
| **Check application version** | `parentalcontrol -v` or `parentalcontrol --version` |
| **List system users for spreadsheet** | `parentalcontrol list-users` |
| **Export system users as CSV rows** | `parentalcontrol list-users --csv` |
| **Check service status & active sessions** | `parentalcontrol service-status` |
| **View live system logs** | `sudo journalctl -u parental-control.service -f` |
| **Restart system service** | `sudo systemctl restart parental-control.service` |
| **Preview & validate Google Sheet** | `parentalcontrol test-sheet --url "<SHEET_URL>"` |
| **Check schedule status for a user** | `parentalcontrol status --user himanshu` |
| **Dry-run login test** | `parentalcontrol check --dry-run --user himanshu` |
| **Manual upgrade check** | `sudo parentalcontrol update` |
| **Uninstall system service & APT hook** | `sudo parentalcontrol service-uninstall` |
| **Run unit tests** | `uv run pytest -v` |


---

## ⚙️ Configuration File (`/etc/parental-control/config.yaml`)

```yaml
device_name: null            # Optional custom name (defaults to system hostname e.g. optiplex-3050)

google_sheet:
  url: "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
  sheet_name: null
  sync_interval_minutes: 3   # Check for spreadsheet updates every N minutes (e.g. set to 1 for faster sync)

rules:
  target_users:
    # Explicit child usernames (wildcard '*' is NOT allowed in system service mode):
    - "himanshu"
    - "himanshi"
  exempt_users:
    - "atul"      # Parent admin account (never restricted)
    - "root"
    - "admin"
    - "parent"
    - "gdm"       # Display manager greeters (must remain exempt)
    - "lightdm"
    - "sddm"
  offline_policy: "allow_cached"
  offline_grace_minutes: 15

warnings:
  intervals_minutes:
    - 30
    - 20
    - 10
    - 5
    - 2
  show_notifications: true
  show_modal_prompts: true
  play_sound: true

enforcement:
  termination_grace_seconds: 30
  login_denial_grace_seconds: 15
```

### ⏱️ How Long Do Spreadsheet Changes Take to Apply?
- **Automatic Sync**: By default, the daemon re-syncs with your Google Sheet every **3 minutes** (`sync_interval_minutes: 3`). Any changes made in the sheet (e.g. extending time or blocking access) take effect within 3 minutes automatically.
- **Faster Sync**: To sync faster, change `sync_interval_minutes: 1` in `/etc/parental-control/config.yaml` to refresh every 60 seconds.
- **Instant Apply (0 seconds)**: To apply changes immediately without waiting:
  ```bash
  sudo systemctl restart parental-control.service
  ```

---

## ⚠️ Known Issue & Safety Guardrails

### GDM Greeter & Wildcard `target_users: ['*']`
- **What happened**: Ubuntu's login screen (GNOME Display Manager) runs as the system user `gdm` (UID 128). If `target_users` in `/etc/parental-control/config.yaml` was set to `*` without explicitly exempting `gdm`, the background daemon would detect the login greeter as an unauthorized user session outside scheduled hours. It would play warning beeps (`canberra-gtk-play -i dialog-warning`) and terminate `gdm`. When a user attempted to log in during this crash loop, the user's session was created in the background while GDM restarted, causing the error **"session already active"** and blocking logins across reboots.
- **Automatic Service Halting on Wildcard**: The system service **automatically halts and refuses to start** (`sys.exit(1)`) if `target_users` contains `*` or `all` or is left empty. This prevents misconfiguration from ever breaking system login.
- **Built-in System Account Protection**: All system accounts (`UID < 1000`) and display manager users (`gdm`, `gdm3`, `lightdm`, `sddm`, `daemon`, `nobody`) are permanently exempt. The service will refuse to track, alert, or terminate any system user.
- **Resolution**: Specify explicit child usernames in `rules.target_users` (e.g. `['himanshu', 'himanshi']`) and ensure parent admins and display managers (`gdm`, `lightdm`, `sddm`) are in `rules.exempt_users`.

### Ubuntu Release Upgrades & Self-Healing Launcher (v1.0.3)
- **What happened**: When upgrading Ubuntu between major releases (for example Ubuntu 24.04 LTS to Ubuntu 26.04 LTS or intermediate releases), the default system Python version changes (e.g., Python 3.12 is replaced by Python 3.14). Because standard virtual environments pin the interpreter symlink to the previous Python binary, running the CLI or system service resulted in `bash: /usr/local/bin/parentalcontrol: /opt/parental-control/.venv/bin/python3: bad interpreter: No such file or directory`.
- **Self-Healing Architecture (v1.0.3)**:
  1. **Resilient Shell Wrapper**: `/usr/local/bin/parentalcontrol` is deployed as an auto-healing launcher script rather than a direct symlink. On every invocation, it verifies if the virtualenv interpreter can execute Python code. If broken or missing, it automatically invokes `uv venv --clear --python /usr/bin/python3 && uv sync --quiet` to rebuild the venv instantly with the host's new Python version.
  2. **APT Post-Invoke Hook**: An automatic hook is registered at `/etc/apt/apt.conf.d/99parentalcontrol` which triggers venv self-healing automatically after any `apt-get upgrade` or release upgrade transaction.
  3. **System Service Resilience**: The systemd service unit executes via the self-healing launcher, ensuring background service recovery across OS upgrades without manual intervention.
### Safe Lockout Screen & Parent Extension (v1.0.4)
- **Work Preservation**: When a child's session reaches timeout (or login outside schedule), the application does **not** abruptly kill processes or break ongoing work (such as long-running terminal commands, 30-minute builds, renders, or downloads).
- **Always-on-Top Lockout Overlay**: Displays a fullscreen, modal GTK3 overlay that blocks keyboard/mouse access to other applications while background tasks continue executing safely.
- **Two Exclusive Actions**:
  1. **Log Out Now**: Voluntarily ends the session cleanly.
  2. **Parent Extension / Temporary Allow**: The parent selects their account (from `rules.exempt_users`, e.g. `atul`), selects an extension duration (15m, 30m, 45m, 1h, 2h, or Rest of Today), and enters their password.
- **System PAM Authentication**: Authenticated securely via Ubuntu's PAM stack (`libpam.so.0`). If valid, grants the temporary override and dismisses the overlay without restarting the desktop.
- **Anti-Tampering Watchdog**: If the child attempts to kill or terminate the lockout process without parent authorization, the root system daemon instantly detects the breach and terminates the session (`loginctl terminate-session`).
- **Interactive Testing & Overrides**:
  - Test overlay on desktop: `parentalcontrol test-lockout`
  - Manage overrides via CLI: `parentalcontrol override --user himanshu --minutes 30` (or `--list`, `--revoke`)

---

## 🛠️ Manual Installation (from Source)

```bash
# 1. Clone repository
git clone https://github.com/atulmahankal/UbuntuParentalControl.git /opt/parental-control
cd /opt/parental-control

# 2. Install Astral uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# 3. Sync dependencies
uv sync

# 4. Install and enable the system service
sudo uv run parentalcontrol service-install --url "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing"
```

---

## 🧪 Automated Testing

Run the 39 automated unit and integration tests:
```bash
uv run pytest -v
```
