import { useCallback, useEffect, useSyncExternalStore } from 'react';

/**
 * Browser geolocation for the ground station.
 *
 * The dashboard asks the browser for location permission the first time it is
 * opened. Live positioning is only initialised when the user allows it; if the
 * permission is denied nothing is faked - the map simply keeps using the
 * drone's own telemetry position (and shows no marker when there is no real
 * position at all).
 *
 * The permission prompt is raised only once per browser session, and the
 * resulting fix is shared by every map instance (dashboard card + Map tab).
 */

export interface BrowserFix {
  lat: number;
  lng: number;
  /** Device heading in degrees (null when the device has no compass). */
  heading: number | null;
  /** Horizontal accuracy in metres (null when unknown). */
  accuracy: number | null;
  /** Epoch ms of the fix. */
  timestamp: number;
}

export interface GeolocationState {
  /** The browser exposes the geolocation API. */
  supported: boolean;
  /** The user allowed location access. */
  granted: boolean;
  /** The user (or the browser) refused location access. */
  denied: boolean;
  /** The latest real fix (null until the permission is granted). */
  fix: BrowserFix | null;
  /** Ask again (only useful after a denial). */
  request: () => void;
}

const supported =
  typeof navigator !== 'undefined' && typeof navigator.geolocation !== 'undefined';

type Shared = {
  granted: boolean;
  denied: boolean;
  fix: BrowserFix | null;
  watchId: number | null;
  started: boolean;
  listeners: Set<() => void>;
  version: number;
};

const shared: Shared = {
  granted: false,
  denied: false,
  fix: null,
  watchId: null,
  started: false,
  listeners: new Set(),
  version: 0,
};

function notify() {
  shared.version += 1;
  for (const listener of shared.listeners) listener();
}

function toFix(pos: GeolocationPosition): BrowserFix {
  return {
    lat: pos.coords.latitude,
    lng: pos.coords.longitude,
    heading: Number.isFinite(pos.coords.heading) ? pos.coords.heading : null,
    accuracy: Number.isFinite(pos.coords.accuracy) ? pos.coords.accuracy : null,
    timestamp: pos.timestamp,
  };
}

function startWatching() {
  if (!supported || shared.watchId != null) return;
  shared.watchId = navigator.geolocation.watchPosition(
    (pos) => {
      shared.fix = toFix(pos);
      shared.granted = true;
      shared.denied = false;
      notify();
    },
    () => {},
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 1000 },
  );
}

function requestPosition() {
  if (!supported) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      shared.granted = true;
      shared.denied = false;
      shared.fix = toFix(pos);
      notify();
      startWatching();
    },
    () => {
      shared.granted = false;
      shared.denied = true;
      notify();
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
  );
}

/** Ask once per session; later instances reuse the granted permission. */
function ensureStarted() {
  if (!supported || shared.started) return;
  shared.started = true;

  if (typeof navigator.permissions?.query === 'function') {
    navigator.permissions
      .query({ name: 'geolocation' })
      .then((status) => {
        if (status.state === 'granted') {
          shared.granted = true;
          notify();
          requestPosition();
          startWatching();
        } else if (status.state === 'denied') {
          shared.denied = true;
          notify();
        } else {
          requestPosition();
        }
      })
      .catch(() => requestPosition());
  } else {
    requestPosition();
  }
}

export function useGeolocation(): GeolocationState {
  useSyncExternalStore(
    (cb) => {
      shared.listeners.add(cb);
      return () => {
        shared.listeners.delete(cb);
      };
    },
    () => shared.version,
    () => shared.version,
  );

  useEffect(() => {
    ensureStarted();
  }, []);

  const request = useCallback(() => {
    shared.started = true;
    requestPosition();
  }, []);

  return {
    supported,
    granted: shared.granted,
    denied: shared.denied,
    fix: shared.fix,
    request,
  };
}
