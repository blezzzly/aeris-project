/**
 * Shared helpers that decide whether a hardware status may be shown as
 * "online / connected".
 *
 * The ground station only ever displays what the hardware actually reported.
 * Transport state and device state are deliberately separate:
 *
 *   * a device is "online" only while the backend measured it AND the report
 *     that carried that measurement is fresh,
 *   * a device whose last measurement was "online" but whose report is old is
 *     shown as "stale" - NOT as "offline": a Wi-Fi/tab problem is not a
 *     hardware failure,
 *   * a device is "offline" only when the backend itself measured it offline,
 *   * no report at all is "unknown", never "offline".
 */

/** A hardware report older than this is considered stale. */
export const REPORT_STALE_MS = 15_000;

/** A thermal frame older than this is considered stale. */
export const FRAME_STALE_MS = 5_000;

/** State of the live link between the dashboard and the ground station. */
export type LinkState = 'connecting' | 'live' | 'stale' | 'offline';

/** Age of an ISO timestamp in milliseconds (null when there is no timestamp). */
export function reportAgeMs(timestamp: string | null | undefined, now: number = Date.now()): number | null {
  if (!timestamp) return null;
  const t = Date.parse(timestamp);
  if (Number.isNaN(t)) return null;
  return now - t;
}

/** True when the report is recent enough to be trusted. */
export function isReportFresh(
  timestamp: string | null | undefined,
  maxAgeMs: number = REPORT_STALE_MS,
  now: number = Date.now(),
): boolean {
  const age = reportAgeMs(timestamp, now);
  return age !== null && age >= 0 && age <= maxAgeMs;
}

/**
 * Connection state of one hardware component as *measured* by the backend.
 *
 * This answers "did the hardware answer?" and intentionally does not know
 * anything about the browser link. Use {@link deviceDisplayState} to turn the
 * measurement into what the dashboard may display.
 *
 *  - no report at all            -> 'offline' (nothing has answered yet)
 *  - report older than the window -> 'offline' (stale data is not truth)
 *  - device reports not connected -> 'offline'
 *  - device reports connected     -> 'online'
 */
export function deviceConnectionState(
  connected: boolean | undefined | null,
  timestamp: string | null | undefined,
  maxAgeMs: number = REPORT_STALE_MS,
  now: number = Date.now(),
): 'online' | 'offline' {
  if (!connected) return 'offline';
  const age = reportAgeMs(timestamp, now);
  if (age === null) return 'offline';
  if (age < 0 || age > maxAgeMs) return 'offline';
  return 'online';
}

/**
 * What the dashboard may show for a device, given the backend's last measured
 * verdict and the age of the report that carried it.
 *
 *  - backend measured 'offline'            -> 'offline' (the sensor really failed)
 *  - backend measured 'online', fresh      -> 'online'
 *  - backend measured 'online', stale      -> 'stale' (link/report problem)
 *  - backend said 'stale'                  -> 'stale'
 *  - no measurement                        -> 'unknown'
 *
 * Never turns a transport problem into a red "DISCONNECTED" hardware failure.
 */
export function deviceDisplayState(
  measured: 'online' | 'stale' | 'offline' | 'unknown' | null | undefined,
  reportAgeMsValue: number | null,
  maxAgeMs: number = REPORT_STALE_MS,
): 'online' | 'stale' | 'offline' | 'unknown' {
  if (measured == null || measured === 'unknown') return 'unknown';
  if (measured === 'offline') return 'offline';
  if (measured === 'stale') return 'stale';
  if (reportAgeMsValue == null) return 'unknown';
  if (reportAgeMsValue < 0 || reportAgeMsValue > maxAgeMs) return 'stale';
  return 'online';
}

/** Link state derived from the age of the last message received. */
export function linkStateFrom(lastMessageAt: number | null, now: number = Date.now()): LinkState {
  if (lastMessageAt == null) return 'connecting';
  const age = now - lastMessageAt;
  if (age <= FRAME_STALE_MS) return 'live';
  if (age <= REPORT_STALE_MS) return 'stale';
  return 'offline';
}
