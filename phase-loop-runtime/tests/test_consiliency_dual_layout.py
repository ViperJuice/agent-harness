"""CS-0.5/CS-0.6 acceptance: a repo carrying BOTH `.consiliency/` and a legacy
`.phase-loop/` layout still runs the existing phase-loop unchanged (the
working-tooling-floor). `.consiliency/` is purely additive -- nothing in this
PR reads or writes `.phase-loop/`/`.pipeline/`, so the legacy layout must be
byte-for-byte untouched by scaffolding or gate-scanning, and CLI commands that
don't know about `.consiliency/` (e.g. `status`) must behave identically."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import make_repo
from phase_loop_runtime.consiliency_gates import scan_consiliency_gates
from phase_loop_runtime.consiliency_scaffold import scaffold

BIN = (sys.executable, "-m", "phase_loop_runtime.cli")


class ConsiliencyDualLayoutTest(unittest.TestCase):
    def _seed_legacy_phase_loop(self, repo: Path) -> tuple[Path, Path]:
        legacy = repo / ".phase-loop"
        legacy.mkdir()
        state_path = legacy / "state.json"
        events_path = legacy / "events.jsonl"
        state_path.write_text(json.dumps({"legacy": "state", "phases": {}}, indent=2) + "\n", encoding="utf-8")
        events_path.write_text('{"legacy": "event"}\n', encoding="utf-8")
        return state_path, events_path

    def test_scaffold_leaves_legacy_phase_loop_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            state_path, events_path = self._seed_legacy_phase_loop(repo)
            before_state = state_path.read_bytes()
            before_events = events_path.read_bytes()

            scaffold(repo, mode="archetyped", archetypes=("tooling-meta",))

            self.assertEqual(state_path.read_bytes(), before_state)
            self.assertEqual(events_path.read_bytes(), before_events)
            self.assertTrue((repo / ".consiliency" / "manifest.json").is_file())

    def test_gate_scan_leaves_legacy_phase_loop_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            state_path, events_path = self._seed_legacy_phase_loop(repo)
            scaffold(repo, mode="archetyped", archetypes=("tooling-meta",))
            before_state = state_path.read_bytes()
            before_events = events_path.read_bytes()

            scan_consiliency_gates(repo)
            scan_consiliency_gates(repo, env={"PHASE_LOOP_CONSILIENCY_GATES": "hard"})

            self.assertEqual(state_path.read_bytes(), before_state)
            self.assertEqual(events_path.read_bytes(), before_events)

    def test_status_command_is_unaffected_by_consiliency_layout_presence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            before = subprocess.run(
                [*BIN, "status", "--repo", str(repo), "--roadmap", str(roadmap), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )

            scaffold(repo, mode="archetyped", archetypes=("tooling-meta",))
            # Commit the scaffolded files so the tree is clean again -- isolates
            # "does status behave differently because `.consiliency/` exists"
            # from the unrelated, expected effect of any new untracked file.
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "scaffold .consiliency", "-q"], cwd=repo, check=True)

            after = subprocess.run(
                [*BIN, "status", "--repo", str(repo), "--roadmap", str(roadmap), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertEqual(before.returncode, after.returncode)
            before_payload = json.loads(before.stdout)
            after_payload = json.loads(after.stdout)
            # `timestamp` and `git_topology.head` legitimately advance (we just
            # committed the scaffold); every other field -- phase status,
            # cleanliness, blockers -- must be identical.
            for payload in (before_payload, after_payload):
                payload.pop("timestamp", None)
                if isinstance(payload.get("git_topology"), dict):
                    payload["git_topology"].pop("head", None)
            self.assertEqual(before_payload, after_payload)


if __name__ == "__main__":
    unittest.main()
