from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.models import BLOCKER_CLASSES, DISPATCH_CAPABILITIES, EXECUTORS, PHASE_STATUSES
from phase_loop_runtime.phase_loop_drift_audit import VERIFICATION_STATUSES, run_drift_audit


def _write_terminal_summary(repo: Path, payload: dict, *, run_name: str = "20260522T000000Z-01-test-execute") -> Path:
    run_dir = repo / ".phase-loop" / "runs" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "terminal-summary.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _append_event(repo: Path, event: dict) -> None:
    event_path = repo / ".phase-loop" / "events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


class PhaseLoopDriftAuditTest(unittest.TestCase):
    def test_clean_repo_reports_no_drift(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_terminal_summary(repo, {"terminal_status": "complete", "verification_status": "passed"})

            result = run_drift_audit([repo], days=7, scope="closeout")

            self.assertTrue(result.is_clean(), msg=result.to_json())
            self.assertEqual(result.repos[0].terminal_summaries_scanned, 1)

    def test_terminal_status_dry_run_is_reported_without_allowlist_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_terminal_summary(repo, {"terminal_status": "dry_run", "verification_status": "passed"})

            result = run_drift_audit([repo], days=7, scope="closeout")

            self.assertNotIn("dry_run", PHASE_STATUSES)
            self.assertEqual(len(result.drift_counts), 1)
            finding = result.drift_counts[0]
            self.assertEqual(finding.field, "terminal_status")
            self.assertEqual(finding.literal, "dry_run")

    def test_invalid_verification_status_and_blocker_class_are_reported(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_terminal_summary(
                repo,
                {
                    "terminal_status": "blocked",
                    "verification_status": "success",
                    "blocker_class": "needs_person",
                },
            )

            result = run_drift_audit([repo], days=7, scope="closeout")
            found = {(item.field, item.literal) for item in result.drift_counts}

            self.assertIn(("verification_status", "success"), found)
            self.assertIn(("blocker_class", "needs_person"), found)

    def test_multi_repo_aggregation_sorts_repos_fields_and_literals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo_a = root / "a"
            repo_b = root / "b"
            repo_a.mkdir()
            repo_b.mkdir()
            _write_terminal_summary(repo_b, {"terminal_status": "dry_run", "verification_status": "passed"})
            _write_terminal_summary(repo_a, {"terminal_status": "complete", "verification_status": "partial"})

            result = run_drift_audit([repo_b, repo_a], days=7, scope="closeout")

            self.assertEqual([Path(repo.repo).name for repo in result.repos], ["a", "b"])
            self.assertEqual(
                [(item.field, item.literal, Path(item.repo).name) for item in result.drift_counts],
                [("terminal_status", "dry_run", "b"), ("verification_status", "partial", "a")],
            )

    def test_days_window_filters_old_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
            _append_event(
                repo,
                {
                    "timestamp": old,
                    "action": "phase_closeout",
                    "metadata": {"terminal_summary": {"terminal_status": "dry_run", "verification_status": "passed"}},
                },
            )

            result = run_drift_audit([repo], days=7, scope="closeout")

            self.assertTrue(result.is_clean(), msg=result.to_json())
            self.assertEqual(result.repos[0].events_scanned, 0)

    def test_json_roundtrip_and_allowlists_come_from_models(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_terminal_summary(repo, {"terminal_status": "complete", "verification_status": "passed"})

            payload = run_drift_audit([repo], days=7, scope="closeout").to_json()
            roundtripped = json.loads(json.dumps(payload))

            self.assertEqual(roundtripped["allowlists"]["terminal_status"], list(PHASE_STATUSES))
            self.assertEqual(roundtripped["allowlists"]["verification_status"], list(VERIFICATION_STATUSES))
            self.assertEqual(roundtripped["allowlists"]["blocker_class"], list(BLOCKER_CLASSES))
            self.assertEqual(roundtripped["allowlists"]["executor"], list(EXECUTORS))
            self.assertEqual(roundtripped["allowlists"]["dispatch_capability"], list(DISPATCH_CAPABILITIES))

    def test_cli_help_lists_closeout_drift_audit_flags(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
            build_parser().parse_args(["closeout-drift-audit", "--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        for flag in ("--repo", "--days", "--scope", "--json"):
            self.assertIn(flag, help_text)

    def test_cli_exit_codes_for_clean_drift_and_setup_error(self):
        with tempfile.TemporaryDirectory() as td:
            clean_repo = Path(td) / "clean"
            drift_repo = Path(td) / "drift"
            clean_repo.mkdir()
            drift_repo.mkdir()
            _write_terminal_summary(clean_repo, {"terminal_status": "complete", "verification_status": "passed"})
            _write_terminal_summary(drift_repo, {"terminal_status": "dry_run", "verification_status": "passed"})

            self.assertEqual(_run_cli(["closeout-drift-audit", "--repo", str(clean_repo)]), 0)
            self.assertEqual(_run_cli(["closeout-drift-audit", "--repo", str(drift_repo)]), 1)
            self.assertEqual(_run_cli(["closeout-drift-audit", "--repo", str(Path(td) / "missing")]), 2)


def _run_cli(argv: list[str]) -> int:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        return main(argv)


if __name__ == "__main__":
    unittest.main()
