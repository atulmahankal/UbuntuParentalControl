# 🛡️ Ubuntu Parental Control (System Service & Google Sheets Schedule)

A robust, system-level parental control daemon and login protection application for **Ubuntu Linux** managed with **`uv`**. It runs securely as a **systemd service** (`parental-control.service`), enforcing screen time schedules dynamically configured from a **Google Spreadsheet**.

---

## ⚡ One-Line Automatic Installation

You can install and configure Parental Control on Ubuntu with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/atulmahankal/ParentalControl/master/install.sh | sudo bash
```

Or provide your Google Sheet URL directly during installation:

```bash
curl -fsSL https://raw.githubusercontent.com/atulmahankal/ParentalControl/master/install.sh | sudo SHEET_URL="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing" bash
```

### What `install.sh` does automatically:
1. Installs all required Ubuntu packages (`python3`, `git`, `curl`, `zenity`, `libnotify-bin`, `libcanberra-gtk-module`).
2. Installs the Astral **`uv`** package manager.
3. Clones/installs the application to `/opt/parental-control`.
4. Builds the virtual environment and links `/usr/local/bin/parentalcontrol`.
5. Prompts for or saves your Google Sheet URL in `/etc/parental-control/config.yaml`.
6. Configures, enables, and starts the systemd service `/etc/systemd/system/parental-control.service`.

---

## 👥 Getting the System User List for Google Spreadsheet

To view all human user accounts on your Ubuntu computer (so you can copy their exact usernames into your Google Spreadsheet):

```bash
parentalcontrol list-users
```

Or generate **spreadsheet-ready CSV rows** directly:

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

## 📋 Google Spreadsheet Setup

Create a Google Sheet with the following columns (or import [`google_spreadsheet_template.csv`](./google_spreadsheet_template.csv)):

| User | Day | Start Time | End Time | Allowed | Max Minutes | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `*` | `Monday-Friday` | `16:00` | `20:00` | `TRUE` | `120` | Weekday homework & screen time |
| `*` | `Saturday-Sunday`| `10:00 AM` | `12:30 PM` | `TRUE` | `150` | Weekend morning session |
| `*` | `Saturday-Sunday`| `4:00 PM` | `8:30 PM` | `TRUE` | `180` | Weekend evening session |
| `himanshu` | `Friday` | `15:00` | `21:00` | `TRUE` | `180` | Friday reward extended time |
| `himanshi` | `Sunday` | `2:00 PM` | `7:00 PM` | `TRUE` | `120` | Sunday afternoon gaming |
| `*` | `*` | `21:00` | `07:00` | `FALSE` | | Bedtime - Access blocked |

### Column Details:
- **`User`**: Child's Ubuntu username (e.g. `himanshu`), or `*` / `all` for all children.
- **`Day`**: `Monday`, `Tuesday`, `Mon-Fri`, `Weekday`, `Weekend`, `Saturday,Sunday`, `All`, or date `YYYY-MM-DD`.
- **`Start Time` / `End Time`**: 24-hour (`16:00`) or 12-hour (`4:00 PM`).
- **`Allowed`**: `TRUE` (allowed) or `FALSE` (lockout).
- **`Max Minutes`** *(optional)*: Daily screen time quota in minutes or hours (e.g. `120` or `2h`).
- **`Message`** *(optional)*: Custom note displayed to the child on screen.

### Sharing Your Google Sheet:
1. Open your spreadsheet on Google Sheets.
2. Click **Share** (top right) ➔ Change **General access** to **Anyone with the link can view** ➔ Copy the link.
3. Use this link in `parentalcontrol service-install` or in `/etc/parental-control/config.yaml`.

---

## 🔄 Upgrading / Updating the Application

To upgrade the application to the latest release and restart the service, run:

```bash
sudo parentalcontrol update
```

Alternatively, re-running the installation one-liner will automatically pull updates without overwriting your existing `/etc/parental-control/config.yaml`:
```bash
curl -fsSL https://raw.githubusercontent.com/atulmahankal/ParentalControl/master/install.sh | sudo bash
```

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

## 🛠️ Manual Installation (from Source)

If you prefer installing manually from git:

```bash
# 1. Clone repository
git clone https://github.com/atulmahankal/ParentalControl.git /opt/parental-control
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

## 🕹️ CLI Commands Reference

| Action | Command |
| :--- | :--- |
| **List system users for spreadsheet** | `parentalcontrol list-users` |
| **Export system users as CSV rows** | `parentalcontrol list-users --csv` |
| **Check service status & active sessions** | `parentalcontrol service-status` |
| **View live system logs** | `sudo journalctl -u parental-control.service -f` |
| **Upgrade application to latest version** | `sudo parentalcontrol update` |
| **Restart system service** | `sudo systemctl restart parental-control.service` |
| **Preview & validate Google Sheet** | `parentalcontrol test-sheet --url "<SHEET_URL>"` |
| **Check schedule status for a user** | `parentalcontrol status --user himanshu` |
| **Dry-run login test** | `parentalcontrol check --dry-run --user himanshu` |
| **Uninstall system service** | `sudo parentalcontrol service-uninstall` |
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

## 🧪 Automated Testing

Run the automated test suite:
```bash
uv run pytest -v
```
