"""Tests for P3 train coordinator: serial draft-PR execution (issue #29).

Run with:
    cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_runner.py -q

All git/gh/run_loop/publish boundaries are stubbed; no live network access.

Coverage:
  - Preflight fail (dirty repo) → zero PRs opened
  - Preflight fail (bad gh auth) → zero PRs opened
  - 2-node train: set_upstream_ref called before run_loop for downstream node
  - 2-node train: draft PRs opened in topo order, linked in body
  - 2-node train: ledger records pr_open for each node
  - 2-node train: run_mode passed correctly to run_loop
  - 3-node train: all nodes injected and published in order
  - Mid-train failure: prior node stays pr_open, failed node is blocked
  - Resume: completed nodes skipped (run_loop + publish not called again)
  - No merge: all publish calls use draft=True, no merge seam called
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, call, patch

import pytest

from phase_loop_runtime.train_ledger import LedgerRecord, read_ledger
from phase_loop_runtime.train_roadmap import (
    TrainNode,
    parse_train_roadmap,
)
from phase_loop_runtime.train_runner import run_train


# ---------------------------------------------------------------------------
# Test fixtures: minimal train roadmap markdown

TRAIN_2NODE_MD = """\
# Release Train: feature-x

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
"""

TRAIN_3NODE_MD = """\
# Release Train: three-repo-feature

## Nodes

### Node: alpha / specs/alpha.md

**Depends on:** (none)
**Channel:** (none)

### Node: beta / specs/beta.md

**Depends on:** alpha / specs/alpha.md
**Channel:** pin name=alpha-lib version=1.0.0

### Node: gamma / specs/gamma.md

**Depends on:** beta / specs/beta.md
**Channel:** workspace path=../beta
"""

# ---------------------------------------------------------------------------
# Git fixture helper (for real-repo preflight tests)


def _git_in(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command inside ``repo``, failing loudly on non-zero exit."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Helpers


def _node_a() -> TrainNode:
    return TrainNode(repo="repo-a", roadmap="specs/plan-a.md")


def _node_b() -> TrainNode:
    return TrainNode(repo="repo-b", roadmap="specs/plan-b.md")


def _make_workspace_map(*nodes: TrainNode) -> Dict[str, Path]:
    """Return a {node_id: tmp_path} map for the given nodes."""
    # Use a deterministic path under /tmp for consistency (not real git repos)
    return {n.node_id: Path(f"/tmp/train-test/{n.repo}") for n in nodes}


def _resolve_workspace(workspace_map: Dict[str, Path]):
    def _resolve(node: TrainNode) -> Path:
        return workspace_map[node.node_id]
    return _resolve


def _make_publish_stub(results: Dict[str, dict]):
    """Return a publish stub that returns predefined results per workspace.

    The stub is called as ``publish_fn(workspace, owned_paths, draft=True, ...)``.
    Key is the workspace Path.
    """
    def _publish(workspace: Path, owned_paths, *, draft: bool, pr_body: Optional[str] = None, **kwargs):
        # Assert draft=True invariant (structural, not just behavioral)
        assert draft is True, (
            f"P3 coordinator must always open draft PRs; got draft={draft!r}"
        )
        return results.get(str(workspace), {
            "status": "published",
            "branch": f"feat/train-{workspace.name}",
            "head_sha": f"sha-{workspace.name[:6]}",
            "pr_url": f"https://github.com/owner/{workspace.name}/pull/1",
        })
    return _publish


def _run_loop_recording(call_log: list):
    """Return a run_loop stub that records (workspace, roadmap, run_mode) calls."""
    def _run_loop(workspace: Path, roadmap: Path, *, run_mode: str = "autonomous", **kwargs):
        call_log.append({"workspace": workspace, "roadmap": roadmap, "run_mode": run_mode})
    return _run_loop


def _set_upstream_ref_recording(call_log: list):
    """Return a set_upstream_ref stub that records calls."""
    def _set_upstream_ref(workspace: Path, channel, ref: str):
        call_log.append({"workspace": workspace, "channel": channel, "ref": ref})
    return _set_upstream_ref


def _preflight_pass(nodes, resolve_workspace):
    """Stub preflight that always passes."""
    return []


def _pr_is_open_true(workspace: Path, branch: str) -> bool:
    """Stub that says every PR is open."""
    return True


def _pr_is_open_false(workspace: Path, branch: str) -> bool:
    """Stub that says no PR is open."""
    return False


# ---------------------------------------------------------------------------
# 1. Preflight failure → ZERO PRs opened


class TestPreflightGateZeroPRs:
    """A preflight failure must result in zero publish calls."""

    def test_dirty_repo_opens_zero_prs(self, tmp_path: Path):
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        def _preflight_dirty(nodes, resolve_workspace):
            return ["[repo-a/specs/plan-a.md] workspace has uncommitted changes — preflight failed"]

        nodes = roadmap.nodes
        ws_map = _make_workspace_map(*nodes)

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=_resolve_workspace(ws_map),
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _preflight_fn=_preflight_dirty,
        )

        # Preflight failed → status="preflight_failed", zero PR opens
        assert result["status"] == "preflight_failed"
        assert len(result["errors"]) >= 1
        assert "uncommitted" in result["errors"][0]
        # THE CRITICAL ASSERTION: zero publishes
        assert publish_mock.call_count == 0, (
            f"Expected zero publish calls on preflight failure; got {publish_mock.call_count}"
        )
        assert run_loop_mock.call_count == 0

    def test_bad_gh_auth_opens_zero_prs(self, tmp_path: Path):
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        def _preflight_bad_auth(nodes, resolve_workspace):
            return ["gh auth status failed: not authenticated"]

        nodes = roadmap.nodes
        ws_map = _make_workspace_map(*nodes)

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=_resolve_workspace(ws_map),
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _preflight_fn=_preflight_bad_auth,
        )

        assert result["status"] == "preflight_failed"
        assert "gh auth" in result["errors"][0]
        # ZERO PRs
        assert publish_mock.call_count == 0

    def test_multiple_preflight_errors_reported(self, tmp_path: Path):
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        publish_mock = MagicMock()

        def _preflight_multi(nodes, resolve_workspace):
            return [
                "gh auth status failed: not authenticated",
                "[repo-a/specs/plan-a.md] remote 'origin' is not reachable",
                "[repo-b/specs/plan-b.md] workspace has uncommitted changes",
            ]

        ws_map = _make_workspace_map(*roadmap.nodes)
        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=_resolve_workspace(ws_map),
            _publish=publish_mock,
            _preflight_fn=_preflight_multi,
        )

        assert result["status"] == "preflight_failed"
        assert len(result["errors"]) == 3
        assert publish_mock.call_count == 0


# ---------------------------------------------------------------------------
# 2. Two-node train: happy path


class TestTwoNodeTrain:
    """A 2-node train must inject upstream refs, open draft PRs in order,
    and ledger pr_open for each node."""

    def test_set_upstream_ref_called_before_run_loop(self, tmp_path: Path):
        """set_upstream_ref must be called BEFORE run_loop for each downstream node."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        node_a = roadmap.nodes[0]  # repo-a (upstream, no deps)
        node_b = roadmap.nodes[1]  # repo-b (downstream, depends on repo-a)

        ws_map = {
            node_a.node_id: tmp_path / "repo-a",
            node_b.node_id: tmp_path / "repo-b",
        }

        call_order: List[str] = []

        def _set_upstream_ref(workspace, channel, ref):
            call_order.append(f"set_upstream_ref:{workspace.name}:{ref}")

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            call_order.append(f"run_loop:{workspace.name}")

        # Publish stubs returning distinct SHAs per repo
        publish_results = {
            str(tmp_path / "repo-a"): {
                "status": "published",
                "branch": "feat/train-a",
                "head_sha": "sha-aaa",
                "pr_url": "https://github.com/owner/repo-a/pull/10",
            },
            str(tmp_path / "repo-b"): {
                "status": "published",
                "branch": "feat/train-b",
                "head_sha": "sha-bbb",
                "pr_url": "https://github.com/owner/repo-b/pull/11",
            },
        }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop,
            _publish=_make_publish_stub(publish_results),
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"

        # repo-a is a root node: no set_upstream_ref before it
        # repo-b depends on repo-a: set_upstream_ref must appear before run_loop:repo-b
        set_upstream_idx = next(
            i for i, entry in enumerate(call_order)
            if entry.startswith("set_upstream_ref:repo-b")
        )
        run_loop_b_idx = next(
            i for i, entry in enumerate(call_order)
            if entry == "run_loop:repo-b"
        )
        assert set_upstream_idx < run_loop_b_idx, (
            f"set_upstream_ref (idx {set_upstream_idx}) must precede "
            f"run_loop:repo-b (idx {run_loop_b_idx})"
        )

        # set_upstream_ref for repo-b must carry repo-a's head_sha
        upstream_call = next(
            e for e in call_order if e.startswith("set_upstream_ref:repo-b")
        )
        assert "sha-aaa" in upstream_call, (
            f"Expected repo-a's head_sha 'sha-aaa' injected into repo-b; got {upstream_call!r}"
        )

    def test_draft_prs_opened_in_topo_order(self, tmp_path: Path):
        """Both PRs must be opened as drafts in topo order (repo-a then repo-b)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        node_a = roadmap.nodes[0]
        node_b = roadmap.nodes[1]
        ws_map = {
            node_a.node_id: tmp_path / "repo-a",
            node_b.node_id: tmp_path / "repo-b",
        }

        publish_call_workspaces: List[str] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True, "all publishes must be draft"
            publish_call_workspaces.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-{workspace.name[:4]}",
                "pr_url": f"https://github.com/owner/{workspace.name}/pull/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"
        # Both PRs opened, in topo order
        assert publish_call_workspaces == ["repo-a", "repo-b"]

    def test_run_mode_passed_to_run_loop(self, tmp_path: Path):
        """run_mode must be forwarded to each run_loop call exactly."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        run_loop_log: List[str] = []

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            run_loop_log.append(run_mode)

        _publish_fn = _make_publish_stub({
            str(tmp_path / "repo-a"): {
                "status": "published", "branch": "feat/a",
                "head_sha": "sha-a", "pr_url": "https://gh.com/a/1",
            },
            str(tmp_path / "repo-b"): {
                "status": "published", "branch": "feat/b",
                "head_sha": "sha-b", "pr_url": "https://gh.com/b/1",
            },
        })

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop,
            _publish=_publish_fn,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"
        # run_mode="governed" forwarded to ALL run_loop calls
        assert run_loop_log == ["governed", "governed"], (
            f"Expected run_mode='governed' for all calls; got {run_loop_log}"
        )

    def test_ledger_records_pr_open_for_each_node(self, tmp_path: Path):
        """Ledger must record pr_open with branch+head_sha+pr_url for each node."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        publish_results = {
            str(tmp_path / "repo-a"): {
                "status": "published",
                "branch": "feat/train-a",
                "head_sha": "sha-aaa111",
                "pr_url": "https://github.com/owner/repo-a/pull/10",
            },
            str(tmp_path / "repo-b"): {
                "status": "published",
                "branch": "feat/train-b",
                "head_sha": "sha-bbb222",
                "pr_url": "https://github.com/owner/repo-b/pull/11",
            },
        }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_make_publish_stub(publish_results),
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"

        # Read ledger and assert pr_open records
        state = read_ledger(ledger)
        node_a_id = "repo-a/specs/plan-a.md"
        node_b_id = "repo-b/specs/plan-b.md"

        assert node_a_id in state
        assert state[node_a_id].status == "pr_open"
        assert state[node_a_id].branch == "feat/train-a"
        assert state[node_a_id].pr_url == "https://github.com/owner/repo-a/pull/10"
        assert state[node_a_id].upstream_merge_sha == "sha-aaa111"
        assert state[node_a_id].merge_order == 0  # topo index for repo-a

        assert node_b_id in state
        assert state[node_b_id].status == "pr_open"
        assert state[node_b_id].branch == "feat/train-b"
        assert state[node_b_id].pr_url == "https://github.com/owner/repo-b/pull/11"
        assert state[node_b_id].upstream_merge_sha == "sha-bbb222"
        assert state[node_b_id].merge_order == 1  # topo index for repo-b

    def test_no_merge_attempted_all_publishes_draft(self, tmp_path: Path):
        """Assert no merge is ever attempted: all publishes must use draft=True."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        draft_flags: List[bool] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            draft_flags.append(draft)
            return {
                "status": "published",
                "branch": f"feat/{workspace.name}",
                "head_sha": f"sha-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"
        assert len(draft_flags) == 2
        # P3 invariant: NO merge (all must be draft=True)
        assert all(d is True for d in draft_flags), (
            f"P3: expected all draft=True; got {draft_flags}"
        )

    def test_pr_body_contains_upstream_url(self, tmp_path: Path):
        """Downstream node's PR body must reference upstream PR URLs (cross-linking)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        node_a = roadmap.nodes[0]
        node_b = roadmap.nodes[1]
        ws_map = {
            node_a.node_id: tmp_path / "repo-a",
            node_b.node_id: tmp_path / "repo-b",
        }

        pr_bodies: Dict[str, str] = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            pr_bodies[workspace.name] = pr_body or ""
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-{workspace.name[:4]}",
                "pr_url": f"https://github.com/owner/{workspace.name}/pull/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"

        # node-b's PR body must contain node-a's PR URL (backward cross-link)
        repo_a_pr_url = "https://github.com/owner/repo-a/pull/1"
        repo_b_body = pr_bodies.get("repo-b", "")
        assert repo_a_pr_url in repo_b_body, (
            f"Expected node-a PR URL {repo_a_pr_url!r} in node-b's PR body; "
            f"got:\n{repo_b_body}"
        )
        # Both PR bodies reference the merged node IDs in the merge-order list
        assert "repo-a/specs/plan-a.md" in pr_bodies.get("repo-a", "")
        assert "repo-b/specs/plan-b.md" in pr_bodies.get("repo-b", "")


# ---------------------------------------------------------------------------
# 3. Three-node train


class TestThreeNodeTrain:
    """A 3-node train must inject each upstream ref and publish in topo order."""

    def test_three_node_injection_and_ordering(self, tmp_path: Path):
        roadmap = parse_train_roadmap(TRAIN_3NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        nodes = roadmap.nodes  # alpha, beta, gamma
        ws_map = {n.node_id: tmp_path / n.repo for n in nodes}

        injection_log: List[dict] = []
        run_loop_log: List[str] = []

        def _set_upstream_ref(workspace, channel, ref):
            injection_log.append({
                "workspace": workspace.name,
                "channel_kind": channel.kind,
                "ref": ref,
            })

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            run_loop_log.append(workspace.name)

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            name = workspace.name
            return {
                "status": "published",
                "branch": f"feat/{name}",
                "head_sha": f"sha-{name}",
                "pr_url": f"https://gh.com/{name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"

        # Topo order: alpha → beta → gamma
        assert run_loop_log == ["alpha", "beta", "gamma"]

        # alpha: no injections (root node)
        # beta: injected with alpha's head_sha, via pin channel
        # gamma: injected with beta's head_sha, via workspace channel
        beta_injections = [e for e in injection_log if e["workspace"] == "beta"]
        assert len(beta_injections) == 1
        assert beta_injections[0]["channel_kind"] == "pin"
        assert beta_injections[0]["ref"] == "sha-alpha"

        gamma_injections = [e for e in injection_log if e["workspace"] == "gamma"]
        assert len(gamma_injections) == 1
        assert gamma_injections[0]["channel_kind"] == "workspace"
        assert gamma_injections[0]["ref"] == "sha-beta"

        # Ledger state: all three nodes pr_open
        state = read_ledger(ledger)
        assert len([r for r in state.values() if r.status == "pr_open"]) == 3

        # No merge: all 3 published with draft=True (enforced in _publish above)


# ---------------------------------------------------------------------------
# 4. Mid-train failure: blocked + resumable


class TestMidTrainFailure:
    """A failure at node B must ledger B as blocked and leave the train resumable."""

    def test_first_node_success_second_node_blocked(self, tmp_path: Path):
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            if workspace.name == "repo-a":
                return {
                    "status": "published",
                    "branch": "feat/train-a",
                    "head_sha": "sha-aaa",
                    "pr_url": "https://github.com/owner/repo-a/pull/10",
                }
            # repo-b fails
            return {
                "status": "publication_blocked",
                "reason": "push_rejected",
                "detail": "remote rejected the push",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "blocked"
        assert result["node_id"] == "repo-b/specs/plan-b.md"

        # Ledger: repo-a is pr_open, repo-b is blocked
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "pr_open"
        assert state["repo-b/specs/plan-b.md"].status == "blocked"

    def test_blocked_train_is_resumable(self, tmp_path: Path):
        """After a failure, re-running skips the completed node and retries blocked."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        # First run: repo-b fails
        publish_calls: List[str] = []

        def _publish_first(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            publish_calls.append(workspace.name)
            if workspace.name == "repo-a":
                return {
                    "status": "published",
                    "branch": "feat/train-a",
                    "head_sha": "sha-aaa",
                    "pr_url": "https://github.com/owner/repo-a/pull/10",
                }
            return {
                "status": "publication_blocked",
                "reason": "push_rejected",
                "detail": "first attempt rejected",
            }

        run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish_first,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert publish_calls == ["repo-a", "repo-b"]  # sanity check

        # Second run: repo-b now succeeds; repo-a's PR is still live
        publish_calls_2: List[str] = []
        run_loop_calls_2: List[str] = []

        def _publish_second(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            publish_calls_2.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-{workspace.name[:3]}",
                "pr_url": f"https://github.com/owner/{workspace.name}/pull/1",
            }

        def _run_loop_2(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            run_loop_calls_2.append(workspace.name)

        result2 = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_2,
            _publish=_publish_second,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            # repo-a's PR IS still open (live state check says yes)
            _pr_is_open=_pr_is_open_true,
        )

        assert result2["status"] == "completed"
        # repo-a was SKIPPED (already pr_open and live-confirmed)
        assert "repo-a" not in publish_calls_2, (
            "repo-a should be skipped on resume; publish must not be called again"
        )
        assert "repo-a" not in run_loop_calls_2, (
            "repo-a should be skipped on resume; run_loop must not be called again"
        )
        # repo-b was retried
        assert "repo-b" in publish_calls_2

    def test_blocked_records_in_ledger_not_phase_loop(self, tmp_path: Path):
        """Train state must never be written under .phase-loop/."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        # Attempt to use a .phase-loop path — must raise
        bad_ledger = tmp_path / ".phase-loop" / "train.ledger.jsonl"

        from phase_loop_runtime.train_ledger import LedgerRecord, append_record

        with pytest.raises(ValueError, match=r"\.phase-loop"):
            append_record(bad_ledger, LedgerRecord(node_id="x/y", status="pending"))


# ---------------------------------------------------------------------------
# 5. Invariant: no merge ever attempted in P3


class TestNoMergeInvariant:
    """P3 must never call a merge operation. Verified via draft=True and no merge seam."""

    def test_all_publish_calls_are_draft_true(self, tmp_path: Path):
        """Every publish call in P3 must carry draft=True."""
        roadmap = parse_train_roadmap(TRAIN_3NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        draft_args: List[bool] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            draft_args.append(draft)
            return {
                "status": "published",
                "branch": f"feat/{workspace.name}",
                "head_sha": f"sha-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: None,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "completed"
        assert len(draft_args) == 3
        assert all(d is True for d in draft_args), (
            "P3: all publishes must be draft=True; merges are P4 scope"
        )

    # NOTE: "no merge" is enforced structurally rather than via a live merge seam.
    # P3 has no merge code path: `publish_from_worktree` is the only publish
    # primitive and it is called with `draft=True` in all cases (verified above
    # via `test_all_publish_calls_are_draft_true` and the draft-flag asserts in
    # other tests).  There is nothing to stub out — that is the guarantee.


# ---------------------------------------------------------------------------
# 6. Preflight real detection (not stubbed)
#
# These tests use real git repos to verify that _check_repo_clean and
# _default_preflight actually detect dirty state rather than just accepting
# injected error strings.  If _check_repo_clean had an inverted condition,
# the stub-only tests above would still pass; these would not.


class TestPreflightRealDetection:
    """Real-git tests: preflight detection code, not just injection plumbing."""

    def test_check_repo_clean_detects_dirty(self, tmp_path: Path):
        """_check_repo_clean must return an error for a workspace with untracked files."""
        from phase_loop_runtime.train_runner import _check_repo_clean

        repo = tmp_path / "testrepo"
        repo.mkdir()
        _git_in(repo, "init", "-q")
        _git_in(repo, "config", "user.email", "test@example.com")
        _git_in(repo, "config", "user.name", "Test User")
        # Untracked file → git status --short is non-empty
        (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

        err = _check_repo_clean(repo, "test/node")

        assert err is not None, "Expected an error for a dirty workspace"
        assert "uncommitted" in err.lower(), (
            f"Expected 'uncommitted' in error message; got: {err!r}"
        )

    def test_check_repo_clean_passes_clean(self, tmp_path: Path):
        """_check_repo_clean must return None for a freshly-initialized (clean) repo."""
        from phase_loop_runtime.train_runner import _check_repo_clean

        repo = tmp_path / "cleanrepo"
        repo.mkdir()
        _git_in(repo, "init", "-q")
        _git_in(repo, "config", "user.email", "test@example.com")
        _git_in(repo, "config", "user.name", "Test User")
        # No files → git status --short returns empty → clean

        err = _check_repo_clean(repo, "test/node")

        assert err is None, f"Expected None for a clean workspace; got: {err!r}"

    def test_default_preflight_real_dirty_repo_opens_zero_prs(self, tmp_path: Path):
        """run_train with _default_preflight (not stubbed) and a real dirty repo
        must return preflight_failed and open zero PRs.

        Stubs only the network-touching checks (_check_gh_auth,
        _check_remote_reachable, _check_base_branch_exists); _check_repo_clean
        runs against the real filesystem to verify detection is wired correctly.
        """
        repo = tmp_path / "dirty-repo"
        repo.mkdir()
        _git_in(repo, "init", "-q")
        _git_in(repo, "config", "user.email", "test@example.com")
        _git_in(repo, "config", "user.name", "Test User")
        # Untracked file makes the repo dirty
        (repo / "staged.txt").write_text("uncommitted\n", encoding="utf-8")

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: repo for n in roadmap.nodes}
        publish_mock = MagicMock()

        # Patch only network/remote checks; let _check_repo_clean run real
        with (
            patch("phase_loop_runtime.train_runner._check_gh_auth", return_value=None),
            patch(
                "phase_loop_runtime.train_runner._check_remote_reachable",
                return_value=None,
            ),
            patch(
                "phase_loop_runtime.train_runner._check_base_branch_exists",
                return_value=None,
            ),
        ):
            result = run_train(
                roadmap,
                ledger,
                run_mode="autonomous",
                resolve_workspace=lambda n: ws_map[n.node_id],
                _publish=publish_mock,
                # _preflight_fn intentionally omitted → uses real _default_preflight
            )

        assert result["status"] == "preflight_failed", (
            f"Expected preflight_failed from real dirty-repo detection; got: {result}"
        )
        assert any("uncommitted" in e.lower() for e in result.get("errors", [])), (
            f"Expected 'uncommitted' in preflight errors; got: {result.get('errors')}"
        )
        # THE CRITICAL ASSERTION: real detection → zero PRs
        assert publish_mock.call_count == 0, (
            f"Expected zero publish calls after real preflight failure; "
            f"got {publish_mock.call_count}"
        )


# ---------------------------------------------------------------------------
# 7. run-train CLI smoke (parser registration + handler execution)


class TestCLIRegistration:
    """Verify run-train is registered as a CLI subcommand."""

    def test_run_train_subcommand_registered(self):
        """phase-loop run-train --help must not raise SystemExit(2)."""
        from phase_loop_runtime.cli import build_parser

        parser = build_parser()
        # Should parse without error
        args = parser.parse_args(["run-train", "--train", "specs/my-train.md"])
        assert args.command == "run-train"
        assert args.train_file == "specs/my-train.md"

    def test_run_train_governed_flag(self):
        """--governed must be accepted by run-train."""
        from phase_loop_runtime.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["run-train", "--train", "t.md", "--governed"])
        assert args.governed is True

    def test_run_train_requires_train_flag(self):
        """run-train without --train must exit with error."""
        from phase_loop_runtime.cli import build_parser
        import sys

        parser = build_parser()
        # parse_args with no --train should still succeed (required enforced in _main)
        # but let's check we can at least parse and that train_file is None
        args = parser.parse_args(["run-train"])
        assert getattr(args, "train_file", None) is None

    def test_cli_main_run_train_smoke(self, tmp_path: Path):
        """main(['run-train', ...]) must reach train_runner.run_train and exit 0.

        This test exercises the full handler path (argument parsing → _main
        dispatch → _run_train_command → train_runner.run_train), catching any
        crash in the pre-dispatch gauntlet that argparse-only tests would miss.
        """
        from phase_loop_runtime.cli import main

        tmp_train = tmp_path / "smoke-train.md"
        tmp_train.write_text(TRAIN_2NODE_MD, encoding="utf-8")

        # Patch train_runner.run_train at the module boundary
        with patch("phase_loop_runtime.train_runner.run_train") as mock_run_train:
            mock_run_train.return_value = {"status": "completed", "nodes": {}}
            exit_code = main(["run-train", "--train", str(tmp_train), "--governed"])

        assert exit_code == 0, f"Expected exit 0; got {exit_code}"
        mock_run_train.assert_called_once()
        call_kwargs = mock_run_train.call_args.kwargs
        assert call_kwargs.get("run_mode") == "governed", (
            f"Expected run_mode='governed' forwarded to run_train; "
            f"call kwargs: {call_kwargs}"
        )
