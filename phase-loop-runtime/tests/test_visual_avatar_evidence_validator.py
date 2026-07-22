"""FAV (issue #91, Phase 4B) -- visual-avatar-evidence closeout validator.

Covers the detection contract (structural avatar/browser-media surface AND
explicit visible-render claim, both required), the pixel-evidence schema
(rejects black/blank/uniform frames), warn-default vs opt-in-block posture,
typed opt-out, and the false-positive boundary the contract is designed to
avoid (incidental keyword mentions with no owned media surface / no explicit
claim must stay silent, exactly like legacy non-media phases).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.closeout_validators import (
    CloseoutContext,
    clear_closeout_validators,
    register_closeout_validator,
)
from phase_loop_runtime.models import VisualEvidenceObservation, avatar_visible_render_claimed, derive_visual_observation
from phase_loop_runtime.visual_avatar_evidence_validator import visual_avatar_evidence_validator
from phase_loop_test_utils import (
    write_blank_png,
    write_l_trns_transparent_png,
    write_rgb_trns_partial_transparency_png,
    write_rgb_trns_transparent_png,
    write_transparent_varied_png,
    write_varied_png,
)

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


def _ctx(plan, changed_paths=(), phase="FAV", terminal=None, automation=None, repo_root=None, declared=True):
    """FAV #272: ``declared`` defaults True so the many pre-existing evidence-
    validation tests below (which only care about the evidence-schema/decode
    behavior once the gate is triggered) don't each need to spell out
    ``visual_render_declared`` explicitly. The false-positive-boundary tests
    (and anything exercising the new advisory) pass ``declared=False``
    explicitly."""
    terminal_dict = dict(terminal) if terminal is not None else {"verification_status": "passed"}
    terminal_dict.setdefault("visual_render_declared", declared)
    return CloseoutContext(
        phase_alias=phase,
        plan_path=str(plan),
        terminal=terminal_dict,
        automation=automation or {"verification_status": "passed"},
        changed_paths=tuple(changed_paths),
        repo_root=repo_root,
    )


NON_GOAL_AVATAR_PLAN = (
    "# FAV\n\n"
    "## Objective\n\n"
    "This phase parses meeting transcripts server-side.\n\n"
    "## Non-goals\n\n"
    "- This phase must not render a visible avatar in the browser meeting UI.\n"
)


def _write_plan(td: str, text: str) -> Path:
    plan = Path(td) / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return plan


def _closeout(plan, changed_paths, terminal_extra=None, declared=False):
    terminal = {"terminal_status": "complete", "verification_status": "passed"}
    terminal["visual_render_declared"] = declared
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
        ctx = _ctx(plan, changed_paths=["src/tests/playwright_runner.py"], declared=False)
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_incidental_video_mention_without_owned_media_file_is_silent(self):
        plan = _write_plan(self._td.name, INCIDENTAL_VIDEO_PLAN)
        ctx = _ctx(plan, changed_paths=["src/ingest/video_parser.py"], declared=False)
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_legacy_non_media_phase_is_silent(self):
        plan = _write_plan(self._td.name, LEGACY_PLAN)
        ctx = _ctx(plan, changed_paths=["src/runner.py", "src/models.py"], declared=False)
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- FAV #272: the heuristic (either axis alone) never blocks -- it only
    # raises the non-blocking visual_render_undeclared_surface advisory when
    # the phase never declared. These next two used to assert the CONTRACT
    # (structural AND claim) never blocked on a single axis; now that block
    # is declared-only, a single axis firing is exactly what the advisory
    # exists to flag. ---

    def test_owned_media_file_without_explicit_claim_raises_advisory_only(self):
        # Structural signal present (an .html fixture), no declaration --
        # raises the advisory (never a block) even though the plan makes no
        # explicit visible-render deliverable claim.
        plan = _write_plan(self._td.name, INCIDENTAL_VIDEO_PLAN)
        ctx = _ctx(plan, changed_paths=["tests/fixtures/some_page.html"], declared=False)
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_render_undeclared_surface")
        self.assertEqual(findings[0].severity, "warn")
        self.assertIsNone(findings[0].blocker_class)

    def test_explicit_claim_without_owned_media_file_raises_advisory_only(self):
        # Explicit claim language present, no owned/changed media-surface
        # file, no declaration -- raises the advisory only.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(plan, changed_paths=["src/runner.py"], declared=False)
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_render_undeclared_surface")
        self.assertEqual(findings[0].severity, "warn")

    def test_neither_axis_and_not_declared_is_fully_silent(self):
        # Neither the structural surface nor the explicit claim fires --
        # genuinely nothing to advise on either.
        plan = _write_plan(self._td.name, LEGACY_PLAN)
        ctx = _ctx(plan, changed_paths=["src/runner.py"], declared=False)
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

    def test_out_of_range_observation_is_rejected(self):
        # Fix 4: pixel channel values must be 0..255; a count must be >= 0.
        self.assertIsNone(VisualEvidenceObservation.from_mapping({"nonBlackPixels": 1, "pixelMin": 0, "pixelMax": 300}))
        self.assertIsNone(VisualEvidenceObservation.from_mapping({"nonBlackPixels": 1, "pixelMin": -1, "pixelMax": 255}))
        self.assertIsNone(VisualEvidenceObservation.from_mapping({"nonBlackPixels": -5, "pixelMin": 0, "pixelMax": 255}))

    # --- Fix 3 (round-6 CR): coverage floor -- a single opaque pixel (or a
    # tiny frame) must not masquerade as real visual evidence. ---

    def test_single_opaque_pixel_in_large_frame_is_invalid(self):
        # #91 round-6 repro: a 1000x1000 frame with exactly ONE non-black
        # pixel -- non_black_pixels=1 (>0) and differing extrema (from that
        # one pixel) previously satisfied is_valid() even though the frame is
        # visually blank. The coverage-fraction floor (1% of total_pixels)
        # must reject this once total_pixels is known.
        obs = VisualEvidenceObservation(non_black_pixels=1, pixel_min=0, pixel_max=255, total_pixels=1_000_000)
        self.assertFalse(obs.is_valid())

    def test_below_minimum_dimension_floor_is_invalid(self):
        # A tiny (10x10 = 100px) frame, even 100% non-black, must fail the
        # minimum-total-pixels floor (16x16 = 256px).
        obs = VisualEvidenceObservation(non_black_pixels=100, pixel_min=0, pixel_max=255, total_pixels=100)
        self.assertFalse(obs.is_valid())

    def test_meaningful_coverage_fraction_is_valid(self):
        # A real avatar frame: e.g. a 100x100 (10,000px) image with 20%
        # non-black coverage clears both the dimension and fraction floors.
        obs = VisualEvidenceObservation(non_black_pixels=2000, pixel_min=0, pixel_max=255, total_pixels=10_000)
        self.assertTrue(obs.is_valid())

    def test_unknown_total_pixels_keeps_legacy_semantics(self):
        # A self-reported observation with no total_pixels (the pre-round-6
        # shape) must still parse and pass under the ORIGINAL
        # non_black_pixels>0 + pixel_min!=pixel_max semantics -- the
        # coverage-floor checks are skipped entirely when total_pixels is
        # unknown (0), preserving back-compat for callers that can't supply
        # it.
        obs = VisualEvidenceObservation(non_black_pixels=19200, pixel_min=0, pixel_max=255)
        self.assertEqual(obs.total_pixels, 0)
        self.assertTrue(obs.is_valid())

    def test_non_black_exceeding_total_pixels_is_rejected(self):
        # An impossible observation (more non-black pixels than the frame
        # has total) is malformed the same way pixel_min > pixel_max is.
        with self.assertRaises(ValueError):
            VisualEvidenceObservation(non_black_pixels=200, pixel_min=0, pixel_max=255, total_pixels=100)

    def test_single_pixel_evidence_finds_end_to_end(self):
        # End-to-end: the closeout validator must find a self-reported
        # single-opaque-pixel "pass" when the total_pixels the DECODED image
        # actually carries reveals it as a near-blank frame.
        pytest.importorskip("PIL")
        from PIL import Image

        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1000, 1000), color=(0, 0, 0))
        img.putpixel((0, 0), (255, 255, 255))
        img.save(artifact, format="PNG")
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

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
        pytest.importorskip("PIL")
        # round-3 (codex CR): a repo_root=None caller can prove neither
        # containment nor decode the artifact, so it now fails closed (see
        # test_missing_repo_root_still_finds_even_with_good_self_report below)
        # -- this test supplies a real repo_root + a REAL, varied, decodable
        # PNG so the DERIVED observation (not the self-report) makes it clean.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_missing_repo_root_still_finds_even_with_good_self_report(self):
        # round-3 (codex CR): repo_root=None can never prove containment or
        # decode the artifact -- it must fail closed rather than fall back to
        # trusting a self-reported observation (that fallback WAS the hole).
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
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_nested_artifact_paths_is_clean(self):
        pytest.importorskip("PIL")
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "runs" / "x" / "frame.png"
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
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

    # --- Fix 5: negation / Non-goals must NOT match (no false-positive block) ---

    def test_non_goal_negated_claim_never_blocks(self):
        # "must not render a visible avatar" under a Non-goals section, with an
        # owned .html surface, must NEVER produce a BLOCK finding -- the claim
        # detector correctly reads this as NOT an affirmative deliverable, but
        # under #272 that nuance no longer matters for blocking anyway (block
        # is declared-only). The owned .html surface still raises the
        # non-blocking advisory (the OR axis fires on structure alone).
        plan = _write_plan(self._td.name, NON_GOAL_AVATAR_PLAN)
        ctx = _ctx(plan, changed_paths=["tests/fixtures/avatar_call.html"], declared=False)
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_render_undeclared_surface")
        self.assertEqual(findings[0].severity, "warn")

    def test_non_goal_negated_claim_is_silent_end_to_end(self):
        plan = _write_plan(self._td.name, NON_GOAL_AVATAR_PLAN)
        c = _closeout(plan, ["tests/fixtures/avatar_call.html"], declared=False)
        # Never blocks -- the sole result is the non-blocking advisory.
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertEqual(codes, ["visual_render_undeclared_surface"])
        self.assertEqual(c["verification"]["results"][0]["severity"], "warn")
        self.assertEqual(c["terminal_status"], "complete")

    # --- round-5 CR (codex): detector false-NEGATIVE -- title / IF-gate /
    # exit-gate headings must be evaluated, and "without" must not suppress
    # an otherwise-affirmative claim (negation is claim-local). ---

    def test_title_heading_claim_is_detected(self):
        # The phase TITLE ("# " heading) itself carries the claim -- was
        # previously invisible to the scan (headings were only used to
        # toggle scope, never evaluated for a claim themselves).
        self.assertTrue(avatar_visible_render_claimed("# AV-1 — Visible Avatar\n\nSome body text here.\n"))

    def test_if_gate_heading_body_claim_is_detected(self):
        # An IF-gate heading ("## IF-AV-1-1") doesn't match the affirmative-
        # keyword regex, so it previously turned scanning OFF for its body --
        # silently hiding a real claim underneath it.
        self.assertTrue(
            avatar_visible_render_claimed("## IF-AV-1-1\n\nThe avatar renders visibly in the browser.\n")
        )

    def test_exit_gate_heading_body_claim_is_detected(self):
        # Same shape as the IF-gate case for an "Exit gate" heading (does not
        # match "exit\s+criteria").
        self.assertTrue(
            avatar_visible_render_claimed("## Exit gate\n\nRenders a visible avatar in the meeting UI.\n")
        )

    def test_without_qualified_affirmative_claim_is_detected(self):
        # Negation must be CLAIM-LOCAL: a "without <X>" qualifier on an
        # affirmative claim is not a negation of the claim itself.
        self.assertTrue(
            avatar_visible_render_claimed(
                "## Objective\n\nRenders a visible avatar without operator intervention.\n"
            )
        )

    def test_title_heading_claim_end_to_end(self):
        plan = _write_plan(
            self._td.name,
            "# AV-1 — Visible Avatar\n\nThis phase adds the visible-avatar render path.\n",
        )
        ctx = _ctx(plan, changed_paths=["tests/fixtures/avatar_call.html"])
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_exit_gate_heading_claim_end_to_end(self):
        plan = _write_plan(
            self._td.name,
            "# FAV\n\n## Exit gate\n\nRenders a visible avatar in the meeting UI.\n",
        )
        ctx = _ctx(plan, changed_paths=["tests/fixtures/avatar_call.html"])
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_unrecognized_heading_still_respects_fp_boundary(self):
        # An unrecognized heading is now affirmative-eligible, but that alone
        # must not manufacture a claim where none exists -- no claim pattern,
        # no finding, even under an unnamed heading.
        self.assertFalse(
            avatar_visible_render_claimed("## Some Unnamed Section\n\nThis integration test runs in a browser.\n")
        )

    # --- round-6 CR (regression in the round-5 fix + negation scoping): a
    # nested subheading under Non-goals must NOT flip scope back on, and
    # negation must be CLAIM-SPAN-LOCAL (a preceding "without" still negates;
    # a trailing "without" qualifier on an affirmative claim does not). ---

    def test_nested_heading_under_non_goals_stays_out_of_scope(self):
        # The round-5 fix's redundant if/else both set in_scope=True for ANY
        # unrecognized heading -- including one NESTED under a Non-goals
        # section -- silently turning scanning back ON and resurfacing the
        # exact false-positive Fix 5 exists to close. A depth-aware scope
        # stack must keep the nested heading's body OUT of scope.
        self.assertFalse(
            avatar_visible_render_claimed(
                "# FAV\n\n"
                "## Non-goals\n\n"
                "- This phase must not render a visible avatar in the browser meeting UI.\n\n"
                "### Rationale\n\n"
                "It renders a visible avatar in a way that is explicitly forbidden here.\n"
            )
        )

    def test_scope_restored_after_non_goals_section_ends(self):
        # A sibling heading at the SAME depth as the Non-goals heading must
        # pop it off the scope stack and restore the parent's (affirmative)
        # scope -- Non-goals must not leak into a later top-level section.
        self.assertTrue(
            avatar_visible_render_claimed(
                "# FAV\n\n"
                "## Non-goals\n\n"
                "- This phase must not render a visible avatar server-side only.\n\n"
                "## Deliverable\n\n"
                "This phase renders a visible avatar in the browser meeting UI.\n"
            )
        )

    def test_validate_without_rendering_is_negated(self):
        # A NEGATING "without" precedes the claim -- must suppress it (this is
        # the over-negation-removal regression: round-5 stripped "without"
        # globally from the negation regex, which fixed the trailing-
        # qualifier false-negative but broke this true negation).
        self.assertFalse(
            avatar_visible_render_claimed("## Objective\n\nWe validate without rendering a visible avatar.\n")
        )

    # --- Fix 4: artifact must EXIST inside the repo when a repo root is known ---

    def test_repo_root_valid_artifact_is_clean(self):
        pytest.importorskip("PIL")
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        # agent-harness#91 round-3 (codex CR): the gate now DERIVES pixel stats
        # from the DECODED image, so this must be a REAL, varied (non-blank)
        # PNG -- a magic-header-only fake is now UNDECODABLE and fails closed.
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_repo_root_nonexistent_artifact_still_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/nope.png",  # never created
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_repo_root_out_of_repo_artifact_still_finds(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "/etc/hostname",  # absolute out-of-repo escape
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_flat_baml_encoded_observation_valid_artifact_is_clean(self):
        pytest.importorskip("PIL")
        # Fix 1: the flat BAML encoding (visual_evidence_non_black_pixels/...)
        # folded onto the terminal summary is accepted equivalently.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        # agent-harness#91 round-3 (codex CR): the gate now DERIVES pixel stats
        # from the DECODED image, so this must be a REAL, varied (non-blank)
        # PNG -- a magic-header-only fake is now UNDECODABLE and fails closed.
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_non_black_pixels": 19200,
                "visual_evidence_pixel_min": 0,
                "visual_evidence_pixel_max": 255,
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- Fix 3 round 2 (codex): evidence must be a real, decodable image file ---

    def test_repo_root_directory_path_still_finds(self):
        # codex probe: visual_evidence_path="." (the repo directory itself)
        # must be REJECTED -- exists() alone is not "a valid artifact".
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": ".",
                "visual_evidence_observed": {"nonBlackPixels": 1, "pixelMin": 0, "pixelMax": 1},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_text_renamed_to_png_still_finds(self):
        # A plain-text file merely renamed to .png must be REJECTED -- it has no
        # valid image magic-number header.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("this is not an image\n", encoding="utf-8")
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_valid_png_header_artifact_is_clean(self):
        pytest.importorskip("PIL")
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    def test_pixel_min_greater_than_max_still_finds(self):
        # codex probe: {"nonBlackPixels": 1, "pixelMin": 0, "pixelMax": ...} with
        # min > max describes no real frame -- self-report is now never
        # authoritative anyway, but the artifact here is still the
        # magic-header-only fake, which is UNDECODABLE, so this still finds
        # (for the derivation-failure reason, not the self-report shape).
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 1, "pixelMin": 200, "pixelMax": 10},
            },
        )
        self.assertEqual(len(visual_avatar_evidence_validator(ctx)), 1)

    def test_pixel_min_greater_than_max_rejected_at_the_model(self):
        self.assertIsNone(VisualEvidenceObservation.from_mapping({"nonBlackPixels": 1, "pixelMin": 200, "pixelMax": 10}))
        with self.assertRaises(ValueError):
            VisualEvidenceObservation(non_black_pixels=1, pixel_min=200, pixel_max=10)

    # --- round-3 (codex CR): the gate DERIVES pixel stats from the DECODED
    # image; self-reported observations can never override a failing derived
    # result, and derivation itself must fail CLOSED when it cannot run. ---

    def test_blank_decoded_image_still_finds_despite_fabricated_self_report(self):
        pytest.importorskip("PIL")
        # Core round-3 repro: a REAL, DECODABLE, but genuinely BLANK (uniform)
        # image, paired with FABRICATED "good" self-reported numbers, must
        # still find -- the derived observation is authoritative.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_blank_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_undecodable_artifact_finds_with_undecodable_code(self):
        # Requires a REAL Pillow install: distinguishing "undecodable" (Pillow
        # present, decode failed on corrupt bytes) from "cannot_verify"
        # (Pillow itself absent) needs the real import to succeed and then
        # fail on the corrupt body -- with no Pillow at all, this artifact
        # would report "cannot_verify" instead (asserted separately below).
        pytest.importorskip("PIL")
        # Core round-3 repro: a valid-header but UNDECODABLE (corrupt/
        # truncated) artifact, paired with fabricated "good" self-reported
        # numbers, must find with a distinct undecodable code -- self-reported
        # pixel observations are never accepted as a substitute.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_undecodable")
        self.assertEqual(findings[0].severity, "block")

    def test_decoder_unavailable_blocks_on_opt_in(self):
        # A decoder-unavailable environment (Pillow import raises) must fail
        # CLOSED under the opt-in `block` posture -- never fabricate a pass
        # because derivation could not run. This is the CORE-ONLY fail-closed
        # smoke (agent-harness#91 round-4 CR): it must PASS even when Pillow
        # is genuinely absent, so it deliberately does NOT use write_varied_png
        # (which needs a real Pillow install to construct the fixture) --
        # derive_visual_observation raises on the `from PIL import Image`
        # import itself, before ever touching the artifact's bytes, so a
        # plain placeholder file is sufficient and no
        # `pytest.importorskip("PIL")` guard belongs here.
        #
        # Fix 4b (round-6 CR): a decoder-unavailable environment is now
        # SILENT under the default warn posture (see
        # test_decoder_unavailable_is_silent_under_warn_default below) -- this
        # test explicitly opts into `block` to exercise the still-blocking
        # path.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"placeholder, never decoded: decoder is what's missing")
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}), patch.dict(
            sys.modules, {"PIL": None, "PIL.Image": None}
        ):
            findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_cannot_verify")
        self.assertEqual(findings[0].severity, "block")

    def test_decoder_unavailable_is_silent_under_warn_default(self):
        # Fix 4b (agent-harness#91 round-6 CR): ADOPTION DEFAULT -- a standard
        # install without the optional `visual` extra (Pillow) must NOT get a
        # `visual_evidence_cannot_verify` finding on every passing visual/
        # avatar phase closeout under the default warn posture; that would
        # spam every such phase purely because an optional dependency isn't
        # installed, not because anything is actually wrong. Under warn/off,
        # a decoder-unavailable environment must be SILENT (no finding at
        # all) -- distinct from `visual_evidence_undecodable` (a genuinely
        # corrupt artifact), which is still recorded as before.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"placeholder, never decoded: decoder is what's missing")
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
            findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(findings, [])

    def test_varied_real_image_is_clean(self):
        pytest.importorskip("PIL")
        # A real VARIED decoded image genuinely passes.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- round-4 (codex CR): a fully-transparent image with varied HIDDEN
    # RGB must not fail-open. `derive_visual_observation` previously
    # converted straight to grayscale, ignoring alpha, so the invisible RGB
    # variance leaked through as if it were on-screen. ---

    def test_transparent_varied_image_is_undecodable_as_blank(self):
        pytest.importorskip("PIL")
        # Direct repro at the derivation layer (codex probe): a fully
        # transparent RGBA PNG with varied hidden RGB must now decode as
        # uniformly black (non_black_pixels==0) and fail is_valid() --
        # NOT the pre-fix non_black_pixels=2/pixel_min=0/pixel_max=255/
        # is_valid()==True fail-open.
        artifact = Path(self._td.name) / "shots" / "transparent.png"
        write_transparent_varied_png(artifact)
        obs = derive_visual_observation(artifact)
        self.assertEqual(obs.non_black_pixels, 0)
        self.assertEqual(obs.pixel_min, 0)
        self.assertEqual(obs.pixel_max, 0)
        self.assertFalse(obs.is_valid())

    def test_transparent_varied_evidence_still_finds_despite_fabricated_self_report(self):
        pytest.importorskip("PIL")
        # End-to-end through the validator: a genuinely-transparent (visually
        # blank) artifact, paired with fabricated "good" self-reported
        # numbers, must still find -- the derived observation (post-alpha-
        # composite) is authoritative, exactly like the blank/uniform case.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_transparent_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_rgb_trns_transparent_image_is_undecodable_as_blank(self):
        pytest.importorskip("PIL")
        # Round-5 CR (codex): a mode-RGB PNG with a `tRNS` chunk decodes with
        # NO alpha channel at all -- the round-4 fix only checked
        # RGBA/LA/P-with-transparency, so this bypassed compositing entirely
        # and read the bright transparent-marked pixel as non-black. After
        # the fix, this composites to uniformly black and fails is_valid().
        artifact = Path(self._td.name) / "shots" / "rgb_trns.png"
        write_rgb_trns_transparent_png(artifact)
        obs = derive_visual_observation(artifact)
        self.assertEqual(obs.non_black_pixels, 0)
        self.assertEqual(obs.pixel_min, 0)
        self.assertEqual(obs.pixel_max, 0)
        self.assertFalse(obs.is_valid())

    def test_l_trns_transparent_image_is_undecodable_as_blank(self):
        pytest.importorskip("PIL")
        # Round-5 CR (codex): same fail-open shape as the RGB-tRNS case, for
        # the grayscale (mode L) decode path.
        artifact = Path(self._td.name) / "shots" / "l_trns.png"
        write_l_trns_transparent_png(artifact)
        obs = derive_visual_observation(artifact)
        self.assertEqual(obs.non_black_pixels, 0)
        self.assertEqual(obs.pixel_min, 0)
        self.assertEqual(obs.pixel_max, 0)
        self.assertFalse(obs.is_valid())

    def test_rgb_trns_transparent_evidence_still_finds_despite_fabricated_self_report(self):
        pytest.importorskip("PIL")
        # End-to-end through the validator: a tRNS-transparent (visually
        # blank) mode-RGB artifact, paired with fabricated "good"
        # self-reported numbers, must still find -- the derived observation
        # (post-alpha-composite) is authoritative.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_rgb_trns_transparent_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
                "visual_evidence_observed": {"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255},
            },
        )
        findings = visual_avatar_evidence_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "visual_evidence_missing_or_blank")

    def test_rgb_trns_partial_transparency_reflects_only_visible_pixels(self):
        pytest.importorskip("PIL")
        # Round-5 CR (codex): a mode-RGB tRNS image where a portion of the
        # pixels are transparent-marked and the rest are genuinely
        # visible/varied must reflect ONLY the opaque visible pixels -- the
        # transparent pixels composite to black (folded into the existing
        # black==blank semantics) without swallowing or inflating the real
        # variance. Fix 3 (round-6 CR): the fixture now tiles its 2x2 pattern
        # across a (16, 16) frame (see write_rgb_trns_partial_transparency_png)
        # so the derived observation also clears the new coverage floor --
        # non_black_pixels scales predictably as 2 non-black opaque pixels
        # (white + gray) per 2x2 tile.
        artifact = Path(self._td.name) / "shots" / "rgb_trns_partial.png"
        write_rgb_trns_partial_transparency_png(artifact)
        obs = derive_visual_observation(artifact)
        expected_non_black = 2 * (16 // 2) * (16 // 2)  # 2 visible opaque pixels per 2x2 tile
        self.assertEqual(obs.non_black_pixels, expected_non_black)
        self.assertEqual(obs.pixel_min, 0)  # transparent pixels + visible black pixels
        self.assertEqual(obs.pixel_max, 255)  # visible white pixels
        self.assertTrue(obs.is_valid())

    def test_opaque_varied_image_still_valid_after_alpha_fix(self):
        pytest.importorskip("PIL")
        # Keep the genuinely-visible (opaque, varied) path passing -- the
        # alpha-composite fix must not regress a real non-transparent frame.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        artifact = Path(self._td.name) / "shots" / "frame.png"
        write_varied_png(artifact)
        ctx = _ctx(
            plan,
            changed_paths=["tests/fixtures/avatar_call.html"],
            repo_root=self._td.name,
            terminal={
                "verification_status": "passed",
                "visual_evidence_path": "shots/frame.png",
            },
        )
        self.assertEqual(visual_avatar_evidence_validator(ctx), [])

    # --- end-to-end through closeout: warn-default / opt-in-block / no human_required ---

    def test_matching_phase_warns_but_completes_by_default(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        c = _closeout(plan, ["tests/fixtures/avatar_call.html"], declared=True)
        self.assertEqual(c["terminal_status"], "complete")  # warn default never stalls
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertIn("visual_evidence_missing_or_blank", codes)

    def test_matching_phase_blocks_on_opt_in(self):
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(plan, ["tests/fixtures/avatar_call.html"], declared=True)
        self.assertEqual(c["terminal_status"], "blocked")
        self.assertEqual(c["blocker"]["blocker_class"], "review_gate_block")
        self.assertFalse(c["blocker"].get("human_required", True))  # never human_required

    def test_undeclared_phase_never_blocks_even_on_opt_in(self):
        # FAV #272 discriminating case: the SAME structural surface + explicit
        # claim that used to trigger a block under the retired heuristic
        # contract must NEVER block now, even under opt-in `block` -- the
        # heuristic only ever reaches the non-blocking advisory. This is the
        # explicit posture-independence proof the redesign requires.
        plan = _write_plan(self._td.name, VISIBLE_AVATAR_PLAN)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(plan, ["tests/fixtures/avatar_call.html"], declared=False)
        self.assertEqual(c["terminal_status"], "complete")
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertEqual(codes, ["visual_render_undeclared_surface"])
        self.assertEqual(c["verification"]["results"][0]["severity"], "warn")

    def test_legacy_phase_no_finding_end_to_end(self):
        plan = _write_plan(self._td.name, LEGACY_PLAN)
        c = _closeout(plan, ["src/runner.py"], declared=False)
        self.assertFalse(c["verification"]["results"])
        self.assertEqual(c["terminal_status"], "complete")


if __name__ == "__main__":
    unittest.main()
