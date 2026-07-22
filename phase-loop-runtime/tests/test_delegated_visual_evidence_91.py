"""agent-harness#91 round-2 (codex Finding 1): delegated-completion visual-
evidence propagation.

``_delegated_child_closeout_result`` (runner.py ~310) previously discarded
``native_closeout_payload`` (and the flattened ``visual_evidence_*`` keys)
entirely -- it only carried a terse status/verification/blocker summary
through. The AUTHORITATIVE visual-avatar-evidence gate
(``_visual_evidence_closeout_outcome``, reached from ``_closeout_gate_recheck``
for the delegated child's own completion, see runner.py ~3900) reads those
fields from exactly the dict ``_delegated_child_closeout_result`` returns. So
a DELEGATED visual phase that attached VALID evidence (or a typed opt-out)
was falsely reduced to ``visual_evidence_missing_or_blank`` -- the same class
of bug as the #245 ``produced_if_gates`` drop, fixed the same way:
``_delegated_child_closeout_result`` now propagates the visual-evidence
fields through (``_delegated_child_visual_evidence_fields``), mirroring
``_delegated_child_produced_if_gates``.

These tests exercise the REAL parse + serializer chain
(``_parse_native_closeout_status`` -> ``_delegated_child_closeout_result``),
not a hand-fabricated ``child_closeout_result`` dict, so a regression in
either function is caught -- consistent with the post-merge #245 CR fix,
which moved off hand-fabricated dicts for exactly this reason (see
``tests/test_gate_parity_244_245.py``).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import DelegationDecision, PromptBundle
from phase_loop_runtime.runner import (
    _closeout_gate_recheck,
    _delegated_child_closeout_result,
    _delegated_child_status_and_blocker,
    _parse_native_closeout_status,
    launch_delegated_child,
)
from phase_loop_test_utils import (
    build_fake_delegation_request,
    commit_fixture_paths,
    make_repo,
    write_phase_plan,
    write_varied_png,
)

VISIBLE_AVATAR_BODY = (
    "# RUNNER\n\n"
    "## Objective\n\n"
    "This phase renders a visible avatar in the browser meeting UI (synthetic media).\n\n"
    "## Lanes\n\n"
    "### SL-0 - RUNNER\n"
    "- **Owned files**: `tests/fixtures/avatar_call.html`\n"
)


def _native_closeout_json(**visual_fields: object) -> str:
    closeout = {
        "terminal_status": "complete",
        "verification_status": "passed",
        "dirty_paths": [],
        # BAML validation rejects a "complete" closeout with zero produced_if_gates;
        # the plan here declares no **Produces** gate, so any non-empty list clears
        # validate_produced_gates's no-expected-gates always-ok branch.
        "produced_if_gates": ["IF-0-NOOP-1"],
        "next_action": None,
        "blocker_class": None,
        "blocker_summary": None,
        "human_required": None,
        "required_human_inputs": [],
        **visual_fields,
    }
    return json.dumps(closeout)


class DelegatedVisualEvidenceTest(unittest.TestCase):
    def setUp(self):
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.repo = make_repo(Path(self._td.name))
        self.roadmap = self.repo / "specs" / "phase-plans-v1.md"
        self.plan = write_phase_plan(self.repo, "RUNNER", self.roadmap, body=VISIBLE_AVATAR_BODY)
        commit_fixture_paths(self.repo, "add plan", self.plan)
        # A DIRTY (uncommitted) avatar/browser-media surface -- the real changed
        # path the runner reduction sees via _dirty_paths(repo). The authoritative
        # visual gate reads the PARENT repo's own dirty paths, not anything the
        # delegated child self-reports.
        media = self.repo / "tests" / "fixtures" / "avatar_call.html"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_text("<html><body>avatar</body></html>\n", encoding="utf-8")
        self.decision = DelegationDecision(
            request_id="req-visual-1",
            status="approved",
            reason_code="ok",
            summary="approved",
            selected_executor="codex",
        )

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review

    def _delegated_recheck(self, native_json: str):
        """Drives the REAL delegated-completion chain: parse the child's raw
        (BAML-shaped) output exactly as ``_parsed_child_automation`` does, run
        it through the REAL ``_delegated_child_closeout_result`` serializer
        (the function under test), then the REAL ``_closeout_gate_recheck`` --
        the same sequence runner.py's delegated-completion branch (~3900)
        uses."""
        child_automation = _parse_native_closeout_status(native_json)
        self.assertTrue(child_automation.get("native_closeout_payload"), "fixture BAML payload must parse cleanly")
        closeout = _delegated_child_closeout_result(decision=self.decision, child_automation=child_automation)
        self.assertIsInstance(closeout, dict)
        status_after_launch, event_blocker = _delegated_child_status_and_blocker(closeout)
        closeout["automation_status"] = status_after_launch
        return _closeout_gate_recheck(self.repo, self.roadmap, self.plan, closeout, status_after_launch, event_blocker)

    def test_delegated_valid_evidence_survives_serializer_and_is_clean(self):
        pytest.importorskip("PIL")
        artifact = self.repo / "shots" / "frame.png"
        # round-3 (codex CR): the gate now DERIVES pixel stats from the decoded
        # image, so the artifact must be a REAL, varied (non-blank) PNG -- a
        # magic-header-only fake no longer suffices.
        write_varied_png(artifact)
        native_json = _native_closeout_json(
            visual_evidence_path="shots/frame.png",
            visual_evidence_non_black_pixels=19200,
            visual_evidence_pixel_min=0,
            visual_evidence_pixel_max=255,
        )
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._delegated_recheck(native_json)
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "complete")

    def test_delegated_missing_evidence_blocks_on_opt_in(self):
        native_json = _native_closeout_json()  # no visual_evidence_* fields at all
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._delegated_recheck(native_json)
        self.assertEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "blocked")
        self.assertFalse(outcome.event_blocker.get("human_required", True))

    def test_delegated_missing_evidence_does_not_block_under_warn_default(self):
        native_json = _native_closeout_json()
        outcome = self._delegated_recheck(native_json)  # PHASE_LOOP_REVIEW unset -> warn
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "complete")

    def test_delegated_typed_opt_out_survives_serializer_and_is_clean(self):
        native_json = _native_closeout_json(visual_evidence_opt_out="no_visible_media_surface")
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._delegated_recheck(native_json)
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "complete")


class RealLaunchDelegatedChildVisualEvidenceTest(unittest.TestCase):
    """Strongest-fidelity variant: drives the REAL ``launch_delegated_child``
    (unmocked ``validate_delegation_request``, real ``_parsed_child_automation``
    on the child's actual output text, real ``_delegated_child_closeout_result``
    serializer, real ``ParentChildRunMetadata.to_json``/``merge_launch_metadata``,
    real ``launch_metadata`` JSON round-trip) -- the exact chain
    runner.py's delegated-completion branch reads
    ``outcome["launch_metadata"]["parent_child"]["child_closeout_result"]``
    from (runner.py ~3906-3910). ``DelegatedVisualEvidenceTest`` above proves
    ``_delegated_child_closeout_result`` itself carries the fields; this class
    additionally proves nothing between there and the field the runner reads
    strips them back out (mirrors ``test_gate_parity_244_245.py``'s
    ``_launch_delegated_child_real``, written for the identical concern about
    ``produced_if_gates``)."""

    def setUp(self):
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.repo = make_repo(Path(self._td.name))
        self.roadmap = self.repo / "specs" / "phase-plans-v1.md"
        self.plan = write_phase_plan(self.repo, "RUNNER", self.roadmap, body=VISIBLE_AVATAR_BODY)
        commit_fixture_paths(self.repo, "add plan", self.plan)
        media = self.repo / "tests" / "fixtures" / "avatar_call.html"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_text("<html><body>avatar</body></html>\n", encoding="utf-8")

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review

    def _launch_real(self, native_json: str) -> dict:
        request = build_fake_delegation_request(
            request_id="req-visual-real-1",
            target_executor="codex",
            product_action="execute",
            owned_files=("tests/fixtures/avatar_call.html",),
            expected_output="Delegated visual work",
        )

        def _fake_build_prompt(*args, **kwargs) -> PromptBundle:
            return PromptBundle(workflow_command="execute", body="stub", injection_mode="context_file")

        def _fake_launch(spec, dry_run=False, log_path=None, **kwargs) -> LaunchResult:
            return LaunchResult(command=spec.command, returncode=0, output=native_json, executor=spec.executor)

        with patch("phase_loop_runtime.runner.build_prompt", side_effect=_fake_build_prompt), \
             patch(
                 "phase_loop_runtime.runner.run_auth_preflight",
                 return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
             ), \
             patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
            outcome = launch_delegated_child(
                repo=self.repo,
                roadmap=self.roadmap,
                parent_phase="RUNNER",
                parent_action="execute",
                parent_executor="codex",
                plan=self.plan,
                request=request,
                dry_run=False,
            )
        self.assertEqual(outcome["decision"]["status"], "approved", outcome["decision"])
        closeout = outcome["launch_metadata"]["parent_child"]["child_closeout_result"]
        self.assertIsInstance(closeout, dict)
        return closeout

    def test_valid_evidence_survives_real_launch_metadata_round_trip(self):
        pytest.importorskip("PIL")
        artifact = self.repo / "shots" / "frame.png"
        # round-3 (codex CR): the gate now DERIVES pixel stats from the decoded
        # image, so the artifact must be a REAL, varied (non-blank) PNG -- a
        # magic-header-only fake no longer suffices.
        write_varied_png(artifact)
        native_json = _native_closeout_json(
            visual_evidence_path="shots/frame.png",
            visual_evidence_non_black_pixels=19200,
            visual_evidence_pixel_min=0,
            visual_evidence_pixel_max=255,
        )
        closeout = self._launch_real(native_json)
        # The proof: after the REAL to_json()/merge_launch_metadata() JSON
        # round-trip, the flattened visual-evidence keys are still present --
        # nothing between the serializer and the runner's read site drops them.
        self.assertEqual(closeout.get("visual_evidence_path"), "shots/frame.png")
        self.assertEqual(closeout.get("visual_evidence_observed"), {
            "non_black_pixels": 19200, "pixel_min": 0, "pixel_max": 255,
        })

        status_after_launch, event_blocker = _delegated_child_status_and_blocker(closeout)
        closeout["automation_status"] = status_after_launch
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = _closeout_gate_recheck(
                self.repo, self.roadmap, self.plan, closeout, status_after_launch, event_blocker,
            )
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "complete")

    def test_typed_opt_out_survives_real_launch_metadata_round_trip(self):
        native_json = _native_closeout_json(visual_evidence_opt_out="no_visible_media_surface")
        closeout = self._launch_real(native_json)
        self.assertEqual(closeout.get("visual_evidence_opt_out"), "no_visible_media_surface")

        status_after_launch, event_blocker = _delegated_child_status_and_blocker(closeout)
        closeout["automation_status"] = status_after_launch
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = _closeout_gate_recheck(
                self.repo, self.roadmap, self.plan, closeout, status_after_launch, event_blocker,
            )
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "complete")


if __name__ == "__main__":
    unittest.main()
