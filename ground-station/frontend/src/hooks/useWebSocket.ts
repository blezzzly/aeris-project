import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import { getWebSocketUrl } from '../lib/api';
import type { UAVTelemetry, SensorStatus, LoraHardware, SystemStats, HardwareStatus, ThermalStatus, ThermalFrame, LoraRxStatus, DeviceHealthMap } from '../types';

// The URL is resolved by ../lib/api so it always points at the backend that is
// actually serving this page (same origin in production, :8000 in dev mode).
// No token in the URL - it is sent in the auth handshake frame so it never lands in logs.

/** Close code used by the backend when no credential was presented yet. */
const WS_CLOSE_AUTH_REQUIRED = 4401;
/** Close code used by the backend when the presented credential was rejected. */
const WS_CLOSE_UNAUTHORIZED = 1008;

/** Reconnect delays (ms) used when the link drops - grows slowly, capped. */
const RECONNECT_DELAYS = [1000, 2000, 3000, 5000, 8000];

/** Custom event dispatched by the app whenever the stored token changes. */
export const AUTH_CHANGED_EVENT = 'aeris-auth-changed';

/** Notify the WebSocket layer that the credential in localStorage changed. */
export function notifyAuthChanged(): void {
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function useWebSocket(url: string = getWebSocketUrl()) {
  const wsRef = useRef<WebSocket | null>(null);
  const { addDetection, setHardwareData, setTelemetry, setLoraRx, setThermalFrame, setDevices, setLinkState } =
    useAppStore();
  // The connection state lives here, next to the socket lifecycle, so it is
  // always in sync with the real socket.
  const [connected, setConnected] = useState(false);
  // Timestamp of the last message that arrived on the socket. The dashboard
  // uses it to tell a live link from a stale one.
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    let attempt = 0;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    /** Close the current socket (if any) without scheduling a reconnect. */
    const dropSocket = () => {
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        // Detach the handlers first so the close does not trigger a retry.
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };

    /** Open the socket - no-op while one is already open/connecting. */
    const connect = () => {
      if (disposed) return;
      const existing = wsRef.current;
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return;
      }
      // The backend needs a credential for the live channel. Without one
      // there is nothing to authenticate with - connecting anyway would only
      // produce an "auth required" rejection and leave the dashboard
      // permanently offline. Wait until the operator signs in.
      const token = localStorage.getItem('aeris_token') ?? '';
      if (!token) {
        setConnected(false);
        setLinkState('offline');
        return;
      }
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        console.error('WebSocket connection error:', err);
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        // Auth handshake: first frame carries the access token (never in the URL).
        ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('aeris_token') ?? '' }));
        console.log('WebSocket connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setLastMessageAt(Date.now());
          setLinkState('live');

          if (data.type === 'auth_ok') {
            // The handshake really succeeded - the live channel is authorised.
            attempt = 0;
            setConnected(true);
          } else if (data.type === 'telemetry') {
            // Only accept concrete numeric readings from the backend. Flight telemetry
            // fields are null until a real sensor source reports (see backend telemetry_data),
            // and unavailable readings must not clobber the last known-good values.
            const updates: Partial<UAVTelemetry> = {};
            if (typeof data.altitude === 'number') updates.altitude = data.altitude;
            if (typeof data.speed === 'number') updates.speed = data.speed;
            if (typeof data.heading === 'number') updates.heading = data.heading;
            if (typeof data.battery === 'number') updates.battery = data.battery;
            if (typeof data.battery_voltage === 'number') updates.battery_voltage = data.battery_voltage;
            if (typeof data.lat === 'number') updates.lat = data.lat;
            if (typeof data.lng === 'number') updates.lng = data.lng;
            if (typeof data.connected === 'boolean') updates.connected = data.connected;
            if (typeof data.source === 'string') updates.source = data.source;
            if (typeof data.last_update === 'string') updates.last_update = data.last_update;
            if (data.unavailable_reason === null || typeof data.unavailable_reason === 'string') {
              updates.unavailable_reason = data.unavailable_reason;
            }
            updates.positionLive = typeof data.lat === 'number' && typeof data.lng === 'number';

            if (Object.keys(updates).length > 0) {
              setTelemetry(updates);
            }
          } else if (data.type === 'system') {
            // Live hardware snapshot: system stats, buses, thermal camera,
            // sensor availability and the LoRa radio identity.
            setHardwareData({
              system: (data.system as SystemStats) ?? null,
              hardware: (data.hardware as HardwareStatus) ?? null,
              thermal: (data.thermal as ThermalStatus) ?? null,
              sensors: (data.sensors as SensorStatus) ?? undefined,
              loraHardware: (data.lora_hardware as LoraHardware) ?? null,
            });
            // Per-device health (online / stale / offline) - the dashboard
            // renders its status indicators straight from this.
            if (data.devices) {
              setDevices(data.devices as DeviceHealthMap);
            }
            // Ground-side receiver stats (rx_count, RSSI/SNR, last RX
            // timestamp) travel nested in the same broadcast - surface them
            // so the LoRa panels show real reception telemetry.
            if (data.lora_rx) {
              setLoraRx(data.lora_rx as LoraRxStatus);
            }

            // Telemetry is part of the same broadcast so the panels that show
            // flight data refresh together with the hardware panels.
            if (data.telemetry) {
              const t = data.telemetry as Partial<UAVTelemetry>;
              const updates: Partial<UAVTelemetry> = {};
              if (typeof t.altitude === 'number') updates.altitude = t.altitude;
              if (typeof t.speed === 'number') updates.speed = t.speed;
              if (typeof t.heading === 'number') updates.heading = t.heading;
              if (typeof t.battery === 'number') updates.battery = t.battery;
              if (typeof t.battery_voltage === 'number') updates.battery_voltage = t.battery_voltage;
              if (typeof t.lat === 'number') updates.lat = t.lat;
              if (typeof t.lng === 'number') updates.lng = t.lng;
              if (typeof t.connected === 'boolean') updates.connected = t.connected;
              if (typeof t.source === 'string') updates.source = t.source;
              if (typeof t.last_update === 'string') updates.last_update = t.last_update;
              if (t.unavailable_reason === null || typeof t.unavailable_reason === 'string') {
                updates.unavailable_reason = t.unavailable_reason;
              }
              updates.positionLive = typeof t.lat === 'number' && typeof t.lng === 'number';
              if (Object.keys(updates).length > 0) setTelemetry(updates);
            }
          } else if (data.type === 'thermal_frame') {
            // Live MLX90640 frame pushed by the backend capture loop.
            // matrix: null -> sensor offline/unavailable right now.
            if (data.matrix == null) {
              setThermalFrame(null);
            } else if (Array.isArray(data.matrix) && data.matrix.length === 768) {
              const frame: ThermalFrame = {
                matrix: data.matrix as number[],
                min_c: typeof data.min_c === 'number' ? data.min_c : null,
                max_c: typeof data.max_c === 'number' ? data.max_c : null,
                mean_c: typeof data.mean_c === 'number' ? data.mean_c : null,
                hotspot_x: typeof data.hotspot_x === 'number' ? data.hotspot_x : null,
                hotspot_y: typeof data.hotspot_y === 'number' ? data.hotspot_y : null,
                hotspot_c: typeof data.hotspot_c === 'number' ? data.hotspot_c : null,
                frames: typeof data.frames === 'number' ? data.frames : null,
                fps: typeof data.fps === 'number' ? data.fps : null,
                error: typeof data.error === 'string' ? data.error : null,
                timestamp: typeof data.timestamp === 'string' ? data.timestamp : null,
                receivedAt: Date.now(),
              };
              setThermalFrame(frame);
            }
          } else if (data.type === 'lora_rx') {
            setLoraRx(data.status ?? null);
          } else if (data.type === 'detection') {
            // A detection without a real GPS fix is NOT dropped: it is stored
            // with lat/lng = null and the UI renders "GPS unavailable".
            // Number(null) === 0 used to make missing coordinates render as
            // "0.0000° N, 0.0000° E" — that is fabrication. Coordinates are only
            // accepted when they are actual finite numbers and not the (0, 0) null-island.
            const rawLat = data.lat;
            const rawLng = data.lng;
            const parsedLat = typeof rawLat === 'number' && Number.isFinite(rawLat) ? rawLat : null;
            const parsedLng = typeof rawLng === 'number' && Number.isFinite(rawLng) ? rawLng : null;
            const validFix = parsedLat !== null && parsedLng !== null && !(parsedLat === 0 && parsedLng === 0);
            const confidence = Number(data.confidence);
            // The hotspot is the MLX90640 pixel that produced the detection -
            // real sensor data that gives the detection direction.
            const hs = (data as any).thermal?.hotspot;
            addDetection({
              id: data.id,
              timestamp: new Date(data.timestamp).toLocaleTimeString(),
              confidence: Number.isFinite(confidence) ? confidence : 0,
              lat: validFix ? parsedLat : null,
              lng: validFix ? parsedLng : null,
              altitude: Number.isFinite(Number(data.altitude)) ? Number(data.altitude) : null,
              distance: Number.isFinite(Number(data.distance)) ? Number(data.distance) : null,
              status: 'new',
              // A position is only marked 'gps' when the source reported one.
              positionSource: validFix ? 'gps' : 'thermal',
              hotspot:
                hs && Number.isFinite(Number(hs.x))
                  ? { x: Number(hs.x), y: Number(hs.y), c: Number(hs.c) }
                  : null,
            });
          }
        } catch (err) {
          console.error('WS parse error:', err);
        }
      };

      ws.onerror = (err) => {
        console.error('WS error:', err);
        setConnected(false);
      };

      ws.onclose = (ev) => {
        setConnected(false);
        if (ev.code === WS_CLOSE_UNAUTHORIZED) {
          // The presented credential was rejected: drop it and send the user
          // back to the login screen.
          localStorage.removeItem('aeris_token');
          window.dispatchEvent(new Event('aeris-auth-failed'));
          setLinkState('offline');
          return;
        }
        if (ev.code === WS_CLOSE_AUTH_REQUIRED) {
          // No credential yet - the operator has to sign in. This is not a
          // failure, so the socket is simply left closed and reopened as
          // soon as a token appears.
          setLinkState('offline');
          return;
        }
        console.log('WebSocket disconnected, reconnecting...');
        setLinkState('offline');
        scheduleReconnect();
      };
    };

    /** Reconnect with a growing delay: covers a Wi-Fi drop or a short
     *  communication interruption without hammering the backend. */
    const scheduleReconnect = () => {
      if (disposed) return;
      const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
      attempt += 1;
      clearTimer();
      timer = window.setTimeout(connect, delay);
    };

    // (Re)open the live channel - and reopen it whenever the operator signs
    // in or out. This is what makes the dashboard come alive after the
    // access token has been entered.
    connect();

    const onAuthChanged = () => {
      clearTimer();
      attempt = 0;
      const token = localStorage.getItem('aeris_token') ?? '';
      dropSocket();
      if (!token) {
        // Signed out: the live channel stays closed until a token appears.
        setConnected(false);
        setLinkState('offline');
        return;
      }
      // Signed in (or the token changed): open a fresh socket.
      connect();
    };

    window.addEventListener(AUTH_CHANGED_EVENT, onAuthChanged);

    return () => {
      disposed = true;
      window.removeEventListener(AUTH_CHANGED_EVENT, onAuthChanged);
      clearTimer();
      dropSocket();
    };
  }, [url, addDetection, setHardwareData, setTelemetry, setLoraRx, setThermalFrame, setDevices, setLinkState]);

  const send = (data: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  };

  return { send, ws: wsRef, connected, lastMessageAt };
}
