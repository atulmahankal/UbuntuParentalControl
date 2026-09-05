#!/usr/bin/env bash
# ==============================================================================
# Parental Control for Ubuntu - Automated Installer & Updater
# Supports one-line install:
#   curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/main/install.sh | sudo bash
# Or with Google Sheet URL:
#   curl -fsSL https://raw.githubusercontent.com/atulmahankal/UbuntuParentalControl/main/install.sh | sudo SHEET_URL="https://docs.google.com/..." bash
# ==============================================================================

set -euo pipefail

APP_NAME="parentalcontrol"
SERVICE_NAME="parental-control.service"
INSTALL_DIR="/opt/parental-control"
CONFIG_DIR="/etc/parental-control"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
APT_HOOK_FILE="/etc/apt/apt.conf.d/99parentalcontrol"
REPO_URL="${REPO_URL:-https://github.com/atulmahankal/UbuntuParentalControl.git}"
BRANCH="${BRANCH:-main}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 1. Require Root / Sudo
if [ "$(id -u)" -ne 0 ]; then
    log_error "This script must be run as root. Please run with sudo:"
    echo "  sudo bash install.sh"
    exit 1
fi

echo -e "${BOLD}============================================================${NC}"
echo -e "${BOLD}   🛡️  Ubuntu Parental Control - Installer & Auto-Updater    ${NC}"
echo -e "${BOLD}============================================================${NC}\n"

# 2. Install System Dependencies via apt
log_info "Checking and installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true
apt-get install -y -qq \
    curl \
    git \
    python3 \
    python3-venv \
    zenity \
    libnotify-bin \
    libcanberra-gtk3-module \
    libcanberra-gtk-module \
    ca-certificates > /dev/null

log_success "System dependencies installed."

# 3. Install Astral uv if not present
if ! command -v uv &> /dev/null && [ ! -f "/root/.local/bin/uv" ]; then
    log_info "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$HOME/.local/bin:$PATH"
else
    export PATH="/root/.local/bin:$HOME/.local/bin:$PATH"
fi
UV_BIN="$(command -v uv || echo "/root/.local/bin/uv")"
log_success "Using uv binary at: ${UV_BIN}"

# 4. Clone or Update Application Code in /opt/parental-control
if [ -d "${INSTALL_DIR}/.git" ]; then
    log_info "Existing installation detected in ${INSTALL_DIR}. Updating repository..."
    cd "${INSTALL_DIR}"
    git fetch origin "${BRANCH}" --quiet || true
    git reset --hard "origin/${BRANCH}" --quiet || true
    log_success "Updated source code to latest ${BRANCH} commit."
elif [ -d "${INSTALL_DIR}" ]; then
    log_info "Directory ${INSTALL_DIR} exists without git. Backing up..."
    mv "${INSTALL_DIR}" "${INSTALL_DIR}.bak.$(date +%s)"
    log_info "Cloning repository from ${REPO_URL}..."
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    log_success "Cloned repository to ${INSTALL_DIR}."
else
    # Check if installer is executed from within the project directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
    if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/pyproject.toml" ]; then
        log_info "Installing from local directory: ${SCRIPT_DIR}..."
        mkdir -p "${INSTALL_DIR}"
        cp -r "${SCRIPT_DIR}/"* "${INSTALL_DIR}/"
        if [ -d "${SCRIPT_DIR}/.git" ]; then
            cp -r "${SCRIPT_DIR}/.git" "${INSTALL_DIR}/"
        fi
    else
        log_info "Cloning repository from ${REPO_URL}..."
        git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    fi
    log_success "Source code prepared at ${INSTALL_DIR}."
fi

# 5. Build Python Virtual Environment with uv
cd "${INSTALL_DIR}"
log_info "Setting up Python virtual environment and dependencies..."
"${UV_BIN}" sync --frozen --quiet || "${UV_BIN}" sync --quiet
log_success "Virtual environment initialized at ${INSTALL_DIR}/.venv"

# 6. Create resilient self-healing launcher wrapper in /usr/local/bin
mkdir -p /usr/local/bin
cat << 'WRAPPER_EOF' > /usr/local/bin/parentalcontrol
#!/usr/bin/env bash
# Ubuntu Parental Control - Resilient Self-Healing Launcher
# Survives system Python upgrades across Ubuntu releases (e.g. 24.04 -> 24.10 / 26.04)
INSTALL_DIR="/opt/parental-control"
VENV_DIR="${INSTALL_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python3"

# Self-heal virtualenv if python interpreter is broken or missing
if [ ! -x "${VENV_PYTHON}" ] || ! "${VENV_PYTHON}" -c "import sys" >/dev/null 2>&1; then
    UV_BIN="$(which uv 2>/dev/null || echo /usr/local/bin/uv)"
    if [ ! -x "${UV_BIN}" ]; then
        for u_home in /home/* /root; do
            if [ -x "${u_home}/.local/bin/uv" ]; then
                UV_BIN="${u_home}/.local/bin/uv"
                break
            fi
        done
    fi
    SYS_PYTHON="$(which python3 2>/dev/null || echo /usr/bin/python3)"
    if [ -x "${UV_BIN}" ] && [ -d "${INSTALL_DIR}" ] && [ -x "${SYS_PYTHON}" ]; then
        (
            cd "${INSTALL_DIR}" && \
            "${UV_BIN}" venv --clear --python "${SYS_PYTHON}" && \
            "${UV_BIN}" sync --quiet
        ) >/dev/null 2>&1
    fi
fi

if [ -x "${VENV_PYTHON}" ] && "${VENV_PYTHON}" -c "import sys" >/dev/null 2>&1; then
    exec "${VENV_PYTHON}" -m parentalcontrol "$@"
elif [ -x "${UV_BIN}" ] && [ -d "${INSTALL_DIR}" ]; then
    exec "${UV_BIN}" run --directory "${INSTALL_DIR}" parentalcontrol "$@"
else
    echo "❌ Error: Parental Control virtual environment could not be loaded." >&2
    echo "   Please run: cd ${INSTALL_DIR} && uv venv --clear && uv sync" >&2
    exit 1
fi
WRAPPER_EOF
chmod 755 /usr/local/bin/parentalcontrol
log_success "Resilient CLI launcher installed at /usr/local/bin/parentalcontrol"

# 7. Setup Configuration in /etc/parental-control/config.yaml
mkdir -p "${CONFIG_DIR}"
chmod 755 "${CONFIG_DIR}"

# Copy template CSV to /etc/parental-control
if [ -f "${INSTALL_DIR}/google_spreadsheet_template.csv" ]; then
    cp "${INSTALL_DIR}/google_spreadsheet_template.csv" "${CONFIG_DIR}/google_spreadsheet_template.csv"
fi

if [ ! -f "${CONFIG_FILE}" ]; then
    SHEET_URL="${SHEET_URL:-}"
    if [ -z "${SHEET_URL}" ] && [ -t 0 ]; then
        echo -e "\n${YELLOW}Please enter your Google Sheet URL (Shared as 'Anyone with link can view'):${NC}"
        read -r -p "Google Sheet URL: " SHEET_URL || SHEET_URL=""
    fi

    CURRENT_SUDO_USER="${SUDO_USER:-}"
    EXEMPT_USERS="root,admin,parent,gdm,lightdm,sddm"
    if [ -n "${CURRENT_SUDO_USER}" ] && [ "${CURRENT_SUDO_USER}" != "root" ]; then
        EXEMPT_USERS="${EXEMPT_USERS},${CURRENT_SUDO_USER}"
    fi

    # Auto-detect non-sudo human user accounts (UID 1000-59999)
    DETECTED_USERS=$(awk -F: -v su="${CURRENT_SUDO_USER}" '$3 >= 1000 && $3 < 60000 && $1 != su && $7 !~ /nologin|false/ {print $1}' /etc/passwd | tr '\n' ',' | sed 's/,$//')

    TARGET_USERS_LIST="${TARGET_USER:-${DETECTED_USERS}}"
    if [ -z "${TARGET_USERS_LIST}" ] || [ "${TARGET_USERS_LIST}" = "*" ]; then
        if [ -n "${DETECTED_USERS}" ]; then
            TARGET_USERS_LIST="${DETECTED_USERS}"
        fi
    fi

    if [ -n "${TARGET_USERS_LIST}" ] && [ "${TARGET_USERS_LIST}" != "*" ]; then
        TARGET_YAML=$(echo "${TARGET_USERS_LIST}" | tr ',' '\n' | sed 's/^/    - "/; s/$/"/')
    else
        TARGET_YAML="    # - \"child1\""
    fi

    EXEMPT_YAML=$(echo "${EXEMPT_USERS}" | tr ',' '\n' | sed 's/^/    - "/; s/$/"/')

    cat << CFG_EOF > "${CONFIG_FILE}"
google_sheet:
  url: "${SHEET_URL}"
  sheet_name: null
  sync_interval_minutes: 3

rules:
  target_users:
${TARGET_YAML}
  exempt_users:
${EXEMPT_YAML}
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
CFG_EOF
    chmod 644 "${CONFIG_FILE}"
    log_success "Created configuration file at ${CONFIG_FILE}"
else
    log_info "Existing configuration found at ${CONFIG_FILE} (preserved)."
    # If SHEET_URL was passed as an env var, update it
    if [ -n "${SHEET_URL:-}" ]; then
        sed -i "s|url:.*|url: \"${SHEET_URL}\"|" "${CONFIG_FILE}"
        log_info "Updated Google Sheet URL in ${CONFIG_FILE}."
    fi
fi

# 8. Install Systemd Service
log_info "Configuring systemd service..."
cat << SVC_EOF > "${SERVICE_FILE}"
[Unit]
Description=Parental Control Google Sheets Schedule Enforcer & Session Guard
Documentation=https://github.com/atulmahankal/UbuntuParentalControl
After=network.target network-online.target systemd-logind.service
Wants=network-online.target systemd-logind.service

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/local/bin/parentalcontrol run-service --config /etc/parental-control/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
KillMode=process

[Install]
WantedBy=multi-user.target
SVC_EOF

chmod 644 "${SERVICE_FILE}"

# 9. Configure APT Hook so 'sudo apt update' and 'sudo apt upgrade' auto-upgrade Parental Control
if [ -d "/etc/apt/apt.conf.d" ]; then
    cat << 'APT_EOF' > "${APT_HOOK_FILE}"
// Automatically upgrade Parental Control on 'apt update' or 'apt upgrade'
APT::Update::Post-Invoke-Success {
    "if [ -d /opt/parental-control/.git ] && [ -x /usr/local/bin/parentalcontrol ]; then /usr/local/bin/parentalcontrol update --quiet || true; fi";
};
DPkg::Post-Invoke {
    "if [ -d /opt/parental-control/.git ] && [ -x /usr/local/bin/parentalcontrol ]; then /usr/local/bin/parentalcontrol update --quiet || true; fi";
};
APT_EOF
    chmod 644 "${APT_HOOK_FILE}"
    log_success "Configured APT auto-upgrade hook (/etc/apt/apt.conf.d/99parentalcontrol)."
fi


# 10. Enable & Start Systemd Service
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
log_success "Systemd service '${SERVICE_NAME}' enabled and started!"

# 11. Print Completion Message
echo -e "\n${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD}   🎉  Parental Control Successfully Installed & Active!   ${NC}"
echo -e "${BOLD}============================================================${NC}\n"

echo -e "Useful Commands:"
echo -e "  • Check service & session status:  ${BOLD}parentalcontrol service-status${NC}"
echo -e "  • Get user list for spreadsheet:   ${BOLD}parentalcontrol list-users${NC}"
echo -e "  • View live service logs:          ${BOLD}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  • Test Google Sheet connection:    ${BOLD}parentalcontrol test-sheet${NC}"
echo -e "  • Check schedule status:           ${BOLD}parentalcontrol status${NC}"
echo -e "  • Auto-upgrade on APT:             ${BOLD}sudo apt upgrade${NC} (automatic)"
echo -e "  • Edit configuration:              ${BOLD}sudo nano /etc/parental-control/config.yaml${NC}"
echo ""
