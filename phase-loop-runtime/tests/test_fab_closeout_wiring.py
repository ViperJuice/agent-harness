"""FAB (Consiliency/agent-harness#191) piece 2 — closeout wiring + byte-neutrality.

Verifies that the FAB producer wiring in ``runner._governed_premerge_review`` /
``runner._fab_closeout_producer`` is (a) BYTE-NEUTRAL when ``fab_run_id`` is
``None`` (the flag-off default — nothing is written to the run store), and (b)
captures the real panel + runs the producer transaction when scoped to FAB.
Deliberately UNMARKED so CI runs it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import fab_gate as fg
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime import runner as R
from phase_loop_runtime.governed_premerge import LoopResult
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult

_GIT = shutil.which("git")


def _panel() -> PanelResult:
    return PanelResult(
        legs=(
            PanelLegResult(leg="codex", status="OK", text="Reviewed.\n\nAGREE", seat_key="codex:x:high"),
            PanelLegResult(leg="gemini", status="OK", text="Reviewed.\n\nAGREE", seat_key="gemini:y:high"),
        )
    )


class CloseoutWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        if _GIT is None:  # pragma: no cover
            self.skipTest("git not available")
        self._tmp = tempfile.mkdtemp(prefix="fab-closeout-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.repo = Path(self._tmp) / "work"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for args in (("config", "user.email", "t@example.com"), ("config", "user.name", "Test")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True)
        (self.repo / "a.py").write_text("hello\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "c0"], check=True)
        (self.repo / "a.py").write_text("hello world\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "a.py"], check=True)

    def _patch_review(self, mergeable=True, panel=None):
        """Patch governed_premerge_for_run so no real panel is spawned."""
        def fake(*a, **kw):
            return LoopResult(mergeable=mergeable, ran=True, rounds=1, panel=panel)

        self._orig = R.governed_premerge_for_run
        R.governed_premerge_for_run = fake
        self.addCleanup(lambda: setattr(R, "governed_premerge_for_run", self._orig))

    def _run_store_files(self, run_id: str) -> list[str]:
        d = fp.provenance_dir_for_run(self.repo, run_id)
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir())

    def test_fab_off_capture_is_byte_neutral(self):
        """fab_run_id=None → the panel is NEVER captured; no run-store artifacts
        are written (the flag-off default is byte-for-byte unchanged)."""
        self._patch_review(mergeable=True, panel=_panel())
        result = R._governed_premerge_review(
            self.repo, self.repo / "roadmap.md", "phase-1", None, "complete",
            ("a.py",), {}, "governed", fab_run_id=None,
        )
        self.assertIsNone(result)  # passed → proceed to commit
        # No FAB run store directory was created for any tree.
        tree = subprocess.run(
            ["git", "-C", str(self.repo), "write-tree"], capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(self._run_store_files(f"fab-{tree}"), [])

    def test_fab_on_captures_panel_at_invocation(self):
        """fab_run_id set + a passing panel → durable seat ledger + expected-seat
        manifest + reviewed bundle snapshot are written at invocation."""
        self._patch_review(mergeable=True, panel=_panel())
        run_id = "fab-capture-test"
        result = R._governed_premerge_review(
            self.repo, self.repo / "roadmap.md", "phase-1", None, "complete",
            ("a.py",), {}, "governed", fab_run_id=run_id,
        )
        self.assertIsNone(result)
        files = self._run_store_files(run_id)
        self.assertIn(fg.SEAT_OUTCOMES_FILENAME, files)
        # piece 3b G1: the round record is now PER-EPOCH (candidate = epoch 1).
        self.assertIn(fg.review_round_path_for_run(self.repo, run_id, fg.FAB_CANDIDATE_EPOCH).name, files)
        durable = fg.read_seat_outcomes(self.repo, run_id)
        self.assertEqual(len(durable), 2)
        self.assertEqual({d.verdict for d in durable}, {"AGREE"})
        self.assertTrue(all(d.seat_instance_id for d in durable))
        # the round was NOT finalized yet (that happens post-commit).
        rr = fg.read_review_round(self.repo, run_id)
        self.assertFalse(rr.finalized)
        self.assertEqual(len(rr.expected_seats), 2)

    def test_fab_on_blocked_review_does_not_capture(self):
        """A non-mergeable governed review (block) captures nothing."""
        self._patch_review(mergeable=False, panel=_panel())
        run_id = "fab-blocked"
        R._governed_premerge_review(
            self.repo, self.repo / "roadmap.md", "phase-1", None, "complete",
            ("a.py",), {}, "governed", fab_run_id=run_id,
        )
        self.assertEqual(self._run_store_files(run_id), [])

    def test_e2e_capture_through_real_governed_path_then_producer(self):
        """Flag-ON e2e: exercise the REAL `_governed_premerge_review` (no mocked
        LoopResult — the panel is injected only at the `invoke_panel` seam) so
        the panel actually plumbs through to `capture_review_at_invocation`, then
        commit + run the REAL `_fab_closeout_producer`. This is the integration
        the whole CR round-1 failure came from lacking: it would have caught the
        `finalize_and_gate(reviewed_bundle_text=...)` TypeError and the dead
        capture."""
        from phase_loop_runtime import panel_invoker as pi

        # Standard FAB two-remote convention: `origin` is github-shaped (repo
        # IDENTITY for patch_digest), `fetchsrc` is a real local bare repo the
        # honesty gate fetches merge-base from. Set refs/remotes/origin/HEAD so
        # _fab_resolve_base_ref_name yields "main".
        base = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        origin = Path(self._tmp) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        for args in (
            ("remote", "add", "origin", "git@github.com:testorg/testrepo.git"),
            ("remote", "add", "fetchsrc", str(origin)),
            ("push", "-q", "fetchsrc", "HEAD:refs/heads/main"),
            ("update-ref", "refs/remotes/origin/main", base),
            ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
        ):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True)

        # Inject at the REAL spawn boundary (the only real-exec seam invoke_panel
        # uses) so the entire governed stack runs unmocked but no CLI is spawned.
        real_spawn = pi._default_spawn_via_provider
        pi._default_spawn_via_provider = lambda leg, artifact, **kw: ("OK", "Reviewed.\n\nAGREE")
        self.addCleanup(lambda: setattr(pi, "_default_spawn_via_provider", real_spawn))
        # Disjoint reviewer pool: author=claude, available legs=codex/gemini.
        self._orig_legs = R.available_panel_legs
        self._orig_authors = R._phase_author_vendors
        R.available_panel_legs = lambda *a, **k: ("codex", "gemini")
        R._phase_author_vendors = lambda *a, **k: ("claude",)
        self.addCleanup(lambda: setattr(R, "available_panel_legs", self._orig_legs))
        self.addCleanup(lambda: setattr(R, "_phase_author_vendors", self._orig_authors))

        reviewed_tree = subprocess.run(
            ["git", "-C", str(self.repo), "write-tree"], capture_output=True, text=True
        ).stdout.strip()
        reviewed_base = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        run_id = f"fab-{reviewed_tree}"

        gov = R._governed_premerge_review(
            self.repo, self.repo / "roadmap.md", "phase-1", None, "complete",
            ("a.py",), {}, "governed", fab_run_id=run_id,
        )
        self.assertIsNone(gov, "governed review should pass (AGREE panel)")
        # capture fired via the REAL path (not a mocked LoopResult.panel).
        durable = fg.read_seat_outcomes(self.repo, run_id)
        self.assertEqual(len(durable), 2)
        self.assertEqual({d.verdict for d in durable}, {"AGREE"})

        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "c1"], check=True)
        committed = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        outcome = R._fab_closeout_producer(
            self.repo, fab_run_id=run_id, reviewed_base_sha=reviewed_base, reviewed_tree=reviewed_tree,
            committed_head=committed, closeout_dirty_paths=("a.py",), origin="fetchsrc",
        )
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.wrote_provenance, outcome.skipped_reason)
        self.assertFalse(outcome.blocked, outcome.block_reason)
        self.assertEqual(fp.read_provenance(self.repo, run_id).candidate.head_sha, committed)

    def test_resolve_base_ref_name_none_without_origin(self):
        """No origin/HEAD → base ref unresolved → producer declines (None)."""
        self.assertIsNone(R._fab_resolve_base_ref_name(self.repo))
        out = R._fab_closeout_producer(
            self.repo, fab_run_id="fab-x", reviewed_base_sha="0" * 40, reviewed_tree="0" * 40,
            committed_head="0" * 40, closeout_dirty_paths=("a.py",),
        )
        self.assertIsNone(out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
