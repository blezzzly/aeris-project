"""Test bootstrap for the AERIS backend regression suite.

Self-contained on purpose: every test module imports it FIRST, so it must
work in every supported invocation:

    python3 tests/run_tests.py                       (tests/ added by the runner)
    python3 -m unittest discover -s tests -v         (unittest adds tests/)
    python3 -m unittest discover -s tests -t . -v    (the tests package)

In all three cases this module must run BEFORE the backend is imported,
because it:

  * exports a DEDICATED test token, so the real ``.aeris_auth_token`` is
    never read and never written,
  * redirects every SQLite connection to a throw-away database, so the real
    detection database is never touched,
  * disarms the real hardware (the thermal capture thread and the sensor
    bring-up), so no ``/dev/i2c-*`` and no ``/dev/spidev*`` is ever opened.

Nothing here needs a third-party package: standard library only.
"""
import os
import sqlite3
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)

for _path in (BACKEND_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ── A dedicated test token - the real token file is never touched ──────────
TEST_OPERATOR_TOKEN = "aeris-test-operator-token-0123456789"
TEST_VIEWER_TOKEN = "aeris-test-viewer-token-0123456789"
os.environ["AERIS_AUTH_TOKEN"] = TEST_OPERATOR_TOKEN
os.environ["AERIS_VIEWER_TOKEN"] = TEST_VIEWER_TOKEN

# Keep the backend from claiming the real radio.
os.environ.setdefault("AERIS_SKIP_LORA_RX", "1")

# Tests must never make an outbound weather request.
os.environ.setdefault("AERIS_SKIP_WEATHER", "1")

# ── Redirect every SQLite connection to a throw-away file ───────────────────
_TEST_DB_DIR = tempfile.mkdtemp(prefix="aeris-tests-")
_real_connect = sqlite3.connect


def _redirected_connect(target, *args, **kwargs):
    if isinstance(target, str) and target.endswith("aeris_detections.db"):
        target = os.path.join(_TEST_DB_DIR, "aeris_detections.db")
    return _real_connect(target, *args, **kwargs)


sqlite3.connect = _redirected_connect

# ── Never touch the real hardware ───────────────────────────────────────────
# The backend's API endpoints call _ensure_thermal_thread(), which starts the
# real MLX90640 capture thread - and that thread opens /dev/i2c-1. The suite
# replaces the sensor with a fake, so the real capture thread is never
# started here. The second guard covers the sensor bring-up itself.
import main as _main  # noqa: E402  (must come after the token setup above)


def _no_real_thermal_thread():
    """Never start the real thermal capture thread from a test."""
    return None


def _no_real_sensor(*_args, **_kwargs):
    """Refuse to open the real I2C bus."""
    raise RuntimeError(
        "the test suite must never open the real I2C bus - "
        "patch main._init_thermal_sensor with a fake"
    )


_main._ensure_thermal_thread = _no_real_thermal_thread
_main._init_thermal_sensor = _no_real_sensor
