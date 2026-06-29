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

    def test_verification_commands_surface(self):
        # #5: build_terminal_summary records per-command results under
        # `verification_commands`, NOT `verification`/`verification_results`; the
        # bundle must read the real key or the panel sees "no results recorded".
        from phase_loop_runtime.governed_bundle import render_governed_bundle
        with tempfile.TemporaryDirectory() as td:
            terminal = {
                "phase_owned_dirty_paths": ["src/x.py"],
                "verification_commands": [
                    {"command": "pytest -q", "status": "passed"},
                    {"command": "ruff check", "status": "failed"},
                ],
            }
            bundle, _ = render_governed_bundle(
                repo=Path(td), phase_alias="P1", terminal=terminal, plan_path=None,
            )
        self.assertIn("pytest -q", bundle)
        self.assertIn("ruff check", bundle)
        self.assertNotIn("no per-command results recorded", bundle)

    def test_owned_paths_exclude_whole_worktree(self):
        # #10: the bundle reviews exactly what closeout commits
        # (phase_owned_dirty_paths + previous_phase_owned_paths) — never the whole
        # worktree `dirty_paths`, which would leak a sibling phase's changes.
        from phase_loop_runtime.governed_bundle import _owned_dirty_paths
        paths = _owned_dirty_paths({
            "phase_owned_dirty_paths": ["a.py"],
            "previous_phase_owned_paths": ["carry.py", "a.py"],
            "dirty_paths": ["a.py", "sibling_phase.py"],
        })
        self.assertEqual(paths, ("a.py", "carry.py"))   # deduped; sibling excluded
        self.assertNotIn("sibling_phase.py", paths)

    def test_untracked_owned_file_appears_in_diff(self):
        # #3: plain `git diff HEAD` omits untracked NEW files; a phase that only
        # adds files must still present its change to the panel.
        import subprocess
        from phase_loop_runtime.governed_bundle import render_governed_bundle
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
            run("init", "-q")
            run("config", "user.email", "t@t")
            run("config", "user.name", "t")
            (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
            run("add", "base.py")
            run("commit", "-qm", "base")
            (repo / "new_feature.py").write_text("MARKER_UNTRACKED = 42\n", encoding="utf-8")  # untracked
            bundle, _ = render_governed_bundle(
                repo=repo, phase_alias="P1",
                terminal={"phase_owned_dirty_paths": ["new_feature.py"]}, plan_path=None,
            )
        self.assertIn("MARKER_UNTRACKED", bundle)       # the new file's content is reviewed


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


# --- 4b. reviewer ≠ author (the #4 de-masking guarantee) -------------------
class ReviewerExcludesAuthorTest(unittest.TestCase):
    """The implementing model must never review its own work. Driven through the
    REAL `governed_planning_gate` with a spawn recorder, so it would FAIL if the
    author vendor were empty (the masked bug: '' is disjoint from every leg, so
    the author's own leg gets spawned)."""

    def _spawned_legs(self, author_executor):
        from phase_loop_runtime.governed_review import governed_planning_gate
        spawned = []

        def spawn(leg, artifact):
            spawned.append(leg)
            return ("ok", "AGREE — looks correct")

        governed_planning_gate(
            artifact="bundle",
            author_executor=author_executor,
            run_mode="governed",
            available_legs=("codex", "gemini", "claude"),
            spawn=spawn,
        )
        return spawned

    def test_author_vendor_leg_is_excluded(self):
        spawned = self._spawned_legs("claude")
        self.assertNotIn("claude", spawned)            # the author's own leg never runs
        self.assertEqual(set(spawned), {"codex", "gemini"})

    def test_empty_author_would_self_review(self):
        # Documents the masked bug: with no author vendor, the claude leg IS spawned.
        # The wiring fix (_phase_author_vendor) is what prevents '' reaching here.
        self.assertIn("claude", self._spawned_legs(""))


# --- 5. the runner gate (live wiring) -------------------------------------
def _selection():
    # No `executor` field (ModelSelection has none); author vendor derives from
    # `model`. `claude-opus-4-8` → vendor `claude`.
    return types.SimpleNamespace(
        model="claude-opus-4-8", effort="medium", source="default", override_reason=None,
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

    def test_block_event_surfaces_findings(self):
        # #6: the run-end summary needs the panel findings on the terminal event,
        # else the operator sees only "blocked" with no review detail.
        snap = types.SimpleNamespace(closeout_terminal_status="complete", terminal_summary={})
        blocked = LoopResult(
            mergeable=False, ran=True, rounds=1,
            findings=(ReviewFinding(code="panel_block", reason="endpoint skips auth", severity="block"),),
            terminal_blocker={"human_required": False, "blocker_class": "review_gate_block"},
            reason="non_convergence",
        )
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
             patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
            roadmap = Path(td) / "r.md"
            roadmap.write_text("# roadmap\n", encoding="utf-8")
            _status, event = runner._governed_premerge_gate(
                Path(td), roadmap, "P1", None, snap, _selection(), "run"
            )
        findings = event.metadata["governed_premerge"]["findings"]
        self.assertTrue(findings)
        self.assertEqual(findings[0]["code"], "panel_block")
        self.assertIn("endpoint skips auth", findings[0]["reason"])

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
