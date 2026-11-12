#!/bin/bash
# ============================================================
# AERIS Watchdog - periodic self-healing check
# ============================================================
#
# Started by aeris-watchdog.timer every 5 minutes.
#
# systemd's Restart= only covers a process that DIES. It does not cover the
# failure modes that actually happen on a long-running ground station:
#
#   * uvicorn is alive but the HTTP port stopped answering (wedged socket)
#   * the unit is active but the application exited its event loop
#   * NetworkManager dropped the "aeris-hotspot" profile and never restored it
#   * the hotspot profile is up but 192.168.4.1 was never assigned
#   * avahi-daemon died, so aeris.local no longer resolves
#   * somebody disabled a unit, so the next reboot would come up broken
#
# This script detects those states and repairs them. It runs entirely
# unattended - it never asks a question and never waits for input.
#
# Log: /var/log/aeris-watchdog.log (rotated by /etc/logrotate.d/aeris)
#
# No secret (token, passphrase) is ever written to the log.
# ============================================================

set -uo pipefail

LOG_FILE="/var/log/aeris-watchdog.log"
STATE_DIR="/var/lib/aeris"
ENV_FILE="/etc/aeris/aeris.env"

PORT="$(grep -E '^AERIS_PORT=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$PORT" ] && PORT=80
HOTSPOT_IP="$(grep -E '^AERIS_HOTSPOT_IP=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$HOTSPOT_IP" ] && HOTSPOT_IP=192.168.4.1
HOTSPOT_CON="$(grep -E '^AERIS_HOTSPOT_CON=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
[ -z "$HOTSPOT_CON" ] && HOTSPOT_CON=aeris-hotspot

mkdir -p "$STATE_DIR" 2>/dev/null || true

# Consecutive-failure counters, so a permanently broken component is not
# restarted in a tight loop.
FAIL_BACKEND="$STATE_DIR/watchdog-backend-failures"
FAIL_HOTSPOT="$STATE_DIR/watchdog-hotspot-failures"

say() {
    printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# read_counter <file> -> number
read_counter() {
    local v=0
    [ -f "$1" ] && v="$(cat "$1" 2>/dev/null | tr -dc '0-9')"
    [ -z "$v" ] && v=0
    printf '%s' "$v"
}

# write_counter <file> <value>
write_counter() {
    printf '%s' "$2" > "$1" 2>/dev/null || true
}

REPAIRS=0
PROBLEMS=0

note_repair() {
    say "REPAIR  $*"
    REPAIRS=$((REPAIRS + 1))
}

note_problem() {
    say "PROBLEM $*"
    PROBLEMS=$((PROBLEMS + 1))
}

health_ok() {
    local body
    body="$(curl -fsS -m 5 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
    printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'
}

# ---------------------------------------------------------------------------
# 1. Backend process
# ---------------------------------------------------------------------------
if ! systemctl is-active --quiet aeris-backend; then
    fails="$(read_counter "$FAIL_BACKEND")"
    if [ "$fails" -lt 3 ]; then
        say "CHECK   aeris-backend is not active - restarting"
        systemctl restart aeris-backend >/dev/null 2>&1 || true
        sleep 3
        if systemctl is-active --quiet aeris-backend; then
            note_repair "aeris-backend restarted"
            write_counter "$FAIL_BACKEND" 0
        else
            write_counter "$FAIL_BACKEND" "$((fails + 1))"
            note_problem "aeris-backend could not be started - journalctl -u aeris-backend -n 30"
        fi
    else
        note_problem "aeris-backend is not active ($fails consecutive failures) - repair with: sudo /home/piaeris/aeris-project/aeris.sh"
    fi
elif ! health_ok; then
    # The unit is running but the HTTP port does not answer: the process is
    # wedged. Restarting is the only way to recover.
    fails="$(read_counter "$FAIL_BACKEND")"
    if [ "$fails" -lt 3 ]; then
        say "CHECK   aeris-backend is running but /health does not answer - restarting"
        systemctl restart aeris-backend >/dev/null 2>&1 || true
        write_counter "$FAIL_BACKEND" "$((fails + 1))"
        # Give uvicorn time to bind the socket again.
        i=0
        while [ "$i" -lt 30 ]; do
            health_ok && break
            sleep 2
            i=$((i + 1))
        done
        if health_ok; then
            note_repair "aeris-backend was wedged - restarted and /health answers again"
            write_counter "$FAIL_BACKEND" 0
        else
            note_problem "aeris-backend did not recover after a restart"
        fi
    else
        note_problem "aeris-backend is running but /health does not answer ($fails consecutive failures)"
    fi
else
    write_counter "$FAIL_BACKEND" 0
fi

# ---------------------------------------------------------------------------
# 2. Wi-Fi hotspot
# ---------------------------------------------------------------------------
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -q "^${HOTSPOT_CON}\$"; then
    # The profile itself is gone - recreate it is out of scope for a watchdog,
    # but we can still report it precisely.
    note_problem "the hotspot profile '${HOTSPOT_CON}' does not exist - run: sudo /home/piaeris/aeris-project/aeris.sh"
elif ! nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}\$"; then
    fails="$(read_counter "$FAIL_HOTSPOT")"
    if [ "$fails" -lt 3 ]; then
        say "CHECK   hotspot '${HOTSPOT_CON}' is not active - activating"
        nmcli con up "$HOTSPOT_CON" >/dev/null 2>&1 || true
        sleep 4
        if nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}\$"; then
            note_repair "hotspot '${HOTSPOT_CON}' re-activated"
            write_counter "$FAIL_HOTSPOT" 0
        else
            write_counter "$FAIL_HOTSPOT" "$((fails + 1))"
            note_problem "hotspot '${HOTSPOT_CON}' could not be activated"
        fi
    else
        note_problem "hotspot '${HOTSPOT_CON}' is not active ($fails consecutive failures)"
    fi
elif ! ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/"; then
    # The profile is up but the address was never assigned: bounce the
    # connection so NetworkManager re-runs its IP configuration.
    fails="$(read_counter "$FAIL_HOTSPOT")"
    if [ "$fails" -lt 3 ]; then
        say "CHECK   hotspot address ${HOTSPOT_IP} is missing - bouncing the connection"
        nmcli con down "$HOTSPOT_CON" >/dev/null 2>&1 || true
        sleep 2
        nmcli con up "$HOTSPOT_CON" >/dev/null 2>&1 || true
        write_counter "$FAIL_HOTSPOT" "$((fails + 1))"
        i=0
        while [ "$i" -lt 15 ]; do
            ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/" && break
            sleep 2
            i=$((i + 1))
        done
        if ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/"; then
            note_repair "hotspot address ${HOTSPOT_IP} restored"
            write_counter "$FAIL_HOTSPOT" 0
        else
            note_problem "hotspot address ${HOTSPOT_IP} could not be restored"
        fi
    else
        note_problem "hotspot address ${HOTSPOT_IP} is missing ($fails consecutive failures)"
    fi
else
    write_counter "$FAIL_HOTSPOT" 0
fi

# ---------------------------------------------------------------------------
# 3. mDNS (aeris.local)
# ---------------------------------------------------------------------------
if systemctl list-unit-files avahi-daemon.service >/dev/null 2>&1 \
   && ! systemctl is-active --quiet avahi-daemon; then
    say "CHECK   avahi-daemon is not active - restarting"
    systemctl restart avahi-daemon >/dev/null 2>&1 || true
    sleep 2
    if systemctl is-active --quiet avahi-daemon; then
        note_repair "avahi-daemon restarted (aeris.local resolves again)"
    else
        note_problem "avahi-daemon could not be started"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Boot persistence - a disabled unit comes back broken after a reboot.
# ---------------------------------------------------------------------------
for unit in aeris-backend.service aeris-hotspot-guard.service aeris-boot-check.service aeris.target aeris-watchdog.timer; do
    if ! systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        say "CHECK   ${unit} is not enabled at boot - enabling"
        systemctl enable "$unit" >/dev/null 2>&1 || true
        if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
            note_repair "${unit} enabled for boot"
        else
            note_problem "${unit} could not be enabled"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 5. Summary
# ---------------------------------------------------------------------------
# The log only gets a line when something was wrong or repaired, plus a
# heartbeat once an hour, so the file stays small on the SD card.
RUNS_FILE="$STATE_DIR/watchdog-runs"
runs="$(read_counter "$RUNS_FILE")"
runs=$((runs + 1))
write_counter "$RUNS_FILE" "$runs"

if [ "$REPAIRS" -gt 0 ] || [ "$PROBLEMS" -gt 0 ]; then
    say "STATUS  $REPAIRS repair(s), $PROBLEMS unresolved problem(s) (run #$runs)"
else
    if [ "$((runs % 12))" -eq 1 ]; then
        say "STATUS  all checks passed (run #$runs)"
    fi
fi

exit 0
