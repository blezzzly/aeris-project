# AERIS — PHASE 2 ROOT-CAUSE REPORT AND IMPLEMENTATION PROPOSAL

**System:** AERIS Ground Station · Raspberry Pi 4 Model B Rev 1.2 · Debian GNU/Linux 13 (Trixie) · kernel 6.18.60+rpt-rpi-v8  
**Branch:** `feature/phase1-reliability` @ `c4b26eb` (clean working tree)  
**Baseline:** `main` / `origin/main` @ `4524cd3` — untouched  
**Runtime:** `aeris-backend` PID **55638**, active since **2026-11-13 01:58:32 PST**, `NRestarts=0`  
**Diagnostic window:** 2026-11-13 02:08 → 03:23 PST  
**Report status:** **DIAGNOSIS COMPLETE — APPROVAL REQUIRED — NO IMPLEMENTATION STARTED**

> **Bottom line:** the Raspberry Pi hardware is not disconnecting and the backend is not restarting. The remote dashboard flips every device to OFFLINE because a **client-link problem** is incorrectly rendered as a **hardware failure**. The chain is: Wi-Fi link stalls/drops (with AP power-save enabled as a confirmed physical-layer defect) → Serialized WebSocket fan-out blocks for 2 s per stalled client (measured **16.02 s with 8 stalled clients**) → the 15 s hardware-freshness rule marks every device OFFLINE → the dropped socket is never closed, so the browser never reconnects → no heartbeat/watchdog leaves a half-open link stuck. The hardware layer remained independent and healthy throughout.

---

## 1. EXECUTIVE SUMMARY

| Question | Finding |
|---|---|
| Is the physical hardware disconnecting? | **No evidence of that.** Backend PID unchanged, `NRestarts=0`, zero tracebacks/capture errors, no sensor re-initialization, no LoRa re-initialization during client churn. |
| Is the backend restarting or crashing? | **No.** Process healthy for the whole window; HTTP `/health` returned 200 on **398/398** samples with **9.9 ms average** and **40.5 ms maximum** response time. |
| Why does remote monitoring flap? | The remote browser crosses the Wi-Fi AP and is exposed to link stalls/drops. The backend's serialized WebSocket sends then delay data beyond the dashboard's 15 s freshness window, and the frontend converts "link stale" into "all hardware OFFLINE". |
| Why does local monitoring work? | The local browser uses loopback (`127.0.0.1`) and does not cross the Wi-Fi link. It is not exposed to the AP drop or the remote client's TCP stalls. |
| Is this a DHCP problem? | **No.** The laptop retained `192.168.4.57`; the dnsmasq lease is 3600 s and stable. |
| Is this a thermal/CPU throttling problem? | **No.** CPU was 74–75.4 °C, `vcgencmd get_throttled` = `0x0`, no throttling. Load was high (≈5–6) because the local desktop/Firefox/agent workload is running, but the backend answered promptly and the hardware did not fault. |
| Is this a duplicate-polling problem? | **No.** The backend is the single owner of hardware acquisition; clients receive shared broadcasts. There is no per-client sensor polling. |
| Is the AP configuration itself healthy? | Mostly, but **Wi-Fi power save is ON** on `wlan0`, while the NetworkManager profile leaves it at `0 (default)`. This is a confirmed misconfiguration and the leading physical-layer suspect for the AP-STA disconnects. |

### The two proven software defects

1. **Blocking, serialized WebSocket fan-out.**
   - `ConnectionManager.broadcast()` awaits `send_json()` **one client at a time** with a 2 s timeout.
   - 3 stalled clients delayed one broadcast by **6.01 s**.
   - 8 stalled clients delayed one broadcast by **16.02 s**, which is already beyond the frontend's `REPORT_STALE_MS = 15_000`.
   - The MLX90640 is configured for the highest sustainable rate (4 Hz when the I2C bus supports it, otherwise 2 or 1 Hz), plus a system broadcast every 2 s; the resulting overlapping broadcasts can also call `send_json()` on the **same socket concurrently** (measured `max_simultaneous_sends = 2`).
   - The production journal contains **142** `[WS] client dropped` lines and **15** `Unexpected ASGI message 'websocket.send', after sending 'websocket.close'` errors.

2. **Dropped clients are removed but never closed.**
   - `_drop()` removes the socket from the fan-out list and logs the failure, but never calls `websocket.close()`.
   - Reproduction result: `removed=True close_called=False close_code=None`.
   - The browser therefore never receives `onclose`, never schedules its existing reconnect, and can remain in a half-open state indefinitely.
   - There is no server heartbeat and no client silence watchdog.

### The frontend defect

`useHardwareStatus()` returns `'offline'` for **every** device whenever `linkFresh` is false:

```ts
const rpi = linkFresh ? (...) : 'offline';
const thermal = linkFresh ? (...) : 'offline';
const lora = linkFresh ? (...) : 'offline';
const i2c = linkFresh ? (...) : 'offline';
const spi = linkFresh ? (...) : 'offline';
```

That is a **link-state** decision applied to **device-state** indicators. A remote Wi-Fi stall therefore displays exactly like a failed MLX90640, failed SX1278, failed I2C bus, and failed Raspberry Pi at the same time — which is not what the hardware measured.

---

## 2. CHANGE-CONTROL STATE

### Git

- Branch: `feature/phase1-reliability` @ `c4b26eba21bc3327699849199f7b74c8f4ebda5a`
- `main`: `4524cd3abd6b0002ed5e7660bf0f32a93cf7c8de`
- `origin/main`: same as `main`
- Working tree: clean before this report; this report is the only new file.
- Stashes: none.
- Recovery commit `c7b291a419a5a50b89d67bb3fbee8786faab18aa` exists and is reachable by hash.
- No worktree other than `/home/piaeris/aeris-project`.
- **No commit, branch, reset, rebase, checkout, or destructive Git operation has been performed.**

### Backups

`/home/piaeris/aeris-backups/20261113-010000/`:

| File | Size | Purpose |
|---|---:|---|
| `10-project-source.tar.gz` | 25,190,850 B | source-only restore |
| `11-project-FULL.tar.gz` | 263,749,908 B | full restore including venvs/.git |
| `04-untracked-files.tar.gz` | 74,116 B | untracked files |
| `RESTORE.md` | 2,979 B | verified restore procedure |
| `20-system-config/` | — | systemd, `/etc/aeris`, token backup |

The frontend `dist/` is **gitignored** and currently built at 2026-11-13 00:28. It must be backed up before any rebuild.

### Runtime state

| Item | Value |
|---|---|
| Backend PID | 55638 |
| Active since | 2026-11-13 01:58:32 PST |
| `NRestarts` | 0 |
| Backend CPU at 03:20 | 48.8 % of one core |
| Backend RSS | 195,712 kB |
| CPU temp | 74.0–75.4 °C |
| `vcgencmd get_throttled` | `0x0` |
| Load average | 5.85 / 5.28 / 4.98 (local desktop workload) |
| AP | `wlan0` `192.168.4.1/24`, SSID `AERIS-GroundStation`, channel 6, 20 MHz, WPA2-PSK |
| AP power save | **`Power save: on`** |
| NetworkManager profile | `802-11-wireless.powersave: 0 (default)` |
| Hotspot guard | active, PID 13742 |
| Watchdog timer | active |
| Unauthorized changes | none |

`sudo -n` is not available to this session (`a password is required`), so deployment, network changes, and reading the root-only token file require explicit user action/approval.

---

## 3. ARCHITECTURE AS BUILT

```text
                       AERIS-GroundStation (2.4 GHz, ch 6)
 Laptop / Phone / Tablet  ────────────── Wi-Fi ──────────────┐
                                                             │
                                                   wlan0 192.168.4.1/24
                                                             │
                                              NetworkManager AP + dnsmasq
                                                             │
                                              FastAPI + uvicorn :80
                                              (single asyncio loop)
                                                             │
                        ┌────────────────────────────────────┼────────────────────────────────────┐
                        │                                    │                                    │
                 system broadcaster                  thermal capture thread              sensor poll thread
                 every 2.0 s                         MLX90640 / I2C                      I2C/SPI probes
                        │                                    │                                    │
                        └──────────────► shared in-process state ◄───────────────────────────────┘
                                                     │
                                          ConnectionManager.broadcast()
                                          sequential send_json(), 2 s timeout
                                                     │
                                          all WebSocket clients
                                                     │
                                          React dashboard (dist/)
```

**What is already correct:**

- The Raspberry Pi owns all hardware acquisition. The browser never talks to I2C/SPI/LoRa directly.
- Multiple clients share the same backend state; there is no per-client sensor polling or per-client initialization.
- Frontend assets in `dist/index.html` are local (`/assets/index-*.js`, `/assets/index-*.css`, local icons/images); no CDN is required for the app shell.
- Authentication is local token auth; the WebSocket token travels in the first auth frame, not the URL.
- Thermal capture, LoRa RX, and the sensor poller are independent of client lifecycles.

**What is wrong:**

- Wi-Fi AP power save is enabled.
- The WebSocket fan-out is serialized and blocking.
- Dropped clients are not closed.
- No heartbeat or silence watchdog exists.
- Link freshness is treated as hardware health.
- The map and weather panels still make optional external requests; the app shell is offline, but those panels are not fully offline-first.

---

## 4. EVIDENCE

### 4.1 The hardware and backend stayed healthy

Journal checks over the backend's full lifetime:

- `NRestarts=0`, PID unchanged.
- `Traceback` count: **0**.
- Thermal capture errors / LoRa reinit / sensor reinit during client churn: **0**.
- Backend `[WS] system broadcast failed` / `thermal frame broadcast failed`: **0**.
- The only recurring errors are the WebSocket client-drop and ASGI-close messages, which are caused by the fan-out defect.

Ten-minute health sampler (`/tmp/opencode/aeris_diag.csv`, 398 samples, no clients attached for the final portion):

| Metric | Result |
|---|---|
| HTTP `/health` samples | 398 |
| Non-200 responses | **0** |
| Average response time | **9.9 ms** |
| Minimum | 3.1 ms |
| Maximum | 40.5 ms |
| Backend PID across samples | 55638 (unchanged) |

This proves the backend and the Pi remained responsive while the remote browser was flapping. It also proves the flapping is not a backend crash or a thermal-throttling event.

> Note: the Pi's load average was high (≈5–6) because a local desktop, Firefox, and the diagnostic agent are running. That can add scheduling jitter, but the backend still answered in single-digit milliseconds and `throttled=0x0`; load is a contributing background condition, not the root cause.

### 4.2 Wi-Fi AP evidence

```text
$ iw dev wlan0 get power_save
Power save: on

$ nmcli -g 802-11-wireless.powersave con show aeris-hotspot
default

$ journalctl -k | grep brcmfmac
Nov 13 00:02:02 raspberrypi kernel: brcmfmac: brcmf_cfg80211_set_power_mgmt: power save enabled
```

AP profile:

```text
802-11-wireless.ssid:             AERIS-GroundStation
802-11-wireless.mode:             ap
802-11-wireless.band:             bg
802-11-wireless.channel:          6
802-11-wireless.powersave:        0 (default)
802-11-wireless-security.key-mgmt: wpa-psk
ipv4.method:                      shared
ipv4.addresses:                   192.168.4.1/24
```

The laptop (`18:cc:18:b1:1a:2f`, `192.168.4.57`) produced 7 disconnect/connect pairs:

| Session start | Session end | Duration |
|---|---|---:|
| 00:00:08 | 00:01:03 | 55 s |
| 02:46:27 | 02:53:36 | 429 s |
| 02:53:40 | 02:56:37 | 177 s |
| 02:56:37 | 03:03:02 | 385 s |
| 03:03:20 | 03:04:39 | 79 s |
| 03:04:42 | 03:05:39 | 57 s |
| 03:05:47 | 03:12:58 | 431 s |

The final disconnect was at **03:12:58**. Immediately afterwards:

```text
$ ip neigh show 192.168.4.57
192.168.4.57 dev wlan0 FAILED

$ ping -c 40 -i 0.2 -W 1 192.168.4.57
40 packets transmitted, 0 received, 100% packet loss
```

The AP is on a locally clear channel: channel 6 had no competing SSID in the scan; channel 1 was congested (`fh_ssid1`, `KESTREL PHOS28`). Channel congestion is therefore not the explanation.

**Important limitation:** the available logs prove the AP-STA deauth events, and they prove power save is enabled, but they do not contain the driver's deauth reason code. The exact cause of each individual deauth is not proven. Power save in AP mode is a known source of missed beacons/buffered-frame stalls on `brcmfmac`, and it is a confirmed misconfiguration that must be removed; the software fan-out defect is independently proven and is sufficient to produce the OFFLINE flapping even without a deauth.

### 4.3 WebSocket fan-out evidence

Production code:

```python
WS_SEND_TIMEOUT_S = 2.0

async def broadcast(self, message):
    payload = _json_safe(message)
    for connection in list(self.active_connections):
        try:
            await asyncio.wait_for(
                connection.send_json(payload), timeout=WS_SEND_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            self._drop(connection, f"send timeout after {WS_SEND_TIMEOUT_S}s")
```

`_drop()`:

```python
def _drop(self, websocket, reason):
    self.disconnect(websocket)
    print(f"[WS] client dropped: {reason}")
```

There is no `websocket.close()`.

Production counter examples (not a complete list):

| Minute | `[WS] client dropped` lines |
|---|---:|
| 02:47 | 5 |
| 02:50 | 5 |
| 02:51 | 10 |
| 02:57 | 5 |
| 02:58 | 4 |
| 02:59 | 41 |
| 03:01 | 33 |
| 03:05 | 5 |
| 03:06 | 5 |
| 03:07 | 11 |
| 03:19–03:20 | 5 |
| **Total by 03:20** | **142** |

The 03:19–03:20 entries are from a localhost client, so the total is not exclusively the laptop. The 02:47–03:07 burst includes the remote client sessions and is the relevant failure window.

Reproduction with the real `ConnectionManager` and production timeout:

```text
3 stalled clients:
  elapsed = 6.01 s
  healthy_received = 1

8 stalled clients:
  elapsed = 16.02 s
  healthy_received = 1
```

Reproduction of the close defect:

```text
DROP: removed=True close_called=False close_code=None
```

Reproduction of concurrent writes:

```text
CONCURRENCY: max_simultaneous_sends=2 delivered=2
```

Reproduction scripts (temporary, outside the repository):

- `/tmp/opencode/probe_ws.py`
- `/tmp/opencode/probe_blocking.py`
- `/tmp/opencode/probe_8stalled.py`

**Why this produces visible flapping:** the frontend treats a hardware report older than **15 s** as not fresh. One broadcast with 8 stalled clients already takes **16.02 s**. The thermal feed runs at up to 4 frames per second (2 or 1 Hz if the I2C bus is slower), and the system broadcast every 2 s; when several broadcasts overlap, the same client can be written concurrently and the system broadcaster's next cycle is delayed. A remote client that has a brief Wi-Fi stall can therefore cause every dashboard to show OFFLINE even though the Pi measured everything as online.

### 4.4 Frontend conflation evidence

`ground-station/frontend/src/lib/hardwareStatus.ts`:

```ts
export const REPORT_STALE_MS = 15_000;
export const FRAME_STALE_MS = 5_000;
```

`ground-station/frontend/src/hooks/useHardwareStatus.ts`:

```ts
const linkFresh = reportAgeMs != null && reportAgeMs >= 0 && reportAgeMs <= REPORT_STALE_MS;

const rpi = linkFresh ? (...) : 'offline';
const thermal = linkFresh ? (...) : 'offline';
const lora = linkFresh ? (...) : 'offline';
const i2c = linkFresh ? (...) : 'offline';
const spi = linkFresh ? (...) : 'offline';
```

This is the direct UI cause of the ONLINE→OFFLINE→ONLINE display. The backend might still be reporting `mlx90640: online`, `lora_sx1278: online`, `i2c_bus: online`, `spi_bus: online`, but the dashboard never reaches that code once `linkFresh` is false.

`App.tsx` only shows the link-warning banner when `!wsConnected`. Because the dropped socket is never closed, `wsConnected` stays `true` while the hardware data goes stale. The user sees no "link lost" banner, only red hardware indicators.

`useLinkState()` exists but is unused. `clearHardwareData()` exists in the store but is never called; the corruption happens through `linkFresh`, not through clearing.

### 4.5 Offline dependency audit

Local app shell:

- `dist/index.html` references local `/assets/index-*.js`, `/assets/index-*.css`, local `favicon.svg`, and local `icons.svg`.
- No CDN script/style/font reference was found in the built HTML.
- Authentication is local; no cloud auth.

Optional external runtime requests:

| Location | External URL | Effect offline |
|---|---|---|
| `frontend/src/hooks/useLiveWeather.ts` | `https://api.open-meteo.com/...` | Weather panel shows `Unavailable`; UI still works. Retries every 5 min. |
| `frontend/src/components/MissionMap.tsx` | OpenStreetMap and Esri tile servers | Map background tiles fail; markers still render over a blank/grey map. |

The backend already has an offline-aware `weather_cache.py` and `GET /api/weather`, but the frontend bypasses it and calls Open-Meteo directly. The map has no offline basemap fallback. These are not the cause of the status flapping, but they prevent a fully offline-first acceptance.

### 4.6 Security baseline (unchanged)

Phase 1 open items remain open and were **not** changed:

- CORS `allow_origins=["*"]`.
- SSH listening on 22.
- `rpcbind` on 111.
- `wayvnc` on 5900.
- Backend runs as root with `NoNewPrivileges=yes`.

Phase 2 will preserve token auth, LAN-only API access, and the existing close codes. No authentication will be disabled and no new network service will be exposed.

---

## 5. ROOT-CAUSE STATEMENT

### Primary causal chain

```text
AP power-save enabled / client Wi-Fi stall or deauth
        │
        ▼
TCP send stalls or half-open connection
        │
        ▼
ConnectionManager.broadcast() awaits each client serially (2 s each)
        │
        ├── 3 stalled clients = 6.01 s measured
        ├── 8 stalled clients = 16.02 s measured (> 15 s freshness)
        └── overlapping thermal broadcasts can write the same socket concurrently
        │
        ▼
No fresh "system" frame reaches the browser for > 15 s
        │
        ▼
useHardwareStatus() forces rpi/thermal/lora/i2c/spi = "offline"
        │
        ▼
Dashboard shows ONLINE → OFFLINE → ONLINE

Separately:
_drop() removes the socket but never closes it
        │
        ▼
Browser never receives onclose → never reconnects
        │
        ▼
No heartbeat/watchdog → half-open link can persist
```

### What the root cause is **not**

- Not a hardware disconnect: hardware state never changed in the journal; PID unchanged; zero thermal/LoRa reinit.
- Not a backend crash: `NRestarts=0`; 398/398 health checks passed.
- Not DHCP: lease stable; laptop kept `192.168.4.57`.
- Not channel congestion: channel 6 was locally clear.
- Not thermal throttling: 74–75 °C, `throttled=0x0`.
- Not duplicate hardware polling: only the backend polls hardware.

### Why it looks remote-only

The local dashboard does not cross the AP. The remote dashboard does. The remote path adds Wi-Fi link stalls, client power-save behavior, and longer TCP round trips; the backend's serialized fan-out turns any one of those into a global delay. The frontend then reports that delay as a hardware failure.

---

## 6. PROPOSED IMPLEMENTATION

**No implementation has started. The items below require explicit approval.**

### 6.1 Branch and change management

1. Create `feature/phase2-offline-reliability` from `c4b26eb` (the current Phase 1 HEAD). Do **not** touch `main` or `origin/main`.
2. Keep the branch history in small, reviewable commits:
   - `P2-1`: backend WebSocket hub, heartbeat, close semantics, regression tests.
   - `P2-2`: frontend link/device-state separation, silence watchdog, reconnect tests.
   - `P2-3`: AP power-save disable and boot/guard enforcement.
   - `P2-4`: offline map/weather fallback.
   - `P2-5`: diagnostics endpoint, stress harness, measured results, final report.
3. Back up the current `dist/` and the `aeris-hotspot` profile before deployment.
4. Do not change GPIO/I2C/SPI/LoRa/RF settings, auth tokens, firewall rules, or service users in this phase.

### 6.2 Backend: non-blocking WebSocket hub

Replace the current serialized `ConnectionManager` with a per-client queue + writer task design.

**Connection lifecycle**

- Each accepted, authenticated socket gets a `ClientSession` with:
  - a bounded `asyncio.Queue` (proposed 32 messages);
  - exactly one writer task that owns every `send_json()` call for that socket;
  - a monotonic `last_send_ok`, `last_pong`, and `closed` flag;
  - a queue-overflow/drop counter.
- `broadcast()` becomes non-blocking:
  - sanitize once with `_json_safe()`;
  - snapshot the session list;
  - `put_nowait()` into each queue;
  - never `await` a network send.
- If a queue is full:
  - drop a queued `thermal_frame` for that client (latest frame wins);
  - for a critical `system`/`telemetry`/`detection` message, mark the client slow and schedule an idempotent close with code `1013` (Try Again Later);
  - never let one client's backlog affect another client.
- The writer task:
  - takes one message at a time;
  - `await asyncio.wait_for(send_json(...), timeout=WS_SEND_TIMEOUT_S)`;
  - on timeout/exception, calls `close()` exactly once, removes the session, logs the reason, and exits;
  - guarantees no two coroutines write the same socket concurrently.

**Close semantics**

- `close()` must:
  - be idempotent;
  - remove the session from the registry;
  - call `await websocket.close(code=...)` (with a short bounded wait);
  - cancel/join the writer task safely (not self-cancel from inside the writer).
- The endpoint's `finally` calls the same `close()` so every exit path (disconnect, auth failure, error, timeout) closes the socket.
- Dropped clients will now trigger the browser's `onclose` and the existing reconnect schedule.

**Heartbeat and watchdog**

- The server enqueues an application-level `{"type":"ping","ts":...}` every **10 s** to each authenticated session.
- The frontend replies with `{"type":"pong","ts":...}`.
- The server treats any inbound frame as activity; it does not force-close old clients that omit a pong. This preserves compatibility with already-open tabs during rollout.
- The frontend has its own watchdog: if no message arrives for **20 s**, or a connection stays `CONNECTING` for **10 s**, it closes the socket and reconnects.
- The server's send timeout/queue overflow remains the authoritative dead-client detector; the heartbeat gives the browser a fast, deterministic way to detect a silent server.

**Diagnostics for measured evidence**

Add a protected, LAN-only `GET /api/diagnostics` (operator/viewer read access) returning:

```json
{
  "active_clients": 0,
  "accepted_total": 0,
  "closed_total": 0,
  "dropped_slow_total": 0,
  "heartbeat_timeouts_total": 0,
  "messages_sent_total": 0,
  "queue_depth_max": 0,
  "broadcast_calls_total": 0,
  "broadcast_enqueue_ms_max": 0,
  "last_error": null,
  "uptime_s": 0
}
```

No token, hardware secret, or client IP is exposed. This endpoint is for the stress test evidence and for the final report.

**Files**

- `ground-station/backend/main.py`
- `ground-station/backend/tests/test_ws_manager.py`
- `ground-station/backend/tests/fakes.py`
- new `ground-station/backend/tests/test_ws_hub.py` (slow client, close, queue, heartbeat, concurrency)
- possibly `ground-station/backend/tests/run_tests.py` only if new tests need registration.

The existing 48-test suite must stay green. New tests must not open real hardware; the existing `_bootstrap.py` already disables the thermal thread and uses a test token.

### 6.3 Frontend: separate link state from device state

Do not force device indicators to `'offline'` when the link is stale.

**State model**

- `lastMessageAt`: last WebSocket message of any kind (including heartbeat).
- `hardwareUpdatedAt`: last `type: "system"` hardware report.
- `linkState`: derived from `lastMessageAt` (live ≤5 s, stale ≤15 s, offline >15 s).
- `hardwareFresh`: derived from `hardwareUpdatedAt` (≤15 s).

**Device rendering**

- Use the backend's measured `devices` map as the source of truth.
- If a device's last measured state is `online` but the report is older than 15 s, show **`stale`** (amber) plus the age, not `offline` (red).
- If the backend measured `offline`, keep `offline`.
- If no report has ever arrived, show `unknown`.
- Never show a green "CONNECTED" for stale data; never show a red "DISCONNECTED" just because the browser link is down.

**UI banners**

- Link live + hardware fresh: no banner.
- Link live + hardware stale: amber `LIVE LINK ACTIVE — no fresh hardware report (Ns); showing last measured device state`.
- Link down: amber/red `LIVE LINK OFFLINE — last known device state from Ns ago; reconnecting`.
- Auth required/rejected: existing behavior preserved.

**Watchdog and reconnect**

- Add the 20 s silence watchdog and 10 s connect timeout to `useWebSocket.ts`.
- Reset the reconnect attempt counter only after `auth_ok`.
- On tab visibility change, immediately re-check the socket and reconnect if needed.
- Preserve the existing auth handshake and close-code handling (4401, 1008).
- The token remains in `localStorage` and in the first auth frame; no URL token.

**REST fallback**

- Keep the first-load `/api/hardware_status` fallback, but treat a 401/403 as "not loaded" rather than a hardware state.
- Do not poll `/api/hardware_status` repeatedly; the WebSocket remains the single live source.

**Files**

- `ground-station/frontend/src/hooks/useWebSocket.ts`
- `ground-station/frontend/src/hooks/useHardwareStatus.ts`
- `ground-station/frontend/src/lib/hardwareStatus.ts`
- `ground-station/frontend/src/stores/appStore.ts`
- `ground-station/frontend/src/App.tsx`
- `ground-station/frontend/src/components/Sidebar.tsx`
- `ground-station/frontend/src/components/HardwareStatus.tsx`
- `ground-station/frontend/src/components/RpiSystemHealth.tsx`
- `ground-station/frontend/src/components/CommLinkHealth.tsx`
- `ground-station/frontend/src/components/LiveFeed.tsx`
- `ground-station/frontend/src/components/ThermalAnalytics.tsx`

### 6.4 AP: disable Wi-Fi power save persistently

**Configuration change (requires approval):**

```bash
# Persistent NetworkManager setting for the AERIS AP profile.
nmcli connection modify aeris-hotspot 802-11-wireless.powersave 2

# Immediate runtime enforcement.
iw dev wlan0 set power_save off

# Re-apply the profile so the setting is actually used.
nmcli connection up aeris-hotspot
```

`802-11-wireless.powersave = 2` means "disable". The current value is `0 (default)`.

**Code changes:**

- `aeris.sh`:
  - when creating `aeris-hotspot`, add `wifi.powersave 2`;
  - for an existing profile, modify `802-11-wireless.powersave 2`;
  - after activation, verify `iw dev "$WIFI_IFACE" get power_save` reports `off`; warn loudly if not.
- `setup_aeris.sh`: apply the same setting on both the create and existing-profile paths.
- `ground-station/hotspot_guard.sh`:
  - after confirming the AP is active, read `iw dev "$WIFI_IFACE" get power_save`;
  - if it is `on`, run `iw dev "$WIFI_IFACE" set power_save off` and log the transition once;
  - do not log on every 5 s poll when it is already off;
  - do not fight NetworkManager; the profile setting is the primary mechanism, the guard is the runtime backstop.
- `ground-station/boot_check.sh`:
  - add a check that `802-11-wireless.powersave` is `2 (disable)` and runtime power save is `off`;
  - add a check that `192.168.4.1` is assigned and the AP is active;
  - keep the existing watchdog/unit checks.

**No changes to:**

- SSID `AERIS-GroundStation`
- channel 6 / band bg
- WPA2 passphrase or key management
- `192.168.4.1/24`
- dnsmasq lease time (3600 s is stable and not a cause)
- firewall, SSH, rpcbind, wayvnc

**Why this is safe:** disabling power save increases AP responsiveness at the cost of a small amount of idle power. The Pi is mains-powered in this deployment. It does not change RF frequency, transmit power, channel, or LoRa settings.

### 6.5 Offline-first map and weather

**Weather**

- Replace the browser's direct Open-Meteo call with `GET /api/weather` (local backend cache).
- The backend remains the only component that may attempt an external weather update, in a background thread with a short timeout, and only when internet is available.
- If no fresh external data exists, show `Unavailable (offline)` and the cached age; never show stale data as live.
- No external DNS request is required for the dashboard to load or for telemetry.

**Map**

- Add an explicit `offline` map type that renders a local coordinate grid with the mission area, UAV marker, detections, and geofence, using the already-bundled Leaflet library.
- Default to `offline` in production (`http://192.168.4.1/`); allow `streets`/`satellite` only as an explicit operator choice when internet is available.
- If an online tile request fails, fall back to the local grid and show `Map tiles unavailable offline`.
- The map panel must never block or delay telemetry rendering.

**Files**

- `ground-station/frontend/src/hooks/useLiveWeather.ts`
- `ground-station/frontend/src/components/MissionMap.tsx`
- `ground-station/frontend/src/pages/MapPage.tsx`
- `ground-station/backend/main.py` (weather refresh worker, only if approved)
- `ground-station/backend/weather_cache.py` (no schema change)

No new runtime package is required. The backend already has `weather_cache.py`; the test harness can use the already-installed `websockets` 12.0 client library.

### 6.6 Stress and acceptance harness

Create a repeatable harness (stdlib + already-installed `websockets`) that records:

- WebSocket disconnect count
- reconnect count
- API response times (`/health`, `/api/hardware_status` with test token)
- telemetry delivery rate (messages/s and expected vs received)
- sensor timeout count and capture reinit count
- backend restart count / PID changes
- CPU, memory, temperature
- actual hardware state (from the API, not inferred)
- slow-client drops and queue depth

**Test layers**

1. **Unit / regression** — existing 48 tests plus new W1–W12-style tests:
   - 8 stalled clients do not delay healthy clients by more than 100 ms;
   - a dropped client receives `close()` and is removed;
   - no concurrent `send_json()` on one socket;
   - queue overflow drops thermal frames first, never blocks the broadcaster;
   - heartbeat is emitted;
   - `_json_safe` behavior preserved.
2. **Synthetic network** — a test-only server on a non-production port with `AERIS_SKIP_LORA_RX=1`, test token, fake/disabled hardware; 1, 2, 3, 6, 10 clients; inject a stalled client; 30 min.
3. **Browser / device** — 1 laptop, 2 laptops, laptop + phone, 3+ devices; refresh loops; tab hidden/return; Wi-Fi off/on; verify reconnect and no false device OFFLINE.
4. **Two-hour continuous run** — all available real devices connected to `AERIS-GroundStation`, dashboard open, telemetry sustained; record the full metric set.
5. **Offline test** — disconnect WAN/Ethernet (with approval) and verify the dashboard loads, auth works, telemetry continues, no external DNS is required. Do not disconnect hardware.

**Acceptance gates**

- No backend restart during the 2 h run (`NRestarts=0`, PID unchanged).
- Hardware acquisition continues through every client disconnect; no thermal/LoRa reinit caused by client churn.
- No unexplained device ONLINE/OFFLINE flapping.
- Healthy clients receive each system report within 1 s of the broadcast, even with a stalled client present.
- A dropped/slow client is closed and reconnects automatically within the configured backoff.
- Dashboard reconnects after Wi-Fi loss and after tab idle/return.
- All app-shell assets load with WAN disconnected; no CDN/cloud auth dependency.
- `iw dev wlan0 get power_save` remains `off` after reboot and after guard cycles.

**No zero-disconnection claim will be made.** The final report will state the measured disconnect/reconnect counts and any remaining unexplained events.

---

## 7. TEST PLAN AND EXPECTED RESULTS

| Test | Method | Pass criterion |
|---|---|---|
| 1 client, 30 min | synthetic + real browser | continuous system reports; no UI flapping |
| 2 clients, 30 min | two browsers/devices | both receive same data; no duplicate hardware polling |
| 3+ clients, 30 min | laptop + phone + tablet | all clients live; backend CPU stable |
| Slow client | inject a non-reading client | healthy clients unaffected; slow client closed |
| Browser refresh ×10 | reload dashboard repeatedly | reconnect within backoff; no sensor restart |
| Wi-Fi disconnect/reconnect | disable/enable client Wi-Fi | dashboard reconnects; device states remain last measured/stale, not false offline |
| Tab idle/return | hide tab 5–10 min, return | watchdog or visibility check reconnects |
| 2 h continuous | all available devices | no restart; no unexplained flapping; metrics recorded |
| WAN offline | disconnect Ethernet with approval | dashboard, auth, telemetry, local assets work |
| Reboot | approved reboot | AP power save off; backend/hotspot/boot checks pass |

Required user participation: the 2-hour multi-device and real Wi-Fi disconnect tests need the user's laptop/phone/tablet connected to `AERIS-GroundStation`. The laptop was disconnected at 03:12:58 and is not currently associated, so only loopback/synthetic tests can run without user devices.

---

## 8. RISKS AND MITIGATIONS

| Risk | Mitigation |
|---|---|
| Queue overflow drops telemetry for a slow client | Thermal frames are coalesced; system messages are preserved; the client is closed and reconnects cleanly. |
| Heartbeat changes break already-open tabs | Server does not require a pong; the frontend watchdog is the primary detector. After deployment, clients should reload once. |
| Stale device state is mistaken for live | Explicit `stale` state, amber styling, age label, and banner; never green `CONNECTED` for stale data. |
| AP power-save change causes a brief AP interruption | Apply the NM profile change and `iw` command in the approved maintenance window; verify the AP comes back; guard restores it. |
| `nmcli` profile value is ignored by the driver | Guard re-asserts `iw ... set power_save off` and boot check verifies both settings. |
| Frontend rebuild breaks offline assets | Back up current `dist/`; build and test with WAN disconnected before deploying. |
| New diagnostics endpoint leaks information | Protected by existing token auth and LAN-only middleware; no secrets or IPs in the response. |
| High local load masks or worsens timing | Record load/CPU/temp during the 2 h run; do not claim improvement without measured data. |
| Security regressions | No auth/CORS/firewall changes in this phase; existing Phase 1 items remain open and documented. |

---

## 9. ROLLBACK

### Before deployment

- Record the exact `aeris-hotspot` profile (`nmcli -f all con show aeris-hotspot > /tmp/...`), `iw dev wlan0 get power_save`, `ip addr`, and `systemctl show aeris-backend`.
- Back up `ground-station/frontend/dist/` with `cp -a`.
- Create the Phase 2 branch from `c4b26eb`; leave `feature/phase1-reliability` untouched.
- Commit each stage; tag the pre-deployment commit.

### Code rollback

```bash
cd /home/piaeris/aeris-project
git switch feature/phase1-reliability
# restore the pre-Phase-2 dist backup to ground-station/frontend/dist/
sudo systemctl restart aeris-backend   # requires approval
```

Do not reset or delete `main`. The Phase 2 branch remains available for inspection.

### AP rollback

```bash
nmcli connection modify aeris-hotspot 802-11-wireless.powersave 0
iw dev wlan0 set power_save on
nmcli connection up aeris-hotspot
```

(The original profile was `0 (default)`; runtime was `on`.)

### Service rollback

No service-unit change is planned. If one becomes necessary, restore the copy in `/home/piaeris/aeris-backups/20261113-010000/20-system-config/` and run `systemctl daemon-reload`.

### Data rollback

No database schema or sensor-calibration change is planned. Detection data remains in the existing SQLite database.

---

## 10. APPROVAL REQUEST

**Approval is requested for the following, in this order:**

1. Create branch `feature/phase2-offline-reliability` from `c4b26eb` (no change to `main`/`origin/main`).
2. Implement the backend per-client queue/writer hub, close semantics, heartbeat, diagnostics, and regression tests.
3. Implement the frontend link-state/device-state separation, silence watchdog, and reconnect behavior.
4. Apply the AP power-save disable persistently (`802-11-wireless.powersave 2`) and add guard/boot-check enforcement.
5. Implement the offline map/weather fallback.
6. Rebuild `ground-station/frontend/dist/` and deploy the new backend/frontend, including one `aeris-backend` restart (brief dashboard disconnect).
7. Run the synthetic tests, then the real multi-device 2-hour test with the user's laptop/phone/tablet connected, and produce `AERIS-PHASE2-OFFLINE-RELIABILITY-REPORT.md` with measured results.

**Not included without separate approval:**

- CORS restriction, SSH/rpcbind/wayvnc changes, firewall changes.
- Any GPIO/I2C/SPI/LoRa/RF configuration change.
- Any LoRa transmission.
- Any hardware disconnect or reboot.
- Any change to authentication or tokens.

**Current state:** diagnosis only; no implementation, deployment, service restart, network change, or hardware change has been performed.

---

## 11. APPENDIX — KEY FILE REFERENCES

| File | Relevant content |
|---|---|
| `ground-station/backend/main.py` | `WS_SEND_TIMEOUT_S` (line 247), `ConnectionManager` (line 270), `_drop()` (line 295), `broadcast()` (line 303), WebSocket endpoint (line 369), `_system_broadcaster` (line 1145, 2 s cadence), thermal broadcast (line 1848) |
| `ground-station/backend/tests/test_ws_manager.py` | Existing W1–W5 fan-out tests |
| `ground-station/backend/tests/fakes.py` | `FakeWebSocket` with `stall`/`fail`/`disconnect` |
| `ground-station/backend/tests/_bootstrap.py` | Test token, temp DB, hardware disarming |
| `ground-station/frontend/src/hooks/useWebSocket.ts` | Auth handshake, reconnect delays, no silence watchdog |
| `ground-station/frontend/src/hooks/useHardwareStatus.ts` | `linkFresh` forces all devices `'offline'` (lines 55–74) |
| `ground-station/frontend/src/lib/hardwareStatus.ts` | `REPORT_STALE_MS = 15_000`, `FRAME_STALE_MS = 5_000` |
| `ground-station/frontend/src/App.tsx` | Link banner only when `!wsConnected` |
| `ground-station/frontend/src/components/HardwareStatus.tsx` | Device labels and REST fallback |
| `ground-station/frontend/src/components/Sidebar.tsx` | `!hw.linkFresh` → `unknown` |
| `aeris.sh` | Hotspot creation at lines 1208–1227; no power-save disable |
| `setup_aeris.sh` | Alternate hotspot creation at lines 366–395; no power-save disable |
| `ground-station/hotspot_guard.sh` | AP liveness loop; no power-save enforcement |
| `ground-station/boot_check.sh` | Boot verification; no power-save check |
| `ground-station/frontend/src/hooks/useLiveWeather.ts` | Direct external Open-Meteo request |
| `ground-station/frontend/src/components/MissionMap.tsx` | External OSM/Esri tile layers |
| `ground-station/backend/weather_cache.py` | Existing offline-aware weather cache |
| `AERIS-FINAL-PHASE1-ACCEPTANCE-REPORT.md` | Phase 1 baseline and open security items |
| `/home/piaeris/aeris-backups/20261113-010000/RESTORE.md` | Verified rollback procedure |
