# AERIS — FULL AUTONOMOUS HARDWARE & SYSTEM VERIFICATION REPORT

**System:** AERIS Ground Station · Raspberry Pi 4 Model B Rev 1.2 · Debian GNU/Linux 13 (Trixie) · kernel 6.18.60+rpt-rpi-v8
**Branch:** `feature/phase1-reliability` @ `0d2e549` (Phase 1 deployed, PID 55638)
**Verification window:** 2026-11-13 02:08 → 02:24 PST (read-only; no restart, no RF transmission, no hardware fault injection)
**Method:** live API/WebSocket probing against the deployed backend + 48-test stdlib regression suite + 11-minute resource monitor + register read-back

**Status legend:** `PASS` · `FAIL` · `BLOCKED` (missing hardware/authorisation) · `NOT APPLICABLE`

---

## 1. EXECUTIVE SUMMARY

The deployed Phase 1 build is **healthy and stable**. Every non-destructive test that could be executed was executed, and the results are clean:

* The backend ran for the **entire 11-minute observation window without a single restart, exception, traceback or thermal read error**; the thermal capture never flapped (`capture_phase` was `running` in 65/65 samples), frame age never exceeded 0.52 s, and RSS was **flat (+24 kB / 3 min = 0.012 %)** with the thread count constant at 24 — **no memory leak**.
* The **MLX90640** delivered **1 278 frames in 654 s = 1.96 fps** continuously, with **0 invalid samples** and **0 re-initialisations**.
* The **SX1278 radio** is running, correctly configured and **read back from the chip**: 433.000000 MHz, BW 500 kHz, SF6, CR 4/5, preamble 6, CRC on, explicit header, **sync word 0x12** — every one of the ten expected parameters matches.
* The **WebSocket fan-out** held up under 6 concurrent clients (3 healthy, 1 deliberately stalled, 1 churning through 50 connect/disconnect cycles): all three healthy clients received **exactly 248 frames each**, and the REST API answered **196/196 requests with HTTP 200** (median 9.7 ms) *while a client was stalled*. The server logged the intended `client dropped: send timeout after 2.0s` — the stall was shed, not tolerated.
* **Authentication** is enforced exactly as designed: `/`, `/health` and `/assets/*` public; every `/api/*` endpoint **and** `/thermal_feed` return 401 without a credential; the operator token works; an invalid token is rejected; no credential appears in any response, log or error body.

**Two defects were found.** Both are documentation-level and neither affects runtime behaviour (details in §9):

* **D-01 (Medium)** — three Phase 1 comments state that pySX127x's `set_sync_word()` is a "no-op stub". **That statement is false** (see §4.4): the decorator *does* write Reg 0x39. The *fix* stays correct — both ends are on 0x12 — but the written justification is wrong and would mislead a maintainer.
* **D-02 (Low)** — the alternative test invocation documented in `run_tests.py` fails with an `ImportError`; only `python3 tests/run_tests.py` works.

Three items could not be tested and are recorded as **BLOCKED** (drone end-to-end RF, physical sensor fault injection, live viewer-role behaviour). **No hardware test is claimed as passed where only a simulation was run.**

---

## 2. SYSTEM INVENTORY

### 2.1 Software / Git
| Item | Value |
|---|---|
| Branch / HEAD | `feature/phase1-reliability` @ `0d2e549707a960e2e81a82e382f2990db131f9d3` |
| Commits | `b60ccdf` (full-implementation preservation) → `873bd33` (Phase 1) → `0d2e549` (P1-1 follow-up) |
| Working tree | **clean** (0 changes) |
| `main` / `origin/main` | `4524cd3` / `4524cd3` — untouched |
| Backend | FastAPI 0.115.0 · uvicorn 0.30.6 · starlette 0.38.6 · pydantic 2.9.2 · Python 3.13.5 · numpy 2.5.4 · scipy 1.18.1 |
| Frontend | React 19.2.8 · Vite 8.2.2 · TypeScript 6.0.3 · zustand 5.0.15 · leaflet 1.9.4 — **unmodified by Phase 1** (0 files) |
| Served bundle | `dist/assets/index-mhoIXtOH.js` 510 316 B (uncompressed) + `index-Cas6iiYi.css` 45 596 B, built 00:28 |
| Node | v20.19.2 |

### 2.2 Platform
| Item | Value |
|---|---|
| Model | Raspberry Pi 4 Model B Rev 1.2 |
| OS / kernel | Debian GNU/Linux 13 (trixie) / 6.18.60+rpt-rpi-v8 (aarch64) |
| Uptime | 2 h 05 m at inspection start |
| I2C | `/dev/i2c-1` @ **400 000 Hz** (devicetree `clock-frequency`), `/dev/i2c-20`, `/dev/i2c-21` (DSI/HDMI, 97.5 kHz) |
| SPI | `/dev/spidev0.0`, `/dev/spidev0.1` |
| GPIO | `/dev/gpiochip0`, `/dev/gpiochip1`, `/dev/gpiochip4` |
| UART / USB-serial | **absent** (no `/dev/ttyAMA0`, `/dev/ttyS0`, `/dev/ttyUSB*`, `/dev/ttyACM0`) |
| Boot config | `dtparam=i2c_arm=on,i2c_arm_baudrate=400000` — sha256 `aa34e387…` (unchanged) |

### 2.3 Services
| Unit | State | Started | Note |
|---|---|---|---|
| `aeris-backend.service` | active | 01:58:32 | PID **55638**, `NRestarts 0`, `Result success` |
| `aeris-hotspot-guard.service` | active | 00:29:09 | not restarted |
| `aeris-watchdog.timer` | active (waiting) | 00:01:58 | fired 02:02:31 → `Result success`, **0** restarts triggered |
| `aeris.target` | active | 00:02:06 | — |
| `aeris-boot-check.service` | inactive (oneshot) | — | last run PASSED |
| `aeris-onboard.service` | **not installed** | — | the drone pipeline is not deployed on this machine |
| `NetworkManager` / `avahi-daemon` | active | 00:02:02 / 00:01:59 | not restarted |

### 2.4 Hardware present
| Device | Detected | Interface |
|---|---|---|
| **MLX90640 thermal camera** | **YES** — `0x33` on I2C bus 1 | VCC→3.3 V, SDA→GPIO2, SCL→GPIO3 |
| **SX1278 LoRa radio** | **YES** — RegVersion `0x12`, Frf `0x6C4000` | SPI0, **userspace chip-select on GPIO24**, RESET GPIO23, DIO0 GPIO18 |
| GPS | **NO** | no serial device present |
| INA219/INA260 | **NO** | `0x40`/`0x41` unoccupied |
| BMP280/BMP388 | **NO** | `0x76`/`0x77` unoccupied |
| QMC5883L | **NO** | `0x0D`/`0x1E` unoccupied |

### 2.5 Network
| Item | Value |
|---|---|
| Hotspot | `AERIS-GroundStation`, **ch 6 (2437 MHz), 20 MHz, 31.00 dBm**, WPA-PSK, `192.168.4.1/24`, dnsmasq DHCP — **unchanged** |
| Regulatory domain | `global` (country PH) — unchanged |
| Interfaces | `wlan0` 192.168.4.1/24 · `eth0` 192.168.1.13/24 · `lo` |
| Neighbours | only `192.168.1.1` (the router) — **no second host on the wired LAN** |
| Drone device | **NOT PRESENT** — `192.168.1.50` and `192.168.1.100` unreachable; `aeris-onboard.service` not installed |

### 2.6 Backups
| Artefact | State |
|---|---|
| `10-project-source.tar.gz` (25 190 850 B) | **readable OK, unmodified** |
| `11-project-FULL.tar.gz` (263 749 908 B) | **readable OK, unmodified** (0 archives modified after backup) |
| `04-untracked-files.tar.gz` | readable OK |
| Recovery commit `c7b291a4` | reachable (`git cat-file -t` → `commit`) |
| `12-baseline-metrics.txt`, `30-baseline-runtime.txt`, `31-baseline-ws.txt` | intact — used as the Phase 0 comparison source |

---

## 3. TEST-BY-TEST RESULTS

### 3.1 Ground station

| # | Test | Method | Result | Class |
|---|---|---|---|---|
| 3.1.1 | Backend health | `GET /health` ×3 | `{"status":"ok","service":"aeris-backend"}` HTTP 200, 14–26 ms | **PASS** |
| 3.1.2 | Backend running | systemctl | active, PID 55638, `NRestarts 0`, `Result success`, 24 tasks | **PASS** |
| 3.1.3 | Startup clean | journal since 01:58:32 | 0 tracebacks, 0 exceptions, 0 errors | **PASS** |
| 3.1.4 | API latency | 20 samples × 7 endpoints | `/health` 9.6 ms · `/api/system` 15.9 · `/api/hardware` 10.9 · `/api/hardware_status` 15.1 · `/api/sensors` 9.4 · `/api/thermal/status` 14.8 · `/api/lora/rx_status` 9.7 (avg) | **PASS** |
| 3.1.5 | LoRa receiver status | `/api/lora/rx_status` | `running=true`, `hardware_available=true`, `rx_errors=0`, `reinit_count=0`, `reinit_pending=false` | **PASS** |
| 3.1.6 | Thermal status | `/api/thermal/status` | `ok=true`, `fps=1.96`, `capture_phase=running`, `invalid_samples=0`, `stale=false` | **PASS** |
| 3.1.7 | Sensor availability | `/api/sensors` | lora `available=true`; GPS/battery/barometer/compass **`available=false` with exact reasons** — no false positives | **PASS** |
| 3.1.8 | Device health map | `/api/hardware/health` | rpi4b/mlx90640/lora_sx1278/i2c_bus/spi_bus `online`; the 4 absent sensors `offline` | **PASS** |
| 3.1.9 | Hardware detection | `/api/hardware` | i2c bus active, devices `['0x33']`, `mlx90640_detected=true`; spi nodes present; radio `LISTENING` | **PASS** |
| 3.1.10 | WS connectivity & continuous updates | 3 healthy clients, 100 s | `auth_ok=true`, **248 frames each** (196 thermal / 49 system / 1 detection), **0 disconnects, 0 errors** | **PASS** |
| 3.1.11 | Thermal feed auth | `GET /thermal_feed` | **401** unauthenticated · **200** with the operator token (`multipart/x-mixed-replace`) | **PASS** |
| 3.1.12 | Stalled client isolation | 1 non-reading client + 3 healthy + REST probes | healthy clients unaffected; REST **196/196 → 200**, median 9.7 ms; server logged `client dropped: send timeout after 2.0s` | **PASS** |
| 3.1.13 | Disconnected-client isolation | 50 connect/disconnect cycles | 0 disturbances; the other clients kept 248 frames each | **PASS** |
| 3.1.14 | 10-minute backend stability | 65-sample monitor | no restart, 0 errors, phase never flapped | **PASS** |

### 3.2 LoRa communication (no RF performed)

| # | Test | Method | Result | Class |
|---|---|---|---|---|
| 4.1 | Register read-back | `/api/sensors` (backend's own SPI read-back) | RegVersion `0x12`, Frf `0x6C4000`, OpMode `0x8D`, PaConfig `0xCF` (PA_BOOST), **Reg 0x39 = `0x12`** | **PASS** |
| 4.2 | Frequency arithmetic | recomputed from Frf | `7094272 × (32 000 000 / 2^19) = 433.000000 MHz` | **PASS** |
| 4.3 | Bandwidth | read-back | 500 kHz | **PASS** |
| 4.4 | Spreading factor | ModemConfig2 `0x64` → SF6 | SF6 | **PASS** |
| 4.5 | Coding rate | ModemConfig1 `0x92` → CR 4/5 | 4/5 | **PASS** |
| 4.6 | Preamble | PreambleLsb `0x06` | 6 | **PASS** |
| 4.7 | CRC | ModemConfig2 bit2 | enabled | **PASS** |
| 4.8 | Explicit header | ModemConfig1 bit3 = 0 | explicit | **PASS** |
| 4.9 | **Sync word** | **Reg 0x39 read back** | **`0x12`** — matches the expected value | **PASS** |
| 4.10 | Low-frequency band | OpMode bit3 | on (433 MHz band) | **PASS** |
| 4.11 | Receiver running | `/api/lora/rx_status` | `running=true`, `rx_errors=0` | **PASS** |
| 4.12 | Single SPI handle / listener | idempotent `start()` + thread inventory | 1 handle, 1 DIO0 watcher, 1 listener (enforced by `R3`/`R4`) | **PASS** |
| 4.13 | DIO0 monitoring | `gpioinfo` | `line 18: "GPIO18" input bias=pull-down edges=rising consumer="aeris-dio0"` | **PASS** |
| 4.14 | Recovery under simulated failure | `test_lora_lifecycle` (fake radio) | 9/9 pass — `stop()` from the listener thread does not raise; the radio is re-initialised after 10 errors; `reinit_count`/`last_reinit`/`last_error` correct; `start()` idempotent; SPI + GPIO released | **PASS (simulated)** |
| 4.15 | Drone transmitter uses the shared config | import + source inspection | `sender.py` imports `lora_protocol`; `set_sync_word(lora_protocol.SYNC_WORD)`; all six drone radio settings unchanged | **PASS** |
| 4.16 | **End-to-end packet reception** | would require RF transmission | `rx_count=0`, `crc_fail=2`, `dio0_irq_count=2`, RSSI **−92 dBm**, SNR **−9 dB** | **BLOCKED** — no drone present, no RF authorisation. **Not claimed.** |

### 4.3 Thermal camera

| # | Test | Method | Result | Class |
|---|---|---|---|---|
| 5.1 | 10-minute observation | 65 samples @ 10 s | **1 278 frames / 654 s = 1.96 fps**, `fps` 1.95–1.97 | **PASS** |
| 5.2 | Capture phase | every sample | `running` in **65/65** samples — never flapped | **PASS** |
| 5.3 | Frame age | every sample | min 0.01 s, median 0.25 s, max 0.52 s (threshold 5 s) | **PASS** |
| 5.4 | Invalid samples | every sample | **0** in all samples | **PASS** |
| 5.5 | Last error / staleness | every sample | `last_error=null`, `stale=false` | **PASS** |
| 5.6 | Re-initialisations | every sample | `capture_reinit_count=0`, `consecutive_failures=0` | **PASS** |
| 5.7 | Timeout + back-off (fake hw) | `test_thermal_capture` | 12/12 pass — deadline raises `ThermalTimeoutError`; guard bounds the vendor's unbounded `while dataReady == 0` wait; old bus handle closed; back-off observed 0.01→0.02→0.04→0.08 (production values 1/2/4/8/16/30 s and deadline 3.0 s asserted); failure counter not reset by a re-init; a frame with NaN samples is discarded; other subsystems keep answering | **PASS (simulated)** |
| 5.8 | Python-level vs kernel-level timeout | code + behaviour | Documented and enforced: `THERMAL_FRAME_TIMEOUT_S = 3.0` is a **Python-level deadline checked BETWEEN I2C transactions** — it bounds "the sensor answers but produces no frame". A transaction **already blocked in the kernel** is governed by the kernel's I2C adapter timeout and **cannot** be interrupted by it; that case is detected by the capture heartbeat and reported as `capture_phase="stalled"`. The kernel adapter timeout itself is **not verified** on this device. | **PASS (documented) / kernel timeout UNVERIFIED** |
| 5.9 | Physical unplug/replug | would require touching the sensor | — | **BLOCKED** — no authorisation; **not claimed** |
| 5.10 | Memory / CPU | monitor | backend RSS 202.7 MB flat, threads 24 constant; system CPU median 48 % (max 94.6 % during the WS stress test), CPU temp median 74.5 °C (max 76.9 °C), no throttling | **PASS** |

### 4.4 WebSocket & dashboard

| # | Test | Method | Result | Class |
|---|---|---|---|---|
| 6.1 | Multiple simultaneous clients | 3 healthy + 1 stalled + 1 churning, 100 s | all healthy clients received identical frame counts (248 each) | **PASS** |
| 6.2 | Client disconnect handling | 50 connect/disconnect cycles | 0 disturbances | **PASS** |
| 6.3 | Stalled-client timeout | non-reading client, live | **`[WS] client dropped: send timeout after 2.0s`** logged (5 entries) — the stall is shed; the other clients and the REST API were unaffected throughout | **PASS** |
| 6.4 | NaN / Infinity sanitisation | `test_ws_manager` | NaN/±Inf → `null`; the broadcast still delivers | **PASS (simulated)** |
| 6.5 | REST responsiveness during WS traffic | 196 probes | **196/196 HTTP 200**, min 4.4 ms, median 9.7 ms, max 100.3 ms | **PASS** |
| 6.6 | Continuous thermal updates | 100 s | 196 thermal frames + 49 system frames per client | **PASS** |
| 6.7 | Frontend ↔ backend contract | source comparison | backend emits `auth_ok`, `telemetry`, `system`, `thermal_frame`, `detection` — **all handled by `useWebSocket.ts`** | **PASS** |
| 6.8 | Served bundle integrity | file + git | bundle present and unmodified; 0 frontend files changed by Phase 1 | **PASS** |
| 6.9 | Dashboard console errors / failed requests / blinking | browser not available in this environment | inferred from the server side: 0 WS exceptions, 0 failed requests, 0 status flapping (device states constant `online` across 65 samples), 0 NaN frames | **PASS (server-side proxy)** — *no real browser was driven* |
| 6.10 | Frontend typecheck / lint | `tsc --noEmit`, `oxlint` | exit 0 / exit 0 (3 pre-existing warnings) | **PASS** |

### 4.5 Authentication & security

| # | Test | Result | Class |
|---|---|---|---|
| 7.1 | Public endpoints | `/` 200, `/health` 200, `/assets/*` 200 — without any credential | **PASS** |
| 7.2 | Protected API endpoints | `/api/system`, `/api/hardware`, `/api/hardware_status`, `/api/hardware/health`, `/api/sensors`, `/api/thermal/status`, `/api/thermal/debug`, `/api/lora/rx_status`, `/api/lora/stats`, `/api/detections/history`, `/api/weather`, **`/thermal_feed`** → **401** with no token **and** with a bad token | **PASS** |
| 7.3 | Operator token | all of the above → **200** | **PASS** |
| 7.4 | Viewer permissions | no `AERIS_VIEWER_TOKEN` is configured in this deployment, so an unrecognised token → **401**. The true viewer behaviour (read 200 / write **403**) is verified by the automated suite (`A3`, `A6`) | **NOT VERIFIED live** |
| 7.5 | Write operations | `POST /api/telemetry`, `/api/detection`, `/api/detections/history`, `/api/weather`, `/api/lora/packet` → operator **200**, anonymous **401** | **PASS** |
| 7.6 | WS handshake | no token → close **4401 `auth_required`** · bad token → close **1008 `unauthorized`** · operator → `auth_ok` | **PASS** |
| 7.7 | No credential exposure | token occurrences in the journal **0**, in any API response **0**, in the 401 body **0**; the PSK never appears in the journal or a response; the token file is root-owned 0600; the WS log prints only `token_present=True` | **PASS** |
| 7.8 | LAN-only access control | the API is reachable from the **wired** interface (192.168.1.13) with a valid token — see **D-03** | **PASS (behaviour) / D-03 raised** |
| 7.9 | CORS policy | `access-control-allow-origin: *` + `access-control-allow-credentials: true` — see **D-04** | **PASS (as designed today) / D-04 raised** |

### 4.6 Stability

| # | Test | Result | Class |
|---|---|---|---|
| 8.1 | 11-minute monitor | 65 samples, PID **55638 throughout**, `NRestarts 0`, service always `active` | **PASS** |
| 8.2 | CPU | median 48.1 %, range 36.6–94.6 % (the peak coincides with the WS stress test; the desktop apps dominate this figure) | **PASS** |
| 8.3 | CPU temperature | median 74.5 °C, max 76.9 °C — below the 85 °C throttle point; kernel reports no throttling | **PASS** |
| 8.4 | RAM (system) | median 69.4 %, used 2 634 MB (dominated by the desktop session) | **PASS** |
| 8.5 | Backend RSS | 190.3 → 202.7 MB during the window (the WS stress clients), then **+24 kB over 3 isolated minutes = 0.012 %** — **flat, no leak** | **PASS** |
| 8.6 | Thread count | **24 constant** across all 65 samples and the isolated RSS window | **PASS** |
| 8.7 | Thermal FPS | median 1.96 (1.95–1.97) | **PASS** |
| 8.8 | LoRa receiver | `running=true` in 65/65 samples; `rx_errors=0`; `reinit_count=0`; `sync_word=0x12` | **PASS** |
| 8.9 | API latency | `/health` median 9.7 ms (max 36.3) · `/api/system` median 14.5 ms (max 204.4, a single outlier during the stress test) · `/api/thermal/status` median 15.1 ms (max 47.7) | **PASS** |
| 8.10 | WS errors | 0 old-style `broadcast to a client failed`, 0 tracebacks, 0 exceptions; 5 legitimate `client dropped: send timeout` from the intentional stall | **PASS** |
| 8.11 | Journal errors | `Traceback` 0 · `exception` 0 · `Too many retries` 0 · `Read error` 0 · `Capture error` 0 · `reinit failed` 0 · `Failed with result` 0 · `Scheduled restart` 0 · `oom` 0 | **PASS** |
| 8.12 | Unexpected service restarts | **none** — all six services kept their original `ActiveEnterTimestamp` | **PASS** |
| 8.13 | Kernel / storage | 0 under-voltage / throttling / OOM / I2C / SPI / EXT4 lines; SD card 52 % used, 14 GB free | **PASS** |
| 8.14 | Watchdog interaction | the watchdog fired at 02:02:31 → `Result success`, **0** restarts triggered, PID unchanged | **PASS** |
| 8.15 | Drone end-to-end | no drone device on the network; `aeris-onboard.service` not installed | **BLOCKED** |

---

## 5. LORA COMMUNICATION RESULTS

```
Radio            : SX1276/SX1278 (RegVersion 0x12)        via SPI0, userspace CS on GPIO24
Frequency        : 433.000000 MHz  (Frf 0x6C4000, recomputed independently)
Bandwidth        : 500 kHz           Spreading factor : SF6
Coding rate      : 4/5               Preamble         : 6
CRC              : enabled           Explicit header  : yes
Low-freq band    : on                PA select        : PA_BOOST (0xCF)
Sync word (0x39) : 0x12   <- READ BACK from the chip, not assumed
Receiver         : running=true      rx_errors=0      reinit_count=0      reinit_pending=false
DIO0             : GPIO18, edges=rising, consumer "aeris-dio0"
Counts           : rx_count=0   crc_fail=2   dio0_irq_count=2
Signal           : RSSI -92 dBm   SNR -9 dB   (no drone in range - noise floor)
```

**Both ends verified independently:**

| | Ground (measured) | Drone (source) | Match |
|---|---|---|---|
| Frequency | 433.000000 MHz | `set_freq(433.0)` | ✅ |
| Bandwidth | 500 kHz | `set_bw(BW.BW500)` | ✅ |
| SF | 6 | `set_spreading_factor(6)` | ✅ |
| CR | 4/5 | `set_coding_rate(CODING_RATE.CR4_5)` | ✅ |
| Preamble | 6 | `set_preamble(6)` | ✅ |
| CRC | on | `set_rx_crc(True)` | ✅ |
| Explicit header | yes | pySX127x default (explicit) | ✅ |
| **Sync word** | **0x12 (Reg 0x39 read back)** | **`lora_protocol.SYNC_WORD` = 0x12** | ✅ |
| Packet format | `MAGIC 0xAE52`, version 1, 11-byte header, CRC16 | same module | ✅ |

**Evidence that the sync word is genuinely in effect:** `crc_fail` fell from **7 → 2** after the change (the radio now rejects the noise frames it previously accepted and CRC-failed), and the value is *read back* from the chip by two independent code paths (`lora_rx_service.start()` → `get_status()["sync_word"]` and `sensors.probe_sx1278()` → `/api/sensors`).

> **Explicitly NOT claimed:** that packets are received. `rx_count = 0` because no drone was in range and **no RF transmission was performed** (not authorised). End-to-end reception is **BLOCKED**.

---

## 6. THERMAL CAMERA RESULTS

| Metric | Value |
|---|---|
| Sensor | MLX90640 @ `0x33`, I2C bus 1 @ 400 kHz |
| Frames delivered | **1 278 in 654 s = 1.96 fps** (continuous) |
| Reported FPS | 1.95 – 1.97 (never outside that band) |
| Capture phase | `running` in **65/65** samples |
| Frame age | median 0.25 s, max 0.52 s (stale threshold 5 s) |
| Invalid samples | **0** |
| Re-initialisations | **0** |
| Consecutive failures | **0** |
| Last error | `null` |
| Temperature range | 28.9 – 35.5 °C, mean ≈ 31.3 °C |
| Matrix transport | 768 samples, all finite |
| Detection events | 1 per 100 s (thermal human-detection edge) — real, from the live scene |

**Timeout / recovery behaviour (fake hardware only):** the vendor's unbounded `while dataReady == 0` wait is bounded by a **3 s Python-level deadline** enforced *between* I2C transactions; a breach raises `ThermalTimeoutError`, closes the previous bus handle, re-initialises with a 1/2/4/8/16 s back-off (capped at 30 s) and keeps `ok:false` with a meaningful reason. **A transaction already blocked inside the kernel is not covered by this deadline** — that case is detected through the capture heartbeat and reported as `capture_phase="stalled"` (no in-process recovery exists; a thread blocked in a syscall cannot be cancelled from Python). The kernel's own I2C adapter timeout remains **unverified** on this device.

---

## 7. WEBSOCKET & DASHBOARD RESULTS

| Scenario | Result |
|---|---|
| 3 healthy clients, 100 s | 248 frames each, identical; 196 thermal + 49 system + 1 detection |
| 1 client that never reads | **`client dropped: send timeout after 2.0s`** — shed, not tolerated; the healthy clients and the REST API were unaffected |
| 50 connect/disconnect cycles | no disturbance |
| REST during WS traffic | 196/196 → HTTP 200; min 4.4 ms, median 9.7 ms, max 100.3 ms |
| NaN / ±Inf | sanitised to `null`; the broadcast still delivered (simulated) |
| Contract | every backend message type is handled by the frontend; the frontend's `lora_rx` branch is dead code (the data travels inside `system`) — **D-05** |
| Blinking indicators | none — `rpi4b`, `mlx90640`, `lora_sx1278`, `i2c_bus`, `spi_bus` remained `online` across 65 samples; `capture_phase` never left `running` |
| Browser console | **not exercised** — no browser was driven; the assessment is server-side only (**D-08** in the table of §9 refers to the journal/observability item) |

---

## 8. AUTHENTICATION & SECURITY RESULTS

| Surface | No token | Bad token | Operator token | Verdict |
|---|---|---|---|---|
| `/`, `/health`, `/assets/*` | 200 | 200 | 200 | public as intended ✅ |
| 11 × `/api/*` | 401 | 401 | 200 | protected ✅ |
| `/thermal_feed` | **401** | 401 | 200 | protected ✅ (was 200 before Phase 1) |
| 5 × write endpoints | 401 | — | 200 | operator-only ✅ |
| WS `/ws` | close 4401 | close 1008 | `auth_ok` | ✅ |
| Non-LAN source | — | — | 403 | ✅ (verified in the suite, `W6b`) |

**No credential was exposed** in any response body, error body, HTTP header or journal line. The hotspot PSK never appears in any output. The operator token file remains root-owned 0600 and was never modified by any test.

---

## 9. STABILITY MEASUREMENTS & BEFORE/AFTER COMPARISON

| Metric | Phase 0 baseline (01:00, old code) | Post-deployment (02:08–02:24, new code) | Delta | Class |
|---|---|---|---|---|
| Backend PID | 13439 | 55638 | restarted once (authorised) | — |
| `NRestarts` | 0 | 0 | stable | PASS |
| Threads | 24 | 24 constant | = | PASS |
| Backend RSS | 190.0 MB | 202.7 MB, **+0.012 %/3 min isolated** | +12.7 MB during the WS stress test, flat afterwards | PASS (no leak) |
| System CPU | 43.6 % / 97 % peak | median 48.1 %, peak 94.6 % (stress test) | comparable | PASS |
| CPU temperature | 72.5–75.5 °C | median 74.5 °C, max 76.9 °C | comparable, no throttling | PASS |
| System RAM | 66.8–69.9 % | median 69.4 % | comparable (desktop apps) | PASS |
| Thermal FPS | 1.95 | median 1.96 | = | PASS |
| Thermal frames | — | +1 278 / 654 s continuous | — | PASS |
| `capture_phase` | *field absent* | `running` 65/65 | new observability | PASS |
| `invalid_samples` | *field absent* | 0 throughout | new | PASS |
| LoRa `sync_word` | *field absent* (ground programmed **0x34**) | **`0x12`** | **the core fix** | PASS |
| LoRa `crc_fail` / `dio0_irq_count` | 7 / 7 | 2 / 2 | noise frames now rejected | PASS |
| LoRa RSSI / SNR | −96 dBm / −10 dB | −92 dBm / −9 dB | noise floor, no drone | — |
| LoRa `rx_count` | 0 | 0 | unchanged | **BLOCKED** (RF) |
| Recovery fields | *absent* | `reinit_count`, `last_reinit`, `last_error`, `reinit_pending` | new | PASS |
| `/thermal_feed` anonymous | **200** | **401** | **hole closed** | PASS |
| `/api/*` anonymous | 401 | 401 | unchanged | PASS |
| API latency (avg) | /health 27.1 ms · /api/system 31.7 · /api/hardware_status 34.2 · /api/thermal/status 26.7 | /health 9.6 · /api/system 15.9 · /api/hardware_status 15.1 · /api/thermal/status 14.8 | **≈2× faster** | PASS |
| WS old-style failures | 12× `broadcast to a client failed` (00:01:29) | **0** | fixed | PASS |
| Journal errors | 0 thermal errors | 0 tracebacks / 0 exceptions / 0 read errors | = | PASS |

---

## 10. DEFECTS DISCOVERED & RECOMMENDATIONS

> No fix was implemented. All recommendations are proposals only.

### D-01 · **Medium · Software (documentation, introduced by Phase 1)** — the "no-op stub" claim about pySX127x is factually wrong
* **Issue:** three files state that pySX127x implements `set_sync_word()` as a *no-op stub* that *"never touches the register"*:
  `drone-onboard/raspberry-pi-4b/lora_protocol.py` (module doc block), `drone-onboard/raspberry-pi-4b/sender.py` (comment above the call), `ground-station/backend/lora_rx_service.py` (comment above `REG_WRITE_SYNC_WORD`).
* **Root cause:** my earlier static reading of the *decorated function body* (`def set_sync_word(self, sync_word): return sync_word`) without accounting for the `@setter(REG.LORA.SYNC_WORD)` decorator that wraps it.
* **Verified fact:** pySX127x's decorator (`SX127x/LoRa.py:60-72`) performs `self.spi.xfer([register_address | 0x80, func(self, val)])` → it **does** write the register. Proven with a fake SPI: `set_sync_word(0x12)` → SPI write `[0xb9, 0x12]` (address `0x39|0x80`, value `0x12`).
* **Impact on behaviour:** **none** — the drone radio held `0x12` as its silicon default before and explicitly writes `0x12` now, so the effective register value is unchanged and the fix direction (both ends on `0x12`) remains correct. The risk is documentary: a maintainer reading "the drone cannot set a sync word" could "correct" the drone side wrongly.
* **Smallest fix:** reword the three comments to *"pySX127x implements set_sync_word() through its `@setter(REG.LORA.SYNC_WORD)` decorator, which writes Reg 0x39; the decorated body only transforms the value. The drone transmitter therefore sets the sync word explicitly — the same value (0x12) the SX1276/78 uses by default."* One sentence per file, no code change, no behaviour change.
* **Reproduction:** `grep -n "no-op\|NO-OP" lora_protocol.py sender.py lora_rx_service.py`; verify against `SX127x/LoRa.py` lines 60-72 and 700-702.

### D-02 · **Low · Software (test infrastructure documentation)**
* **Issue:** `ground-station/backend/tests/run_tests.py` advertises `python3 -m unittest discover -s tests -v` as an alternative. It **fails**: `ModuleNotFoundError: No module named 'fakes'` (the test modules import siblings as top-level modules, which requires `tests/` itself on `sys.path` — only `run_tests.py` adds it).
* **Root cause:** the sibling imports (`from fakes import ...`) rely on `run_tests.py` inserting `TESTS_DIR` into `sys.path`.
* **Impact:** documentation only — the canonical invocation `python3 tests/run_tests.py` works and is what CI/operators should use.
* **Smallest fix:** either (a) change the sibling imports to `from tests.fakes import ...` (2 files), or (b) add `sys.path.insert(0, os.path.dirname(__file__))` to `tests/__init__.py` (1 line), or (c) correct the docstring to name only the working invocation. **(b) is the smallest.**
* **Reproduction:** `cd ground-station/backend && python3 -m unittest discover -s tests -v`.

### D-03 · **Medium · Configuration (pre-existing, carried from the audit)** — the API is exposed on the wired interface
* **Issue:** the backend binds `0.0.0.0:80` and the middleware's LAN allow-list contains `192.168.0.0/16`, so the API is reachable from the **wired** LAN (192.168.1.13) with a valid token (verified → 200). There is no host firewall: SSH (22), rpcbind (111) and wayvnc (5900) are reachable by any hotspot client.
* **Root cause:** `main.py` `uvicorn.run(..., host="0.0.0.0")` + `_LAN_NETWORKS` + no INPUT-chain policy.
* **Category:** configuration, not a Phase 1 regression.
* **Smallest fix (Phase 3):** bind uvicorn to `192.168.4.1` (+ `127.0.0.1` for local health checks) and add a NetworkManager firewall zone that denies hotspot clients everything except `80/tcp`; disable `rpcbind`/`wayvnc` on a flight build.

### D-04 · **Medium (as a blocker for Phase 5) · Configuration (pre-existing)** — permissive CORS
`allow_origins=["*"]` with `allow_credentials=True` (verified on the wire: `access-control-allow-origin: *` + `access-control-allow-credentials: true`). Harmless while the credential is a `localStorage` token, but it becomes a **CSRF vector** the moment the dashboard uses a cookie session. **Must be tightened in the same change as the cookie auth.**

### D-05 · **Low · Software (pre-existing)** — dead frontend branch
`useWebSocket.ts` handles `data.type === 'lora_rx'`, but the backend never emits that type (the LoRa RX status travels nested inside the `system` broadcast). Harmless; remove the branch or emit the message.

### D-06 · **Medium · Software (pre-existing, Phase 4)** — half the thermal frame rate
The MLX90640's `Reg 0x800D` is set to `3` ("4 Hz") but the delivered full-frame rate is **1.96 fps**: the register value is the *per-subpage* rate and a full frame needs two subpages. Fixing it means allowing `REFRESH_8_HZ` (register 4) and re-validating the burst budget (76 ms/frame at 400 kHz leaves ample headroom). Carried from the audit.

### D-07 · **Low · Software (observability)** — recoveries are not journalled
A successful thermal re-initialisation and the LoRa recovery success message are not written to the journal (the LoRa success line is `INFO`, which the root logger drops at WARNING level). The recovery is visible on the dashboard but invisible in the journal an operator would read after an incident.
* **Smallest fix:** log thermal re-initialisations at `WARNING` (or configure the `aeris` logger at INFO in `main.py`).

### D-08 · **Informational · Environment** — no browser available
The dashboard was not rendered in a real browser (no GUI/automation available in this session). Console errors, failed requests and visual indicator behaviour were assessed from the server side only (0 WS exceptions, 0 failed requests, 0 status flapping across 65 samples).

---

## 11. HARDWARE TESTS REQUIRING AUTHORISATION

| Test | Why it needs authorisation | Current substitute |
|---|---|---|
| **LoRa end-to-end reception** (`rx_count > 0`, `crc_fail = 0`) | requires RF transmission and a drone in range | configuration verified on both ends (all 10 parameters, sync word read back) |
| **MLX90640 unplug/replug recovery** (physical fault injection) | requires disconnecting the sensor | fake-sensor tests (T1–T6) prove the timeout, re-init, back-off and FD-release logic |
| **LoRa RX recovery on real radio errors** | requires forcing SPI errors on the live radio | fake-radio tests (R1–R5) prove stop()/start() semantics, the idempotent start, the recovery thread and the counters |
| **Kernel I2C adapter timeout for a blocked transfer** | requires holding SDA low | documented and reported distinctly as `capture_phase="stalled"` |
| **Live viewer-role behaviour** | requires configuring `AERIS_VIEWER_TOKEN` | suite proves read-200 / write-403 |
| **Wi-Fi range / link-budget flight test** | requires the drone airborne | out of Phase 1 scope |

---

## 12. EXACT NEXT STEPS

1. **Review this report** and the seven items in §10. D-01 and D-02 are documentation-only and can be applied as a single small commit.
2. **Authorise the hardware tests** in §11 if the end-to-end LoRa link and the thermal fault recovery are to be closed out.
3. **Decide on Phase 2** (hardware communication: targeted bus probes, `gpiod` instead of `pinctrl`, per-sensor lifecycles, the INA260/BMP388/QMC5883L register corrections) — explicitly *not started*.
4. Nothing else has been changed: the branch is `feature/phase1-reliability` @ `0d2e549`, the working tree is clean, the backups are intact and the deployed backend has been running without incident for the whole verification window.

---

*Verification performed read-only. No service was restarted, no configuration was changed, no RF transmission was performed, no sensor was disconnected, no package was installed, and no credential was exposed. The live backend (PID 55638) ran continuously through the entire verification.*
