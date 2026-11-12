#!/bin/bash
# ============================================================
# AERIS Phase 2 deployment - offline reliability
# ============================================================
#
# Applies the approved Phase 2 changes:
#   1. persistent AP power-save disable (NetworkManager profile),
#   2. runtime AP power-save disable (iw),
#   3. restart of the hotspot guard so it enforces power-save off,
#   4. one restart of aeris-backend to load the new WebSocket hub,
#   5. verification of health, dashboard, diagnostics and power save.
#
# The frontend build is already deployed in place (dist/); it needs no root.
#
# Usage:
#   sudo ./deploy_phase2.sh
#
# Rollback:
#   sudo nmcli con modify aeris-hotspot 802-11-wireless.powersave 0
#   sudo iw dev wlan0 set power_save on
#   git switch feature/phase2-offline-reliability
#   git revert <phase2-commit>          # or checkout the previous known-good commit
#   sudo systemctl restart aeris-backend aeris-hotspot-guard
#
# This script never touches GPIO, I2C, SPI, LoRa or RF settings and never
# transmits a LoRa packet.
# ============================================================

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_DIR/ground-station/backend"
FRONTEND_DIR="$REPO_DIR/ground-station/frontend"
VENV_PY="$BACKEND_DIR/venv/bin/python"
TOKEN_FILE="$BACKEND_DIR/.aeris_auth_token"
HOTSPOT_CON="${AERIS_HOTSPOT_CON:-aeris-hotspot}"
LOG_FILE="/var/log/aeris-phase2-deploy.log"
BACKUP_DIR="/var/backups/aeris-phase2-$(date +%Y%m%d-%H%M%S)"

FAILURES=0
say() {
    printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}
ok() {
    say "PASS  $*"
}
warn() {
    say "WARN  $*"
}
fail() {
    say "FAIL  $*"
    FAILURES=$((FAILURES + 1))
}

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must run as root:  sudo $0" >&2
    exit 2
fi

mkdir -p "$BACKUP_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

say "=== AERIS Phase 2 deployment started ==="
say "repo: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown) on $(git -C "$REPO_DIR" branch --show-current 2>/dev/null || echo unknown)"
say "backup dir: $BACKUP_DIR"

# ---------------------------------------------------------------------------
# 0. Preflight. A deployment that cannot start the backend must not begin.
# ---------------------------------------------------------------------------
if [ ! -x "$VENV_PY" ]; then
    fail "backend venv python is missing: $VENV_PY"
    exit 1
fi
if ! "$VENV_PY" -m py_compile "$BACKEND_DIR/main.py" "$BACKEND_DIR/weather_cache.py"; then
    fail "backend Python did not compile - refusing to restart the service"
    exit 1
fi
if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
    fail "frontend build is missing: $FRONTEND_DIR/dist/index.html"
    exit 1
fi
ok "preflight: backend compiles, frontend build present"

# ---------------------------------------------------------------------------
# 1. Record the current state (for rollback and the report).
# ---------------------------------------------------------------------------
{
    echo "== NetworkManager profile before =="
    nmcli -f connection.id,802-11-wireless.powersave connection show "$HOTSPOT_CON" 2>&1 || true
    echo "== runtime before =="
    for iface in /sys/class/net/*/wireless; do
        [ -e "$iface" ] || continue
        dev="$(basename "$(dirname "$iface")")"
        iw dev "$dev" get power_save 2>&1 || true
    done
    echo "== service before =="
    systemctl is-active aeris-backend aeris-hotspot-guard 2>&1 || true
} > "$BACKUP_DIR/before-state.txt" 2>&1
ok "recorded the pre-deployment state in $BACKUP_DIR/before-state.txt"

# ---------------------------------------------------------------------------
# 2. Resolve the AP interface.
# ---------------------------------------------------------------------------
WIFI_IFACE="$(nmcli -g GENERAL.DEVICES connection show "$HOTSPOT_CON" 2>/dev/null | head -1 | tr -d ' ')"
if [ -z "$WIFI_IFACE" ]; then
    for iface in /sys/class/net/*/wireless; do
        [ -e "$iface" ] || continue
        WIFI_IFACE="$(basename "$(dirname "$iface")")"
        break
    done
fi
if [ -z "$WIFI_IFACE" ]; then
    fail "could not determine the Wi-Fi interface"
    exit 1
fi
say "AP interface: $WIFI_IFACE"

# ---------------------------------------------------------------------------
# 3. Disable AP power save - persistent profile + runtime driver state.
#    No connection reactivation: existing clients are not disconnected.
# ---------------------------------------------------------------------------
if nmcli con modify "$HOTSPOT_CON" 802-11-wireless.powersave 2; then
    ok "NetworkManager profile '$HOTSPOT_CON': 802-11-wireless.powersave = 2 (disable)"
else
    fail "could not modify NetworkManager profile '$HOTSPOT_CON'"
fi

if command -v iw >/dev/null 2>&1; then
    if iw dev "$WIFI_IFACE" set power_save off; then
        PS_RUNTIME="$(iw dev "$WIFI_IFACE" get power_save 2>/dev/null | awk -F': ' '{print $2}' | head -1)"
        if [ "$PS_RUNTIME" = "off" ]; then
            ok "runtime power save is off on $WIFI_IFACE"
        else
            fail "runtime power save is '${PS_RUNTIME:-unknown}' on $WIFI_IFACE"
        fi
    else
        fail "could not set runtime power save off on $WIFI_IFACE"
    fi
else
    fail "'iw' is not installed - cannot enforce runtime power save off"
fi

# Persisted value must be readable as "disable" (nmcli -g normalises enum 2).
PS_PROFILE="$(nmcli -g 802-11-wireless.powersave connection show "$HOTSPOT_CON" 2>/dev/null | head -1 | tr -d ' ')"
case "$PS_PROFILE" in
    disable|2) ok "persisted power-save profile value: ${PS_PROFILE}" ;;
    *)         fail "persisted power-save profile value is '${PS_PROFILE:-unknown}'" ;;
esac

# ---------------------------------------------------------------------------
# 4. Restart the guard (loads the runtime backstop) and the backend (loads the
#    new WebSocket hub). Only these two units are restarted.
# ---------------------------------------------------------------------------
systemctl restart aeris-hotspot-guard
if systemctl is-active --quiet aeris-hotspot-guard; then
    ok "aeris-hotspot-guard is active"
else
    fail "aeris-hotspot-guard did not become active"
fi

if systemctl restart aeris-backend; then
    ok "aeris-backend restart requested"
else
    fail "systemctl restart aeris-backend failed"
fi

HEALTH=""
for _ in $(seq 1 60); do
    HEALTH="$(curl -fsS -m 3 "http://127.0.0.1/health" 2>/dev/null || true)"
    printf '%s' "$HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' && break
    sleep 1
done
if printf '%s' "$HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok "backend /health is ok"
else
    fail "backend /health did not recover within 60 s"
fi

# ---------------------------------------------------------------------------
# 5. Verify the dashboard and the new hub diagnostics.
# ---------------------------------------------------------------------------
TITLE="$(curl -fsS -m 5 "http://127.0.0.1/" 2>/dev/null | grep -o '<title>[^<]*</title>' || true)"
if [ "$TITLE" = "<title>AERIS</title>" ]; then
    ok "dashboard is served"
else
    fail "dashboard title not found (got: ${TITLE:-none})"
fi

if [ -r "$TOKEN_FILE" ]; then
    TOKEN="$(cat "$TOKEN_FILE")"
    DIAG="$(curl -fsS -m 5 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1/api/diagnostics" 2>/dev/null || true)"
    unset TOKEN
    if printf '%s' "$DIAG" | grep -q '"queue_max"'; then
        ok "authenticated /api/diagnostics answers (new hub loaded)"
        say "diagnostics: $DIAG"
    else
        fail "/api/diagnostics did not return the new hub counters"
    fi
else
    fail "token file is unreadable: $TOKEN_FILE"
fi

# The AP itself must still be up and reachable through its own address.
if ip -4 addr show 2>/dev/null | grep -q "inet 192.168.4.1/"; then
    ok "hotspot address 192.168.4.1 is assigned"
else
    fail "hotspot address 192.168.4.1 is missing"
fi
if curl -fsS -m 5 "http://192.168.4.1/health" >/dev/null 2>&1; then
    ok "backend is reachable through the hotspot address"
else
    fail "backend is not reachable through 192.168.4.1"
fi

# ---------------------------------------------------------------------------
# 6. Result.
# ---------------------------------------------------------------------------
say "=== deployment finished: $FAILURES failure(s) ==="
if [ "$FAILURES" -eq 0 ]; then
    say "PASS - Phase 2 backend + AP settings are live."
else
    say "FAIL - inspect $LOG_FILE; rollback instructions are in the script header."
fi
say "Log: $LOG_FILE"
exit "$([ "$FAILURES" -eq 0 ] && echo 0 || echo 1)"
