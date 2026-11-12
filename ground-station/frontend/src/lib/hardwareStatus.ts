/**
 * Shared helpers that decide whether a hardware status may be shown as
 * "online / connected".
 *
 * The ground station only ever displays what the hardware actually reported.
 * A report that is older than the freshness window is treated as *not*
 * connected: showing a stale green "CONNECTED" for a sensor that stopped
 * answering (or for a laptop that lost the Wi-Fi link) would be exactly the
 * kind of wrong status this dashboard must never show.
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
 * Connection state of one hardware component.
 *
 * The result is derived from the measured report, never hardcoded:
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

/** Link state derived from the age of the last message received. */
export function linkStateFrom(lastMessageAt: number | null, now: number = Date.now()): LinkState {
  if (lastMessageAt == null) return 'connecting';
  const age = now - lastMessageAt;
  if (age <= FRAME_STALE_MS) return 'live';
  if (age <= REPORT_STALE_MS) return 'stale';
  return 'offline';
}
