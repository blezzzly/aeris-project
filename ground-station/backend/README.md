# AERIS Backend - FastAPI

WebSocket server for AERIS.

---

## Setup

```bash
# Create virtual environment
python -m venv venv

# Activate
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running

```bash
# Development (with hot reload)
uvicorn main:app --reload --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

# Or simply
python main.py
```

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ws` | WebSocket | Real-time data |
| `/api/telemetry` | GET | Current UAV telemetry |
| `/api/detections` | GET | All detections |
| `/api/detection` | POST | Add new detection |
| `/api/mission` | GET | Current mission info |
| `/thermal_feed` | GET | MJPEG stream of the live MLX90640 thermal frames |

### Authentication

| Path | Credential required |
|------|--------------------|
| `/`, `/assets/*`, `/health` | no (LAN clients only) |
| `/api/*` | **yes** - operator or viewer bearer token |
| `/thermal_feed` | **yes** - operator or viewer bearer token |

The token is sent as `Authorization: Bearer <token>` - never in a URL. Read
requests accept the operator or the viewer token; write requests
(`POST`/`PUT`/`DELETE`) require the **operator** token.

`/thermal_feed` streams live thermal imagery, so it is protected like every
other data surface: an unauthenticated request is rejected with **401**
*before* the streaming generator starts, so it never triggers the (expensive)
per-frame JPEG rendering.

> The dashboard does **not** use `/thermal_feed` - it renders the thermal image
> on a canvas from the WebSocket matrix (`thermal_frame`). The endpoint stays
> available for programmatic clients and is deprecated for browser use.

## WebSocket Messages

**Client → Server:**
```json
{
  "type": "detection",
  "confidence": 0.92,
  "lat": 14.2515,
  "lng": 120.7310,
  "altitude": 120,
  "distance": 120
}
```

**Server → Client (Telemetry):**
```json
{
  "type": "telemetry",
  "altitude": 120.4,
  "speed": 8.2,
  "heading": 218,
  "battery": 78,
  "lat": 14.2500,
  "lng": 120.7300
}
```

**Server → Client (Detection):**
```json
{
  "type": "detection",
  "id": "ALERT-20260904-123456",
  "timestamp": "2026-09-04T12:34:56",
  "confidence": 92,
  "lat": 14.2515,
  "lng": 120.7310,
  "altitude": 120,
  "distance": 120
}
```

## Testing

```bash
# Test WebSocket
python -c "
import websocket
import json
ws = websocket.create_connection('ws://localhost:8000/ws')
ws.send(json.dumps({'type': 'detection', 'confidence': 0.95, 'lat': 14.25, 'lng': 120.73, 'altitude': 120, 'distance': 100}))
print(ws.recv())
ws.close()
"

# Test REST API
curl http://localhost:8000/api/telemetry
```
