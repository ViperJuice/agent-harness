"""FAV (issue #91, CR Fix 1) -- the visual-avatar-evidence gate is WIRED into
the live runner reduction, not merely nested under a successful outer status.

Two independent guarantees:

1. The native closeout's visual-evidence fields SURVIVE into the terminal
   summary the closeout validator inspects (they were previously silently
   discarded by the TERMINAL_SUMMARY_FIELDS whitelist).
2. A visual-gate BLOCK under the opt-in ``PHASE_LOOP_REVIEW=block`` posture
   reaches the AUTHORITATIVE runner reduction (``_closeout_gate_recheck`` ->
   blocked_reason / event_blocker), mirroring the produced-gates/#243 path, so
   the reducer cannot COMPLETE a phase the visual gate blocked. Under the
   default warn posture the phase still completes (autonomy-first).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import commit_fixture_paths, make_repo, write_blank_png, write_phase_plan, write_varied_png
from phase_loop_runtime import runner as runner_mod
from phase_loop_runtime.observability import build_terminal_summary

VISIBLE_AVATAR_BODY = (
    "# RUNNER\n\n"
    "## Objective\n\n"
    "This phase renders a visible avatar in the browser meeting UI (synthetic media).\n\n"
    "## Lanes\n\n"
    "### SL-0 - RUNNER\n"
    "- **Owned files**: `tests/fixtures/avatar_call.html`\n"
)


class VisualEvidenceSurvivesWhitelistTest(unittest.TestCase):
    """Guarantee 1: the flat BAML visual-evidence fields survive into the
    terminal summary (whitelist no longer discards them)."""

    def test_flat_visual_fields_survive_terminal_summary(self):
        summary = build_terminal_summary(
            terminal_status="complete",
            terminal_blocker=None,
            verification_status="passed",
            next_action="none",
            child_baml_closeout={
                "terminal_status": "complete",
                "verification_status": "passed",
                "produced_if_gates": ["IF-0-X-1"],
                "dirty_paths": [],
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_non_black_pixels": 19200,
                "visual_evidence_pixel_min": 0,
                "visual_evidence_pixel_max": 255,
                "visual_render_declared": True,
            },
        )
        self.assertEqual(summary["visual_evidence_path"], "shots/frame.png")
        self.assertEqual(
            summary["visual_evidence_observed"],
            {"non_black_pixels": 19200, "pixel_min": 0, "pixel_max": 255},
        )
        # FAV #272: the DECLARED trigger must survive the SAME real production
        # write path (apply_child_terminal_summary_overlay's
        # visual_evidence_terminal_fields lift -> TERMINAL_SUMMARY_FIELDS
        # projection) as the evidence fields above -- this is the exact seam
        # Fix 1 (issue #91) had to fix once already (the whitelist silently
        # discarding the field), and both the validator's live ctx.terminal
        # and reconcile's persisted-event reader depend on it.
        self.assertIs(summary["visual_render_declared"], True)

    def test_ordinary_summary_omits_visual_fields(self):
        summary = build_terminal_summary(
            terminal_status="complete",
            terminal_blocker=None,
            verification_status="passed",
            next_action="none",
        )
        self.assertNotIn("visual_evidence_path", summary)
        self.assertNotIn("visual_evidence_observed", summary)
        self.assertNotIn("visual_render_declared", summary)


class VisualGateAuthoritativeBlockTest(unittest.TestCase):
    """Guarantee 2: a visual block reaches the authoritative reduction."""

    def setUp(self):
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.repo = make_repo(Path(self._td.name))
        self.roadmap = self.repo / "specs" / "phase-plans-v1.md"
        self.plan = write_phase_plan(self.repo, "RUNNER", self.roadmap, body=VISIBLE_AVATAR_BODY)
        commit_fixture_paths(self.repo, "add plan", self.plan)
        # A DIRTY (uncommitted) avatar/browser-media surface -- the real changed
        # path the runner reduction sees via _dirty_paths(repo).
        media = self.repo / "tests" / "fixtures" / "avatar_call.html"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_text("<html><body>avatar</body></html>\n", encoding="utf-8")

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review

    def _child(self, native_payload):
        return {
            "automation_status": "complete",
            "automation_verification_status": "passed",
            "native_closeout_payload": native_payload,
        }

    def _recheck(self, child):
        return runner_mod._closeout_gate_recheck(
            self.repo, self.roadmap, self.plan, child, "complete", None,
        )

    def test_missing_evidence_blocks_authoritatively_on_opt_in(self):
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "blocked")
        self.assertIsNotNone(outcome.event_blocker)
        self.assertEqual(outcome.event_blocker["blocker_class"], "review_gate_block")
        self.assertFalse(outcome.event_blocker.get("human_required", True))

    def test_missing_evidence_does_not_block_under_warn_default(self):
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
        })
        outcome = self._recheck(child)  # PHASE_LOOP_REVIEW unset -> warn
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")

    def test_valid_evidence_does_not_visual_block_on_opt_in(self):
        pytest.importorskip("PIL")
        artifact = self.repo / "shots" / "frame.png"
        # round-3 (codex CR): the gate now DERIVES pixel stats from the decoded
        # image -- a magic-header-only fake no longer suffices, the artifact
        # must be a REAL, varied (non-blank) PNG.
        write_varied_png(artifact)
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_path": "shots/frame.png",
            "visual_evidence_non_black_pixels": 19200,
            "visual_evidence_pixel_min": 0,
            "visual_evidence_pixel_max": 255,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")

    def test_blank_decoded_image_blocks_despite_fabricated_self_report_on_opt_in(self):
        pytest.importorskip("PIL")
        # round-3 (codex CR) core repro: a valid-header, REAL, but genuinely
        # BLANK (uniform) decoded image, paired with FABRICATED "good"
        # self-reported numbers, must still BLOCK -- the derived observation
        # is authoritative and the self-report can never override it.
        artifact = self.repo / "shots" / "frame.png"
        write_blank_png(artifact)
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_path": "shots/frame.png",
            "visual_evidence_non_black_pixels": 19200,
            "visual_evidence_pixel_min": 0,
            "visual_evidence_pixel_max": 255,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertEqual(outcome.automation_status, "blocked")

    def test_undecodable_artifact_fails_closed_on_opt_in(self):
        # Requires a REAL Pillow install: distinguishing "undecodable"
        # (Pillow present, decode failed on corrupt bytes) from
        # "cannot_verify" (Pillow itself absent) needs the real import to
        # succeed and then fail on the corrupt body.
        pytest.importorskip("PIL")
        # round-3 (codex CR) core repro: a valid-header but UNDECODABLE
        # (corrupt/truncated) artifact, paired with fabricated "good"
        # self-reported numbers, must BLOCK -- never silently pass on the
        # self-report because derivation itself could not run.
        artifact = self.repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_path": "shots/frame.png",
            "visual_evidence_non_black_pixels": 19200,
            "visual_evidence_pixel_min": 0,
            "visual_evidence_pixel_max": 255,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertIsNotNone(outcome.event_blocker)
        self.assertIn("visual_evidence_undecodable", outcome.event_blocker["blocker_summary"])
        self.assertEqual(outcome.automation_status, "blocked")

    def test_decoder_unavailable_fails_closed_on_opt_in(self):
        # A decoder-unavailable environment (Pillow import raises) must fail
        # CLOSED -- never fabricate a pass because derivation could not run.
        # CORE-ONLY fail-closed smoke (agent-harness#91 round-4 CR): must
        # PASS even when Pillow is genuinely absent -- derive_visual_
        # observation raises on `from PIL import Image` itself, before ever
        # touching the artifact's bytes, so a plain placeholder file (not
        # write_varied_png, which needs a real Pillow install) is enough and
        # no importorskip("PIL") guard belongs here.
        artifact = self.repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"placeholder, never decoded: decoder is what's missing")
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_path": "shots/frame.png",
            "visual_evidence_non_black_pixels": 19200,
            "visual_evidence_pixel_min": 0,
            "visual_evidence_pixel_max": 255,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}), patch.dict(
            sys.modules, {"PIL": None, "PIL.Image": None}
        ):
            outcome = self._recheck(child)
        self.assertIsNotNone(outcome.event_blocker)
        self.assertIn("visual_evidence_cannot_verify", outcome.event_blocker["blocker_summary"])
        self.assertEqual(outcome.automation_status, "blocked")

    def test_out_of_repo_artifact_still_blocks_on_opt_in(self):
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_path": "/etc/hostname",  # absolute out-of-repo escape
            "visual_evidence_non_black_pixels": 19200,
            "visual_evidence_pixel_min": 0,
            "visual_evidence_pixel_max": 255,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")

    def test_undeclared_missing_evidence_never_blocks_on_opt_in(self):
        # FAV #272 discriminating case: the trigger is DECLARED-only. A phase
        # that never declared visual_render_declared -- even with the SAME
        # missing-evidence shape as test_missing_evidence_blocks_
        # authoritatively_on_opt_in -- must never block, even under opt-in
        # `block`. This is the case that used to be gated by
        # avatar_visual_evidence_required(changed_paths, plan_text); it is
        # not read here at all anymore.
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": False,
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")
        self.assertNotEqual(outcome.automation_status, "blocked")

    def test_typed_opt_out_does_not_block_on_opt_in(self):
        child = self._child({
            "terminal_status": "complete",
            "verification_status": "passed",
            "produced_if_gates": [],
            "dirty_paths": [],
            "visual_render_declared": True,
            "visual_evidence_opt_out": "no_visible_media_surface",
        })
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            outcome = self._recheck(child)
        self.assertNotEqual(outcome.blocked_reason, "visual_evidence_missing_or_blank")


if __name__ == "__main__":
    unittest.main()
