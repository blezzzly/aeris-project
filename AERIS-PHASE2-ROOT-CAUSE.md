# AERIS — PHASE 2 INVESTIGATION: CONNECTION INSTABILITY

**Status: DIAGNOSIS COMPLETE. No fix applied. No service restarted. No hardware touched. Awaiting approval to implement.**

System: Raspberry Pi 4B · Debian 13 · branch `feature/phase1-reliability` @ `c4b26eb` · deployed backend PID 55638 (running since 01:58:32)

---

## 1. HEADLINE

> **The hardware is not disconnecting. The dashboard is reporting a transport problem as a hardware problem, and one slow WebSocket client is starving every other client of the backend's event loop.**

Measured over **1 695 consecutive samples (14.2 minutes at 0.5 s)** of the backend's own hardware verdicts:

| Device | States observed | Transitions |
|---|---|---|
| `mlx90640` | `online` **1695 / 1695** | **0** |
| `lora_sx1278` | `online` **1695 / 1695** | **0** |
| `i2c_bus` | `online` **1695 / 1695** | **0** |
| `spi_bus` | `online` **1695 / 1695** | **0** |
| `capture_phase` | `running` **1695 / 1695** | **0** |
| thermal frame age | min 0.000 s · max 0.530 s (stale threshold 5 s) | — |

Journal during the same window: **0** `system broadcast failed` · **0** `Bus] scan failed` · **0** `Too many retries` · **0** `Read error` · **0** `Capture error` · **0** `Traceback`.

The radio's `op_mode` stayed `0x8d` (LoRa + RX continuous) throughout a 3.5-minute window covering three 60-second identity probes → the probe does **not** knock the receiver out of RX.

**Conclusion: the MLX90640, the I2C bus, the SPI bus and the LoRa radio are all stable and correctly reported. Every "OFFLINE" the dashboard shows is produced downstream of the WebSocket transport.**

---

## 2. THE ROOT CAUSE — four defects in a chain

### Defect 1 — One stalled client saturates the event loop (the primary cause)

`main.py` `ConnectionManager.broadcast()`:

```python
for connection in list(self.active_connections):
    await asyncio.wait_for(connection.send_json(payload), timeout=WS_SEND_TIMEOUT_S)
```

* The loop **awaits each client in turn, in connection order**. A client whose socket buffer is full blocks the loop for the full **2 s** timeout.
* The thermal capture schedules a **new broadcast every frame** (~2/s) with `asyncio.run_coroutine_threadsafe(...)`, **without awaiting the previous one** → several broadcasts are in flight at once, each holding its own pre-removal snapshot of the client list.

**Evidence:**

| Measurement | Result | Expected if healthy |
|---|---|---|
| "client dropped" log lines in a 45 s window with **ONE** stalled client | **137** | 1 |
| Frames received by a **healthy** client that connected *behind* a stalled client | **12 in 90 s** | ~230 |
| Frames received by a healthy client that connected *in front of* the stalled one | 149 in 60 s (max gap 0.55 s) | ~150 |

→ the drop is re-logged on **every** broadcast because several in-flight broadcasts each hold a pre-removal snapshot; and **connection order decides who is starved** — a client that reconnects (as the laptop does) lands *behind* the offender.

### Defect 2 — A dropped client is never told, so it never reconnects

`_drop()` removes the socket from the fan-out list but **never sends a WebSocket close frame**. Measured on a dropped client:

```
established conns to :80 : before=0  with-client=1
50 s later (server has dropped it): established=0
-> the server did close the socket
the 'dropped' client is STILL RECEIVING data: {"type":"auth_ok","role":"operator"}
!! the client CANNOT close: the server never completes the close handshake (it kept the socket open)
```

The client never receives a close frame ⇒ the browser's `onclose` does not fire ⇒ **the frontend never reconnects** ⇒ the dashboard stays frozen on the last frame it received.

**Live evidence — the operator's own laptop (`192.168.4.57`):**

```
02:46:34  connection accepted / auth handshake role=operator
02:47:16  [WS] client dropped: send timeout after 2.0s     (x5)
02:49:35  connection accepted / auth handshake role=operator   <- 2 min 19 s later
02:50:17  [WS] client dropped: send timeout after 2.0s     (x5)
```

**42 s connected → dropped → 2 min 19 s dead.** During the dead window the dashboard shows every hardware item as OFFLINE.

### Defect 3 — The dashboard paints a transport problem as a hardware problem

`frontend/src/hooks/useHardwareStatus.ts`:

```javascript
const linkFresh = reportAgeMs != null && reportAgeMs >= 0 && reportAgeMs <= REPORT_STALE_MS; // 15 s
const rpi    = linkFresh ? ... : 'offline';
const thermal= linkFresh ? ... : 'offline';
const lora   = linkFresh ? ... : 'offline';
const i2c    = linkFresh ? ... : 'offline';
const spi    = linkFresh ? ... : 'offline';
```

**Every** device is gated on "a report arrived in the last 15 s". A Wi-Fi hiccup on the laptop therefore paints the *whole* hardware inventory red, even though the backend measured every device as healthy. `clearHardwareData()` exists in the store but is never called, so the last known state is kept and merely age-gated.

Independently, `frontend/src/components/ThermalCanvas.tsx` shows **"Thermal sensor offline"** whenever no thermal frame has arrived for **5 s** — again a *transport* condition (frames delayed behind a stalled client), not a sensor condition. This is exactly the user's primary symptom.

### Defect 4 — What triggers the stall on the laptop (the trigger, not the cause)

`iw dev wlan0 station dump` for the laptop (`18:cc:18:b1:1a:2f`):

```
tx packets: 3371    tx bytes: 3325009    tx failed: 718   (~21 % failed)
tx bitrate: 43.3 MBit/s   rx bitrate: 2.0 MBit/s   DTIM period: 2
```

21 % of the AP→laptop transmissions fail to deliver, and the laptop's radio is in DTIM power-save. Any pause in the browser reading the socket (background tab throttling, a Wi-Fi roam, a momentary link loss) fills the server's socket buffer; after roughly 40 s of backlog the server's `send_json` blocks and the client is dropped.

---

## 3. WHAT WAS RULED OUT

| Suspect | Verdict | Evidence |
|---|---|---|
| MLX90640 disconnecting | **RULED OUT** | `online` 1695/1695 samples; 0 read errors; frame age ≤ 0.53 s |
| I2C bus flapping | **RULED OUT** | `i2c_bus` `online` 1695/1695; 0 `Bus] scan failed` |
| SPI bus flapping | **RULED OUT** | `spi_bus` `online` 1695/1695 (the SPI verdict is derived from the device nodes, which always exist) |
| LoRa radio resetting / leaving RX | **RULED OUT** | `op_mode` stayed `0x8d` across three identity probes; `rx_errors=0` |
| The 60 s identity probe disturbing the receiver | **RULED OUT** for this symptom | `op_mode 0x8d` continuously; it does open a second SPI handle and hold the radio lock ~180 ms, but that does not move the dashboard state |
| Backend service instability | **RULED OUT** | PID 55638 since 01:58:32, `NRestarts 0`, `Result success`, 0 tracebacks |
| Power / under-voltage / throttling | **RULED OUT** | 0 matching kernel lines; `throttled=0x0`; CPU 72–77 °C, no throttling |
| False status from the sensor layer | **RULED OUT** | the backend's per-device verdicts never changed |
| **The WebSocket fan-out + the dashboard's age-gating** | **CONFIRMED — the cause** | 137 drop events in 45 s; a healthy client starved to 12 frames/90 s; `linkFresh` gates every device |

---

## 4. PROPOSED FIX

Nothing below is implemented. All of it is confined to the backend + frontend code; **no GPIO, I2C, SPI, LoRa, Wi-Fi, systemd or boot configuration is touched.**

### Fix A — Per-client isolation (backend, core) ⭐
Give every WebSocket **its own bounded outbound queue and a dedicated writer task**. `broadcast()` then only *enqueues* — it never awaits a socket, so it can never block the event loop.

* A slow client fills only **its own** queue; the queue is capped (e.g. 32 messages / 256 KB).
* On overflow **that client alone** is dropped. **No other client is affected.**
* The loop can never be saturated, so broadcasts cannot pile up.

### Fix B — Tell the dropped client (backend) ⭐
When a client is dropped, **send a WebSocket close frame** (e.g. code 1013 "try again later") before removing it, so the browser's `onclose` fires **immediately** and the frontend reconnects at once instead of waiting ~2 minutes for a TCP timeout.

### Fix C — Coalesce broadcasts (backend)
One "broadcast pending" flag so the loop cannot be saturated by a backlog, and keep only the **newest** thermal frame per client (a superseded frame is worthless). This also bounds the work when many clients are attached.

### Fix D — Separate "link down" from "hardware down" (frontend) ⭐
Show the backend's **per-device verdict** (which is measured and trustworthy) and use the existing **"LIVE LINK OFFLINE"** banner for the transport state. A device is marked offline **only when the backend says so**. This removes the "all hardware goes red because the laptop's Wi-Fi blinked" behaviour while keeping the dashboard honest — it is *not* hiding a fault, it is attributing the fault to the right layer.

### Fix E — Frontend reconnect robustness
* Reset the reconnect backoff when the socket was healthy for > 10 s (today `attempt` only resets on `auth_ok`, so a flapping link keeps the 8 s delay forever).
* Cap the backoff at ~2–3 s.
* Reconnect on `visibilitychange` (browsers throttle timers in background tabs to once per minute, which is exactly how a 2-minute dead window was produced).

### Fix F — Lighter thermal frame (backend + frontend, optional in this phase)
The thermal matrix travels as **768 JSON floats (4 722 B) at 2 Hz**. As 16-bit binary/base64 (or a small PNG) it is ~1.5 KB — about **70 % less traffic**, which materially helps a marginal 2.4 GHz link. (Keeps the payload format decision explicit; the packet format on the radio is unaffected.)

### Fix G — Link quality (Phase 3, **not** part of this fix)
5 GHz AP on a non-DFS channel, a proper antenna, Wi-Fi power save off. This reduces the *trigger*; it is not the cause and does not belong in the code fix.

---

## 5. WHAT THE FIX DOES **NOT** DO

* It does **not** fake any sensor reading.
* It does **not** force any status to ONLINE. The backend's verdicts stay exactly as measured — `online` only when the device really answered.
* It does **not** hide a real hardware failure: a genuine sensor failure still yields `offline`/`stale` with its reason, exactly as today.
* It changes **no** GPIO, I2C, SPI, LoRa parameter, frequency, payload format, Wi-Fi setting, systemd unit or boot configuration.
* It performs **no** RF transmission and injects **no** hardware fault.

---

## 6. VERIFICATION PLAN (to run after approval)

| # | Test | Pass criterion |
|---|---|---|
| V1 | **Isolation** — 1 healthy + 1 deliberately stalled client, 90 s | the healthy client keeps **≥ 98 %** of its frames (today: 12/230 ≈ 5 %) |
| V2 | **Isolation** — 1 healthy + 3 stalled clients, 90 s | the healthy client keeps **≥ 98 %** of its frames |
| V3 | **Drop semantics** — a dropped client must be told | the client receives a close frame within ~2 s and can reconnect immediately |
| V4 | **No log spam** — 1 stalled client, 45 s | ≤ 2 "client dropped" lines (today: 137) |
| V5 | **Backend truth** — 30 min sampler at 0.5 s | **0** device-state transitions, `capture_phase=running` throughout, frame age ≤ 1 s |
| V6 | **Dashboard truth** — with the laptop connected, induce a Wi-Fi interruption on the laptop | the dashboard shows the **link** banner, and the hardware indicators keep the backend's last measured verdicts instead of all turning red |
| V7 | **Recovery** — disconnect/reconnect the laptop | the dashboard reconnects within **≤ 5 s** (today: up to 2 min 19 s) |
| V8 | **Regression** — the full stdlib suite | 48/48 still pass, **plus** new isolation tests (isolation, drop-notification, coalescing) |
| V9 | **Live soak** — the laptop connected for 30–60 min | 0 WS drops of the laptop, 0 backend errors, flat RSS, stable frame rate |
| V10 | **TypeScript / lint** | `tsc --noEmit` exit 0; `oxlint` exit 0 with no new warnings |

A **deployment** (one `systemctl restart aeris-backend`) would be needed for V5–V10 and will be requested separately.

---

## 7. ESTIMATED CHANGE SCOPE

| File | Change | ~lines |
|---|---|---|
| `ground-station/backend/main.py` | per-client queue + writer task, close-frame on drop, broadcast coalescing | ~120 |
| `ground-station/frontend/src/hooks/useHardwareStatus.ts` | hardware state from the backend's verdict; link state separate | ~30 |
| `ground-station/frontend/src/hooks/useWebSocket.ts` | backoff reset/cap, reconnect on `visibilitychange` | ~25 |
| `ground-station/frontend/src/components/ThermalCanvas.tsx` | distinguish "no recent frame" from "sensor offline" | ~15 |
| `ground-station/backend/tests/` | new isolation / drop-notification / coalescing tests | ~150 |

No public API changes, no protocol change, no frontend rebuild required beyond the normal `npm run build` for the three touched frontend files.

---

## 8. REQUEST FOR APPROVAL

I am asking for approval to implement **Fixes A–E** (backend isolation + drop notification + coalescing, frontend truth separation + reconnect robustness). **Fix F** (binary thermal frame) and **Fix G** (radio/link) are separable and can be deferred.

On approval I will: implement on `feature/phase1-reliability`, extend the stdlib test suite, run the full verification (V1–V10), commit, and then **stop and request separate authorisation** for the deployment restart.
