#!/bin/bash
# ============================================================
# AERIS Hotspot Guard
# Keeps the "aeris-hotspot" Wi-Fi access point up.
#
# Why this exists: NetworkManager occasionally drops the AP connection
# (channel change, driver hiccup, rfkill). Without a guard the ground
# station becomes unreachable over the hotspot after a reboot.
#
# A "disabled" marker file lets stop_aeris.sh shut the hotspot down for
# good (the guard then leaves it alone) while a normal start removes the
# marker again.
#
# The guard never exits on its own: it polls forever and re-activates the AP
# whenever it is missing. systemd restarts it if it ever dies.
# ============================================================

set -uo pipefail

CON_NAME="${AERIS_HOTSPOT_CON:-aeris-hotspot}"
DISABLE_MARKER="${AERIS_HOTSPOT_DISABLE_MARKER:-/var/lib/aeris/hotspot-disabled}"
POLL_INTERVAL="${AERIS_HOTSPOT_POLL_INTERVAL:-5}"

log() {
    echo "[hotspot-guard] $*"
}

mkdir -p "$(dirname "$DISABLE_MARKER")" 2>/dev/null || true

# Consecutive failed activation attempts. The first few are logged in full;
# after that the guard only reports every 12th attempt so a permanently
# broken hotspot cannot flood the journal.
ATTEMPTS=0
LOG_EVERY=1

is_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${CON_NAME}\$"
}

profile_exists() {
    nmcli -t -f NAME connection show 2>/dev/null | grep -q "^${CON_NAME}\$"
}

while true; do
    if [ -f "$DISABLE_MARKER" ]; then
        sleep "$POLL_INTERVAL"
        continue
    fi

    if ! profile_exists; then
        # The profile is gone - "nmcli con up" could never work. Report it
        # clearly instead of retrying a command that can not succeed.
        if [ "$LOG_EVERY" -eq 1 ] || [ $((ATTEMPTS % 12)) -eq 0 ]; then
            log "the connection profile '$CON_NAME' does not exist - run aeris.sh to recreate it"
        fi
        ATTEMPTS=$((ATTEMPTS + 1))
        LOG_EVERY=$((ATTEMPTS + 1))
        sleep "$POLL_INTERVAL"
        continue
    fi

    if ! is_active; then
        if [ "$LOG_EVERY" -eq 1 ] || [ $((ATTEMPTS % 12)) -eq 0 ]; then
            log "hotspot '$CON_NAME' is not active - activating it"
        fi
        if nmcli con up "$CON_NAME" >/dev/null 2>&1; then
            if [ "$ATTEMPTS" -gt 0 ]; then
                log "hotspot '$CON_NAME' is active again"
            fi
            ATTEMPTS=0
            LOG_EVERY=1
        else
            ATTEMPTS=$((ATTEMPTS + 1))
            LOG_EVERY=$((ATTEMPTS + 1))
            if [ $((ATTEMPTS % 12)) -eq 0 ]; then
                log "activation of '$CON_NAME' keeps failing ($ATTEMPTS attempts) - check 'nmcli con show $CON_NAME'"
            fi
        fi
    fi

    sleep "$POLL_INTERVAL"
done
