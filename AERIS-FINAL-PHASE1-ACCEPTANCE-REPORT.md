# AERIS — FINAL PHASE 1 ACCEPTANCE REPORT

**System:** AERIS Ground Station · Raspberry Pi 4 Model B Rev 1.2 · Debian GNU/Linux 13 (Trixie) · kernel 6.18.60+rpt-rpi-v8
**Branch:** `feature/phase1-reliability` @ `d00332a`
**Deployed backend:** active, PID **55638**, since 01:58:32 PST, `NRestarts 0`
**Acceptance date:** 2026-11-13 PST

**Phase 1 verdict: COMPLETE for the software scope — with two findings explicitly deferred to later phases (D-05, D-06) and one observability item (D-07) awaiting a deployment authorisation. No unresolved Critical or High-severity software issue remains.**

---

## 1. EXECUTIVE SUMMARY

Phase 1 closed out as follows:

| Finding | Severity | Outcome |
|---|---|---|
| **D-01** — pySX127x `set_sync_word()` described as a "no-op stub" | Medium (documentation) | **FIXED** — comment-only correction in 3 files; verified against the installed library; `SYNC_WORD = 0x12` and every radio setting unchanged |
| **D-02** — documented `unittest discover` invocation broken *and* bootstrap-skipping (would have touched the real token file and the real I2C bus) | Low (test infrastructure) | **FIXED** — bootstrap moved to `tests/_bootstrap.py`; all three invocations work, 48/48 tests, zero hardware access |
| **D-03** — network exposure (API on the wired LAN, no host firewall, SSH/rpcbind/VNC open to hotspot clients) | Medium (configuration, pre-existing) | **INVESTIGATED + documented** — no change applied (would alter network config) |
| **D-04** — permissive CORS (`*` + `allow_credentials=True`) | Medium (blocker for the future cookie auth, not for the current token auth) | **INVESTIGATED + documented** — exact effective behaviour measured; remediation specified; **not deployed** |
| **D-05** — dead `lora_rx` WebSocket branch in the frontend | Low | **DOCUMENTED** — no functional loss (the data arrives via the `system` broadcast); deferred cleanup |
| **D-06** — MLX90640 delivers 1.96 fps although the register says "4 Hz" | Medium (hardware characteristic) | **DOCUMENTED** — register semantics explained; the fix changes the capture cadence and belongs to Phase 4 |
| **D-07** — thermal/LoRa recovery events not journalled | Low (observability) | **DOCUMENTED** — requires a code change *and* a backend restart to deploy; awaiting authorisation |

**No service was restarted for this cleanup.** The D-01/D-02 changes are comment and test-infrastructure only, so the deployed backend (PID 55638) required no restart and has now run continuously since 01:58:32.

---

## 2. EVERY FINDING AND ITS RESOLUTION

### D-01 · Incorrect pySX127x documentation — **RESOLVED (FIXED)**

**Finding.** Three files asserted that pySX127x implements `set_sync_word()` as a *no-op stub* that *"never touches the register"*.

**Investigation (the installed library, not a guess).**
`drone-onboard/raspberry-pi-4b/venv/lib/python3.13/site-packages/SX127x/LoRa.py`:
```python
def setter(register_address):                      # lines 60-72
    def decorator(func):
        def wrapper(self, val):
            return self.spi.xfer([register_address | 0x80, func(self, val)])[1]
        return wrapper
    return decorator
...
@setter(REG.LORA.SYNC_WORD)                        # line 700-702
def set_sync_word(self, sync_word):
    return sync_word
```
The decorated body only *transforms* the value; the decorator's wrapper performs the **actual register write**. `REG.LORA.SYNC_WORD = 0x39`.

**Proof executed (fake SPI, no hardware):** `set_sync_word(0x12)` → SPI write `[0xb9, 0x12]` = register `0x39 | 0x80` (write flag) with value `0x12`. → **It DOES write Reg 0x39.**

**Fix applied (comments only).**
| File | Correction |
|---|---|
| `drone-onboard/raspberry-pi-4b/lora_protocol.py` | now states that pySX127x writes Reg 0x39 through its `@setter(REG.LORA.SYNC_WORD)` decorator, that the drone sets the word **explicitly**, and that it writes the same value the SX1276/78 would use by default (0x12) |
| `drone-onboard/raspberry-pi-4b/sender.py` | same correction above the `set_sync_word(lora_protocol.SYNC_WORD)` call; states that the call does write the register, with the same effective value, so the radio configuration is unchanged |
| `ground-station/backend/lora_rx_service.py` | states that the drone sets the word from the same shared constant and that the ground must program the same value |

**Preserved (verified):** `SYNC_WORD = 0x12` · 433 MHz · BW 500 kHz · SF6 · CR 4/5 · preamble 6 · CRC on · explicit header · packet format (`MAGIC 0xAE52`, version 1, 11-byte header, CRC16) · every GPIO/SPI/I2C setting · **the drone radio's effective Reg 0x39 value is still 0x12** — identical to before, only now explicit instead of implicit.

**Verification.** `git diff -U0` on the three files shows **zero non-comment lines changed** → no runtime behaviour change → **no backend restart required**.

**Regression tests.** `test_lora_protocol.py` 7/7 pass (L1–L5 + the preserved-modulation assertions); the drone `sender.py` still imports and uses the shared constant (L5).

---

### D-02 · Test runner compatibility — **RESOLVED (FIXED)**

**Finding.** The documented command `python3 -m unittest discover -s tests -v` failed with `ModuleNotFoundError: No module named 'fakes'`. Worse: in that invocation `unittest` uses `tests/` as the *top-level* directory, so `tests/__init__.py` is **never imported** → **the bootstrap never ran** → the suite would have read the real `.aeris_auth_token` and started the real thermal capture thread (opening `/dev/i2c-1`).

**Fix.** The bootstrap moved into its own self-contained module `ground-station/backend/tests/_bootstrap.py` (own `sys.path` setup, test tokens, SQLite redirect, hardware guards). Every test module now begins with `import _bootstrap`, `tests/__init__.py` is a thin shim, and the sibling imports changed from `from tests import …` to `from _bootstrap import …`.

**All three supported invocations, each executed:**

| Command | Result |
|---|---|
| `python3 tests/run_tests.py` | **48 tests, 0 failures, 0 errors, 0 skipped — OK** |
| `python3 -m unittest discover -s tests -v` *(the required command)* | **48 tests, 0 failures, 0 errors, 0 skipped — OK** |
| `python3 -m unittest discover -s tests -t . -v` | **48 tests, 0 failures, 0 errors, 0 skipped — OK** |

**Hardware-safety proof for each invocation** (`/proc/<pid>/fd` sampled during the run):

| Invocation | `/dev/i2c-*` or `/dev/spidev*` opened |
|---|---|
| `tests/run_tests.py` | **none** |
| `unittest discover -s tests` | **none** |
| `unittest discover -s tests -t .` | **none** |

The production token file mtime is unchanged (`2026-11-11 22:31`), and it is root-owned 0600 — an unprivileged test process cannot open it at all.

**No packages installed.** The suite remains standard-library only.

---

### D-03 · Network exposure — **INVESTIGATED & DOCUMENTED (no change applied)**

**Listening sockets (measured, read-only):**

| Port | Service | Bind | Reachable via `192.168.4.1` (hotspot) | Reachable via `192.168.1.13` (wired) | Authentication |
|---|---|---|---|---|---|
| **22/tcp** | `sshd` | `0.0.0.0` + `[::]` | **OPEN** | **OPEN** | Debian 13 defaults: `PasswordAuthentication yes`, `PubkeyAuthentication yes`, `PermitRootLogin prohibit-password` — **no key-only restriction** |
| **80/tcp** | AERIS backend (uvicorn) | `0.0.0.0` | **OPEN** | **OPEN** | token (operator) / viewer for reads |
| **111/tcp+udp** | `rpcbind` (`-w`, warm start → the port stays bound) | `0.0.0.0` + `[::]` | **OPEN** | **OPEN** | none |
| **5900/tcp** | `wayvnc` | `*` (all interfaces, IPv6 too) | **OPEN** | **OPEN** | `enable_auth=true` + `enable_pam=true` (PAM-backed) |
| 53/tcp+udp | `dnsmasq` (hotspot DHCP/DNS) | `192.168.4.1` only | OPEN | closed | — (intended) |
| 67/udp | `dnsmasq` DHCP server | `0.0.0.0` | — | — | (intended) |
| 5353/udp | avahi mDNS | `0.0.0.0` | — | — | (intended) |

**Firewall posture.** `nftables` contains **only** NetworkManager's `nm-shared-wlan0` table (a NAT `postrouting` chain and a `filter_forward` chain). **There is no `input` chain at all** → the host's INPUT path is unfiltered. `iptables` and `ufw` are not installed.

**What the hotspot isolation does achieve (verified from the ruleset):**
```
ip saddr 192.168.4.0/24 iifname "wlan0" accept
iifname "wlan0" oifname "wlan0" accept
iifname "wlan0" reject
oifname "wlan0" reject
```
→ hotspot clients **cannot** route to the wired LAN or the internet, and cannot reach each other through the router. That part is sound.

**Risks.**

| # | Risk | Severity | Condition |
|---|---|---|---|
| R1 | **SSH reachable by any hotspot client with the PSK** (password auth enabled) | **High** | anyone who joins `AERIS-GroundStation` with the shared passphrase gets a shell on the drone's computer |
| R2 | **wayvnc (5900) reachable from the hotspot** — a full desktop session | **High** | same PSK ⇒ graphical control of the machine |
| R3 | **rpcbind (111) reachable** — unnecessary attack surface, no function needed | Medium | same PSK |
| R4 | **The AERIS API reachable from the wired LAN** (192.168.1.0/24 is inside the middleware's `_LAN_NETWORKS`) | Medium | any host on the home LAN with the operator token can read/write the ground station |
| R5 | The backend binds `0.0.0.0`, so it would also answer on any future interface | Low–Medium | mitigated once the bind address is fixed |
| R6 | Home-router UPnP/NAT-PMP could publish port 80 to the internet | **UNVERIFIED** | the router was not inspected (out of scope) |

**Minimal remediation plan — NOT applied (requires approval).**

1. *Host firewall (highest value, 1 rule set):* add an nftables `input` chain with policy drop, accepting `lo`, the established/related traffic, `80/tcp` (and `443/tcp` when TLS is added), and the hotspot DHCP/DNS replies — nothing else. This closes R1, R2, R3 and R4 in one step and is reversible by removing one file.
   *nftables alternative, same effect:* `nft add table inet aeris-filter` + an `input` chain, drop policy, the accept rules above.
2. *Bind the backend to the hotspot only:* `uvicorn.run(app, host="192.168.4.1", …)` plus `127.0.0.1` for the local health checks — closes R4/R5 without touching any firewall.
3. *Disable what is not needed on a flight build:* `rpcbind` (R3) and `wayvnc` (R2) via `systemctl disable --now` — they exist for the desktop session, not for the drone.
4. *Harden SSH (R1):* `PasswordAuthentication no` + key-only, or at minimum an `AllowUsers`/`AllowGroups` restriction — needs a verified key path first, otherwise it locks the operator out.
5. *Router:* confirm no UPnP mapping publishes port 80 (R6).

**Order of application:** step 2 and 3 first (smallest blast radius), then step 1, then step 4 with a key verified beforehand. Each step is independently revertible.

---

### D-04 · CORS — **INVESTIGATED & DOCUMENTED (no change applied)**

**Current configuration** (`ground-station/backend/main.py:97-103`):
```python
app.add_middleware(CORSMiddleware,
                   allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
```

| Setting | Current value |
|---|---|
| Allowed origins | **`*`** (any origin) |
| Credentials | **allowed** (`Access-Control-Allow-Credentials: true`) |
| Allowed methods | **any** (`*`) |
| Allowed headers | **any** (`*`) |
| Exposed headers | none |
| Max age | default (600 s) |

**Effective behaviour, measured on the wire.** The middleware order matters: `AERISAuthMiddleware` was added **last**, so it is the **outermost** layer and answers *before* CORS ever runs.

| Case | Result |
|---|---|
| (a) Public endpoint (`/health`) + foreign `Origin` | 200 **with** `access-control-allow-origin: *` and `access-control-allow-credentials: true` |
| (b) Protected endpoint, no token, foreign `Origin` | **401, no CORS headers** — the auth layer short-circuits, the browser blocks the read |
| (c) Protected endpoint **with** the token, foreign `Origin` | 200 **with** `access-control-allow-origin: *` + `allow-credentials: true` |
| (d) CORS preflight (`OPTIONS`, no credentials by design) | **401** — preflights can never carry the bearer token, so cross-origin API calls fail outright today |

**Conclusion.** With the current *header* token the wildcard is **not** an exploitable data leak: a cross-origin browser request carries no token → 401 → and even a request that does carry one exposes only what the caller already possesses. The real exposure today is limited to the public surfaces (`/`, `/health`, `/assets/*`) — low sensitivity.

**It becomes a genuine vulnerability the moment the dashboard moves to a cookie session** (the planned frictionless-access work): the browser would attach the cookie automatically on a cross-origin request, the auth layer would accept it, and CORS would then attach `*` + `allow-credentials: true` → **any web page the operator visits could read the operator's API responses** and, for non-GET methods, act with their session.

**Recommended secure configuration — NOT deployed.**
```python
# The dashboard is served by this same backend, so the production dashboard is
# SAME-ORIGIN and needs no CORS at all. Only the Vite dev server is
# cross-origin, and only for local development.
_AERIS_ALLOWED_ORIGINS = [
    "http://192.168.4.1",     # the hotspot address
    "http://aeris.local",     # mDNS name
    "http://127.0.0.1:80",    # local health checks
    "http://localhost:5173",  # Vite dev server (development only)
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_AERIS_ALLOWED_ORIGINS,   # explicit list, NOT "*"
    allow_credentials=True,                  # required for the cookie session
    allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
```
* **Compatibility:** the production dashboard (`http://192.168.4.1/`) and the dev server keep working; `Authorization` and `Content-Type` are the only headers the frontend actually sends.
* **Hard requirement:** this **must ship in the same change as the cookie session** — tightening CORS while the token is in `localStorage` is harmless but pointless; shipping cookies without it is the vulnerability.
* **Deployment:** needs a backend restart → requires authorisation.

---

### D-05 · Dead LoRa frontend branch — **DOCUMENTED (no change)**

* **Evidence:** `useWebSocket.ts:198` handles `data.type === 'lora_rx'`, but the backend emits **0** messages of that type (`grep -c '"type": "lora_rx"' main.py` → 0). The LoRa RX status travels **nested inside the `system` broadcast** (`main.py:1158` → `"lora_rx": …`) and is consumed by the `system` branch at `useWebSocket.ts:151`.
* **Impact:** **none** — the data reaches the store and the Comm Links panel through the `system` path. The `lora_rx` branch is simply unreachable.
* **Classification:** low-severity frontend dead code. **No code change is required for correctness**; removing the branch is optional cleanup that belongs with the next frontend change (it would also require a rebuild of `dist/`, so it is not done here).

### D-06 · MLX90640 capture rate vs configured register — **DOCUMENTED (no change)**

* **Evidence (live):** the backend's own selection log — `[Thermal] I2C burst 39 ms -> sensor refresh rate 4 Hz (frame ~77 ms)` — while `/api/thermal/status` reports **1.95–1.96 fps**; the control register `0x800D` was read directly during the audit and holds `3` ("4 Hz").
* **Root cause:** the MLX90640 asserts the **data-ready flag per subpage**; a complete 32×24 frame requires **two** subpages. The register value is therefore the *per-subpage* rate, so "4 Hz" yields **~2 full frames/s**.
* **Classification:** **hardware/driver characteristic, not a software defect.** The code's own comments ("4 Hz ⇒ 4 fps") are the inaccurate part.
* **Fix (Phase 4, not now):** allow `REFRESH_8_HZ` (register value 4) → ~3.9 full fps. The I2C budget allows it (77 ms/frame against a 250 ms period at 400 kHz). **Not applied here** because it changes the capture cadence and the CPU cost of the per-frame human detection, which is a Phase 4 decision.

### D-07 · Recovery events not journalled — **DOCUMENTED (code change pending authorisation)**

* **Evidence:** `_thermal_reinit()` contains **0** `print()`/`logger` calls — a thermal re-initialisation is visible only through `/api/thermal/status` (`capture_reinit_count`, `last_error`). `lora_rx_service.py` creates `logging.getLogger(__name__)` but never configures logging, so the root logger stays at WARNING and its `INFO` lines (e.g. `"LoRa RX: radio re-initialised successfully"`) are **dropped**; only the WARNING-level recovery lines reach the journal.
* **Impact:** an operator reading `journalctl -u aeris-backend` after an incident sees *no* record of a recovery — while the dashboard shows it. **Observability gap, not a functional fault.**
* **Smallest fix (needs a restart to deploy):** emit the thermal re-init outcome at WARNING (`print(..., file=sys.stderr)` or a logger call) and change the LoRa success line to WARNING; optionally configure the `aeris` logger at INFO in `main.py` instead of touching every call site.
* **Status:** **not applied** — it changes runtime logging and therefore requires a backend restart, which needs explicit authorisation. Classified as a **Low-severity observability item**, not a blocker.

---

## 3. FILES CHANGED IN THIS CLEANUP

| File | Change | Lines |
|---|---|---|
| `drone-onboard/raspberry-pi-4b/lora_protocol.py` | D-01 — corrected the module doc block | comment only (28 → 32 lines) |
| `drone-onboard/raspberry-pi-4b/sender.py` | D-01 — corrected the comment above `set_sync_word()` | comment only (+17 → +25) |
| `ground-station/backend/lora_rx_service.py` | D-01 — corrected the comment above `REG_WRITE_SYNC_WORD` | comment only |
| `ground-station/backend/tests/_bootstrap.py` | D-02 — **new**: the self-contained test bootstrap | +89 (new file) |
| `ground-station/backend/tests/__init__.py` | D-02 — thin shim: `sys.path` + `import _bootstrap` | 71 → 11 |
| `ground-station/backend/tests/test_auth.py` | D-02 — `import _bootstrap`; `from _bootstrap import …` | +2 / 2 import changes |
| `ground-station/backend/tests/test_lora_lifecycle.py` | D-02 — `import _bootstrap` | +2 |
| `ground-station/backend/tests/test_lora_protocol.py` | D-02 — `import _bootstrap`; `from _bootstrap import BACKEND_DIR` | +2 / 1 import change |
| `ground-station/backend/tests/test_thermal_capture.py` | D-02 — `import _bootstrap` | +2 |
| `ground-station/backend/tests/test_ws_manager.py` | D-02 — `import _bootstrap` | +2 |
| `AERIS-FULL-VERIFICATION-REPORT.md` | the verification deliverable | new file (387 lines) |
| `AERIS-FINAL-PHASE1-ACCEPTANCE-REPORT.md` | this document | new file |

**Not touched:** every frontend source file, every systemd unit, `/etc/aeris/aeris.env`, the Wi-Fi profile, the GPIO/SPI/I2C assignments, `/boot/firmware/config.txt`, the database, the token file, the backups.

---

## 4. COMMIT HASHES

| Commit | Content |
|---|---|
| `4524cd3` | upstream HEAD (`main`, `origin/main`) — **untouched** |
| `b60ccdf` | preserve the complete pre-Phase-1 implementation (recovery point) |
| `873bd33` | **Phase 1** — P1-1 … P1-5 |
| `0d2e549` | P1-1 follow-up — startup race + stale sync-word log |
| `d00332a` | **Phase 1 cleanup** — D-01 + D-02 (this task) |
| `c7b291a4` | dangling recovery commit (pre-Phase-1 worktree snapshot) — still reachable |

---

## 5. COMPLETE TEST RESULTS

### 5.1 Python

| Command | Result |
|---|---|
| `python3 tests/run_tests.py` | **48 run · 0 failures · 0 errors · 0 skipped · OK** |
| `python3 -m unittest discover -s tests -v` | **48 run · 0 failures · 0 errors · 0 skipped · OK** |
| `python3 -m unittest discover -s tests -t . -v` | **48 run · 0 failures · 0 errors · 0 skipped · OK** |

Per module: `test_thermal_capture` 12/12 · `test_ws_manager` 8/8 · `test_auth` 12/12 · `test_lora_protocol` 7/7 · `test_lora_lifecycle` 9/9.
**No hardware accessed in any invocation** (proved against `/proc/<pid>/fd`). **No package installed.**

### 5.2 Frontend

| Check | Result |
|---|---|
| `npx tsc --noEmit -p tsconfig.app.json` | **exit 0**, no diagnostics, no build artefacts written |
| `npx oxlint` | **exit 0** — 3 warnings, all pre-existing (`SettingsPage.tsx:27`, `ThermalCanvas.tsx:7,82`), **0 errors, 0 new warnings** |

### 5.3 Live system (deployed backend, PID 55638)

| Check | Result |
|---|---|
| Service | `active`, `NRestarts 0`, `Result success` |
| `/health` | `{"status":"ok","service":"aeris-backend"}` HTTP 200, 7 ms |
| Thermal | `ok=true`, `fps=1.95`, `frames=4263`, `capture_phase=running`, `capture_reinit_count=0`, `invalid_samples=0`, `stale=false`, `frame_age 0.00 s`; matrix 768 samples, **all finite** |
| LoRa status | `running=true`, `sync_word=0x12`, `rx_count=0`, `crc_fail=2`, `dio0_irq_count=2`, `rx_errors=0`, `reinit_count=0`, `last_error=null` |
| LoRa register read-back | `version=0x12`, `frf=0x6c4000` (→ 433.000000 MHz), `op_mode=0x8d`, `pa=0xcf` (PA_BOOST), `bw=500 kHz`, `sync_word=0x12` |
| WebSocket (60 s, 3 clients + 30 connect/disconnect cycles) | 3 healthy clients each received **identical 151 frames** (118 thermal / 29 system / 2 detection), **0 disconnects, 0 errors**; REST during the traffic: **117/117 → HTTP 200**, min 5.4 ms, median 11.0 ms |
| Authentication | `/` 200 anonymous · `/health` 200 anonymous · `/api/system` **401** anonymous / 200 operator · `/thermal_feed` **401** anonymous / 200 operator · `POST /api/telemetry` **401** anonymous |
| Journal | 0 tracebacks · 0 exceptions · 0 thermal read errors · 0 unexpected restarts |
| Git | branch `feature/phase1-reliability` @ `d00332a`; `main` = `origin/main` = `4524cd3`; **0 stash entries**; working tree **clean**; recovery commit `c7b291a4` reachable |
| Backups | `10-project-source.tar.gz` readable OK (25 190 850 B) · `11-project-FULL.tar.gz` readable OK (263 749 908 B) · **0 archives modified** · `RESTORE.md` present |

---

## 6. BEFORE / AFTER SYSTEM STATE

| Item | Phase 0 baseline | After Phase 1 | Now |
|---|---|---|---|
| Backend PID / restarts | 13439 | 55638, `NRestarts 0` | 55638, `NRestarts 0` (no restart for the cleanup) |
| Backend threads | 24 | 24 | 24 |
| Backend RSS | 190.0 MB | 202.7 MB, +0.012 %/3 min | 202.8 MB (flat) |
| CPU / temperature | 43.6 % · 72.5–75.5 °C | median 48.1 % · 74.5 °C | comparable, no throttling |
| Thermal FPS | 1.95 | 1.96 | 1.95–1.96 |
| Thermal capture phase | *field absent* | `running` | `running` |
| Thermal invalid samples | *field absent* | 0 | 0 |
| LoRa sync word | *field absent* (ground programmed **0x34**) | **`0x12`** | **`0x12`** |
| LoRa crc_fail / irq | 7 / 7 | 2 / 2 | 2 / 2 |
| LoRa recovery fields | *absent* | `reinit_count`, `last_reinit`, `last_error`, `reinit_pending` | present |
| `/thermal_feed` anonymous | **200** | **401** | **401** |
| `/api/*` anonymous | 401 | 401 | 401 |
| API latency (avg) | /health 27.1 ms · /api/system 31.7 | /health 9.6 · /api/system 15.9 | /health 7 · /api/system ~15 |
| WS old-style failures | 12 × `broadcast to a client failed` | 0 | 0 |
| Journal errors | 0 thermal errors | 0 | 0 |

---

## 7. REMAINING HARDWARE TESTS (require explicit authorisation)

| Test | Blocked by | Current substitute |
|---|---|---|
| **LoRa end-to-end reception** (`rx_count > 0`, `crc_fail = 0`) | RF transmission + a drone in range | both ends' configuration verified (all 10 parameters, sync word read back from the chip) |
| **MLX90640 unplug/replug recovery** | physical disconnection | fake-sensor tests T1–T6 (timeout, re-init, back-off, FD release, frame rejection, subsystem independence) |
| **LoRa RX recovery on the real radio** | forcing SPI errors on the live radio | fake-radio tests R1–R5 (stop/start semantics, idempotent start, recovery thread, counters) |
| **Kernel I2C adapter timeout** for a blocked transfer | holding SDA low | documented; reported distinctly as `capture_phase="stalled"` |
| **Live viewer-role behaviour** | configuring `AERIS_VIEWER_TOKEN` | suite proves read-200 / write-403 |
| **Wi-Fi link budget in flight** | the drone airborne | out of Phase 1 scope |

---

## 8. REMAINING SECURITY RISKS

| # | Risk | Severity | Status |
|---|---|---|---|
| S1 | **SSH (22) reachable by any hotspot client, password authentication enabled** | **High** | open — remediation in D-03 step 4 |
| S2 | **wayvnc (5900) reachable from the hotspot** (PAM-authenticated desktop) | **High** | open — D-03 step 3 |
| S3 | rpcbind (111) reachable, unnecessary | Medium | open — D-03 step 3 |
| S4 | The AERIS API reachable from the **wired** LAN | Medium | open — D-03 steps 1–2 |
| S5 | CORS `*` + `allow_credentials` | Medium | **dormant today**, becomes a live vulnerability **iff** cookie auth is introduced → must ship with the CORS fix (D-04) |
| S6 | Router UPnP/NAT-PMP might publish port 80 | **UNVERIFIED** | router not inspected (out of scope) |
| S7 | The operator token is a single static shared secret with no rotation | Low–Medium | accepted for a single-operator ground station; rotation is a later item |
| S8 | No host firewall at all | Medium | open — D-03 step 1 |

---

## 9. BACKUP & ROLLBACK VERIFICATION

| Artefact | State |
|---|---|
| `10-project-source.tar.gz` (25 190 850 B) | readable OK, **unmodified** (mtime 01:00) |
| `11-project-FULL.tar.gz` (263 749 908 B) | readable OK, **unmodified** (mtime 01:03) — includes `.git`, both venvs (the drone venv holds the compiled `spidev` .so that PyPI cannot reproduce), `node_modules` and the token file |
| `04-untracked-files.tar.gz` | readable OK |
| `20-system-config/` | `/etc/aeris/`, all six systemd units, logrotate, journald drop-in, boot config, token (0600) |
| `RESTORE.md` | present and accurate |
| Recovery commit `c7b291a4` | reachable (`git cat-file -t` → `commit`) |
| Archives modified after the backup | **0** |

**Rollback paths (all verified, none destructive):**
* *This cleanup only:* `git revert d00332a` (or `git checkout 0d2e549 -- <the 9 files>`); **no backend restart needed** — the deployed code is unaffected.
* *All of Phase 1:* `git revert 0d2e549 873bd33` + `sudo systemctl restart aeris-backend`.
* *Pre-Phase-1 state:* `tar -xzf 10-project-source.tar.gz -C /home/piaeris/aeris-project` (system config from `20-system-config/`, steps in `RESTORE.md`).
* *The git-side recovery:* `git stash apply c7b291a419a5a50b89d67bb3fbee8786faab18aa`.

---

## 10. EXPLICIT LIST OF THINGS STILL REQUIRING AUTHORISATION

1. **`sudo systemctl restart aeris-backend`** — needed only to deploy the D-07 observability change (and, later, the D-04 CORS change). **Not required for D-01/D-02.**
2. **D-07 code change + restart** — journal the thermal re-init outcome and the LoRa recovery success.
3. **D-04 CORS tightening + restart** — must ship together with the cookie-session change; **must not** ship without it.
4. **D-03 remediation** — host firewall, bind address, disabling rpcbind/wayvnc, SSH hardening. Each step touches network configuration and needs approval.
5. **LoRa RF transmission** — the end-to-end link test.
6. **MLX90640 unplug/replug** — the physical fault-recovery test.
7. **Kernel I2C timeout measurement** — holding SDA low.
8. **`AERIS_VIEWER_TOKEN` configuration** — to exercise the viewer role live.
9. **Router UPnP inspection** — to close S6.
10. **Phase 2** — not started.

---

## 11. ACCEPTANCE STATEMENT

* All five Phase 1 tasks are implemented, deployed and verified (P1-1 … P1-5).
* Every planned automated test exists and passes: **48/48**, three invocation styles, zero hardware access.
* TypeScript and lint are clean; the frontend was not modified.
* The deployed backend (PID 55638) ran continuously through the deployment, the autonomous verification and this cleanup — **no restart was needed for the cleanup**.
* Backups, the recovery commit and the branch are intact; `main`/`origin/main` were never touched; no destructive Git command was used.
* No credential or secret was exposed in any report, log, response or commit.
* **No unresolved Critical or High-severity software issue remains.** The three open High items (S1, S2) and the remaining Medium items are **configuration/environment** risks of the deployed desktop-style installation, each with a specified, minimal, independently revertible remediation that awaits approval.

**Phase 1 is complete. Phase 2 has not been started and will not be without your approval.**
