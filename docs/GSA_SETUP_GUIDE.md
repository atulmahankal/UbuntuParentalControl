# Google Service Account (GSA) Integration Guide

This guide explains how to set up a **Google Service Account** for Parental Control.

---

## 1. Why is a Service Account Needed?

Parental Control supports two modes of Google Sheets interaction:

1. **Read-Only Public Mode (Without GSA)**:
   - Schedule and App Limits can be read from a publicly viewable Google Sheet (`Anyone with the link can view`).
   - Does **not** require any API credentials.
   - **Limitation**: Cannot write live application usage data back to the sheet.

2. **Full Read/Write Mode (With GSA)**:
   - Reads private or shared Google Sheets without making them publicly accessible.
   - Automatically synchronizes children's running application activity into the **`Apps Usages`** tab.
   - Enables real-time remote monitoring from any smartphone, tablet, or PC via Google Sheets.

---

## 2. Step-by-Step Setup in Google Cloud Console

### Step 1: Create a Google Cloud Project
1. Open the [Google Cloud Console](https://console.cloud.google.com/) or navigate directly to the [New Project Creation Page](https://console.cloud.google.com/projectcreate).
2. Log in with your parent Google Account.
3. In the project name field, enter `Parental-Control`.
4. Click **Create** and verify that your new project is selected in the top project dropdown.

### Step 2: Enable Google Sheets & Drive APIs
1. Open the [Google Sheets API Library Page](https://console.cloud.google.com/apis/library/sheets.googleapis.com).
2. Click **Enable**.
3. Open the [Google Drive API Library Page](https://console.cloud.google.com/apis/library/drive.googleapis.com).
4. Click **Enable**.
*(You can also browse the [Google Cloud API Library](https://console.cloud.google.com/apis/library) to search for or manage enabled APIs at any time).*

### Step 3: Create a Service Account
1. Open the [Google Cloud Service Accounts Page](https://console.cloud.google.com/iam-admin/serviceaccounts) (or click [Create Service Account](https://console.cloud.google.com/iam-admin/serviceaccounts/create)).
2. Click **+ Create Service Account**.
3. Fill in the details:
   - **Service account name**: `parental-control-daemon`
   - **Service account ID**: `parental-control-daemon` (auto-filled)
   - **Description**: `Syncs schedule and app limits for Parental Control`
4. Click **Create and Continue**.
5. **Role** (optional): You can leave this blank or choose **Basic ➔ Viewer**. Click **Continue** and then click **Done**.

### Step 4: Generate and Download the JSON Key
1. In the [Service Accounts List](https://console.cloud.google.com/iam-admin/serviceaccounts), click on your newly created service account (e.g. `parental-control-daemon@parental-control-xxxxxx.iam.gserviceaccount.com`).
2. Click on the **Keys** tab at the top.
3. Click **Add Key** ➔ **Create new key**.
4. Select **JSON** format and click **Create**.
5. A `.json` private credentials file will automatically download to your computer (e.g. in your `~/Downloads` folder).

---

## 3. Share Your Google Sheet with the Service Account

1. Open your downloaded `.json` file and copy the `client_email` value (e.g. `parental-control-daemon@parental-control-xxxxxx.iam.gserviceaccount.com`).
2. Open your schedule spreadsheet in [Google Sheets](https://docs.google.com/spreadsheets/).
3. Click the green **Share** button in the top-right corner.
4. Paste the service account email address into the **"Add people and groups"** field.
5. Set the permission role to **Editor** (required for writing live activity records to `Apps Usages`).
6. Uncheck **"Notify people"** and click **Share**.

---

## 4. Install the Key on Your Ubuntu Machine

Installing the credentials is as simple as running a single command:

```bash
sudo parentalcontrol gsa --file ~/Downloads/parental-control-*.json
```

### What this command does automatically:
1. **Validates Credentials**: Inspects the JSON key and verifies required Service Account fields.
2. **Secure Installation**: Copies the key to `/etc/parental-control/service_account.json` with strict permissions (`0600`) and `root:root` ownership.
3. **Config Updates**: Automatically updates `/etc/parental-control/config.yaml`.
4. **Connectivity Check**: Verifies live connection to your Google Spreadsheet.
5. **Auto-creates Missing Tabs**: Checks if `Screen Time`, `Apps Limit`, and `Apps Usages` worksheets exist. If any tab is missing, it offers to create it automatically with default headers and sample rules.
6. **Auto-creates Spreadsheet (If Needed)**: If no spreadsheet URL is configured or the file is missing, it asks permission to create a brand new Google Spreadsheet for you automatically!
7. **Service Refresh**: Restarts the background service (`parental-control.service`) so new credentials take effect immediately.

---

## 5. Multi-Tab Google Sheet Setup

Your Google Sheet can contain up to three tabs:

### Tab 1: `Screen Time`
Controls device login access and overall daily screen time quotas:
* **Headers**: `User`, `Device`, `Day`, `Start Time`, `End Time`, `Allowed`, `Max Minutes`, `Message`

### Tab 2: `Apps Limit`
Controls application restrictions, time windows, and runtime quotas (including Standalone Binaries and AppImages):
* **Headers**: `User`, `App Label`, `Binary / Pattern`, `Device`, `Day`, `Allowed`, `Allowed Window`, `Daily Limit (Min)`, `Session Limit (Min)`, `Message`

#### Example Rules:
| User | App Label | Binary / Pattern | Device | Day | Allowed | Allowed Window | Daily Limit (Min) | Message |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `himanshu` | `Chrome` | `chrome, google-chrome` | `*` | `Monday-Friday` | `TRUE` | `5:00 PM - 8:30 PM` | `60` | `Daily homework & web limit` |
| `himanshu` | `Discord` | `discord` | `*` | `Monday-Thursday` | `FALSE` | | | `Discord blocked on school days` |
| `himanshu` | `Unapproved Binaries`| `*/Downloads/*, *.AppImage` | `*` | `All` | `FALSE` | | | `Unapproved portable executables are blocked` |

### Tab 3: `Apps Usages`
Automatically created and updated by the background service:
* **Headers**: `Date`, `User`, `Device`, `App Label`, `Binary / Pattern`, `Executable Path`, `Minutes Used`, `Daily Limit`, `Remaining`, `Status`, `Last Active`

---

## 6. Verifying the Integration
 
Run the verification commands:

```bash
# 1. Run full system health, key permissions, and spreadsheet check
parentalcontrol recheck

# 2. Test fetching rules from both tabs
parentalcontrol test-sheet

# 3. View active application usage tracked locally
parentalcontrol app-status

# 4. Manually trigger a synchronization to Google Sheets
parentalcontrol sync-usage

# 5. Dry-run test process matching on active user processes
parentalcontrol test-apps --user himanshu
```
