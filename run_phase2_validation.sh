#!/bin/bash
# ============================================================
# AERIS Phase 2 - post-deployment validation
# ============================================================
#
# Run this as root AFTER deploy_phase2.sh:
#
#   sudo ./run_phase2_validation.sh --quick --monitor 7200
#
#   --quick            run the 1/2/3/6/10-client WebSocket matrix against
#                      the live backend (about 3 minutes, loopback only)
#   --monitor SECONDS  then poll health/diagnostics/AP/hardware every
#                      --interval seconds and record the evidence
#   --interval SEC     monitor sample interval (default 60)
#
# The 2-hour monitor is the run the operator should start once the laptop,
# phone and tablet are connected to AERIS-GroundStation. It only reads HTTP
# endpoints and system counters; it never touches GPIO/I2C/SPI/LoRa.
#
# Output: /home/piaeris/aeris-backups/phase2-validation-<timestamp>/
#   synthetic-*.json, monitor.jsonl, monitor.csv, summary.json,
#   backend-journal.log
# ============================================================

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$REPO_DIR/ground-station/backend"
VENV_PY="$BACKEND_DIR/venv/bin/python"
TOKEN_FILE="$BACKEND_DIR/.aeris_auth_token"
RESULT_DIR="/home/piaeris/aeris-backups/phase2-validation-$(date +%Y%m%d-%H%M%S)"

RUN_QUICK=0
MONITOR_SECONDS=0
INTERVAL=60

while [ $# -gt 0 ]; do
    case "$1" in
        --quick) RUN_QUICK=1 ;;
        --monitor) MONITOR_SECONDS="${2:-7200}"; shift ;;
        --interval) INTERVAL="${2:-60}"; shift ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
    shift
done

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must run as root:  sudo $0 $*" >&2
    exit 2
fi
if [ ! -r "$TOKEN_FILE" ]; then
    echo "token file is unreadable: $TOKEN_FILE" >&2
    exit 1
fi
if [ ! -x "$VENV_PY" ]; then
    echo "backend venv is missing: $VENV_PY" >&2
    exit 1
fi

mkdir -p "$RESULT_DIR"
START_ISO="$(date --iso-8601=seconds)"
echo "results: $RESULT_DIR"
echo "started: $START_ISO"
echo "monitor: ${MONITOR_SECONDS}s every ${INTERVAL}s"

FAILURES=0

if [ "$RUN_QUICK" -eq 1 ]; then
    TOKEN="$(cat "$TOKEN_FILE")"
    run_matrix() {
        local clients="$1" seconds="$2" churn="$3"
        echo ""
        echo "================ ${clients} client(s), ${seconds}s, ${churn} refresh(es) ================"
        if ! "$VENV_PY" "$BACKEND_DIR/tests/ws_stress.py" \
            --url http://127.0.0.1 \
            --token "$TOKEN" \
            --clients "$clients" \
            --seconds "$seconds" \
            --churn "$churn" \
            --json-out "$RESULT_DIR/synthetic-${clients}clients-${seconds}s.json"; then
            FAILURES=$((FAILURES + 1))
        fi
    }
    run_matrix 1 20 0
    run_matrix 2 20 0
    run_matrix 3 30 10
    run_matrix 6 30 20
    run_matrix 10 60 20
    unset TOKEN
fi

if [ "$MONITOR_SECONDS" -gt 0 ]; then
    echo ""
    echo "Starting the ${MONITOR_SECONDS}s monitor. Keep the real devices connected."
    "$VENV_PY" "$BACKEND_DIR/tests/phase2_monitor.py" \
        --url http://127.0.0.1 \
        --token-file "$TOKEN_FILE" \
        --seconds "$MONITOR_SECONDS" \
        --interval "$INTERVAL" \
        --out-dir "$RESULT_DIR" || FAILURES=$((FAILURES + 1))
fi

journalctl -u aeris-backend --since "$START_ISO" --no-pager > "$RESULT_DIR/backend-journal.log" 2>&1 || true
journalctl -u aeris-hotspot-guard --since "$START_ISO" --no-pager > "$RESULT_DIR/hotspot-guard-journal.log" 2>&1 || true
{
    echo "result_dir=$RESULT_DIR"
    echo "started=$START_ISO"
    echo "finished=$(date --iso-8601=seconds)"
    echo "failures=$FAILURES"
    echo "power_save=$(iw dev wlan0 get power_save 2>/dev/null || echo unavailable)"
    echo "backend=$(systemctl show aeris-backend -p MainPID -p NRestarts --no-pager 2>/dev/null | tr '\n' ' ')"
} > "$RESULT_DIR/run-meta.txt"

# Make the evidence readable by the operator without sudo.
chown -R piaeris:piaeris "$RESULT_DIR" 2>/dev/null || true

echo ""
echo "=== validation finished with $FAILURES failure(s) ==="
echo "evidence: $RESULT_DIR"
ls -lh "$RESULT_DIR"
exit "$([ "$FAILURES" -eq 0 ] && echo 0 || echo 1)"
