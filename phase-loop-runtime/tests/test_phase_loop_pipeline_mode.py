from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.events import read_events
from phase_loop_runtime.runner import run_loop, status_snapshot
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopPipelineModeTest(unittest.TestCase):
    def test_parser_accepts_pipeline_mode_on_runtime_commands(self):
        parser = build_parser()
        for command in ("run", "resume", "dry-run", "status", "handoff", "execute", "reconcile", "monitor"):
            with self.subTest(command=command):
                argv = [command]
                if command == "execute":
                    argv.append("RUNNER")
                args = parser.parse_args([*argv, "--pipeline-mode", "pipeline_optional"])
                self.assertEqual(args.pipeline_mode, "pipeline_optional")

        self.assertEqual(parser.parse_args(["status"]).pipeline_mode, "standalone")
        self.assertEqual(parser.parse_args(["--pipeline-mode", "pipeline_optional", "status"]).pipeline_mode, "pipeline_optional")
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "--pipeline-mode", "invalid"])

    def test_status_snapshot_records_pipeline_mode(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            snapshot = status_snapshot(repo, roadmap, pipeline_mode="pipeline_optional")

            self.assertEqual(snapshot.pipeline_mode, "pipeline_optional")
            self.assertEqual(snapshot.to_json()["pipeline_mode"], "pipeline_optional")

    def test_standalone_mode_does_not_require_pipeline_acknowledgement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            snapshot, _results = run_loop(
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                dry_run=True,
                pipeline_mode="standalone",
            )

            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(read_events(repo)[-1]["metadata"]["pipeline_mode"], "standalone")

    def test_pipeline_required_refuses_missing_acknowledged_contract(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            snapshot, _results = run_loop(
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                dry_run=True,
                pipeline_mode="pipeline_required",
            )

            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "contract_bug")
            self.assertEqual(event["metadata"]["pipeline_execution_preflight"]["diagnostic"]["kind"], "missing_source_bundle")

    def test_pipeline_optional_proceeds_without_acknowledged_contract(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            snapshot, _results = run_loop(
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                dry_run=True,
                pipeline_mode="pipeline_optional",
            )

            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(read_events(repo)[-1]["metadata"]["pipeline_mode"], "pipeline_optional")


if __name__ == "__main__":
    unittest.main()
