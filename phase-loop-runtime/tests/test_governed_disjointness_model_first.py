"""ABDHOME Lane 3 — governed reviewer≠author disjointness under model-first.

The correctness bug this fixes: ``select_reviewer_pool`` compared leg-NAMES to
author vendor FAMILIES. For the built-3 panel the leg name IS its family, so it
worked — but a custom/model-first board can put two same-vendor seats on
different lanes (``gpt-5.6-sol`` on ``codex`` AND on ``opencode``, both the ``codex``
family). Comparing ``"opencode" not in {"codex"}`` wrongly kept the opencode lane
as a reviewer of a codex-authored artifact — a silent same-vendor self-review.

The fix routes the governed gates onto the FROZEN ``advisor_board.schema`` vendor
projection (not a copy) and projects each available leg through
``vendor_of_harness`` before disjointness, so family-vs-family holds for custom
boards while staying byte-neutral for the built-3/default panel.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime import governed_review as gr
from phase_loop_runtime.advisor_board.fixtures import TWO_SAME_VENDOR_BOARD
from phase_loop_runtime.advisor_board.schema import vendor_of_harness
from phase_loop_runtime.governed_review import (
    GateResult,
    author_vendor_for_executor,
    author_vendor_for_model,
    governed_planning_gate,
    select_reviewer_pool,
)
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult


class GatesConsumeFrozenProjectionTests(unittest.TestCase):
    """The gates delegate to the ONE frozen projection, not a private copy."""

    def test_no_private_executor_vendor_copy_remains(self) -> None:
        self.assertFalse(hasattr(gr, "_EXECUTOR_VENDOR"))

    def test_author_vendor_for_executor_delegates_to_vendor_of_harness(self) -> None:
        for harness in ["codex", "opencode", "claude", "gemini", "pi", "cursor", ""]:
            self.assertEqual(author_vendor_for_executor(harness), vendor_of_harness(harness))

    def test_author_vendor_for_model_is_family_or_bare_model(self) -> None:
        self.assertEqual(author_vendor_for_model("gpt-5.6-sol"), "codex")
        self.assertEqual(author_vendor_for_model("claude-sonnet-5"), "claude")
        self.assertEqual(author_vendor_for_model("Gemini 3.1 Pro"), "gemini")
        self.assertEqual(author_vendor_for_model("some-unknown"), "some-unknown")


class SameVendorAcrossLanesExcludedTests(unittest.TestCase):
    """A same-vendor reviewer on a DIFFERENT lane is excluded (the bug)."""

    def test_opencode_reviewer_excluded_for_codex_author(self) -> None:
        pool, reason = select_reviewer_pool("codex", ("codex", "opencode", "claude"))
        self.assertNotIn("opencode", pool)  # openai-family lane, disjointness must exclude it
        self.assertNotIn("codex", pool)
        self.assertEqual(pool, ("claude",))
        self.assertIsNone(reason)

    def test_built3_disjointness_is_byte_neutral(self) -> None:
        # The leg name IS its family for the built-3, so nothing changes there.
        self.assertEqual(select_reviewer_pool("claude", ("codex", "gemini", "claude"))[0],
                         ("codex", "gemini"))
        self.assertEqual(select_reviewer_pool("codex", ("codex", "gemini", "claude"))[0],
                         ("gemini", "claude"))

    def test_two_same_vendor_board_has_no_disjoint_reviewer_for_that_vendor(self) -> None:
        legs = tuple(s.harness for s in TWO_SAME_VENDOR_BOARD.seats)  # ("codex", "opencode")
        pool, reason = select_reviewer_pool("codex", legs)
        self.assertEqual(pool, ())                    # both project to the codex family
        self.assertEqual(reason, "author_vendor_only")

    def test_two_same_vendor_board_is_a_valid_pool_for_a_disjoint_vendor(self) -> None:
        legs = tuple(s.harness for s in TWO_SAME_VENDOR_BOARD.seats)
        pool, reason = select_reviewer_pool("claude", legs)
        self.assertEqual(pool, ("codex", "opencode"))  # both disjoint from claude
        self.assertIsNone(reason)


class GovernedGateEndToEndTests(unittest.TestCase):
    """The disjointness correctness at the governed-gate boundary."""

    def test_codex_authored_artifact_cannot_be_reviewed_by_a_codex_family_lane(self) -> None:
        # A two-same-vendor (both codex-family) board authored by codex → NO disjoint
        # reviewer → the gate holds fail-closed rather than a silent self-review.
        def _invoke(*a, **k):  # must never be called — pool is empty first
            raise AssertionError("panel invoked despite no disjoint reviewer")

        result = governed_planning_gate(
            artifact="bundle",
            author_vendors=["codex"],
            run_mode="governed",
            available_legs=["codex", "opencode"],
            invoke=_invoke,
        )
        self.assertIsInstance(result, GateResult)
        self.assertTrue(result.ran)
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason, "author_vendor_only")

    def test_disjoint_vendor_author_runs_the_same_vendor_pool(self) -> None:
        # A claude author over the two-codex-family board: both lanes are valid
        # reviewers; a usable AGREE panel promotes.
        def _invoke(artifact, pool, **k):
            self.assertEqual(tuple(pool), ("codex", "opencode"))
            return PanelResult(legs=tuple(
                PanelLegResult(leg=leg, status="OK", text="AGREE") for leg in pool
            ))

        result = governed_planning_gate(
            artifact="bundle",
            author_vendors=["claude"],
            run_mode="governed",
            available_legs=["codex", "opencode"],
            invoke=_invoke,
        )
        self.assertTrue(result.ran)
        self.assertTrue(result.promoted)


if __name__ == "__main__":
    unittest.main()
