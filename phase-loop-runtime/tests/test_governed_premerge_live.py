"""model-routing-v2 P1 — live pre-merge gate, run_mode surfacing, artifact bundle."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.governed_review import GateResult, resolve_run_mode
from phase_loop_runtime.governed_bundle import render_governed_bundle
from phase_loop_runtime.governed_premerge import run_governed_premerge_loop, LoopResult
from phase_loop_runtime.closeout_validators import ReviewFinding
from phase_loop_runtime import runner


# --- 1. run_mode surfacing -------------------------------------------------
class RunModeSurfacingTest(unittest.TestCase):
    def test_run_governed_flag_parses(self):
        args = build_parser().parse_args(["run", "--governed", "--roadmap", "r.md"])
        self.assertTrue(args.governed)

    def test_run_without_flag_defaults_off(self):
        args = build_parser().parse_args(["run", "--roadmap", "r.md"])
        self.assertFalse(getattr(args, "governed", False))

    def test_env_surfacing(self):
        self.assertEqual(resolve_run_mode({"PHASE_LOOP_RUN_MODE": "governed"}), "governed")
        self.assertEqual(resolve_run_mode({}), "autonomous")


# --- 2. artifact bundle (IF-0-P1-1) ---------------------------------------
class BundleTest(unittest.TestCase):
    def test_bundle_has_all_sections(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = repo / "plan.md"
            plan.write_text("# plan\n\n## Acceptance Criteria\n- [ ] `pytest` passes\n", encoding="utf-8")
            terminal = {
                "phase_owned_dirty_paths": ["src/x.py"],
                "verification_status": "passed",
                "next_action": "done — ready to merge",
            }
            review_dir = repo / "rev"
            bundle, staged = render_governed_bundle(
                repo=repo, phase_alias="P1", terminal=terminal, plan_path=plan, review_dir=review_dir,
            )
            for section in ("## Change", "## Acceptance Criteria", "## Verification", "## Summary"):
                self.assertIn(section, bundle)
            self.assertIn("pytest", bundle)            # acceptance criterion carried
            self.assertIn("done — ready to merge", bundle)  # summary
            self.assertTrue(staged.is_file())          # staged to the read-only review dir


# --- 3/4. the loop the gate drives ----------------------------------------
def _gate(promoted, *, block=False, degraded=False):
    f = (ReviewFinding(code="x", reason="r", severity="block"),) if block else ()
    return GateResult(ran=True, promoted=promoted, findings=f, degraded=degraded)


class PremergeLoopTest(unittest.TestCase):
    def test_block_then_fix_then_pass_is_mergeable(self):
        seq = [_gate(False, block=True), _gate(True)]   # round1 block, round2 clean
        invoke = lambda **kw: seq.pop(0)
        fixes = []
        res = run_governed_premerge_loop(
            artifact="bundle-v0", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, cur, find: (fixes.append(rnd), "bundle-v1")[1],
            available_legs=("codex", "gemini"), invoke=invoke,
        )
        self.assertTrue(res.mergeable and res.ran)
        self.assertEqual(res.rounds, 2)
        self.assertEqual(fixes, [1])                   # one fix re-dispatch happened

    def test_non_convergence_is_non_human_terminal(self):
        invoke = lambda **kw: _gate(False, block=True)  # never converges
        res = run_governed_premerge_loop(
            artifact="b", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, cur, find: "b2",
            available_legs=("codex", "gemini"), invoke=invoke, max_rounds=3,
        )
        self.assertFalse(res.mergeable)
        self.assertIsNotNone(res.terminal_blocker)
        self.assertFalse(res.terminal_blocker["human_required"])
        self.assertEqual(res.terminal_blocker["blocker_class"], "review_gate_block")

    def test_autonomous_is_noop_zero_panel(self):
        invoke = Mock()
        res = run_governed_premerge_loop(
            artifact="b", author_executor="claude", run_mode="autonomous", invoke=invoke,
        )
        self.assertTrue(res.mergeable)
        self.assertFalse(res.ran)
        invoke.assert_not_called()                     # zero panel cost on the default path


# --- 5. the runner gate (live wiring) -------------------------------------
def _selection():
    return types.SimpleNamespace(
        model="m", effort="medium", source="default", override_reason=None, executor="claude",
    )


class RunnerGateTest(unittest.TestCase):
    def test_gate_skips_plan_doc_closeout(self):
        snap = types.SimpleNamespace(closeout_terminal_status="planned", terminal_summary={})
        out = runner._governed_premerge_gate(
            Path("/tmp"), Path("r.md"), "P1", None, snap, _selection(), "run"
        )
        self.assertIsNone(out)   # plan-doc closeouts are the planning gate's job (P3)

    def test_gate_blocks_on_non_mergeable_with_non_human_terminal(self):
        snap = types.SimpleNamespace(closeout_terminal_status="complete", terminal_summary={})
        blocked = LoopResult(
            mergeable=False, ran=True, rounds=3,
            terminal_blocker={"human_required": False, "blocker_class": "review_gate_block",
                              "blocker_summary": "did not converge"},
            reason="non_convergence",
        )
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
             patch.object(runner, "available_panel_legs", return_value=()):
            roadmap = Path(td) / "r.md"
            roadmap.write_text("# roadmap\n", encoding="utf-8")
            out = runner._governed_premerge_gate(
                Path(td), roadmap, "P1", None, snap, _selection(), "run"
            )
        self.assertIsNotNone(out)
        status, event = out
        self.assertEqual(status, "blocked")
        self.assertFalse(event.blocker["human_required"])
        self.assertEqual(event.blocker["blocker_class"], "review_gate_block")

    def test_gate_passes_through_when_mergeable(self):
        snap = types.SimpleNamespace(closeout_terminal_status="complete", terminal_summary={})
        ok = LoopResult(mergeable=True, ran=True, rounds=1)
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=ok), \
             patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
            out = runner._governed_premerge_gate(
                Path(td), Path("r.md"), "P1", None, snap, _selection(), "run"
            )
        self.assertIsNone(out)   # mergeable → caller falls through to the commit


if __name__ == "__main__":
    unittest.main()
