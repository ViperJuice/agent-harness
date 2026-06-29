"""model-routing-v2 — governed pre-merge gate (relocated into closeout), run_mode
surfacing, artifact bundle. Updated for the advisor-panel relocation: the gate
reviews the EXACT staged index inside `_perform_phase_closeout`, author identity
is the UNION of dispatch `selected_executor` vendors, and verdicts are strict."""
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.governed_review import GateResult, resolve_run_mode
from phase_loop_runtime.governed_bundle import render_governed_bundle, staged_index_diff
from phase_loop_runtime.governed_premerge import run_governed_premerge_loop, LoopResult
from phase_loop_runtime.closeout_validators import ReviewFinding
from phase_loop_runtime import runner


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def _init_repo(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")


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


# --- 2. artifact bundle (renders from a precomputed staged-index diff) -----
class BundleTest(unittest.TestCase):
    def test_bundle_has_all_sections(self):
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "plan.md"
            plan.write_text("# plan\n\n## Acceptance Criteria\n- [ ] `pytest` passes\n", encoding="utf-8")
            bundle = render_governed_bundle(
                phase_alias="P1",
                terminal={"verification_status": "passed", "next_action": "done — ready to merge"},
                plan_path=plan,
                diff_text="diff --git a/src/x.py b/src/x.py\n+changed",
            )
        for section in ("## Change", "## Acceptance Criteria", "## Verification", "## Summary"):
            self.assertIn(section, bundle)
        self.assertIn("pytest", bundle)                 # acceptance criterion carried
        self.assertIn("done — ready to merge", bundle)  # summary
        self.assertIn("+changed", bundle)               # the precomputed diff is embedded

    def test_verification_commands_surface(self):
        # build_terminal_summary records per-command results under
        # `verification_commands`; the bundle must read that real key.
        bundle = render_governed_bundle(
            phase_alias="P1",
            terminal={"verification_commands": [
                {"command": "pytest -q", "status": "passed"},
                {"command": "ruff check", "status": "failed"},
            ]},
            plan_path=None,
            diff_text="(diff)",
        )
        self.assertIn("pytest -q", bundle)
        self.assertIn("ruff check", bundle)
        self.assertNotIn("no per-command results recorded", bundle)

    def test_staged_diff_is_exactly_the_committed_paths(self):
        # The gate reviews `git diff --cached` over the SAME paths closeout commits,
        # so a sibling phase's staged change is never in this phase's review.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
            _git(repo, "add", "base.py"); _git(repo, "commit", "-qm", "base")
            (repo / "a.py").write_text("OWNED = 1\n", encoding="utf-8")
            (repo / "sibling.py").write_text("SIBLING = 2\n", encoding="utf-8")
            _git(repo, "add", "a.py", "sibling.py")     # both staged
            diff = staged_index_diff(repo, ("a.py",))   # but review only owns a.py
        self.assertIn("OWNED", diff)
        self.assertNotIn("SIBLING", diff)

    def test_untracked_new_file_appears_in_staged_diff(self):
        # A new (untracked) file appears natively in `git diff --cached` once staged
        # — no `--no-index` synthesis needed (the relocation reviews the index).
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
            _git(repo, "add", "base.py"); _git(repo, "commit", "-qm", "base")
            (repo / "new_feature.py").write_text("MARKER_UNTRACKED = 42\n", encoding="utf-8")
            _git(repo, "add", "new_feature.py")         # staged, as closeout would
            diff = staged_index_diff(repo, ("new_feature.py",))
        self.assertIn("MARKER_UNTRACKED", diff)


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

    def test_degraded_no_reviewer_is_non_human_block_not_advisory_pass(self):
        # FAIL-CLOSED: a degraded gate result (no usable disjoint reviewer) must NOT
        # advisory-pass in governed mode — it halts non-human (was a fail-open).
        invoke = lambda **kw: GateResult(ran=True, promoted=False, degraded=True, reason="author_vendor_only")
        res = run_governed_premerge_loop(
            artifact="b", author_executor="claude", run_mode="governed",
            available_legs=("claude",), invoke=invoke,
        )
        self.assertFalse(res.mergeable)
        self.assertEqual(res.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertFalse(res.terminal_blocker["human_required"])

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
    """The implementing model must never review its own work, driven through the
    REAL `governed_planning_gate` with a spawn recorder."""

    def _spawned_legs(self, *, author_vendors):
        from phase_loop_runtime.governed_review import governed_planning_gate
        spawned = []

        def spawn(leg, artifact):
            spawned.append(leg)
            return ("ok", "AGREE — looks correct")

        governed_planning_gate(
            artifact="bundle",
            author_vendors=author_vendors,
            run_mode="governed",
            available_legs=("codex", "gemini", "claude"),
            spawn=spawn,
        )
        return spawned

    def test_single_author_vendor_excluded(self):
        spawned = self._spawned_legs(author_vendors=frozenset({"claude"}))
        self.assertNotIn("claude", spawned)
        self.assertEqual(set(spawned), {"codex", "gemini"})

    def test_union_of_authors_all_excluded(self):
        # rotation/repair: codex executed, claude repaired → BOTH excluded.
        spawned = self._spawned_legs(author_vendors=frozenset({"codex", "claude"}))
        self.assertEqual(set(spawned), {"gemini"})


# --- 4c. author vendor union from REAL event shape (action='run') ----------
class PhaseAuthorVendorsTest(unittest.TestCase):
    def _events(self, td, *executors):
        from phase_loop_runtime.events import append_event
        from phase_loop_runtime.models import LoopEvent
        repo = Path(td)
        for ex in executors:
            append_event(repo, LoopEvent(
                timestamp="2026-01-01T00:00:00Z", repo=str(repo), roadmap="r.md",
                phase="P1", action="run",            # the REAL action — NOT 'execute'
                status="executing", model="m", reasoning_effort="medium",
                source="default", override_reason=None, selected_executor=ex,
            ))
        return repo

    def test_union_across_dispatch_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._events(td, "codex", "claude")   # two authors via action='run'
            self.assertEqual(runner._phase_author_vendors(repo, "P1"), frozenset({"codex", "claude"}))

    def test_empty_when_no_recorded_executor(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(runner._phase_author_vendors(Path(td), "P1"), frozenset())


# --- 5. the relocated runner gate (`_governed_premerge_review`) ------------
class RunnerGateTest(unittest.TestCase):
    PATHS = ("src/x.py",)

    def test_skips_autonomous(self):
        out = runner._governed_premerge_review(
            Path("/tmp"), Path("r.md"), "P1", None, "complete", self.PATHS, {}, "autonomous",
        )
        self.assertIsNone(out)   # autonomous default: literal no-op

    def test_skips_plan_doc_closeout(self):
        out = runner._governed_premerge_review(
            Path("/tmp"), Path("r.md"), "P1", None, "planned", self.PATHS, {}, "governed",
        )
        self.assertIsNone(out)   # plan-doc closeout is the planning gate's job (P3)

    def test_skips_when_nothing_to_review(self):
        out = runner._governed_premerge_review(
            Path("/tmp"), Path("r.md"), "P1", None, "complete", (), {}, "governed",
        )
        self.assertIsNone(out)   # no staged paths → nothing to gate

    def test_blocks_on_non_mergeable_with_non_human_terminal(self):
        blocked = LoopResult(
            mergeable=False, ran=True, rounds=3,
            terminal_blocker={"human_required": False, "blocker_class": "review_gate_block",
                              "blocker_summary": "did not converge"},
            reason="non_convergence",
        )
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
             patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
            out = runner._governed_premerge_review(
                Path(td), Path(td) / "r.md", "P1", None, "complete", self.PATHS, {}, "governed",
            )
        self.assertIsNotNone(out)
        blocker, meta = out
        self.assertFalse(blocker["human_required"])
        self.assertEqual(blocker["blocker_class"], "review_gate_block")
        self.assertEqual(meta["closeout_action"], "review_gate_block")
        self.assertEqual(meta["verification_status"], "blocked")

    def test_block_surfaces_findings(self):
        blocked = LoopResult(
            mergeable=False, ran=True, rounds=1,
            findings=(ReviewFinding(code="panel_block", reason="endpoint skips auth", severity="block"),),
            terminal_blocker={"human_required": False, "blocker_class": "review_gate_block"},
            reason="non_convergence",
        )
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
             patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
            _blocker, meta = runner._governed_premerge_review(
                Path(td), Path(td) / "r.md", "P1", None, "complete", self.PATHS, {}, "governed",
            )
        findings = meta["governed_premerge"]["findings"]
        self.assertTrue(findings)
        self.assertEqual(findings[0]["code"], "panel_block")
        self.assertIn("endpoint skips auth", findings[0]["reason"])

    def test_passes_through_when_mergeable(self):
        ok = LoopResult(mergeable=True, ran=True, rounds=1)
        with tempfile.TemporaryDirectory() as td, \
             patch.object(runner, "governed_premerge_for_run", return_value=ok), \
             patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
            out = runner._governed_premerge_review(
                Path(td), Path(td) / "r.md", "P1", None, "complete", self.PATHS, {}, "governed",
            )
        self.assertIsNone(out)   # mergeable → caller proceeds to the commit


if __name__ == "__main__":
    unittest.main()
