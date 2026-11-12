"""P1-4 - LoRa receiver lifecycle and self-recovery.

Before this fix the listener thread called stop() in order to re-initialise
the radio, and stop() joined the very thread that was calling it:
``RuntimeError: cannot join current thread``. The exception escaped, the
radio was never re-initialised, the listener left its loop and the receiver
died silently - with no indication anywhere that it had stopped working.

These tests drive the real LoRaRxService against a fake radio. No SPI
device, no GPIO line and no RF transmission is involved.
"""
import threading
import time
import unittest

import lora_rx_service
from fakes import FakeSensors


class LoRaLifecycleTests(unittest.TestCase):
    """R1 - R5"""

    def setUp(self):
        self._orig_sensors = lora_rx_service._sensors
        self._orig_gpiod = lora_rx_service._GPIOD_AVAILABLE
        self.sensors = FakeSensors()
        lora_rx_service._sensors = self.sensors
        # Disable the gpiod edge detector so the DIO0 watcher uses its
        # polling fallback, which only talks to the fake SPI. The real
        # GPIO18 line is never requested.
        lora_rx_service._GPIOD_AVAILABLE = False

    def tearDown(self):
        lora_rx_service._sensors = self._orig_sensors
        lora_rx_service._GPIOD_AVAILABLE = self._orig_gpiod

    def _service(self):
        return lora_rx_service.LoRaRxService(on_packet=None)

    # ── R1: stop() must never join the thread that is calling it ──────────
    def test_r1_stop_from_the_listener_thread_does_not_raise(self):
        svc = self._service()
        errors = []

        def listener():
            # Pretend to be the listener thread: the old code joined
            # self._thread, which here IS the calling thread.
            svc._thread = threading.current_thread()
            try:
                svc.stop()
            except Exception as exc:  # pragma: no cover - the failure path
                errors.append(exc)

        thread = threading.Thread(target=listener, name="test-listener")
        thread.start()
        thread.join(timeout=5)

        self.assertEqual(
            errors, [],
            "stop() must not raise 'cannot join current thread' when it is "
            "called from the listener thread",
        )
        self.assertFalse(svc.is_running, "stop() must leave the service stopped")

    def test_r1b_stop_joins_the_other_thread(self):
        """stop() still joins the OTHER worker thread (the DIO0 watcher)."""
        svc = self._service()
        started = threading.Event()

        def watcher():
            started.set()
            while svc.is_running:
                time.sleep(0.02)

        t = threading.Thread(target=watcher, daemon=True)
        svc._watcher_thread = t
        svc._running = True
        t.start()
        started.wait(2)
        svc.stop()
        self.assertFalse(t.is_alive(), "the watcher thread must be joined")
        self.assertFalse(svc.is_running)

    # ── R2: the receiver recovers after repeated errors ───────────────────
    def test_r2_receiver_recovers_after_repeated_errors(self):
        svc = self._service()
        self.assertTrue(svc.start(), "the fake radio must start")
        first_handle = self.sensors.handles[-1]

        # Make the packet handling fail and pre-load the error counter so the
        # recovery threshold is reached after a single error.
        def failing_packet(_raw):
            raise RuntimeError("simulated RX failure")

        svc._handle_packet = failing_packet
        svc._rx_errors = 9
        svc._pending_packets.append((b"\x00", None, None))

        thread = threading.Thread(target=svc._listen_loop,
                                  name="test-listen")
        thread.start()

        # Wait for the recovery to COMPLETE - not only for it to be
        # scheduled. The recovery runs on its own thread: it stops the
        # receiver (which closes the SPI handle) and starts it again.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            status = svc.get_status()
            if status["reinit_count"] >= 1 and not status["reinit_pending"] \
                    and self.sensors.init_calls >= 2:
                break
            time.sleep(0.05)
        thread.join(timeout=3)

        try:
            status = svc.get_status()
            self.assertGreaterEqual(
                status["reinit_count"], 1,
                "the receiver must re-initialise the radio after repeated "
                "errors",
            )
            self.assertIsNotNone(status["last_reinit"],
                                 "the recovery must be visible in the status")
            self.assertTrue(
                first_handle.closed,
                "the old SPI handle must be closed when the radio is replaced",
            )
            self.assertGreaterEqual(
                self.sensors.init_calls, 2,
                "the radio must be initialised again after the failure",
            )
            self.assertTrue(
                svc.is_running,
                "the receiver must be running again after the recovery",
            )
        finally:
            svc.stop()

    def test_r2b_recovery_is_visible_in_the_status(self):
        svc = self._service()
        for field in ("reinit_count", "last_reinit", "last_error",
                      "reinit_pending", "sync_word"):
            self.assertIn(field, svc.get_status())

    # ── R3: start() is idempotent - one handle, one watcher ───────────────
    def test_r3_start_is_idempotent(self):
        svc = self._service()
        try:
            self.assertTrue(svc.start())
            first_handle = self.sensors.handles[-1]
            watcher = svc._watcher_thread
            listener = svc._thread

            self.assertTrue(svc.start(), "a second start() must succeed")
            self.assertEqual(
                self.sensors.init_calls, 1,
                "a second start() must not open a second SPI handle",
            )
            self.assertIs(svc._watcher_thread, watcher,
                          "a second start() must not start a second DIO0 watcher")
            self.assertIs(svc._thread, listener,
                          "a second start() must not start a second listener")
            self.assertFalse(first_handle.closed,
                             "the running handle must stay open")
        finally:
            svc.stop()

    # ── R4: stop() releases the SPI and GPIO resources ────────────────────
    def test_r4_stop_releases_the_spi_handle(self):
        svc = self._service()
        self.assertTrue(svc.start())
        handle = self.sensors.handles[-1]
        svc.stop()
        self.assertTrue(handle.closed, "the SPI handle must be closed")
        self.assertIsNone(svc._spi, "the SPI handle reference must be cleared")
        self.assertFalse(svc.is_running)

    def test_r4b_stop_releases_the_gpio_request(self):
        svc = self._service()

        class _Request:
            def __init__(self):
                self.released = False

            def release(self):
                self.released = True

        request = _Request()
        svc._dio0_request = request
        svc.stop()
        self.assertTrue(request.released, "the GPIO request must be released")
        self.assertIsNone(svc._dio0_request)

    # ── R5: only one recovery may run at a time ──────────────────────────
    def test_r5_reinit_pending_guard(self):
        svc = self._service()
        svc._reinit_pending = True       # a recovery is already running
        before = svc.get_status()["reinit_count"]

        svc._schedule_reinit("simulated")

        self.assertEqual(
            svc.get_status()["reinit_count"], before,
            "a second recovery must not start while one is pending",
        )

    def test_r5b_reinit_pending_is_cleared_after_a_recovery(self):
        svc = self._service()
        self.assertTrue(svc.start())
        try:
            svc._schedule_reinit("simulated")
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if not svc.get_status()["reinit_pending"]:
                    break
                time.sleep(0.05)
            self.assertFalse(
                svc.get_status()["reinit_pending"],
                "the recovery guard must be cleared when the recovery ends",
            )
            self.assertTrue(svc.is_running,
                            "the receiver must be running after a recovery")
        finally:
            svc.stop()


if __name__ == "__main__":
    unittest.main()
