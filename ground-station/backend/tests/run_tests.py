#!/usr/bin/env python3
"""Run the AERIS Phase 1 regression suite.

Standard library only - no pytest, no httpx, no network access, no real
hardware. The suite never opens ``/dev/i2c-*`` or ``/dev/spidev*``, never
toggles a GPIO line and never transmits a LoRa packet.

Usage:
    python3 -m unittest discover -s tests -v      (from the backend folder)
    python3 tests/run_tests.py                     (from anywhere)

The test authentication token and the throw-away SQLite database are set up
by ``tests/__init__.py`` BEFORE the backend is imported, so the production
token file and the production database are never touched.
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbosity = 2 if ("-v" in argv or "--verbose" in argv or not argv) else 1

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=TESTS_DIR, pattern="test_*.py",
                            top_level_dir=BACKEND_DIR)
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    print("")
    print("=" * 68)
    print(f"  tests run    : {result.testsRun}")
    print(f"  failures     : {len(result.failures)}")
    print(f"  errors       : {len(result.errors)}")
    print(f"  skipped      : {len(result.skipped)}")
    print("=" * 68)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
