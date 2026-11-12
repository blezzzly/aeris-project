"""
packet.py - Detection Packet Format (Raspberry Pi 4 Model B)

Defines the JSON packet schema used for LoRa transmission.
Compatible with the existing detection log CSV/JSON format.

Packet Schema:
{
    "packet_id": int,
    "version": "1.0",
    "timestamp": "2026-07-10 12:00:00.123",
    "detection": {
        "person_detected": bool,
        "confidence": float,
        "bbox": [x1, y1, x2, y2]
    },
    "vital_signs": {
        "heart_rate": float,
        "respiration": float,
        "spo2": float,
        "temperature": float
    },
    "transmission": {
        "rssi": float,
        "snr": float,
        "frequency_mhz": float
    }
}
"""

import json
from utils import timestamp_now, PACKET_VERSION


class DetectionPacket:
    """Builds and parses detection packets for LoRa transmission."""

    @staticmethod
    def build(
        packet_id: int,
        person_detected: bool,
        confidence: float = 0.0,
        bbox: tuple | None = None,
        vital_signs: dict | None = None,
        rssi=None,
        snr=None,
        frequency_mhz: float = 915.0,
    ) -> dict:
        """
        Build a complete detection packet.

        Parameters
        ----------
        packet_id : int
            Sequential packet identifier.
        person_detected : bool
            Whether a person was detected in the current frame.
        confidence : float
            Detection confidence score (0–1).
        bbox : tuple | None
            Bounding box (x1, y1, x2, y2) or None.
        vital_signs : dict | None
            {"heart_rate", "respiration", "spo2", "temperature"} or None.
        rssi : float
            Simulated received signal strength indicator (dBm).
        snr : float
            Simulated signal-to-noise ratio (dB).
        frequency_mhz : float
            LoRa frequency in MHz.

        Returns
        -------
        dict
            Fully structured packet ready for JSON serialisation.
        """
        packet: dict = {
            "packet_id": packet_id,
            "version": PACKET_VERSION,
            "timestamp": timestamp_now(),
            "detection": {
                "person_detected": person_detected,
                "confidence": round(confidence, 2),
            },
            "transmission": {
                "rssi": round(rssi, 1) if rssi is not None else None,
                "snr": round(snr, 1) if snr is not None else None,
                "frequency_mhz": frequency_mhz,
            },
        }

        if bbox is not None and len(bbox) == 4:
            packet["detection"]["bbox"] = [int(v) for v in bbox]

        if vital_signs is not None and person_detected:
            packet["vital_signs"] = {
                "heart_rate":    vital_signs.get("heart_rate", 0),
                "respiration":   vital_signs.get("respiration", 0),
                "spo2":          vital_signs.get("spo2", 0),
                "temperature":   vital_signs.get("temperature", 0),
            }

        return packet

    @staticmethod
    def to_json(packet: dict) -> str:
        """Serialise packet dict to JSON string (with indent for readability)."""
        return json.dumps(packet)

    @staticmethod
    def from_json(json_str: str) -> dict:
        """Parse JSON string back into a packet dict."""
        return json.loads(json_str)
