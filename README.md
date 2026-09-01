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
4. Click **Copy link** (e.g. `https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing`).
5. Save this copied URL — you will supply it in **Step 1** during installation.

> [!IMPORTANT]
> The application only requires **Viewer (Read-Only)** access. It reads the schedule securely over HTTPS and never modifies your spreadsheet.

---

## ⚡ STEP 1: One-Line Automatic Installation

Once your Google Sheet is shared with "Anyone with the link (Viewer)", install Parental Control with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/master/install.sh | sudo SHEET_URL="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing" bash
```

*(Or simply run `curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/master/install.sh | sudo bash` to be prompted interactively for your Google Sheet URL).*

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

| User | Day | Start Time | End Time | Allowed | Max Minutes | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `*` | `Monday-Friday` | `16:00` | `20:00` | `TRUE` | `120` | Weekday homework & screen time |
| `*` | `Saturday-Sunday`| `10:00 AM` | `12:30 PM` | `TRUE` | `150` | Weekend morning session |
| `*` | `Saturday-Sunday`| `4:00 PM` | `8:30 PM` | `TRUE` | `180` | Weekend evening session |
| `himanshu` | `Friday` | `15:00` | `21:00` | `TRUE` | `180` | Friday reward extended time |
| `himanshi` | `Sunday` | `2:00 PM` | `7:00 PM` | `TRUE` | `120` | Sunday afternoon gaming |
| `*` | `*` | `21:00` | `07:00` | `FALSE` | | Bedtime - Access blocked |

### Column Definitions:
- **`User`**: Child's Ubuntu username (e.g. `himanshu`), or `*` / `all` for all children.
- **`Day`**: `Monday`, `Tuesday`, `Mon-Fri`, `Weekday`, `Weekend`, `Saturday,Sunday`, `All`, or date `YYYY-MM-DD`.
- **`Start Time` / `End Time`**: 24-hour (`16:00`) or 12-hour (`4:00 PM`).
- **`Allowed`**: `TRUE` (allowed) or `FALSE` (lockout).
- **`Max Minutes`** *(optional)*: Daily screen time quota in minutes or hours (e.g. `120` or `2h`).
- **`Message`** *(optional)*: Custom note displayed to the child on screen.

---

## 🔄 Automatic Upgrades via `sudo apt upgrade`

You do **not** need to manually upgrade this application. 

The installer registers an APT Post-Invoke hook at `/etc/apt/apt.conf.d/99parentalcontrol`. Whenever you run regular system updates on Ubuntu:

```bash
sudo apt update && sudo apt upgrade
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
google_sheet:
  url: "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
  sheet_name: null
  sync_interval_minutes: 3

rules:
  target_users:
    - "*"         # Wildcard matches all non-exempt users, or specify: ["himanshu", "himanshi"]
  exempt_users:
    - "atul"      # Parent admin account (never restricted)
    - "root"
    - "admin"
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

Run the 22 automated unit and integration tests:
```bash
uv run pytest -v
```
