"""AERIS backend test package.

The bootstrap lives in ``_bootstrap.py`` so that it runs in every supported
invocation (see that module's docstring). It is imported here so that the
package form - ``tests.test_x`` - is bootstrapped as well.
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import _bootstrap  # noqa: E402,F401  (the bootstrap must run before the backend)
