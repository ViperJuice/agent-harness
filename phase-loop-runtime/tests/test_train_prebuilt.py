"""Tests for the prebuilt-node publish mode of the release-train coordinator.

A prebuilt node lands an already-committed, independently-verified branch
WITHOUT re-executing the node's phase (no executor dispatch): run_loop is not
called, owned_paths come from the committed diff vs base, and publish pushes the
existing branch + opens a draft PR without a new commit.  The publish mutation
is routed through the credential BROKER — a prebuilt node with a
broker-authoritative ``CoordinatorRuntime`` publishes via the broker; without a
broker_client the publish primitive fails closed (``broker_required``), never a
direct push.

Run with:
    cd phase-loop-runtime && \
        PYTHONPATH=src:tests python -m pytest tests/test_train_prebuilt.py -q

All git/gh/run_loop/publish boundaries are stubbed for the coordinator tests;
the preflight/owned-paths/fail-closed tests use real local git repos.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from phase_loop_runtime.train_ledger import read_ledger
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import (
    CoordinatorRuntime,
    _check_branch_ahead_of_base,
    _prebuilt_owned_paths,
    run_train,
)


# ---------------------------------------------------------------------------
# Fixtures

PREBUILT_1NODE_MD = """\
# Release Train: prebuilt-single

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
**Mode:** prebuilt
"""

PREBUILT_3NODE_MD = """\
# Release Train: prebuilt-three

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
**Mode:** prebuilt

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
**Mode:** prebuilt

### Node: repo-c / specs/plan-c.md

**Depends on:** repo-b / specs/plan-b.md
**Channel:** submodule path=vendor/repo-b
**Mode:** prebuilt
"""


def _preflight_pass(nodes, resolve_workspace):
    return []


def _pr_is_open_false(workspace: Path, branch: str) -> bool:
    return False


def _make_prebuilt_publish_stub(recorder: Optional[dict] = None):
    """Publish stub that records the prebuilt flag + owned_paths + broker kwargs."""
    def _publish(workspace: Path, owned_paths, *, draft: bool, prebuilt: bool = False, **kw):
        assert draft is True, "prebuilt publishes must still be draft"
        if recorder is not None:
            recorder[workspace.name] = {
                "prebuilt": prebuilt,
                "owned_paths": list(owned_paths),
                "broker_client": kw.get("broker_client"),
                "admission": kw.get("admission"),
            }
        return {
            "status": "published",
            "branch": f"feat/train-{workspace.name}",
            "head_sha": f"sha-COMMITTED-{workspace.name}",
            "pr_url": f"https://gh.com/{workspace.name}/pr/1",
        }
    return _publish


def _make_runtime(broker_client: object) -> CoordinatorRuntime:
    return CoordinatorRuntime(
        train_id="train-prebuilt",
        coordinator_root=Path("/coord"),
        roadmap_path="train.md",
        roadmap_digest="deadbeef",
        workspace_id="ws-1",
        broker_client=broker_client,
    )


# ---------------------------------------------------------------------------
# 1. Prebuilt node skips run_loop and publishes with prebuilt=True


class TestPrebuiltSkipsRunLoop:
    def test_run_loop_not_called_publish_is_prebuilt(self, tmp_path: Path):
        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_loop_calls: List[str] = []
        published: dict = {}

        def _run_loop_spy(*a, **kw):
            run_loop_calls.append("called")
            return (None, [])

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_spy,
            _publish=_make_prebuilt_publish_stub(published),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _prebuilt_owned_paths_fn=lambda ws, base: ["src/committed.py", "CHANGELOG.md"],
        )

        assert result["status"] == "completed"
        # ZERO executor dispatch — run_loop must never be called for prebuilt.
        assert run_loop_calls == [], (
            f"prebuilt node must NOT invoke run_loop; got {run_loop_calls}"
        )
        # Publish called with prebuilt=True and the committed-diff owned_paths.
        assert published["repo-a"]["prebuilt"] is True
        assert published["repo-a"]["owned_paths"] == ["src/committed.py", "CHANGELOG.md"]

    def test_ledger_records_committed_head(self, tmp_path: Path):
        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_prebuilt_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _prebuilt_owned_paths_fn=lambda ws, base: ["src/x.py"],
        )

        state = read_ledger(ledger)
        rec = state["repo-a/specs/plan-a.md"]
        assert rec.status == "pr_open"
        assert rec.branch == "feat/train-repo-a"
        assert rec.head_sha == "sha-COMMITTED-repo-a"  # the committed HEAD
        assert rec.upstream_merge_sha is None

    def test_explicit_owned_paths_override_diff(self, tmp_path: Path):
        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        published: dict = {}

        # If an explicit resolver is supplied, the committed-diff seam is bypassed.
        def _diff_should_not_run(ws, base):
            raise AssertionError("prebuilt_owned_paths_fn must not run when resolver given")

        run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            resolve_owned_paths=lambda n: ["explicit/only.py"],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_prebuilt_publish_stub(published),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _prebuilt_owned_paths_fn=_diff_should_not_run,
        )

        assert published["repo-a"]["owned_paths"] == ["explicit/only.py"]


# ---------------------------------------------------------------------------
# 2. Prebuilt publish routes through the broker (the adaptation vs the parked ref)


class TestPrebuiltBrokerRouting:
    def test_prebuilt_publish_passes_the_runtimes_broker_client(self, tmp_path: Path):
        """A broker-authoritative runtime → publish_fn receives broker_client+admission."""
        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        published: dict = {}

        sentinel_broker = object()
        runtime = _make_runtime(sentinel_broker)
        admission_sentinel = object()

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            coordinator_runtime=runtime,
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_prebuilt_publish_stub(published),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _admission_fn=lambda rt, node, ws, owned: admission_sentinel,
            _prebuilt_owned_paths_fn=lambda ws, base: ["src/x.py"],
        )

        assert result["status"] == "completed"
        rec = published["repo-a"]
        assert rec["prebuilt"] is True
        # The publish mutation is routed through the broker: exact runtime client.
        assert rec["broker_client"] is sentinel_broker
        assert rec["admission"] is admission_sentinel

    def test_prebuilt_without_broker_fails_closed_broker_required(self, tmp_path: Path):
        """No broker_client → the REAL publish primitive returns broker_required.

        Uses the live ``publish_from_worktree`` (no _publish stub) against a real
        prebuilt branch with NO coordinator_runtime, so publish gets no
        broker_client → publication_blocked/broker_required → node blocked.  A
        prebuilt node must NEVER fall back to a direct push.
        """
        repo = _make_repo_with_origin(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        (repo / "feature.py").write_text("# work\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-q", "-m", "prebuilt work")

        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        with (
            patch("phase_loop_runtime.train_runner._check_gh_auth", return_value=None),
            patch("phase_loop_runtime.train_runner._check_remote_reachable", return_value=None),
        ):
            result = run_train(
                roadmap,
                ledger,
                run_mode="autonomous",
                resolve_workspace=lambda n: repo,
                _pr_is_open=_pr_is_open_false,
                _live_pr_head_sha_fn=lambda ws, br: None,
                # real _default_preflight (clean+ahead+base) + real publish_from_worktree
            )

        assert result["status"] == "blocked", result
        detail = result["detail"]
        assert detail.get("status") == "publication_blocked"
        assert detail.get("reason") == "broker_required"
        # Nothing was pushed/merged; ledger records the block, not a pr_open.
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "blocked"


# ---------------------------------------------------------------------------
# 3. Full 3-node prebuilt train reaches drafts_open with zero executor dispatch


class TestPrebuiltFullTrain:
    def test_three_node_prebuilt_reaches_drafts_open(self, tmp_path: Path):
        roadmap = parse_train_roadmap(PREBUILT_3NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_loop_calls: List[str] = []
        published: dict = {}

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: run_loop_calls.append("called"),
            _publish=_make_prebuilt_publish_stub(published),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,  # P4 gate on; autonomous → drafts_open
            _prebuilt_owned_paths_fn=lambda ws, base: [f"src/{ws.name}.py"],
        )

        assert result["status"] == "drafts_open", result
        assert len(result["nodes"]) == 3
        assert run_loop_calls == [], "zero executor dispatch expected for prebuilt train"
        assert all(v["prebuilt"] for v in published.values())
        state = read_ledger(ledger)
        assert len([r for r in state.values() if r.status == "pr_open"]) == 3


# ---------------------------------------------------------------------------
# 4. Prebuilt + --governed is rejected up front (P4 out of scope) — INV-3 shape


class TestPrebuiltGovernedRejected:
    def test_governed_prebuilt_opens_zero_prs(self, tmp_path: Path):
        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        published: dict = {}

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_prebuilt_publish_stub(published),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _prebuilt_owned_paths_fn=lambda ws, base: ["src/x.py"],
        )

        assert result["status"] == "preflight_failed"
        assert any("prebuilt" in e for e in result["errors"])
        assert published == {}, "zero PRs must open when prebuilt+governed is rejected"


# ---------------------------------------------------------------------------
# 5. Prebuilt preflight + owned-paths detection against REAL git repos


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _make_repo_with_origin(tmp_path: Path) -> Path:
    """Clone-shaped repo: a bare 'origin' with a main branch, checked out locally."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "base.txt").write_text("base\n")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    return repo


class TestPrebuiltPreflightRealGit:
    def test_clean_and_ahead_passes(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        (repo / "feature.py").write_text("# work\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-q", "-m", "prebuilt work")

        assert _check_branch_ahead_of_base(repo, "repo/plan", "main") is None

    def test_clean_but_not_ahead_errors(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        # Fresh branch AT origin/main — clean but not ahead → nothing to publish.
        _git(repo, "checkout", "-q", "-b", "feat/empty")

        err = _check_branch_ahead_of_base(repo, "repo/plan", "main")
        assert err is not None
        assert "not" in err and "ahead" in err

    def test_owned_paths_from_committed_diff(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        (repo / "a.py").write_text("a\n")
        (repo / "b.py").write_text("b\n")
        _git(repo, "add", "a.py", "b.py")
        _git(repo, "commit", "-q", "-m", "two files")

        paths = _prebuilt_owned_paths(repo, "main")
        assert sorted(paths) == ["a.py", "b.py"]

    # --- agent-harness#250 (N1/N4): `-z --no-renames` against a REAL git repo, proving
    # the rename source is surfaced (not hidden behind the destination) and a legit
    # within-scope rename still lists both endpoints identically to the broker's own
    # `_branch_diff_paths` re-derivation (the two MUST agree byte-for-byte).
    def test_rename_of_a_file_surfaces_both_source_and_destination(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        (repo / "unowned").mkdir()
        (repo / "unowned" / "x.py").write_text("x\n")
        _git(repo, "add", "unowned/x.py")
        _git(repo, "commit", "-q", "-m", "add unowned file")
        _git(repo, "push", "-q", "origin", "main")
        _git(repo, "fetch", "-q", "origin")

        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        (repo / "owned").mkdir()
        _git(repo, "mv", "unowned/x.py", "owned/x.py")
        _git(repo, "commit", "-q", "-m", "move unowned file into owned/")

        paths = _prebuilt_owned_paths(repo, "main")
        # Both the destination AND the source must be present — a plain `--name-only`
        # (rename-detecting) diff would report ONLY "owned/x.py", hiding the unowned
        # source from any downstream coverage check.
        assert sorted(paths) == ["owned/x.py", "unowned/x.py"]

    def test_rename_within_the_same_owned_directory_lists_both_endpoints(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        (repo / "owned").mkdir()
        (repo / "owned" / "old.py").write_text("x\n")
        _git(repo, "add", "owned/old.py")
        _git(repo, "commit", "-q", "-m", "add owned file")
        _git(repo, "push", "-q", "origin", "main")
        _git(repo, "fetch", "-q", "origin")

        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        _git(repo, "mv", "owned/old.py", "owned/new.py")
        _git(repo, "commit", "-q", "-m", "rename within owned/")

        paths = _prebuilt_owned_paths(repo, "main")
        # A within-scope rename lists BOTH endpoints (delete + add), not just the dest —
        # matching the broker side so a directory-owned entry covers both and the
        # coordinator never derives a scope the broker would then false-reject.
        assert sorted(paths) == ["owned/new.py", "owned/old.py"]


# ---------------------------------------------------------------------------
# 6. Dirty prebuilt workspace fails preflight (via the default preflight path)


class TestPrebuiltDirtyPreflight:
    def test_dirty_prebuilt_workspace_fails(self, tmp_path: Path):
        repo = _make_repo_with_origin(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feat/prebuilt")
        (repo / "committed.py").write_text("x\n")
        _git(repo, "add", "committed.py")
        _git(repo, "commit", "-q", "-m", "committed")
        # Now dirty the tree (uncommitted change).
        (repo / "uncommitted.py").write_text("dirty\n")

        roadmap = parse_train_roadmap(PREBUILT_1NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_calls: List[str] = []

        with (
            patch("phase_loop_runtime.train_runner._check_gh_auth", return_value=None),
            patch("phase_loop_runtime.train_runner._check_remote_reachable", return_value=None),
        ):
            result = run_train(
                roadmap,
                ledger,
                run_mode="autonomous",
                resolve_workspace=lambda n: repo,
                _run_loop=lambda *a, **kw: (None, []),
                _publish=lambda *a, **kw: publish_calls.append("x"),
                # real _default_preflight (clean + ahead checks run against repo)
            )

        assert result["status"] == "preflight_failed"
        assert any("uncommitted" in e.lower() for e in result["errors"])
        assert publish_calls == []


def test_prebuilt_owned_paths_fails_closed_on_diff_error(tmp_path):
    """CR fix: a git-diff error must RAISE (fail-closed), not return [] — an empty
    owned-paths scope would let the broker admission approve nothing while the push
    publishes the real branch (approved-nothing / published-something mismatch)."""
    import subprocess as sp
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"]}
    sp.run(["git", "init", "-q", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "x"], check=True, env=env)
    # No 'origin/main' ref → `git diff origin/main...HEAD` fails → must raise, never [].
    with pytest.raises(RuntimeError):
        _prebuilt_owned_paths(tmp_path)
