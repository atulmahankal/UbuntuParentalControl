# 🛡️ Ubuntu Parental Control (System Service & Google Sheets Schedule)

A system-level parental control daemon and login protection application for **Ubuntu Linux** managed with **`uv`**. It runs securely as a **systemd system service** (`parental-control.service`), enforcing screen time schedules dynamically configured from a **Google Spreadsheet**.

---

## 🔒 Why System Service?

- 🛡️ **Tamper-Proof & Non-Bypassable**: Runs as `root` in the background. Children cannot terminate, disable, or kill the service from their user accounts.
- 👥 **Multi-Session & Multi-User**: Automatically detects whenever a child logs in (Wayland, X11, or TTY), evaluates their permitted hours, and monitors all active user sessions simultaneously.
- ⛔ **Enforced Login Lockout**: If a child logs in outside scheduled hours, the service immediately displays a warning countdown and terminates their desktop session.
- 🔄 **Live Remote Sync**: Periodically re-syncs with your Google Sheet — parents can extend time, adjust rules, or lock access from their phone in real time!
- ⏳ **Native Desktop Prompts**: Injects countdown notifications and interactive modal dialogs directly into the child's active GUI session:
  - **30 minutes remaining**: Desktop notification.
  - **20 minutes remaining**: Desktop notification.
  - **10 minutes remaining**: Critical notification + audio chime + modal popup prompt to save open games and homework.
  - **5 minutes & 2 minutes**: Urgent warning + audio chime.
  - **0 minutes (Time Expired)**: 30-second animated countdown bar before auto-signout.

---

## 📋 Google Spreadsheet Template

Create a Google Sheet with the following columns (or import [`google_spreadsheet_template.csv`](./google_spreadsheet_template.csv)):

| User | Day | Start Time | End Time | Allowed | Max Minutes | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `*` | `Monday-Friday` | `16:00` | `20:00` | `TRUE` | `120` | Weekday homework & screen time |
| `*` | `Saturday-Sunday`| `10:00 AM` | `12:30 PM` | `TRUE` | `150` | Weekend morning session |
| `*` | `Saturday-Sunday`| `4:00 PM` | `8:30 PM` | `TRUE` | `180` | Weekend evening session |
| `child1` | `Friday` | `15:00` | `21:00` | `TRUE` | `180` | Friday reward extended time |
| `child2` | `Sunday` | `2:00 PM` | `7:00 PM` | `TRUE` | `120` | Sunday afternoon gaming |
| `*` | `*` | `21:00` | `07:00` | `FALSE` | | Bedtime - Access blocked |

### Column Details:
- **`User`**: Child's Ubuntu username (e.g. `child1`), or `*` / `all` for all children.
- **`Day`**: `Monday`, `Tuesday`, `Mon-Fri`, `Weekday`, `Weekend`, `Saturday,Sunday`, `All`, or date `YYYY-MM-DD`.
- **`Start Time` / `End Time`**: 24-hour (`16:00`) or 12-hour (`4:00 PM`).
- **`Allowed`**: `TRUE` (allowed) or `FALSE` (lockout).
- **`Max Minutes`** *(optional)*: Daily screen time quota (e.g. `120` or `2h`).
- **`Message`** *(optional)*: Custom note displayed to the child on screen.

> **Sharing Setup**: In Google Sheets, click **Share** (top right) ➔ Set **General access** to **Anyone with the link can view** ➔ Copy link.

---

## 🚀 Installation & System Service Setup

### 1. Prerequisites
Ensure `uv` is installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### 2. Install Dependencies
```bash
uv sync
```

### 3. Install & Start the System Service
Run the installer with `sudo`:
```bash
sudo uv run parentalcontrol service-install --url "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing"
```

This will automatically:
1. Save the system configuration to `/etc/parental-control/config.yaml`.
2. Generate the systemd service `/etc/systemd/system/parental-control.service`.
3. Enable and start the service with `systemctl enable --now parental-control.service`.

---

## 🛠️ Service Management Commands

| Action | Command |
| :--- | :--- |
| **Check service status & active sessions** | `uv run parentalcontrol service-status` |
| **View live system logs** | `sudo journalctl -u parental-control.service -f` |
| **Restart system service** | `sudo systemctl restart parental-control.service` |
| **Uninstall system service** | `sudo uv run parentalcontrol service-uninstall` |
| **Preview Google Sheet schedule** | `uv run parentalcontrol test-sheet --url "<SHEET_URL>"` |
| **Check access status for a user** | `uv run parentalcontrol status --user child1` |
| **Run unit & integration tests** | `uv run pytest -v` |

---

## ⚙️ System Configuration (`/etc/parental-control/config.yaml`)

```yaml
google_sheet:
  url: "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
  sheet_name: null
  sync_interval_minutes: 3

rules:
  target_users:
    - "*"         # Or specify specific child accounts: ["child1", "child2"]
  exempt_users:
    - "atul"      # Parent admin account is never restricted
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

## 🧪 Testing

Run the automated test suite:
```bash
uv run pytest -v
```
