"""
utils.py - Shared Utility Functions (Raspberry Pi 4 Model B)
============================================================

Provides configuration constants, timestamp helpers, and formatting
utilities used across the entire pipeline.

Device-specific identity values (DEVICE_NAME, CPU_MODEL, RAM_SIZE,
LORA_MODE, CAMERA_NAME, DEVICE_VERSION, STATUS) are imported from
config.py so the Raspberry Pi 4 Model B hardware is the single source
of truth for those values.
"""

import time
from datetime import datetime

from config import (
    DEVICE_NAME, DEVICE_VERSION, CPU_MODEL, RAM_SIZE,
    STATUS, LORA_MODE, CAMERA_NAME,
)


# ── Default Configuration (functional pipeline constants) ──────────────────

LORA_HOST = "localhost"
LORA_PORT = 9999

PACKET_VERSION = "1.0"

# Simulated vital signs realistic ranges
VITAL_SIGNS_RANGE = {
    "heart_rate":    (60, 100),    # bpm
    "respiration":   (12, 20),     # breaths per minute
    "spo2":          (95, 100),    # percentage
    "temperature":   (36.0, 37.5), # celsius
}

# Simulated LoRa transmission parameters
LORA_FREQUENCY_MHZ = 433.0
LORA_RSSI_RANGE    = (-90, -50)   # dBm
LORA_SNR_RANGE     = (5.0, 12.0)  # dB

# Dashboard
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 5000


# ── Timestamp Helpers ──────────────────────────────────────────────────────

def timestamp_now() -> str:
    """Return current timestamp with milliseconds."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def timestamp_short() -> str:
    """Return short timestamp for log entries (HH:MM:SS)."""
    return datetime.now().strftime("%H:%M:%S")


# ── Formatting Helpers ─────────────────────────────────────────────────────

def format_confidence(conf: float) -> str:
    return f"{conf * 100:.1f}%"


def format_heart_rate(hr: float) -> str:
    return f"{hr:.1f} bpm"


def format_respiration(rr: float) -> str:
    return f"{rr:.1f} br/min"


def format_spo2(spo2: float) -> str:
    return f"{spo2:.1f}%"


def format_temperature(temp: float) -> str:
    return f"{temp:.1f} °C"
