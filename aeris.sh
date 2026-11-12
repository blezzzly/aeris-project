#!/bin/bash
# =============================================================================
#  AERIS Ground Station — One-Command Auto-Repair & Deployment
# =============================================================================
#
#  USAGE:
#      cd ~/aeris-project && sudo ./aeris.sh
#
#  What it does (every step is idempotent - safe to run again and again):
#      1.  Preflight        - OS, kernel, required tools
#      2.  Packages         - install missing system packages
#      3.  Toolchain        - Node.js / npm / Python version checks
#      4.  Backend deps     - virtualenv + pip requirements
#      5.  Backend config   - token file, env file, syntax/import validation
#      6.  Frontend deps    - npm install
#      7.  Frontend build   - TypeScript check + Vite production build
#      8.  systemd          - install/repair units, enable for boot
#                              (backend, hotspot guard, boot check, target,
#                               watchdog timer) + log rotation & journal limit
#      9.  Backend health   - /health validation with diagnostics
#      10. Frontend         - dashboard availability + asset check
#      11. Backend API      - authenticated API + auth enforcement check
#      12. Hotspot/network  - NetworkManager, aeris-hotspot, 192.168.4.1
#      13. Boot survival    - verify services are enabled and active
#      14. Report           - full deployment report
#
#  OPTIONS:
#      --skip-apt      do not touch apt (assume system packages are present)
#      --skip-build    do not rebuild the frontend (use the existing dist/)
#      --skip-deps     do not reinstall python/npm dependencies
#      --help          show this help
#
#  LOGGING: every step is written to /var/log/aeris-setup.log
#  SECRETS: authentication tokens and hotspot passphrases are never printed.
# =============================================================================

set -Eeuo pipefail

# -----------------------------------------------------------------------------
# PATH / globals
# -----------------------------------------------------------------------------
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"
export DEBIAN_FRONTEND=noninteractive

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="${AERIS_PROJECT_DIR:-$(dirname "$SCRIPT_PATH")}"
BACKEND_DIR="$PROJECT_DIR/ground-station/backend"
FRONTEND_DIR="$PROJECT_DIR/ground-station/frontend"

ENV_DIR="/etc/aeris"
ENV_FILE="$ENV_DIR/aeris.env"
STATE_DIR="/var/lib/aeris"
LOG_FILE="/var/log/aeris-setup.log"
UNIT_DIR="/etc/systemd/system"
BACKEND_UNIT="$UNIT_DIR/aeris-backend.service"
GUARD_UNIT="$UNIT_DIR/aeris-hotspot-guard.service"
BOOT_UNIT="$UNIT_DIR/aeris-boot-check.service"
TARGET_UNIT="$UNIT_DIR/aeris.target"
WATCHDOG_UNIT="$UNIT_DIR/aeris-watchdog.service"
WATCHDOG_TIMER="$UNIT_DIR/aeris-watchdog.timer"

LOGROTATE_FILE="/etc/logrotate.d/aeris"
JOURNALD_DIR="/etc/systemd/journald.conf.d"
JOURNALD_FILE="$JOURNALD_DIR/aeris.conf"

HOTSPOT_CON="aeris-hotspot"
HOTSPOT_SSID="AERIS-GroundStation"
HOTSPOT_IP="192.168.4.1"
HOTSPOT_DISABLE_MARKER="$STATE_DIR/hotspot-disabled"

DEFAULT_PORT=80
PORT=""

SKIP_APT=0
SKIP_BUILD=0
SKIP_DEPS=0

STEPS_TOTAL=14
STEP_NO=0
WARNINGS=0
REPAIRS=0
FAILURES=0
START_EPOCH=$(date +%s)

# -----------------------------------------------------------------------------
# Output helpers
# -----------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[1;33m'; C_BLUE=$'\033[0;34m'; C_CYAN=$'\033[0;36m'
    C_BOLD=$'\033[1m'
else
    C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_CYAN=''; C_BOLD=''
fi

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

emit() { printf '%s\n' "$*"; }

log_raw() {
    # Timestamped copy for the log file only - the console gets the clean line.
    printf '[%s] %s\n' "$(stamp)" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

log_step() {
    STEP_NO=$((STEP_NO + 1))
    printf '\n%s[%02d/%02d] %s%s\n' "$C_CYAN$C_BOLD" "$STEP_NO" "$STEPS_TOTAL" "$*" "$C_RESET"
    log_raw "=== STEP $STEP_NO/$STEPS_TOTAL :: $* ==="
}

ok()   { printf '  %s[ OK ]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; log_raw "OK   $*"; }
warn() { printf '  %s[ WARN ]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; log_raw "WARN $*"; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf '  %s[FAIL ]%s %s\n' "$C_RED" "$C_RESET" "$*"; log_raw "FAIL $*"; FAILURES=$((FAILURES + 1)); }
info() { printf '  %s[INFO ]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; log_raw "INFO $*"; }
fix()  { printf '  %s[FIX  ]%s %s\n' "$C_CYAN" "$C_RESET" "$*"; log_raw "FIX  $*"; REPAIRS=$((REPAIRS + 1)); }

die() {
    printf '\n%s================================================================%s\n' "$C_RED$C_BOLD" "$C_RESET"
    printf '%s  AERIS DEPLOYMENT FAILED%s\n' "$C_RED$C_BOLD" "$C_RESET"
    printf '%s================================================================%s\n' "$C_RED$C_BOLD" "$C_RESET"
    printf '  Reason: %s\n\n' "$*"
    if [ $# -gt 1 ]; then
        printf '  %sHow to fix it:%s\n' "$C_YELLOW$C_BOLD" "$C_RESET"
        shift
        for line in "$@"; do printf '    - %s\n' "$line"; done
        printf '\n'
    fi
    printf '  Full log: %s\n' "$LOG_FILE"
    log_raw "FATAL: $*"
    exit 1
}

# Redact anything that looks like a secret before it reaches the log/console.
redact() {
    sed -E \
        -e 's/(TOKEN=|token=|Bearer |AERIS_AUTH_TOKEN=|AERIS_VIEWER_TOKEN=|PSK=|password=)[^[:space:]"'"'"']+/\1***REDACTED***/g' \
        -e 's/("?psk"?[[:space:]]*[:=][[:space:]]*"?)[^[:space:]"'"'"',}]+/\1***REDACTED***/g'
}

on_error() {
    local exit_code=$?
    local line=${1:-?}
    log_raw "ERROR: command failed at line $line (exit $exit_code)"
    die "Unexpected error at line $line (exit code $exit_code)." \
        "Check $LOG_FILE for the failing command." \
        "Re-run: sudo $SCRIPT_PATH"
}
trap 'on_error $LINENO' ERR

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        --skip-apt)   SKIP_APT=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --skip-deps)  SKIP_DEPS=1 ;;
        --help|-h)
            sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "Unknown option: $arg" "Valid options: --skip-apt --skip-build --skip-deps --help" ;;
    esac
done

# -----------------------------------------------------------------------------
# Root check + logging setup
# -----------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    printf '%sThis script must be run as root.%s\n\n' "$C_RED" "$C_RESET"
    printf '  Usage:  cd %s && sudo ./aeris.sh\n\n' "$PROJECT_DIR"
    exit 1
fi

mkdir -p /var/log "$STATE_DIR"
touch "$LOG_FILE"
chmod 0640 "$LOG_FILE"
# Everything below is echoed to the screen AND appended to the log file.
exec > >(tee -a "$LOG_FILE") 2>&1

REAL_USER="${SUDO_USER:-}"
REAL_UID="${SUDO_UID:-}"

# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------

# has_pkg <pkg>  -> true when a dpkg package is installed
has_pkg() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "ok installed"; }

# cmd_exists <cmd>
cmd_exists() { command -v "$1" >/dev/null 2>&1; }

# ensure_env_key <KEY> <VALUE> [FORCE]
#   Adds the key when missing. Never overwrites an existing non-empty value
#   unless FORCE=1 - existing operator configuration is preserved.
ensure_env_key() {
    local key="$1" value="$2" force="${3:-0}"
    [ -f "$ENV_FILE" ] || { mkdir -p "$ENV_DIR"; : > "$ENV_FILE"; chmod 0640 "$ENV_FILE"; }
    if grep -qE "^${key}=" "$ENV_FILE"; then
        local current
        current="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
        if [ "$force" = "1" ]; then
            local tmp
            tmp="$(mktemp)"
            sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" > "$tmp"
            cat "$tmp" > "$ENV_FILE"
            rm -f "$tmp"
        elif [ -z "$current" ]; then
            local tmp
            tmp="$(mktemp)"
            sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" > "$tmp"
            cat "$tmp" > "$ENV_FILE"
            rm -f "$tmp"
        fi
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

# env_value <KEY> [default]
env_value() {
    local key="$1" default="${2:-}" value=""
    if [ -f "$ENV_FILE" ]; then
        value="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
    fi
    [ -n "$value" ] && { printf '%s' "$value"; return 0; }
    printf '%s' "$default"
}

# resolve_port: single source of truth for the backend port.
#   1. /etc/aeris/aeris.env
#   2. the running systemd unit's environment
#   3. the unit file on disk
#   4. built-in default
resolve_port() {
    local p=""
    p="$(env_value AERIS_PORT '')"
    if [ -z "$p" ]; then
        p="$(systemctl show aeris-backend -p Environment --value 2>/dev/null \
              | tr ' ' '\n' | grep -E '^AERIS_PORT=' | head -1 | cut -d= -f2 || true)"
    fi
    if [ -z "$p" ] && [ -f "$BACKEND_UNIT" ]; then
        p="$(grep -E '^Environment=AERIS_PORT=' "$BACKEND_UNIT" | head -1 | cut -d= -f2 || true)"
    fi
    if [ -z "$p" ] && [ -f "$PROJECT_DIR/ground-station/aeris-backend.service" ]; then
        p="$(grep -E '^Environment=AERIS_PORT=' "$PROJECT_DIR/ground-station/aeris-backend.service" | head -1 | cut -d= -f2 || true)"
    fi
    [ -z "$p" ] && p="$DEFAULT_PORT"
    printf '%s' "$p"
}

# chown_to_real_user <path...> - give build output back to the invoking user
chown_to_real_user() {
    if [ -n "$REAL_USER" ] && [ -n "$REAL_UID" ]; then
        local gid
        gid="$(id -g "$REAL_USER" 2>/dev/null || echo "$REAL_UID")"
        chown -R "$REAL_USER:$gid" "$@" 2>/dev/null || true
    fi
}

# as_user <cmd...> - run a command as the invoking user, not as root.
#
# Package managers (npm, pip) must NOT run as root inside the user's project
# directories: every file they create becomes root-owned and the user can then
# no longer run `npm install`, `npm run dev` or `pip install` themselves
# (EACCES). Running them as the real user keeps the ownership correct.
#
# HOME is set explicitly so npm/pip use the user's own cache and config
# directories instead of root's.
as_user() {
    if [ -n "$REAL_USER" ] && [ -n "$REAL_UID" ] && id "$REAL_USER" >/dev/null 2>&1; then
        local user_home
        user_home="$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f6)"
        [ -z "$user_home" ] && user_home="/home/$REAL_USER"
        runuser -u "$REAL_USER" -- env \
            "HOME=$user_home" \
            "USER=$REAL_USER" \
            "LOGNAME=$REAL_USER" \
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
            "$@"
    else
        "$@"
    fi
}

# repair_ownership <path...> - hand root-owned leftovers back to the user.
#
# Needed because a previous run (or a manual `sudo npm install`) may have left
# root-owned files behind. The top-level directory is often still owned by the
# user while its CONTENT is root-owned, so the whole tree has to be inspected.
repair_ownership() {
    local target
    for target in "$@"; do
        [ -e "$target" ] || continue
        [ -n "$REAL_USER" ] || continue
        id "$REAL_USER" >/dev/null 2>&1 || continue
        local offender
        offender="$(find "$target" ! -user "$REAL_USER" -print -quit 2>/dev/null || true)"
        if [ -n "$offender" ]; then
            local gid count
            gid="$(id -g "$REAL_USER" 2>/dev/null || echo "$REAL_UID")"
            count="$(find "$target" ! -user "$REAL_USER" 2>/dev/null | wc -l)"
            chown -R "$REAL_USER:$gid" "$target" 2>/dev/null || true
            fix "Handed $count root-owned file(s) back to '$REAL_USER': $target"
        fi
    done
}

# run_logged <cmd...> - run a command, show the tail of its output and keep its
# real exit code.
#
# A plain `cmd | tail -5` returns the exit code of `tail`, so a failing build
# would look successful. This helper preserves the true status.
run_logged() {
    local tmp rc=0
    tmp="$(mktemp)"
    "$@" > "$tmp" 2>&1 || rc=$?
    tail -15 "$tmp"
    rm -f "$tmp"
    return $rc
}

# install_unit <source> <target> - idempotent unit install with backup
install_unit() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        bad "Unit file missing in project: $src"
        return 1
    fi
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        info "Unit already up to date: $dst"
        return 0
    fi
    if [ -f "$dst" ]; then
        cp -a "$dst" "${dst}.bak-$(date +%Y%m%d-%H%M%S)"
        fix "Backed up existing unit -> ${dst}.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    cp "$src" "$dst"
    chmod 0644 "$dst"
    fix "Installed unit: $dst"
}

# install_file <source> <target> - idempotent config file install with backup
install_file() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        bad "Config file missing in project: $src"
        return 1
    fi
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        info "Config already up to date: $dst"
        return 0
    fi
    if [ -f "$dst" ]; then
        cp -a "$dst" "${dst}.bak-$(date +%Y%m%d-%H%M%S)"
        fix "Backed up existing config -> ${dst}.bak-$(date +%Y%m%d-%H%M%S)"
    fi
    cp "$src" "$dst"
    fix "Installed config: $dst"
}

# wait_for_health <url> <attempts> <delay>
wait_for_health() {
    local url="$1" attempts="${2:-30}" delay="${3:-1}" body=""
    local i
    for i in $(seq 1 "$attempts"); do
        body="$(curl -fsS -m 5 "$url" 2>/dev/null || true)"
        if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
            printf '%s' "$body"
            return 0
        fi
        sleep "$delay"
    done
    printf '%s' "$body"
    return 1
}

print_banner() {
    printf '%s\n' "$C_CYAN"
    cat <<'BANNER'
   ___  ___ ___ _   _ ___ ___ ___
  / _ \| _ \ __| | | | __/ __| __|     A E R I S   G R O U N D   S T A T I O N
 | (_) |  _/ _ \ |_| | _| (__| _|      one-command auto-repair & deployment
  \___/|_| \___/\__,_|_|  \___\___|
BANNER
    printf '%s\n' "$C_RESET"
}

# =============================================================================
# 00 - Banner
# =============================================================================
print_banner
log_raw "AERIS deployment started by $(id -un) (real user: ${REAL_USER:-n/a})"
log_raw "Project dir: $PROJECT_DIR"

# =============================================================================
# 01 - Preflight
# =============================================================================
log_step "Preflight - operating system and required tools"

if [ ! -r /etc/os-release ]; then
    die "/etc/os-release is missing - cannot identify the operating system."
fi
# shellcheck disable=SC1091
. /etc/os-release
info "OS            : ${PRETTY_NAME:-$NAME $VERSION_ID}"
info "Kernel        : $(uname -r)"
info "Architecture  : $(uname -m)"
info "Project dir   : $PROJECT_DIR"

case "${ID:-}" in
    raspbian|debian) ok "Debian-family OS detected (apt package manager available)" ;;
    *) warn "Unexpected OS '${ID:-unknown}' - continuing, but apt-based steps may not apply" ;;
esac

if [ -f /proc/device-tree/model ]; then
    MODEL="$(tr -d '\0' < /proc/device-tree/model)"
    info "Device model  : $MODEL"
fi

for tool in systemctl curl sed grep awk python3; do
    if ! cmd_exists "$tool"; then
        bad "Required tool missing: $tool"
        die "The system is missing '$tool'." \
            "Install it with:  sudo apt-get install -y ${tool}"
    fi
done
ok "Core tools present (systemctl, curl, python3, ...)"

# Verify the project layout before touching anything.
for required in \
    "$BACKEND_DIR/main.py" \
    "$BACKEND_DIR/requirements.txt" \
    "$FRONTEND_DIR/package.json" \
    "$FRONTEND_DIR/src" \
    "$PROJECT_DIR/ground-station/aeris-backend.service" \
    "$PROJECT_DIR/aeris-hotspot-guard.service" \
    "$PROJECT_DIR/ground-station/aeris-boot-check.service" \
    "$PROJECT_DIR/ground-station/aeris.target" \
    "$PROJECT_DIR/ground-station/aeris-watchdog.service" \
    "$PROJECT_DIR/ground-station/aeris-watchdog.timer" \
    "$PROJECT_DIR/ground-station/hotspot_guard.sh" \
    "$PROJECT_DIR/ground-station/boot_check.sh" \
    "$PROJECT_DIR/ground-station/watchdog.sh"
do
    [ -e "$required" ] || die "Project is incomplete - missing: $required" \
        "Make sure you run the script from the AERIS project root:" \
        "  cd ~/aeris-project && sudo ./aeris.sh"
done
ok "Project layout verified"

# =============================================================================
# 02 - System packages
# =============================================================================
log_step "System packages"

REQUIRED_PKGS=(python3 python3-venv python3-pip curl ca-certificates network-manager i2c-tools)
OPTIONAL_PKGS=(avahi-daemon)

if [ "$SKIP_APT" = "1" ]; then
    info "--skip-apt given: not touching apt"
else
    if ! cmd_exists apt-get; then
        warn "apt-get not available - skipping package installation"
    else
        MISSING=()
        for pkg in "${REQUIRED_PKGS[@]}"; do
            has_pkg "$pkg" || MISSING+=("$pkg")
        done
        for pkg in "${OPTIONAL_PKGS[@]}"; do
            has_pkg "$pkg" || MISSING+=("$pkg")
        done

        if [ ${#MISSING[@]} -eq 0 ]; then
            ok "All required system packages are installed"
        else
            info "Installing missing packages: ${MISSING[*]}"
            export DEBIAN_FRONTEND=noninteractive
            if ! apt-get update -qq; then
                warn "apt-get update failed (offline?) - continuing with the local package cache"
            fi
            if apt-get install -y --no-install-recommends "${MISSING[@]}"; then
                fix "Installed system packages: ${MISSING[*]}"
            else
                die "apt-get install failed for: ${MISSING[*]}" \
                    "Fix apt first:  sudo apt-get update && sudo apt-get -f install" \
                    "Then re-run:  sudo $SCRIPT_PATH"
            fi
        fi
    fi
fi

if cmd_exists nmcli; then
    ok "NetworkManager tooling available"
else
    die "nmcli is missing - NetworkManager is required for the hotspot." \
        "Install it:  sudo apt-get install -y network-manager"
fi

# =============================================================================
# 03 - Toolchain (Node / npm / Python)
# =============================================================================
log_step "Toolchain - Node.js, npm and Python"

install_node_if_needed() {
    # Debian/Raspberry Pi OS ships an older Node; Vite 8 needs >= 20.19 or >= 22.12.
    if cmd_exists node; then
        local major minor
        major="$(node -v | sed 's/v\([0-9]*\)\.\([0-9]*\).*/\1/')"
        minor="$(node -v | sed 's/v\([0-9]*\)\.\([0-9]*\).*/\2/')"
        if [ "$major" -ge 22 ] || { [ "$major" -eq 20 ] && [ "$minor" -ge 19 ]; }; then
            ok "Node.js $(node -v) satisfies the Vite requirement"
            return 0
        fi
        warn "Node.js $(node -v) is too old for Vite 8 (need >= 20.19 or >= 22.12)"
    else
        warn "Node.js is not installed"
    fi

    # Try the distro package first (cheap, no network surprises).
    if [ "$SKIP_APT" != "1" ] && cmd_exists apt-get; then
        info "Trying the distribution Node.js package..."
        apt-get install -y --no-install-recommends nodejs npm >/dev/null 2>&1 || true
        if cmd_exists node; then
            local major minor
            major="$(node -v | sed 's/v\([0-9]*\)\.\([0-9]*\).*/\1/')"
            minor="$(node -v | sed 's/v\([0-9]*\)\.\([0-9]*\).*/\2/')"
            if [ "$major" -ge 22 ] || { [ "$major" -eq 20 ] && [ "$minor" -ge 19 ]; }; then
                fix "Installed Node.js $(node -v) from the distribution packages"
                return 0
            fi
        fi
    fi

    # Fallback: official Node.js LTS tarball.
    local node_ver="v22.14.0" arch tarball tmp
    case "$(uname -m)" in
        x86_64) arch=x64 ;;
        aarch64|arm64) arch=arm64 ;;
        armv7l) arch=armv7l ;;
        *) arch="" ;;
    esac
    [ -z "$arch" ] && die "Unsupported CPU architecture for Node.js: $(uname -m)" \
        "Install Node.js >= 20.19 manually, then re-run this script."

    tarball="node-${node_ver}-linux-${arch}.tar.xz"
    tmp="$(mktemp -d)"
    info "Downloading the official Node.js LTS build ($node_ver)..."
    if ! curl -fsSL -o "$tmp/$tarball" "https://nodejs.org/dist/${node_ver}/${tarball}"; then
        rm -rf "$tmp"
        die "Could not download Node.js from nodejs.org." \
            "Check internet connectivity, or install Node.js >= 20.19 manually:" \
            "  curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/').tar.xz | sudo tar -xJ -C /usr/local --strip-components=1"
    fi
    tar -xJf "$tmp/$tarball" -C /usr/local --strip-components=1
    rm -rf "$tmp"
    hash -r
    fix "Installed Node.js $(node -v) into /usr/local"
}

install_node_if_needed
cmd_exists node || die "Node.js is still unavailable after the install attempts."
cmd_exists npm  || die "npm is still unavailable after the install attempts."

info "Node.js       : $(node -v)"
info "npm           : $(npm -v)"

# =============================================================================
# 03b - I2C bus speed (MLX90640 thermal camera)
# =============================================================================
# The MLX90640 delivers a full frame as 2 x 1664-byte I2C bursts. At the
# Raspberry Pi's default 100 kHz that takes ~450 ms per frame, which caps the
# thermal feed at ~2 fps and makes the image trail fast movement. The
# how2electronics MLX90640 reference explicitly requires 400 kHz:
#   dtparam=i2c_arm=on, i2c_arm_baudrate=400000
# The setting lives in the boot config, so it needs root and a reboot.
ensure_i2c_baudrate() {
    local boot_cfg=""
    for candidate in /boot/firmware/config.txt /boot/config.txt; do
        [ -f "$candidate" ] && { boot_cfg="$candidate"; break; }
    done
    if [ -z "$boot_cfg" ]; then
        warn "Could not find the Raspberry Pi boot config - skipping the I2C speed check"
        return 0
    fi

    # The speed the kernel actually uses. It has to be read from the device
    # tree node behind /dev/i2c-1 - the "brcmstb-i2c ... @97500hz" lines in
    # dmesg belong to the LEGACY 40-pin header buses (i2c@7ef04500 /
    # i2c@7ef09500), which are NOT the bus the MLX90640 sits on.
    local effective_hz=""
    local i2c_node
    i2c_node="$(readlink -f /sys/bus/i2c/devices/i2c-1/of_node 2>/dev/null || true)"
    if [ -n "$i2c_node" ] && [ -r "$i2c_node/clock-frequency" ]; then
        effective_hz="$(od -An -tu4 --endian=big "$i2c_node/clock-frequency" 2>/dev/null | tr -d ' ')"
    fi

    if grep -q "i2c_arm_baudrate=400000" "$boot_cfg"; then
        if [ -n "$effective_hz" ] && [ "$effective_hz" -ge 300000 ] 2>/dev/null; then
            ok "I2C bus is running at ${effective_hz} Hz (the MLX90640 frame rate is at its maximum)"
            return 0
        fi
        # The setting is in the boot config but the running kernel still uses
        # the old speed. It only takes effect after a reboot.
        if [ -n "$effective_hz" ]; then
            warn "The I2C bus is configured for 400 kHz but is running at ${effective_hz} Hz"
        fi
        warn "REBOOT REQUIRED for the faster thermal feed:  sudo reboot"
        return 0
    fi

    if sed -i 's/^dtparam=i2c_arm=on$/dtparam=i2c_arm=on,i2c_arm_baudrate=400000/' "$boot_cfg" 2>/dev/null \
       || sed -i 's/^#dtparam=i2c_arm=on$/dtparam=i2c_arm=on,i2c_arm_baudrate=400000/' "$boot_cfg" 2>/dev/null; then
        fix "Set the I2C bus to 400 kHz in $boot_cfg (reboot to apply - the thermal camera then runs ~4x faster)"
        warn "REBOOT REQUIRED for the faster thermal feed:  sudo reboot"
    else
        warn "Could not update $boot_cfg (needs root)."
        warn "Add this line manually for the faster MLX90640 frame rate:"
        warn "  sudo sed -i 's/^dtparam=i2c_arm=on\$/dtparam=i2c_arm=on,i2c_arm_baudrate=400000/' $boot_cfg"
    fi
}

ensure_i2c_baudrate

if ! cmd_exists python3; then
    die "python3 is missing." "Install it:  sudo apt-get install -y python3 python3-venv"
fi
info "Python        : $(python3 --version 2>&1)"

PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')"
[ "$PY_OK" = "1" ] || die "Python 3.9+ is required (found $(python3 --version 2>&1))." \
    "Install a newer Python:  sudo apt-get install -y python3"

# =============================================================================
# 04 - Backend dependencies
# =============================================================================
log_step "Backend Python environment"

VENV_DIR="$BACKEND_DIR/venv"
VENV_CREATED=0
if [ ! -d "$VENV_DIR" ] || [ ! -x "$VENV_DIR/bin/python" ]; then
    info "Creating the Python virtual environment..."
    rm -rf "$VENV_DIR"
    # Created as the real user so pip can update it later without root.
    as_user python3 -m venv "$VENV_DIR" || python3 -m venv "$VENV_DIR" \
        || die "Could not create the virtualenv at $VENV_DIR" \
        "Install the venv module:  sudo apt-get install -y python3-venv"
    fix "Created virtualenv: $VENV_DIR"
    VENV_CREATED=1
else
    ok "Virtualenv already present: $VENV_DIR"
fi

# A venv created by an earlier root-run is root-owned: the user then cannot
# `pip install` into it. Hand it back.
repair_ownership "$VENV_DIR"

if [ "$SKIP_DEPS" = "1" ]; then
    info "--skip-deps given: not reinstalling Python packages"
else
    info "Installing/verifying Python packages..."
    if [ "$VENV_CREATED" = "1" ]; then
        # Only bootstrap pip on a fresh venv - upgrading it on every run wastes time.
        as_user "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip wheel >/dev/null 2>&1 || true
    fi
    if ! as_user "$VENV_DIR/bin/python" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"; then
        warn "pip install failed - retrying with --legacy-peer-deps"
        as_user "$VENV_DIR/bin/python" -m pip install --quiet --legacy-peer-deps -r "$BACKEND_DIR/requirements.txt" \
            || die "pip install -r requirements.txt failed." \
               "Install the failing package manually, e.g.:" \
               "  sudo $VENV_DIR/bin/pip install -r $BACKEND_DIR/requirements.txt"
        fix "Recovered the pip install with --legacy-peer-deps"
    else
        ok "Python requirements installed"
    fi
    repair_ownership "$VENV_DIR"
fi

MISSING_PY=""
for mod in fastapi uvicorn numpy scipy PIL matplotlib; do
    "$VENV_DIR/bin/python" -c "import $mod" >/dev/null 2>&1 || MISSING_PY="$MISSING_PY $mod"
done
if [ -n "$MISSING_PY" ]; then
    warn "Optional modules missing:$MISSING_PY"
    info "  (the thermal feed falls back to a simpler renderer without them)"
else
    ok "Core Python modules importable (fastapi, uvicorn, numpy, scipy, pillow, matplotlib)"
fi

if ! "$VENV_DIR/bin/python" -c "import adafruit_mlx90640" >/dev/null 2>&1; then
    warn "adafruit_mlx90640 is not installed - the MLX90640 thermal sensor will be reported as unavailable"
    info "  Install it with:  sudo $VENV_DIR/bin/pip install adafruit-blinka adafruit-circuitpython-mlx90640"
fi

# =============================================================================
# 05 - Backend configuration
# =============================================================================
log_step "Backend configuration"

mkdir -p "$ENV_DIR" "$STATE_DIR"

# --- env file (single source of truth for the whole system) -----------------
ensure_env_key AERIS_PORT "$DEFAULT_PORT"
ensure_env_key AERIS_PROJECT_DIR "$PROJECT_DIR" 1
ensure_env_key AERIS_BACKEND_DIR "$BACKEND_DIR" 1
ensure_env_key AERIS_FRONTEND_DIR "$FRONTEND_DIR" 1
ensure_env_key AERIS_HOTSPOT_CON "$HOTSPOT_CON" 1
ensure_env_key AERIS_HOTSPOT_SSID "$HOTSPOT_SSID"
ensure_env_key AERIS_HOTSPOT_IP "$HOTSPOT_IP" 1
chmod 0640 "$ENV_FILE"
PORT="$(resolve_port)"
info "Backend port  : $PORT (source: /etc/aeris/aeris.env or systemd unit)"
ok "Environment file ready: $ENV_FILE"

# --- auth token ------------------------------------------------------------
TOKEN_FILE="$BACKEND_DIR/.aeris_auth_token"
if [ ! -f "$TOKEN_FILE" ]; then
    info "No auth token yet - generating one..."
    ( umask 077; "$VENV_DIR/bin/python" -c "import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))" > "$TOKEN_FILE" )
    fix "Generated a new operator access token (mode 600)"
else
    ok "Operator access token present"
fi
chmod 0600 "$TOKEN_FILE"
chown root:root "$TOKEN_FILE" 2>/dev/null || true
info "Token file    : $TOKEN_FILE (mode 600, never printed by this script)"

# --- database & data directory permissions ---------------------------------
DB_FILE="$BACKEND_DIR/aeris_detections.db"
if [ -f "$DB_FILE" ]; then
    chmod 0644 "$DB_FILE" 2>/dev/null || true
    # The service writes it as root, but the user must still be able to run the
    # backend locally in development mode.
    chown_to_real_user "$DB_FILE"
    ok "Detection database preserved: $DB_FILE"
else
    info "Detection database will be created on first use"
fi

# The service runs as root, so it leaves root-owned __pycache__ folders behind
# that would block the normal user from running python in the backend folder.
if [ -n "$REAL_USER" ]; then
    while IFS= read -r cache_dir; do
        chown -R "$REAL_USER" "$cache_dir" 2>/dev/null || true
    done < <(find "$BACKEND_DIR" -maxdepth 2 -type d -name '__pycache__' 2>/dev/null)
fi

# --- source syntax / import validation -------------------------------------
if ! "$VENV_DIR/bin/python" -m py_compile "$BACKEND_DIR/main.py"; then
    die "main.py has a syntax error." \
        "Fix the reported error in $BACKEND_DIR/main.py and re-run this script."
fi
ok "main.py compiles cleanly"

IMPORT_OUT="$("$VENV_DIR/bin/python" -c "
import sys, io, contextlib
sys.path.insert(0, '$BACKEND_DIR')
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        import main
except Exception as exc:
    print('IMPORT_ERROR: %s: %s' % (type(exc).__name__, exc))
    sys.exit(1)
print('IMPORT_OK')
" 2>&1 | tail -5)"
if printf '%s' "$IMPORT_OUT" | grep -q "IMPORT_OK"; then
    ok "Backend application imports cleanly"
else
    bad "Backend application failed to import"
    printf '%s\n' "$IMPORT_OUT" | redact | sed 's/^/      /'
    die "The backend module could not be imported." \
        "Install the missing dependency:" \
        "  sudo $VENV_DIR/bin/pip install -r $BACKEND_DIR/requirements.txt" \
        "Then re-run:  sudo $SCRIPT_PATH"
fi

# --- real hardware inventory ------------------------------------------------
# Report what is ACTUALLY connected. Missing hardware is reported as such -
# the dashboard shows N/A / Disconnected instead of invented values.
HARDWARE_OUT="$("$VENV_DIR/bin/python" -c "
import sys, json
sys.path.insert(0, '$BACKEND_DIR')
try:
    import sensors
    data = sensors.read_all()
    for name, info in data.items():
        if info.get('available'):
            detail = ''
            if name == 'lora':
                detail = '%s @ %s MHz' % (info.get('model'), info.get('frequency_mhz'))
            elif name == 'gps':
                detail = '%s, %s sats' % (info.get('device'), info.get('satellites'))
            elif name == 'battery':
                detail = '%s, %s V' % (info.get('chip'), info.get('voltage_v'))
            elif name == 'barometer':
                detail = '%s, %s hPa' % (info.get('chip'), info.get('pressure_hpa'))
            elif name == 'compass':
                detail = '%s, %s deg' % (info.get('chip'), info.get('heading_deg'))
            print('DETECTED\t%s\t%s' % (name, detail))
        else:
            print('MISSING\t%s\t%s' % (name, (info.get('reason') or '')))
except Exception as exc:
    print('ERROR\t\t%s' % exc)
" 2>/dev/null | grep -E '^(DETECTED|MISSING|ERROR)')"

if [ -n "$HARDWARE_OUT" ]; then
    info "Real hardware inventory:"
    while IFS=$'\t' read -r state name detail; do
        case "$state" in
            DETECTED) ok   "$name: $detail" ;;
            MISSING)  warn "$name: not connected - ${detail:-no reason reported}" ;;
            *)        warn "hardware probe error: $detail" ;;
        esac
    done <<< "$HARDWARE_OUT"
else
    warn "Could not run the hardware inventory (needs the I2C/SPI permissions)"
fi

# =============================================================================
# 06 - Frontend dependencies
# =============================================================================
log_step "Frontend dependencies"

cd "$FRONTEND_DIR"

# node_modules created by an earlier root-run is root-owned, which breaks
# `npm run dev` for the normal user (EACCES on node_modules/.vite-temp).
repair_ownership "$FRONTEND_DIR/node_modules"

if [ "$SKIP_DEPS" = "1" ]; then
    info "--skip-deps given: not installing npm packages"
elif [ -d node_modules ] && [ -f package-lock.json ] \
     && [ node_modules -nt package-lock.json ] 2>/dev/null \
     && [ -x node_modules/.bin/vite ] \
     && { [ -z "$REAL_USER" ] || [ -z "$(find node_modules ! -user "$REAL_USER" -print -quit 2>/dev/null)" ]; }; then
    ok "node_modules is newer than package-lock.json and fully owned by '$REAL_USER' - nothing to install"
else
    info "Installing npm packages (this can take a few minutes)..."
    if [ -f package-lock.json ]; then
        if ! run_logged as_user npm ci --no-audit --no-fund; then
            warn "npm ci failed - falling back to npm install"
            run_logged as_user npm install --no-audit --no-fund \
                || die "npm install failed." \
                       "Check the npm registry connection and retry:" \
                       "  cd $FRONTEND_DIR && npm install"
            fix "Recovered the npm install with 'npm install'"
        fi
    else
        run_logged as_user npm install --no-audit --no-fund \
            || die "npm install failed." \
                   "Check the npm registry connection and retry:" \
                   "  cd $FRONTEND_DIR && npm install"
    fi
    repair_ownership "$FRONTEND_DIR/node_modules"
    fix "npm dependencies installed"
fi

# =============================================================================
# 07 - Frontend build
# =============================================================================
log_step "Frontend production build"

if [ "$SKIP_BUILD" = "1" ]; then
    info "--skip-build given: using the existing dist/"
else
    info "Running the TypeScript type check..."
    if run_logged as_user npx --no-install tsc -b --pretty false; then
        ok "TypeScript check passed"
    else
        die "The TypeScript check failed." \
            "Fix the type errors listed above in $FRONTEND_DIR/src and re-run:" \
            "  cd $FRONTEND_DIR && npx tsc -b"
    fi

    info "Building the production bundle (Vite)..."
    run_logged as_user npm run build \
        || die "The Vite production build failed." \
               "Fix the build errors above and re-run:" \
               "  cd $FRONTEND_DIR && npm run build"
    fix "Frontend production build completed"
fi

if [ -f "$FRONTEND_DIR/dist/index.html" ]; then
    ok "Build output present: $FRONTEND_DIR/dist/index.html"
else
    die "No build output at $FRONTEND_DIR/dist/index.html" \
        "Run the build manually:" \
        "  cd $FRONTEND_DIR && npm ci && npm run build"
fi

if [ -d "$FRONTEND_DIR/dist/assets" ] && [ -n "$(ls -A "$FRONTEND_DIR/dist/assets" 2>/dev/null)" ]; then
    ok "Bundled assets present ($(ls -1 "$FRONTEND_DIR/dist/assets" | wc -l) files)"
else
    warn "No hashed assets found in dist/assets - the dashboard may not load correctly"
fi

chown_to_real_user "$FRONTEND_DIR/dist"

# Clean up leftovers from an earlier root-owned install (the directory can only
# be removed with root, which is exactly what this script has).
for stale in "$FRONTEND_DIR/node_modules.root-owned" "$FRONTEND_DIR/node_modules.old" "$BACKEND_DIR/venv.root-owned"; do
    if [ -e "$stale" ]; then
        info "Removing leftover directory: $stale"
        rm -rf "$stale" 2>/dev/null || warn "Could not remove $stale - run: sudo rm -rf '$stale'"
    fi
done

# Guard against the "root-owned node_modules" regression: it breaks `npm run dev`
# and `npm install` for the normal user with an EACCES error.
if [ -n "$REAL_USER" ]; then
    if [ -d "$FRONTEND_DIR/node_modules" ] \
       && [ -n "$(find "$FRONTEND_DIR/node_modules" ! -user "$REAL_USER" -print -quit 2>/dev/null)" ]; then
        warn "node_modules still contains root-owned files - 'npm run dev' would fail with EACCES"
        repair_ownership "$FRONTEND_DIR/node_modules"
    fi
    if [ ! -w "$FRONTEND_DIR/node_modules" ]; then
        warn "The user cannot write to node_modules - repairing"
        repair_ownership "$FRONTEND_DIR/node_modules"
    fi
    if [ -d "$FRONTEND_DIR/node_modules" ] \
       && [ -z "$(find "$FRONTEND_DIR/node_modules" ! -user "$REAL_USER" -print -quit 2>/dev/null)" ]; then
        ok "node_modules is fully owned by '$REAL_USER' (npm run dev / npm install work)"
    else
        warn "node_modules still has root-owned files - run 'sudo $SCRIPT_PATH' again"
    fi
fi

# =============================================================================
# 08 - systemd configuration
# =============================================================================
log_step "systemd services"

# Rewrite the paths inside the unit files so the project can live anywhere.
UNIT_SRC="$PROJECT_DIR/ground-station/aeris-backend.service"
GUARD_SRC="$PROJECT_DIR/aeris-hotspot-guard.service"
BOOT_UNIT_SRC="$PROJECT_DIR/ground-station/aeris-boot-check.service"
TARGET_SRC="$PROJECT_DIR/ground-station/aeris.target"
WATCHDOG_SRC="$PROJECT_DIR/ground-station/aeris-watchdog.service"
WATCHDOG_TIMER_SRC="$PROJECT_DIR/ground-station/aeris-watchdog.timer"
BOOT_UNIT="$UNIT_DIR/aeris-boot-check.service"
UNIT_TMP="$(mktemp)"; GUARD_TMP="$(mktemp)"; BOOT_TMP="$(mktemp)"
TARGET_TMP="$(mktemp)"; WATCHDOG_TMP="$(mktemp)"; TIMER_TMP="$(mktemp)"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$UNIT_SRC" > "$UNIT_TMP"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$GUARD_SRC" > "$GUARD_TMP"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$BOOT_UNIT_SRC" > "$BOOT_TMP"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$TARGET_SRC" > "$TARGET_TMP"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$WATCHDOG_SRC" > "$WATCHDOG_TMP"

sed -e "s|/home/piaeris/aeris-project|$PROJECT_DIR|g" \
    "$WATCHDOG_TIMER_SRC" > "$TIMER_TMP"

install_unit "$UNIT_TMP" "$BACKEND_UNIT"
install_unit "$GUARD_TMP" "$GUARD_UNIT"
install_unit "$BOOT_TMP" "$BOOT_UNIT"
install_unit "$TARGET_TMP" "$TARGET_UNIT"
install_unit "$WATCHDOG_TMP" "$WATCHDOG_UNIT"
install_unit "$TIMER_TMP" "$WATCHDOG_TIMER"
rm -f "$UNIT_TMP" "$GUARD_TMP" "$BOOT_TMP" "$TARGET_TMP" "$WATCHDOG_TMP" "$TIMER_TMP"

chmod 0755 "$PROJECT_DIR/ground-station/hotspot_guard.sh"
chmod 0755 "$PROJECT_DIR/ground-station/boot_check.sh"
chmod 0755 "$PROJECT_DIR/ground-station/watchdog.sh"

systemctl daemon-reload
ok "systemd units reloaded"

systemctl enable aeris-backend >/dev/null 2>&1 && ok "aeris-backend enabled at boot" \
    || warn "Could not enable aeris-backend at boot"
systemctl enable aeris-hotspot-guard >/dev/null 2>&1 && ok "aeris-hotspot-guard enabled at boot" \
    || warn "Could not enable aeris-hotspot-guard at boot"
systemctl enable aeris-boot-check >/dev/null 2>&1 && ok "aeris-boot-check enabled at boot" \
    || warn "Could not enable aeris-boot-check at boot"
systemctl enable aeris.target >/dev/null 2>&1 && ok "aeris.target enabled at boot" \
    || warn "Could not enable aeris.target at boot"
systemctl enable aeris-watchdog.timer >/dev/null 2>&1 && ok "aeris-watchdog.timer enabled at boot" \
    || warn "Could not enable aeris-watchdog.timer at boot"

# --- Log persistence -------------------------------------------------------
# The AERIS log files live on the SD card. Without rotation they grow until
# the card is full, which is exactly the failure mode that has to survive a
# power cut. The drop-in below is additive: no existing logrotate rule or
# journald setting is removed.
if [ -d /etc/logrotate.d ] && [ -f "$PROJECT_DIR/ground-station/aeris-logrotate.conf" ]; then
    install_file "$PROJECT_DIR/ground-station/aeris-logrotate.conf" "$LOGROTATE_FILE"
    chmod 0644 "$LOGROTATE_FILE"
    ok "log rotation installed: $LOGROTATE_FILE"
else
    warn "log rotation not configured (missing /etc/logrotate.d or the config file)"
fi

if [ -f "$PROJECT_DIR/ground-station/aeris-journald.conf" ]; then
    mkdir -p "$JOURNALD_DIR"
    install_file "$PROJECT_DIR/ground-station/aeris-journald.conf" "$JOURNALD_FILE"
    chmod 0644 "$JOURNALD_FILE"
    systemctl restart systemd-journald >/dev/null 2>&1 || true
    ok "journal size limit installed: $JOURNALD_FILE"
else
    warn "journal size limit not configured (config file missing)"
fi

# The hotspot must come back after a reboot -> remove the "disabled" marker.
rm -f "$HOTSPOT_DISABLE_MARKER"

info "Restarting aeris-backend..."
systemctl restart aeris-backend || die "systemctl restart aeris-backend failed." \
    "Inspect the unit:  sudo systemctl cat aeris-backend" \
    "Inspect the logs:  sudo journalctl -u aeris-backend -n 50"

sleep 2
if systemctl is-active --quiet aeris-backend; then
    ok "aeris-backend is active"
else
    systemctl is-active --quiet aeris-backend || true
    warn "aeris-backend is not active yet - the health check below will retry"
fi

# =============================================================================
# 09 - Backend health
# =============================================================================
log_step "Backend health validation"

HEALTH_URL="http://127.0.0.1:${PORT}/health"
HEALTH_BODY="$(wait_for_health "$HEALTH_URL" 30 1 || true)"

if printf '%s' "$HEALTH_BODY" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok "Backend health check passed: $HEALTH_BODY"
else
    bad "Backend health check failed"
    printf '      Last response: %s\n' "$(printf '%s' "$HEALTH_BODY" | head -c 300 | redact)"

    echo
    info "Diagnosing..."
    printf '      --- systemctl status ---\n'
    systemctl status aeris-backend --no-pager -l 2>&1 | head -20 | redact | sed 's/^/      /'
    printf '      --- journal (last 40 lines) ---\n'
    journalctl -u aeris-backend -n 40 --no-pager 2>&1 | redact | sed 's/^/      /'
    printf '      --- port %s usage ---\n' "$PORT"
    ss -tlnp 2>/dev/null | grep -E "[:.]${PORT}\b" | sed 's/^/      /' || echo "      (nothing is listening on port $PORT)"
    echo

    die "The backend is not answering on http://127.0.0.1:${PORT}/health." \
        "Read the diagnostics above; the most common causes are:" \
        "  - the port is taken by another process (see 'port usage' above)" \
        "  - a missing Python dependency (see the journal output)" \
        "  - a stale unit file:  sudo cp $PROJECT_DIR/ground-station/aeris-backend.service /etc/systemd/system/ && sudo systemctl daemon-reload"
fi

# =============================================================================
# 10 - Frontend availability
# =============================================================================
log_step "Frontend availability"

FRONTEND_BODY="$(curl -fsS -m 10 "http://127.0.0.1:${PORT}/" 2>/dev/null || true)"
if printf '%s' "$FRONTEND_BODY" | grep -q "<title>AERIS</title>"; then
    ok "Dashboard served by the backend (title: AERIS)"
else
    TITLE_FOUND="$(printf '%s' "$FRONTEND_BODY" | grep -o '<title>[^<]*</title>' | head -1 || true)"
    bad "The dashboard page was not served correctly"
    die "http://127.0.0.1:${PORT}/ did not return the AERIS dashboard." \
        "Rebuild the frontend and restart the backend:" \
        "  cd $FRONTEND_DIR && sudo npm ci && sudo npm run build" \
        "  sudo systemctl restart aeris-backend" \
        "Detected title: ${TITLE_FOUND:-none}"
fi

ASSET="$(printf '%s' "$FRONTEND_BODY" | grep -o '/assets/[A-Za-z0-9._-]*\.js' | head -1 || true)"
if [ -n "$ASSET" ]; then
    ASSET_CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${PORT}${ASSET}" || echo 000)"
    if [ "$ASSET_CODE" = "200" ]; then
        ok "JavaScript bundle served correctly ($ASSET)"
    else
        bad "The JavaScript bundle returned HTTP $ASSET_CODE"
        die "The frontend bundle is not being served." \
            "Rebuild it and restart the backend:" \
            "  cd $FRONTEND_DIR && sudo npm run build && sudo systemctl restart aeris-backend"
    fi
else
    warn "Could not find a hashed JS bundle reference in index.html"
fi

# =============================================================================
# 11 - Backend API connectivity
# =============================================================================
log_step "Backend API connectivity"

TOKEN="$(cat "$TOKEN_FILE" 2>/dev/null || true)"

if [ -z "$TOKEN" ]; then
    bad "The operator token could not be read from $TOKEN_FILE"
    die "The auth token file is missing or unreadable." \
        "Regenerate it with:" \
        "  sudo rm -f $TOKEN_FILE && sudo systemctl restart aeris-backend"
fi

api_get() {
    curl -s -o /tmp/aeris_api_out -w '%{http_code}' -m 10 \
        -H "Authorization: Bearer $TOKEN" "$1" 2>/dev/null || echo "000"
}

CODE="$(api_get "http://127.0.0.1:${PORT}/api/system")"
if [ "$CODE" = "200" ]; then
    ok "/api/system responded 200"
    printf '      %s\n' "$(head -c 220 /tmp/aeris_api_out | redact)"
else
    die "/api/system returned HTTP $CODE (expected 200)" \
        "Check the backend logs:  sudo journalctl -u aeris-backend -n 50"
fi

CODE="$(api_get "http://127.0.0.1:${PORT}/api/hardware_status")"
if [ "$CODE" = "200" ]; then
    ok "/api/hardware_status responded 200"
    printf '      %s\n' "$(head -c 220 /tmp/aeris_api_out | redact)"
else
    die "/api/hardware_status returned HTTP $CODE (expected 200)" \
        "Check the backend logs:  sudo journalctl -u aeris-backend -n 50"
fi

CODE="$(api_get "http://127.0.0.1:${PORT}/api/telemetry")"
[ "$CODE" = "200" ] && ok "/api/telemetry responded 200" \
    || bad "/api/telemetry returned HTTP $CODE (expected 200)"

CODE="$(api_get "http://127.0.0.1:${PORT}/api/thermal/status")"
if [ "$CODE" = "200" ]; then
    # The MLX90640 capture thread needs a moment to initialise after a restart,
    # so give it a few seconds before deciding the sensor is broken.
    THERMAL_STATE="unknown"
    T=0
    while [ "$T" -lt 20 ]; do
        THERMAL_STATE="$(grep -o '"ok":[a-z]*' /tmp/aeris_api_out | head -1 | cut -d: -f2 || echo unknown)"
        [ "$THERMAL_STATE" = "true" ] && break
        T=$((T + 1))
        sleep 1
        api_get "http://127.0.0.1:${PORT}/api/thermal/status" >/dev/null
    done
    if [ "$THERMAL_STATE" = "true" ]; then
        ok "MLX90640 thermal sensor is live (/api/thermal/status ok=true)"
    else
        warn "The thermal sensor did not report ready (ok=$THERMAL_STATE)."
        info "  This is not fatal - check the I2C bus with:  sudo i2cdetect -y 1"
    fi
else
    warn "/api/thermal/status returned HTTP $CODE"
fi

# Authentication must be enforced (no token -> 401).
UNAUTH_CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:${PORT}/api/telemetry" 2>/dev/null || echo 000)"
if [ "$UNAUTH_CODE" = "401" ]; then
    ok "API authentication enforced (unauthenticated request -> 401)"
elif [ "$UNAUTH_CODE" = "403" ]; then
    ok "API access control enforced (unauthenticated request -> 403, LAN only)"
else
    bad "An unauthenticated API request returned HTTP $UNAUTH_CODE (expected 401)"
fi

rm -f /tmp/aeris_api_out
unset TOKEN

# =============================================================================
# 12 - Hotspot and network
# =============================================================================
log_step "Wi-Fi hotspot and network"

if systemctl is-active --quiet NetworkManager; then
    ok "NetworkManager is running"
else
    info "Starting NetworkManager..."
    systemctl start NetworkManager || true
    sleep 3
    systemctl is-active --quiet NetworkManager \
        && ok "NetworkManager is running" \
        || die "NetworkManager could not be started." "Fix it with:  sudo systemctl status NetworkManager"
fi

# Find the Wi-Fi interface.
WIFI_IFACE=""
for iface in /sys/class/net/*/wireless; do
    [ -e "$iface" ] || continue
    WIFI_IFACE="$(basename "$(dirname "$iface")")"
    break
done
if [ -z "$WIFI_IFACE" ]; then
    WIFI_IFACE="$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | grep ':wifi$' | head -1 | cut -d: -f1 || true)"
fi
if [ -z "$WIFI_IFACE" ]; then
    die "No Wi-Fi interface was found." \
        "The hotspot needs one. Check with:  nmcli device status" \
        "If the interface is soft-blocked:  sudo rfkill unblock wifi"
fi
info "Wi-Fi interface: $WIFI_IFACE"

if [ "$(nmcli radio wifi 2>/dev/null || echo enabled)" != "enabled" ]; then
    warn "Wi-Fi radio is off - enabling it"
    nmcli radio wifi on || warn "Could not enable the Wi-Fi radio"
fi

# Create the hotspot connection if it is missing.
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -q "^${HOTSPOT_CON}$"; then
    warn "Hotspot connection '$HOTSPOT_CON' not found - creating it"
    HOTSPOT_PSK="$(env_value AERIS_HOTSPOT_PSK '')"
    if [ -z "$HOTSPOT_PSK" ]; then
        HOTSPOT_PSK="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 14)"
        ensure_env_key AERIS_HOTSPOT_PSK "$HOTSPOT_PSK"
        chmod 0640 "$ENV_FILE"
        info "A new hotspot passphrase was generated and stored in $ENV_FILE"
        info "Read it with:  sudo grep AERIS_HOTSPOT_PSK $ENV_FILE"
    fi
    nmcli con add type wifi ifname "$WIFI_IFACE" con-name "$HOTSPOT_CON" \
        autoconnect yes wifi.mode ap wifi.ssid "$HOTSPOT_SSID" wifi.band bg wifi.channel 6 \
        wifi.powersave 2 \
        ipv4.method shared ipv4.addresses "${HOTSPOT_IP}/24" \
        >/dev/null 2>&1 \
        || die "Could not create the hotspot connection '$HOTSPOT_CON'." \
               "Check the Wi-Fi adapter:  iw list | grep -A 8 'Supported interface modes'"
    nmcli con modify "$HOTSPOT_CON" wifi-sec.key-mgmt wpa-psk >/dev/null 2>&1 || true
    nmcli con modify "$HOTSPOT_CON" wifi-sec.psk "$HOTSPOT_PSK" >/dev/null 2>&1 || true
    fix "Created hotspot connection '$HOTSPOT_CON'"
else
    ok "Hotspot connection '$HOTSPOT_CON' exists"
    # Make sure it survives a reboot.
    nmcli con modify "$HOTSPOT_CON" connection.autoconnect yes >/dev/null 2>&1 || true
    # Keep the advertised IP consistent with the configuration.
    CURRENT_IP="$(nmcli -g ipv4.addresses connection show "$HOTSPOT_CON" 2>/dev/null | head -1 | cut -d/ -f1 || true)"
    if [ -n "$CURRENT_IP" ] && [ "$CURRENT_IP" != "$HOTSPOT_IP" ]; then
        warn "Hotspot IP is $CURRENT_IP but $HOTSPOT_IP is configured - updating"
        nmcli con modify "$HOTSPOT_CON" ipv4.addresses "${HOTSPOT_IP}/24" >/dev/null 2>&1 || true
        fix "Hotspot IP set to $HOTSPOT_IP"
    fi
fi

# Disable Wi-Fi power save on the AP. Power save on an access point is a
# known source of missed beacons and multi-second buffered-frame stalls on
# brcmfmac, which the client sees as a link drop. This is the persistent
# NetworkManager setting; the runtime 'iw' command below applies it at once
# (and hotspot_guard.sh re-asserts it every few seconds).
nmcli con modify "$HOTSPOT_CON" 802-11-wireless.powersave 2 >/dev/null 2>&1 \
    || warn "Could not set 802-11-wireless.powersave=2 on '$HOTSPOT_CON'"
if command -v iw >/dev/null 2>&1; then
    iw dev "$WIFI_IFACE" set power_save off >/dev/null 2>&1 \
        || warn "Could not disable Wi-Fi power save on $WIFI_IFACE"
fi

# Bring it up.
if ! nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}$"; then
    info "Activating the hotspot..."
    nmcli con up "$HOTSPOT_CON" >/dev/null 2>&1 || true
    sleep 3
fi

if nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}$"; then
    ok "Hotspot '$HOTSPOT_CON' is active (SSID: $HOTSPOT_SSID)"
else
    warn "The hotspot is not active - the guard service will retry every few seconds"
fi

# Runtime verification: the profile may say "disable" while the driver still
# has power save on. Re-assert it and report the measured state.
if command -v iw >/dev/null 2>&1; then
    iw dev "$WIFI_IFACE" set power_save off >/dev/null 2>&1 || true
    POWER_SAVE_NOW="$(iw dev "$WIFI_IFACE" get power_save 2>/dev/null | awk -F': ' '{print $2}' | head -1)"
    if [ "$POWER_SAVE_NOW" = "off" ]; then
        ok "Wi-Fi power save is off on $WIFI_IFACE"
    else
        warn "Wi-Fi power save is still '${POWER_SAVE_NOW:-unknown}' on $WIFI_IFACE - the hotspot guard will retry"
    fi
else
    warn "'iw' is not installed - cannot verify the Wi-Fi power-save state"
fi

systemctl restart aeris-hotspot-guard >/dev/null 2>&1 || true
if systemctl is-active --quiet aeris-hotspot-guard; then
    ok "aeris-hotspot-guard is running"
else
    warn "aeris-hotspot-guard is not running - check: sudo journalctl -u aeris-hotspot-guard -n 20"
fi

# Verify the hotspot IP.
if ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/"; then
    ok "$HOTSPOT_IP is configured"
else
    warn "$HOTSPOT_IP is not assigned yet - waiting 5s"
    sleep 5
    if ip -4 addr show 2>/dev/null | grep -q "inet ${HOTSPOT_IP}/"; then
        ok "$HOTSPOT_IP is configured"
    else
        bad "$HOTSPOT_IP is not configured on any interface"
        info "Current addresses:"
        ip -4 addr show 2>/dev/null | grep -E "inet " | sed 's/^/      /'
        die "The hotspot did not get the address $HOTSPOT_IP." \
            "Recreate the hotspot connection:" \
            "  sudo nmcli con down $HOTSPOT_CON && sudo nmcli con delete $HOTSPOT_CON" \
            "  sudo nmcli con add type wifi ifname $WIFI_IFACE con-name $HOTSPOT_CON autoconnect yes wifi.mode ap wifi.ssid $HOTSPOT_SSID wifi.band bg wifi.channel 6 ipv4.method shared ipv4.addresses $HOTSPOT_IP/24"
    fi
fi

# The backend must be reachable through the hotspot address.
HOTSPOT_HEALTH="$(curl -fsS -m 8 "http://${HOTSPOT_IP}/health" 2>/dev/null || true)"
if printf '%s' "$HOTSPOT_HEALTH" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok "Backend reachable through the hotspot: http://${HOTSPOT_IP}/health"
else
    warn "The backend did not answer on http://${HOTSPOT_IP}/health"
    info "The firewall may be blocking it. Check with:  sudo nft list ruleset | grep -i ${PORT}"
fi

# mDNS (aeris.local) - optional but nice.
if has_pkg avahi-daemon; then
    systemctl enable --now avahi-daemon >/dev/null 2>&1 || true
    systemctl is-active --quiet avahi-daemon && ok "mDNS (avahi) active - aeris.local available" \
        || warn "avahi-daemon is installed but not active"
else
    warn "avahi-daemon is not installed - aeris.local will not resolve"
fi

# =============================================================================
# 13 - Boot survival
# =============================================================================
log_step "Boot survival check"

for unit in aeris-backend aeris-hotspot-guard aeris-boot-check aeris.target aeris-watchdog.timer; do
    if systemctl is-enabled --quiet "$unit"; then
        ok "$unit is enabled (starts automatically after a reboot)"
    else
        warn "$unit is NOT enabled - fixing"
        systemctl enable "$unit" >/dev/null 2>&1 && fix "$unit enabled at boot" \
            || bad "$unit could not be enabled"
    fi
done

if systemctl is-active --quiet aeris-backend; then
    ok "aeris-backend is active"
else
    bad "aeris-backend is not active"
fi

BACKEND_PID="$(systemctl show aeris-backend -p MainPID --value 2>/dev/null || echo 0)"
if [ "${BACKEND_PID:-0}" != "0" ]; then
    ok "aeris-backend is running (PID $BACKEND_PID)"
fi

# Run the same verification that runs after a real reboot, right now.
info "Running the post-boot verification (same checks as after a real reboot)..."
sudo systemctl restart aeris-boot-check.service >/dev/null 2>&1 || true
sleep 2
BOOT_RESULT="$(tail -n 12 /var/log/aeris-boot-check.log 2>/dev/null || true)"
if printf '%s' "$BOOT_RESULT" | grep -q "boot verification PASSED"; then
    ok "Post-boot verification PASSED (see /var/log/aeris-boot-check.log)"
elif printf '%s' "$BOOT_RESULT" | grep -q "boot verification FAILED"; then
    bad "Post-boot verification FAILED"
    printf '%s\n' "$BOOT_RESULT" | sed 's/^/      /'
else
    warn "Post-boot verification produced no result yet"
fi

ELAPSED=$(( $(date +%s) - START_EPOCH ))
ok "Deployment finished in ${ELAPSED}s"

# =============================================================================
# 14 - Deployment report
# =============================================================================
log_step "Deployment report"

BACKEND_STATE="$(systemctl is-active aeris-backend 2>/dev/null || echo inactive)"
GUARD_STATE="$(systemctl is-active aeris-hotspot-guard 2>/dev/null || echo inactive)"
HOTSPOT_STATE="$(nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${HOTSPOT_CON}$" && echo active || echo inactive)"
DASH_STATE="$(curl -fsS -m 5 "http://127.0.0.1:${PORT}/" 2>/dev/null | grep -q '<title>AERIS</title>' && echo reachable || echo unreachable)"
API_STATE="$(curl -fsS -m 5 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"status"' && echo healthy || echo unhealthy)"

printf '\n'
printf '  %sAERIS DEPLOYMENT REPORT%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
printf '  %s%s%s\n' "$C_BOLD" "------------------------------------------------------------" "$C_RESET"
printf '  %-26s %s\n' "Project directory" "$PROJECT_DIR"
printf '  %-26s %s\n' "Backend health" "$API_STATE"
printf '  %-26s %s\n' "Dashboard" "$DASH_STATE"
printf '  %-26s %s\n' "aeris-backend.service" "$BACKEND_STATE ($(systemctl is-enabled aeris-backend 2>/dev/null || echo '?'))"
printf '  %-26s %s\n' "aeris-hotspot-guard.service" "$GUARD_STATE ($(systemctl is-enabled aeris-hotspot-guard 2>/dev/null || echo '?'))"
printf '  %-26s %s\n' "aeris-boot-check.service" "$(systemctl is-enabled aeris-boot-check 2>/dev/null || echo '?')"
printf '  %-26s %s\n' "aeris.target" "$(systemctl is-enabled aeris.target 2>/dev/null || echo '?')"
printf '  %-26s %s\n' "aeris-watchdog.timer" "$(systemctl is-enabled aeris-watchdog.timer 2>/dev/null || echo '?') ($(systemctl is-active aeris-watchdog.timer 2>/dev/null || echo 'inactive'))"
printf '  %-26s %s\n' "Hotspot ($HOTSPOT_SSID)" "$HOTSPOT_STATE"
printf '  %-26s %s\n' "Hotspot address" "$HOTSPOT_IP"
printf '  %-26s %s\n' "Backend port" "$PORT"
printf '  %-26s %s\n' "Frontend build" "$FRONTEND_DIR/dist"
printf '  %-26s %s\n' "Node / npm" "$(node -v) / $(npm -v)"
printf '  %-26s %s\n' "Python" "$(python3 --version 2>&1)"
printf '  %-26s %s\n' "Repairs applied" "$REPAIRS"
printf '  %-26s %s\n' "Warnings" "$WARNINGS"
printf '  %-26s %s\n' "Log file" "$LOG_FILE"
printf '  %s%s%s\n' "$C_BOLD" "------------------------------------------------------------" "$C_RESET"
printf '\n'
printf '  %sAccess%s\n' "$C_BOLD" "$C_RESET"
printf '    Dashboard      http://%s/\n' "$HOTSPOT_IP"
printf '    mDNS           http://aeris.local/\n'
printf '    Health         http://%s/health\n' "$HOTSPOT_IP"
printf '    Backend        http://%s/\n' "127.0.0.1:$PORT"
printf '\n'
printf '  %sOperator token%s (needed to open the dashboard)\n' "$C_BOLD" "$C_RESET"
printf '    sudo cat %s\n' "$TOKEN_FILE"
printf '\n'
printf '  %sUseful commands%s\n' "$C_BOLD" "$C_RESET"
printf '    Start / repair  sudo %s\n' "$SCRIPT_PATH"
printf '    Stop            sudo %s/stop_aeris.sh\n' "$PROJECT_DIR"
printf '    Backend logs    sudo journalctl -u aeris-backend -f\n'
printf '    Setup log       sudo tail -f %s\n' "$LOG_FILE"
printf '    Boot check      sudo cat /var/log/aeris-boot-check.log\n'
printf '\n'

if [ "$API_STATE" != "healthy" ] || [ "$BACKEND_STATE" != "active" ] || [ "$DASH_STATE" != "reachable" ]; then
    printf '%s  DEPLOYMENT INCOMPLETE - see the failures above%s\n' "$C_RED$C_BOLD" "$C_RESET"
    printf '  Log: %s\n\n' "$LOG_FILE"
    exit 1
fi

printf '%s  AERIS IS READY - one command, full system verified%s\n' "$C_GREEN$C_BOLD" "$C_RESET"
printf '  Dashboard: http://%s/   (token: sudo cat %s)\n\n' "$HOTSPOT_IP" "$TOKEN_FILE"
exit 0
