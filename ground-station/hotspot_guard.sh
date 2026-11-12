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

# The AP interface. Power save on an AP is a known source of missed beacons
# and multi-second client stalls on brcmfmac, so the guard re-asserts "off"
# at runtime (the NetworkManager profile is the persistent setting).
WIFI_IFACE="${AERIS_WIFI_IFACE:-}"
if [ -z "$WIFI_IFACE" ]; then
    for iface in /sys/class/net/*/wireless; do
        [ -e "$iface" ] || continue
        WIFI_IFACE="$(basename "$(dirname "$iface")")"
        break
    done
fi

# Consecutive failed activation attempts. The first few are logged in full;
# after that the guard only reports every 12th attempt so a permanently
# broken hotspot cannot flood the journal.
ATTEMPTS=0
LOG_EVERY=1
PS_FAILS=0

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

    # Runtime backstop for the AP power-save setting. Only touch it when the
    # AP is actually up, and only log on a transition (not every 5 s).
    if is_active && [ -n "$WIFI_IFACE" ] && command -v iw >/dev/null 2>&1; then
        if iw dev "$WIFI_IFACE" get power_save 2>/dev/null | grep -qi 'Power save: off'; then
            PS_FAILS=0
        elif iw dev "$WIFI_IFACE" set power_save off >/dev/null 2>&1; then
            if [ "$PS_FAILS" -gt 0 ]; then
                log "Wi-Fi power save recovered on $WIFI_IFACE"
            else
                log "Wi-Fi power save was ON on $WIFI_IFACE - disabled"
            fi
            PS_FAILS=0
        else
            PS_FAILS=$((PS_FAILS + 1))
            if [ "$PS_FAILS" -eq 1 ] || [ $((PS_FAILS % 12)) -eq 0 ]; then
                log "Wi-Fi power save is ON on $WIFI_IFACE and could not be disabled ($PS_FAILS attempts)"
            fi
        fi
    fi

    sleep "$POLL_INTERVAL"
done
