"""P1-1 - LoRa sync-word consistency.

The SX1276/78 in LoRa mode only accepts a packet whose private network word
matches its own. The drone transmitter cannot change its own sync word
(pySX127x implements set_sync_word() as a no-op stub), so it always uses the
SX1276/78 silicon default 0x12 - and the ground receiver has to use the same
value. Before this fix the ground receiver programmed 0x34, so the two radios
could never decode each other: dio0_irq_count climbed while rx_count stayed
at 0 and crc_fail grew.

These tests are hardware-free: the radio is a FakeSpi.
"""
import os
import re
import unittest

# The test bootstrap must run BEFORE the backend is imported.
import _bootstrap  # noqa: F401

import lora_protocol
import lora_rx_service
from fakes import FakeSensors


class SyncWordConsistencyTests(unittest.TestCase):
    """L1 - L5"""

    # ── L1: one shared constant, used by both ends ────────────────────────
    def test_l1_shared_sync_word_constant(self):
        self.assertEqual(
            lora_protocol.SYNC_WORD, 0x12,
            "the shared sync word must be the SX1276/78 silicon default 0x12",
        )
        self.assertEqual(
            lora_rx_service.REG_WRITE_SYNC_WORD,
            lora_protocol.SYNC_WORD,
            "the ground receiver must use the shared constant, not its own",
        )

    # ── L2: the ground receiver programs Reg 0x39 with 0x12 ────────────────
    def test_l2_receiver_programs_sync_word_register(self):
        sensors = FakeSensors()
        original = lora_rx_service._sensors
        original_gpiod = lora_rx_service._GPIOD_AVAILABLE
        lora_rx_service._sensors = sensors
        # Disable the gpiod path so the DIO0 watcher uses the polling
        # fallback - that path only talks to the fake SPI.
        lora_rx_service._GPIOD_AVAILABLE = False
        svc = lora_rx_service.LoRaRxService(on_packet=None)
        try:
            self.assertTrue(svc.start(), "the fake radio must start")
            spi = sensors.handles[-1]
            self.assertIn(
                0x12, spi.written_to(0x39),
                f"Reg 0x39 must be programmed with 0x12, writes were {spi.writes}",
            )
        finally:
            svc.stop()
            lora_rx_service._sensors = original
            lora_rx_service._GPIOD_AVAILABLE = original_gpiod

    # ── L3: the reported sync word is READ BACK, not assumed ───────────────
    def test_l3_sync_word_is_read_back_from_the_register(self):
        # The fake radio answers Reg 0x39 with 0x34 - a value that differs
        # from the constant. The status must report what the radio actually
        # holds, not the constant.
        sensors = FakeSensors(read_overrides={0x39: 0x34})
        original = lora_rx_service._sensors
        original_gpiod = lora_rx_service._GPIOD_AVAILABLE
        lora_rx_service._sensors = sensors
        lora_rx_service._GPIOD_AVAILABLE = False
        svc = lora_rx_service.LoRaRxService(on_packet=None)
        try:
            self.assertTrue(svc.start())
            status = svc.get_status()
            self.assertEqual(
                status["sync_word"], "0x34",
                "the sync word must be read back from Reg 0x39, not assumed",
            )
        finally:
            svc.stop()
            lora_rx_service._sensors = original
            lora_rx_service._GPIOD_AVAILABLE = original_gpiod

    def test_l3b_sync_word_is_exposed_in_the_receiver_status(self):
        sensors = FakeSensors()
        original = lora_rx_service._sensors
        original_gpiod = lora_rx_service._GPIOD_AVAILABLE
        lora_rx_service._sensors = sensors
        lora_rx_service._GPIOD_AVAILABLE = False
        svc = lora_rx_service.LoRaRxService(on_packet=None)
        try:
            svc.start()
            status = svc.get_status()
            for field in ("sync_word", "reinit_count", "last_reinit", "last_error"):
                self.assertIn(field, status, f"status must expose {field}")
        finally:
            svc.stop()
            lora_rx_service._sensors = original
            lora_rx_service._GPIOD_AVAILABLE = original_gpiod

    # ── L4: the startup log states the REAL configuration ─────────────────
    def test_l4_startup_log_reports_the_real_configuration(self):
        sensors = FakeSensors()
        original = lora_rx_service._sensors
        original_gpiod = lora_rx_service._GPIOD_AVAILABLE
        lora_rx_service._sensors = sensors
        lora_rx_service._GPIOD_AVAILABLE = False
        svc = lora_rx_service.LoRaRxService(on_packet=None)
        try:
            with self.assertLogs("lora_rx_service", level="INFO") as captured:
                svc.start()
            text = "\n".join(captured.output)
            self.assertIn(
                "SF6/BW500", text,
                "the startup log must state SF6/BW500 (it used to claim SF7/BW125)",
            )
            self.assertIn(
                "0x12", text,
                "the startup log must state the sync word that is in the radio",
            )
            self.assertNotIn("SF7/BW125", text)
        finally:
            svc.stop()
            lora_rx_service._sensors = original
            lora_rx_service._GPIOD_AVAILABLE = original_gpiod

    # ── L5: the drone side documents the (no-op) call ─────────────────────
    def test_l5_drone_transmitter_uses_the_shared_constant(self):
        from _bootstrap import BACKEND_DIR
        project_root = os.path.dirname(os.path.dirname(BACKEND_DIR))
        sender = os.path.join(
            project_root, "drone-onboard", "raspberry-pi-4b", "sender.py",
        )
        self.assertTrue(os.path.exists(sender), f"sender.py not found at {sender}")
        with open(sender, "r", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn(
            "lora_protocol.SYNC_WORD", source,
            "the drone transmitter must take the sync word from the shared module",
        )
        self.assertIn(
            "set_sync_word", source,
            "the drone transmitter must call set_sync_word() so the intent is explicit",
        )
        # The radio behaviour must NOT change: the modulation settings stay.
        for setting in ("set_freq(433.0)", "set_bw(BW.BW500)",
                        "set_spreading_factor(6)", "set_coding_rate(CODING_RATE.CR4_5)",
                        "set_preamble(6)", "set_rx_crc(True)"):
            self.assertIn(setting, source, f"the drone radio setting {setting} changed")

    # ── the preserved modulation parameters ───────────────────────────────
    def test_preserved_modulation_parameters(self):
        self.assertEqual(lora_protocol.FREQUENCY_HZ, 433_000_000)
        self.assertEqual(lora_protocol.BANDWIDTH_HZ, 500_000)
        self.assertEqual(lora_protocol.SPREADING_FACTOR, 6)
        self.assertEqual(lora_protocol.CODING_RATE, "4/5")
        self.assertEqual(lora_protocol.PREAMBLE_SYMBOLS, 6)
        self.assertTrue(lora_protocol.CRC_ON)
        # The packet format must not change.
        self.assertEqual(lora_protocol.MAGIC, b"\xAE\x52")
        self.assertEqual(lora_protocol.PROTOCOL_VERSION, 1)
        self.assertEqual(lora_protocol.HEADER_SIZE, 11)


if __name__ == "__main__":
    unittest.main()
