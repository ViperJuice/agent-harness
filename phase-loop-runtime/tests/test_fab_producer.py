"""FAB (Consiliency/agent-harness#191) activation — piece 2 PRODUCER acceptance
tests. Deliberately UNMARKED (no ``dotfiles_integration``) so CI's
``-m "not dotfiles_integration"`` runs it (the goal-id-inc2 lesson). Uses REAL
temporary git repositories with a real bare origin — no mocked git for the
honesty gate / equivalence recompute.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import fab_canonical as fc
from phase_loop_runtime import fab_gate as fg
from phase_loop_runtime import fab_producer as prod
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult

_GIT = shutil.which("git")


def _panel(*verdicts: tuple[str, str, str]) -> PanelResult:
    """Build a PanelResult. Each verdict is (leg, seat_key, terminal_line)."""
    legs = tuple(
        PanelLegResult(leg=leg, status="OK", text=f"Reviewed.\n\n{line}", seat_key=seat_key)
        for leg, seat_key, line in verdicts
    )
    return PanelResult(legs=legs)


_DEFAULT_PANEL = (("codex", "codex:x:high", "AGREE"), ("gemini", "gemini:y:high", "AGREE"))


class ProducerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if _GIT is None:  # pragma: no cover
            self.skipTest("git not available")
        self._tmp = tempfile.mkdtemp(prefix="fab-producer-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.origin_dir = Path(self._tmp) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin_dir)], check=True)
        self.repo = Path(self._tmp) / "work"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self._run("config", "user.email", "t@example.com")
        self._run("config", "user.name", "Test")
        self._run("remote", "add", "origin", "git@github.com:testorg/testrepo.git")
        self._run("remote", "add", "fetchsrc", str(self.origin_dir))

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AssertionError(f"git {args} failed: {r.stderr}")
        return r

    def _write(self, rel: str, content: bytes | str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str) else content)

    def _rev(self, ref: str = "HEAD") -> str:
        return self._run("rev-parse", ref).stdout.strip()

    def _base_and_stage(self, *, extra_pre_commits: int = 0):
        """Base commit pushed to origin/main; then optional extra intermediate
        commits (to make the PR multi-commit); then stage a single change and
        return (reviewed_base_sha, reviewed_tree)."""
        self._write("a.py", "hello\n")
        self._run("add", "-A")
        self._run("commit", "-q", "-m", "c0 base")
        self._run("push", "-q", "-f", "fetchsrc", "HEAD:refs/heads/main")
        for i in range(extra_pre_commits):
            self._write(f"intermediate{i}.py", f"x{i}\n")
            self._run("add", "-A")
            self._run("commit", "-q", "-m", f"intermediate {i}")
        self._write("a.py", "hello world\n")
        self._run("add", "a.py")
        return self._rev("HEAD"), self._run("write-tree").stdout.strip()

    def _capture(self, run_id: str, bundle: str = "bundle text", panel=None) -> None:
        prod.capture_review_at_invocation(
            self.repo, run_id, _panel(*(panel or _DEFAULT_PANEL)), epoch=1, reviewed_bundle_text=bundle
        )

    def _commit_and_finalize(self, run_id, reviewed_base, reviewed_tree, *, dirty=("a.py",), bundle="bundle text"):
        self._run("commit", "-q", "-m", "c1 reviewed change")
        head = self._rev("HEAD")
        return head, prod.finalize_and_gate(
            self.repo,
            run_id,
            epoch=1,
            reviewed_base_sha=reviewed_base,
            reviewed_tree=reviewed_tree,
            committed_head_sha=head,
            closeout_dirty_paths=dirty,
            base_ref_name="main",
            origin="fetchsrc",
            reviewed_bundle_text=bundle,
        )

    # -- honesty: single clean closeout → provenance + PASS -----------------

    def test_single_commit_clean_closeout_writes_provenance_and_passes(self):
        base, tree = self._base_and_stage()
        run_id = "run-clean"
        self._capture(run_id)
        head, outcome = self._commit_and_finalize(run_id, base, tree)
        self.assertTrue(outcome.wrote_provenance, outcome.skipped_reason)
        self.assertFalse(outcome.blocked, outcome.block_reason)

        artifact = fp.read_provenance(self.repo, run_id)
        self.assertEqual(artifact.candidate.head_sha, head)
        self.assertEqual(
            artifact.candidate.review_scope.covers_patch_digest,
            fc.patch_digest(self.repo, base, head, repo_slug=fc.resolve_broker_repo_identity(self.repo)),
        )
        # covers exactly merge_base..head.
        self.assertEqual(artifact.base.base_sha, base)
        # the round identity is bound to the reviewed head + material.
        rr = fg.read_review_round(self.repo, run_id)
        self.assertTrue(rr.finalized)
        self.assertEqual(rr.reviewed_head_sha, head)

    # -- honesty: multi-commit PR → no provenance (fallback) ----------------

    def test_multi_commit_pr_writes_no_provenance(self):
        base, tree = self._base_and_stage(extra_pre_commits=1)
        # reviewed_base (HEAD before staging) is NOT the merge-base of origin/main
        # and head (there is an intermediate commit) → out of scope.
        run_id = "run-multi"
        self._capture(run_id)
        _head, outcome = self._commit_and_finalize(run_id, base, tree)
        self.assertFalse(outcome.wrote_provenance)
        self.assertFalse(outcome.blocked)
        self.assertEqual(outcome.skipped_reason, "multi_commit_pr_out_of_scope")
        with self.assertRaises(fp.ProvenanceNotFound):
            fp.read_provenance(self.repo, run_id)

    # -- honesty: empty closeout → no provenance ----------------------------

    def test_empty_closeout_writes_no_provenance(self):
        base, tree = self._base_and_stage()
        run_id = "run-empty"
        self._capture(run_id)
        self._run("commit", "-q", "-m", "c1")
        head = self._rev("HEAD")
        outcome = prod.finalize_and_gate(
            self.repo, run_id, epoch=1, reviewed_base_sha=base, reviewed_tree=tree,
            committed_head_sha=head, closeout_dirty_paths=(), base_ref_name="main",
            origin="fetchsrc", reviewed_bundle_text="bundle text",
        )
        self.assertFalse(outcome.wrote_provenance)
        self.assertEqual(outcome.skipped_reason, "empty_closeout")

    # -- honesty: pre-commit hook mutates the tree → no provenance ----------

    def test_hook_mutated_tree_writes_no_provenance(self):
        base, tree = self._base_and_stage()
        run_id = "run-hook"
        self._capture(run_id)
        # Simulate a pre-commit hook that mutated the tree AFTER review: the
        # committed tree differs from the reviewed tree the panel saw.
        self._write("a.py", "hello world -- SECRETLY MUTATED BY A HOOK\n")
        self._run("add", "a.py")
        self._run("commit", "-q", "-m", "c1 with hook mutation")
        head = self._rev("HEAD")
        outcome = prod.finalize_and_gate(
            self.repo, run_id, epoch=1, reviewed_base_sha=base, reviewed_tree=tree,
            committed_head_sha=head, closeout_dirty_paths=("a.py",), base_ref_name="main",
            origin="fetchsrc", reviewed_bundle_text="bundle text",
        )
        self.assertFalse(outcome.wrote_provenance)
        self.assertEqual(outcome.skipped_reason, "hook_mutated_tree")

    # -- complete-review-representation: binary-elided path → no provenance --

    def test_binary_changed_path_writes_no_provenance(self):
        self._write("a.py", "hello\n")
        self._run("add", "-A")
        self._run("commit", "-q", "-m", "c0")
        self._run("push", "-q", "-f", "fetchsrc", "HEAD:refs/heads/main")
        # A binary blob (NUL bytes) — git renders it only as "Binary files differ"
        # and numstat as "-\t-", i.e. the seats never saw its bytes.
        self._write("blob.bin", b"\x00\x01\x02\x03\xff\xfe\x00rest")
        self._run("add", "blob.bin")
        base = self._rev("HEAD")
        tree = self._run("write-tree").stdout.strip()
        run_id = "run-binary"
        self._capture(run_id)
        head, outcome = self._commit_and_finalize(run_id, base, tree, dirty=("blob.bin",))
        self.assertFalse(outcome.wrote_provenance)
        self.assertIsNotNone(outcome.skipped_reason)
        self.assertTrue(outcome.skipped_reason.startswith("incomplete_review_representation"), outcome.skipped_reason)

    # -- hard gate blocks even under PHASE_LOOP_REVIEW=warn -----------------

    def test_hard_gate_blocks_under_review_warn(self):
        base, tree = self._base_and_stage()
        run_id = "run-warn"
        self._capture(run_id)
        self._run("commit", "-q", "-m", "c1 reviewed change")
        head = self._rev("HEAD")
        # Advance the branch head AFTER review so the live head no longer matches
        # the reviewed head → the dedicated hard gate's equivalence recompute
        # INVALIDATES. Even with PHASE_LOOP_REVIEW=warn set, the producer's hard
        # gate is a direct compose_gate_status call (not the warn-downgradable
        # registry), so it BLOCKS.
        self._write("a.py", "hello world\nunreviewed extra\n")
        self._run("add", "a.py")
        self._run("commit", "-q", "-m", "c2 unreviewed drift")
        drifted = self._rev("HEAD")
        prev = os.environ.get("PHASE_LOOP_REVIEW")
        os.environ["PHASE_LOOP_REVIEW"] = "warn"
        try:
            outcome = prod.finalize_and_gate(
                self.repo, run_id, epoch=1, reviewed_base_sha=base, reviewed_tree=tree,
                committed_head_sha=head, closeout_dirty_paths=("a.py",), base_ref_name="main",
                origin="fetchsrc", reviewed_bundle_text="bundle text",
                # note: committed_head_sha is the REVIEWED head; the live head has
                # drifted — compose_gate_status is called with live_head=reviewed
                # head here, so instead assert via a follow-up gate below.
            )
        finally:
            if prev is None:
                os.environ.pop("PHASE_LOOP_REVIEW", None)
            else:
                os.environ["PHASE_LOOP_REVIEW"] = prev
        # provenance was written for the reviewed head; now re-run the dedicated
        # hard gate against the DRIFTED live head — it must BLOCK regardless of
        # PHASE_LOOP_REVIEW.
        self.assertTrue(outcome.wrote_provenance, outcome.skipped_reason)
        gate = fg.compose_gate_status(
            repo=self.repo, run_id=run_id, live_base_ref_name="main", live_head_sha=drifted, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_INVALIDATED)

    # -- authenticity is NON-tautological: durable ledger is the anchor ------

    def test_gate_blocks_when_durable_seat_ledger_dropped(self):
        """The produced provenance passes; but if the durable seat ledger the
        gate re-reads from disk is missing a required seat, the gate BLOCKS —
        proving the pass depends on the independently-written durable anchor, not
        the artifact's self-report."""
        base, tree = self._base_and_stage()
        run_id = "run-anchor"
        self._capture(run_id)
        head, outcome = self._commit_and_finalize(run_id, base, tree)
        self.assertTrue(outcome.wrote_provenance and not outcome.blocked)

        # Truncate the durable seat-outcome ledger to zero records (as if a seat
        # never ran) and re-gate: the artifact's seats now have no matching
        # durable record → BLOCK.
        ledger = fg.seat_outcomes_path_for_run(self.repo, run_id)
        ledger.write_text("", encoding="utf-8")
        gate = fg.compose_gate_status(
            repo=self.repo, run_id=run_id, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
