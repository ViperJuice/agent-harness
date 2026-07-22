"""FAV (issue #91, Phase 4B) -- visual-avatar-evidence closeout validator.

Covers the detection contract (structural avatar/browser-media surface AND
explicit visible-render claim, both required), the pixel-evidence schema
(rejects black/blank/uniform frames), warn-default vs opt-in-block posture,
typed opt-out, and the false-positive boundary the contract is designed to
avoid (incidental keyword mentions with no owned media surface / no explicit
claim must stay silent, exactly like legacy non-media phases).
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.closeout_validators import (
    CloseoutContext,
    clear_closeout_validators,
    register_closeout_validator,
)
from phase_loop_runtime.models import VisualEvidenceObservation
from phase_loop_runtime.visual_avatar_evidence_validator import visual_avatar_evidence_validator

VISIBLE_AVATAR_PLAN = (
    "# FAV\n\n"
    "## Objective\n\n"
    "This phase renders a visible avatar in the browser meeting UI (synthetic media).\n\n"
    "## Exit criteria\n\n"
    "- [ ] The avatar renderer produces a visible avatar target via getUserMedia.\n"
)

INCIDENTAL_BROWSER_PLAN = (
    "# GENERIC\n\n## Objective\n\nThis integration test suite runs in a browser via Playwright.\n"
)

INCIDENTAL_VIDEO_PLAN = (
    "# PARSER\n\n## Objective\n\nThis phase tests video parsing for the ingest pipeline.\n"
)

LEGACY_PLAN = "# LEGACY\n\nA generic backend refactor phase, no media surface at all.\n"


def _ctx(plan, changed_paths=(), phase="FAV", terminal=None, automation=None):
    return CloseoutContext(
        phase_alias=phase,
        plan_path=str(plan),
        terminal=terminal or {"verification_status": "passed"},
        automation=automation or {"verification_status": "passed"},
        changed_paths=tuple(changed_paths),
    )


def _write_plan(td: str, text: str) -> Path:
    plan = Path(td) / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return plan


def _closeout(plan, changed_paths, terminal_extra=None):
    terminal = {"terminal_status": "complete", "verification_status": "passed"}
    terminal.update(terminal_extra or {})
    return build_phase_loop_closeout(
        phase_alias="FAV",
        plan_path=plan,
        terminal_summary=terminal,
        automation={"status": "complete", "verification_status": "passed", "human_required": False},
        changed_paths=changed_paths,
    )


class VisualAvatarEvidenceValidatorTest(unittest.TestCase):
    def setUp(self):
        clear_closeout_validators()
        register_closeout_validator(visual_avatar_evidence_validator)
        self._td = tempfile.TemporaryDirectory()
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)

    def tearDown(self):
        clear_closeout_validators()
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        self._td.cleanup()

    # --- detection contract: unit-level on the validator function ---

    def test_matching_phase_missing_evidence_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(plan, changed_paths=["tests/fixtures/avatar_call.html"])
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")
        self.assertEqual(findings[0].severity, "block")
        self.assertEqual(findings[0].blocker_class, "review_gate_block")

    def test_not_passed_is_clean(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(plan, changed_paths=["tests/fixtures/avatar_call.html"], terminal={"verification_status": "not_run"})
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- false-positive boundary ---

    def test_incidental_browser_mention_without_owned_media_file_is_silent(self):
        plan = _write_plan(self._td.name, INCIDENTAL_BROWSER_PLAN)
        ctx = _ctx(plan, changed_paths=["src/tests/playwright_runner.py"])
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_incidental_video_mention_without_owned_media_file_is_silent(self):
        plan = _write_plan(self._td.name, INCIDENTAL_VIDEO_PLAN)
        ctx = _ctx(plan, changed_paths=["src/ingest/video_parser.py"])
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_legacy_non_media_phase_is_silent(self):
        plan = _write_plan(self._td.name, LEGACY_PLAN)
        ctx = _ctx(plan, changed_paths=["src/runner.py", "src/models.py"])
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_owned_media_file_without_explicit_claim_is_silent(self):
        # Structural signal present (an .html fixture) but the plan makes no
        # explicit visible-render deliverable claim -- still silent.
        plan = _write_plan(self._td.name, INCIDENTAL_VIDEO_PLAN)
        ctx = _ctx(plan, changed_paths=["tests/fixtures/some_page.html"])
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_explicit_claim_without_owned_media_file_is_silent(self):
        # Explicit claim language present but no owned/changed media-surface file.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(plan, changed_paths=["src/runner.py"])
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- pixel-evidence schema: rejects black/blank/uniform frames ---

    def test_uniform_gray_frame_is_invalid(self):
        # #91's own repro: pixelMin==pixelMax==243, nonBlackPixels==19200.
        obs = VisualEvidenceObservation(non_black_pixels=19200, pixel_min=243, pixel_max=243)
        self.assertFalse(obs.is_valid())

    def test_all_black_frame_is_invalid(self):
        obs = VisualEvidenceObservation(non_black_pixels=0, pixel_min=0, pixel_max=0)
        self.assertFalse(obs.is_valid())

    def test_varied_frame_is_valid(self):
        obs = VisualEvidenceObservation(non_black_pixels=19200, pixel_min=0, pixel_max=255)
        self.assertTrue(obs.is_valid())

    def test_camel_case_observation_parses(self):
        obs = VisualEvidenceObservation.from_mapping({"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255})
        self.assertIsNotNone(obs)
        self.assertTrue(obs.is_valid())

    def test_uniform_gray_evidence_still_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 243, "pixelMax": 243},
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_all_black_evidence_still_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 0, "pixelMin": 0, "pixelMax": 0},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_varied_frame_evidence_is_clean(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_nested_artifact_paths_is_clean(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={
                "verification_status": "passed",
                "artifact_paths": {
                    "visual_evidence": "runs/x/frame.png",
                    "visual_evidence_observed": {"non_black_pixels": 500, "pixel_min": 10, "pixel_max": 200},
                },
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- typed opt-out ---

    def test_typed_opt_out_is_clean(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={"verification_status": "passed", "visual_evidence_opt_out": "no_visible_media_surface"},
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_untyped_opt_out_still_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            terminal={"verification_status": "passed", "visual_evidence_opt_out": "because_i_said_so"},
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    # --- end-to-end through closeout: warn-default / opt-in-block / no human_required ---

    def test_matching_phase_warns_but_completes_by_default(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        c = _closeout(plan, ["tests/fixtures/avatar_call.html"])
        self.assertEqual(c["terminal_status"], "complete")  # warn default never stalls
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertIn("visual_evidence_missing_or_blank", codes)

    def test_matching_phase_blocks_on_opt_in(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(plan, ["tests/fixtures/avatar_call.html"])
        self.assertEqual(c["terminal_status"], "blocked")
        self.assertEqual(c["blocker"]["blocker_class"], "review_gate_block")
        self.assertFalse(c["blocker"].get("human_required", True))  # never human_required

    def test_legacy_phase_no_finding_end_to_end(self):
        plan = _write_plan(self._td.name, LEGACY_PLAN)
        c = _closeout(plan, ["src/runner.py"])
        self.assertFalse(c["verification"]["results"])
        self.assertEqual(c["terminal_status"], "complete")


if __name__ == "__main__":
    unittest.main()
