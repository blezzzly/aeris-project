"""
main.py  (v8 — Thermal + Real LoRa) - Raspberry Pi 4 Model B
=============================================================

Full prototype pipeline running on the Raspberry Pi 4 Model B (AERIS):

   MLX90640 Thermal Camera (I2C) -> Hotspot Analytics -> Detection Packet
                                  -> Real LoRa (SX1278, SPI) -> Ground Station Dashboard

Hardware wiring (per AERIS thesis, "project thesis thermal camera.docx"):
  - MLX90640 (I2C) : SDA -> GPIO2, SCL -> GPIO3, VCC -> 3.3V, GND -> GND  (@ I2C addr 0x33)
  - Ra-02 LoRa (SX1278, SPI) : MOSI->GPIO10, MISO->GPIO9, SCK->GPIO11,
                               CE0->GPIO8, INT->GPIO4, VCC->3.3V, GND->GND, ANT->antenna
  - Both operate on 3.3V logic.

Device identity lives in config.py; functional pipeline constants in utils.py.
"""

import os
import time
import threading
import logging
import random
import warnings

os.environ["OPENCV_LOG_LEVEL"] = "SILENT"
os.environ["YOLO_VERBOSE"]     = "False"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
logging.getLogger("ultralytics").setLevel(logging.WARNING)
warnings.filterwarnings("ignore")

from config import (
    DEVICE_NAME, DEVICE_VERSION, CPU_MODEL, RAM_SIZE,
    STATUS, LORA_MODE, CAMERA_NAME,
)
from vital_signs     import VitalSignsEstimator
from packet          import DetectionPacket
from sender          import FastLoRaTransmitter
from receiver        import LoRaReceiver
from dashboard       import DashboardState, state as dash_state, run_dashboard
from logger          import DetectionLogger
from thermal         import ThermalCamera
from utils           import (
    timestamp_now, timestamp_short,
    LORA_HOST, LORA_PORT,
    LORA_RSSI_RANGE, LORA_SNR_RANGE, LORA_FREQUENCY_MHZ,
    DASHBOARD_HOST, DASHBOARD_PORT,
)


# ── Settings ──────────────────────────────────────────────────────────────
LORA_TX_INTERVAL     = 1.0        # send at most one packet per second
THERMAL_REFRESH_HZ   = 16         # MLX90640 frame refresh rate (REFRESH_16_HZ)


# ── MAIN ───────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  Human Detection → LoRa → Ground Station Dashboard")
    print("  DRRM Prototype Pipeline (Thermal + Real LoRa)")
    print("=" * 55)

    # ── Device information (Raspberry Pi 4 Model B) ───────────────────────
    print("\n[Device] Raspberry Pi 4 Model B")
    print(f"  Device   : {DEVICE_NAME}")
    print(f"  CPU      : {CPU_MODEL}")
    print(f"  RAM      : {RAM_SIZE}")
    print(f"  Status   : {STATUS}")
    print(f"  LoRa     : {LORA_MODE}")
    print(f"  Camera   : {CAMERA_NAME}")
    print(f"  Version  : {DEVICE_VERSION}")

    # ── Initialise modules ──────────────────────────────────────────────────
    print("\n[1/6] Initialising MLX90640 thermal camera...")
    thermal = ThermalCamera()
    thermal_matrix = thermal.get_matrix()
    hotspot = thermal.get_hotspot(thermal_matrix)
    norm_bytes = thermal.normalize_frame(thermal_matrix)
    print("OK")

    print("[2/6] Initialising vital signs estimator...")
    vital_estimator = VitalSignsEstimator()
    print("OK")

    print("[3/6] Initialising packet module...")
    packet_builder = DetectionPacket()
    print("OK")

    print("[4/6] Initialising real LoRa transmitter (SX1278 via SPI)...")
    lora_tx = FastLoRaTransmitter()
    print("OK")

    print("[5/6] Initialising LoRa receiver...")
    lora_receiver = LoRaReceiver(host=LORA_HOST, port=LORA_PORT)
    ok = lora_receiver.open()
    print("OK" if ok else "FAIL")
    if ok:
        lora_receiver.start()
        print("  └─ Receiver listening on UDP {}:{}".format(LORA_HOST, LORA_PORT))

    print("[6/6] Starting Ground Station Dashboard...")
    dash_thread = threading.Thread(
        target=run_dashboard,
        args=(DASHBOARD_HOST, DASHBOARD_PORT),
        daemon=True,
    )
    dash_thread.start()
    time.sleep(1.0)  # let Flask boot
    print("OK")

    logger = DetectionLogger()
    dash_state.add_log(f"System initialised — {DEVICE_NAME}")
    dash_state.add_log(f"Dashboard: http://localhost:{DASHBOARD_PORT}")

    # ── Thermal → Packet → Real LoRa transmit loop ─────────────────────────
    frame_id = 0
    packet_id = 0
    last_tx_time = 0.0

    try:
        while True:
            now = time.perf_counter()

            # Send at most one thermal LoRa packet per interval
            if (now - last_tx_time) >= LORA_TX_INTERVAL:
                # 1. Thermal frame → hotspot analytics
                thermal_matrix = thermal.get_matrix()
                hotspot = thermal.get_hotspot(thermal_matrix)
                min_temp = hotspot["min_temp"]
                max_temp = hotspot["max_temp"]
                hx = hotspot["hotspot_x"]
                hy = hotspot["hotspot_y"]

                # 2. Vital signs from thermal (temperature-driven)
                vitals = vital_estimator.estimate(True, confidence=1.0)

                # 3. Build detection packet
                packet_id += 1
                packet = packet_builder.build(
                    packet_id=packet_id,
                    person_detected=True,
                    confidence=1.0,
                    bbox=None,
                    vital_signs=vitals,
                    # No receiver exists on this node: RSSI/SNR are unmeasured (null), never simulated.
                    rssi=None,
                    snr=None,
                    frequency_mhz=LORA_FREQUENCY_MHZ,
                )

                # 4. Thermal compression: float32 → 8-bit normalized frame (768 B)
                norm_frame = thermal.normalize_frame(thermal_matrix)
                flat_bytes = norm_frame
                dash_state.update_thermal_frame(thermal_matrix)

                # 5. Pack + transmit over real LoRa (2 fast packets per frame)
                frame_id = (frame_id + 1) % 256
                header_1 = bytes([frame_id, 1, int(min_temp), int(max_temp), int(hx), int(hy)])
                header_2 = bytes([frame_id, 2, int(min_temp), int(max_temp), int(hx), int(hy)])
                packet_1 = header_1 + flat_bytes[:384]
                packet_2 = header_2 + flat_bytes[384:]

                dash_state.update_detection([], vitals, packet, packet_id)
                dash_state.lora_status = "Transmitting"

                tx_ok = lora_tx.transmit_packet(list(packet_1))
                tx_ok2 = lora_tx.transmit_packet(list(packet_2))
                # TX done means only "Waiting" (idle). "Connected" requires a receiver ACK,
                # which this protocol does not have — never claim it.
                dash_state.lora_status = "Waiting" if (tx_ok and tx_ok2) else "Error"

                status_str = "DETECTED"
                print(f"  [TX OK] Packet #{packet_id} | {status_str} | "
                      f"TEMP={max_temp:.1f}C HOTSPOT=({hx},{hy})")

                dash_state.add_log(f"Packet #{packet_id} | TX OK | {status_str}")

                last_tx_time = now

    finally:
        lora_receiver.stop()
        lora_tx.close()
        dash_state.add_log("System shut down")

        print(f"\n{'─' * 55}")
        print(f"  Device            : {DEVICE_NAME}")
        print(f"  Packets sent      : {packet_id}")
        print(f"  Packets received  : {lora_receiver.packet_count}")
        print(f"{'─' * 55}")


if __name__ == "__main__":
    main()
