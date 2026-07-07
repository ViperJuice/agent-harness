"""model-routing-v1 P2 — panel-invoker (IF-0-P2-2). No live CLI calls."""
import unittest
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD
from phase_loop_runtime.panel_invoker import (
    LEG_STATUSES,
    PANEL_LEGS,
    PanelLegResult,
    PanelRequest,
    available_panel_legs,
    invoke_board,
    invoke_panel,
    invoke_panel_request,
    panel_leg_timeout_seconds,
)


class PanelInvokerTest(unittest.TestCase):
    def test_available_legs_uses_injected_probe(self):
        # only codex + claude "installed"
        present = {"codex", "claude"}
        legs = available_panel_legs(probe=lambda cli: cli in present)
        self.assertEqual(set(legs), {"codex", "claude"})

    def test_invoke_flags_per_leg_status(self):
        def spawn(leg, artifact):
            if leg == "codex":
                return ("ok", "AGREE. looks fine")
            if leg == "gemini":
                return ("timeout", "")
            raise RuntimeError("claude leg crashed")

        result = invoke_panel("ARTIFACT", ["codex", "gemini", "claude"], spawn=spawn)
        by_leg = {leg.leg: leg for leg in result.legs}
        self.assertEqual(by_leg["codex"].status, "OK")
        self.assertTrue(by_leg["codex"].usable)
        self.assertEqual(by_leg["gemini"].status, "TIMEOUT")
        self.assertFalse(by_leg["gemini"].usable)
        # a crashing leg is fail-closed to degraded, never raised
        self.assertEqual(by_leg["claude"].status, "DEGRADED")
        self.assertIn("crashed", by_leg["claude"].detail or "")
        self.assertEqual(result.usable_legs, (by_leg["codex"],))

    def test_ok_with_empty_text_becomes_empty(self):
        result = invoke_panel("A", ["codex"], spawn=lambda leg, art: ("ok", "   "))
        self.assertEqual(result.legs[0].status, "EMPTY")
        self.assertFalse(result.legs[0].usable)

    def test_unknown_status_degrades(self):
        result = invoke_panel("A", ["codex"], spawn=lambda leg, art: ("weird", "x"))
        self.assertEqual(result.legs[0].status, "DEGRADED")

    def test_leg_status_validation(self):
        with self.assertRaises(ValueError):
            PanelLegResult(leg="codex", status="not_a_status")

    def test_leg_statuses_are_canonical_uppercase(self):
        self.assertEqual(LEG_STATUSES, ("OK", "EMPTY", "TIMEOUT", "ERROR", "DEGRADED", "UNAVAILABLE"))
        self.assertEqual(PanelLegResult(leg="codex", status="ok", text="AGREE").status, "OK")
        self.assertEqual(PanelLegResult(leg="codex", status="Ok", text="AGREE").status, "OK")

    def test_panel_request_uses_metadata_only_redaction_and_scaled_timeouts(self):
        request = PanelRequest(artifact="x" * 400_000)
        self.assertEqual(request.redaction_posture, "metadata_only")
        self.assertGreater(request.timeout_seconds_for_leg("codex"), panel_leg_timeout_seconds("codex", "small"))
        self.assertLessEqual(request.timeout_seconds_for_leg("codex"), 1800)
        with self.assertRaises(ValueError):
            PanelRequest(artifact="bundle", redaction_posture="raw_payload")

    def test_panel_legs_are_three_vendors(self):
        self.assertEqual(PANEL_LEGS, ("codex", "gemini", "claude"))

    def test_artifact_ref_wins_over_inline_artifact_for_panel(self):
        seen = {}

        def spawn(leg, artifact):
            seen[leg] = artifact
            return ("ok", "AGREE")

        with TemporaryDirectory() as td:
            p = Path(td) / "bundle.md"
            p.write_text("FROM_ARTIFACT_REF", encoding="utf-8")
            invoke_panel("INLINE_SHOULD_NOT_APPEAR", ["codex"], artifact_ref=str(p), spawn=spawn)
        self.assertEqual(seen["codex"], "FROM_ARTIFACT_REF")

    def test_board_threads_artifact_ref_and_context_refs(self):
        seen = {}

        def spawn(leg, artifact):
            seen[leg] = artifact
            return ("ok", "AGREE")

        with TemporaryDirectory() as td:
            bundle = Path(td) / "bundle.md"
            bundle.write_text("FROM_BOARD_REF", encoding="utf-8")
            ref = Path(td) / "private.txt"
            ref.write_text("PRIVATE_BODY_ABSENT", encoding="utf-8")
            invoke_board(
                DEFAULT_BOARD,
                "INLINE",
                artifact_ref=str(bundle),
                context_refs=[str(ref)],
                spawn=spawn,
            )
        self.assertIn("FROM_BOARD_REF", seen["codex"])
        self.assertNotIn("PRIVATE_BODY_ABSENT", seen["codex"])
        self.assertIn(str(ref.resolve()), seen["codex"])

    def test_board_threads_timeout_overrides_to_real_spawn_boundary(self):
        seen_timeouts = {}

        def spawn(leg, artifact, **kwargs):
            seen_timeouts[leg] = kwargs.get("timeout_s")
            return ("OK", "AGREE")

        with patch("phase_loop_runtime.panel_invoker._default_spawn_via_provider", side_effect=spawn):
            invoke_board(
                DEFAULT_BOARD,
                "ARTIFACT",
                timeouts_by_leg={"gemini": 137},
                max_concurrency=1,
            )

        self.assertEqual(seen_timeouts["gemini"], 137)
        self.assertIsNone(seen_timeouts["codex"])
        self.assertIsNone(seen_timeouts["claude"])

    def test_panel_request_threads_artifact_ref_and_timeout_field_names(self):
        seen = {}

        def spawn(leg, artifact):
            seen[leg] = artifact
            return ("ok", "AGREE")

        with TemporaryDirectory() as td:
            p = Path(td) / "bundle.md"
            p.write_text("FROM_REQUEST_REF", encoding="utf-8")
            req = PanelRequest(
                artifact="INLINE",
                artifact_ref=str(p),
                legs=("codex",),
                timeout_seconds_by_leg={"codex": 123},
            )
            invoke_panel_request(req, spawn=spawn)
        self.assertEqual(seen["codex"], "FROM_REQUEST_REF")
        self.assertIn("timeout_seconds_by_leg", {field.name for field in fields(PanelRequest)})
        self.assertNotIn("timeouts_by_leg", {field.name for field in fields(PanelRequest)})

    def test_panel_request_deliberately_excludes_brief_ref(self):
        request_fields = {field.name for field in fields(PanelRequest)}
        self.assertNotIn("brief_ref", request_fields)


if __name__ == "__main__":
    unittest.main()
