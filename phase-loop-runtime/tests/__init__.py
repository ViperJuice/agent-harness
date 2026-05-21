"""Tests for the vendored phase-loop runtime package."""

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _TESTS_DIR.parent / "src"

# Make test helpers importable as bare names (e.g., `from phase_loop_test_utils import ...`)
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# Make the phase_loop_runtime package importable without requiring `pip install -e`
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
