#!/bin/bash
# ============================================================
# AERIS Ground Station — Complete Deployment Script
# Usage: sudo bash /home/piaeris/aeris-project/setup_aeris.sh
#
# WARNING: Run via Ethernet SSH only! Do NOT run over Wi-Fi SSH.
#          Hotspot config will change Wi-Fi — Ethernet is required.
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
fail() { echo -e "${RED}  ❌ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
info() { echo -e "${BLUE}  ℹ️  $1${NC}"; }
step() { echo ""; echo -e "${BLUE}[$1/$TOTAL_STEPS] $2${NC}"; }

TOTAL_STEPS=10
ERRORS=0

# ── Helper: fail and exit ────────────────────────────────────
abort() {
    echo ""
    echo -e "${RED}=========================================="
    echo -e "  SETUP FAILED at: $1"
    echo -e "  Reason: $2"
    echo -e "==========================================${NC}"
    echo ""
    echo "  Recovery steps:"
    echo "    1. Check the error message above"
    echo "    2. Fix the issue manually"
    echo "    3. Re-run this script (it is idempotent for most steps)"
    echo "    4. If hotspot broke Wi-Fi: sudo nmcli con delete aeris-hotspot && sudo nmcli radio wifi on"
    echo ""
    exit 1
}

# ── Helper: check command exists ─────────────────────────────
require_cmd() {
    if ! command -v "$1" &>/dev/null; then
        abort "Prerequisite check" "$1 is not installed. Install it first: $2"
    fi
}

# ============================================================
# STEP 0: ROOT CHECK
# ============================================================
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}This script must be run with sudo.${NC}"
    echo "  Usage: sudo bash /home/piaeris/aeris-project/setup_aeris.sh"
    exit 1
fi

# ============================================================
# STEP 1: DETECT PROJECT DIRECTORY
# ============================================================
step 1 "Detecting project directory"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# Verify it's the right project
if [ ! -f "$PROJECT_DIR/ground-station/backend/main.py" ]; then
    # Try common locations
    for candidate in "/home/piaeris/aeris-project" "/home/pi/aeris-project" "/opt/aeris-project"; do
        if [ -f "$candidate/ground-station/backend/main.py" ]; then
            PROJECT_DIR="$candidate"
            break
        fi
    done
fi

if [ ! -f "$PROJECT_DIR/ground-station/backend/main.py" ]; then
    abort "Project detection" "Cannot find AERIS project (ground-station/backend/main.py). Run this script from the project root."
fi

FRONTEND_DIR="$PROJECT_DIR/ground-station/frontend"
BACKEND_DIR="$PROJECT_DIR/ground-station/backend"
SERVICE_FILE="$PROJECT_DIR/ground-station/aeris-backend.service"

echo "  Project dir: $PROJECT_DIR"
echo "  Frontend:    $FRONTEND_DIR"
echo "  Backend:     $BACKEND_DIR"

# Verify required files
for f in \
    "$FRONTEND_DIR/package.json" \
    "$BACKEND_DIR/main.py" \
    "$BACKEND_DIR/requirements.txt" \
    "$SERVICE_FILE"; do
    if [ ! -f "$f" ]; then
        abort "File check" "Missing required file: $f"
    fi
done

# Verify dist will be created (check frontend src exists)
if [ ! -d "$FRONTEND_DIR/src" ]; then
    abort "File check" "Frontend src directory not found: $FRONTEND_DIR/src"
fi

ok "Project directory detected and verified"

# ============================================================
# STEP 2: PREREQUISITE CHECKS
# ============================================================
step 2 "Checking prerequisites"

require_cmd "node" "sudo apt install -y nodejs"
require_cmd "npm" "sudo apt install -y npm"
require_cmd "python3" "sudo apt install -y python3"
require_cmd "pip3" "sudo apt install -y python3-pip"
require_cmd "nmcli" "sudo apt install -y network-manager"

# Check versions
NODE_VER=$(node --version)
NPM_VER=$(npm --version)
PY_VER=$(python3 --version)
echo "  Node: $NODE_VER"
echo "  npm:  $NPM_VER"
echo "  Python: $PY_VER"

# Check NetworkManager is running
if ! systemctl is-active --quiet NetworkManager; then
    warn "NetworkManager is not running. Starting it..."
    systemctl start NetworkManager
    sleep 2
    if ! systemctl is-active --quiet NetworkManager; then
        abort "NetworkManager" "Cannot start NetworkManager. Install it: sudo apt install -y network-manager"
    fi
fi

ok "All prerequisites installed"

# ============================================================
# STEP 3: DETECT WI-FI INTERFACE
# ============================================================
step 3 "Detecting Wi-Fi interface"

WIFI_IFACE=""
# Find wireless interfaces
for iface in $(ls /sys/class/net/); do
    if [ -d "/sys/class/net/$iface/wireless" ]; then
        WIFI_IFACE="$iface"
        break
    fi
done

if [ -z "$WIFI_IFACE" ]; then
    # Fallback: use nmcli
    WIFI_IFACE=$(nmcli -t -f DEVICE,TYPE device status | grep ':wifi$' | head -1 | cut -d: -f1)
fi

if [ -z "$WIFI_IFACE" ]; then
    abort "Wi-Fi detection" "No Wi-Fi interface found. Check hardware: ls /sys/class/net/"
fi

echo "  Wi-Fi interface: $WIFI_IFACE"

# Check if it supports AP mode
AP_SUPPORT=$(iw list 2>/dev/null | grep -A 8 "Supported interface modes" | grep -i "AP" || true)
if [ -z "$AP_SUPPORT" ]; then
    warn "Could not confirm AP mode support via 'iw'. Proceeding anyway (may fail)."
    warn "Verify manually: iw list | grep -A 8 'Supported interface modes'"
else
    ok "Wi-Fi interface $WIFI_IFACE supports AP mode"
fi

# Check current SSH connection to warn user
SSH_CONN=$(who am i 2>/dev/null | grep -o '([0-9.]*' | tr -d '(' || true)
if [ -n "$SSH_CONN" ]; then
    info "Current SSH connection from: $SSH_CONN"
    # Check if we're on Wi-Fi
    SSH_IFACE=$(ip route get "$SSH_CONN" 2>/dev/null | grep -o 'dev [a-z0-9]*' | awk '{print $2}' || true)
    if [ "$SSH_IFACE" = "$WIFI_IFACE" ]; then
        warn "You appear to be connected via Wi-Fi SSH!"
        warn "Hotspot config will disrupt this connection."
        warn "Strongly recommended: use Ethernet SSH instead."
        echo ""
        read -p "  Continue anyway? (y/N): " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            abort "User cancelled" "Re-run via Ethernet SSH for safety."
        fi
    fi
fi

ok "Wi-Fi interface detected: $WIFI_IFACE"

# ============================================================
# STEP 4: CHECK EXISTING HOTSPOT CONFIG
# ============================================================
step 4 "Checking existing network configuration"

EXISTING_HOTSPOT=$(nmcli -t -f NAME,TYPE connection show 2>/dev/null | grep 'aeris-hotspot' || true)
if [ -n "$EXISTING_HOTSPOT" ]; then
    warn "Existing 'aeris-hotspot' connection found."
    read -p "  Overwrite it? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        info "Keeping existing hotspot config. Skipping hotspot setup."
        SKIP_HOTSPOT=true
    else
        SKIP_HOTSPOT=false
    fi
else
    SKIP_HOTSPOT=false
fi

# Check if 192.168.4.1 is already in use
if ip addr show | grep -q "192.168.4.1"; then
    warn "192.168.4.1 is already assigned to an interface."
fi

ok "Network check complete"

# ============================================================
# STEP 5: ASK FOR WI-FI PASSWORD
# ============================================================
step 5 "Wi-Fi hotspot password"

if [ "$SKIP_HOTSPOT" = "true" ]; then
    HOTSPOT_PASS="(existing)"
    info "Using existing hotspot config"
else
    echo "  Enter a secure password for the AERIS-GroundStation Wi-Fi (min 8 chars):"
    read -s -p "  Password: " HOTSPOT_PASS
    echo ""
    if [ ${#HOTSPOT_PASS} -lt 8 ]; then
        abort "Password" "Password must be at least 8 characters"
    fi
    read -s -p "  Confirm password: " HOTSPOT_PASS2
    echo ""
    if [ "$HOTSPOT_PASS" != "$HOTSPOT_PASS2" ]; then
        abort "Password" "Passwords do not match"
    fi
    ok "Password accepted"
fi

HOTSPOT_SSID="AERIS-GroundStation"

# ============================================================
# STEP 6: BUILD FRONTEND
# ============================================================
step 6 "Building frontend"

cd "$FRONTEND_DIR"

echo "  Installing npm dependencies..."
if ! npm install 2>&1 | tail -3; then
    abort "Frontend build" "npm install failed. Check node/npm installation."
fi

echo "  Building React app..."
if ! npm run build 2>&1 | tail -5; then
    abort "Frontend build" "npm run build failed. Check TypeScript errors."
fi

if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
    abort "Frontend build" "Build completed but dist/index.html not found."
fi

ok "Frontend built successfully"

# ============================================================
# STEP 7: INSTALL BACKEND DEPENDENCIES
# ============================================================
step 7 "Installing backend dependencies"

cd "$BACKEND_DIR"

# Create venv if not exists
if [ ! -d "$BACKEND_DIR/venv" ]; then
    echo "  Creating Python virtual environment..."
    python3 -m venv venv
fi

source "$BACKEND_DIR/venv/bin/activate"

echo "  Installing pip packages..."
if ! pip install -r requirements.txt 2>&1 | tail -3; then
    abort "Backend deps" "pip install failed. Check requirements.txt and Python version."
fi

# Verify key packages
python3 -c "import fastapi, uvicorn; print('  fastapi + uvicorn OK')" || \
    abort "Backend deps" "fastapi/uvicorn not installed correctly"

ok "Backend dependencies installed"

# ============================================================
# STEP 8: VERIFY & FIX SYSTEMD SERVICE
# ============================================================
step 8 "Configuring systemd service"

# Resolve the backend port from the unit file / env file (single source of truth).
AERIS_PORT="$(grep -E '^Environment=AERIS_PORT=' "$SERVICE_FILE" 2>/dev/null | head -1 | cut -d= -f2 || true)"
[ -z "$AERIS_PORT" ] && AERIS_PORT="$(grep -E '^AERIS_PORT=' /etc/aeris/aeris.env 2>/dev/null | head -1 | cut -d= -f2 || true)"
[ -z "$AERIS_PORT" ] && AERIS_PORT=80
BACKEND_URL="http://localhost:${AERIS_PORT}"
echo "  Backend port: $AERIS_PORT"

# Check if service file needs path fix
SERVICE_CONTENT=$(cat "$SERVICE_FILE")

# Verify it references correct paths
if echo "$SERVICE_CONTENT" | grep -q "/home/pi/aeris-project"; then
    warn "Service file contains /home/pi path. Fixing to $PROJECT_DIR..."
    sed -i "s|/home/pi/aeris-project|$PROJECT_DIR|g" "$SERVICE_FILE"
fi

# Verify WorkingDirectory and ExecStart
if ! echo "$SERVICE_CONTENT" | grep -q "WorkingDirectory="; then
    abort "Service config" "Service file missing WorkingDirectory"
fi

# Copy to systemd
sudo cp "$SERVICE_FILE" /etc/systemd/system/aeris-backend.service
sudo systemctl daemon-reload
sudo systemctl enable aeris-backend

# Install + enable the hotspot guard so the AP comes back after a reboot
if [ -f "$PROJECT_DIR/aeris-hotspot-guard.service" ]; then
    sudo cp "$PROJECT_DIR/aeris-hotspot-guard.service" /etc/systemd/system/aeris-hotspot-guard.service
    sudo chmod 0755 "$PROJECT_DIR/ground-station/hotspot_guard.sh" 2>/dev/null || true
    sudo systemctl daemon-reload
    sudo systemctl enable aeris-hotspot-guard 2>/dev/null || true
    sudo rm -f /var/lib/aeris/hotspot-disabled
fi

# Restart (stop first if running)
sudo systemctl stop aeris-backend 2>/dev/null || true
sleep 1
sudo systemctl start aeris-backend
sleep 3

# Verify
if ! sudo systemctl is-active --quiet aeris-backend; then
    abort "Service start" "aeris-backend failed to start. Check: sudo journalctl -u aeris-backend -n 20"
fi

# Test health endpoint
HEALTH=$(curl -s "$BACKEND_URL/health" 2>/dev/null || echo "FAILED")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    ok "Backend service running and healthy"
else
    abort "Health check" "Backend not responding on $BACKEND_URL/health. Response: $HEALTH"
fi

# Verify frontend serving
TITLE=$(curl -s "$BACKEND_URL/" 2>/dev/null | grep -o "<title>[^<]*</title>" || echo "NONE")
if [ "$TITLE" = "<title>AERIS</title>" ]; then
    ok "Frontend served correctly ($TITLE)"
else
    warn "Frontend title not found. Got: $TITLE"
    warn "Check that frontend/dist/index.html exists"
fi

# ============================================================
# STEP 9: CONFIGURE HOTSPOT
# ============================================================
step 9 "Configuring Wi-Fi hotspot"

if [ "$SKIP_HOTSPOT" = "true" ]; then
    info "Skipping hotspot (user chose to keep existing)"
else
    # Remove existing aeris-hotspot if present
    sudo nmcli con delete aeris-hotspot 2>/dev/null || true
    sleep 1

    # Create new hotspot
    if ! sudo nmcli con add type wifi ifname "$WIFI_IFACE" con-name aeris-hotspot autoconnect yes \
        wifi.mode ap wifi.ssid "$HOTSPOT_SSID" wifi.band bg wifi.channel 6 \
        wifi.powersave 2 \
        ipv4.method shared ipv4.addresses 192.168.4.1/24 2>&1; then
        abort "Hotspot creation" "Failed to create hotspot connection. Check Wi-Fi interface: $WIFI_IFACE"
    fi

    sudo nmcli con modify aeris-hotspot wifi-sec.key-mgmt wpa-psk
    sudo nmcli con modify aeris-hotspot wifi-sec.psk "$HOTSPOT_PASS"

    if ! sudo nmcli con up aeris-hotspot 2>&1; then
        abort "Hotspot start" "Failed to start hotspot. Check: nmcli con show aeris-hotspot"
    fi

    sleep 2

    # Verify IP
    if ip addr show "$WIFI_IFACE" | grep -q "192.168.4.1"; then
        ok "Hotspot active: $HOTSPOT_SSID on $WIFI_IFACE (192.168.4.1)"
    else
        warn "Hotspot started but 192.168.4.1 not found on $WIFI_IFACE"
        warn "Check: ip addr show $WIFI_IFACE"
    fi
fi

# Disable Wi-Fi power save on the AP (both paths: freshly created or kept).
# Persist it in the profile and enforce it at runtime.
if sudo nmcli -t -f NAME connection show 2>/dev/null | grep -q '^aeris-hotspot$'; then
    sudo nmcli con modify aeris-hotspot 802-11-wireless.powersave 2 >/dev/null 2>&1 \
        || warn "Could not set 802-11-wireless.powersave=2 on aeris-hotspot"
fi
if command -v iw >/dev/null 2>&1 && [ -n "${WIFI_IFACE:-}" ]; then
    sudo iw dev "$WIFI_IFACE" set power_save off >/dev/null 2>&1 \
        || warn "Could not disable Wi-Fi power save on $WIFI_IFACE"
    POWER_SAVE_NOW="$(iw dev "$WIFI_IFACE" get power_save 2>/dev/null | awk -F': ' '{print $2}' | head -1)"
    if [ "$POWER_SAVE_NOW" = "off" ]; then
        ok "Wi-Fi power save is off on $WIFI_IFACE"
    else
        warn "Wi-Fi power save is still '${POWER_SAVE_NOW:-unknown}' on $WIFI_IFACE"
    fi
else
    warn "'iw' is not installed - cannot verify the Wi-Fi power-save state"
fi

# ============================================================
# STEP 10: INSTALL mDNS (OPTIONAL)
# ============================================================
step 10 "Installing mDNS (avahi)"

if command -v avahi-daemon &>/dev/null; then
    info "avahi-daemon already installed"
else
    sudo apt install -y avahi-daemon 2>&1 | tail -2 || warn "avahi install failed (optional, skipping)"
fi
sudo systemctl enable --now avahi-daemon 2>/dev/null || warn "avahi enable failed (optional)"

ok "mDNS configured (aeris.local)"

# ============================================================
# FINAL SUMMARY
# ============================================================
echo ""
echo -e "${GREEN}=========================================="
echo -e "  ✅ AERIS SETUP COMPLETE!"
echo -e "==========================================${NC}"
echo ""
echo "  Project dir:  $PROJECT_DIR"
echo "  Wi-Fi SSID:  $HOTSPOT_SSID"
echo "  Wi-Fi Pass:  $HOTSPOT_PASS"
echo "  Gateway IP:  192.168.4.1"
echo "  Dashboard:   http://192.168.4.1"
echo "  mDNS:        http://aeris.local"
echo ""
echo "  Backend service: sudo systemctl status aeris-backend"
echo "  Backend logs:    sudo journalctl -u aeris-backend -f"
echo "  Hotspot status:  nmcli con show aeris-hotspot"
echo ""
echo -e "${YELLOW}  NEXT STEPS:${NC}"
echo "  1. Reboot: sudo reboot"
echo "  2. Connect laptop to '$HOTSPOT_SSID' Wi-Fi"
echo "  3. Open Chrome -> http://192.168.4.1"
echo "  4. Verify dashboard loads with real data"
echo ""
echo -e "${RED}  RECOVERY (if something breaks):${NC}"
echo "  sudo nmcli con down aeris-hotspot"
echo "  sudo nmcli con delete aeris-hotspot"
echo "  sudo nmcli radio wifi on"
echo "  sudo systemctl restart aeris-backend"
echo "=========================================="