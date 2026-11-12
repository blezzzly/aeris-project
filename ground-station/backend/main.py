from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.websockets import WebSocketState
from typing import List
import json
import asyncio
import math
import threading
import time
import sys
import io
import os
import secrets
import ipaddress
from collections import deque
sys.path.insert(0, os.path.dirname(__file__))
try:
    import database as _db
    _DB_AVAILABLE = True
except Exception as _db_err:
    _DB_AVAILABLE = False
    print(f"[DB] Database module unavailable: {_db_err}")

try:
    import sensors as _sensors
    _SENSORS_AVAILABLE = True
except Exception as _sens_err:
    _sensors = None
    _SENSORS_AVAILABLE = False
    print(f"[Sensors] Hardware sensor layer unavailable: {_sens_err}")

def _forward_lora_detection(packet):
    """Publish a real LoRa detection (drone telemetry) to the dashboard.

    The packet carries the drone's own GPS fix; it is forwarded only when a
    real position is present, so the map never shows a fabricated coordinate.
    """
    if not isinstance(packet, dict):
        return
    lat = packet.get("gps_lat")
    lng = packet.get("gps_lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return
    if lat == 0 and lng == 0:
        return
    event = {
        "type": "detection",
        "id": f"ALERT-LOKA-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().isoformat(),
        "confidence": packet.get("confidence") or 0,
        "lat": float(lat),
        "lng": float(lng),
        "altitude": packet.get("altitude"),
        "distance": packet.get("distance"),
        "thermal": {
            "min_c": packet.get("temp_min"),
            "max_c": packet.get("temp_max"),
            "hotspot": {
                "x": packet.get("hotspot_x"),
                "y": packet.get("hotspot_y"),
                "c": packet.get("hotspot_c"),
            },
        },
    }
    loop = globals().get("_main_loop")
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(event), loop)
    except Exception as exc:
        print(f"[LoRaRX] detection forward failed: {type(exc).__name__}: {exc}")


try:
    from lora_rx_service import LoRaRxService
    _lora_rx_svc = LoRaRxService(on_packet=_forward_lora_detection)
    _LORA_RX_AVAILABLE = True
except Exception as _lrx_err:
    _lora_rx_svc = None
    _LORA_RX_AVAILABLE = False
    print(f"[LoRaRX] Service unavailable: {_lrx_err}")

try:
    import weather_cache as _wc
    _WEATHER_AVAILABLE = True
except Exception as _w_err:
    _wc = None
    _WEATHER_AVAILABLE = False
    print(f"[Weather] Cache unavailable: {_w_err}")
from datetime import datetime
import random

app = FastAPI(title="AERIS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_TOKEN_FILE = os.environ.get("AERIS_TOKEN_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".aeris_auth_token"
)


def _load_or_create_operator_token() -> str:
    """No insecure default: use AERIS_AUTH_TOKEN, else a generated token persisted with mode 600."""
    env_token = (os.environ.get("AERIS_AUTH_TOKEN") or "").strip()
    if env_token:
        return env_token
    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        if stored:
            return stored
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        print(f"[AUTH] No AERIS_AUTH_TOKEN set - generated token saved to {_TOKEN_FILE} (mode 600).")
        print(f"[AUTH] Read it with: cat {_TOKEN_FILE}")
    except OSError as e:
        # Never print the secret itself - only the fact that it could not be stored.
        print(f"[AUTH] WARNING: could not persist the token to {_TOKEN_FILE} ({e}).")
        print("[AUTH] A temporary token was generated for this process only.")
    return token


_OPERATOR_TOKEN = _load_or_create_operator_token()
_VIEWER_TOKEN = (os.environ.get("AERIS_VIEWER_TOKEN") or "").strip()
_ALLOW_PUBLIC = (os.environ.get("AERIS_ALLOW_PUBLIC") or "").strip().lower() in ("1", "true", "yes")


def _role_for(token: str):
    """Map a presented token to a role: operator (full) or viewer (read-only)."""
    if not token:
        return None
    if token == _OPERATOR_TOKEN:
        return "operator"
    if _VIEWER_TOKEN and token == _VIEWER_TOKEN:
        return "viewer"
    return None


_LAN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_lan_client(host: str) -> bool:
    """Only private/loopback clients may talk to the ground station (LAN-only by default)."""
    if _ALLOW_PUBLIC:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _LAN_NETWORKS)


# Paths that require a credential. "/api/" covers the whole REST surface;
# "/thermal_feed" is the MJPEG stream of live thermal imagery and must not be
# readable by an unauthenticated client on the local network either.
_PROTECTED_PREFIXES = ("/api/", "/thermal_feed")


class AERISAuthMiddleware(BaseHTTPMiddleware):
    """Token auth for /api/* (header only - never in URLs) + LAN-only access control.

    Read requests accept operator or viewer tokens; write (control) requests require operator.
    """

    async def dispatch(self, request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if not _is_lan_client(client_host):
            return JSONResponse(status_code=403, content={"detail": "Forbidden: LAN access only"})
        if request.url.path.startswith(_PROTECTED_PREFIXES):
            token = ""
            auth_header = request.headers.get("authorization")
            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[len("bearer "):].strip()
            role = _role_for(token)
            if role is None:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            if request.method not in ("GET", "HEAD", "OPTIONS") and role != "operator":
                return JSONResponse(status_code=403, content={"detail": "Operator role required"})
            request.scope["aeris_role"] = role
        return await call_next(request)


app.add_middleware(AERISAuthMiddleware)

# Serve the built frontend (dist/) so the Ground Station can host the complete
# AERIS dashboard locally over the Wi-Fi hotspot (http://192.168.4.1).
import os as _os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_FRONTEND_DIST = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "..", "frontend", "dist")
)
if _os.path.isdir(_FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=_os.path.join(_FRONTEND_DIST, "assets")), name="assets")
    # Serve the bundled public assets (logo, placeholder imagery, icons) locally so the
    # dashboard works with no internet access.
    _DIST_IMAGE = _os.path.join(_FRONTEND_DIST, "image")
    if _os.path.isdir(_DIST_IMAGE):
        app.mount("/image", StaticFiles(directory=_DIST_IMAGE), name="image")
    for _static_name in ("favicon.svg", "icons.svg"):
        _static_path = _os.path.join(_FRONTEND_DIST, _static_name)
        if _os.path.exists(_static_path):
            def _make_static_handler(_p):
                async def _handler():
                    return FileResponse(_p, media_type="image/svg+xml")
                return _handler
            app.get(f"/{_static_name}", include_in_schema=False)(_make_static_handler(_static_path))

# ── WebSocket fan-out limits ─────────────────────────────────────────────────
#
# FastAPI serves every route AND every WebSocket on ONE asyncio event loop.
# Two consequences drive the code below:
#
#   * A client that stops reading (TCP zero-window, a sleeping laptop, a
#     Wi-Fi power-save stall) makes `await send_json()` block forever.
#     WS_SEND_TIMEOUT_S bounds that wait, so ONE stalled client can no
#     longer freeze the REST API, the thermal feed and every other
#     dashboard at the same time.
#   * The connection list is mutated by other tasks (connect/disconnect),
#     so broadcast() must iterate a SNAPSHOT. Mutating the list while it is
#     being iterated raises "RuntimeError: list changed size during
#     iteration", which aborted the whole broadcast and left the remaining
#     clients without data - the dashboard then showed every component as
#     disconnected until the next cycle.
WS_SEND_TIMEOUT_S = 2.0


def _json_safe(value):
    """Recursively replace non-finite floats with ``None``.

    Starlette's ``send_json()`` serialises with ``allow_nan=False``, so a
    single NaN coming from a sensor raises and kills the broadcast for
    EVERY client. A non-finite value is not a measurement, so it is
    transported as ``null`` (absent) instead of being silently coerced
    into a number.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class ConnectionManager:
    """Fan-out of live updates to the connected dashboards.

    Three properties are guaranteed:

      * one failing client never interrupts the others,
      * one stalled client never blocks the shared event loop,
      * a closed client is never left behind in the list.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        # Accept only if the caller has not already accepted the socket.
        if websocket.application_state != WebSocketState.CONNECTED:
            await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    def _drop(self, websocket: WebSocket, reason: str):
        """Remove a dead client from the fan-out list.

        A client that is left in the list is retried on EVERY broadcast
        (every 2 s) and produces a failure line each time - the connection
        leak that was visible in the journal as a repeating
        "broadcast to a client failed" message.
        """
        self.disconnect(websocket)
        print(f"[WS] client dropped: {reason}")

    async def broadcast(self, message: dict):
        # Sanitise once, outside the loop: a non-finite value must never
        # abort the delivery to the remaining clients.
        payload = _json_safe(message)
        # Iterate over a SNAPSHOT - disconnect() runs from another task and
        # mutates self.active_connections while we await.
        for connection in list(self.active_connections):
            try:
                await asyncio.wait_for(
                    connection.send_json(payload), timeout=WS_SEND_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                # The client stopped reading. Drop it rather than letting
                # it stall the event loop for every other client.
                self._drop(connection, f"send timeout after {WS_SEND_TIMEOUT_S}s")
            except Exception as exc:
                self._drop(connection, f"{type(exc).__name__}: {exc}")

manager = ConnectionManager()

# Real telemetry state (no simulated values). Fields are null until provided by real hardware sources.
# With only RPi4B + MLX90640 + LoRa SX1278, flight telemetry (battery/altitude/speed/heading/GPS)
# has no physical sensor source — reported as unavailable/disconnected unless a real source supplies them.
telemetry_data = {
    "altitude": None,       # AMSL meters — requires FC/baro/GPS (unavailable on current hardware)
    "speed": None,          # m/s — requires FC/GPS
    "heading": None,        # degrees — requires FC/magnetometer or GPS COG
    "battery": None,        # % — requires battery sensor/FC power module
    "battery_voltage": None,  # V — requires battery monitor (INA219/INA260)
    "lat": None,            # degrees — requires GPS module/FC
    "lng": None,            # degrees — requires GPS module/FC
    "connected": False,     # true only when a real telemetry producer is actively reporting
    "source": "none",       # e.g. "mavlink", "gps", "rpi4b-lora" (real sources only)
    "last_update": None,    # ISO timestamp of last real update
    "unavailable_reason": "No flight telemetry sensors (battery/altitude/speed/heading/GPS) on RPi4B + MLX90640 + LoRa SX1278",
}

# Real hardware sensor readings (filled by the sensor poller).
sensor_status = {
    "lora": {"available": False, "reason": "not probed yet"},
    "gps": {"available": False, "reason": "not probed yet"},
    "battery": {"available": False, "reason": "not probed yet"},
    "barometer": {"available": False, "reason": "not probed yet"},
    "compass": {"available": False, "reason": "not probed yet"},
}

# The LoRa radio identity as read from the hardware itself (None until probed).
_lora_hardware = {"available": False, "reason": "not probed yet"}

survivor_data = {
    "lat": 14.2515,
    "lng": 120.7310,
}

# WebSocket close codes.
#   1008            - policy violation: the presented token was rejected.
#   WS_CLOSE_AUTH_REQUIRED (4401) - no credential was presented at all. This is
#                     a *transient* state (the operator has not signed in on
#                     this device yet), so the client must wait for a sign-in
#                     instead of throwing its stored credential away.
WS_CLOSE_AUTH_REQUIRED = 4401


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # LAN-only: reject clients outside the private network.
    client_host = websocket.client.host if websocket.client else ""
    if not _is_lan_client(client_host):
        await websocket.close(code=1008, reason="forbidden")
        return

    await websocket.accept()
    try:
        # Auth handshake: the first frame must be {"type":"auth","token":"..."}.
        # The token is never placed in the URL, so it stays out of access logs.
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        try:
            handshake = json.loads(raw) or {}
        except json.JSONDecodeError:
            handshake = {}
        token = str(handshake.get("token") or "").strip()
        role = _role_for(token)
        print(f"[WS] auth handshake role={role} token_present={bool(token)}")
        if role is None:
            if token:
                # A credential was presented and it is not valid.
                await websocket.close(code=1008, reason="unauthorized")
            else:
                # No credential yet - the operator still has to sign in.
                await websocket.close(code=WS_CLOSE_AUTH_REQUIRED, reason="auth_required")
            return

        await manager.connect(websocket)
        # Tell the client the handshake succeeded and which role it got. The
        # client uses this to know that the live channel is really authorised.
        await websocket.send_json({"type": "auth_ok", "role": role})
        # Send current real telemetry state immediately on connect
        await websocket.send_json({"type": "telemetry", **telemetry_data})
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                if message.get("type") == "detection":
                    if role != "operator":
                        continue  # read-only viewers may not inject detections
                    await manager.broadcast({
                        "type": "detection",
                        "id": f"ALERT-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                        "timestamp": datetime.now().isoformat(),
                        "confidence": message.get("confidence", 0.92),
                        "lat": message.get("lat", survivor_data["lat"]),
                        "lng": message.get("lng", survivor_data["lng"]),
                        "altitude": message.get("altitude", telemetry_data.get("altitude")),
                        "distance": message.get("distance", 120),
                    })
                elif message.get("type") == "telemetry_update":
                    # Accept real telemetry from a hardware producer (e.g., onboard RPi/LoRa bridge).
                    # Only update fields explicitly provided; never fabricate missing values.
                    if role != "operator":
                        continue  # telemetry injection is an operator action
                    allowed = {"altitude", "speed", "heading", "battery", "lat", "lng", "source", "connected"}
                    update = {k: message[k] for k in allowed if k in message}
                    telemetry_data.update(update)
                    telemetry_data["last_update"] = datetime.now().isoformat()
                    if "connected" not in update:
                        telemetry_data["connected"] = True
                    await manager.broadcast({"type": "telemetry", **telemetry_data})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        # No auth frame arrived in time.
        try:
            await websocket.close(code=1008, reason="unauthorized")
        except Exception:
            pass
    except Exception as e:
        print(f"[WS] connection error: {type(e).__name__}: {e}")
        try:
            await websocket.close(code=1011, reason="internal error")
        except Exception:
            pass
    finally:
        # EVERY exit path removes the socket from the fan-out list. Before
        # this, a client that left through any path other than
        # WebSocketDisconnect stayed in the list for ever and was retried
        # (and logged as a failure) on every single broadcast.
        manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "aeris-backend"}

@app.get("/api/telemetry")
async def get_telemetry():
    return telemetry_data


@app.post("/api/telemetry")
async def update_telemetry(payload: dict):
    """Ingest real telemetry from a hardware producer. Never fabricates missing values."""
    allowed = {"altitude", "speed", "heading", "battery", "lat", "lng", "source", "connected"}
    update = {k: payload[k] for k in allowed if k in payload}
    telemetry_data.update(update)
    telemetry_data["last_update"] = datetime.now().isoformat()
    if "connected" not in update and update:
        telemetry_data["connected"] = True
    await manager.broadcast({"type": "telemetry", **telemetry_data})
    return {"status": "ok", "telemetry": telemetry_data}

@app.get("/api/detections")
async def get_detections():
    return []

@app.post("/api/detection")
async def add_detection(detection: dict):
    # Never broadcast a payload without usable coordinates: clients render lat/lng.
    def _num(value, fallback):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback

    await manager.broadcast({
        "type": "detection",
        "id": f"ALERT-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().isoformat(),
        "confidence": _num(detection.get("confidence"), 0.92),
        "lat": _num(detection.get("lat"), survivor_data["lat"]),
        "lng": _num(detection.get("lng"), survivor_data["lng"]),
        "altitude": _num(detection.get("altitude"), telemetry_data.get("altitude") or 0),
        "distance": _num(detection.get("distance"), 120),
    })
    return {"status": "ok"}

@app.get("/api/mission")
async def get_mission():
    return {
        "id": "mission-001",
        "name": "Maragondon, Cavite Search",
        "startedAt": "07:30 AM",
        "status": "active"
    }

# telemetry_broadcaster removed: simulated telemetry is disabled.
# Real telemetry is ingested via POST /api/telemetry or WS "telemetry_update" from hardware producers.

# ---------------------------------------------------------------------------
# MLX90640 Thermal Feed (real 32x24 sensor, I2C @ 0x33)
# Serves /thermal_feed as MJPEG with thermal colormap (blue→green→yellow→red)
# ---------------------------------------------------------------------------
def _resolve_drone_site_packages() -> list:
    """Locate the onboard-drone virtualenv site-packages so the MLX90640 stack
    (adafruit-blinka / adafruit_mlx90640 / cv2) can be imported even when it is
    not installed inside the backend venv.

    Resolution order:
      1. <project>/drone-onboard/*/venv/lib/python*/site-packages (any python version)
      2. the historical absolute path (kept as a last-resort fallback)

    Returns the directories that actually exist on disk.
    """
    candidates = []
    onboard_root = _os.path.abspath(
        _os.path.join(_os.path.dirname(__file__), "..", "..", "drone-onboard")
    )
    try:
        for board in sorted(_os.listdir(onboard_root)):
            lib_dir = _os.path.join(onboard_root, board, "venv", "lib")
            if not _os.path.isdir(lib_dir):
                continue
            for py in sorted(_os.listdir(lib_dir), reverse=True):
                sp = _os.path.join(lib_dir, py, "site-packages")
                if _os.path.isdir(sp):
                    candidates.append(sp)
    except OSError:
        pass
    candidates.append(
        "/home/piaeris/aeris-project/drone-onboard/raspberry-pi-4b/venv/lib/python3.13/site-packages"
    )
    seen, resolved = set(), []
    for path in candidates:
        if path not in seen and _os.path.isdir(path):
            seen.add(path)
            resolved.append(path)
    return resolved


for _site_packages in _resolve_drone_site_packages():
    if _site_packages not in sys.path:
        sys.path.insert(0, _site_packages)

_thermal_lock = threading.Lock()
_thermal_jpeg: bytes | None = None
# Latest real frame payload, broadcast to WebSocket clients as "thermal_frame".
# None means "no usable frame" (sensor offline / consecutive read failures).
_thermal_frame: dict | None = None
# Timestamps of the most recent frames - used to measure the REAL frame rate
# the sensor is delivering (never a hardcoded value).
_thermal_frame_times: deque = deque(maxlen=8)

# MLX90640 I2C address. 0x33 is the address the real sensor is wired to; the
# environment override exists so the capture loop can be pointed at another
# address without editing the code (used to verify the offline detection path
# against a sensor that is not present).
try:
    THERMAL_I2C_ADDRESS = int(str(os.environ.get("AERIS_THERMAL_I2C_ADDRESS", "0x33")), 0)
except (TypeError, ValueError):
    THERMAL_I2C_ADDRESS = 0x33
THERMAL_I2C_ADDRESS_STR = f"0x{THERMAL_I2C_ADDRESS:02x}"

_thermal_status = {"ok": False, "error": "initializing", "min_c": None, "max_c": None, "mean_c": None, "hotspot_x": None, "hotspot_y": None, "hotspot_c": None, "resolution": "32x24", "address": THERMAL_I2C_ADDRESS_STR, "model": "MLX90640", "frames": 0, "fps": None}

# ── Thermal watchdog ───────────────────────────────────────────────────────
# The capture thread is the only thing that can prove the sensor is alive.
# If it dies, is starved or the sensor stops answering, the "ok" flag would
# stay true forever and the dashboard would keep showing a stale ONLINE
# thermal camera. The watchdog therefore measures the age of the last real
# frame: a sensor that has not delivered anything for THERMAL_STALE_S is
# reported as stale/offline, never as connected.
THERMAL_STALE_S = 5.0
_thermal_last_frame_ts = 0.0      # monotonic timestamp of the last good frame
_thermal_last_frame_iso: str | None = None
_thermal_last_error: str | None = None
_thermal_last_error_ts: float = 0.0

# ── MLX90640 capture lifecycle ────────────────────────────────────────────
#
# TWO DIFFERENT TIMEOUTS ARE INVOLVED AND THEY ARE NOT THE SAME THING:
#
#  1. THERMAL_FRAME_TIMEOUT_S is a PYTHON-LEVEL deadline that is checked
#     BETWEEN I2C transactions. It bounds the vendor library's unbounded
#     "while dataReady == 0" wait, i.e. the case where the sensor is
#     reachable but stops producing new frames. This case IS recoverable
#     in-process: the frame is abandoned, the sensor is re-initialised
#     and the capture continues.
#
#  2. The KERNEL's I2C adapter timeout governs a transaction that is
#     already blocked inside the kernel (for example a device that holds
#     SDA low, or a wedged bus). A Python deadline CANNOT interrupt that:
#     the thread is blocked inside the ioctl and only returns when the
#     kernel gives up. There is no in-process recovery for that case -
#     a thread blocked in a syscall cannot be cancelled from Python.
#     It is detected with the capture thread's HEARTBEAT (which stops
#     advancing while the thread sits in the transaction) and reported as
#     the distinct state "stalled".
#
# The two states are kept apart on purpose: the dashboard must be able to
# tell "the sensor went quiet" (auto-recoverable) from "the bus is wedged"
# (not auto-recoverable, needs an operator/restart).
THERMAL_FRAME_TIMEOUT_S = 3.0
# Back-off between sensor re-initialisation attempts (seconds).
THERMAL_REINIT_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
# Consecutive read failures after which the sensor is re-initialised.
THERMAL_REINIT_AFTER = 10
# Consecutive read failures after which the failure is published to the UI.
THERMAL_ERROR_PUBLISH_AFTER = 3
# The capture thread's heartbeat older than this => the thread is stuck
# inside a kernel I2C transaction (the Python deadline cannot fire).
THERMAL_STALL_S = 10.0

# Live state of the capture thread - observed from OUTSIDE the thread.
_thermal_capture_state = {
    "phase": "starting",        # starting | running | quiet | stalled | failed
    "heartbeat": 0.0,           # monotonic timestamp of the last loop iteration
    "reinit_count": 0,
    "consecutive_failures": 0,
    "invalid_samples": 0,       # non-finite pixels in the last rejected frame
    "last_error": None,
    "backoff_s": 0.0,
}


class ThermalTimeoutError(RuntimeError):
    """The MLX90640 did not deliver a frame within the Python-level deadline."""


class _FrameDeadline:
    """Python-level deadline, checked BETWEEN I2C transactions.

    This is NOT an I2C transaction timeout: it cannot interrupt a transfer
    that is already blocked inside the kernel (see THERMAL_STALL_S).
    """

    __slots__ = ("until",)

    def __init__(self):
        self.until = 0.0

    def arm(self, seconds: float):
        self.until = time.monotonic() + seconds

    def disarm(self):
        self.until = 0.0

    def check(self):
        if self.until and time.monotonic() > self.until:
            raise ThermalTimeoutError(
                f"no frame from the MLX90640 within {THERMAL_FRAME_TIMEOUT_S}s"
            )


def _thermal_frame_age_s() -> float | None:
    """Seconds since the last real MLX90640 frame (None when never seen)."""
    if not _thermal_last_frame_ts:
        return None
    return max(0.0, time.monotonic() - _thermal_last_frame_ts)


def _thermal_capture_phase() -> str:
    """Classify the capture thread from the outside.

    ``running``  frames are arriving
    ``quiet``    the thread is alive but the sensor produces no frames
                 (auto-recoverable - the Python deadline fires and the
                 sensor is re-initialised)
    ``stalled``  the thread is blocked inside a kernel I2C transaction
                 (NOT auto-recoverable in-process: a Python deadline
                 cannot interrupt a transaction that is already blocked)
    ``failed``   the sensor could not be initialised at all
    """
    hb = _thermal_capture_state.get("heartbeat") or 0.0
    if not hb:
        return "starting"
    hb_age = time.monotonic() - hb
    if hb_age > THERMAL_STALL_S:
        return "stalled"
    age = _thermal_frame_age_s()
    if age is not None and age > THERMAL_STALE_S:
        return "quiet"
    return "running"


def _thermal_status_snapshot() -> dict:
    """Thermal status enriched with the frame watchdog.

    ``ok`` is only true while fresh frames keep arriving, so a dead sensor
    (or a dead capture thread) can never leave a stale "online" on screen.
    """
    with _thermal_lock:
        data = dict(_thermal_status)
    age = _thermal_frame_age_s()
    fresh = age is not None and age <= THERMAL_STALE_S
    phase = _thermal_capture_phase()
    data["last_frame"] = _thermal_last_frame_iso
    data["frame_age_s"] = round(age, 2) if age is not None else None
    data["stale"] = bool(age is not None and not fresh)
    data["last_error"] = _thermal_last_error
    data["capture_phase"] = phase
    data["capture_reinit_count"] = _thermal_capture_state.get("reinit_count", 0)
    data["capture_consecutive_failures"] = _thermal_capture_state.get("consecutive_failures", 0)
    data["invalid_samples"] = _thermal_capture_state.get("invalid_samples", 0)
    data["frame_timeout_s"] = THERMAL_FRAME_TIMEOUT_S
    if data.get("ok") and not fresh:
        # The last frame is too old to be trusted any more.
        data["ok"] = False
        data["error"] = (
            f"no frame from the MLX90640 for {age:.1f}s"
            if age is not None else "no frame from the MLX90640 yet"
        )
    if phase == "stalled":
        # The capture thread is blocked inside a kernel I2C transaction.
        # The Python-level frame deadline cannot interrupt it, so this is
        # reported as a distinct, non self-healing state.
        data["ok"] = False
        data["error"] = (
            "capture thread stalled inside an I2C transaction - the sensor "
            "must be re-initialised (a Python deadline cannot interrupt a "
            "blocked kernel transfer)"
        )
    return data


def _measured_fps():
    """Real frames-per-second of the sensor feed (measured, not assumed)."""
    if len(_thermal_frame_times) < 2:
        return None
    span = _thermal_frame_times[-1] - _thermal_frame_times[0]
    if span <= 0:
        return None
    return round((len(_thermal_frame_times) - 1) / span, 2)


def _broadcast_thermal_frame(payload):
    """Push a real thermal frame (or an explicit null frame) to WS clients."""
    loop = globals().get("_main_loop")
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
    except Exception as exc:
        print(f"[WS] thermal frame broadcast failed: {type(exc).__name__}: {exc}")


def _read_cpu_times():
    """Raw CPU jiffies from /proc/stat -> (total, idle)."""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        values = [int(p) for p in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        return sum(values), idle
    except Exception:
        return None, None


# Real CPU utilisation, measured by a background sampler. The load average is
# NOT a percentage: on a 4-core Pi a load of 4 means "all cores busy", so the
# load average was reported as "100% CPU" even when the CPU was mostly idle.
_cpu_usage = {"percent": None, "at": 0.0}


def _cpu_monitor_loop():
    """Sample /proc/stat in the background to measure the real CPU utilisation."""
    last_total, last_idle = _read_cpu_times()
    while True:
        time.sleep(2.0)
        total, idle = _read_cpu_times()
        if total is None or idle is None or last_total is None or last_idle is None:
            continue
        d_total = total - last_total
        d_idle = idle - last_idle
        last_total, last_idle = total, idle
        if d_total <= 0:
            continue
        _cpu_usage["percent"] = round(max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0)), 1)
        _cpu_usage["at"] = time.monotonic()


def _read_system_stats():
    """Real Raspberry Pi system stats (no simulated values)."""
    import os
    cpu_temp_c = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            cpu_temp_c = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        pass
    # Real CPU utilisation (measured), not the load average.
    cpu_usage_pct = _cpu_usage.get("percent")
    if cpu_usage_pct is not None and time.monotonic() - _cpu_usage.get("at", 0) > 10:
        # The sampler stopped - do not show a stale figure.
        cpu_usage_pct = None
    mem_total_kb = mem_avail_kb = None
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in ("MemTotal:", "MemAvailable:"):
                    info[parts[0]] = int(parts[1])
        mem_total_kb, mem_avail_kb = info.get("MemTotal:"), info.get("MemAvailable:")
    except Exception:
        pass
    ram_usage_pct = None
    if mem_total_kb and mem_avail_kb is not None:
        ram_usage_pct = round((1 - mem_avail_kb / mem_total_kb) * 100.0, 1)
    uptime_s = None
    try:
        with open("/proc/uptime") as f:
            uptime_s = round(float(f.read().split()[0]), 1)
    except Exception:
        pass
    return {
        "cpu_temp_c": cpu_temp_c,
        "cpu_usage_pct": cpu_usage_pct,
        "ram_usage_pct": ram_usage_pct,
        "ram_total_mb": round(mem_total_kb / 1024, 1) if mem_total_kb else None,
        "ram_used_mb": round((mem_total_kb - mem_avail_kb) / 1024, 1) if mem_total_kb and mem_avail_kb is not None else None,
        "uptime_s": uptime_s,
        "cpu_count": os.cpu_count(),
        "loadavg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "timestamp": datetime.now().isoformat(),
    }


_LORA_TX_STATUS_PATH = "/tmp/opencode/lora_tx_status.json"


def _read_lora_tx_state():
    """Read the real SX1278 TX state written by the onboard transmitter (sender.py).
    Returns dict with state (sending/waiting/error), timestamps, counts, ack."""
    import json as _json, os as _os, time as _time
    try:
        mtime = _os.path.getmtime(_LORA_TX_STATUS_PATH)
        if _time.time() - mtime > 30:  # stale: transmitter not running / not reporting
            return {"state": "waiting", "fresh": False, "last_start": None, "last_ok": None, "last_error": None,
                    "tx_ok_count": None, "tx_err_count": None, "ack_received": False}
        with open(_LORA_TX_STATUS_PATH) as f:
            d = _json.load(f)
        return {"state": d.get("state", "waiting"), "fresh": True, "last_start": d.get("last_start"),
                "last_ok": d.get("last_ok"), "last_error": d.get("last_error"),
                "tx_ok_count": d.get("tx_ok_count"), "tx_err_count": d.get("tx_err_count"),
                "ack_received": bool(d.get("ack_received"))}
    except Exception:
        return {"state": "waiting", "fresh": False, "last_start": None, "last_ok": None, "last_error": None,
                "tx_ok_count": None, "tx_err_count": None, "ack_received": False}


# ── Bus monitor ────────────────────────────────────────────────────────────
# The I2C inventory is obtained with `i2cdetect`, which takes ~100 ms and
# probes every address on the bus. Running it inline in the WebSocket
# broadcast blocked the asyncio event loop on every cycle, which delayed the
# live frames and made the dashboard look frozen. The scan now runs in its own
# thread and the result is cached, so the broadcast always has a fresh-enough
# inventory without ever blocking the event loop.
#
# The interval is deliberately generous: the scan adds traffic to the same
# I2C bus that the MLX90640 capture thread streams frames over.
BUS_SCAN_INTERVAL_S = 15.0
_bus_cache = {
    "at": 0.0,
    "i2c": {"bus": 1, "bus_active": False, "devices": [], "mlx90640_detected": False,
            "connected": False, "error": None, "scanned_at": None},
    "spi": {"bus": 0, "bus_active": False, "devices": [], "connected": False,
            "error": None, "scanned_at": None},
}


def _scan_i2c_bus(bus: int = 1) -> dict:
    """Real I2C bus scan - the addresses that actually acknowledge."""
    import re
    import subprocess
    devices: list[str] = []
    bus_active = False
    error = None
    try:
        out = subprocess.run(
            ["i2cdetect", "-y", str(bus)], capture_output=True, text=True, timeout=5
        ).stdout
        # The scan completed -> the I2C bus answered.
        bus_active = True
        # i2cdetect prints addresses without 0x prefix (e.g. "33" in the 0x30 row)
        found: list[str] = []
        for line in out.splitlines():
            if ":" in line:
                found += re.findall(r"\b[0-9a-fA-F]{2}\b", line.split(":", 1)[1])
        devices = [f"0x{a}" for a in found if a != "--"]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "bus": bus,
        "bus_active": bus_active,
        "devices": devices,
        "mlx90640_detected": THERMAL_I2C_ADDRESS_STR in devices,
        "connected": THERMAL_I2C_ADDRESS_STR in devices,
        "error": error,
        "scanned_at": datetime.now().isoformat(),
    }


def _scan_spi_bus() -> dict:
    """Real SPI bus check - the device nodes have to exist and be usable."""
    import os
    nodes = ["/dev/spidev0.0", "/dev/spidev0.1"]
    spi_ok = all(os.path.exists(n) for n in nodes)
    # The SPI bus is only usable when the device nodes can really be opened.
    spi_active = spi_ok and os.access("/dev/spidev0.0", os.R_OK | os.W_OK)
    return {
        "bus": 0,
        "bus_active": spi_active,
        "devices": nodes if spi_ok else [],
        "connected": spi_ok,
        "error": None if spi_ok else "SPI device nodes are missing",
        "scanned_at": datetime.now().isoformat(),
    }


def _refresh_bus_cache() -> None:
    """Refresh the cached bus inventory (runs in the bus-monitor thread)."""
    try:
        i2c = _scan_i2c_bus(1)
        spi = _scan_spi_bus()
        _bus_cache["i2c"] = i2c
        _bus_cache["spi"] = spi
        _bus_cache["at"] = time.monotonic()
    except Exception as exc:
        print(f"[Bus] scan failed: {type(exc).__name__}: {exc}")


def _bus_monitor_loop():
    """Background thread: keep the I2C/SPI inventory fresh."""
    while True:
        _refresh_bus_cache()
        time.sleep(BUS_SCAN_INTERVAL_S)


def _read_hardware_status():
    """Real device/bus detection. LoRa RSSI/SNR only if a receiver reports them (none here -> null)."""
    import os
    # The bus inventory comes from the cache - the scan itself runs in the
    # bus-monitor thread so the event loop is never blocked by a subprocess.
    i2c = dict(_bus_cache.get("i2c") or {})
    spi = dict(_bus_cache.get("spi") or {})
    i2c_devices = list(i2c.get("devices") or [])
    i2c_bus_active = bool(i2c.get("bus_active"))
    spi_ok = bool(spi.get("connected"))
    spi_active = bool(spi.get("bus_active"))
    _lora_tx = _read_lora_tx_state()
    lora_hw = dict(_lora_hardware)
    lora_present = bool(lora_hw.get("available"))
    lora_probe_at = lora_hw.get("last_probe")
    # The radio identity is re-probed on a timer. A probe that is too old can
    # no longer prove that the radio is still there.
    lora_probe_fresh = True
    if lora_probe_at:
        try:
            age = time.time() - datetime.fromisoformat(lora_probe_at).timestamp()
            lora_probe_fresh = age <= 180.0
        except Exception:
            lora_probe_fresh = True
    lora_rx = _lora_rx_svc.get_status() if _lora_rx_svc is not None else {}
    lora_rx_running = bool(lora_rx.get("running"))
    lora_rx_packets = lora_rx.get("rx_count")
    lora_rx_rssi = lora_rx.get("last_rssi")
    lora_rx_snr = lora_rx.get("last_snr")
    lora_rx_last = lora_rx.get("last_rx")
    # A running ground-side receiver is independent proof that the radio is
    # alive (it holds the SPI device and decodes frames continuously).
    lora_connected = bool(lora_present and lora_probe_fresh) or lora_rx_running
    thermal = _thermal_status_snapshot()
    thermal_connected = bool(thermal.get("ok"))
    # LoRa SX1278 is attached via SPI (CE0/GPIO8, INT/GPIO4). Without a ground-side
    # receiver decoding frames, RSSI/SNR/packet_count are genuinely unavailable.
    return {
        "rpi": {"model": "Raspberry Pi 4 Model B", "connected": True},
        "i2c": {
            "bus": 1,
            "bus_active": i2c_bus_active,
            "devices": i2c_devices,
            "mlx90640_detected": THERMAL_I2C_ADDRESS_STR in i2c_devices,
            "connected": THERMAL_I2C_ADDRESS_STR in i2c_devices,
            "error": i2c.get("error"),
            "scanned_at": i2c.get("scanned_at"),
        },
        "spi": {
            "bus": 0,
            "bus_active": spi_active,
            "devices": list(spi.get("devices") or []) if spi_ok else [],
            "connected": spi_ok,
            "error": spi.get("error"),
            "scanned_at": spi.get("scanned_at"),
        },
        "mlx90640": {
            "address": THERMAL_I2C_ADDRESS_STR,
            "resolution": "32x24",
            # Connected only while the sensor really delivers frames.
            "connected": thermal_connected,
            "stale": bool(thermal.get("stale")),
            "last_frame": thermal.get("last_frame"),
            "frame_age_s": thermal.get("frame_age_s"),
            "frames": thermal.get("frames"),
            "error": thermal.get("error"),
        },
        "lora_sx1278": {
            "model": lora_hw.get("model") if lora_present else None,
            "frequency_mhz": lora_hw.get("frequency_mhz") if lora_present else None,
            "bandwidth": lora_hw.get("bandwidth") if lora_present else None,
            "spi_device": lora_hw.get("spi_device"),
            "spi_connected": spi_ok,
            # A radio only counts as connected when it actually answered on SPI.
            "connected": lora_connected,
            "rx_running": lora_rx_running,
            "last_probe": lora_probe_at,
            "probe_fresh": lora_probe_fresh,
            "transmission_status": "LISTENING" if lora_rx_running else ("WAITING" if spi_ok else "NO RADIO"),
            "last_tx_start": _lora_tx["last_start"],
            "last_tx_ok": _lora_tx["last_ok"],
            "last_tx_error": _lora_tx["last_error"],
            "tx_ok_count": _lora_tx["tx_ok_count"],
            "tx_err_count": _lora_tx["tx_err_count"],
            "ack_received": _lora_tx["ack_received"],
            "tx_reporter_fresh": _lora_tx["fresh"],
            # Real reception measurements: null until the first packet
            # actually arrives - never a fabricated value.
            "rx_packet_count": lora_rx_packets if lora_rx_packets else None,
            "last_rx": lora_rx_last,
            "rssi_dbm": lora_rx_rssi,
            "snr_db": lora_rx_snr,
            "reason": (
                None if lora_connected else
                (lora_hw.get("reason") or "no LoRa radio answered on SPI")
            ),
        },
        "timestamp": datetime.now().isoformat(),
    }


def _read_device_health() -> dict:
    """Per-device health for every hardware component registered in the station.

    Each entry states what was actually measured:

      ``online``   the device answered recently
      ``stale``    it answered, but not recently enough to trust the reading
      ``offline``  it did not answer
      ``unknown``  it has not been probed yet

    The dashboard renders these states directly, so a device that stops
    answering turns red on its own - it is never left showing a stale green.
    """
    hw = _read_hardware_status()
    thermal = _thermal_status_snapshot()
    sensors = _read_sensor_status()
    now = datetime.now().isoformat()

    def _state(connected: bool, reason: str | None = None, stale: bool = False) -> str:
        if stale:
            return "stale"
        return "online" if connected else "offline"

    mlx = hw.get("mlx90640", {})
    mlx_stale = bool(mlx.get("stale"))
    mlx_connected = bool(mlx.get("connected"))
    lora = hw.get("lora_sx1278", {})
    lora_connected = bool(lora.get("connected"))
    i2c = hw.get("i2c", {})
    spi = hw.get("spi", {})

    devices = {
        "rpi4b": {
            "label": "Raspberry Pi 4B",
            "state": "online",
            "connected": True,
            "reason": None,
            "last_seen": now,
        },
        "mlx90640": {
            "label": "MLX90640 thermal camera",
            "state": _state(mlx_connected, mlx.get("error"), mlx_stale),
            "connected": mlx_connected,
            "reason": mlx.get("error"),
            "last_seen": thermal.get("last_frame"),
            "detail": f"I2C {mlx.get('address')} · frames {mlx.get('frames')}",
        },
        "lora_sx1278": {
            "label": "SX1278 LoRa radio",
            "state": _state(lora_connected, lora.get("reason")),
            "connected": lora_connected,
            "reason": lora.get("reason"),
            "last_seen": lora.get("last_rx") or lora.get("last_probe"),
            "detail": f"{lora.get('frequency_mhz')} MHz · {lora.get('transmission_status')}",
        },
        "i2c_bus": {
            "label": "I2C bus 1",
            "state": "online" if i2c.get("bus_active") else "offline",
            "connected": bool(i2c.get("bus_active")),
            "reason": i2c.get("error"),
            "last_seen": i2c.get("scanned_at"),
            "detail": ", ".join(i2c.get("devices") or []) or "no devices",
        },
        "spi_bus": {
            "label": "SPI bus 0",
            "state": "online" if spi.get("connected") else "offline",
            "connected": bool(spi.get("connected")),
            "reason": spi.get("error"),
            "last_seen": spi.get("scanned_at"),
            "detail": ", ".join(spi.get("devices") or []) or "no device nodes",
        },
    }

    # Optional flight sensors - reported only when they are actually probed.
    for name, info in sensors.items():
        if name == "lora":
            continue
        available = bool((info or {}).get("available"))
        devices[f"sensor_{name}"] = {
            "label": f"{name.upper()} sensor",
            "state": "online" if available else "offline",
            "connected": available,
            "reason": (info or {}).get("reason"),
            "last_seen": now if available else None,
            "detail": None,
        }

    return {
        "devices": devices,
        "hardware": hw,
        "thermal": thermal,
        "timestamp": now,
    }


async def _system_broadcaster():
    """Push real system + hardware status over WebSocket (real values only)."""
    while True:
        try:
            health = _read_device_health()
            await manager.broadcast({
                "type": "system",
                "system": _read_system_stats(),
                "hardware": health["hardware"],
                "devices": health["devices"],
                "thermal": health["thermal"],
                "sensors": _read_sensor_status(),
                "lora_hardware": dict(_lora_hardware),
                "lora_rx": _lora_rx_svc.get_status() if _lora_rx_svc is not None else None,
                "telemetry": dict(telemetry_data),
            })
        except Exception as exc:
            # Never swallow this silently: a broken broadcast means the
            # dashboard stops receiving live hardware data.
            print(f"[WS] system broadcast failed: {type(exc).__name__}: {exc}")
        await asyncio.sleep(2.0)


# ── Real hardware sensor poller ────────────────────────────────────────────
def _apply_sensor_readings(readings: dict) -> bool:
    """Merge real sensor readings into telemetry_data.

    Only values that a real device actually measured are written. Sensors that
    are absent leave their field untouched (None), so the dashboard shows N/A
    instead of a fabricated reading.

    Returns True when at least one real telemetry value was produced.
    """
    updated = False

    gps = readings.get("gps") or {}
    if gps.get("available"):
        if gps.get("lat") is not None:
            telemetry_data["lat"] = gps["lat"]
            updated = True
        if gps.get("lng") is not None:
            telemetry_data["lng"] = gps["lng"]
            updated = True
        if gps.get("altitude_m") is not None:
            telemetry_data["altitude"] = gps["altitude_m"]
            updated = True
        if gps.get("speed_mps") is not None:
            telemetry_data["speed"] = gps["speed_mps"]
            updated = True
        if gps.get("heading_deg") is not None:
            telemetry_data["heading"] = gps["heading_deg"]
            updated = True

    battery = readings.get("battery") or {}
    if battery.get("available"):
        if battery.get("percent") is not None:
            telemetry_data["battery"] = battery["percent"]
            updated = True
        if battery.get("voltage_v") is not None:
            telemetry_data["battery_voltage"] = battery["voltage_v"]
            updated = True

    barometer = readings.get("barometer") or {}
    if barometer.get("available") and barometer.get("altitude_m") is not None:
        # A barometer measures height above the reference sea level, which is
        # only used when no GPS altitude is available.
        if telemetry_data.get("altitude") is None:
            telemetry_data["altitude"] = barometer["altitude_m"]
            updated = True

    compass = readings.get("compass") or {}
    if compass.get("available") and compass.get("heading_deg") is not None:
        if telemetry_data.get("heading") is None:
            telemetry_data["heading"] = compass["heading_deg"]
            updated = True

    if updated:
        telemetry_data["connected"] = True
        telemetry_data["source"] = "rpi4b-sensors"
        telemetry_data["last_update"] = datetime.now().isoformat()
        telemetry_data["unavailable_reason"] = None
    else:
        telemetry_data["connected"] = False
        telemetry_data["source"] = "none"
        missing = [name for name, info in sensor_status.items() if not info.get("available")]
        telemetry_data["unavailable_reason"] = (
            "No flight telemetry hardware detected (" + ", ".join(missing) + ")"
            if missing else "No flight telemetry hardware detected"
        )

    return updated


def _sensor_poll_loop():
    """Poll the real sensors and publish the readings.

    The LoRa radio identity is re-probed every 60 s (it does not change while
    the radio is connected, but this lets the dashboard recover automatically
    once the wiring is fixed - no service restart needed). The other sensors
    are polled continuously so the dashboard updates live.
    """
    global _lora_hardware
    if not _SENSORS_AVAILABLE:
        return

    last_lora_probe = 0.0
    while True:
        try:
            readings = {}

            now = time.time()
            if now - last_lora_probe >= 60.0:
                _lora_hardware = _sensors.probe_sx1278()
                # Record when the radio identity was really read, so the
                # dashboard can tell a fresh answer from an outdated one.
                _lora_hardware["last_probe"] = datetime.now().isoformat()
                last_lora_probe = now
            else:
                _lora_hardware = dict(_lora_hardware)
            readings["lora"] = dict(_lora_hardware)

            for name in ("gps", "battery", "barometer", "compass"):
                try:
                    readings[name] = getattr(_sensors, f"read_{name}")()
                except Exception as exc:
                    readings[name] = {"available": False, "reason": f"read failed: {exc}"}

            sensor_status.update({k: dict(v) for k, v in readings.items()})
            _apply_sensor_readings(readings)
        except Exception as exc:
            print(f"[Sensors] poll error: {type(exc).__name__}: {exc}")
        time.sleep(2.0)


def _read_sensor_status():
    """Snapshot of every real sensor (used by the API and the WebSocket)."""
    return {k: dict(v) for k, v in sensor_status.items()}


def _build_thermal_lut():
    """256-entry RGB LUT (high-contrast thermal): deep navy -> electric blue -> cyan
    -> bright green -> yellow -> orange -> red -> white-hot."""
    import numpy as np
    stops = [
        (0,   (0, 0, 64)),      # deep navy (coldest)
        (36,  (0, 0, 255)),     # electric blue
        (73,  (0, 224, 255)),   # cyan
        (110, (0, 255, 64)),    # bright green
        (146, (255, 255, 0)),   # yellow
        (182, (255, 128, 0)),   # orange
        (219, (255, 0, 0)),     # red
        (255, (255, 255, 255)), # white-hot (hottest)
    ]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        for j in range(len(stops) - 1):
            i0, c0 = stops[j]
            i1, c1 = stops[j + 1]
            if i0 <= i <= i1:
                t = (i - i0) / max(i1 - i0, 1)
                lut[i] = tuple(int(c0[k] + t * (c1[k] - c0[k])) for k in range(3))
                break
    return lut


# --- Real-time human detection (heat-based, display overlay only) ---
_human_track = {"boxes": [], "last_seen": 0.0, "lock": threading.Lock()}
HUMAN_TIMEOUT_S = 2.0  # remove box after this long without detection


def _detect_humans(arr):
    """Detect human-sized thermal regions from the raw 24x32 matrix.

    Adaptive threshold (Otsu-style split on the frame's own distribution),
    connected components, size/aspect filtering. Returns list of boxes
    [(x0, y0, x1, y1)] in sensor coords (raw physical orientation).
    """
    import numpy as np
    try:
        from scipy import ndimage as _ndi
    except Exception:
        return []
    a = np.asarray(arr, dtype=np.float32)
    mn, mx = float(a.min()), float(a.max())
    if mx - mn < 1.0:  # no meaningful gradient -> no detection
        return []
    # adaptive threshold: Otsu on the frame histogram
    hist, edges = np.histogram(a, bins=32, range=(mn, mx))
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(hist); w1 = hist.sum() - w0
    mu0 = np.cumsum(hist * centers)
    total_mu = mu0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mu1 = (total_mu - mu0) / np.where(w1 == 0, 1, w1)
        between = w0 * w1 * (mu0 / np.where(w0 == 0, 1, w0) - mu1) ** 2
    between[~np.isfinite(between)] = 0
    thr = float(centers[int(np.argmax(between))])
    mask = a > thr
    lab, n = _ndi.label(mask)
    boxes = []
    _mean, _std = float(a.mean()), float(a.std())
    # Sensitive gate (distant/small persons): ambient gradients are rejected by the
    # area/aspect filters below (they produce whole-frame regions), not by this gate.
    _gate = _mean + max(1.5, 1.5 * _std)
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if float(a[ys, xs].max()) < _gate:
            continue
        area = len(xs)
        # human-sized region: plausible pixel count + aspect (not whole-frame, not noise)
        if area < 4 or area > 400:
            continue
        x0, x1 = xs.min(), xs.max()
        y0, y1 = ys.min(), ys.max()
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw == 0 or bh == 0:
            continue
        if bw > 28 or bh > 21:  # whole-frame/background gradient, not a person
            continue
        aspect = bh / max(bw, 1)
        if not (0.3 <= aspect <= 3.0):
            continue
        boxes.append((int(x0), int(y0), int(x1), int(y1)))
    # prefer largest regions first (reliable separation), cap at 3
    boxes.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return boxes[:3]


def _track_humans(detections):
    """Temporal smoothing + timeout. Returns smoothed boxes."""
    import time as _time
    now = _time.time()
    with _human_track["lock"]:
        if detections:
            prev = _human_track["boxes"]
            smoothed = []
            for b in detections:
                best, bestd = None, 1e9
                for p in prev:
                    d = abs(p[0] - b[0]) + abs(p[1] - b[1]) + abs(p[2] - b[2]) + abs(p[3] - b[3])
                    if d < bestd:
                        bestd, best = d, p
                if best is not None and bestd < 40:  # same person: EMA smooth
                    smoothed.append(tuple(int(round(0.6 * b[k] + 0.4 * best[k])) for k in range(4)))
                else:
                    smoothed.append(b)
            _human_track["boxes"] = smoothed
            _human_track["last_seen"] = now
            return smoothed
        if now - _human_track["last_seen"] > HUMAN_TIMEOUT_S:
            _human_track["boxes"] = []
        return _human_track["boxes"]


def _render_thermal_jpeg(matrix, min_c, max_c, lut, status_text=None, error_text=None):
    """Render a 24x32 thermal matrix to JPEG bytes (pure thermal output, no overlays).
    Rendering: nearest-neighbor pixelated (sharp visible square pixels).
    Depixelization (curve extraction/contour/polygon smoothing) removed 2026-11-09."""
    import numpy as np
    from PIL import Image, ImageDraw

    scale = 16  # upscale 32x24 -> 512x384 for visibility
    if matrix is not None and error_text is None:
        arr = np.asarray(matrix, dtype=np.float32).reshape((24, 32))
        # High-res edge-preserving rendering (display-only; raw data untouched):
        # bilateralFilter @32x24 (temperature-aware smoothing, hotspots kept)
        # -> single cv2 INTER_CUBIC jump to 1024x768 -> inferno.
        try:
            import matplotlib
            import cv2
            span = max(float(max_c) - float(min_c), 1e-3)
            norm = np.clip((arr - float(min_c)) / span, 0.0, 1.0).astype(np.float32)
            # No flip/rotation: display matches the sensor's physical orientation.
            smooth = cv2.bilateralFilter(norm, d=5, sigmaColor=0.08, sigmaSpace=2)
            up = cv2.resize(smooth, (1024, 768), interpolation=cv2.INTER_CUBIC)
            up = np.clip(up, 0.0, 1.0)
            cmap = matplotlib.colormaps["inferno"]
            rgba = (cmap(up)[:, :, :3] * 255.0).astype(np.uint8)
            img = Image.fromarray(rgba, mode="RGB")
            # Human detection overlay (AFTER rendering; raw temps untouched).
            # Coords: sensor (x,y) -> display pixels (x*32, y*32), no flipping.
            from PIL import ImageDraw as _ID
            _detections = _detect_humans(arr)
            _boxes = _track_humans(_detections)
            if _boxes:
                _draw = _ID.Draw(img)
                _sx, _sy = 1024 // 32, 768 // 24
                try:
                    from PIL import ImageFont as _IF
                    _font = _IF.load_default(size=30)
                except Exception:
                    _font = None
                for (x0, y0, x1, y1) in _boxes:
                    px0, py0 = x0 * _sx, y0 * _sy
                    px1, py1 = (x1 + 1) * _sx - 1, (y1 + 1) * _sy - 1
                    # Neon cyan outline, thickness scales with box size (min 5px)
                    _bw = max(5, min(10, (px1 - px0 + py1 - py0) // 60))
                    _draw.rectangle([px0, py0, px1, py1], outline=(0, 255, 255), width=_bw)
                    _label = "HUMAN DETECTED"
                    _tw = _draw.textlength(_label, font=_font) if _font else _draw.textlength(_label)
                    _ty = max(py0 - 40, 0)
                    _draw.rectangle([px0, _ty, px0 + _tw + 16, _ty + 40], fill=(0, 200, 200))
                    _draw.text((px0 + 8, _ty + 7), _label, fill=(0, 0, 0), font=_font)
        except Exception:
            span = max(float(max_c) - float(min_c), 1e-3)
            norm = np.clip((arr - float(min_c)) / span * 255.0, 0, 255).astype(np.uint8)
            rgb = lut[norm]  # (24,32,3)
            img = Image.fromarray(rgb, mode="RGB").resize(
                (32 * scale, 24 * scale), Image.Resampling.BILINEAR
            )
    else:
        # Keep the image clean; frontend handles error messaging.
        img = Image.new("RGB", (32 * scale, 24 * scale), (8, 8, 8))
        if error_text:
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), "THERMAL UNAVAILABLE", fill=(200, 80, 80))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _measure_i2c_burst_ms(mlx, samples=3):
    """Time a full 1664-byte RAM burst read (the sensor's frame payload).

    The MLX90640 delivers a frame as two such bursts, so the achievable
    frame rate is bounded by the I2C bus speed, not by the sensor setting.
    The median of several reads is used so scheduler jitter does not skew
    the decision.
    """
    times = []
    try:
        buf = [0] * 834
        for _ in range(samples):
            t0 = time.monotonic()
            mlx._I2CReadWords(0x0400, buf, end=832)
            times.append((time.monotonic() - t0) * 1000.0)
    except Exception:
        return None
    if not times:
        return None
    times.sort()
    return times[len(times) // 2]


def _select_refresh_rate(mlx):
    """Pick the highest sensor refresh rate the I2C bus can actually sustain.

    At 400 kHz a full frame (2 x 1664 bytes) takes ~115 ms, so 4 Hz works.
    At the Raspberry Pi's default 100 kHz the same frame needs ~460 ms, so
    asking for 4 Hz makes the sensor library overrun its retry loop
    ("Too many retries") and throw frames away. Measuring the bus keeps the
    setting honest - and it upgrades automatically once the boot config
    raises the bus to 400 kHz.
    """
    import adafruit_mlx90640
    burst_ms = _measure_i2c_burst_ms(mlx)
    if burst_ms is None:
        chosen = 2
    elif 2 * burst_ms < 225:      # frame fits inside a 4 Hz period (250 ms)
        chosen = 4
    else:
        # 2 Hz is the safe default for a slow bus; if the frame still does
        # not fit, the capture loop downgrades to 1 Hz at runtime.
        chosen = 2
    rates = {
        1: adafruit_mlx90640.RefreshRate.REFRESH_1_HZ,
        2: adafruit_mlx90640.RefreshRate.REFRESH_2_HZ,
        4: adafruit_mlx90640.RefreshRate.REFRESH_4_HZ,
    }
    mlx.refresh_rate = rates[chosen]
    print(
        f"[Thermal] I2C burst {burst_ms:.0f} ms -> sensor refresh rate {chosen} Hz "
        f"(frame ~{2 * burst_ms:.0f} ms)"
    )
    return chosen


def _init_thermal_sensor():
    """Bring up the MLX90640.

    Returns ``(mlx, i2c, error)``. Split out of the capture loop so that
    the sensor can be re-created deterministically (and the previous bus
    handle released) and so the tests can replace it with a fake.
    """
    import board
    import busio
    import adafruit_mlx90640
    i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
    mlx = adafruit_mlx90640.MLX90640(i2c, address=THERMAL_I2C_ADDRESS)
    _select_refresh_rate(mlx)
    return mlx, i2c, None


def _release_thermal_sensor(mlx, i2c):
    """Release the bus handle of a sensor that is being replaced.

    The previous code re-created the sensor without closing the old
    ``busio.I2C`` handle, leaking one file descriptor on EVERY re-init.
    """
    for handle in (i2c,):
        try:
            if handle is not None and hasattr(handle, "close"):
                handle.close()
        except Exception:
            pass
    try:
        if mlx is not None and hasattr(mlx, "i2c_device"):
            device = getattr(mlx, "i2c_device", None)
            if device is not None and hasattr(device, "close"):
                device.close()
    except Exception:
        pass


def _arm_frame_guard(mlx, deadline: _FrameDeadline):
    """Bound the vendor library's unbounded data-ready wait.

    adafruit-circuitpython-mlx90640 1.3.9 waits for the data-ready bit in
    an unbounded ``while dataReady == 0`` loop (adafruit_mlx90640.py:146).
    The instance's I2C primitives are wrapped so the deadline is checked
    BETWEEN transactions.

    SCOPE - this is a PYTHON-LEVEL guarantee only. It bounds the case
    "the sensor is reachable but produces no new frame". It does NOT
    interrupt a transfer that is already blocked inside the kernel; that
    case is detected through the capture thread's heartbeat and reported
    as the distinct "stalled" state (see THERMAL_STALL_S).

    Signatures verified against adafruit-circuitpython-mlx90640 1.3.9:
        _I2CReadWords(self, addr, buffer, *, end=None)
        _I2CWriteWord(self, writeAddress, data)
    """
    if mlx is None:
        return False
    if getattr(mlx, "_aeris_guard_armed", False):
        return True
    original_read = mlx._I2CReadWords
    original_write = mlx._I2CWriteWord

    def guarded_read(addr, buffer, *, end=None):
        deadline.check()
        return original_read(addr, buffer, end=end)

    def guarded_write(addr, data):
        deadline.check()
        return original_write(addr, data)

    mlx._I2CReadWords = guarded_read
    mlx._I2CWriteWord = guarded_write
    mlx._aeris_guard_armed = True
    return True


def _thermal_reinit(mlx, i2c, deadline: _FrameDeadline, backoff_idx: int):
    """Release the current sensor and bring a new one up, with back-off.

    Returns ``(mlx, i2c, backoff_idx, error_or_None)``.

    The back-off grows with every consecutive failed attempt
    (1, 2, 4, 8, 16, 30 s and then stays at 30 s), so a sensor that stays
    unavailable can never spin the CPU or flood the bus. The previous
    bus handle is closed FIRST - the old code re-created the sensor
    without closing the old handle and leaked one file descriptor on
    every re-initialisation.
    """
    _release_thermal_sensor(mlx, i2c)
    delay = THERMAL_REINIT_BACKOFF_S[
        min(backoff_idx, len(THERMAL_REINIT_BACKOFF_S) - 1)
    ]
    backoff_idx += 1
    _thermal_capture_state["reinit_count"] += 1
    _thermal_capture_state["backoff_s"] = delay
    # Back off before touching the bus again.
    time.sleep(delay)
    try:
        new_mlx, new_i2c, _ = _init_thermal_sensor()
        _arm_frame_guard(new_mlx, deadline)
        # The back-off is reset by a successful bring-up. The failure
        # counter is reset by a successful FRAME - never by a successful
        # re-initialisation.
        return new_mlx, new_i2c, 0, None
    except Exception as exc:
        return None, None, backoff_idx, f"reinit failed: {exc}"


def _thermal_capture_loop():
    """Background thread: read real MLX90640 frames and update shared JPEG."""
    # NOTE: _thermal_frame MUST be declared global - otherwise the assignments
    # below create a local variable and the shared frame never gets cleared,
    # which would keep serving a stale matrix after the sensor died.
    global _thermal_jpeg, _thermal_status, _thermal_frame
    global _thermal_last_frame_ts, _thermal_last_frame_iso
    global _thermal_last_error, _thermal_last_error_ts
    import numpy as np

    lut = _build_thermal_lut()
    deadline = _FrameDeadline()

    # Init sensor with retries (first reads can transiently fail)
    mlx = None
    i2c = None
    last_err = None
    for attempt in range(6):
        _thermal_capture_state["heartbeat"] = time.monotonic()
        try:
            mlx, i2c, last_err = _init_thermal_sensor()
            _arm_frame_guard(mlx, deadline)
            last_err = None
            break
        except Exception as e:
            mlx = None
            i2c = None
            last_err = str(e)
            time.sleep(1.0)

    if mlx is None:
        msg = f"Sensor init failed @{THERMAL_I2C_ADDRESS_STR}: {last_err}"
        _thermal_last_error = last_err
        _thermal_last_error_ts = time.monotonic()
        _thermal_capture_state["phase"] = "failed"
        _thermal_capture_state["last_error"] = last_err
        with _thermal_lock:
            if globals().get("_mjpeg_clients", 0):
                _thermal_jpeg = _render_thermal_jpeg(None, 0, 0, lut, error_text=msg)
            _thermal_frame = None
            _thermal_status = {"ok": False, "error": msg, "min_c": None, "max_c": None, "mean_c": None, "hotspot_x": None, "hotspot_y": None, "hotspot_c": None, "resolution": "32x24", "address": THERMAL_I2C_ADDRESS_STR, "model": "MLX90640", "frames": 0, "fps": None}
        _broadcast_thermal_frame({"type": "thermal_frame", "matrix": None, "error": msg, "timestamp": datetime.now().isoformat()})
        # keep serving the error frame
        while True:
            time.sleep(5)

    frame = [0.0] * 768
    consec_fail = 0
    retry_overflow = 0
    backoff_idx = 0
    while True:
        # The heartbeat lets an OUTSIDE observer distinguish two very
        # different failures:
        #   * the thread keeps ticking but no frame arrives -> the sensor is
        #     quiet -> the Python-level deadline applies and the sensor is
        #     re-initialised automatically;
        #   * the heartbeat stops advancing -> the thread is blocked inside
        #     a kernel I2C transaction -> no Python deadline can interrupt
        #     it, so that state is reported separately (see
        #     _thermal_capture_phase).
        _thermal_capture_state["heartbeat"] = time.monotonic()
        if mlx is None:
            # The sensor could not be (re-)initialised. Keep the thread
            # alive, back off and try again - the rest of the ground
            # station is not affected by this component.
            mlx, i2c, backoff_idx, reinit_err = _thermal_reinit(
                None, None, deadline, backoff_idx
            )
            if reinit_err:
                _thermal_last_error = reinit_err
                _thermal_last_error_ts = time.monotonic()
                _thermal_capture_state["last_error"] = reinit_err
                _thermal_capture_state["phase"] = "failed"
            continue
        try:
            # Allow settle time; retry on transient errors (math domain error / retries)
            read_ok = False
            read_err = None
            timed_out = False
            deadline.arm(THERMAL_FRAME_TIMEOUT_S)
            for _ in range(5):
                try:
                    # getFrame() blocks internally until the sensor signals a
                    # fresh frame (status reg 0x8000 bit 3). Sleeping first
                    # would only add latency on top of the sensor cadence.
                    #
                    # The deadline armed above turns that unbounded wait into
                    # a bounded one. It is a PYTHON-level deadline checked
                    # BETWEEN I2C transactions - it cannot interrupt a
                    # transfer that is already blocked inside the kernel.
                    mlx.getFrame(frame)
                    read_ok = True
                    break
                except ThermalTimeoutError as e:
                    read_err = str(e)
                    timed_out = True
                    # A deadline breach is not worth retrying: the sensor is
                    # not producing frames, it has to be re-initialised.
                    break
                except Exception as e:
                    read_err = str(e)
                    # "Too many retries" = the sensor produces data faster than
                    # the I2C bus can drain it -> drop one refresh step.
                    if "Too many retries" in str(e):
                        retry_overflow += 1
                        if retry_overflow >= 2:
                            import adafruit_mlx90640 as _am
                            current = getattr(mlx, "refresh_rate", None)
                            if current == _am.RefreshRate.REFRESH_4_HZ:
                                mlx.refresh_rate = _am.RefreshRate.REFRESH_2_HZ
                                print("[Thermal] frame retries exceeded - dropping the sensor to 2 Hz")
                            elif current == _am.RefreshRate.REFRESH_2_HZ:
                                mlx.refresh_rate = _am.RefreshRate.REFRESH_1_HZ
                                print("[Thermal] frame retries exceeded - dropping the sensor to 1 Hz")
                            retry_overflow = 0
                    time.sleep(0.1)
            deadline.disarm()
            if not read_ok:
                consec_fail += 1
                _thermal_capture_state["consecutive_failures"] = consec_fail
                _thermal_capture_state["phase"] = "quiet"
                _thermal_last_error = read_err
                _thermal_last_error_ts = time.monotonic()
                _thermal_capture_state["last_error"] = read_err
                # Re-initialise the sensor: immediately for a deadline
                # breach (the sensor answers but produces no frames),
                # otherwise after THERMAL_REINIT_AFTER consecutive errors.
                if timed_out or consec_fail >= THERMAL_REINIT_AFTER:
                    mlx, i2c, backoff_idx, reinit_err = _thermal_reinit(
                        mlx, i2c, deadline, backoff_idx
                    )
                    if reinit_err:
                        read_err = reinit_err
                        _thermal_last_error = reinit_err
                        _thermal_capture_state["last_error"] = reinit_err
                if timed_out or consec_fail >= THERMAL_ERROR_PUBLISH_AFTER:
                    jpg = None
                    if globals().get("_mjpeg_clients", 0):
                        jpg = _render_thermal_jpeg(None, 0, 0, lut, error_text=f"Read error: {read_err}")
                    with _thermal_lock:
                        if jpg is not None:
                            _thermal_jpeg = jpg
                        _thermal_frame = None
                        _thermal_status.update({"ok": False, "error": read_err, "fps": None})
                    _broadcast_thermal_frame({"type": "thermal_frame", "matrix": None, "error": read_err, "timestamp": datetime.now().isoformat()})
                continue

            consec_fail = 0
            _thermal_capture_state["consecutive_failures"] = 0
            arr = np.array(frame, dtype=np.float32).reshape((24, 32))
            # A frame that contains non-finite samples is NOT a measurement.
            # Those values are discarded with the whole frame and the
            # condition is reported - they must never reach the dashboard as
            # if they were real readings.
            invalid = int((~np.isfinite(arr)).sum())
            if invalid:
                _thermal_capture_state["invalid_samples"] = invalid
                _thermal_capture_state["phase"] = "quiet"
                invalid_err = (
                    f"{invalid} invalid (non-finite) sample(s) in the MLX90640 "
                    f"frame - the frame was discarded"
                )
                _thermal_last_error = invalid_err
                _thermal_last_error_ts = time.monotonic()
                _thermal_capture_state["last_error"] = invalid_err
                jpg = None
                if globals().get("_mjpeg_clients", 0):
                    jpg = _render_thermal_jpeg(None, 0, 0, lut, error_text=invalid_err)
                with _thermal_lock:
                    if jpg is not None:
                        _thermal_jpeg = jpg
                    _thermal_frame = None
                    _thermal_status.update({"ok": False, "error": invalid_err, "fps": None})
                _broadcast_thermal_frame({"type": "thermal_frame", "matrix": None, "error": invalid_err, "timestamp": datetime.now().isoformat()})
                continue
            _thermal_capture_state["invalid_samples"] = 0
            _thermal_capture_state["phase"] = "running"
            globals()["_last_matrix"] = arr.copy()  # for detection diagnostics
            mn, mx, mean = float(arr.min()), float(arr.max()), float(arr.mean())
            # Measure the real frame rate of the sensor feed
            _thermal_frame_times.append(time.monotonic())
            fps = _measured_fps()
            # The watchdog uses this to prove the sensor is still alive.
            _thermal_last_frame_ts = time.monotonic()
            _thermal_last_frame_iso = datetime.now().isoformat()
            _thermal_last_error = None
            # Only render the expensive MJPEG frame when someone is actually
            # watching /thermal_feed - nobody else consumes it (the dashboard
            # renders the raw matrix on a canvas instead).
            jpg = _render_thermal_jpeg(arr, mn, mx, lut) if globals().get("_mjpeg_clients", 0) else None
            # The human detection is computed OUTSIDE the shared lock: it is
            # the most expensive step and the lock is also taken by the
            # WebSocket broadcast, so holding it here would stall the live
            # channel.
            _hy, _hx = np.unravel_index(int(np.argmax(arr)), arr.shape)
            _detections_now = _track_humans(_detect_humans(arr))
            _detected_now = len(_detections_now) > 0
            with _thermal_lock:
                if jpg is not None:
                    _thermal_jpeg = jpg
                _frames = _thermal_status.get("frames", 0) + 1
                _thermal_status = {"ok": True, "error": None, "min_c": round(mn, 2), "max_c": round(mx, 2), "mean_c": round(mean, 2), "hotspot_x": int(_hx), "hotspot_y": int(_hy), "hotspot_c": round(float(arr[_hy, _hx]), 2), "resolution": "32x24", "address": THERMAL_I2C_ADDRESS_STR, "human_detected": _detected_now, "detection_boxes": _detections_now, "frames": _frames, "fps": fps}
                # Row-major 32x24 (768) raw temperatures, rounded to 0.01C -
                # exactly what the sensor reported, nothing interpolated.
                _thermal_frame = {
                    "type": "thermal_frame",
                    "matrix": [round(float(v), 2) for v in arr.flatten()],
                    "min_c": round(mn, 2),
                    "max_c": round(mx, 2),
                    "mean_c": round(mean, 2),
                    "hotspot_x": int(_hx),
                    "hotspot_y": int(_hy),
                    "hotspot_c": round(float(arr[_hy, _hx]), 2),
                    "frames": _frames,
                    "fps": fps,
                    "timestamp": datetime.now().isoformat(),
                }
                _broadcast_thermal_frame(_thermal_frame)
                # Real detection EVENT on false->true edge (no mock/simulated data).
                if _detected_now and not globals().get("_prev_detected", False):
                    _span = max(float(arr.max() - arr.min()), 1e-3)
                    _conf = round(float(np.clip((float(arr.max()) - float(arr.mean())) / _span, 0, 1) * 100), 0)
                    _event = {
                        "type": "detection",
                        "id": f"ALERT-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                        "timestamp": datetime.now().isoformat(),
                        "confidence": _conf,  # thermal signal strength (peak vs frame span), genuinely calculated
                        "thermal": {"min_c": round(mn, 2), "max_c": round(mx, 2), "mean_c": round(mean, 2),
                                    "hotspot": {"x": int(_hx), "y": int(_hy), "c": round(float(arr[_hy, _hx]), 2)}},
                        "sensor_status": f"MLX90640 @{THERMAL_I2C_ADDRESS_STR} OK",
                        "lat": None, "lng": None,  # no drone GPS hardware
                    }
                    _loop = globals().get("_main_loop")
                    if _loop is not None:
                        try:
                            asyncio.run_coroutine_threadsafe(manager.broadcast(_event), _loop)
                        except Exception:
                            pass
                globals()["_prev_detected"] = _detected_now
        except Exception as e:
            jpg = None
            _thermal_last_error = str(e)
            _thermal_last_error_ts = time.monotonic()
            if globals().get("_mjpeg_clients", 0):
                jpg = _render_thermal_jpeg(None, 0, 0, lut, error_text=f"Capture error: {e}")
            with _thermal_lock:
                if jpg is not None:
                    _thermal_jpeg = jpg
                _thermal_frame = None
                _thermal_status.update({"ok": False, "error": str(e), "fps": None})
            _broadcast_thermal_frame({"type": "thermal_frame", "matrix": None, "error": str(e), "timestamp": datetime.now().isoformat()})
            time.sleep(1.0)


_thermal_thread_started = False


def _ensure_thermal_thread():
    global _thermal_thread_started
    if not _thermal_thread_started:
        t = threading.Thread(target=_thermal_capture_loop, daemon=True, name="mlx90640-capture")
        t.start()
        _thermal_thread_started = True


@app.get("/api/thermal/status")
async def thermal_status():
    _ensure_thermal_thread()
    data = _thermal_status_snapshot()
    with _thermal_lock:
        # Include the latest raw frame so REST clients can render the very
        # first image before the first WebSocket frame arrives. The frame is
        # only served while the watchdog says it is still fresh.
        if _thermal_frame and data.get("ok"):
            data["matrix"] = _thermal_frame["matrix"]
    return data


@app.get("/api/system")
async def system_status():
    """Real Raspberry Pi system stats."""
    return _read_system_stats()


@app.get("/api/hardware")
async def hardware_status():
    """Real hardware/bus status (I2C/SPI/MLX90640/LoRa)."""
    return _read_hardware_status()


@app.get("/api/hardware/health")
async def hardware_health():
    """Per-device health of every registered hardware component.

    Each device reports the state that was actually measured (online / stale /
    offline) plus the reason and the timestamp of the last successful
    communication. The dashboard uses this to drive its status indicators.
    """
    _ensure_thermal_thread()
    return _read_device_health()


@app.get("/api/thermal/debug")
async def thermal_debug():
    """Detection diagnostics on the LIVE frame (person must be in front of sensor)."""
    import numpy as np
    a = globals().get("_last_matrix")
    if a is None:
        return {"error": "no frame yet"}
    mn, mx = float(a.min()), float(a.max())
    mean, std = float(a.mean()), float(a.std())
    # Otsu threshold (same as detector)
    hist, edges = np.histogram(a, bins=32, range=(mn, mx))
    centers = 0.5 * (edges[:-1] + edges[1:])
    w0 = np.cumsum(hist); w1 = hist.sum() - w0
    mu0 = np.cumsum(hist * centers); total_mu = mu0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mu1 = (total_mu - mu0) / np.where(w1 == 0, 1, w1)
        between = w0 * w1 * (mu0 / np.where(w0 == 0, 1, w0) - mu1) ** 2
    between[~np.isfinite(between)] = 0
    thr = float(centers[int(np.argmax(between))])
    gate = mean + max(1.5, 1.5 * std)
    try:
        from scipy import ndimage as _ndi
        lab, n = _ndi.label(a > thr)
        comps = []
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            comps.append({"area": len(xs), "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                          "peak_c": round(float(a[ys, xs].max()), 2), "mean_c": round(float(a[ys, xs].mean()), 2)})
        comps.sort(key=lambda c: -c["area"])
    except Exception as e:
        comps = [{"error": str(e)}]
    return {"min_c": round(mn, 2), "max_c": round(mx, 2), "mean_c": round(mean, 2), "std_c": round(std, 2),
            "otsu_thr": round(thr, 2), "gate": round(gate, 2), "peak_minus_mean": round(mx - mean, 2),
            "components": comps[:6], "detections": _detect_humans(a), "tracked": _track_humans(_detect_humans(a))}


@app.get("/api/hardware_status")
async def hardware_status_full():
    _ensure_thermal_thread()
    health = _read_device_health()
    return {
        "system": _read_system_stats(),
        "hardware": health["hardware"],
        "devices": health["devices"],
        "thermal": health["thermal"],
        "sensors": _read_sensor_status(),
        "lora_hardware": dict(_lora_hardware),
    }


@app.get("/api/sensors")
async def sensors_status():
    """Real hardware sensor readings (GPS, battery, barometer, compass, LoRa radio).

    Every entry reports whether the device was actually detected and, when it
    was, the measured values. Missing hardware is reported with a reason
    instead of a placeholder value.
    """
    return {
        "sensors": _read_sensor_status(),
        "lora_hardware": dict(_lora_hardware),
        "telemetry": dict(telemetry_data),
    }


@app.get("/thermal_feed")
async def thermal_feed():
    """MJPEG stream of real MLX90640 (32x24) thermal frames with colormap.

    DEPRECATED for browser use - the dashboard renders the thermal image on a
    canvas from the WebSocket matrix, it does not consume this stream.

    This endpoint streams live thermal imagery, so it requires a credential
    like every other protected surface (operator OR viewer token is enough -
    it is a read-only stream). The authentication is enforced by
    AERISAuthMiddleware BEFORE this handler runs, so an unauthenticated
    caller never reaches the generator and never starts the (expensive)
    per-frame JPEG rendering.
    """
    _ensure_thermal_thread()
    boundary = "frame"

    def generate():
        # Register this client while its stream is open: the capture loop
        # only renders the (expensive) JPEG while someone is actually watching.
        globals()["_mjpeg_clients"] = globals().get("_mjpeg_clients", 0) + 1
        try:
            # wait briefly for first frame
            for _ in range(40):
                with _thermal_lock:
                    jpg = _thermal_jpeg
                if jpg is not None:
                    break
                time.sleep(0.25)
            lut = None
            while True:
                with _thermal_lock:
                    jpg = _thermal_jpeg
                if jpg is None:
                    # should not happen after warmup - render an "unavailable"
                    # placeholder so connected MJPEG viewers see the real state
                    if lut is None:
                        lut = _build_thermal_lut()
                    jpg = _render_thermal_jpeg(None, 0, 0, lut, error_text="Waiting for sensor...")
                yield (b"--" + boundary.encode() + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                time.sleep(0.25)  # ~4 fps matches the 4 Hz sensor refresh
        finally:
            globals()["_mjpeg_clients"] = max(0, globals().get("_mjpeg_clients", 1) - 1)

    return StreamingResponse(generate(), media_type=f"multipart/x-mixed-replace; boundary={boundary}")


@app.on_event("startup")
async def startup():
    globals()["_main_loop"] = asyncio.get_running_loop()
    globals()["_prev_detected"] = False
    asyncio.create_task(_system_broadcaster())
    _ensure_thermal_thread()
    # Keep the I2C/SPI inventory fresh in its own thread: the scan is a
    # subprocess and must never block the asyncio event loop (that would
    # stall the live WebSocket frames and the dashboard would look frozen).
    # The first scan runs synchronously here so the very first broadcast
    # already carries a real inventory.
    _refresh_bus_cache()
    threading.Thread(target=_bus_monitor_loop, daemon=True, name="aeris-bus-monitor").start()
    print("[Bus] I2C/SPI monitor started")
    # Real CPU utilisation sampler (the load average is not a percentage).
    threading.Thread(target=_cpu_monitor_loop, daemon=True, name="aeris-cpu-monitor").start()
    # Start the ground-side LoRa receiver (real hardware; starts in real
    # RX CONTINUOUS mode on 433 MHz; safe with no drone transmitting).
    # A second instance on the same Pi would fight the primary for the radio
    # (one NSS line, one FIFO), so secondary instances must not claim it.
    #
    # ORDER MATTERS: the receiver is started BEFORE the sensor poller. The
    # poller probes the radio identity (including Reg 0x39, the sync word)
    # straight away - if it ran first it would read whatever the radio still
    # held from a previous process and report a stale sync word until the
    # next 60 s re-probe.
    _skip_lora_rx = (os.environ.get("AERIS_SKIP_LORA_RX") or "").strip().lower() in ("1", "true", "yes")
    if _lora_rx_svc is not None and _skip_lora_rx:
        print("[LoRaRX] skipped (AERIS_SKIP_LORA_RX set - another instance owns the radio)")
    elif _lora_rx_svc is not None:
        try:
            started = _lora_rx_svc.start()
            if started:
                _lora_status = _lora_rx_svc.get_status()
                _lora_sync = _lora_status.get("sync_word")
                print(
                    "[LoRaRX] ground-side receiver started - listening on 433 MHz "
                    "(SF6/BW500, CR4/5, sync "
                    f"{_lora_sync if _lora_sync else 'n/a'}, matching the drone transmitter)"
                )
            else:
                print(f"[LoRaRX] not started: {_lora_rx_svc.get_status().get('reason')}")
        except Exception as _lrx_err:
            print(f"[LoRaRX] start failed: {_lrx_err}")
    # Start the real hardware sensor poller (GPS, battery, barometer, compass,
    # LoRa radio identity). It publishes into telemetry_data and the WebSocket.
    if _SENSORS_AVAILABLE:
        threading.Thread(target=_sensor_poll_loop, daemon=True, name="aeris-sensors").start()
        print("[Sensors] hardware sensor poller started")


@app.on_event("shutdown")
async def shutdown():
    if _lora_rx_svc is not None:
        try:
            _lora_rx_svc.stop()
        except Exception:
            pass

# ── Local database endpoints (detection history + LoRa packet stats) ──────────
@app.get("/api/detections/history")
async def get_detection_history(limit: int = 100, offset: int = 0):
    if not _DB_AVAILABLE:
        return {"error": "database unavailable", "detections": []}
    return {"detections": _db.get_detections(limit, offset), "total": _db.detection_count()}


@app.post("/api/detections/history")
async def add_detection_record(payload: dict):
    if not _DB_AVAILABLE:
        return {"status": "error", "reason": "database unavailable"}
    ok = _db.add_detection(payload)
    return {"status": "ok" if ok else "duplicate", "inserted": ok}


@app.get("/api/lora/stats")
async def get_lora_stats():
    if not _DB_AVAILABLE:
        return {"error": "database unavailable"}
    return _db.lora_stats()


@app.post("/api/lora/packet")
async def log_lora_packet(payload: dict):
    if not _DB_AVAILABLE:
        return {"status": "error", "reason": "database unavailable"}
    _db.add_lora_packet(payload)
    return {"status": "ok"}


# ── LoRa ground-side RX status ───────────────────────────────────────────────
@app.get("/api/lora/rx_status")
async def lora_rx_status():
    if _lora_rx_svc is None:
        return {"hardware_available": False, "running": False, "reason": "LoRa RX service not initialized"}
    return _lora_rx_svc.get_status()


# ── Weather (offline-aware) ──────────────────────────────────────────────────
@app.get("/api/weather")
async def get_weather():
    if not _WEATHER_AVAILABLE:
        return {"status": "unavailable", "reason": "weather cache unavailable", "data": None, "is_live": False}
    return _wc.get_weather()


@app.post("/api/weather")
async def update_weather(payload: dict):
    if not _WEATHER_AVAILABLE:
        return {"status": "error", "reason": "weather cache unavailable"}
    _wc.update_weather(payload.get("data", payload), payload.get("source", "manual"))
    return {"status": "ok"}


# ── SPA fallback (must be registered LAST, after all API/WS/thermal routes) ──
_FRONTEND_DIST_CHECK = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "..", "frontend", "dist")
)
if _os.path.isdir(_FRONTEND_DIST_CHECK):
    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        # Never intercept real API/WS/thermal/health routes
        if full_path.startswith(("api/", "ws", "thermal_feed", "health")):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        index = _os.path.join(_FRONTEND_DIST_CHECK, "index.html")
        if _os.path.exists(index):
            return FileResponse(index)
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

if __name__ == "__main__":
    import uvicorn
    # Port 80 for direct hotspot access (http://192.168.4.1)
    # Falls back to 8000 if port 80 is unavailable
    import os
    _port = int(os.environ.get("AERIS_PORT", "80"))
    uvicorn.run(app, host="0.0.0.0", port=_port)
