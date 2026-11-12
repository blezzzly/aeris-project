import { useEffect } from 'react';
import { useAppStore } from '../stores/appStore';
import { getApiBase } from '../lib/api';
import { AUTH_CHANGED_EVENT } from './useWebSocket';

/**
 * Weather for the mission area (Maragondon, Cavite).
 *
 * The browser NEVER calls an external service: it reads the backend's local
 * cache through /api/weather. The backend may refresh that cache when a WAN
 * exists, but AERIS works fully offline without one.
 *
 * Cached-but-old data is never presented as a live reading: the panel shows
 * "Unavailable / offline" and the age of the cache instead.
 */
const REFRESH_MS = 5 * 60 * 1000;

interface WeatherPayload {
  status?: string;
  data?: {
    current?: {
      temperature_2m?: number;
      weather_code?: number;
      time?: string;
    };
    temperature?: number;
    temperature_2m?: number;
    weather_code?: number;
    code?: number;
    time?: string;
  } | null;
  is_live?: boolean;
  age_seconds?: number | null;
  fetched_at?: number | null;
  source?: string | null;
  reason?: string | null;
}

function wmoToCondition(code: number): string {
  if (code === 0) return 'Clear';
  if (code === 1) return 'Mostly Clear';
  if (code === 2) return 'Partly Cloudy';
  if (code === 3) return 'Overcast';
  if (code === 45 || code === 48) return 'Foggy';
  if (code >= 51 && code <= 55) return 'Drizzle';
  if (code >= 61 && code <= 65) return 'Rain';
  if (code >= 66 && code <= 67) return 'Freezing Rain';
  if (code >= 71 && code <= 77) return 'Snow';
  if (code >= 80 && code <= 82) return 'Rain Showers';
  if (code >= 85 && code <= 86) return 'Snow Showers';
  if (code >= 95 && code <= 99) return 'Thunderstorm';
  return 'Unknown';
}

function ageLabel(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return 'age unknown';
  const minutes = Math.round(seconds / 60);
  if (minutes <= 0) return 'just now';
  if (minutes < 60) return `${minutes} min old`;
  const hours = Math.round(minutes / 60);
  return `${hours} h old`;
}

export function useLiveWeather() {
  useEffect(() => {
    let cancelled = false;

    const fetchWeather = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/weather`, {
          headers: {
            Authorization: `Bearer ${localStorage.getItem('aeris_token') ?? ''}`,
          },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const payload = (await res.json()) as WeatherPayload;
        const data = payload?.data;
        const current = data?.current;
        const temp = current?.temperature_2m ?? data?.temperature ?? data?.temperature_2m;
        const code = current?.weather_code ?? data?.weather_code ?? data?.code;
        const observedAt =
          current?.time ??
          data?.time ??
          (typeof payload.fetched_at === 'number'
            ? new Date(payload.fetched_at * 1000).toISOString()
            : null);
        const hasReading = typeof temp === 'number' && typeof code === 'number';
        const live = payload?.is_live === true && hasReading;

        if (cancelled) return;
        if (live) {
          useAppStore.getState().updateEnvironment({
            temperature: Math.round((temp as number) * 10) / 10,
            condition: wmoToCondition(code as number),
            updatedAt: observedAt,
            source: 'Open-Meteo (local cache)',
          });
        } else if (hasReading) {
          // Cached, but not fresh: do not display it as a current reading.
          useAppStore.getState().updateEnvironment({
            temperature: null,
            condition: `Offline — cached ${ageLabel(payload.age_seconds)}`,
            updatedAt: observedAt,
            source: 'Local weather cache (stale)',
          });
        } else {
          useAppStore.getState().updateEnvironment({
            temperature: null,
            condition: 'Unavailable (offline)',
            updatedAt: null,
            source: 'Local weather cache',
          });
        }
      } catch {
        if (!cancelled) {
          useAppStore.getState().updateEnvironment({
            temperature: null,
            condition: 'Unavailable (offline)',
            updatedAt: null,
            source: 'Local weather cache',
          });
        }
      }
    };

    fetchWeather();
    const id = setInterval(fetchWeather, REFRESH_MS);
    // A token change means the request may start succeeding (or fail): retry
    // immediately instead of waiting for the next 5-minute tick.
    const onAuthChanged = () => { void fetchWeather(); };
    window.addEventListener(AUTH_CHANGED_EVENT, onAuthChanged);
    return () => {
      cancelled = true;
      clearInterval(id);
      window.removeEventListener(AUTH_CHANGED_EVENT, onAuthChanged);
    };
  }, []);
}
