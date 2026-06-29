"""rigor-v1 — run-end review-findings summary (acceptance #3)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.closeout_validators import (
    ReviewFinding,
    clear_closeout_validators,
    register_closeout_validator,
)
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runner import _emit_review_findings_summary, _governed_not_live_warning
from phase_loop_runtime.review_summary import (
    collect_review_findings,
    render_review_findings_summary,
    summarize_run_review_findings,
)
from phase_loop_test_utils import make_repo, write_named_roadmap


class ReviewSummaryTest(unittest.TestCase):
    def test_collect_finds_nested_findings_and_tags_phase(self):
        events = [
            {"phase": "P2", "metadata": {"closeout": {"verification": {"results": [
                {"kind": "review_finding", "code": "doc_delta_undecided", "reason": "r", "severity": "warn"},
            ]}}}},
            {"phase": "P5", "x": [{"kind": "review_finding", "code": "ve", "reason": "r2", "severity": "warn"}]},
        ]
        found = collect_review_findings(events)
        self.assertEqual({f["code"] for f in found}, {"doc_delta_undecided", "ve"})
        self.assertEqual({f["phase"] for f in found}, {"P2", "P5"})

    def test_collect_dedups_repeated_findings(self):
        f = {"kind": "review_finding", "code": "c", "reason": "r", "severity": "warn"}
        events = [{"phase": "P", "a": f}, {"phase": "P", "b": f}]  # same finding echoed twice
        self.assertEqual(len(collect_review_findings(events)), 1)

    def test_render_empty_is_blank(self):
        self.assertEqual(render_review_findings_summary([]), "")

    def test_render_lists_findings_and_counts(self):
        out = render_review_findings_summary([
            {"code": "c1", "reason": "r1", "severity": "warn", "phase": "P2"},
            {"code": "c2", "reason": "r2", "severity": "block", "phase": "P6"},
        ])
        self.assertIn("Review findings this run: 2", out)
        self.assertIn("1 warn", out)
        self.assertIn("1 block", out)
        self.assertIn("P2: c1", out)
        self.assertIn("PHASE_LOOP_REVIEW=block", out)

    def test_end_to_end_real_closeout_shape(self):
        # A real closeout's verification.results carries the review_finding; the
        # summary must surface it when that closeout is an event record.
        clear_closeout_validators()
        register_closeout_validator(
            lambda ctx: [ReviewFinding(code="demo", reason="noted", severity="warn")]
        )
        try:
            with tempfile.TemporaryDirectory() as td:
                plan = Path(td) / "p.md"
                plan.write_text("x", encoding="utf-8")
                closeout = build_phase_loop_closeout(
                    phase_alias="P9", plan_path=plan,
                    terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                    automation={"status": "complete", "verification_status": "passed", "human_required": False},
                )
                event = {"phase": "P9", "action": "execute", "metadata": {"closeout": closeout}}
                summary = summarize_run_review_findings([event])
                self.assertIn("demo", summary)
                self.assertIn("P9", summary)
        finally:
            clear_closeout_validators()

    def test_run_end_helper_emits_findings_to_stderr(self):
        # The wired path: events on disk -> _emit_review_findings_summary -> stderr.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"),))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
                    phase="ALPHA", action="execute", status="complete",
                    model="fixture", reasoning_effort="medium", source="fixture",
                    metadata={"closeout": {"verification": {"results": [
                        {"kind": "review_finding", "code": "doc_delta_undecided",
                         "reason": "changed a public surface", "severity": "warn"},
                    ]}}},
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _emit_review_findings_summary(repo)
            out = err.getvalue()
            self.assertIn("Review findings this run: 1", out)
            self.assertIn("doc_delta_undecided", out)

    def test_run_end_helper_scopes_to_since_baseline(self):
        # Findings from a prior batch (before `since`) must not be re-reported.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))

            def _finding_event(phase, code):
                return LoopEvent(
                    timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
                    phase=phase, action="execute", status="complete",
                    model="fixture", reasoning_effort="medium", source="fixture",
                    metadata={"closeout": {"verification": {"results": [
                        {"kind": "review_finding", "code": code, "reason": "r", "severity": "warn"},
                    ]}}},
                    **event_provenance(roadmap, phase),
                )

            append_event(repo, _finding_event("ALPHA", "prior_batch"))
            from phase_loop_runtime.events import read_events
            baseline = len(read_events(repo))
            append_event(repo, _finding_event("BETA", "this_batch"))

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _emit_review_findings_summary(repo, since=baseline)
            out = err.getvalue()
            self.assertIn("this_batch", out)
            self.assertNotIn("prior_batch", out)

    def test_run_end_helper_silent_when_no_findings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _emit_review_findings_summary(repo)
            self.assertEqual(err.getvalue(), "")


class GovernedNotLiveWarningTest(unittest.TestCase):
    # v2-P1: governed pre-merge gate is now LIVE on the serial path; the notice
    # explains the remaining partial liveness (panel spawn P2, planning gate P3).
    def test_governed_warns(self):
        msg = _governed_not_live_warning("governed")
        self.assertIsNotNone(msg)
        self.assertIn("pre-merge gate is live", msg)
        self.assertIn("advisory pass", msg)

    def test_autonomous_is_silent(self):
        self.assertIsNone(_governed_not_live_warning("autonomous"))


if __name__ == "__main__":
    unittest.main()
