# 🛡️ Ubuntu Parental Control (Google Sheets Schedule & Login Guard)

A robust, Python-based parental control and login protection application for **Ubuntu Linux** managed with **`uv`**. It enforces screen time schedules dynamically configured from a **Google Spreadsheet**.

---

## ✨ Features

- 🕒 **Login Access Control**: Checks current day and time against the Google Spreadsheet schedule on login.
- ⛔ **Instant Login Block**: If outside permitted hours, displays a non-closable warning countdown and automatically terminates the session.
- 🔄 **Live Cloud Sync**: Periodically re-checks the Google Spreadsheet during an active session — parents can instantly grant extra time, adjust schedules, or lock access from their phone!
- ⏳ **Smart Multi-Stage Warnings**:
  - **30 minutes left**: Desktop notification.
  - **20 minutes left**: Desktop notification.
  - **10 minutes left**: Urgent desktop notification + audio alert + interactive popup prompt advising children to save games and homework.
  - **5 minutes left**: Urgent notification + audio chime.
  - **0 minutes left**: 30-second animated countdown bar before auto-signout.
- 📴 **Offline Grace & Cache**: Caches the schedule locally so internet outages or disconnected WiFi won't lock kids out during their legitimate scheduled hours.
- 👨‍👩‍👧 **Multi-Child & Role Support**: Specify rules per child username, default rules (`*`), and exempt parent/admin accounts (`atul`, `root`).
- ⚡ **Zero-API Setup Option**: Supports direct Google Sheet sharing links (`Anyone with the link can view`), as well as Google Cloud Service Accounts for private sheets.

---

## 📋 Google Spreadsheet Format

Create a Google Sheet with the following columns (case-insensitive):

| User | Day | Start Time | End Time | Allowed | Max Minutes | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `*` | `Monday-Friday` | `16:00` | `20:00` | `TRUE` | `120` | Weekday homework & screen time |
| `*` | `Saturday-Sunday`| `10:00 AM` | `12:30 PM` | `TRUE` | `150` | Weekend morning session |
| `*` | `Saturday-Sunday`| `4:00 PM` | `8:30 PM` | `TRUE` | `180` | Weekend evening session |
| `child1` | `Friday` | `15:00` | `21:00` | `TRUE` | `180` | Friday extended reward time |
| `child2` | `Sunday` | `2:00 PM` | `7:00 PM` | `TRUE` | `120` | Sunday afternoon |
| `*` | `*` | `21:00` | `07:00` | `FALSE` | | Bedtime - Access blocked |

### Column Details:
- **`User`**: Linux username (e.g. `child1`), or `*` / `all` for all children.
- **`Day`**: `Monday`, `Tuesday`, `Mon-Fri`, `Weekday`, `Weekend`, `Saturday,Sunday`, `All`, or specific date `YYYY-MM-DD`.
- **`Start Time` / `End Time`**: 24-hour (`16:00`) or 12-hour (`4:00 PM`).
- **`Allowed`**: `TRUE` (allowed) or `FALSE` (lockout).
- **`Max Minutes`** *(optional)*: Daily screen time quota in minutes or hours (e.g. `120` or `2h`).
- **`Message`** *(optional)*: Custom note displayed to the child on screen.

---

## 🚀 Quick Start

### 1. Prerequisites
Ensure `uv` is installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### 2. Setup Google Sheet
1. Create a spreadsheet on [Google Sheets](https://sheets.new).
2. You can generate a template CSV using:
   ```bash
   uv run parentalcontrol create-template
   ```
3. Import the CSV or set up your columns, then click **Share** (top right) ➔ Set **General access** to **Anyone with the link can view** ➔ Copy link.

### 3. Configure & Install
Run the interactive setup wizard:
```bash
uv run parentalcontrol setup --url "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit?usp=sharing"
```
The wizard will:
- Save configuration to `~/.config/parental-control/config.yaml`
- Set targeted child accounts & exempt accounts
- Install the XDG desktop autostart entry (`~/.config/autostart/parental-control.desktop`)

---

## 🛠️ CLI Commands Reference

| Command | Description |
| :--- | :--- |
| `uv run parentalcontrol status` | View current access status, remaining time today, and active schedule |
| `uv run parentalcontrol check --dry-run` | Test login permission check without terminating session |
| `uv run parentalcontrol monitor` | Run background session monitoring daemon |
| `uv run parentalcontrol test-sheet` | Test Google Sheet connectivity and preview parsed schedule table |
| `uv run parentalcontrol create-template` | Generate a sample schedule CSV file |
| `uv run parentalcontrol setup` | Re-run setup wizard |
| `uv run parentalcontrol install-autostart` | Install autostart entry for current or specified user |
| `uv run parentalcontrol uninstall-autostart` | Remove autostart entry |

---

## ⚙️ Configuration File (`~/.config/parental-control/config.yaml`)

```yaml
google_sheet:
  url: "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
  sheet_name: null
  sync_interval_minutes: 3

rules:
  target_users:
    - "child1"
    - "child2"
  exempt_users:
    - "atul"
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

## 🧪 Running Tests

Execute the automated test suite with:
```bash
uv run pytest -v
```
