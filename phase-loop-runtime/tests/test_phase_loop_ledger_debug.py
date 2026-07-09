import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance, roadmap_sha256, phase_sha256
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.render import render_status
from phase_loop_test_utils import make_repo, provenanced_event, write_phase_plan


class PhaseLoopLedgerDebugTest(unittest.TestCase):
    def test_status_parser_accepts_ledger_debug_only_for_status(self):
        parser = build_parser()
        args = parser.parse_args(["status", "--ledger-debug"])
        self.assertTrue(args.ledger_debug)
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--ledger-debug"])

    def test_text_ledger_debug_outputs_rejected_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_rejection_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)

            output = render_status(snapshot, ledger_debug=True)

            self.assertIn("Ledger warnings: 7", output)
            self.assertIn("Rejected events:", output)
            self.assertIn("reason=provenance_mismatch", output)
            self.assertIn("reason=planned_without_plan_artifact", output)
            self.assertIn("raw_event_summary=", output)

    def test_json_ledger_debug_outputs_rejected_events_array(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_rejection_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)

            payload = json.loads(render_status(snapshot, as_json=True, ledger_debug=True))

            self.assertEqual(len(payload["rejected_events"]), 7)
            reasons = {record["reason"] for record in payload["rejected_events"]}
            self.assertEqual(
                reasons,
                {
                    "provenance_mismatch",
                    "phase_missing",
                    "not_in_allowed_status_set",
                    "legacy_pre_schema_v2",
                    "planned_without_plan_artifact",
                    "blocker_supersession",
                },
            )

    def test_ledger_debug_reports_empty_array_for_zero_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = reconcile(repo, roadmap)

            text = render_status(snapshot, ledger_debug=True)
            payload = json.loads(render_status(snapshot, as_json=True, ledger_debug=True))

            self.assertIn("Rejected events:\n  none", text)
            self.assertIn("Duplicates skipped:\n  none", text)
            self.assertEqual(payload["rejected_events"], [])
            self.assertEqual(payload["duplicates_skipped"], [])

    def test_default_output_is_unchanged_without_ledger_debug(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_rejection_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)

            text = render_status(snapshot)
            payload = json.loads(render_status(snapshot, as_json=True))

            self.assertIn("Ledger warnings: 7", text)
            self.assertNotIn("Rejected events:", text)
            self.assertNotIn("Duplicates skipped:", text)
            self.assertNotIn("rejected_events", payload)
            self.assertNotIn("duplicates_skipped", payload)

    def test_rejected_events_use_safe_raw_event_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_rejection_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)
            payload = json.loads(render_status(snapshot, as_json=True, ledger_debug=True))

            summaries = [record["raw_event_summary"] for record in payload["rejected_events"]]
            serialized = json.dumps(summaries, sort_keys=True)
            self.assertIn("roadmap_sha256_present", serialized)
            self.assertNotIn("metadata", serialized)
            self.assertNotIn("provider_payload", serialized)
            self.assertNotIn("SECRET_TOKEN", serialized)

    def test_status_command_threads_ledger_debug_to_rendering(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_rejection_fixture(Path(td))
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                code = main(["status", "--repo", str(repo), "--roadmap", str(roadmap), "--json", "--ledger-debug"])

            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["rejected_events"]), 7)

    def test_text_ledger_debug_outputs_duplicates_skipped_after_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_duplicate_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)

            output = render_status(snapshot, ledger_debug=True)

            self.assertIn("Ledger warnings: 1", output)
            self.assertLess(output.index("Rejected events:"), output.index("Duplicates skipped:"))
            self.assertIn("count=1", output)
            self.assertIn("phase=CONTRACT", output)
            self.assertIn("duplicate_key=", output)

    def test_json_ledger_debug_outputs_duplicates_skipped_array(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _make_duplicate_fixture(Path(td))
            snapshot = reconcile(repo, roadmap)

            payload = json.loads(render_status(snapshot, as_json=True, ledger_debug=True))

            self.assertEqual(len(payload["rejected_events"]), 1)
            self.assertEqual(len(payload["duplicates_skipped"]), 1)
            duplicate = payload["duplicates_skipped"][0]
            self.assertEqual(duplicate["phase"], "CONTRACT")
            self.assertEqual(duplicate["duplicate_key"]["status"], "plan_skipped")
            serialized = json.dumps(duplicate, sort_keys=True)
            self.assertNotIn("metadata", serialized)
            self.assertNotIn("provider_payload", serialized)


def _make_rejection_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    write_phase_plan(repo, "CONTRACT", roadmap)
    append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "blocked", action="execute"))
    _append_raw_event(
        repo,
        {
            "timestamp": utc_now(),
            "repo": str(repo),
            "roadmap": str(roadmap),
            "phase": "MISSING",
            "action": "execute",
            "status": "complete",
            "source": "fixture",
            "schema_version": 2,
            "roadmap_sha256": roadmap_sha256(roadmap),
            "phase_sha256": "missing-phase",
        },
    )
    _append_raw_event(
        repo,
        {
            "timestamp": utc_now(),
            "repo": str(repo),
            "roadmap": str(roadmap),
            "phase": "CONTRACT",
            "action": "execute",
            "status": "dry_run",
            "source": "fixture",
            "schema_version": 2,
            "roadmap_sha256": roadmap_sha256(roadmap),
            "phase_sha256": phase_sha256(roadmap, "CONTRACT"),
            "metadata": {"provider_payload": "SECRET_TOKEN"},
        },
    )
    _append_raw_event(
        repo,
        {
            "timestamp": utc_now(),
            "repo": str(repo),
            "roadmap": str(roadmap),
            "phase": "RUNNER",
            "action": "execute",
            "status": "complete",
            "source": "fixture",
            "schema_version": 1,
        },
    )
    append_event(repo, provenanced_event(repo, roadmap, "ACCESS", "planned", action="plan"))
    append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "planned", action="plan"))
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="RUNNER",
            action="execute",
            status="complete",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            schema_version=2,
            roadmap_sha256=roadmap_sha256(roadmap),
            phase_sha256="stale-phase",
        ),
    )
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="CONTRACT",
            action="plan",
            status="planned",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            schema_version=2,
            roadmap_sha256="stale-roadmap",
            phase_sha256=phase_sha256(roadmap, "CONTRACT"),
        ),
    )
    return repo, roadmap


def _make_duplicate_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    payload = {
        "timestamp": utc_now(),
        "repo": str(repo),
        "roadmap": str(roadmap),
        "phase": "CONTRACT",
        "action": "run",
        "status": "plan_skipped",
        "source": "fixture",
        "schema_version": 2,
        "roadmap_sha256": roadmap_sha256(roadmap),
        "phase_sha256": phase_sha256(roadmap, "CONTRACT"),
        "metadata": {"provider_payload": "SECRET_TOKEN"},
    }
    _append_raw_event(repo, payload)
    _append_raw_event(repo, dict(payload))
    return repo, roadmap


def _append_raw_event(repo: Path, payload: dict[str, object]) -> None:
    path = repo / ".phase-loop" / "events.jsonl"
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
