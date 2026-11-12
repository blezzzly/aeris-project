"""P1-3 - MLX90640 capture timeout and recovery.

Two DIFFERENT timeouts are involved and they are not the same thing:

  1. The Python-level frame deadline (THERMAL_FRAME_TIMEOUT_S) bounds the
     vendor library's unbounded "while dataReady == 0" wait - the case where
     the sensor answers but stops producing new frames. This IS recoverable
     in-process.

  2. The kernel's I2C adapter timeout governs a transaction that is already
     blocked inside the kernel. A Python deadline CANNOT interrupt that; the
     capture thread's heartbeat stops advancing and the state is reported as
     "stalled".

The tests below use a fake sensor - no /dev/i2c-* is ever opened.
"""
import threading
import time
import unittest

# The test bootstrap must run BEFORE the backend is imported.
import _bootstrap  # noqa: F401

import main
from fakes import FakeI2C, FakeMLX90640


class ThermalDeadlineTests(unittest.TestCase):
    """T1 - T5"""

    def setUp(self):
        self._orig_init = main._init_thermal_sensor
        self._orig_timeout = main.THERMAL_FRAME_TIMEOUT_S
        self._orig_backoff = main.THERMAL_REINIT_BACKOFF_S
        main.THERMAL_FRAME_TIMEOUT_S = 0.3
        main.THERMAL_REINIT_BACKOFF_S = (0.02, 0.04, 0.08, 0.16, 0.32, 0.64)
        # The capture state is a module-level counter - it has to be reset
        # so the tests do not see each other's re-initialisations.
        self._orig_state = dict(main._thermal_capture_state)
        main._thermal_capture_state.update({
            "phase": "starting", "heartbeat": 0.0, "reinit_count": 0,
            "consecutive_failures": 0, "invalid_samples": 0,
            "last_error": None, "backoff_s": 0.0,
        })

    def tearDown(self):
        main._init_thermal_sensor = self._orig_init
        main.THERMAL_FRAME_TIMEOUT_S = self._orig_timeout
        main.THERMAL_REINIT_BACKOFF_S = self._orig_backoff
        main._thermal_capture_state.update(self._orig_state)

    # ── T1: the Python-level deadline turns the unbounded wait into a
    #       bounded one -----------------------------------------------------
    def test_t1_frame_deadline_raises_when_it_expires(self):
        deadline = main._FrameDeadline()
        deadline.arm(0.05)
        time.sleep(0.12)
        with self.assertRaises(main.ThermalTimeoutError):
            deadline.check()

    def test_t1b_disarmed_deadline_never_raises(self):
        deadline = main._FrameDeadline()
        deadline.arm(0.01)
        deadline.disarm()
        time.sleep(0.05)
        deadline.check()   # must not raise

    def test_t1c_guard_wraps_the_vendor_i2c_primitives(self):
        """The guard makes the vendor's unbounded wait bounded."""
        sensor = FakeMLX90640(mode="block")
        deadline = main._FrameDeadline()
        deadline.arm(0.2)

        self.assertTrue(main._arm_frame_guard(sensor, deadline))
        self.assertTrue(getattr(sensor, "_aeris_guard_armed", False))

        # The fake mimics adafruit_mlx90640._GetFrameData: it polls the
        # status register until the data-ready bit is set. Without the guard
        # that loop never ends.
        with self.assertRaises(main.ThermalTimeoutError):
            sensor.getFrame([0.0] * 768)

    def test_t1d_guard_is_installed_only_once(self):
        sensor = FakeMLX90640(mode="ok")
        deadline = main._FrameDeadline()
        main._arm_frame_guard(sensor, deadline)
        wrapped = sensor._I2CReadWords
        main._arm_frame_guard(sensor, deadline)
        self.assertIs(sensor._I2CReadWords, wrapped,
                      "arming twice must not wrap the wrapper again")

    # ── T2: the old bus handle is released before a new sensor is created
    def test_t2_reinit_releases_the_previous_handle(self):
        old_sensor = FakeMLX90640(mode="ok")
        old_bus = FakeI2C()
        new_sensor = FakeMLX90640(mode="ok")
        new_bus = FakeI2C()

        created = []

        def fake_init():
            created.append(new_sensor)
            return new_sensor, new_bus, None

        main._init_thermal_sensor = fake_init
        deadline = main._FrameDeadline()

        mlx, i2c, backoff_idx, error = main._thermal_reinit(
            old_sensor, old_bus, deadline, 0
        )

        self.assertIsNone(error, "the re-initialisation must succeed")
        self.assertIs(mlx, new_sensor)
        self.assertIs(i2c, new_bus)
        self.assertTrue(
            old_bus.closed,
            "the previous I2C handle must be closed - otherwise every "
            "re-initialisation leaks one file descriptor",
        )

    # ── T3: exponential back-off ------------------------------------------
    def test_t3_backoff_sequence_is_exponential(self):
        """The observed delays grow exponentially while the sensor stays down."""
        observed = []
        bus = FakeI2C()

        def failing_init():
            raise RuntimeError("sensor not present")

        main._init_thermal_sensor = failing_init
        deadline = main._FrameDeadline()

        original_backoff = main.THERMAL_REINIT_BACKOFF_S
        try:
            main.THERMAL_REINIT_BACKOFF_S = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
            for index in range(4):
                main._thermal_capture_state["backoff_s"] = 0.0
                main._thermal_reinit(None, bus, deadline, index)
                observed.append(main._thermal_capture_state["backoff_s"])
        finally:
            main.THERMAL_REINIT_BACKOFF_S = original_backoff

        self.assertEqual(observed, [0.01, 0.02, 0.04, 0.08],
                         "the back-off must double after every attempt")
        for i in range(1, len(observed)):
            self.assertAlmostEqual(observed[i], observed[i - 1] * 2, places=6)

    def test_t3b_production_backoff_values(self):
        """The production back-off is 1, 2, 4, 8, 16 s, capped at 30 s."""
        self.assertEqual(
            self._orig_backoff, (1.0, 2.0, 4.0, 8.0, 16.0, 30.0),
            "the back-off must be 1, 2, 4, 8, 16 s and then capped at 30 s",
        )
        self.assertEqual(self._orig_timeout, 3.0,
                         "the frame deadline must be 3 s")

    def test_t3c_failed_reinit_reports_the_error(self):
        def failing_init():
            raise RuntimeError("sensor not present")

        main._init_thermal_sensor = failing_init
        deadline = main._FrameDeadline()
        mlx, i2c, backoff_idx, error = main._thermal_reinit(
            None, None, deadline, 0
        )
        self.assertIsNone(mlx)
        self.assertIsNone(i2c)
        self.assertIn("reinit failed", error)
        self.assertEqual(backoff_idx, 1, "the back-off index must advance")

    # ── T4/T5: the capture loop reacts correctly ---------------------------
    def _run_capture_loop(self, factory, wait_for, timeout=8.0):
        """Run the capture loop in a daemon thread with a fake sensor."""
        main._init_thermal_sensor = factory
        thread = threading.Thread(target=main._thermal_capture_loop,
                                  daemon=True, name="test-thermal-capture")
        thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if wait_for():
                return thread
            time.sleep(0.05)
        return thread

    def test_t4_capture_loop_times_out_and_reinitialises(self):
        """A sensor that stops producing frames must not spin the CPU."""
        sensors = []

        def factory():
            sensor = FakeMLX90640(mode="block")
            bus = FakeI2C()
            sensors.append((sensor, bus))
            return sensor, bus, None

        # Wait for the SECOND sensor to appear: the re-initialisation
        # releases the old sensor first and creates the new one afterwards,
        # so a second sensor proves the re-initialisation completed.
        thread = self._run_capture_loop(
            factory,
            lambda: len(sensors) >= 2,
        )
        try:
            self.assertGreaterEqual(
                main._thermal_capture_state.get("reinit_count", 0), 1,
                "the capture loop must re-initialise the sensor after the "
                "frame deadline expires",
            )
            self.assertGreaterEqual(
                len(sensors), 1,
                "the capture loop must have brought the sensor up",
            )
            self.assertTrue(
                sensors[0][1].closed,
                "the previous I2C handle must be closed on re-initialisation",
            )
            snapshot = main._thermal_status_snapshot()
            self.assertFalse(snapshot["ok"],
                             "a sensor that produces no frame must not be 'ok'")
            self.assertIsNotNone(snapshot["error"])
            self.assertIn(
                "no frame from the mlx90640", snapshot["error"].lower(),
                "the failure reason must name the frame timeout",
            )
        finally:
            # Stop the daemon loop: make the sensor come up and then let the
            # test session end. The thread is a daemon, so it cannot keep
            # the process alive.
            main._init_thermal_sensor = self._orig_init

    def test_t5_frame_with_invalid_samples_is_discarded(self):
        """A frame with non-finite samples must never be published."""
        sensor = FakeMLX90640(mode="nan", nan_count=4, frame_interval=0.01)

        def factory():
            return sensor, FakeI2C(), None

        main._init_thermal_sensor = factory
        main._thermal_capture_state["invalid_samples"] = 0
        main._thermal_status.update({"ok": True, "error": None})

        thread = threading.Thread(target=main._thermal_capture_loop,
                                  daemon=True, name="test-thermal-nan")
        thread.start()
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if main._thermal_capture_state.get("invalid_samples", 0) > 0:
                break
            time.sleep(0.05)

        try:
            self.assertGreater(
                main._thermal_capture_state.get("invalid_samples", 0), 0,
                "the invalid samples must be counted",
            )
            snapshot = main._thermal_status_snapshot()
            self.assertFalse(
                snapshot["ok"],
                "a frame with invalid samples must not be reported as a valid "
                "measurement",
            )
            self.assertIn("invalid", snapshot["error"].lower())
        finally:
            main._init_thermal_sensor = self._orig_init

    def test_t6_failure_counter_is_not_reset_by_a_reinit(self):
        """The failure counter is cleared by a successful FRAME only."""
        source = open(main.__file__, "r", encoding="utf-8").read()
        # The re-init helper must not touch the failure counter.
        helper = source.split("def _thermal_reinit(")[1].split("\ndef ")[0]
        self.assertNotIn(
            "consec_fail", helper,
            "the re-initialisation must not reset the failure counter - only a "
            "successful frame may do that",
        )


class ThermalIndependenceTests(unittest.TestCase):
    """T6 - a thermal failure must not interrupt the rest of the system."""

    def test_t6_other_subsystems_keep_answering(self):
        """While the thermal capture is failing, the API still answers."""
        import json as _json
        import urllib.request

        # The real running backend is NOT touched - this checks the module's
        # own reporting path with the capture thread degraded.
        sensor = FakeMLX90640(mode="block")

        def factory():
            return sensor, FakeI2C(), None

        original = main._init_thermal_sensor
        main._init_thermal_sensor = factory
        thread = threading.Thread(target=main._thermal_capture_loop,
                                  daemon=True, name="test-thermal-independence")
        thread.start()
        try:
            time.sleep(0.8)
            # The system status is computed from /proc - it must work.
            stats = main._read_system_stats()
            self.assertIsNotNone(stats["cpu_count"])
            self.assertIsNotNone(stats["uptime_s"])
            # The sensor status (GPS/battery/barometer/compass) must work.
            status = main._read_sensor_status()
            self.assertIsInstance(status, dict)
            # The LoRa status must work.
            lora = main._read_hardware_status()
            self.assertIn("mlx90640", lora)
            self.assertIn("lora_sx1278", lora)
            # The thermal snapshot must report the failure, not a fake "ok".
            snapshot = main._thermal_status_snapshot()
            self.assertFalse(snapshot["ok"])
        finally:
            main._init_thermal_sensor = original


if __name__ == "__main__":
    unittest.main()
