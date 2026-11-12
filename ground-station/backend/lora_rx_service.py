"""
lora_rx_service.py - Ground-side LoRa receiver service.

This module provides the software interface for receiving LoRa packets from
the drone via a ground-side SX1278 module. It is HONEST about hardware
availability — if no SX1278 is connected to the ground station Pi, it reports
hardware_available=False and does not fabricate reception data.

Hardware (user-verified wiring):
  MLX90640 (I2C)        VCC->1  SDA->3  SCL->5  GND->6
  Ra-02 SX1278 (SPI)    VCC->17 GND->9  MISO->21(GPIO9) MOSI->19(GPIO10)
                        SCK->23(GPIO11) NSS->18(GPIO24) RESET->16(GPIO23)
                        DIO0->12(GPIO18)

When hardware IS available, the service:
  - Initialises the SX1278 for 433 MHz LoRa RX
  - Watches the DIO0 interrupt line (GPIO18, via gpiod) and reads packets
    from the FIFO (register-polled fallback if gpiod is unavailable)
  - Validates CRC and framing (using lora_protocol)
  - Reassembles multi-chunk thermal frames
  - Tracks RX stats (count, CRC fail, duplicates, RSSI/SNR, DIO0 interrupts)
  - Forwards decoded data to the backend via callback

Usage:
    from lora_rx_service import LoRaRxService
    svc = LoRaRxService(on_packet=callback)
    svc.start()  # returns False if hardware unavailable
"""
import threading
import time
import logging
import os
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

# Add drone protocol module to path
_DRONE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "drone-onboard", "raspberry-pi-4b"))
if _DRONE_DIR not in sys.path:
    sys.path.insert(0, _DRONE_DIR)

try:
    import lora_protocol as _proto
    _PROTO_AVAILABLE = True
except ImportError:
    _PROTO_AVAILABLE = False
    logger.warning("lora_protocol module not found — packet decoding disabled")

try:
    import gpiod
    from gpiod.line import Edge, Bias, Direction, Clock
    _GPIOD_AVAILABLE = True
except Exception:
    _GPIOD_AVAILABLE = False
    logger.info("gpiod not available — DIO0 interrupt watching disabled (polling fallback)")

import sensors as _sensors
import sx1278_gpio as _gpio

# SX1278 register map (Semtech SX1276/77/78/79 datasheet, LoRa mode)
REG_OP_MODE = 0x01
REG_MODEM_CONFIG_1 = 0x1D
REG_MODEM_CONFIG_2 = 0x1E
REG_MODEM_CONFIG_3 = 0x26
REG_PREAMBLE_MSB = 0x20
REG_PREAMBLE_LSB = 0x21
REG_SYNC_WORD = 0x39
REG_FIFO_RX_BASE_ADDR = 0x0F
REG_FIFO_ADDR_PTR = 0x0D
REG_FIFO_RX_CURRENT_ADDR = 0x10
REG_RX_NB_BYTES = 0x13
REG_IRQ_FLAGS = 0x12
REG_PKT_RSSI_VALUE = 0x1A
REG_PKT_SNR_VALUE = 0x19

# LoRa continuous RX, LongRangeMode + LowFrequencyModeOn (<525 MHz)
_OPMODE_RXCONT_LF = 0x8D
# Modem config matching the AERIS drone transmitter (drone-onboard sender.py):
#   433.000 MHz, BW 500 kHz, SF6, CR 4/5, explicit header, CRC on,
#   preamble 6.
REG_WRITE_MODEM_CONFIG_1 = 0x92   # BW=500 kHz (9), CR=4/5, explicit header
REG_WRITE_MODEM_CONFIG_2 = 0x64   # SF6 (6), CRC on
REG_WRITE_MODEM_CONFIG_3 = 0x04   # AGC auto on, low data rate optimize off
REG_WRITE_PREAMBLE_LSB = 0x06     # preamble length 6
# IRQ flag bits
_IRQ_RX_TIMEOUT = 0x80
_IRQ_RX_DONE = 0x40
_IRQ_PAYLOAD_CRC_ERROR = 0x20

# ── Shared radio configuration ──────────────────────────────────────────────
# The values live in lora_protocol.py, the module BOTH ends of the link use
# (this file imports it as _proto above). SYNC_WORD in particular MUST be
# identical on the drone and on the ground: the SX1276/78 in LoRa mode only
# accepts packets whose private network word matches its own.
#
# The drone transmitter sets the word explicitly from this same constant
# (sender.py: set_sync_word(lora_protocol.SYNC_WORD)). pySX127x writes
# Reg 0x39 through its @setter(REG.LORA.SYNC_WORD) decorator, so the value
# in the drone radio is 0x12 - the same value the SX1276/78 uses by default.
# The ground receiver therefore has to program 0x12 as well: anything else
# made the two radios unable to decode each other (observed: dio0_irq_count
# climbing while rx_count stayed at 0 and crc_fail grew).
try:
    REG_WRITE_SYNC_WORD = int(_proto.SYNC_WORD)
except Exception:  # pragma: no cover - the protocol module is always present
    REG_WRITE_SYNC_WORD = 0x12


class LoRaRxService:
    """Ground-side LoRa receiver. Honest about hardware availability."""

    def __init__(self, on_packet=None, frequency_mhz=433.0):
        self._on_packet = on_packet
        self._frequency = frequency_mhz
        self._frequency_mhz = frequency_mhz
        self._radio_model = None
        self._running = False
        self._thread = None
        self._watcher_thread = None
        self._spi = None
        self._lock = threading.Lock()

        # Stats
        self._rx_count = 0
        self._crc_fail = 0
        self._duplicate_count = 0
        self._last_packet = None
        self._last_rssi = None
        self._last_snr = None
        self._last_rx = None
        self._hardware_available = False
        self._radio = None
        self._radio_model = None
        self._reason = "not started"

        # Reassembler
        if _PROTO_AVAILABLE:
            self._reassembler = _proto.FrameReassembler()
        else:
            self._reassembler = None

        # Seen seqs for dedup
        self._seen_seqs = set()

        # DIO0 interrupt bookkeeping (GPIO18 rising edge from the radio).
        # Packets read on the IRQ line are queued here; the worker loop drains.
        self._dio0_irq_count = 0
        self._dio0_request = None
        self._rx_errors = 0
        self._pending_packets = []

        # ── Lifecycle bookkeeping ──────────────────────────────────────────
        # _lifecycle serialises start()/stop() so a re-initialisation can
        # never race with a shutdown.
        self._lifecycle = threading.Lock()
        # _reinit_pending guards against two recovery attempts running at
        # the same time (each one would open its own SPI handle).
        self._reinit_pending = False
        self._reinit_count = 0
        self._last_reinit = None
        self._last_error = None
        # The sync word that was READ BACK from the radio register 0x39.
        self._sync_word = None

    @property
    def hardware_available(self) -> bool:
        return self._hardware_available

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Register helpers (GPIO24 CS + spidev, no_cs) ───────────────────────
    def _rd(self, addr):
        return _sensors._sx1278_read_reg(self._spi, addr)

    def _wr(self, addr, value):
        return _sensors._sx1278_write_reg(self._spi, addr, value)

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the RX service.

        The radio is initialised for real first (id registers are read back
        from the chip to confirm it is configured for 433 MHz). Only then is
        it put into continuous RX and its DIO0 line watched. When anything is
        missing the service stays down and reports why - it never invents
        reception data.

        IDEMPOTENT: calling start() on an already running service is a no-op.
        The radio has exactly one SPI handle and one DIO0 watcher - a second
        start() would open a second handle on the same radio and start a
        second edge watcher fighting over it.
        """
        with self._lifecycle:
            if self._running:
                # Already running - never open a second SPI handle.
                return True

            try:
                spi, info = _sensors.sx1278_init(0)
            except Exception as exc:
                logger.warning(f"LoRa RX: hardware init failed: {exc}")
                self._reason = f"hardware init failed: {exc}"
                self._last_error = self._reason
                return False

            if not info.get("available"):
                self._reason = info.get("reason") or "no LoRa radio answered on SPI"
                self._last_error = self._reason
                logger.warning(f"LoRa RX unavailable: {self._reason}")
                if spi is not None:
                    try:
                        spi.close()
                    except Exception:
                        pass
                return False

            self._spi = spi
            try:
                # Continuous RX on the 433 MHz low band (LF bit set),
                # matching the AERIS drone transmitter (see constants above).
                self._wr(REG_MODEM_CONFIG_1, REG_WRITE_MODEM_CONFIG_1)
                self._wr(REG_MODEM_CONFIG_2, REG_WRITE_MODEM_CONFIG_2)
                self._wr(REG_MODEM_CONFIG_3, REG_WRITE_MODEM_CONFIG_3)
                self._wr(REG_PREAMBLE_MSB, 0x00)
                self._wr(REG_PREAMBLE_LSB, REG_WRITE_PREAMBLE_LSB)
                self._wr(REG_SYNC_WORD, REG_WRITE_SYNC_WORD)
                self._wr(REG_FIFO_RX_BASE_ADDR, 0x80)
                self._wr(REG_IRQ_FLAGS, 0xFF)          # clear stale flags
                self._wr(REG_OP_MODE, _OPMODE_RXCONT_LF)  # RXCONT + LF band
                self._radio_model = info.get("model") or "SX1276/SX1278"
                self._frequency_mhz = info.get("frequency_mhz") or self._frequency
                self._hardware_available = True
                self._reason = None
                # READ BACK the sync word that is actually in the radio
                # register. The value is reported, never assumed.
                try:
                    self._sync_word = self._rd(REG_SYNC_WORD)
                except Exception as exc:
                    self._sync_word = None
                    logger.warning(f"LoRa RX: could not read back the sync word: {exc}")
                self._last_error = None
            except Exception as exc:
                self._hardware_available = False
                self._reason = f"radio init failed: {exc}"
                self._last_error = self._reason
                logger.error(f"LoRa RX init failed: {exc}")
                try:
                    spi.close()
                except Exception:
                    pass
                self._spi = None
                return False

            self._running = True
            self._reinit_pending = False
            self._watcher_thread = threading.Thread(
                target=self._dio0_watcher, daemon=True, name="aeris-lora-dio0")
            self._watcher_thread.start()
            self._thread = threading.Thread(
                target=self._listen_loop, daemon=True, name="aeris-lora-rx")
            self._thread.start()
            logger.info(
                f"LoRa RX started on a real {self._radio_model} at "
                f"{self._frequency_mhz} MHz (SF6/BW500, CR4/5, sync "
                f"{('0x%02x' % self._sync_word) if self._sync_word is not None else 'n/a'}, "
                f"DIO0 on GPIO{_gpio.DIO0})"
            )
            return True

    def stop(self):
        """Stop the RX service and release the radio.

        IMPORTANT: this never joins the thread that is calling it. The
        listener thread used to call stop() in order to re-initialise the
        radio, and joining itself raises RuntimeError("cannot join current
        thread") - which aborted the whole recovery path and left the
        receiver dead.
        """
        with self._lifecycle:
            self._running = False
            current = threading.current_thread()
            for t in (self._watcher_thread, self._thread):
                if t is not None and t is not current and t.is_alive():
                    t.join(timeout=2.0)
            # Leave the radio in LoRa standby on 433 MHz (safe resting state).
            try:
                if self._spi is not None:
                    self._wr(REG_OP_MODE, 0x89)
                    self._wr(REG_IRQ_FLAGS, 0xFF)
            except Exception:
                pass
            if self._dio0_request is not None:
                try:
                    self._dio0_request.release()
                except Exception:
                    pass
                self._dio0_request = None
            if self._spi is not None:
                try:
                    self._spi.close()
                except Exception:
                    pass
                self._spi = None

    def get_status(self) -> dict:
        """Return current RX status for backend/frontend."""
        with self._lock:
            return {
                "hardware_available": self._hardware_available,
                "running": self._running,
                "model": self._radio_model,
                "frequency_mhz": self._frequency_mhz,
                "rx_count": self._rx_count,
                "crc_fail": self._crc_fail,
                "duplicate_count": self._duplicate_count,
                "last_packet": self._last_packet,
                "last_rssi": self._last_rssi,
                "last_snr": self._last_snr,
                "last_rx": self._last_rx,
                "dio0_irq_count": self._dio0_irq_count,
                "rx_errors": self._rx_errors,
                "reason": self._reason,
                # Recovery visibility: a silent recovery is indistinguishable
                # from a healthy receiver on the dashboard.
                "reinit_count": self._reinit_count,
                "last_reinit": self._last_reinit,
                "last_error": self._last_error,
                "reinit_pending": self._reinit_pending,
                # The sync word actually read back from the radio.
                "sync_word": (
                    "0x%02x" % self._sync_word if self._sync_word is not None else None
                ),
            }

    # ── DIO0 watcher (interrupt, with polling fallback) ─────────────────────
    def _dio0_watcher(self):
        """Watch GPIO18 (DIO0); on rising edge read the FIFO.

        Uses gpiod (the kernel GPIO subsystem) — the old RPi.GPIO/sysfs edge
        path does not work on this kernel. Falls back to register polling of
        RegIrqFlags when gpiod is unavailable.
        """
        request = None
        if _GPIOD_AVAILABLE:
            try:
                request = gpiod.request_lines(
                    "/dev/gpiochip0",
                    consumer="aeris-dio0",
                    config={
                        _gpio.DIO0: gpiod.LineSettings(
                            direction=Direction.INPUT,
                            bias=Bias.PULL_DOWN,
                            edge_detection=Edge.RISING,
                            event_clock=Clock.MONOTONIC,
                        )
                    },
                    event_buffer_size=16,
                )
                self._dio0_request = request
                logger.info("DIO0 watcher armed on GPIO18 via gpiod")
            except Exception as exc:
                logger.warning(f"DIO0 watcher unavailable ({exc}) — falling back to IRQ polling")
                request = None

        while self._running:
            try:
                if request is not None:
                    if request.wait_edge_events(timeout=0.25):
                        events = request.read_edge_events()
                        for _ in events:
                            self._drain_fifo()
                else:
                    # Polling fallback: read RegIrqFlags directly.
                    time.sleep(0.025)
                    self._poll_for_packet()
            except Exception as e:
                logger.error(f"DIO0 watcher error: {e}")
                with self._lock:
                    self._rx_errors += 1
                time.sleep(0.5)

        if request is not None:
            try:
                request.release()
            except Exception:
                pass
            self._dio0_request = None

    def _poll_for_packet(self):
        """Fallback when gpiod is unavailable: check RegIrqFlags directly."""
        if self._spi is None:
            return
        with _gpio.RADIO_LOCK:
            try:
                irq = self._rd(REG_IRQ_FLAGS)
            except Exception:
                return
            if irq & (_IRQ_RX_DONE | _IRQ_PAYLOAD_CRC_ERROR):
                self._drain_fifo()
            elif irq & _IRQ_RX_TIMEOUT:
                # No packet: clear the timeout flag, nothing to read.
                try:
                    self._wr(REG_IRQ_FLAGS, _IRQ_RX_TIMEOUT)
                except Exception:
                    pass

    def _drain_fifo(self):
        """Read a received packet out of the FIFO (called from the watcher)."""
        if self._spi is None:
            return
        with _gpio.RADIO_LOCK:
            self._drain_fifo_locked()

    def _drain_fifo_locked(self):
        try:
            nb = self._rd(REG_RX_NB_BYTES)
            addr = self._rd(REG_FIFO_RX_CURRENT_ADDR)
            self._wr(REG_FIFO_ADDR_PTR, addr)
            data = _sensors.sx1278_read_fifo(self._spi, nb)
            if not data:
                # Nothing in the FIFO (e.g. a CRC-error frame with no payload)
                # - clear the flags and wait for the next real packet.
                try:
                    self._wr(REG_IRQ_FLAGS, 0xFF)
                except Exception:
                    pass
                return
            # RSSI/SNR (only meaningful on RxDone)
            try:
                rssi = self._rd(REG_PKT_RSSI_VALUE) - 157
                snr_raw = self._rd(REG_PKT_SNR_VALUE)
                snr = ((snr_raw - 256) if snr_raw > 127 else snr_raw) / 4.0
            except Exception:
                rssi = None
                snr = None
            try:
                self._wr(REG_IRQ_FLAGS, 0xFF)  # clear flags
            except Exception:
                pass
            with self._lock:
                self._dio0_irq_count += 1
                self._last_rssi = rssi
                self._last_snr = snr
                self._rx_errors = 0
                self._pending_packets.append((data, rssi, snr))
        except Exception as e:
            logger.error(f"FIFO drain error: {e}")
            with self._lock:
                self._rx_errors += 1

    def _listen_loop(self):
        """Process packets queued by the DIO0 watcher; reconnect on failure."""
        while self._running:
            try:
                packet = None
                with self._lock:
                    if self._pending_packets:
                        packet = self._pending_packets.pop(0)
                if packet is not None:
                    self._handle_packet(packet[0])
                else:
                    time.sleep(0.02)
            except Exception as e:
                logger.error(f"RX loop error: {e}")
                with self._lock:
                    self._rx_errors += 1
                time.sleep(0.5)
                # Reconnect: re-initialise the radio after repeated failures.
                # The re-initialisation runs on a DEDICATED thread - it used
                # to run inline here, where stop() tried to join this very
                # thread and raised "cannot join current thread".
                with self._lock:
                    errors = self._rx_errors
                if errors >= 10:
                    logger.warning("LoRa RX: too many errors - reinitialising the radio")
                    self._schedule_reinit("too many RX errors")
                    # Leave the loop: the recovery thread takes over the radio.
                    return
                with self._lock:
                    self._rx_errors = 0

    def _schedule_reinit(self, reason: str):
        """Hand the radio over to a fresh instance on a dedicated thread.

        Only one recovery may run at a time (_reinit_pending), so two
        failures cannot open two SPI handles on the same radio.
        """
        with self._lock:
            if self._reinit_pending:
                logger.warning("LoRa RX: a re-initialisation is already running")
                return
            self._reinit_pending = True
            self._reinit_count += 1
            self._last_reinit = datetime.now().isoformat()
        logger.warning(f"LoRa RX: scheduling a radio re-initialisation ({reason})")
        threading.Thread(
            target=self._reinit_worker,
            args=(reason,),
            daemon=True,
            name="aeris-lora-reinit",
        ).start()

    def _reinit_worker(self, reason: str):
        """Stop the receiver and start it again on the same radio."""
        try:
            # stop() is called from THIS thread, which is neither the listener
            # nor the DIO0 watcher - so the join inside it is safe.
            self.stop()
            time.sleep(0.5)
            started = self.start()
            if started:
                logger.info("LoRa RX: radio re-initialised successfully")
            else:
                self._last_error = self._reason or "re-initialisation failed"
                logger.error(f"LoRa RX: re-initialisation failed: {self._last_error}")
        except Exception as exc:
            self._last_error = f"reconnect failed: {exc}"
            logger.error(f"LoRa RX reconnect failed: {exc}")
        finally:
            with self._lock:
                self._reinit_pending = False

    def _handle_packet(self, raw: bytes):
        """Process a received raw packet."""
        if not _PROTO_AVAILABLE:
            return
        pkt = _proto.decode_packet(raw)
        if pkt is None:
            with self._lock:
                self._crc_fail += 1
            return

        seq = pkt["seq"]
        # Dedup
        if seq in self._seen_seqs:
            with self._lock:
                self._duplicate_count += 1
            return
        self._seen_seqs.add(seq)
        # Keep set bounded
        if len(self._seen_seqs) > 1000:
            self._seen_seqs = set(list(self._seen_seqs)[-500:])

        with self._lock:
            self._rx_count += 1
            self._last_rx = datetime.now().isoformat()
            logger.info(
                f"LoRa RX: packet type={pkt['type']} seq={pkt['seq']} "
                f"len={pkt['length']} rssi={self._last_rssi} snr={self._last_snr}"
            )

        # Handle by type
        if pkt["type"] == _proto.PKT_THERMAL_DATA:
            result = _proto.decode_thermal_payload(pkt["payload"])
            if result:
                meta, chunk_bytes = result
                if self._reassembler:
                    reassembly = self._reassembler.add_chunk(meta, chunk_bytes)
                    if reassembly and reassembly[1]:  # complete frame
                        frame_id, complete, full_bytes, frame_meta = reassembly
                        self._last_packet = {
                            "frame_id": frame_id,
                            "timestamp": frame_meta.get("ts"),
                            "temp_min": frame_meta.get("mn"),
                            "temp_max": frame_meta.get("mx"),
                            "hotspot_x": frame_meta.get("hx"),
                            "hotspot_y": frame_meta.get("hy"),
                            "detected": frame_meta.get("det"),
                            "confidence": frame_meta.get("conf"),
                            "gps_lat": frame_meta.get("lat"),
                            "gps_lng": frame_meta.get("lng"),
                            "battery": frame_meta.get("bat"),
                        }
                        if self._on_packet:
                            self._on_packet(self._last_packet)

        # Send ACK if required
        if pkt.get("requires_ack"):
            self._send_ack(seq)

    def _send_ack(self, seq: int):
        """Send ACK for a received packet (requires TX capability on ground radio)."""
        if not self._hardware_available:
            return
        # ACK sending would use the radio's TX mode
        # self._radio.transmit(_proto.encode_packet(_proto.PKT_ACK, b"", ack_seq=seq))
        pass
