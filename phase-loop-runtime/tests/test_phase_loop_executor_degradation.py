import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.runtime_paths import phase_loop_executor_degradation_file
from phase_loop_runtime.state_degradation import (
    active_degraded_executors,
    clear,
    load_degradation,
    record_degradation,
)
from phase_loop_runtime.state_ops import archive_state
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import make_repo, provenanced_state


class PhaseLoopExecutorDegradationTest(unittest.TestCase):
    def test_path_resolves_under_canonical_phase_loop_dir(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            self.assertEqual(phase_loop_executor_degradation_file(repo), repo / ".phase-loop" / "executor-degradation.json")

    def test_record_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            record_degradation(repo, "codex", "timeout", "FOUND", "Executor timed out.", 300)

            records = load_degradation(repo)

            self.assertEqual(set(records), {"codex"})
            self.assertEqual(records["codex"].demoted_to, "proof_gated")
            self.assertEqual(records["codex"].reason, "timeout")
            self.assertEqual(records["codex"].source_phase, "FOUND")
            self.assertEqual(records["codex"].blocker_summary, "Executor timed out.")
            self.assertEqual(records["codex"].ttl_seconds, 300)

    def test_missing_file_loads_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_degradation(Path(td) / "repo"), {})

    def test_corrupted_json_loads_empty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            path = phase_loop_executor_degradation_file(repo)
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")

            self.assertEqual(load_degradation(repo), {})
            self.assertEqual(active_degraded_executors(repo), set())

    def test_clear_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            record_degradation(repo, "claude", "malformed_closeout", "FOUND", "Bad closeout.", 300)

            clear(repo)
            clear(repo)

            self.assertFalse(phase_loop_executor_degradation_file(repo).exists())
            self.assertEqual(load_degradation(repo), {})

    def test_active_degraded_executors_respects_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            path = phase_loop_executor_degradation_file(repo)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "codex": {
                            "since": "2026-05-20T00:00:00Z",
                            "ttl_seconds": 60,
                            "demoted_to": "proof_gated",
                            "reason": "timeout",
                            "source_phase": "FOUND",
                            "blocker_summary": "Temporary degradation.",
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(active_degraded_executors(repo, now=datetime(2026, 5, 20, 0, 0, 30, tzinfo=timezone.utc)), {"codex"})
            self.assertEqual(active_degraded_executors(repo, now=datetime(2026, 5, 20, 0, 1, 0, tzinfo=timezone.utc)), set())

    def test_repeated_writes_replace_executor_record_without_malformed_json(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            record_degradation(repo, "codex", "timeout", "FOUND", "First.", 60)
            record_degradation(repo, "codex", "malformed_closeout", "DISPATCH", "Second.", 120, demoted_to="manual_only")

            payload = json.loads(phase_loop_executor_degradation_file(repo).read_text(encoding="utf-8"))
            records = load_degradation(repo)

            self.assertEqual(set(payload), {"codex"})
            self.assertEqual(records["codex"].demoted_to, "manual_only")
            self.assertEqual(records["codex"].reason, "malformed_closeout")
            self.assertEqual(records["codex"].source_phase, "DISPATCH")
            self.assertEqual(records["codex"].ttl_seconds, 120)

    def test_record_degradation_rejects_invalid_demotion_mode(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                record_degradation(Path(td) / "repo", "codex", "timeout", "FOUND", "Bad mode.", 60, demoted_to="live")

    def test_degradation_file_survives_archive_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"CONTRACT": "planned"}))
            record_degradation(repo, "codex", "timeout", "FOUND", "Executor timed out.", 300)
            subprocess.run(["git", "status", "--short"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            result = archive_state(repo, reason="unit test")

            self.assertTrue(result["archived"])
            self.assertTrue(phase_loop_executor_degradation_file(repo).exists())
            self.assertEqual(set(load_degradation(repo)), {"codex"})


if __name__ == "__main__":
    unittest.main()
