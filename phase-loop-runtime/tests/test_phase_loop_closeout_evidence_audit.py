from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout_evidence_audit import AuditResult, audit_closeout_evidence
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo, write_phase_plan


class CloseoutEvidenceAuditTest(unittest.TestCase):
    def test_matched_claim_passes_by_path_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "docs/closeout.md",
                "closeout evidence\n",
                "phase-loop closeout: CONTRACT\n\nAdded `docs/closeout.md`\n",
            )

            result = audit_closeout_evidence(commit, "CONTRACT", repo)

            self.assertIsInstance(result, AuditResult)
            self.assertEqual(result.audit_status, "passed")
            self.assertEqual(result.matched_claims, ["docs/closeout.md"])
            self.assertEqual(result.unmatched_claims, [])
            self.assertFalse(hasattr(result, "commit_body"))
            self.assertFalse(hasattr(result, "diff_body"))

    def test_unmatched_claim_detects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "docs/other.md",
                "closeout evidence\n",
                "phase-loop closeout: CONTRACT\n\nCreated `docs/missing.md`\n",
            )

            result = audit_closeout_evidence(commit, "CONTRACT", repo)

            self.assertEqual(result.audit_status, "drift_detected")
            self.assertEqual(result.matched_claims, [])
            self.assertEqual(result.unmatched_claims, ["docs/missing.md"])

    def test_mixed_claims_report_only_unmatched(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "src/runtime.py",
                "def audit_closeout_evidence():\n    return True\n",
                "phase-loop closeout: CONTRACT\n\n"
                "- Added `src/runtime.py`\n"
                "- Updated `missingRuntimeSymbol`\n",
            )

            result = audit_closeout_evidence(commit, "CONTRACT", repo)

            self.assertEqual(result.audit_status, "drift_detected")
            self.assertEqual(result.matched_claims, ["src/runtime.py"])
            self.assertEqual(result.unmatched_claims, ["missingRuntimeSymbol"])

    def test_free_form_prose_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "docs/notes.md",
                "closeout evidence\n",
                "phase-loop closeout: CONTRACT\n\nThis closeout improved docs.\n",
            )

            result = audit_closeout_evidence(commit, "CONTRACT", repo)

            self.assertEqual(result.audit_status, "skipped")
            self.assertEqual(result.matched_claims, [])
            self.assertEqual(result.unmatched_claims, [])

    def test_identifier_in_diff_content_matches_claim(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "src/runtime.py",
                "def audit_closeout_evidence():\n    return True\n",
                "phase-loop closeout: CONTRACT\n\nUpdated `audit_closeout_evidence`\n",
            )

            result = audit_closeout_evidence(commit, "CONTRACT", repo)

            self.assertEqual(result.audit_status, "passed")
            self.assertEqual(result.matched_claims, ["audit_closeout_evidence"])

    def test_reconcile_skips_when_roadmap_flag_absent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _append_awaiting_closeout(repo, roadmap)
            (repo / "README.md").write_text("phase output\n")

            with patch("phase_loop_runtime.runner.audit_closeout_evidence") as audit:
                snapshot, _results = run_loop(repo, roadmap, closeout_mode="commit")

            event = read_events(repo)[-1]
            audit.assert_not_called()
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            self.assertEqual(event["status"], "complete")
            self.assertNotIn("closeout_evidence_audit", event["metadata"]["closeout"])

    def test_reconcile_blocks_enabled_roadmap_drift(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _enable_roadmap_audit(repo, roadmap)
            _append_awaiting_closeout(repo, roadmap)
            (repo / "README.md").write_text("phase output\n")

            audit_result = AuditResult(
                matched_claims=[],
                unmatched_claims=["getPilotAllowlist derives from App"],
                audit_status="drift_detected",
            )
            with patch("phase_loop_runtime.runner.audit_closeout_evidence", return_value=audit_result):
                snapshot, _results = run_loop(repo, roadmap, closeout_mode="commit")

            event = read_events(repo)[-1]
            closeout = event["metadata"]["closeout"]
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "closeout_evidence_drift")
            self.assertEqual(
                event["blocker"]["blocker_summary"],
                "1 of 1 closeout claims have no matching files in the closeout diff",
            )
            self.assertEqual(closeout["verification_status"], "blocked")
            self.assertEqual(closeout["closeout_evidence_audit"]["audit_status"], "drift_detected")
            self.assertIn("closeout_commit", closeout)
            self.assertNotIn("getPilotAllowlist", event["blocker"]["blocker_summary"])

    def test_reconcile_enabled_roadmap_allows_matching_closeout_claim(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _enable_roadmap_audit(repo, roadmap)
            _append_awaiting_closeout(repo, roadmap)
            (repo / "README.md").write_text("phase output\n")

            audit_result = AuditResult(matched_claims=["README.md"], unmatched_claims=[], audit_status="passed")
            with patch("phase_loop_runtime.runner.audit_closeout_evidence", return_value=audit_result):
                snapshot, _results = run_loop(repo, roadmap, closeout_mode="commit")

            event = read_events(repo)[-1]
            closeout = event["metadata"]["closeout"]
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            self.assertEqual(event["status"], "complete")
            self.assertEqual(closeout["closeout_evidence_audit"]["audit_status"], "passed")
            self.assertEqual(closeout["closeout_evidence_audit"]["matched_claim_count"], 1)

    def test_f32d9afb_style_synthetic_drift_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit = _commit_with_message(
                repo,
                "docs/admin.md",
                "admin docs\n",
                "phase-loop closeout: DYNAMICALLOWLIST\n\n"
                "- Updated `getPilotAllowlist derives from App`\n",
            )

            result = audit_closeout_evidence(commit, "DYNAMICALLOWLIST", repo)

            self.assertEqual(result.audit_status, "drift_detected")
            self.assertEqual(result.unmatched_claims, ["getPilotAllowlist derives from App"])


def _commit_with_message(repo: Path, relpath: str, content: str, message: str) -> str:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-F", "-"],
        cwd=repo,
        input=message,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _enable_roadmap_audit(repo: Path, roadmap: Path) -> None:
    text = roadmap.read_text(encoding="utf-8")
    roadmap.write_text(
        text.replace("# Roadmap\n\n", "# Roadmap\n\n## Metadata\n\n- closeout_evidence_audit: true\n\n"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "enable audit"], cwd=repo, check=True, stdout=subprocess.DEVNULL)


def _append_awaiting_closeout(repo: Path, roadmap: Path) -> None:
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
    subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="CONTRACT",
            action="execute",
            status="awaiting_phase_closeout",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "completion_dirty_worktree": {
                    "reason": "complete_status_with_dirty_worktree",
                    "terminal_status": "complete",
                    "dirty_paths": ["README.md"],
                    "phase_owned_dirty_paths": ["README.md"],
                    "unowned_dirty_paths": [],
                    "pre_existing_dirty_paths": [],
                    "phase_owned_dirty": True,
                }
            },
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )


if __name__ == "__main__":
    unittest.main()
