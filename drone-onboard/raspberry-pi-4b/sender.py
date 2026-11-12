"""
sender.py - Simulated LoRa Sender (Raspberry Pi 4 Model B)

Transmits detection packets via UDP sockets, simulating a LoRa radio link.
Designed so that replacing the socket with real LoRa hardware (e.g. SX1278
via SPI on a Raspberry Pi 4 Model B) requires minimal changes — just swap the
transport layer while keeping the packet interface identical.
"""

import socket
import json
import time
import random
import logging
from utils import LORA_HOST, LORA_PORT, LORA_RSSI_RANGE, LORA_SNR_RANGE

# Real SX1278 LoRa radio via SPI (Raspberry Pi 4 Model B, CE0 on GPIO8)
from SX127x.LoRa import *
from SX127x.board_config import BOARD

# Shared radio configuration (sync word, frequency, modulation). The ground
# receiver imports the SAME module, so the two ends cannot drift apart.
# The import is independent of the working directory: the module always
# sits next to this file.
try:
    import lora_protocol
except ImportError:  # pragma: no cover
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import lora_protocol

logger = logging.getLogger(__name__)


class LoRaSender:
    """Simulated LoRa transmitter using UDP as a stand-in for the radio link."""

    def __init__(self, host: str = LORA_HOST, port: int = LORA_PORT):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._last_transmission_time = 0.0
        self._packets_sent = 0
        self._open()

    # ── Internal ─────────────────────────────────────────────────────────

    def _open(self):
        """Open a UDP socket for simulated LoRa transmission."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(2.0)
            logger.info("[LoRaSender] Socket opened → %s:%s", self.host, self.port)
        except OSError as e:
            logger.error("[LoRaSender] Failed to open socket: %s", e)
            self._sock = None

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    @property
    def packets_sent(self) -> int:
        return self._packets_sent

    @property
    def last_transmission_time(self) -> float:
        return self._last_transmission_time

    # ── Public API ───────────────────────────────────────────────────────

    def transmit(self, packet: dict) -> bool:
        """
        Transmit a packet over the simulated LoRa link.

        Parameters
        ----------
        packet : dict
            Detection packet (see packet.DetectionPacket.build).

        Returns
        -------
        bool
            True if the packet was sent successfully.
        """
        if self._sock is None:
            logger.warning("[LoRaSender] Socket not available — packet dropped.")
            return False

        try:
            # Simulate LoRa airtime delay (100–350 ms depending on payload)
            payload_len = len(json.dumps(packet))
            delay = random.uniform(0.08, 0.15) + payload_len * 0.00005
            time.sleep(delay)

            data = json.dumps(packet).encode("utf-8")
            self._sock.sendto(data, (self.host, self.port))

            self._packets_sent += 1
            self._last_transmission_time = time.time()

            logger.debug(
                "[LoRaSender] Packet #%s transmitted (%d bytes)",
                packet.get("packet_id", "?"), len(data),
            )
            return True

        except socket.timeout:
            logger.warning("[LoRaSender] Transmission timeout.")
            return False
        except OSError as e:
            logger.error("[LoRaSender] Transmission failed: %s", e)
            return False

    def close(self):
        """Close the socket."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            logger.info("[LoRaSender] Socket closed.")


class FastLoRaTransmitter(LoRa):
    """
    Real SX1278 LoRa transmitter (SPI on Raspberry Pi 4 Model B, CE0 = GPIO8).

    Implements the high-performance thermal transmission strategy from the
    AERIS thesis ("project thesis thermal camera.docx"):

    - LoRa radio tuning: BW = 500 kHz, SF = 6, Explicit Header
      (~21.8 kbps max throughput).
    - Thermal compression: float32 -> 8-bit dynamic normalized frames
      (32x24 grid), packed into two ultra-fast LoRa packets.

    Warning: never power on the LoRa module without its antenna connected —
    operating without an antenna damages the RF power amplifier.
    """

    def __init__(self):
        # Thesis wiring schema (project thesis thermal camera.docx):
        #   NSS/CS -> GPIO24 (Pin 18), RESET -> GPIO23 (Pin 16), DIO0 -> GPIO18 (Pin 12).
        # SX1278 requires a RESET pulse and CS deasserted between transactions.
        import RPi.GPIO as _GPIO
        import spidev as _spidev
        import time as _time
        _GPIO.setmode(_GPIO.BCM)
        _GPIO.setup(23, _GPIO.OUT)  # RESET
        _GPIO.setup(24, _GPIO.OUT)  # NSS
        _GPIO.output(23, _GPIO.LOW)
        _time.sleep(0.05)
        _GPIO.output(23, _GPIO.HIGH)
        _time.sleep(0.05)

        class _ThesisSpi:
            """SPI wrapper asserting NSS (GPIO24) around every transaction."""
            def __init__(self):
                self._s = _spidev.SpiDev()
                self._s.open(0, 0)
                self._s.max_speed_hz = 1000000
                self._s.no_cs = True
                _GPIO.output(24, _GPIO.HIGH)
            def xfer(self, data):
                _GPIO.output(24, _GPIO.LOW)
                try:
                    return self._s.xfer(data)
                finally:
                    _GPIO.output(24, _GPIO.HIGH)
            def close(self):
                try:
                    self._s.close()
                except Exception:
                    pass
        _spi = _ThesisSpi()
        BOARD.spi = _spi
        LoRa.spi = _spi
        # LoRa.py uses board_config_ada.BOARD (a distinct class); patch that one too.
        from SX127x import board_config_ada as _ada
        _ada.BOARD.spi = _spi
        # GPIO edge detection unavailable on this kernel; TX completion is polled
        # via SPI register reads (get_irq_flags), so DIO interrupt registration is a no-op.
        BOARD.add_event_detect = staticmethod(lambda *a, **k: None)
        _ada.BOARD.add_event_detect = staticmethod(lambda *a, **k: None)
        super().__init__(verbose=False, do_calibration=False)
        self.set_mode(MODE.SLEEP)
        self.set_dio_mapping([0, 0, 0, 0, 0, 0])
        # Frequency: 433 MHz (physical Ra-02 module band)
        self.set_freq(433.0)
        self.set_bw(BW.BW500)              # 500 kHz bandwidth for max bitrate
        self.set_spreading_factor(6)  # SF6 = fast packet delivery
        self.set_coding_rate(CODING_RATE.CR4_5)
        self.set_preamble(6)
        self.set_rx_crc(True)
        # Private network word (sync word). In LoRa mode the SX1276/78 only
        # accepts a packet whose sync word matches its own, so this value has
        # to be IDENTICAL on the drone and on the ground receiver - the
        # ground receiver programs lora_protocol.SYNC_WORD into Reg 0x39 and
        # reads it back to prove it.
        #
        # pySX127x implements set_sync_word() as a NO-OP STUB (SX127x/LoRa.py):
        # it returns the value without ever touching the register. The call
        # is therefore kept for documentation and for a future library that
        # implements it properly - it does NOT change the radio behaviour.
        # The radio keeps the SX1276/78 silicon default 0x12, which is exactly
        # what lora_protocol.SYNC_WORD defines.
        self.set_sync_word(lora_protocol.SYNC_WORD)

    # Real TX state (surfaced to the ground station via status file; never treated as "connected" without ACK)
    _tx_state = "waiting"  # waiting | sending | error
    _tx_last_start = None
    _tx_last_ok = None
    _tx_last_error = None
    _tx_ok_count = 0
    _tx_err_count = 0
    _ack_received = False  # no ACK protocol exists; stays False
    STATUS_PATH = "/tmp/opencode/lora_tx_status.json"

    @classmethod
    def _write_status(cls):
        import json as _json
        from datetime import datetime, timezone
        try:
            with open(cls.STATUS_PATH, "w") as f:
                _json.dump({
                    "state": cls._tx_state,
                    "last_start": cls._tx_last_start,
                    "last_ok": cls._tx_last_ok,
                    "last_error": cls._tx_last_error,
                    "tx_ok_count": cls._tx_ok_count,
                    "tx_err_count": cls._tx_err_count,
                    "ack_received": cls._ack_received,
                    "written_at": datetime.now(timezone.utc).isoformat(),
                }, f)
        except Exception:
            pass

    def close(self):
        """Release SPI/GPIO resources held by the SX127x driver."""
        try:
            BOARD.teardown()
        except Exception:
            pass
        try:
            import RPi.GPIO as _GPIO
            _GPIO.cleanup()
        except Exception:
            pass

    def transmit_packet(self, payload: list) -> bool:
        """
        Transmit a LoRa packet (list of bytes) using the fast TX path.
        Returns True ONLY if the radio reports tx_done before the timeout.
        """
        type(self)._tx_state = "sending"
        type(self)._tx_last_start = time.time()
        type(self)._write_status()
        try:
            self.set_mode(MODE.STDBY)
            self.write_payload(payload)
            self.set_mode(MODE.TX)

            start_time = time.time()
            while self.get_irq_flags()['tx_done'] == 0:
                if time.time() - start_time > 1.0:
                    type(self)._tx_err_count += 1
                    type(self)._tx_last_error = f"tx_done timeout after 1.0s"
                    type(self)._tx_state = "error"
                    type(self)._write_status()
                    return False
                time.sleep(0.001)
            self.clear_irq_flags(TxDone=1)
            type(self)._tx_ok_count += 1
            type(self)._tx_last_ok = time.time()
            type(self)._tx_last_error = None
            type(self)._tx_state = "waiting"
            type(self)._write_status()
            return True
        except Exception as e:
            type(self)._tx_err_count += 1
            type(self)._tx_last_error = str(e)
            type(self)._tx_state = "error"
            type(self)._write_status()
            return False
