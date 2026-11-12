#!/bin/bash
# ============================================================
# AERIS Onboard Service Installer — Raspberry Pi 4B
# ============================================================
# Installs the onboard detection pipeline as a systemd service that starts
# automatically on every boot.
#
# Usage:
#   sudo ./install_onboard_service.sh              # install + enable + start
#   sudo ./install_onboard_service.sh --no-start   # install + enable only
#   sudo ./install_onboard_service.sh --force      # allow it on a Pi that
#                                                  # already runs the ground station
#
# The script is idempotent - safe to run again at any time.
#
# IMPORTANT — hardware conflict:
#   The ground station backend (aeris-backend.service) and the onboard
#   pipeline drive the SAME physical sensors on a Raspberry Pi:
#     MLX90640  -> I2C1 @ 0x33
#     SX1278    -> SPI0 + GPIO23 (RESET) + GPIO24 (NSS)
#   The ground station RECEIVES LoRa, the onboard pipeline TRANSMITS it.
#   One radio can only do one of those at a time. This installer therefore
#   refuses to enable the onboard service on a Pi that already runs the
#   ground station, unless --force is given.
#
# Logs:   sudo journalctl -u aeris-onboard -f
# ============================================================

set -Eeuo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export DEBIAN_FRONTEND=noninteractive

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
ONBOARD_DIR="$(dirname "$SCRIPT_PATH")"
PROJECT_DIR="${AERIS_PROJECT_DIR:-$(cd "$ONBOARD_DIR/../.." && pwd)}"
VENV_DIR="$ONBOARD_DIR/venv"
UNIT_SRC="$ONBOARD_DIR/aeris-onboard.service"
UNIT_DST="/etc/systemd/system/aeris-onboard.service"

START_AFTER_INSTALL=1
FORCE=0
REAL_USER="${SUDO_USER:-}"
REAL_UID="${SUDO_UID:-}"

for arg in "$@"; do
    case "$arg" in
        --no-start) START_AFTER_INSTALL=0 ;;
        --force)    FORCE=1 ;;
        --help|-h)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_CYAN=''
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_RED=$'\033[0m\033[0;31m'; C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[1;33m'; C_BLUE=$'\033[0;34m'; C_CYAN=$'\033[0;36m'
fi

ok()   { printf '  [ OK ] %s\n' "$*"; }
info() { printf '  [INFO] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$*" "$C_RESET"; }

die() {
    printf '\n%s  INSTALLATION STOPPED: %s%s\n' "$C_RED" "$*" "$C_RESET"
    shift
    for line in "$@"; do printf '    - %s\n' "$line"; done
    printf '\n'
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root:  sudo $0"
    exit 1
fi

printf '\n%s  AERIS Onboard Service Installer (Raspberry Pi 4B)%s\n' "$C_BLUE" "$C_RESET"
printf '  Project dir : %s\n' "$PROJECT_DIR"
printf '  Onboard dir : %s\n' "$ONBOARD_DIR"

# ── as_user: run package managers as the normal user, not as root ─────────
as_user() {
    if [ -n "$REAL_USER" ] && id "$REAL_USER" >/dev/null 2>&1; then
        local user_home gid
        user_home="$(getent passwd "$REAL_USER" | cut -d: -f6)"
        gid="$(id -g "$REAL_USER")"
        runuser -u "$REAL_USER" -- env \
            "HOME=$user_home" "USER=$REAL_USER" "LOGNAME=$REAL_USER" \
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "$@"
    else
        "$@"
    fi
}

# ─────────────────────────────────────────────────────────────────────────
step "1. Hardware conflict check"
# ─────────────────────────────────────────────────────────────────────────
if systemctl is-enabled --quiet aeris-backend.service 2>/dev/null; then
    if [ "$FORCE" -ne 1 ]; then
        die "this Raspberry Pi already runs the ground station (aeris-backend.service is enabled)." \
            "The ground station and the onboard pipeline need the SAME MLX90640 (I2C1 @ 0x33)" \
            "and the SAME SX1278 LoRa radio (SPI0 + GPIO23/GPIO24). One radio cannot receive" \
            "and transmit at the same time, so running both on one Pi corrupts the link." \
            "Choose one of these:" \
            "  a) Use this Pi as the GROUND STATION only (recommended) - nothing to do." \
            "  b) Convert this Pi into a DRONE node:  sudo systemctl disable --now aeris-backend.service" \
            "     and then re-run this script." \
            "  c) Override the check (only if you know the hardware is separate):  $0 --force"
    fi
    warn "continuing because --force was given - the ground station will conflict on I2C/SPI"
fi
ok "no hardware conflict"

# ─────────────────────────────────────────────────────────────────────────
step "2. Python environment"
# ─────────────────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    info "Creating the virtual environment..."
    as_user python3 -m venv "$VENV_DIR" || python3 -m venv "$VENV_DIR" \
        || die "could not create the virtualenv at $VENV_DIR" \
               "Install it with:  sudo apt-get install -y python3-venv"
    ok "virtualenv created: $VENV_DIR"
else
    ok "virtualenv already present: $VENV_DIR"
fi

PY="$VENV_DIR/bin/python"

# The pipeline imports these at runtime. The check is done by IMPORTING them,
# not by reading requirements.txt, because the installed versions are known to
# work on this hardware.
check_module() { "$PY" -c "import $1" >/dev/null 2>&1; }

install_pkg() {
    info "installing $1 ..."
    as_user "$PY" -m pip install --quiet "$1" \
        || die "pip install $1 failed" "Try manually: $PY -m pip install $1"
}

# module          pip package
REQUIREMENTS=(
    "numpy|numpy"
    "flask|flask"
    "cv2|opencv-python-headless"
    "board|adafruit-blinka"
    "busio|adafruit-blinka"
    "adafruit_mlx90640|adafruit-circuitpython-mlx90640"
    "spidev|spidev"
    "RPi.GPIO|RPi.GPIO"
    "SX127x|SX127x"
)

MISSING_PKGS=()
for entry in "${REQUIREMENTS[@]}"; do
    mod="${entry%%|*}"
    pkg="${entry##*|}"
    if check_module "$mod"; then
        ok "$mod"
    else
        warn "$mod is missing"
        case " $MISSING_PKGS " in
            *" $pkg "*) : ;;
            *) MISSING_PKGS+=("$pkg") ;;
        esac
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    for pkg in "${MISSING_PKGS[@]}"; do
        install_pkg "$pkg"
    done
    ok "missing Python packages installed"
else
    ok "all Python dependencies are present"
fi

# Re-verify.
for entry in "${REQUIREMENTS[@]}"; do
    mod="${entry%%|*}"
    check_module "$mod" || die "the module '$mod' is still not importable" \
        "Install it manually:  $PY -m pip install ${entry##*|}"
done
ok "dependency check passed"

# The application itself must import cleanly, otherwise the service would
# crash-loop on every boot.
if ! ( cd "$ONBOARD_DIR" && "$PY" -c "import main" >/dev/null 2>&1 ); then
    IMPORT_ERR="$( cd "$ONBOARD_DIR" && "$PY" -c "import main" 2>&1 | tail -5 )"
    die "the onboard application does not import cleanly:" "$IMPORT_ERR"
fi
ok "main.py imports cleanly"

# ─────────────────────────────────────────────────────────────────────────
step "3. systemd unit"
# ─────────────────────────────────────────────────────────────────────────
[ -f "$UNIT_SRC" ] || die "unit file missing: $UNIT_SRC"

TMP="$(mktemp)"
sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" "$UNIT_SRC" > "$TMP"

if [ -f "$UNIT_DST" ] && cmp -s "$TMP" "$UNIT_DST"; then
    info "unit already up to date: $UNIT_DST"
else
    if [ -f "$UNIT_DST" ]; then
        cp -a "$UNIT_DST" "${UNIT_DST}.bak-$(date +%Y%m%d-%H%M%S)"
        info "existing unit backed up"
    fi
    cp "$TMP" "$UNIT_DST"
    chmod 0644 "$UNIT_DST"
    ok "unit installed: $UNIT_DST"
fi
rm -f "$TMP"

systemctl daemon-reload
ok "systemd units reloaded"

systemctl enable aeris-onboard.service >/dev/null 2>&1 \
    && ok "aeris-onboard.service enabled at boot" \
    || die "could not enable aeris-onboard.service"

# ─────────────────────────────────────────────────────────────────────────
step "4. Start"
# ─────────────────────────────────────────────────────────────────────────
if [ "$START_AFTER_INSTALL" -eq 0 ]; then
    info "--no-start given - the service will start on the next boot"
    exit 0
fi

systemctl restart aeris-onboard.service || true
sleep 5

if systemctl is-active --quiet aeris-onboard.service; then
    ok "aeris-onboard.service is running (PID $(systemctl show aeris-onboard -p MainPID --value))"
else
    warn "aeris-onboard.service is not active - diagnostics:"
    systemctl status aeris-onboard.service --no-pager -l 2>&1 | head -20 | sed 's/^/      /'
    journalctl -u aeris-onboard.service -n 30 --no-pager 2>&1 | sed 's/^/      /'
    die "the onboard service did not start." \
        "Fix the error above, then:  sudo systemctl restart aeris-onboard.service" \
        "Logs:  sudo journalctl -u aeris-onboard -f"
fi

# The Flask dashboard of the onboard pipeline listens on port 5000.
if curl -fsS -m 5 "http://127.0.0.1:5000/" >/dev/null 2>&1; then
    ok "onboard dashboard is reachable on http://127.0.0.1:5000/"
else
    warn "the onboard dashboard did not answer on port 5000 - check: sudo journalctl -u aeris-onboard -n 20"
fi

printf '\n%s  ONBOARD SERVICE INSTALLED%s\n' "$C_GREEN" "$C_RESET"
printf '  Status : systemctl status aeris-onboard\n'
printf '  Logs   : sudo journalctl -u aeris-onboard -f\n'
printf '  Panel  : http://<pi-ip>:5000/\n'
printf '  Boots  : the pipeline now starts automatically on every power-on.\n\n'
