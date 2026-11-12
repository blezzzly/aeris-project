import { useEffect } from 'react';
import { useAppStore } from '../stores/appStore';

// Live weather from Open-Meteo (free, no API key) for the mission area (Maragondon, Cavite).
// Auto-updates every 5 minutes; never uses hardcoded/simulated values.
const LAT = 14.2515;
const LNG = 120.731;
const URL = `https://api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LNG}&current=temperature_2m,weather_code,wind_speed_10m&timezone=Asia%2FManila`;

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

export function useLiveWeather() {
  useEffect(() => {
    let cancelled = false;
    const fetchWeather = async () => {
      try {
        const res = await fetch(URL);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const temp = data?.current?.temperature_2m;
        const code = data?.current?.weather_code;
        if (typeof temp !== 'number' || typeof code !== 'number') throw new Error('Missing data');
        if (!cancelled) {
          useAppStore.getState().updateEnvironment({
            temperature: Math.round(temp * 10) / 10,
            condition: wmoToCondition(code),
            updatedAt: data.current.time ?? new Date().toISOString(),
            source: 'Open-Meteo (live)',
          });
        }
      } catch {
        if (!cancelled) {
          useAppStore.getState().updateEnvironment({
            temperature: null,
            condition: 'Unavailable',
            updatedAt: null,
            source: 'Open-Meteo (live)',
          });
        }
      }
    };
    fetchWeather();
    const id = setInterval(fetchWeather, 5 * 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
}
