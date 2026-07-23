"""FAB (Consiliency/agent-harness#191) activation milestone, piece 1 — wiring
the design §4.4 promotion-time re-assertion into the REAL merge path.
Deliberately UNMARKED (no ``dotfiles_integration``) so CI's
``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2 lesson).

Piece 1 is *wiring only*: no producer (writes FAB provenance on a board pass)
and no consumer (delta-review shortcut) exist yet, so FAB provenance is
fabricated here via the same Lane A/D helpers the existing Lane D tests use
(``fab_provenance``/``fab_gate``), exactly as the plan instructs.

Coverage:
  N1. Byte-neutrality (``PHASE_LOOP_FAB`` unset/absent — the default):
      ``train_runner._default_train_review`` and
      ``runner.governed_premerge_for_run`` never call
      ``fab_canonical.equivalent()``. Stash-proof: this asserts the exact
      property that is unchanged from ``main`` for the non-FAB path.
  N2. Byte-neutrality at the P4 merge-loop threading layer: ``run_train``'s
      merge loop never passes a ``run_id`` kwarg to ``merge_pr_fn`` — a
      strict 4-arg stub (the SAME shape every pre-existing
      ``test_train_merge.py`` stub uses) would ``TypeError`` otherwise. True
      regardless of ``PHASE_LOOP_FAB``, because no producer populates
      ``completed_nodes[node_id]["fab_run_id"]`` yet (piece 2, out of scope).
  P1. Flag ON + ``run_id=None`` -> still inert: merge proceeds,
      ``fab_canonical.equivalent()`` never called.
  P2. Flag ON + ``run_id`` set + no provenance recorded for it (scoped-
      missing/unreadable — ``ProvenanceNotFound``) -> fail CLOSED: merge
      REFUSED (``RuntimeError``), ``gh pr merge`` never invoked. FIX
      (agent-harness#191 CR): a present ``run_id`` is itself the FAB-scope
      marker, so ``ProvenanceNotFound`` (missing, deleted, cleaned up, wrong
      workspace, or a failed write) must NOT be treated as "never scoped to
      FAB" — that was the fail-open this test now pins closed. Matches
      ``fab_gate.py``'s own fail-closed contract at ``fab_gate_validator``.
  P2b. Same scoped-present-run_id posture, but the provenance artifact
      exists and is UNREADABLE/corrupt (malformed JSON -> raises
      ``ProvenanceInvalid``, not ``ProvenanceNotFound``) -> also fails
      CLOSED (``RuntimeError``), never an unhandled exception and never a
      silent merge.
  P3. Flag ON + ``run_id`` set + provenance exists + live PR unchanged ->
      merge proceeds (``equivalent()`` is called and returns EQUIVALENT).
  P4. Flag ON + ``run_id`` set + provenance exists + live content DRIFTED
      after the board pass (a new commit landed on the reviewed branch, base
      unchanged) -> merge REFUSED (``RuntimeError``); ``gh pr merge`` is
      never invoked.
  P5. Fail-closed: flag ON + ``run_id`` set + provenance exists + the live
      head cannot be resolved -> REFUSED.
  P6. Flag OFF (even though a drifted, provenance-bearing ``run_id`` is
      supplied) -> still inert; the opt-in flag gates everything.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from phase_loop_runtime import fab_canonical as fc
from phase_loop_runtime import fab_gate as fg
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime import governed_premerge as gp
from phase_loop_runtime import runner as runner_mod
from phase_loop_runtime.panel_invoker import SeatOutcomeRecord
from phase_loop_runtime.train_runner import _default_train_review, _live_merge_pr, run_train

from test_train_merge import (
    TRAIN_2NODE_MD,
    _FakeCompletedProcess,
    _approval_review_fn,
    _gh_subcommand,
    _make_merge_pr_stub,
    _make_publish_stub,
    _merged_sha_json,
    _preflight_pass,
    _premerge_json,
    _pr_is_open_true,
    _reverify_pass,
    _setup_p3_done,
)
from phase_loop_runtime.train_roadmap import parse_train_roadmap

_GIT = shutil.which("git")
_REAL_SUBPROCESS_RUN = subprocess.run  # captured before any test patches it
REPO_SLUG = "github.com/testorg/testrepo"


# --------------------------------------------------------------------------- #
# Git + FAB-provenance fixtures — mirrors test_fab_gate_d.py /
# test_fab_canonical_b.py's "two remotes" pattern (origin = github-shaped URL,
# identity-only; fetchsrc = a real local bare repo the live re-fetch actually
# reaches), duplicated here as plain functions (not a shared TestCase import)
# so pytest never double-collects an imported unittest.TestCase.
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = _REAL_SUBPROCESS_RUN(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _rev_parse(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _make_fab_repo(tmp_path: Path) -> Path:
    fetchsrc_dir = tmp_path / "fetchsrc.git"
    _REAL_SUBPROCESS_RUN(["git", "init", "-q", "--bare", str(fetchsrc_dir)], check=True)
    repo = tmp_path / "work"
    _REAL_SUBPROCESS_RUN(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", "git@github.com:testorg/testrepo.git")
    _git(repo, "remote", "add", "fetchsrc", str(fetchsrc_dir))
    return repo


def _write(repo: Path, relpath: str, content: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _rev_parse(repo)


def _push_main(repo: Path) -> None:
    _git(repo, "push", "-q", "-f", "fetchsrc", "HEAD:refs/heads/main")


def _finding(id_: str) -> fp.Finding:
    return fp.Finding(id=id_, severity="block", status="clean", path_scope=("a.py",), body_ref=f"sha256:{'0' * 64}")


def _seat(seat_key: str, *, finding_ids: tuple = ()) -> fp.ProvenanceSeat:
    return fp.ProvenanceSeat(
        seat_key=seat_key,
        vendor_leg="codex",
        required=True,
        status="OK",
        epoch=1,
        artifact_digest="1" * 64,
        evidence_digest="2" * 64,
        verdict="AGREE",
        finding_ids=finding_ids,
    )


def _durable_from_seat(seat: fp.ProvenanceSeat) -> SeatOutcomeRecord:
    return SeatOutcomeRecord(
        seat_key=seat.seat_key,
        vendor_leg=seat.vendor_leg,
        required=seat.required,
        status=seat.status,
        attempt_id="a1",
        epoch=seat.epoch,
        artifact_digest=seat.artifact_digest,
        completed_at="2026-01-01T00:00:00Z",
        evidence_digest=seat.evidence_digest,
        reason=None,
    )


def _persist_provenance(repo: Path, run_id: str, *, base_sha: str, head_sha: str) -> None:
    """Fabricate + persist a PASSING FAB provenance artifact reviewed at
    ``base_sha``..``head_sha`` (Lane A/D helpers only — no producer)."""
    pd = fc.patch_digest(repo, base_sha, head_sha, repo_slug=REPO_SLUG)
    scope = fp.ReviewScope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers_patch_digest=pd)
    candidate = fp.CandidateRecord(head_sha=head_sha, review_scope=scope, patch_digest=pd)
    seats = (_seat("codex:x:high", finding_ids=("f1",)),)
    findings = (_finding("f1"),)
    artifact = fp.ReviewProvenanceArtifact.build(
        repo=REPO_SLUG,
        base=fp.BaseBinding(ref_identity=f"{REPO_SLUG}#main", base_sha=base_sha),
        boundary_manifest=fp.BoundaryManifestRef(path=".advisor-board/boundaries.toml", source_rev=base_sha, digest="d" * 64),
        candidate=candidate,
        seats=seats,
        findings=findings,
    )
    fp.write_provenance(repo, run_id, artifact)
    for seat in seats:
        fg.append_seat_outcome(repo, run_id, _durable_from_seat(seat))


def _reviewed_pr(repo: Path, run_id: str) -> tuple[str, str]:
    """Build a `main` (base) + `pr1` (head) history, persist FAB provenance
    reviewed at exactly this base/head, and leave `pr1` checked out."""
    _write(repo, "a.py", "hello\n")
    base = _commit(repo, "c0")
    _push_main(repo)
    _git(repo, "checkout", "-qb", "pr1")
    _write(repo, "a.py", "hello world\n")
    head = _commit(repo, "c1 on pr1")
    _persist_provenance(repo, run_id, base_sha=base, head_sha=head)
    return base, head


def _make_gh_fake(*, base_ref: str, head, merged_sha: str = "sha-realmerge", calls: Optional[list] = None):
    """Fake ``gh`` responses (real ``git`` calls pass through unmocked — the
    FAB equivalence recompute needs a REAL repo, per the Lane B/D test
    convention: 'no mocked git for the core equivalence recompute')."""
    state = {"merged": False}

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
        if calls is not None:
            calls.append(cmd)
        label = _gh_subcommand(cmd)
        if label == "view-merged-sha":
            if not state["merged"]:
                return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", base_ref))
            return _FakeCompletedProcess(
                returncode=0, stdout=_merged_sha_json("MERGED", base_ref, sha=merged_sha, head=head)
            )
        if label == "view-premerge":
            if head is None:
                # Simulate an unresolvable live head: the combined pre-merge
                # `gh pr view` omits headRefOid entirely.
                return _FakeCompletedProcess(returncode=0, stdout=json.dumps({"isDraft": False, "baseRefName": base_ref}))
            return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(False, base_ref, head=head))
        if label == "merge":
            state["merged"] = True
            return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

    return fake_run


def _git_available():
    if _GIT is None:  # pragma: no cover - CI always has git
        pytest.skip("git not available")


# --------------------------------------------------------------------------- #
# N1 — byte-neutrality: the default (flag-off) path never calls equivalent()
# --------------------------------------------------------------------------- #


class TestByteNeutralDefault:
    def test_default_train_review_never_calls_equivalent(self, monkeypatch):
        monkeypatch.delenv(gp.FAB_PROMOTION_ENV, raising=False)

        def _boom(*a, **kw):
            raise AssertionError("fab_canonical.equivalent must NOT be called on the byte-neutral default path")

        with patch("phase_loop_runtime.fab_canonical.equivalent", side_effect=_boom):
            result = _default_train_review("irrelevant bundle text", "autonomous")

        assert result.mergeable is True
        assert result.ran is False
        assert result.reason == "autonomous"

    def test_governed_premerge_for_run_never_calls_equivalent(self, monkeypatch):
        monkeypatch.delenv(gp.FAB_PROMOTION_ENV, raising=False)

        def _boom(*a, **kw):
            raise AssertionError("fab_canonical.equivalent must NOT be called on the byte-neutral default path")

        with patch("phase_loop_runtime.fab_canonical.equivalent", side_effect=_boom):
            result = runner_mod.governed_premerge_for_run(
                artifact="x", author_executor="codex", run_mode="autonomous"
            )

        assert result.mergeable is True
        assert result.ran is False

    def test_governed_premerge_for_run_default_fab_promotion_check_is_none(self):
        """The new kwarg's default is None — the exact byte-neutral sentinel
        `run_governed_premerge_loop` already branches on."""
        import inspect

        sig = inspect.signature(runner_mod.governed_premerge_for_run)
        assert sig.parameters["fab_promotion_check"].default is None


# --------------------------------------------------------------------------- #
# N2 — byte-neutrality at the P4 merge-loop threading layer
# --------------------------------------------------------------------------- #


class TestP4LoopThreadingNeutral:
    @pytest.mark.parametrize("fab_flag", [None, "1"])
    def test_no_run_id_kwarg_leaks_to_merge_pr_fn(self, tmp_path: Path, monkeypatch, fab_flag):
        """A strict 4-arg `_merge_pr_fn` stub (the shape every pre-existing
        test_train_merge.py stub already uses) must never see a `run_id`
        kwarg — true regardless of PHASE_LOOP_FAB, since no producer
        populates `completed_nodes[node_id]["fab_run_id"]` yet."""
        if fab_flag is None:
            monkeypatch.delenv(gp.FAB_PROMOTION_ENV, raising=False)
        else:
            monkeypatch.setenv(gp.FAB_PROMOTION_ENV, fab_flag)

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)
        merge_order: List[str] = []

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_order),  # strict 4-arg signature
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merged"
        assert merge_order == ["repo-a", "repo-b"]


# --------------------------------------------------------------------------- #
# P1-P6 — _live_merge_pr's design §4.4 promotion re-assertion
# --------------------------------------------------------------------------- #


class TestLiveMergePrFabPromotion:
    def test_flag_on_no_run_id_is_inert(self, tmp_path: Path, monkeypatch):
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _base, head = _reviewed_pr(repo, "run-p1")

        def _boom(*a, **kw):
            raise AssertionError("equivalent() must not be called when run_id is None")

        with patch("phase_loop_runtime.fab_canonical.equivalent", side_effect=_boom):
            fake = _make_gh_fake(base_ref="main", head=head)
            with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
                sha = _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=head, run_id=None, fab_fetch_origin="fetchsrc"
                )
        assert sha == "sha-realmerge"

    def test_flag_on_run_id_scoped_missing_provenance_fails_closed(self, tmp_path: Path, monkeypatch):
        """FIX (agent-harness#191 CR, REAL fail-open): a trusted `run_id` is
        present (FAB-scoped) but no provenance was ever recorded for it —
        `fab_gate.read_provenance` raises `ProvenanceNotFound`. Pre-fix this
        was treated as inert ("never scoped to FAB") and the merge proceeded;
        that CONTRADICTS `fab_gate.py`'s own fail-closed contract
        (`fab_gate_validator` ~line 1014: run_id present + ProvenanceNotFound
        -> BLOCK). Post-fix this must REFUSE the merge, and `gh pr merge`
        must never be invoked."""
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _write(repo, "a.py", "hello\n")
        base = _commit(repo, "c0")
        _push_main(repo)
        _git(repo, "checkout", "-qb", "pr1")
        _write(repo, "a.py", "hello world\n")
        head = _commit(repo, "c1")
        # Deliberately never persist provenance for this run_id.

        calls: list = []
        fake = _make_gh_fake(base_ref="main", head=head, calls=calls)
        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
            with pytest.raises(RuntimeError, match="fab-promotion-reassertion-unresolvable"):
                _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=head,
                    run_id="run-never-persisted", fab_fetch_origin="fetchsrc",
                )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            "gh pr merge must never be invoked when a trusted run_id's provenance is missing/unreadable"
        )

    def test_flag_on_run_id_unreadable_provenance_fails_closed(self, tmp_path: Path, monkeypatch):
        """P2b: the provenance artifact EXISTS on disk but is corrupt/
        unreadable (malformed JSON -> `fab_provenance.ProvenanceInvalid`, a
        DIFFERENT exception than `ProvenanceNotFound`). Pre-fix this
        exception was not caught at all by
        `_fab_promotion_gate_before_merge` (only `ProvenanceNotFound` was
        handled) and would propagate as an unhandled crash instead of a
        controlled fail-closed refusal. Post-fix this must also REFUSE the
        merge cleanly, and `gh pr merge` must never be invoked."""
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _write(repo, "a.py", "hello\n")
        base = _commit(repo, "c0")
        _push_main(repo)
        _git(repo, "checkout", "-qb", "pr1")
        _write(repo, "a.py", "hello world\n")
        head = _commit(repo, "c1")

        run_id = "run-corrupt-provenance"
        prov_path = fp.provenance_path_for_run(repo, run_id)
        prov_path.parent.mkdir(parents=True, exist_ok=True)
        prov_path.write_text("{not valid json!!", encoding="utf-8")

        calls: list = []
        fake = _make_gh_fake(base_ref="main", head=head, calls=calls)
        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
            with pytest.raises(RuntimeError, match="fab-promotion-reassertion-unresolvable"):
                _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=head,
                    run_id=run_id, fab_fetch_origin="fetchsrc",
                )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            "gh pr merge must never be invoked when a trusted run_id's provenance is unreadable/corrupt"
        )

    def test_flag_on_provenance_exists_unchanged_merges(self, tmp_path: Path, monkeypatch):
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _base, head = _reviewed_pr(repo, "run-p3")

        fake = _make_gh_fake(base_ref="main", head=head)
        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
            sha = _live_merge_pr(
                repo, "feat/pr1", base="main", head_sha=head, run_id="run-p3", fab_fetch_origin="fetchsrc"
            )
        assert sha == "sha-realmerge"

    def test_flag_on_content_drift_refuses_merge(self, tmp_path: Path, monkeypatch):
        """design §4.4 residual closure (§4.2): the board reviewed `head`, but
        a LATER commit landed on the same branch post-review — the broker
        re-admitted it (so the EXISTING head-advance guard sees a consistent
        admitted/live head and does not itself catch this), yet the content
        no longer matches what FAB actually reviewed. Only the promotion
        re-assertion catches this."""
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _base, reviewed_head = _reviewed_pr(repo, "run-p4")
        _write(repo, "a.py", "hello world -- resolved differently, post-review\n")
        drifted_head = _commit(repo, "c2 not part of the reviewed head")

        calls: list = []
        fake = _make_gh_fake(base_ref="main", head=drifted_head, calls=calls)
        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
            with pytest.raises(RuntimeError, match=r"FAB promotion-time re-assertion failed.*design §4\.4"):
                _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=drifted_head,
                    run_id="run-p4", fab_fetch_origin="fetchsrc",
                )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], "gh pr merge must never be invoked when the FAB re-assertion refuses"

    def test_flag_on_unresolvable_live_head_fails_closed(self, tmp_path: Path, monkeypatch):
        _git_available()
        monkeypatch.setenv(gp.FAB_PROMOTION_ENV, "1")
        repo = _make_fab_repo(tmp_path)
        _base, head = _reviewed_pr(repo, "run-p5")

        calls: list = []
        # head=None -> the combined pre-merge `gh pr view` response omits
        # headRefOid; head_sha is also omitted so the pre-existing
        # head-advance guard (`if head_sha and current_head ...`) does not
        # itself intercept this before the FAB check is reached.
        fake = _make_gh_fake(base_ref="main", head=None, calls=calls)
        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
            with pytest.raises(RuntimeError, match="fab-promotion-reassertion-unresolvable"):
                _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=None,
                    run_id="run-p5", fab_fetch_origin="fetchsrc",
                )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], "gh pr merge must never be invoked when live identity is unresolvable"

    def test_flag_off_stays_inert_even_with_drifted_provenance(self, tmp_path: Path, monkeypatch):
        """The opt-in flag gates EVERYTHING: even a run_id whose provenance
        would fail the live re-check must not be looked at while
        PHASE_LOOP_FAB is off."""
        _git_available()
        monkeypatch.delenv(gp.FAB_PROMOTION_ENV, raising=False)
        repo = _make_fab_repo(tmp_path)
        _base, reviewed_head = _reviewed_pr(repo, "run-p6")
        _write(repo, "a.py", "drift while the flag is off\n")
        drifted_head = _commit(repo, "c2 drift, flag off")

        def _boom(*a, **kw):
            raise AssertionError("equivalent() must not be called while PHASE_LOOP_FAB is off")

        with patch("phase_loop_runtime.fab_canonical.equivalent", side_effect=_boom):
            fake = _make_gh_fake(base_ref="main", head=drifted_head)
            with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake):
                sha = _live_merge_pr(
                    repo, "feat/pr1", base="main", head_sha=drifted_head,
                    run_id="run-p6", fab_fetch_origin="fetchsrc",
                )
        assert sha == "sha-realmerge"
