"""
database.py - Local SQLite storage for AERIS detection history.

Persists detection records on the Ground Station Raspberry Pi so they survive
reboots. Uses SQLite (lightweight, no external server) and handles interrupted
writes via transactions.
"""
import sqlite3
import json
import os
import threading
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "aeris_detections.db")
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # safe concurrent writes
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """Create tables if they don't exist."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detection_id TEXT UNIQUE,
                    timestamp TEXT NOT NULL,
                    classification TEXT,
                    confidence REAL,
                    temp_min REAL,
                    temp_max REAL,
                    hotspot_x INTEGER,
                    hotspot_y INTEGER,
                    lat REAL,
                    lng REAL,
                    frame_id INTEGER,
                    thermal_ref TEXT,
                    status TEXT DEFAULT 'new',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lora_packets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seq INTEGER,
                    pkt_type TEXT,
                    frame_id INTEGER,
                    received_at TEXT NOT NULL,
                    rssi REAL,
                    snr REAL,
                    crc_ok INTEGER DEFAULT 1,
                    duplicate INTEGER DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()


def add_detection(detection: dict) -> bool:
    """Insert a detection record. Returns True if inserted, False if duplicate."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("""
                INSERT INTO detections
                (detection_id, timestamp, classification, confidence,
                 temp_min, temp_max, hotspot_x, hotspot_y, lat, lng,
                 frame_id, thermal_ref, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                detection.get("id"),
                detection.get("timestamp", datetime.now().isoformat()),
                detection.get("classification", "human"),
                detection.get("confidence"),
                detection.get("temp_min"),
                detection.get("temp_max"),
                detection.get("hotspot_x"),
                detection.get("hotspot_y"),
                detection.get("lat"),
                detection.get("lng"),
                detection.get("frame_id"),
                detection.get("thermal_ref"),
                detection.get("status", "new"),
                datetime.now().isoformat(),
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # duplicate detection_id
        except Exception as e:
            print(f"[DB] Insert error: {e}")
            return False
        finally:
            conn.close()


def get_detections(limit: int = 100, offset: int = 0):
    """Retrieve detection records, newest first."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "SELECT * FROM detections ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()


def detection_count() -> int:
    with _lock:
        conn = _connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
        finally:
            conn.close()


def add_lora_packet(pkt: dict):
    """Log a received LoRa packet for monitoring."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("""
                INSERT INTO lora_packets (seq, pkt_type, frame_id, received_at, rssi, snr, crc_ok, duplicate)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                pkt.get("seq"),
                pkt.get("pkt_type"),
                pkt.get("frame_id"),
                datetime.now().isoformat(),
                pkt.get("rssi"),
                pkt.get("snr"),
                1 if pkt.get("crc_ok", True) else 0,
                1 if pkt.get("duplicate", False) else 0,
            ))
            conn.commit()
        except Exception as e:
            print(f"[DB] LoRa packet log error: {e}")
        finally:
            conn.close()


def lora_stats() -> dict:
    """Return aggregated LoRa RX stats."""
    with _lock:
        conn = _connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM lora_packets").fetchone()[0]
            crc_ok = conn.execute("SELECT COUNT(*) FROM lora_packets WHERE crc_ok=1").fetchone()[0]
            dupes = conn.execute("SELECT COUNT(*) FROM lora_packets WHERE duplicate=1").fetchone()[0]
            last = conn.execute(
                "SELECT received_at, seq, frame_id, rssi, snr FROM lora_packets ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return {
                "total_rx": total,
                "crc_ok": crc_ok,
                "crc_fail": total - crc_ok,
                "duplicates": dupes,
                "last_packet": {
                    "received_at": last[0], "seq": last[1], "frame_id": last[2],
                    "rssi": last[3], "snr": last[4],
                } if last else None,
            }
        finally:
            conn.close()


# Initialize on import
init_db()