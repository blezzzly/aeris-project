#!/bin/bash
# ============================================================
# AERIS Boot Verification
# ============================================================
#
# Runs automatically after every boot (enabled in multi-user.target) and
# verifies that the whole ground station came back up on its own:
#
#   * aeris-backend.service is active
#   * the /health endpoint answers
#   * the dashboard (built frontend) is served
#   * the Wi-Fi hotspot is active and has 192.168.4.1
#   * the backend answers through the hotspot address
#   * every unit is ENABLED, so the next reboot will work as well
#   * the self-healing watchdog timer is running
#   * the frontend build exists
#   * the thermal camera and the LoRa radio report their real state
#
# The result is appended to /var/log/aeris-boot-check.log so a reboot can be
# verified without anybody sitting in front of the screen.
#
# No secret (token, passphrase) is ever written to the log.
# ============================================================

set -uo pipefail

LOG_FILE="/var/log/aeris-boot-check.log"
ENV_FILE="/etc/aeris/aeris.env"

PORT="$(grep -E '^AERIS_PORT=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$PORT" ] && PORT=80
HOTSPOT_IP="$(grep -E '^AERIS_HOTSPOT_IP=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$HOTSPOT_IP" ] && HOTSPOT_IP=192.168.4.1
HOTSPOT_CON="$(grep -E '^AERIS_HOTSPOT_CON=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$HOTSPOT_CON" ] && HOTSPOT_CON=aeris-hotspot
BACKEND_DIR="$(grep -E '^AERIS_BACKEND_DIR=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$BACKEND_DIR" ] && BACKEND_DIR="/home/piaeris/aeris-project/ground-station/backend"
FRONTEND_DIR="$(grep -E '^AERIS_FRONTEND_DIR=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$FRONTEND_DIR" ] && FRONTEND_DIR="/home/piaeris/aeris-project/ground-station/frontend"
TOKEN_FILE="$BACKEND_DIR/.aeris_auth_token"

say() {
    printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

FAILURES=0
check() {
    local label="$1" result="$2"
    if [ "$result" = "ok" ]; then
        say "PASS  $label"
    else
        say "FAIL  $label — $result"
        FAILURES=$((FAILURES + 1))
    fi
}

say "=== boot verification started (port=$PORT, hotspot=$HOTSPOT_CON) ==="

# ---------------------------------------------------------------------------
# 1. Wait for the backend to become READY.
#    "systemctl is-active" only means the process started; uvicorn still needs
#    a moment to bind the socket. Wait for the real answer, not the unit state.
# ---------------------------------------------------------------------------
HEALTH=""
for i in $(seq 1 45); do
    HEALTH="$(curl -fsS -m 4 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
    if printf '%s' "$HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        break
    fi
    [ "$i" = "5" ] || [ "$i" = "15" ] || [ "$i" = "30" ] && say "     ...waiting for the backend (${i}x4s)"
    sleep 4
done

if systemctl is-active --quiet aeris-backend; then
    check "aeris-backend.service is active" "ok"
else
    check "aeris-backend.service is active" "unit state: $(systemctl is-active aeris-backend)"
fi

if printf '%s' "$HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    check "backend /health answers on port ${PORT}" "ok"
else
    check "backend /health answers on port ${PORT}" "no valid response"
fi

# ---------------------------------------------------------------------------
# 2. Wait for the dashboard to be served.
# ---------------------------------------------------------------------------
DASH=""
for i in $(seq 1 15); do
    DASH="$(curl -fsS -m 6 "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
    printf '%s' "$DASH" | grep -q '<title>AERIS</title>' && break
    sleep 2
done
if printf '%s' "$DASH" | grep -q '<title>AERIS</title>'; then
    check "dashboard (frontend build) is served" "ok"
else
    check "dashboard (frontend build) is served" "index.html not returned"
fi

# ---------------------------------------------------------------------------
# 3. Wait for the hotspot: the guard may need a few seconds to bring it up,
#    and NetworkManager needs a moment to assign the address.
# ---------------------------------------------------------------------------
HOTSPOT_UP=""
for i in $(seq 1 20); do
    HOTSPOT_UP="$(nmcli -t -f NAME connection show --active 2>/dev/null | grep -c "^${HOTSPOT_CON}\$" || true)"
    [ "$HOTSPOT_UP" -ge 1 ] 2>/dev/null && break
    sleep 3
done
if [ "${HOTSPOT_UP:-0}" -ge 1 ] 2>/dev/null; then
    check "Wi-Fi hotspot '${HOTSPOT_CON}' is active" "ok"
else
    check "Wi-Fi hotspot '${HOTSPOT_CON}' is active" "not active"
fi

IP_OK=""
for i in $(seq 1 20); do
    if ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/"; then
        IP_OK=1
        break
    fi
    sleep 3
done
if [ -n "$IP_OK" ]; then
    check "hotspot address ${HOTSPOT_IP} is assigned" "ok"
else
    check "hotspot address ${HOTSPOT_IP} is assigned" "address missing"
fi

HOT_HEALTH=""
for i in $(seq 1 10); do
    HOT_HEALTH="$(curl -fsS -m 6 "http://${HOTSPOT_IP}/health" 2>/dev/null || true)"
    printf '%s' "$HOT_HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' && break
    sleep 2
done
if printf '%s' "$HOT_HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    check "backend reachable through the hotspot (${HOTSPOT_IP})" "ok"
else
    check "backend reachable through the hotspot (${HOTSPOT_IP})" "no valid response"
fi

# ---------------------------------------------------------------------------
# 3b. AP power save must stay disabled. Power save on an access point is a
#     known source of missed beacons and multi-second client stalls on
#     brcmfmac. Two things are checked: the persistent NetworkManager profile
#     (survives the next boot) and the runtime state the driver actually has.
# ---------------------------------------------------------------------------
WIFI_IFACE="$(nmcli -g GENERAL.DEVICES connection show "$HOTSPOT_CON" 2>/dev/null | head -1 | tr -d ' ')"
if [ -z "$WIFI_IFACE" ]; then
    for iface in /sys/class/net/*/wireless; do
        [ -e "$iface" ] || continue
        WIFI_IFACE="$(basename "$(dirname "$iface")")"
        break
    done
fi

PS_PROFILE="$(nmcli -g 802-11-wireless.powersave connection show "$HOTSPOT_CON" 2>/dev/null | head -1 | tr -d ' ')"
case "$PS_PROFILE" in
    disable|2) check "hotspot profile has Wi-Fi power save disabled" "ok" ;;
    *)         check "hotspot profile has Wi-Fi power save disabled" "profile value: ${PS_PROFILE:-unknown}" ;;
esac

if [ -n "$WIFI_IFACE" ] && command -v iw >/dev/null 2>&1; then
    PS_RUNTIME="$(iw dev "$WIFI_IFACE" get power_save 2>/dev/null | awk -F': ' '{print $2}' | head -1)"
    if [ "$PS_RUNTIME" = "off" ]; then
        check "Wi-Fi power save is off on $WIFI_IFACE at runtime" "ok"
    else
        check "Wi-Fi power save is off on $WIFI_IFACE at runtime" "reported: ${PS_RUNTIME:-unknown}"
    fi
else
    check "Wi-Fi power save is off at runtime" "interface or 'iw' unavailable"
fi

# ---------------------------------------------------------------------------
# 4. Boot persistence: a unit that is not enabled comes back broken after the
#    NEXT reboot, even when it is running perfectly right now.
# ---------------------------------------------------------------------------
for unit in aeris-backend.service aeris-hotspot-guard.service aeris-boot-check.service aeris.target aeris-watchdog.timer; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        check "$unit is enabled for boot" "ok"
    else
        check "$unit is enabled for boot" "unit is not enabled"
    fi
done

# ---------------------------------------------------------------------------
# 5. The watchdog timer - this is what repairs the stack between reboots.
# ---------------------------------------------------------------------------
if systemctl is-active --quiet aeris-watchdog.timer; then
    check "aeris-watchdog.timer is running (self-healing check)" "ok"
else
    check "aeris-watchdog.timer is running (self-healing check)" "timer is not active"
fi

# ---------------------------------------------------------------------------
# 6. The frontend build the backend serves.
# ---------------------------------------------------------------------------
if [ -f "$FRONTEND_DIR/dist/index.html" ]; then
    check "frontend build is present" "ok"
else
    check "frontend build is present" "missing $FRONTEND_DIR/dist/index.html"
fi

# ---------------------------------------------------------------------------
# 7. The hardware pipelines inside the backend (thermal camera, LoRa radio).
#    These use the authenticated API, so the operator token is needed. The
#    token is never printed - only used for the request.
# ---------------------------------------------------------------------------
TOKEN=""
[ -r "$TOKEN_FILE" ] && TOKEN="$(cat "$TOKEN_FILE" 2>/dev/null || true)"

if [ -z "$TOKEN" ]; then
    check "hardware pipelines report their state" "token file $TOKEN_FILE is unreadable"
else
    api_get() {
        curl -s -m 8 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:${PORT}$1" 2>/dev/null || true
    }

    THERMAL_JSON="$(api_get /api/thermal/status)"
    if printf '%s' "$THERMAL_JSON" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
        check "MLX90640 thermal camera is live" "ok"
    else
        check "MLX90640 thermal camera is live" "sensor did not report ready"
    fi

    HW_JSON="$(api_get /api/hardware_status)"
    if printf '%s' "$HW_JSON" | grep -q '"lora_sx1278"'; then
        if printf '%s' "$HW_JSON" | grep -q '"transmission_status"[[:space:]]*:[[:space:]]*"LISTENING"'; then
            check "SX1278 LoRa receiver is listening" "ok"
        elif printf '%s' "$HW_JSON" | grep -q '"spi_connected"[[:space:]]*:[[:space:]]*false'; then
            check "SX1278 LoRa receiver is listening" "no LoRa radio detected"
        else
            check "SX1278 LoRa receiver is listening" "radio not in LISTENING state"
        fi
    else
        check "SX1278 LoRa receiver is listening" "no hardware report"
    fi
    unset TOKEN
fi

if [ "$FAILURES" -eq 0 ]; then
    say "=== boot verification PASSED — AERIS came back automatically ==="
else
    say "=== boot verification FAILED ($FAILURES problem(s)) ==="
    say "    Repair with: sudo /home/piaeris/aeris-project/aeris.sh"
fi

exit 0
