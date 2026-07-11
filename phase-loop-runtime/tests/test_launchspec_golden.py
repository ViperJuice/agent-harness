"""EXECREG acceptance spine — normalized full-``LaunchSpec`` golden.

Captures the ``LaunchSpec`` every pre-existing executor (codex, claude, gemini,
opencode, pi, command, manual) resolves to, with fully pinned inputs + env, and
asserts it is byte-identical to the golden frozen on the pre-refactor base commit.

This is the acceptance bar for rewriting ``build_launch_spec`` to delegate to
``record.build_command`` (IF-0-EXECREG-1): the port must be spec-identical for
every existing executor. It covers route/policy (claude) and stub (manual) /
adapter (command) metadata, not just argv.

Regenerate intentionally (only when a behavior change is deliberate and reviewed):

    PYTHONPATH=src:tests PHASE_LOOP_REGEN_LAUNCHSPEC_GOLDEN=1 \
        python3 -m pytest tests/test_launchspec_golden.py
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from _launchspec_golden_cases import build_cases

_GOLDEN_PATH = Path(__file__).parent / "data" / "launchspec_golden" / "launchspec_golden.json"


def _load_golden() -> dict:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


class LaunchSpecGoldenTest(unittest.TestCase):
    def test_launchspec_golden_is_byte_identical(self):
        cases = build_cases()

        if os.environ.get("PHASE_LOOP_REGEN_LAUNCHSPEC_GOLDEN") == "1":
            _GOLDEN_PATH.write_text(
                json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            self.skipTest("regenerated launchspec golden")

        golden = _load_golden()
        self.assertEqual(
            set(cases), set(golden), "executor case set drifted from the golden"
        )
        for name in sorted(golden):
            with self.subTest(case=name):
                # Compare canonicalized JSON so the diff points at the drifting field.
                self.assertEqual(
                    json.dumps(cases[name], indent=2, sort_keys=True),
                    json.dumps(golden[name], indent=2, sort_keys=True),
                    f"LaunchSpec for {name!r} diverged from the pre-refactor golden",
                )


if __name__ == "__main__":
    unittest.main()
