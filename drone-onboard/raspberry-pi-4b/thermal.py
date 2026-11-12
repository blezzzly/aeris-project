"""
thermal.py - MLX90640 Thermal Camera Interface (Raspberry Pi 4 Model B)
=====================================================================

Reads radiometric thermal frames from the MLX90640 (I2C, address 0x33) and
provides hotspot analytics for the AERIS thermal edge pipeline.

Based on the hardware design in "project thesis thermal camera.docx":
- MLX90640 (I2C) + Ra-02 LoRa (SX1278, SPI) both run on 3.3V logic.
- MLX90640 frame: 32x24 thermal matrix (768 floats, deg C).
- Hotspot: (min_temp, max_temp, hotspot_x, hotspot_y).
- Normalization: float32 -> 8-bit dynamic scale for LoRa packetization.

Thermal frame -> Hotspot Analytics -> Normalized Frame -> LoRa Packet(s)
"""

import board
import busio
import adafruit_mlx90640
import numpy as np
import time

from utils import LORA_FREQUENCY_MHZ  # reuse for packet metadata


class ThermalCamera:
    """MLX90640 thermal camera interface (I2C @ 0x33) for the RPi 4 Model B."""

    MLX90640_ADDRESS = 0x33
    REFRESH_RATE = adafruit_mlx90640.RefreshRate.REFRESH_16_HZ
    EMISSIVITY = 0.95
    OPENTA_SHIFT = 23.15  # ambient temperature offset (deg C)

    def __init__(self, address: int = MLX90640_ADDRESS,
                 refresh_rate=REFRESH_RATE):
        self.address = address
        self.i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
        self.mlx = adafruit_mlx90640.MLX90640(self.i2c, address=address)
        self.mlx.refresh_rate = refresh_rate

    # ── Frame acquisition ──────────────────────────────────────────────────

    def get_frame(self) -> list:
        """
        Request a thermal frame (768-element float list, 32x24 deg C).
        Retries on failure and falls back to a zero frame so the pipeline
        (and the /thermal_feed MJPEG stream) stays alive.
        """
        framebuf = [0.0] * 768
        last_err = None
        for _ in range(3):
            try:
                self.mlx.getFrame(framebuf)
                return framebuf
            except Exception as e:
                last_err = e
                framebuf = [0.0] * 768
                time.sleep(0.1)
        # Fallback: return zero frame to keep the thermal pipeline alive
        return framebuf

    def get_matrix(self) -> np.ndarray:
        """Return thermal matrix as float32 array of shape (24, 32)."""
        frame = self.get_frame()
        return np.array(frame, dtype=np.float32).reshape((24, 32))

    # ── Hotspot analytics ──────────────────────────────────────────────────

    def get_hotspot(self, matrix: np.ndarray = None) -> dict:
        """
        Compute hotspot: (min_temp, max_temp, hotspot_x, hotspot_y).
        Returns dict with keys: min_temp, max_temp, hotspot_x, hotspot_y.
        """
        if matrix is None:
            matrix = self.get_matrix()
        min_temp = float(np.min(matrix))
        max_temp = float(np.max(matrix))
        y_max, x_max = np.unravel_index(np.argmax(matrix), matrix.shape)
        return {
            "min_temp": min_temp,
            "max_temp": max_temp,
            "hotspot_x": int(x_max),
            "hotspot_y": int(y_max),
        }

    # ── Frame normalization (float32 -> 8-bit dynamic scale) ────────────────

    def normalize_frame(self, matrix: np.ndarray) -> bytes:
        """
        Downquantize 32-bit floats to 8-bit dynamic normalized values.
        Returns 768 bytes (32x24 uint8 grid).
        """
        min_temp = float(np.min(matrix))
        max_temp = float(np.max(matrix))
        norm = np.clip(
            (matrix - min_temp) / (max_temp - min_temp + 1e-5) * 255,
            0, 255,
        ).astype(np.uint8)
        return norm.flatten().tobytes()
