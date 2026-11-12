"""
weather_cache.py - Offline-aware weather data cache.

Caches the last successful weather result from Open-Meteo.
Returns honest offline state when internet is unavailable.
Never presents stale cached data as live.
"""
import time
import json
import os

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "weather_cache.json")
_cache = {"data": None, "fetched_at": None, "source": None}


def load_cache():
    global _cache
    try:
        with open(_CACHE_PATH) as f:
            _cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def save_cache():
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump(_cache, f)
    except Exception:
        pass


def update_weather(data: dict, source: str = "open-meteo"):
    """Store a fresh weather result."""
    _cache["data"] = data
    _cache["fetched_at"] = time.time()
    _cache["source"] = source
    save_cache()


def get_weather() -> dict:
    """Return weather data with honest online/offline status."""
    now = time.time()
    data = _cache.get("data")
    fetched_at = _cache.get("fetched_at")

    if data is None:
        return {
            "status": "unavailable",
            "reason": "No weather data cached yet",
            "data": None,
            "fetched_at": None,
            "age_seconds": None,
            "is_live": False,
        }

    age = now - fetched_at if fetched_at else None
    # Consider data "live" only if fetched within last 30 minutes
    is_live = age is not None and age < 1800

    return {
        "status": "ok" if is_live else "stale",
        "reason": None if is_live else "Weather data is stale (no internet or fetch failed)",
        "data": data,
        "fetched_at": fetched_at,
        "age_seconds": round(age, 1) if age else None,
        "is_live": is_live,
        "source": _cache.get("source"),
    }


load_cache()