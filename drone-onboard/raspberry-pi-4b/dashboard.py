"""
dashboard.py - Ground Station Dashboard (Raspberry Pi 4 Model B)

Flask-based web dashboard that serves as the DRRM command centre display.
Shows the live USB camera feed, detection status, vital signs, packet history,
and system logs with real-time Server-Sent Events updates.

Runs in its own thread so it does not block the main detection pipeline.

Device metadata (device name, CPU, RAM, LoRa mode) is imported from config.py
and surfaced on the dashboard's System Information panel.
"""

import time
import threading
import logging

import json as _json
import cv2
import numpy as np
from flask import Flask, Response, render_template_string, jsonify

from utils import timestamp_now, DASHBOARD_HOST, DASHBOARD_PORT
from config import (
    DEVICE_NAME, DEVICE_VERSION, CPU_MODEL, RAM_SIZE,
    STATUS, LORA_MODE, CAMERA_NAME,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Shared State (thread-safe)
# ═══════════════════════════════════════════════════════════════════════

class DashboardState:
    """Holds all state the dashboard displays — written by main, read by Flask."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None               # latest camera frame (BGR)
        self.thermal_frame = None      # latest thermal frame (BGR display)
        self.detections: list[dict] = []
        self.vital_signs: dict | None = None
        self.latest_packet: dict | None = None
        self.packet_count = 0
        self.detection_history: list[dict] = []
        self.connection_status = "Connected"
        self.lora_status = "Idle"
        self.fps = 0.0
        self.logs: list[str] = []

    def add_log(self, message: str):
        from utils import timestamp_short
        entry = f"[{timestamp_short()}] {message}"
        with self.lock:
            self.logs.append(entry)
            if len(self.logs) > 200:
                self.logs.pop(0)

    def update_frame(self, frame: np.ndarray):
        with self.lock:
            self.frame = frame.copy()

    def update_thermal_frame(self, frame: np.ndarray):
        with self.lock:
            self.thermal_frame = frame.copy()

    def get_frame(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def get_thermal_frame(self):
        with self.lock:
            return None if self.thermal_frame is None else self.thermal_frame.copy()

    def update_detection(self, detections: list, vital_signs: dict | None,
                         packet: dict | None, packet_count: int):
        with self.lock:
            self.detections = list(detections)
            self.vital_signs = vital_signs
            self.latest_packet = packet
            self.packet_count = packet_count
            if detections:
                best = max(detections, key=lambda d: d["confidence"])
                self.detection_history.append({
                    "timestamp": timestamp_now(),
                    "confidence": best["confidence"],
                    "vital_signs": vital_signs,
                })
                if len(self.detection_history) > 100:
                    self.detection_history.pop(0)

    def get_status(self) -> dict:
        """Snapshot of all display values for JSON serialisation."""
        with self.lock:
            person_detected = len(self.detections) > 0
            best_conf = max(d["confidence"] for d in self.detections
                           ) if self.detections else 0.0
            vs = self.vital_signs or {}

            return {
                "person_detected": person_detected,
                "confidence": best_conf,
                "fps": round(self.fps, 1),
                "packet_count": self.packet_count,
                "connection_status": self.connection_status,
                "lora_status": self.lora_status,
                "vital_signs": vs,
                "latest_packet": self.latest_packet,
                "detection_history": list(self.detection_history[-20:]),
                "logs": list(self.logs[-30:]),
                # Raspberry Pi 4 Model B device metadata
                "device_name": DEVICE_NAME,
                "device_version": DEVICE_VERSION,
                "cpu_model": CPU_MODEL,
                "ram_size": RAM_SIZE,
                "device_status": STATUS,
                "lora_mode": LORA_MODE,
                "camera_name": CAMERA_NAME,
            }


# Global instance shared with main.py
state = DashboardState()


# ═══════════════════════════════════════════════════════════════════════
# Flask Application
# ═══════════════════════════════════════════════════════════════════════

app = Flask(__name__)


# ── HTML Template ─────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DRRM Ground Station — Raspberry Pi 4 Model B</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Consolas','Courier New',monospace;background:#0a0e14;color:#b3b8c3}
.header{background:linear-gradient(135deg,#001529,#0d2137);border-bottom:2px solid #1e88e5;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:20px;color:#64b5f6;letter-spacing:3px;text-transform:uppercase}
.header .badge{background:#1e88e5;color:#fff;padding:4px 14px;border-radius:12px;font-size:11px;letter-spacing:1px}
.dashboard{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px;max-width:1600px;margin:0 auto}
.panel{background:#111b27;border:1px solid #1a2d42;border-radius:8px;padding:14px;min-height:100px}
.panel h2{font-size:12px;color:#5c8db5;text-transform:uppercase;letter-spacing:2px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #1a2d42}
.camera-container{position:relative;background:#000;border-radius:4px;overflow:hidden;min-height:320px}
.camera-container img{width:100%;display:block}
.camera-overlay{position:absolute;top:8px;left:8px;right:8px;display:flex;justify-content:space-between;pointer-events:none}
.overlay-badge{background:rgba(0,0,0,.75);padding:3px 10px;border-radius:4px;font-size:12px;color:#fff;border:1px solid rgba(255,255,255,.1)}
.overlay-badge.detected{border-color:#4caf50}
.overlay-badge.no-target{border-color:#f44336}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.info-item{background:#0d1a26;border:1px solid #1a2d42;border-radius:6px;padding:10px 12px}
.info-item .label{font-size:10px;color:#5c8db5;text-transform:uppercase;letter-spacing:1px}
.info-item .value{font-size:18px;font-weight:bold;color:#e8edf5;margin-top:4px}
.info-item .value.green{color:#4caf50}
.info-item .value.red{color:#f44336}
.info-item .value.yellow{color:#ff9800}
.info-item .value.cyan{color:#26c6da}
.table-wrap{max-height:220px;overflow-y:auto}
.table-wrap::-webkit-scrollbar{width:6px}
.table-wrap::-webkit-scrollbar-track{background:#0d1a26}
.table-wrap::-webkit-scrollbar-thumb{background:#1a2d42;border-radius:3px}
table{width:100%;border-collapse:collapse;font-size:11px}
table th{background:#0d1a26;color:#5c8db5;padding:6px 8px;text-align:left;text-transform:uppercase;font-size:10px;letter-spacing:1px;position:sticky;top:0}
table td{padding:5px 8px;border-bottom:1px solid #0d1a26}
table tr:hover td{background:#0d2137}
.log-wrap{max-height:130px;overflow-y:auto;font-size:11px;line-height:1.5}
.log-wrap::-webkit-scrollbar{width:6px}
.log-wrap::-webkit-scrollbar-track{background:#0d1a26}
.log-wrap::-webkit-scrollbar-thumb{background:#1a2d42;border-radius:3px}
.log-entry{padding:2px 4px;color:#7899b0}
.log-entry:nth-child(odd){background:rgba(255,255,255,.02)}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.status-dot.online{background:#4caf50;box-shadow:0 0 6px #4caf50}
.status-dot.offline{background:#f44336;box-shadow:0 0 6px #f44336}
.status-dot.active{background:#ff9800;box-shadow:0 0 6px #ff9800}
@media(max-width:900px){.dashboard{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
<h1>&#x1f6f0; DRRM Ground Station</h1>
<span class="badge">Packet #<span id="packetCount">0</span></span>
</div>
<div class="dashboard">
<div>
<div class="panel">
<h2>&#x1f4f7; Live Camera Feed</h2>
<div class="camera-container">
<img src="/video_feed" alt="Live Feed" id="cameraFeed">
<div class="camera-overlay">
<span class="overlay-badge" id="fpsBadge">FPS: 0</span>
<span class="overlay-badge no-target" id="detectionBadge">NO TARGET</span>
</div>
</div>
</div>
<div class="panel">
<h2>&#x23f1; Detection History</h2>
<div class="table-wrap"><table><thead><tr><th>Time</th><th>Conf</th><th>HR</th><th>RR</th><th>SpO&#x2082;</th><th>Temp</th></tr></thead><tbody id="historyTable"></tbody></table></div>
</div>
</div>
<div>
<div class="panel">
<h2>&#x1f4e1; System Information</h2>
<div class="info-grid">
<div class="info-item"><div class="label">Device</div><div class="value cyan" id="deviceName">--</div></div>
<div class="info-item"><div class="label">CPU</div><div class="value" id="cpuModel">--</div></div>
<div class="info-item"><div class="label">RAM</div><div class="value" id="ramSize">--</div></div>
<div class="info-item"><div class="label">Status</div><div class="value green" id="deviceStatus">--</div></div>
<div class="info-item"><div class="label">LoRa</div><div class="value yellow" id="loraMode">--</div></div>
<div class="info-item"><div class="label">Camera</div><div class="value" id="cameraName">--</div></div>
</div>
</div>
<div class="panel">
<h2>&#x1f4e1; Link Status</h2>
<div class="info-grid">
<div class="info-item"><div class="label">LoRa Status</div><div class="value" id="loraStatus"><span class="status-dot offline"></span>Disconnected</div></div>
<div class="info-item"><div class="label">Connection</div><div class="value" id="connectionStatus"><span class="status-dot offline"></span>Disconnected</div></div>
<div class="info-item"><div class="label">Packet Count</div><div class="value cyan" id="packetCountValue">0</div></div>
<div class="info-item"><div class="label">Last TX</div><div class="value" id="lastTransmission">--:--:--</div></div>
</div>
</div>
<div class="panel">
<h2>&#x2764; Vital Signs</h2>
<div class="info-grid">
<div class="info-item"><div class="label">Heart Rate</div><div class="value green" id="heartRate">-- bpm</div></div>
<div class="info-item"><div class="label">Respiration</div><div class="value green" id="respiration">-- br/min</div></div>
<div class="info-item"><div class="label">SpO&#x2082;</div><div class="value cyan" id="spo2">-- %</div></div>
<div class="info-item"><div class="label">Temperature</div><div class="value yellow" id="temperature">-- &#xb0;C</div></div>
</div>
</div>
<div class="panel">
<h2>&#x1f4ca; Latest Detection</h2>
<div class="info-grid">
<div class="info-item"><div class="label">Timestamp</div><div class="value" id="detectionTimestamp">--:--:--</div></div>
<div class="info-item"><div class="label">Confidence</div><div class="value green" id="detectionConfidence">0%</div></div>
<div class="info-item"><div class="label">Status</div><div class="value" id="detectionStatus"><span class="status-dot offline"></span>No Target</div></div>
<div class="info-item"><div class="label">People</div><div class="value cyan" id="peopleCount">0</div></div>
</div>
</div>
<div class="panel">
<h2>&#x1f4dc; System Log</h2>
<div class="log-wrap" id="logContainer"></div>
</div>
</div>
</div>
<script>
const es=new EventSource('/events');
es.onmessage=function(e){
const d=JSON.parse(e.data);
document.getElementById('packetCount').textContent=d.packet_count;
document.getElementById('packetCountValue').textContent=d.packet_count;
document.getElementById('fpsBadge').textContent='FPS: '+d.fps;
const db=document.getElementById('detectionBadge');
if(d.person_detected){db.textContent='DETECTED '+(d.confidence*100).toFixed(0)+'%';db.className='overlay-badge detected'}
else{db.textContent='NO TARGET';db.className='overlay-badge no-target'}
var le=document.getElementById('loraStatus');
if(d.lora_status==='Transmitting')le.innerHTML='<span class="status-dot active"></span>Transmitting';
else if(d.connection_status==='Connected')le.innerHTML='<span class="status-dot online"></span>Connected';
else le.innerHTML='<span class="status-dot offline"></span>Disconnected';
var ce=document.getElementById('connectionStatus');
if(d.connection_status==='Connected')ce.innerHTML='<span class="status-dot online"></span>Connected';
else ce.innerHTML='<span class="status-dot offline"></span>Disconnected';
if(d.latest_packet&&d.latest_packet.timestamp)document.getElementById('lastTransmission').textContent=d.latest_packet.timestamp;
var vs=d.vital_signs||{};
document.getElementById('heartRate').textContent=vs.heart_rate?vs.heart_rate.toFixed(1)+' bpm':'-- bpm';
document.getElementById('respiration').textContent=vs.respiration?vs.respiration.toFixed(1)+' br/min':'-- br/min';
document.getElementById('spo2').textContent=vs.spo2?vs.spo2.toFixed(1)+' %':'-- %';
document.getElementById('temperature').textContent=vs.temperature?vs.temperature.toFixed(1)+' &#xb0;C':'-- &#xb0;C';
if(d.latest_packet){document.getElementById('detectionTimestamp').textContent=d.latest_packet.timestamp||'--:--:--';var dc=d.latest_packet.detection?d.latest_packet.detection.confidence||0:0;document.getElementById('detectionConfidence').textContent=(dc*100).toFixed(0)+'%'}
if(d.person_detected){document.getElementById('detectionStatus').innerHTML='<span class="status-dot online"></span>Target Acquired';document.getElementById('peopleCount').textContent='1'}
else{document.getElementById('detectionStatus').innerHTML='<span class="status-dot offline"></span>No Target';document.getElementById('peopleCount').textContent='0'}
if(d.detection_history&&d.detection_history.length){var h='',a=d.detection_history.slice().reverse();for(var i=0;i<a.length;i++){var e2=a[i],cf=(e2.confidence*100).toFixed(0),vs2=e2.vital_signs||{},hr=vs2.heart_rate?vs2.heart_rate.toFixed(0):'--',rr=vs2.respiration?vs2.respiration.toFixed(0):'--',so=vs2.spo2?vs2.spo2.toFixed(0):'--',tp=vs2.temperature?vs2.temperature.toFixed(1):'--';h+='<tr><td>'+(e2.timestamp||'--')+'</td><td>'+cf+'%</td><td>'+hr+'</td><td>'+rr+'</td><td>'+so+'</td><td>'+tp+'</td></tr>'}
document.getElementById('historyTable').innerHTML=h}
if(d.logs&&d.logs.length){var lc=document.getElementById('logContainer'),lh='';for(var i=0;i<d.logs.length;i++)lh+='<div class="log-entry">'+d.logs[i]+'</div>';lc.innerHTML=lh;lc.scrollTop=lc.scrollHeight}
document.getElementById('deviceName').textContent=d.device_name||'--';
document.getElementById('cpuModel').textContent=d.cpu_model||'--';
document.getElementById('ramSize').textContent=d.ram_size||'--';
document.getElementById('deviceStatus').textContent=d.device_status||'--';
document.getElementById('loraMode').textContent=d.lora_mode||'--';
document.getElementById('cameraName').textContent=d.camera_name||'--';
};
es.onerror=function(){};
</script>
</body>
</html>"""


# ── Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/video_feed")
def video_feed():
    """MJPEG stream of the live camera feed with bounding boxes overlaid."""
    def generate():
        while True:
            frame = state.get_frame()
            if frame is not None:
                with state.lock:
                    detections = list(state.detections)
                # Draw bounding boxes on the frame for the stream
                for d in detections:
                    bbox = d.get("bbox")
                    if bbox:
                        try:
                            x1, y1, x2, y2 = [int(v) for v in bbox]
                            conf = d.get("confidence", 0.0)
                            cv2.rectangle(frame, (x1, y1), (x2, y2),
                                          (0, 255, 0), 2)
                            cv2.putText(
                                frame, f"Person {conf*100:.0f}%",
                                (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (0, 255, 0), 2, cv2.LINE_AA,
                            )
                        except Exception:
                            pass
                # Overlay label
                h, w = frame.shape[:2]
                cv2.putText(frame, "DRRM GROUND STATION — Raspberry Pi 4 Model B",
                            (10, h - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (100, 180, 255), 1, cv2.LINE_AA)

                ret, jpeg = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70]
                )
                if ret:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" +
                        jpeg.tobytes() + b"\r\n"
                    )
            time.sleep(0.033)  # ~30 fps max
    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/thermal_feed")
def thermal_feed():
    """MJPEG stream of the MLX90640 thermal frame with thermal colormap overlay."""

    def generate():
        while True:
            frame = state.get_thermal_frame()
            if frame is not None:
                # Thermal frame: float32 matrix (24x32) -> normalized uint8 -> BGR display
                thermal = frame.copy()
                min_t = float(np.min(thermal))
                max_t = float(np.max(thermal))
                norm = np.clip(
                    (thermal - min_t) / (max_t - min_t + 1e-5) * 255,
                    0, 255,
                ).astype(np.uint8)
                display = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
                h, w = display.shape[:2]
                cv2.putText(
                    display, "DRRM GROUND STATION — THERMAL (MLX90640)",
                    (10, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
                )
                ret, jpeg = cv2.imencode(
                    ".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 70]
                )
                if ret:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" +
                        jpeg.tobytes() + b"\r\n"
                    )
            time.sleep(0.033)  # ~30 fps max

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/events")
def events():
    """Server-Sent Events endpoint pushing JSON status updates ~4×/s."""
    def event_stream():
        last_count = -1
        while True:
            status = state.get_status()
            # Always send for smooth UI updates
            yield f"data: {_json.dumps(status)}\n\n"
            time.sleep(0.25)
    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/status")
def api_status():
    """JSON endpoint for polling-based clients."""
    return jsonify(state.get_status())


# ── Server Runner ────────────────────────────────────────────────────────

def run_dashboard(host: str = "0.0.0.0", port: int = 5000):
    """Run the Flask dashboard server in the current thread (blocking)."""
    import logging as _flog
    _flog.getLogger("werkzeug").setLevel(_flog.WARNING)

    logger.info("[Dashboard] Ground station UI → http://%s:%s", host, port)
    print(f"\n  [Dashboard] Ground station UI → http://{host if host != '0.0.0.0' else 'localhost'}:{port}")
    print(f"  [Dashboard] Open this URL in your browser to see the DRRM command centre.\n")

    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
