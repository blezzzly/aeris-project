# AERIS - Off-Grid UAV System for Search and Rescue

A complete drone-based search and rescue system with **decentralized, dual-link communication** and **localized Edge-AI detection** for post-disaster scenarios where conventional infrastructure is compromised.

## Abstract

AERIS addresses the critical need for resilient communication and survivor detection in disaster-affected areas where public infrastructure is destroyed. By deploying an **off-grid dual-link architecture** — combining 5GHz Wi-Fi for high-bandwidth video streaming and 915/923MHz LoRa for reliable telemetry — the system maintains operational continuity during the "Golden Hour" when every second counts. **Edge-AI processing** on a Raspberry Pi 5 with **Hailo-8L NPU** reduces end-to-end latency from typical cloud-AI delays (150-200ms) to under 105ms, enabling rapid situational awareness and GPS coordinate dissemination to rescue personnel.

> **Deploy the Ground Station with one command:**
> ```bash
> cd ~/aeris-project && sudo ./aeris.sh
> ```
> It installs, configures, repairs, builds, starts and verifies the whole system
> (backend, frontend, systemd, Wi-Fi hotspot) and prints a deployment report.
> See [One-Command Deployment](#one-command-deployment-raspberry-pi-ground-station).

## Research Objectives

1. Design a **decentralized, air-gapped dual-link communication architecture** for reliable low-latency transmission of telemetry and video data.
2. Measure **end-to-end latency (ms)**, **Packet Loss Ratio (%)**, and **RSSI (dBm)** across varying distances (10–200m) and altitudes (10–50m).
3. Develop a functional hardware prototype using **Raspberry Pi 5**, **UART LoRa transceivers**, and optimized antenna configurations compliant with SWaP constraints.
4. Evaluate **detection accuracy, precision, recall, latency, and false positive rate** for survivor identification.
5. Measure **power consumption and operational endurance** of the integrated system.
6. Test effectiveness under **NLOS, obstructed environments, and environmental stress factors**.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AERIS OFF-GRID DUAL-LINK SYSTEM                      │
└─────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐      USB Camera         ┌──────────────────────────┐
   │   DRONE     │ ──────────────────────▶ │  RASPBERRY PI 5          │
   │  (UAV)      │    RGB + Thermal        │  + Hailo-8L NPU          │
   │             │                         │  (Edge-AI Processing)    │
   └─────────────┘                         └────────┬─────────────────┘
                                                      │
                                                      ▼
                                            ┌─────────────────┐
                                            │  YOLOv8n        │
                                            │  Person         │
                                            │  Detection       │
                                            └────────┬────────┘
                                                     │
                                                     ▼
                                            ┌─────────────────┐
                                            │  JSON Packet    │
                                            │  (Detection +   │
                                            │   Vital Signs)  │
                                            └────────┬────────┘
                                                     │
      ┌──────────────────────────────────────────────┼──────────────────────────────────────────────┐
      │                                              │                                              │
      ▼                                              ▼                                              ▼
┌─────────────────┐                        ┌─────────────────┐                          ┌─────────────────┐
│  5GHz Wi-Fi      │                        │  915/923MHz     │                          │   MAVLink       │
│  (Video Stream)  │                        │  LoRa           │                          │   Telemetry     │
│  ~5-6 Mbps       │                        │  (Backup Link)  │                          │                 │
│  <105ms latency  │                        │  <3% PLR        │                          │                 │
│  RSSI ≥ -85 dBm  │                        │  RSSI ≥ -80 dBm │                          │                 │
└────────┬─────────┘                        └────────┬────────┘                          └────────┬────────┘
         │                                            │                                             │
         └──────────────────────────┬────────────────┘                                             │
                                    │                                                            │
                                    ▼                                                            ▼
                          ┌─────────────────────────┐                              ┌──────────────────────┐
                          │  FASTAPI BACKEND        │                              │  Ground Control      │
                          │  WebSocket Server       │ ◀────────────────────────── │  Station (GCS)       │
                          │  Port: 8000             │                              │  Open-source GCS     │
                          └────────────┬────────────┘                              │  + SITL Simulation   │
                                       │                                            └──────────┬───────────┘
                                       ▼                                                       │
                          ┌─────────────────────────┐                                          │
                          │  REACT FRONTEND         │ ◀────────────────────────────────────────┘
                          │  (Vite + TypeScript)    │
                          │  Port: 5173             │
                          │                         │
                          │  - Live Feed Display    │
                          │  - Mission Map         │
                          │  - Detections Table    │
                          │  - UAV Telemetry       │
                          │  - Comm Link Health    │
                          │  - Settings/Config     │
                          └─────────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │  OPERATOR (MDRRMO)      │
                          │  Browser-based Console  │
                          └─────────────────────────┘
```

### Key Design Principles (from paper)

1. **Logical Network Segregation** — High-bandwidth 5GHz video is decoupled from low-bandwidth 915/923MHz LoRa telemetry to mitigate frequency interference.
2. **Edge-AI Processing** — YOLOv8 inference runs on-device (RPi 5 + Hailo-8L) to achieve <105ms latency versus 150-200ms cloud-based.
3. **Multi-Hop Resilient Routing** — Operates at 10 m/s flight speed over 8 km range with no single point of failure.
4. **H.264 Hardware Encoding** — 200 Mbps raw stream compressed to 5-6 Mbps over UDP.
5. **Age of Information (AoI) Optimization** — Decoupled critical navigation data from surveillance streams.

---

## Project Structure

```
aeris-project/
│
├── ground-station/              # Operator's Command Center (runs on laptop/PC)
│   ├── frontend/                # React + TypeScript Dashboard
│   │   ├── src/
│   │   │   ├── components/     # UI components (LiveFeed, MissionMap, etc.)
│   │   │   ├── pages/          # Page components (Dashboard, Map, Detections, Settings)
│   │   │   ├── stores/         # Zustand state management
│   │   │   ├── hooks/          # useWebSocket, useTelemetrySimulation
│   │   │   ├── types/          # TypeScript types
│   │   │   ├── App.tsx
│   │   │   ├── main.tsx
│   │   │   └── index.css
│   │   ├── public/             # Static assets (AERIS logo)
│   │   ├── package.json
│   │   ├── tailwind.config.js
│   │   ├── vite.config.ts
│   │   ├── vercel.json
│   │   └── README.md
│   │
│   └── backend/                 # FastAPI Backend (WebSocket server, port 8000)
│       ├── main.py
│       ├── requirements.txt
│       └── README.md
│
├── drone-onboard/               # Code that runs on Raspberry Pi (mounted on drone)
│   ├── raspberry-pi-4b/         # RPi 4B version (edge-AI)
│   └── raspberry-pi-5/          # RPi 5 version (with Hailo-8L NPU)
│
└── README.md                    # This file
```

---

## Performance Targets

| Parameter | Target | Source |
|-----------|--------|--------|
| Video latency (5GHz) | < 105 ms | Mahdi et al., 2025 |
| Packet Loss Ratio (LoRa) | < 3% | Arslanbenzer et al., 2023; Bordin et al., 2024 |
| RSSI (video) | ≥ -85 dBm | Zhu et al., 2021 |
| RSSI (telemetry) | ≥ -80 dBm | Zhao et al., 2024 |
| Operational range | 10-200 m | Research specification |
| Altitude envelope | 10-50 m | Research specification |
| Edge-AI latency | < 105 ms | Mahdi et al., 2025; Ntousis et al., 2025 |

---

## Tech Stack

### Frontend (Ground Control Station)
- **Vite** — Build tool
- **React 19** — UI framework
- **TypeScript** — Type safety
- **Zustand** — State management (with localStorage persistence)
- **Tailwind CSS** — Styling
- **Leaflet** — Interactive maps (OpenStreetMap + Esri Satellite)
- **Phosphor Icons** — UI icons
- **FastAPI WebSocket** — Real-time data from backend

### Backend (Ground Station)
- **FastAPI** — Web framework
- **WebSocket** — Real-time telemetry/detection broadcast
- **Uvicorn** — ASGI server

### Onboard (Drone)
- **Python 3.11+** — Main language
- **Raspberry Pi 5** — Companion computer
- **Hailo-8L NPU** — Edge-AI accelerator
- **YOLOv8** — Person detection
- **OpenCV** — Image processing
- **PyTorch** — Deep learning
- **5GHz Wi-Fi** — Video streaming (UDP)
- **915/923MHz LoRa** — Telemetry link
- **MAVLink** — Flight controller telemetry

---

## One-Command Deployment (Raspberry Pi Ground Station)

Everything — packages, Python environment, frontend production build, systemd
services, Wi-Fi hotspot and health verification — is handled by a single
idempotent script:

```bash
cd ~/aeris-project && sudo ./aeris.sh
```

The script is safe to run repeatedly. It repairs what it can, never overwrites
your configuration, and refuses to report success if a health check fails.

| What it does | Detail |
|---|---|
| **Preflight** | Verifies Raspberry Pi OS / Debian, required tools and the project layout |
| **Packages** | Installs missing system packages (`python3-venv`, `network-manager`, `i2c-tools`, …) |
| **Toolchain** | Checks Node.js (≥ 20.19), npm and Python; installs Node.js LTS if it is too old |
| **Backend deps** | Creates/updates the venv and installs `requirements.txt` |
| **Backend config** | Generates the operator token (mode 600), validates `main.py`, writes `/etc/aeris/aeris.env` |
| **Frontend** | `npm ci` → TypeScript check → Vite production build → verifies `dist/` |
| **systemd** | Installs `aeris-backend`, `aeris-hotspot-guard`, `aeris-boot-check`, `aeris.target` and the `aeris-watchdog` timer, enables all of them for boot |
| **Log persistence** | Installs log rotation for `/var/log/aeris-*.log` and a persistent, size-capped journal |
| **Health** | Validates `/health`; on failure it prints the diagnostics and the exact fix |
| **API** | Calls the authenticated endpoints and verifies that auth is enforced (401 without a token) |
| **Network** | Ensures NetworkManager, `aeris-hotspot`, `192.168.4.1` and hotspot reachability |
| **Report** | Prints a full deployment report |

Useful options:

```bash
sudo ./aeris.sh --skip-build    # keep the existing dist/ (no rebuild)
sudo ./aeris.sh --skip-deps     # do not reinstall npm/pip packages
sudo ./aeris.sh --skip-apt      # never touch apt
```

### After the deployment

| Resource | Address |
|---|---|
| Dashboard | <http://192.168.4.1/> (also <http://aeris.local/>) |
| Health endpoint | <http://192.168.4.1/health> |
| Backend (local) | <http://127.0.0.1:80/> |

The dashboard asks for an **access token** on first load. Read it with:

```bash
sudo cat ~/aeris-project/ground-station/backend/.aeris_auth_token
```

### Configuration

Runtime configuration lives in **`/etc/aeris/aeris.env`** — the single source of
truth used by the systemd units *and* by every script. It is created on the
first run and preserved afterwards:

```ini
AERIS_PORT=80                 # backend HTTP/WebSocket port
AERIS_PROJECT_DIR=/home/piaeris/aeris-project
AERIS_BACKEND_DIR=…/ground-station/backend
AERIS_FRONTEND_DIR=…/ground-station/frontend
AERIS_HOTSPOT_CON=aeris-hotspot
AERIS_HOTSPOT_SSID=AERIS-GroundStation
AERIS_HOTSPOT_IP=192.168.4.1
```

Change the port there, then `sudo systemctl restart aeris-backend`. The health
checks, `start_aeris.sh` and the dashboard all follow automatically.

### Day-to-day commands

```bash
sudo ./aeris.sh        # full install / repair / verify (the one command)
sudo ./start_aeris.sh  # start everything (no rebuild)
sudo ./stop_aeris.sh   # stop services + hotspot (configuration preserved)
sudo journalctl -u aeris-backend -f      # backend logs
sudo tail -f /var/log/aeris-setup.log    # deployment log
sudo tail -f /var/log/aeris-boot-check.log   # boot verification results
sudo tail -f /var/log/aeris-watchdog.log    # self-healing history
```

### Development mode

For local development the dashboard runs on Vite (`:5173`) and talks to a
standalone backend (`:8000`):

```bash
npm run dev          # frontend only  -> http://localhost:5173
npm run dev:full     # frontend + backend together
npm run setup        # one-time dependency install (venv + pip + npm)
```

The frontend resolves the backend automatically: the same origin in production
(the backend serves the built bundle) and `:8000` while the Vite dev server is
running — see `ground-station/frontend/src/lib/api.ts`.

> **Never run `npm install` or `pip install` with `sudo` inside the project.**
> That makes the installed files root-owned, and you then get
> `EACCES: permission denied` the next time you run `npm run dev` or
> `npm install` as yourself. `aeris.sh` installs the dependencies **as your
> user** and repairs any root-owned leftovers it finds, so prefer:
>
> ```bash
> sudo ./aeris.sh
> ```

### Boot behaviour

The ground station is a **fully automated appliance**: after a power cut or a
reboot every component comes back on its own, in the right order, and repairs
itself if something breaks. No manual command, no user interaction.

| Unit | Role |
|---|---|
| `aeris-hotspot-guard.service` | Keeps the `aeris-hotspot` access point alive |
| `aeris-backend.service` | API, WebSocket, dashboard and the hardware pipelines |
| `aeris.target` | Groups the services and fixes their order |
| `aeris-boot-check.service` | Verifies the whole stack after every boot |
| `aeris-watchdog.timer` | Self-healing check every 5 minutes |

Boot order: `network-online.target` → `aeris-hotspot-guard` →
`aeris-backend` → `aeris-boot-check` → `aeris-watchdog.timer`.

Recovery works in three layers — systemd restarts a process that dies, the
hotspot guard heals the Wi-Fi AP, and the watchdog repairs the failures
systemd cannot see (a wedged HTTP port, a hotspot that lost its address, a
unit that is no longer enabled for boot).

The boot verification writes its result to `/var/log/aeris-boot-check.log`,
so a reboot can be confirmed without anybody being in front of the device.

> **Full reference: [BOOT_AUTOSTART.md](./BOOT_AUTOSTART.md)** — every unit,
> its dependencies, the recovery logic, the logs and the troubleshooting table.

### Real hardware integration

The dashboard only ever displays values that a **real device** produced. There
is no mock API, no random number generator and no placeholder telemetry in the
ground station.

| Hardware | How it is read | Status |
|---|---|---|
| MLX90640 thermal camera (I2C `0x33`) | `adafruit-circuitpython-mlx90640` via `busio` — live min/max/mean/hotspot per frame | **Detected and streaming** |
| SX1278 LoRa radio (SPI + GPIO24 CS) | Identity + frequency read from the radio's **own registers** (`RegVersion 0x42` = `0x12`, `Frf 0x06-0x08`), reconfigured to 433 MHz LoRa on probe | **Detected and communicating (433 MHz, LoRa RXCONT listening)** |
| GPS / NMEA receiver (serial) | `pyserial` + NMEA `GGA`/`RMC` parser | **No serial device present** |
| Battery monitor INA219 / INA260 (I2C `0x40`) | Bus voltage, current, state of charge | **Not on the bus** |
| Barometer BMP280 / BMP388 (I2C `0x76/0x77`) | Bosch compensation, barometric altitude | **Not on the bus** |
| Magnetometer QMC5883L (I2C `0x0D`) | Magnetic heading | **Not on the bus** |

**Data pipeline**

```
sensors.py  ──poll 2s──▶  telemetry_data  ──▶  WebSocket "system" broadcast
                                                     │
                          ┌──────────────────────────┼───────────────────┐
                          ▼                          ▼                   ▼
                   HardwareStatus            CommLinkHealth        UAV Status / Map
```

* `ground-station/backend/sensors.py` — one reader per device. Each returns
  `available: true` **only** when real hardware answered, plus a `reason` when
  it did not.
* The backend merges the readings into `telemetry_data` and publishes them
  every 2 seconds over the existing WebSocket (`type: "system"`), together
  with the sensor availability report.
* `GET /api/sensors` returns the same data for REST clients.

**What the dashboard shows when hardware is missing**

`Disconnected`, `No Data`, `N/A`, `No Link` or `NO LIVE GPS POSITION` — never a
made-up number. The UAV marker on the map is dimmed and greyed out until a real
GPS fix arrives.

### Hardware status reporting (how ONLINE / CONNECTED is decided)

The dashboard shows a component as **ONLINE / CONNECTED** only when the ground
station can prove it right now. Three things have to hold at the same time:

1. **Real communication** — the device actually answered (MLX90640 delivers
   frames over I2C, the SX1278 answers on the SPI bus, the I2C/SPI buses
   themselves answer a scan).
2. **A fresh measurement** — the answer must be recent. The backend runs a
   watchdog on the thermal sensor (a frame older than 5 s means the sensor
   stopped answering) and the dashboard ignores any report older than 15 s.
3. **A live link** — the report must have arrived over the current WebSocket
   connection. A report that stopped arriving (Wi-Fi drop, backend restart)
   turns the indicators red instead of leaving a stale green `CONNECTED`.

`GET /api/hardware/health` (also part of every `type: "system"` broadcast)
returns the verdict per device:

```json
{
  "devices": {
    "mlx90640":   { "state": "online",  "connected": true,  "reason": null, "last_seen": "2026-11-12T06:25:37.687798" },
    "lora_sx1278":{ "state": "online",  "connected": true,  "reason": null, "last_seen": "2026-11-12T06:25:29.461730" },
    "sensor_gps": { "state": "offline", "connected": false, "reason": "no serial device found for a GPS receiver" }
  }
}
```

`state` is one of `online`, `stale`, `offline` — never an assumption.

### The live channel (WebSocket) and the sign-in flow

The live channel is authenticated with the operator token. The handshake
distinguishes two failures so the client can react correctly:

| Close code | Meaning | Client reaction |
|---|---|---|
| `auth_ok` frame | handshake succeeded | channel is live |
| `4401 auth_required` | no credential was presented yet (operator has not signed in) | wait for the sign-in, keep the stored token |
| `1008 unauthorized` | the presented credential was rejected | drop the stored token, show the sign-in screen |
| `1006` / other | the link dropped (Wi-Fi interruption, backend restart) | reconnect automatically with a growing delay (1 → 8 s) |

The socket is (re)opened as soon as a token appears in `localStorage`, so the
dashboard becomes live immediately after the operator presses **Connect** —
it does not need a page reload.

### Wiring the missing hardware

| Module | Connection | Notes |
|---|---|---|
| SX1278 (433 MHz) ground radio | SPI: `SCLK→GPIO11`, `MOSI→GPIO10`, `MISO→GPIO9`; **`NSS→GPIO24`**, `RESET→GPIO23`, `DIO0→GPIO18`; 3.3 V only | Backend drives NSS via userspace chip-select on GPIO24 (the kernel CE lines are not used). `sensors.probe_sx1278()` reports `0x12` on RegVersion with a stable read, Frf `0x6C4000` = exactly 433.000 MHz, PA_BOOST selected, LoRa RX CONTINUOUS. DIO0/GPIO18 fires `RxDone` interrupts |
| GPS (NMEA) | USB-serial (`/dev/ttyUSB0`) or `GPIO14/15` UART | The Pi currently exposes **no** `/dev/ttyAMA0`, `/dev/ttyS0` or `/dev/ttyUSB*` |
| INA219 / INA260 | I2C `0x40` | `sudo i2cdetect -y 1` must list `40` |
| BMP280 / BMP388 | I2C `0x76` (or `0x77`) | `sudo i2cdetect -y 1` must list `76`/`77` |
| QMC5883L | I2C `0x0D` | `sudo i2cdetect -y 1` must list `0d` |

Check the current inventory at any time:

```bash
sudo /home/piaeris/aeris-project/ground-station/backend/venv/bin/python -c "
import sys; sys.path.insert(0, '/home/piaeris/aeris-project/ground-station/backend')
import sensors, json; print(json.dumps(sensors.read_all(), indent=2))"
```

or from the dashboard: the **Comm Links** card and **Real Hardware Status**
panel show the live detection result.

---

## Quick Start

### Prerequisites

| Software | Version | Download |
|----------|---------|----------|
| Node.js | 20+ | https://nodejs.org/ |
| Python | 3.11+ | https://python.org/ |
| Git | Latest | https://git-scm.com/ |
| Chrome/Chromium | Latest | https://www.google.com/chrome/ |

### 1. Clone the Repository

```bash
git clone https://github.com/amblessly/aeris-project.git
cd aeris-project
```

### 2. Setup Ground Control Station (Laptop/PC)

#### Install All Dependencies (one-time setup)

From the **project root**:

```bash
npm run setup
```

This runs:
- Backend: creates venv + installs Python requirements (`ground-station/backend`)
- Frontend: installs npm dependencies (`ground-station/frontend`)

#### Run the Dashboard

From the **project root**:

```bash
# One-time setup: backend venv + pip install + frontend npm install
npm run setup

# Run full stack (frontend + backend together)
npm run dev
```

This runs both frontend (Vite, port 5173) + backend (uvicorn, port 8000) together.

Or run them manually in two terminals:

```bash
# Terminal 1 — Start backend (WebSocket server, port 8000)
cd ground-station/backend
source venv/bin/activate
python main.py

# Terminal 2 — Start frontend (React dashboard, port 5173)
cd ground-station/frontend
npm run dev:frontend

# Access dashboard
# Open: http://localhost:5173
```

**Note:** For Raspberry Pi 5 local setup, make sure Node.js 20+ and Python 3.11+ (with venv) are installed before running the steps above.

The frontend will automatically connect to `ws://localhost:8000/ws`. If the backend is not running, the dashboard will fall back to simulation mode for testing.

### 3. Setup Raspberry Pi 5 (Drone Onboard)

#### Flash Raspberry Pi OS

1. Download Raspberry Pi Imager: https://www.raspberrypi.com/software/
2. Flash **Raspberry Pi OS (64-bit)** to SD card
3. Configure Wi-Fi and SSH during flashing

#### Install Detection Software

```bash
# SSH into Pi
ssh pi@raspberrypi.local

# Update system
sudo apt update && sudo apt upgrade -y

# Install screen (for background processes)
sudo apt install -y screen

# Clone project
git clone https://github.com/amblessly/aeris-project.git
cd aeris-project/drone-onboard/raspberry-pi-5

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies (includes YOLOv8, OpenCV, MAVLink, etc.)
pip install -r requirements.txt
```

#### Run Detection Pipeline

```bash
# Start in background using screen
screen -S aeris
source venv/bin/activate
python main.py

# Detach: Press Ctrl+A, then D
# Reconnect later: screen -r aeris
```

The onboard pipeline runs:
- USB Camera capture (RGB + Thermal if attached)
- YOLOv8 person detection
- Vital signs estimation
- JSON packet creation
- 5GHz Wi-Fi video transmission
- 915MHz LoRa telemetry link

---

## Network Configuration

### Default Setup

```
Ground Station (Laptop)          Raspberry Pi 5 (Drone)
IP: 192.168.1.50                 IP: 192.168.1.100
       │                                 │
       │  ┌──────────────────────────┐  │
       └──┤  Same Wi-Fi Network      ├──┘
          └──────────────────────────┘
```

### WebSocket Configuration

**For local development** (default):
- Frontend → `ws://localhost:8000/ws`
- Backend → listens on `0.0.0.0:8000`

**For network deployment:**

Edit `ground-station/frontend/src/App.tsx`:

```typescript
// Change to your GCS IP address
useWebSocket('ws://192.168.1.50:8000/ws');
```

### Test Distances & Altitudes

Per paper: test at distances of **10, 50, 100, 150, 200 meters** and altitudes of **10, 30, 50 meters**.

---

## Features

### Dashboard
- ✅ **Real-time video feed** from drone camera
- ✅ **Interactive map** with UAV and survivor positions (Streets/Satellite views)
- ✅ **Live person detection** using YOLOv8
- ✅ **Detection alerts** with confidence scores, GPS, and thermal data
- ✅ **UAV telemetry** (altitude, speed, battery, heading, GPS)
- ✅ **Communication link health** (5GHz Wi-Fi + 915MHz LoRa)
- ✅ **Mission management** (start/end tracking, mission time)
- ✅ **Environment monitoring** (temperature, weather)
- ✅ **Notification system** with real-time alerts
- ✅ **Settings page** (comm, mission, alerts, system)

### Detection Modal
- RGB + Thermal image visualization
- GPS coordinates with ±3m accuracy
- Altitude, distance, and timestamp
- Heat range analysal data
- ✅ **UAV telemetry** (altitude, speed, battery, heading, GPS)
- ✅ **Communication link health** (5GHz Wi-Fi + 915MHz LoRa)
- ✅ **Mission management** (start/end tracking, mission time)
- ✅ **Environment monitoring** (temperature, weather)
- ✅ **Notification system** with real-time alerts
- ✅ **Settings page** (comm, mission, alerts, system)

### Detection Modal
- RGB + Thermal image visualization
- GPS coordinates with ±3m accuracy
- Altitude, distance, and timestamp
- Heat range analysis
- AI confidence score
- One-click acknowledgment

### Real-Time Data Flow
1. RPi 5 captures frames from USB camera
2. YOLOv8 detects humans (Edge-AI, <105ms)
3. Detection packets sent via dual-link (5GHz video + 915MHz LoRa telemetry)
4. Backend broadcasts via WebSocket
5. Frontend updates Zustand store
6. Dashboard re-renders with new data

---

## Component Documentation

| Component | README |
|-----------|--------|
| Frontend (Dashboard) | [`ground-station/frontend/README.md`](./ground-station/frontend/README.md) |
| Backend (WebSocket) | [`ground-station/backend/README.md`](./ground-station/backend/README.md) |
| RPi 4B (Detection) | [`drone-onboard/raspberry-pi-4b/README.md`](./drone-onboard/raspberry-pi-4b/README.md) |
| RPi 5 (Detection + Hailo) | [`drone-onboard/raspberry-pi-5/README.md`](./drone-onboard/raspberry-pi-5/README.md) |

---

## Testing Methodology

Per research paper, the system is evaluated at:
- **Distances**: 10, 50, 100, 150, 200 meters (Line-of-Sight)
- **Altitudes**: 10, 30, 50 meters
- **Conditions**: NLOS, obstructed (debris, vegetation), environmental stress

Measured parameters:
- End-to-end transmission latency (ms)
- Received Signal Strength Indicator (RSSI in dBm)
- Packet Loss Ratio (%)
- Detection accuracy, precision, recall
- False positive rate
- Power consumption and operational endurance

---

## Troubleshooting

### Frontend won't start
```bash
cd ground-station/frontend
rm -rf node_modules package-lock.json
npm install
```

### Backend connection refused
```bash
# Check if port 8000 is in use
netstat -ano | findstr :8000

# Kill process if needed
taskkill /PID <PID> /F
```

### RPi can't connect to GCS
```bash
# Test network connection
ping 192.168.1.50

# Check WebSocket
python -c "import websocket; ws = websocket.create_connection('ws://192.168.1.50:8000/ws'); print('OK')"
```

### Camera not detected on RPi
```bash
lsusb
ls /dev/video*
python test_cam.py
```

### The dashboard shows everything as OFFLINE / DISCONNECTED

The indicators follow the **live channel**, so this normally means the laptop is
not receiving the WebSocket broadcasts. Check in this order:

```bash
# 1. Is the ground station reachable over the hotspot?
curl -s http://192.168.4.1/health

# 2. Is the backend serving the dashboard and the API?
curl -s -H "Authorization: Bearer $(sudo cat /home/piaeris/aeris-project/ground-station/backend/.aeris_auth_token)" \
     http://192.168.4.1/api/hardware/health

# 3. Does the hardware answer at all? (real bus scan)
sudo i2cdetect -y 1        # the MLX90640 must show up as 33

# 4. Backend log
sudo journalctl -u aeris-backend -f
```

The usual cause is the **access token**: the live channel needs the operator
token that is stored in the browser (`localStorage.aeris_token`). Open
`http://192.168.4.1`, enter the token from
`sudo cat ground-station/backend/.aeris_auth_token` and press **Connect** — the
channel opens by itself, no reload needed. A wrong token is rejected
(`1008`) and the sign-in screen comes back; a lost link reconnects on its own.

### A device really is offline

`GET /api/hardware/health` says exactly why, per device:

```bash
curl -s -H "Authorization: Bearer $(sudo cat ground-station/backend/.aeris_auth_token)" \
     http://192.168.4.1/api/hardware/health | python3 -m json.tool
```

`state: offline` with a `reason` means the device did not answer (not wired,
not powered, or a bus error). `state: stale` means it answered but not
recently enough. `state: online` means it is genuinely communicating right now.

### Hailo-8L not detected
```bash
# Check NPU
hailortcli scan

# Verify driver
sudo dmesg | grep hailo
```

---

## References (per research paper)

- Saraereh et al. (2020) — UAV-enabled LoRa networks for disaster management
- Abro et al. (2025) — Pixhawk + Raspberry Pi companion computer architecture
- Aizat et al. (2023) — Directional antenna tracker (Haversine-based)
- Mahdi et al. (2025) — Video latency benchmarks (105ms)
- Bordin et al. (2024) — Ground reflection effects (up to 830ms delay)
- Arslanbenzer et al. (2023) — LoRa PLR benchmarks (3%)
- Zhu et al. (2021), Zhao et al. (2024) — RSSI standards
- Lyu et al. (2023) — UAV SAR operational challenges

---

## License

MIT License — Educational prototype project.

---

## Repository

🔗 https://github.com/amblessly/aeris-project

For issues, check individual component READMEs for detailed troubleshooting.
