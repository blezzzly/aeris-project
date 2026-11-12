#!/bin/bash
# ============================================================
# AERIS Ground Station — Startup Script
# Usage: ./start_aeris.sh  (from /home/piaeris/aeris-project)
# 
# Starts all AERIS services without reinstalling or rebuilding.
# Safe to run multiple times (idempotent).
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
fail() { echo -e "${RED}  ❌ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }
info() { echo -e "${BLUE}  ℹ️  $1${NC}"; }

PROJECT_DIR="/home/piaeris/aeris-project"
BACKEND_DIR="$PROJECT_DIR/ground-station/backend"
ENV_FILE="/etc/aeris/aeris.env"

# Resolve the backend port from the single source of truth instead of guessing.
# (A hardcoded port here previously probed 8000 while the service listened on 80,
#  which is what produced "Backend health check failed / Response: FAILED".)
resolve_port() {
    local port=""
    [ -f "$ENV_FILE" ] && port="$(grep -E '^AERIS_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    if [ -z "$port" ]; then
        port="$(systemctl show aeris-backend -p Environment --value 2>/dev/null \
                | tr ' ' '\n' | grep -E '^AERIS_PORT=' | head -1 | cut -d= -f2 || true)"
    fi
    [ -z "$port" ] && port="$(grep -E '^Environment=AERIS_PORT=' "$PROJECT_DIR/ground-station/aeris-backend.service" 2>/dev/null | head -1 | cut -d= -f2 || true)"
    [ -z "$port" ] && port=80
    printf '%s' "$port"
}

BACKEND_PORT="$(resolve_port)"
BACKEND_URL="http://localhost:${BACKEND_PORT}"
DASHBOARD_URL="http://192.168.4.1"

# ── Root check ──────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}This script requires sudo. Run: sudo ./start_aeris.sh${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}=========================================="
echo -e "  AERIS Ground Station — Startup"
echo -e "==========================================${NC}"
echo ""
echo "  Backend port: ${BACKEND_PORT}"

# ── Step 1: NetworkManager ─────────────────────────────────
echo "  [1/7] Checking NetworkManager..."
if ! systemctl is-active --quiet NetworkManager; then
    info "Starting NetworkManager..."
    systemctl start NetworkManager
    sleep 2
fi

if systemctl is-active --quiet NetworkManager; then
    ok "NetworkManager is running"
else
    fail "NetworkManager failed to start"
    echo "  Fix: sudo systemctl start NetworkManager"
    exit 1
fi

# ── Step 2: Hotspot ─────────────────────────────────────────
echo "  [2/7] Checking Wi-Fi hotspot..."

# Remove the "disabled" marker so the guard keeps the hotspot up.
rm -f /var/lib/aeris/hotspot-disabled

# Check if aeris-hotspot connection exists
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -q "^aeris-hotspot$"; then
    fail "aeris-hotspot connection not found"
    echo "  Run setup first: sudo bash $PROJECT_DIR/setup_aeris.sh"
    exit 1
fi

# Check if hotspot is active
HOTSPOT_ACTIVE=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep "aeris-hotspot" || true)
if [ -z "$HOTSPOT_ACTIVE" ]; then
    info "Activating aeris-hotspot..."
    if ! nmcli con up aeris-hotspot 2>/dev/null; then
        fail "Failed to activate hotspot"
        echo "  Fix: sudo nmcli con up aeris-hotspot"
        exit 1
    fi
    sleep 2
fi
ok "Hotspot aeris-hotspot is active"

# ── Step 3: Verify 192.168.4.1 ────────────────────────────
echo "  [3/7] Verifying 192.168.4.1..."

if ip addr show | grep -q "192.168.4.1"; then
    ok "192.168.4.1 is configured"
else
    warn "192.168.4.1 not found on any interface"
    warn "Hotspot may need a moment to assign IP. Waiting 5s..."
    sleep 5
    if ip addr show | grep -q "192.168.4.1"; then
        ok "192.168.4.1 is now configured"
    else
        fail "192.168.4.1 still not found"
        echo "  Fix: sudo nmcli con down aeris-hotspot && sudo nmcli con up aeris-hotspot"
        exit 1
    fi
fi

# ── Step 4: Boot persistence ────────────────────────────────
echo "  [4/7] Checking boot units..."

# Every unit that has to come back after a power cut.
for unit in aeris-backend aeris-hotspot-guard aeris-boot-check aeris.target aeris-watchdog.timer; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        ok "$unit enabled at boot"
    else
        info "Enabling $unit..."
        systemctl enable "$unit" 2>/dev/null || true
    fi
done

# ── Step 5: Backend service ─────────────────────────────────
echo "  [5/7] Checking backend service..."

if ! systemctl is-active --quiet aeris-backend; then
    info "Starting aeris-backend..."
    systemctl start aeris-backend
    sleep 3
fi

if systemctl is-active --quiet aeris-backend; then
    ok "Backend service is running"
else
    fail "Backend service failed to start"
    echo "  Logs: sudo journalctl -u aeris-backend -n 20"
    exit 1
fi

# The self-healing watchdog repairs the stack between reboots.
if ! systemctl is-active --quiet aeris-watchdog.timer; then
    info "Starting aeris-watchdog.timer..."
    systemctl start aeris-watchdog.timer 2>/dev/null || true
fi
systemctl is-active --quiet aeris-watchdog.timer \
    && ok "Watchdog timer is running (self-healing check every 5 min)" \
    || warn "Watchdog timer is not running - check: sudo systemctl status aeris-watchdog.timer"

# ── Step 6: Health check ────────────────────────────────────
echo "  [6/7] Checking backend health..."

HEALTH_OK=false
for i in 1 2 3 4 5; do
    HEALTH=$(curl -s "$BACKEND_URL/health" 2>/dev/null || echo "FAILED")
    if echo "$HEALTH" | grep -q '"status":"ok"'; then
        HEALTH_OK=true
        break
    fi
    sleep 1
done

if [ "$HEALTH_OK" = true ]; then
    ok "Backend health: OK"
else
    fail "Backend health check failed"
    echo "  Response: $HEALTH"
    echo "  Logs: sudo journalctl -u aeris-backend -n 20"
    exit 1
fi
# ── Step 7: Frontend check ──────────────────────────────────
echo "  [7/7] Checking frontend..."

TITLE=$(curl -s "$BACKEND_URL/" 2>/dev/null | grep -o "<title>[^<]*</title>" || echo "NONE")
if [ "$TITLE" = "<title>AERIS</title>" ]; then
    ok "Frontend served correctly ($TITLE)"
else
    warn "Frontend title not found: $TITLE"
    warn "Frontend may need rebuild: cd $PROJECT_DIR/ground-station/frontend && npm run build"
fi

# ── Summary ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=========================================="
echo -e "  ✅ AERIS IS READY"
echo -e "==========================================${NC}"
echo ""
echo "  Dashboard:  http://192.168.4.1"
echo "  mDNS:       http://aeris.local"
echo "  Health:     $BACKEND_URL/health"
echo "  Token:      sudo cat $PROJECT_DIR/ground-station/backend/.aeris_auth_token"
echo "  Hardware:   $BACKEND_URL/api/hardware"
echo ""
echo "  Backend:    $(systemctl is-active aeris-backend)"
echo "  Hotspot:    $(nmcli -t -f NAME connection show --active | grep aeris-hotspot || echo 'active')"
echo "  IP:         192.168.4.1"
echo ""
echo -e "${BLUE}  To stop:  sudo ./stop_aeris.sh${NC}"
echo -e "${BLUE}  Logs:     sudo journalctl -u aeris-backend -f${NC}"
echo "=========================================="