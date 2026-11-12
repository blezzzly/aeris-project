# AERIS — Automatic Startup & Self-Healing (Raspberry Pi 4B)

This document describes every component that runs on the Raspberry Pi 4B, how
they are started after a power-on, in which order, and what happens when
something breaks.

The goal is simple: **the device comes up completely on its own after every
reboot or power cut, and repairs itself without anybody being present.**

---

## 1. Components

### 1.1 On this Pi — the AERIS Ground Station

| Component | Unit | What it does |
|---|---|---|
| **Backend** | `aeris-backend.service` | FastAPI + uvicorn. Serves the REST API, the WebSocket telemetry feed and the built dashboard. Runs the MLX90640 thermal capture, the SX1278 LoRa receiver, the GPS/IMU/barometer/compass poller, the weather cache and the SQLite detection database. |
| **Hotspot guard** | `aeris-hotspot-guard.service` | Keeps the `aeris-hotspot` Wi-Fi access point (SSID `AERIS-GroundStation`, `192.168.4.1`) up. Re-activates it whenever NetworkManager drops it. |
| **Boot check** | `aeris-boot-check.service` | One-shot, runs after every boot. Verifies the whole stack and writes the result to `/var/log/aeris-boot-check.log`. |
| **Stack target** | `aeris.target` | Aggregate unit that groups the services above and defines their order. |
| **Watchdog** | `aeris-watchdog.timer` → `aeris-watchdog.service` | Runs every 5 minutes. Detects and repairs the failure modes systemd's `Restart=` cannot cover. |
| **mDNS** | `avahi-daemon.service` (distribution unit) | Makes the ground station reachable as `http://aeris.local/`. |

### 1.2 On the drone's Pi 4B — the onboard detection pipeline

| Component | Unit | What it does |
|---|---|---|
| **Onboard pipeline** | `aeris-onboard.service` | MLX90640 thermal camera → hotspot analytics → detection packet → SX1278 LoRa transmit → local Flask panel on port 5000. |

Installed with:

```bash
cd ~/aeris-project/drone-onboard/raspberry-pi-4b
sudo ./install_onboard_service.sh
```

> **The two roles cannot run on the same Raspberry Pi.**
> They drive the *same* physical sensors — the MLX90640 on I2C1 @ `0x33` and
> the SX1278 on SPI0 with GPIO23 (RESET) and GPIO24 (NSS). The ground station
> *receives* LoRa, the onboard pipeline *transmits* it; one radio cannot do
> both at once. The onboard unit therefore declares
> `Conflicts=aeris-backend.service`, and the installer refuses to enable it on
> a Pi that already runs the ground station.

---

## 2. Startup order

```
network-online.target
        │
        ├── NetworkManager.service ──► auto-activates "aeris-hotspot"
        │
        ├── aeris-hotspot-guard.service   (heals the AP every 5 s)
        │         │
        │         └── aeris-backend.service  (API, WebSocket, dashboard, hardware pipelines)
        │
        ├── aeris.target  ──► groups the two services above
        │         │
        │         └── aeris-boot-check.service  (verifies everything, writes the log)
        │
        └── aeris-watchdog.timer ──► aeris-watchdog.service  (every 5 min)
```

The dependencies are declared in the unit files themselves, not in a wrapper
script, so they hold on every boot — including after a power cut.

| Unit | Ordering |
|---|---|
| `aeris-hotspot-guard.service` | `After=network-online.target NetworkManager.service`, `Wants=NetworkManager.service` |
| `aeris-backend.service` | `After=network-online.target aeris-hotspot-guard.service`, `Wants=` both |
| `aeris.target` | `After=network-online.target aeris-hotspot-guard.service aeris-backend.service` |
| `aeris-boot-check.service` | `Requires=aeris.target`, `After=aeris.target` |
| `aeris-watchdog.service` | `After=aeris.target`, `Wants=aeris.target` |

Measured boot time on this device: **~16 seconds** from power-on to a fully
verified stack (NetworkManager up at 05:26:38, backend serving at 05:26:51,
boot check passed at 05:26:55).

---

## 3. Auto-restart and error recovery

Three independent layers protect the stack.

### Layer 1 — systemd restarts a dead process

```
Restart=always
RestartSec=5
StartLimitIntervalSec=0
```

`StartLimitIntervalSec=0` is the important part: without it systemd gives up
after five failures in 100 seconds and the unit stays dead until the next
reboot. It is set on every long-running AERIS unit.

### Layer 2 — the hotspot guard heals the Wi-Fi access point

NetworkManager occasionally drops the AP (channel change, driver hiccup,
rfkill). The guard polls every five seconds and re-activates the connection.
It never exits on its own.

The guard uses `Wants=NetworkManager.service` and **not** `Requires=`. This
was a real bug: with `Requires=`, an explicit `systemctl stop NetworkManager`
takes the guard down with it, and because `Requires=` does not pull a unit
back in, the guard never returns — the hotspot stays dead until the next
reboot. With `Wants=` the guard survives and heals the AP as soon as
NetworkManager is back.

### Layer 3 — the watchdog repairs the failures systemd cannot see

`Restart=` only covers a process that *dies*. It does nothing for a process
that is alive but broken. `aeris-watchdog.service` runs every five minutes and
checks:

| Check | Repair |
|---|---|
| `aeris-backend` unit is not active | `systemctl restart aeris-backend` |
| `aeris-backend` is active but `/health` does not answer (wedged socket) | restart it, then wait for the port to come back |
| the `aeris-hotspot` profile is missing | report it precisely — a watchdog cannot recreate it |
| the hotspot profile is not active | `nmcli con up` |
| the hotspot is active but `192.168.4.1` was never assigned | bounce the connection so NetworkManager re-runs its IP configuration |
| `avahi-daemon` is dead | restart it, so `aeris.local` resolves again |
| a unit is not enabled | re-enable it, so the *next* boot also works |

Every repair is written to `/var/log/aeris-watchdog.log`. A component that
fails three times in a row is reported instead of being restarted in a tight
loop.

---

## 4. Verification

### Automatic — after every boot

`aeris-boot-check.service` runs `ground-station/boot_check.sh`, which verifies:

- `aeris-backend.service` is active
- `/health` answers on the configured port
- the dashboard is served
- the Wi-Fi hotspot is active and `192.168.4.1` is assigned
- the backend is reachable *through* the hotspot address
- **every unit is enabled**, so the next reboot will work as well
- the watchdog timer is running
- the frontend build is present
- the MLX90640 thermal camera is live
- the SX1278 LoRa receiver is listening

The result lands in `/var/log/aeris-boot-check.log`:

```
2026-11-12 20:32:12 | PASS  aeris-backend.service is active
2026-11-12 20:32:12 | PASS  backend /health answers on port 80
2026-11-12 20:32:12 | PASS  dashboard (frontend build) is served
2026-11-12 20:32:12 | PASS  Wi-Fi hotspot 'aeris-hotspot' is active
2026-11-12 20:32:12 | PASS  hotspot address 192.168.4.1 is assigned
2026-11-12 20:32:12 | PASS  backend reachable through the hotspot (192.168.4.1)
2026-11-12 20:32:12 | PASS  aeris-backend.service is enabled for boot
2026-11-12 20:32:12 | PASS  aeris-hotspot-guard.service is enabled for boot
2026-11-12 20:32:12 | PASS  aeris-boot-check.service is enabled for boot
2026-11-12 20:32:12 | PASS  aeris.target is enabled for boot
2026-11-12 20:32:12 | PASS  aeris-watchdog.timer is enabled for boot
2026-11-12 20:32:12 | PASS  aeris-watchdog.timer is running (self-healing check)
2026-11-12 20:32:12 | PASS  frontend build is present
2026-11-12 20:32:12 | PASS  MLX90640 thermal camera is live
2026-11-12 20:32:12 | PASS  SX1278 LoRa receiver is listening
2026-11-12 20:32:13 | === boot verification PASSED — AERIS came back automatically ===
```

### Manual

```bash
sudo ./aeris.sh                     # full install / repair / verify (one command)
sudo ./start_aeris.sh               # start everything (no rebuild)
sudo ./stop_aeris.sh                # stop services + hotspot (config preserved)
sudo systemctl status aeris-backend aeris-hotspot-guard aeris.target
sudo systemctl status aeris-watchdog.timer
sudo systemctl start aeris-watchdog.service    # run the self-healing check now
sudo systemctl restart aeris-boot-check.service # re-run the boot verification
```

---

## 5. Logs

| Log | Content |
|---|---|
| `sudo journalctl -u aeris-backend -f` | Backend stdout/stderr, the hardware pipelines, WebSocket traffic |
| `sudo journalctl -u aeris-hotspot-guard -f` | Hotspot activation attempts |
| `sudo journalctl -u aeris-watchdog.service -f` | Self-healing actions |
| `sudo journalctl -u aeris-boot-check.service -f` | Boot verification output |
| `/var/log/aeris-boot-check.log` | Persisted boot verification results (one block per boot) |
| `/var/log/aeris-watchdog.log` | Persisted self-healing history |
| `/var/log/aeris-setup.log` | Persisted deployment history |

**The journal is persistent.** Raspberry Pi OS ships `Storage=volatile`, which
keeps the journal in RAM — every log line is lost the moment the device
reboots, including the traceback that would explain why the last boot failed.
`/etc/systemd/journald.conf.d/aeris.conf` changes it to `Storage=persistent`
and caps the journal at **200 MB** with a **1 month** retention, keeping at
least 500 MB free on the card. To revert:

```bash
sudo rm /etc/systemd/journald.conf.d/aeris.conf
sudo systemctl restart systemd-journald
```

**Log rotation.** `/etc/logrotate.d/aeris` rotates `/var/log/aeris-*.log`
weekly (or at 5 MB), keeps 8 generations and compresses them. Without this
the files grow until the SD card is full.

---

## 6. Files

### Installed on the system

| Path | Purpose |
|---|---|
| `/etc/systemd/system/aeris-backend.service` | Backend unit |
| `/etc/systemd/system/aeris-hotspot-guard.service` | Hotspot guard unit |
| `/etc/systemd/system/aeris-boot-check.service` | Boot verification unit |
| `/etc/systemd/system/aeris.target` | Aggregate stack target |
| `/etc/systemd/system/aeris-watchdog.service` | Self-healing check |
| `/etc/systemd/system/aeris-watchdog.timer` | 5-minute timer |
| `/etc/systemd/system/aeris-onboard.service` | Onboard unit (drone Pi only) |
| `/etc/aeris/aeris.env` | Single source of truth for the port, paths and hotspot settings |
| `/etc/logrotate.d/aeris` | Log rotation |
| `/etc/systemd/journald.conf.d/aeris.conf` | Persistent, capped journal |
| `/var/lib/aeris/` | Runtime state (disable marker, watchdog counters) |

### In the repository (source of truth)

| Path | Purpose |
|---|---|
| `ground-station/aeris-backend.service` | Backend unit |
| `aeris-hotspot-guard.service` | Hotspot guard unit |
| `ground-station/aeris-boot-check.service` | Boot verification unit |
| `ground-station/aeris.target` | Aggregate stack target |
| `ground-station/aeris-watchdog.service` | Self-healing check |
| `ground-station/aeris-watchdog.timer` | 5-minute timer |
| `ground-station/hotspot_guard.sh` | Hotspot guard script |
| `ground-station/boot_check.sh` | Boot verification script |
| `ground-station/watchdog.sh` | Self-healing script |
| `ground-station/aeris-logrotate.conf` | Log rotation rule |
| `ground-station/aeris-journald.conf` | Journal drop-in |
| `drone-onboard/raspberry-pi-4b/aeris-onboard.service` | Onboard unit |
| `drone-onboard/raspberry-pi-4b/install_onboard_service.sh` | Onboard installer |

`./aeris.sh` copies the repository versions into `/etc/systemd/system`,
substituting the project path so the tree can live anywhere. It also installs
the log rotation rule and the journal drop-in, and enables every unit for
boot. Running it again is safe — every step is idempotent.

---

## 7. Configuration

Everything runtime-related lives in **`/etc/aeris/aeris.env`**:

```ini
AERIS_PORT=80
AERIS_PROJECT_DIR=/home/piaeris/aeris-project
AERIS_BACKEND_DIR=…/ground-station/backend
AERIS_FRONTEND_DIR=…/ground-station/frontend
AERIS_HOTSPOT_CON=aeris-hotspot
AERIS_HOTSPOT_SSID=AERIS-GroundStation
AERIS_HOTSPOT_IP=192.168.4.1
```

The units read it with `EnvironmentFile=-/etc/aeris/aeris.env`; a missing file
is not fatal (the `-` prefix), the units fall back to their built-in defaults.

Change the port there, then `sudo systemctl restart aeris-backend`. The health
checks, the watchdog, `start_aeris.sh` and the dashboard all follow.

### I2C bus speed (MLX90640 thermal camera)

The MLX90640 delivers a frame as two 1664-byte I2C bursts. On the Raspberry
Pi's default 100 kHz bus that takes ~152 ms per burst, so the thermal feed is
capped at 2 Hz. The how2electronics MLX90640 reference requires 400 kHz, which
is set in the Pi boot configuration:

```ini
# /boot/firmware/config.txt
dtparam=i2c_arm=on,i2c_arm_baudrate=400000
```

With the bus at 400 kHz the burst drops to **39 ms** and the backend
automatically upgrades the sensor to its highest sustainable refresh rate
(4 Hz). The backend measures the real burst time at startup and picks the
rate itself — it does not assume a fixed value.

> **Reading the speed correctly.** The `brcmstb-i2c ... @97500hz` lines in
> `dmesg` belong to the *legacy 40-pin header* buses
> (`i2c@7ef04500` / `i2c@7ef09500`), **not** to the bus the MLX90640 is on.
> The authoritative value is the device tree node behind `/dev/i2c-1`:
>
> ```bash
> od -An -tu4 --endian=big \
>   /sys/firmware/devicetree/base/soc/i2c@7e804000/clock-frequency
> # 400000
> ```
>
> `aeris.sh` reads that node, so it reports the speed correctly.

Verified on this device:

| | Before | After |
|---|---|---|
| Kernel I2C clock | 97 500 Hz | **400 000 Hz** |
| I2C burst time | 152 ms | **39 ms** |
| Sensor refresh rate | 2 Hz | **4 Hz** |
| Frames delivered to the dashboard | ~2 fps | **3.40 fps** (measured) |

Changing the boot configuration requires a reboot, and the boot verification
then confirms the new speed is in effect.

---

## 8. Troubleshooting

| Symptom | Where to look |
|---|---|
| Dashboard unreachable | `sudo systemctl status aeris-backend`, `sudo journalctl -u aeris-backend -n 50` |
| No Wi-Fi network | `sudo journalctl -u aeris-hotspot-guard -n 30`, `nmcli con show aeris-hotspot` |
| Backend up but the page is blank | `ls ground-station/frontend/dist/index.html` — rebuild with `cd ground-station/frontend && npm run build` |
| Something died after a reboot | `sudo journalctl --list-boots` then `sudo journalctl -b -1 -u aeris-backend` |
| Boot check failed | `sudo tail -40 /var/log/aeris-boot-check.log` |
| Self-healing is acting too often | `sudo tail -40 /var/log/aeris-watchdog.log` |
| Everything is broken | `sudo ./aeris.sh` — it repairs and re-verifies the whole system |

**Disabling the hotspot deliberately** (for example to get the Pi back on the
home Wi-Fi):

```bash
sudo nmcli con down aeris-hotspot
```

The guard would bring it straight back. Stop it first:

```bash
sudo ./stop_aeris.sh
```

`stop_aeris.sh` stops the guard and the watchdog, disables them for boot and
writes the marker `/var/lib/aeris/hotspot-disabled`, so the guard stays
hands-off until `start_aeris.sh` removes the marker again.
