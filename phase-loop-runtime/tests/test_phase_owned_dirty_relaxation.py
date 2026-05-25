from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.runner import _classify_dirty_paths
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


class PhaseOwnedDirtyRelaxationTests(unittest.TestCase):
    def test_valid_executor_evidence_relaxes_unowned_package_lock(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/package.json",))
            commit_fixture_paths(repo, "add runner plan", plan)

            manifest_path = repo / "apps" / "portal" / "package.json"
            lock_path = repo / "apps" / "portal" / "package-lock.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text('{"scripts":{}}\n', encoding="utf-8")
            lock_path.write_text('{"lockfileVersion":3}\n', encoding="utf-8")

            with patch.dict("os.environ", {"PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE": "true"}):
                summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths=[],
                    post_launch_dirty_paths=[
                        "apps/portal/package.json",
                        "apps/portal/package-lock.json",
                    ],
                    current_phase="RUNNER",
                    terminal_summary={
                        "phase_owned_dirty": True,
                        "phase_owned_evidence": ["apps/portal/package-lock.json"],
                    },
                    emit_runtime_relaxation_event=True,
                )

            self.assertTrue(summary["phase_owned_dirty"])
            self.assertEqual(summary["unowned_dirty_paths"], [])
            self.assertIn("apps/portal/package-lock.json", summary["phase_owned_dirty_paths"])
            events = [event for event in read_events(repo) if event["action"] == "runner.runtime_relaxation_invoked"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["metadata"]["declared_paths"], ["apps/portal/package.json"])
            self.assertEqual(
                events[0]["metadata"]["actual_paths"],
                ["apps/portal/package.json", "apps/portal/package-lock.json"],
            )

    def test_exact_false_flag_keeps_unowned_sibling_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/package.json",))
            commit_fixture_paths(repo, "add runner plan", plan)

            with patch.dict("os.environ", {"PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE": "false"}, clear=True):
                summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths=[],
                    post_launch_dirty_paths=[
                        "apps/portal/package.json",
                        "apps/portal/package-lock.json",
                    ],
                    current_phase="RUNNER",
                    terminal_summary={
                        "phase_owned_dirty": True,
                        "phase_owned_evidence": ["apps/portal/package-lock.json"],
                    },
                    emit_runtime_relaxation_event=True,
                )

            self.assertFalse(summary["phase_owned_dirty"])
            self.assertEqual(summary["unowned_dirty_paths"], ["apps/portal/package-lock.json"])
            self.assertEqual(
                [event for event in read_events(repo) if event["action"] == "runner.runtime_relaxation_invoked"],
                [],
            )

    def test_unrelated_evidence_remains_unowned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/src/lib/feature.ts",))
            commit_fixture_paths(repo, "add runner plan", plan)

            with patch.dict("os.environ", {"PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE": "true"}):
                summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths=[],
                    post_launch_dirty_paths=[
                        "apps/portal/src/lib/feature.ts",
                        "apps/portal/src/lib/__tests__/other.test.ts",
                    ],
                    current_phase="RUNNER",
                    terminal_summary={
                        "phase_owned_dirty": True,
                        "phase_owned_evidence": ["apps/portal/src/lib/__tests__/other.test.ts"],
                    },
                    emit_runtime_relaxation_event=True,
                )

            self.assertFalse(summary["phase_owned_dirty"])
            self.assertEqual(summary["unowned_dirty_paths"], ["apps/portal/src/lib/__tests__/other.test.ts"])


if __name__ == "__main__":
    unittest.main()
