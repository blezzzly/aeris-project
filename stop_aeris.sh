#!/bin/bash
# ============================================================
# AERIS Ground Station — Stop Script
# Usage: sudo ./stop_aeris.sh
# 
# Safely stops AERIS services WITHOUT deleting hotspot config.
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️  $1${NC}"; }

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Requires sudo: sudo ./stop_aeris.sh${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}=========================================="
echo -e "  AERIS Ground Station — Stop"
echo -e "==========================================${NC}"
echo ""

# Stop the hotspot guard first, otherwise it re-activates the hotspot a few
# seconds later and "stop" would have no lasting effect.
echo "  Stopping hotspot guard..."
systemctl stop aeris-hotspot-guard 2>/dev/null || true
systemctl disable aeris-hotspot-guard 2>/dev/null || true
# Marker so the guard stays hands-off if it is started again.
mkdir -p /var/lib/aeris
touch /var/lib/aeris/hotspot-disabled
ok "Hotspot guard stopped"

# Stop the self-healing watchdog first, otherwise it would immediately
# restart the backend we are about to stop.
echo "  Stopping watchdog timer..."
systemctl stop aeris-watchdog.timer 2>/dev/null || true
systemctl disable aeris-watchdog.timer 2>/dev/null || true
ok "Watchdog timer stopped"

# Stop backend service
echo "  Stopping backend service..."
if systemctl is-active --quiet aeris-backend; then
    systemctl stop aeris-backend
    ok "Backend service stopped"
else
    warn "Backend service was not running"
fi

# Stop the aggregate target and the boot check (the boot check is a oneshot,
# so it is only stopped if it happens to be running).
systemctl stop aeris.target 2>/dev/null || true
systemctl disable aeris.target 2>/dev/null || true
systemctl stop aeris-boot-check.service 2>/dev/null || true
systemctl disable aeris-boot-check 2>/dev/null || true
ok "Aggregate target and boot check stopped"

# Stop hotspot (but don't delete config)
echo "  Stopping hotspot..."
if nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "aeris-hotspot"; then
    nmcli con down aeris-hotspot 2>/dev/null || true
    ok "Hotspot stopped (config preserved)"
else
    warn "Hotspot was not active"
fi

echo ""
echo -e "${GREEN}  AERIS stopped. Config preserved.${NC}"
echo "  To start again: sudo ./start_aeris.sh  (or: sudo ./aeris.sh for a full repair)"
echo "=========================================="