/**
 * Single source of truth for backend connectivity.
 *
 * Production (hotspot / LAN):
 *   The FastAPI backend serves the built frontend itself, so the API lives on the
 *   exact same origin as the page (e.g. http://192.168.4.1 -> port 80).
 *   Using `window.location.origin` keeps the dashboard working on any host/port
 *   without hardcoding an IP or a port.
 *
 * Development (`npm run dev`):
 *   Vite serves the UI from :5173 while the backend runs standalone on :8000,
 *   so the API origin has to be pointed at :8000 explicitly.
 *
 * NOTE: the WebSocket URL is derived from the API origin. It is never built from
 * `window.location.port` alone — that used to break the local case, because
 * `window.location.port` is an empty string for http://localhost (implicit :80),
 * which silently produced ws://localhost:8000/ws and never connected.
 */

// Ports used by the Vite dev server (vite / vite preview).
const VITE_DEV_PORTS = new Set(['5173', '4173']);

/** True when the page is being served by the Vite dev server. */
export const isDevServer = (): boolean =>
  typeof window !== 'undefined' && VITE_DEV_PORTS.has(window.location.port);

/**
 * Base origin of the AERIS backend API.
 * Same origin as the page in production, :8000 in dev mode.
 */
export const getApiBase = (): string => {
  if (typeof window === 'undefined') return '';
  if (isDevServer()) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return window.location.origin;
};

/**
 * Absolute WebSocket URL for the live telemetry channel.
 * The access token is NOT part of the URL — it is sent in the auth handshake
 * frame so it never lands in access logs.
 */
export const getWebSocketUrl = (): string => {
  if (typeof window === 'undefined') return 'ws://localhost:8000/ws';
  const url = new URL(getApiBase());
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/ws';
  url.search = '';
  url.hash = '';
  return url.toString();
};
