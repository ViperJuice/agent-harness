"""Tests for P3 train coordinator: serial draft-PR execution (issue #29).

Run with:
    cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_runner.py -q

All git/gh/run_loop/publish boundaries are stubbed; no live network access.

Coverage:
  - Preflight fail (dirty repo) → zero PRs opened
  - Preflight fail (bad gh auth) → zero PRs opened
  - 2-node train: set_upstream_ref called before run_loop for downstream node
  - 2-node train: draft PRs opened in topo order, linked in body
  - 2-node train: ledger records pr_open for each node (head_sha, not upstream_merge_sha)
  - 2-node train: run_mode passed correctly to run_loop
  - 3-node train: all nodes injected and published in order
  - Mid-train failure: prior node stays pr_open, failed node is blocked
  - Resume: completed nodes skipped (run_loop + publish not called again)
  - No merge: all publish calls use draft=True, no merge seam called

  CR-panel real-seam tests (CR findings; these would FAIL against the pre-fix code):
  8.  run_loop snapshot paths used (Finding #1): run_loop returning a StateSnapshot-like
      object with phase_owned_dirty_paths → those EXACT paths passed to publish
  9.  Upstream ref missing → fail-loud block (Finding #2b): upstream not in
      completed_nodes → blocked, run_loop NOT called
  10. Inject exception → blocked (Finding #2/3): set_upstream_ref raises →
      ledger blocked, run not a traceback
  11. run_loop exception → blocked (Finding #3): run_loop raises → ledger blocked
  12. Malformed train → zero PRs (Finding #3): none-channel dependency →
      validate_train_loud → preflight_failed → zero PRs
  13. Resume uses live head SHA (Finding #4/5): stale ledger SHA overridden by
      live PR head SHA for injection
  14. Downstream rebuilt when upstream rebuilt this run (Finding #4): upstream
      PR closed, rebuilt; downstream (previously pr_open) also rebuilt
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
**Channel:** pin file=manifest.json key=deps.alpha-lib

### Node: gamma / specs/gamma.md

**Depends on:** beta / specs/beta.md
**Channel:** submodule path=vendor/beta
"""

# A train with an unsupported workspace-channel edge (for T-E / preflight tests)
TRAIN_WORKSPACE_MD = """\
# Release Train: workspace-train

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** workspace path=../repo-a
"""

# A 2-node train with a pin-channel edge (for real-seam pin tests)
TRAIN_PIN_MD = """\
# Release Train: pin-feature

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** pin file=manifest.json key=deps.repo-a
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
        # Draft head SHA is in `head_sha`; upstream_merge_sha is reserved for P4 merged SHA
        assert state[node_a_id].head_sha == "sha-aaa111"
        assert state[node_a_id].upstream_merge_sha is None
        assert state[node_a_id].merge_order == 0  # topo index for repo-a

        assert node_b_id in state
        assert state[node_b_id].status == "pr_open"
        assert state[node_b_id].branch == "feat/train-b"
        assert state[node_b_id].pr_url == "https://github.com/owner/repo-b/pull/11"
        assert state[node_b_id].head_sha == "sha-bbb222"
        assert state[node_b_id].upstream_merge_sha is None
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
        assert gamma_injections[0]["channel_kind"] == "submodule"
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

    def test_run_train_workspace_override_flag_repeatable(self):
        """--workspace repo=PATH is repeatable and collected as a list."""
        from phase_loop_runtime.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "run-train", "--train", "t.md",
            "--workspace", "svc-a=/mnt/vol/svc-a",
            "--workspace", "svc-b=/other/svc-b",
        ])
        assert args.workspace_overrides == ["svc-a=/mnt/vol/svc-a", "svc-b=/other/svc-b"]

    def test_cli_workspace_override_resolves_arbitrary_path(self, tmp_path: Path):
        """The CLI resolves a node to the --workspace override / **Workspace:** attr.

        Precedence: --workspace flag > node.workspace attribute > <root>/<repo>.
        We patch run_train to capture the resolve_workspace callable the CLI
        builds, then exercise its precedence directly.
        """
        from phase_loop_runtime.cli import main
        from phase_loop_runtime.train_roadmap import TrainNode

        train_md = (
            "# Release Train: ws-override\n\n## Nodes\n\n"
            "### Node: svc-a / specs/a.md\n\n"
            "**Depends on:** (none)\n**Channel:** (none)\n\n"
            "### Node: svc-b / specs/b.md\n\n"
            "**Depends on:** (none)\n**Channel:** (none)\n"
            "**Workspace:** /attr/svc-b\n\n"
            "### Node: svc-c / specs/c.md\n\n"
            "**Depends on:** (none)\n**Channel:** (none)\n"
        )
        train_file = tmp_path / "ws-train.md"
        train_file.write_text(train_md, encoding="utf-8")

        captured: dict = {}

        def _capture(roadmap, ledger_path, **kwargs):
            captured["resolve"] = kwargs["resolve_workspace"]
            return {"status": "completed", "nodes": {}}

        with patch("phase_loop_runtime.train_runner.run_train", side_effect=_capture):
            main([
                "run-train", "--train", str(train_file),
                "--workspace-root", "/root",
                "--workspace", "svc-a=/mnt/vol/svc-a",
                "--ledger-dir", str(tmp_path / "ledger"),
            ])

        resolve = captured["resolve"]
        # 1. --workspace flag wins.
        assert resolve(TrainNode(repo="svc-a", roadmap="specs/a.md")) == Path("/mnt/vol/svc-a")
        # 2. **Workspace:** attribute used when no flag override.
        assert resolve(TrainNode(repo="svc-b", roadmap="specs/b.md", workspace="/attr/svc-b")) == Path("/attr/svc-b")
        # 3. Default <workspace-root>/<repo> otherwise.
        assert resolve(TrainNode(repo="svc-c", roadmap="specs/c.md")) == Path("/root/svc-c")

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

    def test_cli_main_run_train_wires_a_routing_broker(self, tmp_path: Path):
        """run-train must pass a broker-authoritative coordinator_runtime (routing broker).

        Without a broker_client, publish_from_worktree fail-closes `broker_required`
        and the train opens ZERO PRs — the shipped-CLI gap the SPECPKGMIN PILOT
        surfaced (agent-harness#205/#206). The runtime must carry a routing broker
        (one client serving every repo) whose durable state lives OUTSIDE any repo.
        """
        from phase_loop_runtime.cli import main

        tmp_train = tmp_path / "smoke-train.md"
        tmp_train.write_text(TRAIN_2NODE_MD, encoding="utf-8")

        with patch("phase_loop_runtime.train_runner.run_train") as mock_run_train:
            mock_run_train.return_value = {"status": "drafts_open", "nodes": {}}
            exit_code = main(["run-train", "--train", str(tmp_train)])

        assert exit_code == 0, f"Expected exit 0; got {exit_code}"
        runtime = mock_run_train.call_args.kwargs.get("coordinator_runtime")
        assert runtime is not None, "run-train must pass a coordinator_runtime"
        assert runtime.broker_client is not None, (
            "coordinator_runtime must carry a broker_client, else publish is broker_required"
        )
        assert type(runtime.broker_client).__name__ == "_RoutingBrokerService", (
            "must be the per-request routing broker so ONE client serves a multi-repo train"
        )
        assert runtime.train_id and runtime.roadmap_digest
        # Durable broker state lives under the ledger dir, namespaced PER TRAIN so an
        # ambiguous outcome in one train can't fail-close a different train.
        assert Path(runtime.coordinator_root) == tmp_path / ".train-ledger" / "broker" / "smoke-train"


# ---------------------------------------------------------------------------
# 8. Finding #1: run_loop snapshot paths are used (real seam, not just called)
#
# Pre-fix: run_loop return was discarded; owned_paths defaulted to [node.roadmap].
# Now: when resolve_owned_paths is not provided, the coordinator uses
# StateSnapshot.phase_owned_dirty_paths from run_loop's return value.


class TestSnapshotPathsUsed:
    """run_loop's returned snapshot.phase_owned_dirty_paths are published (Finding #1)."""

    def test_snapshot_phase_owned_dirty_paths_passed_to_publish(self, tmp_path: Path):
        """The EXACT paths from snapshot.phase_owned_dirty_paths reach publish.

        Pre-fix: run_loop return discarded → owned_paths = [node.roadmap] (wrong).
        Post-fix: snapshot paths are used when resolve_owned_paths is not supplied.
        """
        from types import SimpleNamespace

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Fake StateSnapshot-like object with specific produced paths
        fake_snapshot = SimpleNamespace(
            phase_owned_dirty_paths=("src/feature.py", "src/schema.sql"),
            dirty_paths=("src/feature.py", "src/schema.sql", "specs/plan-a.md"),
        )

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            return (fake_snapshot, [])  # real run_loop returns (StateSnapshot, list)

        published_owned_paths: dict = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            published_owned_paths[workspace.name] = list(owned_paths)
            return {
                "status": "published",
                "branch": f"feat/{workspace.name}",
                "head_sha": f"sha-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            # resolve_owned_paths intentionally NOT supplied → must use snapshot
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed"

        # THE CRITICAL ASSERTION: snapshot paths (not [node.roadmap]) reach publish
        for repo_name, paths in published_owned_paths.items():
            assert "src/feature.py" in paths, (
                f"Expected snapshot path 'src/feature.py' in publish call for "
                f"'{repo_name}'; got {paths!r}"
            )
            assert "src/schema.sql" in paths, (
                f"Expected snapshot path 'src/schema.sql' in publish call for "
                f"'{repo_name}'; got {paths!r}"
            )
            # roadmap-only path must NOT be the sole content
            assert paths != ["specs/plan-a.md"], (
                f"Roadmap-only path set — run_loop return was discarded (pre-fix bug): "
                f"{paths!r}"
            )

    def test_explicit_resolve_owned_paths_overrides_snapshot(self, tmp_path: Path):
        """When resolve_owned_paths is explicitly provided, it takes precedence."""
        from types import SimpleNamespace

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        fake_snapshot = SimpleNamespace(
            phase_owned_dirty_paths=("snapshot/path.py",),
            dirty_paths=("snapshot/path.py",),
        )

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            return (fake_snapshot, [])

        published_owned_paths: dict = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            published_owned_paths[workspace.name] = list(owned_paths)
            return {
                "status": "published",
                "branch": f"feat/{workspace.name}",
                "head_sha": f"sha-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            # Explicit resolver — overrides snapshot
            resolve_owned_paths=lambda n: ["explicit/override.py"],
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed"
        for paths in published_owned_paths.values():
            assert paths == ["explicit/override.py"], (
                f"Expected explicit override paths; got {paths!r}"
            )


# ---------------------------------------------------------------------------
# 9. Finding #2b: upstream ref missing → fail-loud block (no silent skip)
#
# Pre-fix: coordinator silently skipped set_upstream_ref and ran run_loop
# against the absent upstream (building against the wrong upstream).
# Post-fix (Finding #3): when set_upstream_ref_fn raises (for ANY reason —
# including the real executor raising UnsupportedChannelKind or the defensive
# upstream_result guard), the exception is caught → blocked record, no run_loop.
#
# Note: the `upstream_result is None` structural guard in train_runner.py is a
# defensive invariant that is structurally unreachable in a well-formed train
# (topo-sort + T-B validation guarantee the upstream is in completed_nodes by
# the time we process the downstream).  The tests below exercise the exception-
# safety path (Finding #3) by making set_upstream_ref_fn raise directly — they
# are NOT tests of the `upstream_result is None` branch itself.


class TestUpstreamInjectExceptionBlocksNode:
    """When set_upstream_ref_fn raises, the downstream node is blocked (Finding #3).

    These tests exercise the exception-safety path: any error during injection
    produces a blocked ledger record and suppresses run_loop for that node.
    """

    def test_inject_exception_blocks_node_and_suppresses_run_loop(self, tmp_path: Path):
        """set_upstream_ref raising → downstream is blocked, run_loop not called.

        Pre-fix (Finding #3): uncaught exceptions left node stuck at 'running'.
        Post-fix: exception → blocked record, run_loop never called for that node.

        Note: the mechanism exercised here is the try/except around inject+run_loop,
        not the structural `upstream_result is None` guard (which is unreachable in
        normal flow — see class docstring).
        """
        # Use a 2-node train where the upstream (repo-a) will NOT be in
        # completed_nodes (its PR is 'open' per ledger but pr_is_open returns
        # False → it's excluded from completed_nodes).  repo-b then has no
        # upstream ref to inject.
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        run_loop_calls: List[str] = []

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            run_loop_calls.append(workspace.name)
            return (None, [])

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            # repo-a publishes successfully
            if workspace.name == "repo-a":
                return {
                    "status": "published",
                    "branch": "feat/train-a",
                    "head_sha": "sha-aaa",
                    "pr_url": "https://github.com/owner/repo-a/pull/10",
                }
            # Should not reach repo-b publish
            raise AssertionError("publish called for repo-b but upstream was unresolved")

        # Simulate: upstream (repo-a) built in this run (run_loop was called, publish
        # succeeded), but then completed_nodes is patched empty for the downstream step.
        # Easier approach: make the preflight fail so repo-a's run_loop runs but
        # upstream_result lookup fails.  Actually simplest: stub set_upstream_ref to raise.
        def _set_upstream_ref_raises(workspace, channel, ref):
            raise RuntimeError(
                f"upstream ref for '{channel}' is not resolved — cannot inject"
            )

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=_set_upstream_ref_raises,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        # repo-a succeeded (no upstream deps to inject); repo-b blocked by inject failure
        assert result["status"] == "blocked"
        assert result["node_id"] == "repo-b/specs/plan-b.md"

        # repo-b's run_loop must NOT have been called
        assert "repo-b" not in run_loop_calls, (
            f"run_loop was called for repo-b despite upstream ref injection failure; "
            f"pre-fix bug: building against absent upstream. calls={run_loop_calls}"
        )

        # Ledger: repo-b is blocked
        from phase_loop_runtime.train_ledger import read_ledger
        state = read_ledger(ledger)
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            f"Expected repo-b blocked in ledger; got {state.get('repo-b/specs/plan-b.md')}"
        )

    def test_inject_exception_blocks_before_pr_open(self, tmp_path: Path):
        """Injection exception → downstream PR never opened, node blocked.

        Variant that confirms repo-a's PR is opened but repo-b's is not —
        the block happens before publish for repo-b.

        Pre-fix (Finding #3): exception during inject could leak a half-open PR.
        Post-fix: exception → blocked record appended before publish is attempted.
        """
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        publish_calls: List[str] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            publish_calls.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/{workspace.name}",
                "head_sha": f"sha-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        # Raise RuntimeError to simulate the "upstream ref unresolved" check
        def _inject_raises_for_b(workspace, channel, ref):
            if workspace.name == "repo-b":
                raise RuntimeError("upstream ref for 'repo-a/...' is not resolved")

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish,
            _set_upstream_ref_fn=_inject_raises_for_b,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "blocked"
        # repo-a published; repo-b was blocked at inject (before publish)
        assert "repo-a" in publish_calls
        assert "repo-b" not in publish_calls, (
            "repo-b's PR must not be opened when upstream injection fails"
        )


# ---------------------------------------------------------------------------
# 10 + 11. Finding #3: exceptions → blocked (never stuck at "running")
#
# Pre-fix: uncaught exceptions during inject/run_loop left the node at status
# "running" in the ledger (breadcrumb written, no blocked record added).
# Post-fix: any exception in the inject+run_loop+publish block → blocked record.


class TestExceptionBlocksNode:
    """inject or run_loop exception → ledger blocked, not a propagating traceback."""

    def test_inject_exception_becomes_blocked(self, tmp_path: Path):
        """set_upstream_ref raising → node is blocked in ledger (not stuck at running)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        def _inject_raises(workspace, channel, ref):
            raise RuntimeError("simulated inject failure: unsupported channel kind")

        publish_calls: List[str] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            publish_calls.append(workspace.name)
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
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish,
            _set_upstream_ref_fn=_inject_raises,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "blocked"
        assert result["node_id"] == "repo-b/specs/plan-b.md"
        assert "simulated inject failure" in result["detail"]["reason"]

        # Ledger must not leave repo-b stuck at "running"
        from phase_loop_runtime.train_ledger import read_ledger
        state = read_ledger(ledger)
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            f"Expected blocked in ledger; got {state.get('repo-b/specs/plan-b.md')}"
        )
        # repo-a must be pr_open (it succeeded before the inject failure)
        assert state["repo-a/specs/plan-a.md"].status == "pr_open"
        # repo-b's PR was NOT opened (inject failed before publish)
        assert "repo-b" not in publish_calls

    def test_run_loop_exception_becomes_blocked(self, tmp_path: Path):
        """run_loop raising → node is blocked in ledger, not a propagating traceback."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        def _run_loop_raises_for_b(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            if workspace.name == "repo-b":
                raise RuntimeError("simulated run_loop failure mid-node")
            return (None, [])

        publish_calls: List[str] = []

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            publish_calls.append(workspace.name)
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
            _run_loop=_run_loop_raises_for_b,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "blocked"
        assert result["node_id"] == "repo-b/specs/plan-b.md"
        assert "run_loop failure" in result["detail"]["reason"]

        from phase_loop_runtime.train_ledger import read_ledger
        state = read_ledger(ledger)
        assert state["repo-b/specs/plan-b.md"].status == "blocked"
        # Repo-b's PR was never opened
        assert "repo-b" not in publish_calls


# ---------------------------------------------------------------------------
# 12. Finding #3: malformed train → zero PRs (validate_train_loud in preflight)
#
# Pre-fix: no pre-flight validation; a none-channel dependency edge would
# open partial PRs then fail at inject time.
# Post-fix: validate_train_loud is called before any PR is opened.


# A train where downstream has a dependency edge with channel=(none) — invalid
TRAIN_NONE_CHANNEL_MD = """\
# Release Train: bad-channel

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** (none)
"""


class TestMalformedTrainZeroPRs:
    """A train with a none-channel dependency edge opens zero PRs (Finding #3)."""

    def test_none_channel_dependency_opens_zero_prs(self, tmp_path: Path):
        """validate_train_loud catches none-channel dependency → zero PRs opened.

        Pre-fix: validation was absent; the train would attempt to inject and fail
        mid-run after opening repo-a's PR (partial draft train).
        Post-fix: validate_train_loud fires before any PR is opened.
        """
        from phase_loop_runtime.train_roadmap import parse_train_roadmap

        roadmap = parse_train_roadmap(TRAIN_NONE_CHANNEL_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "preflight_failed", (
            f"Expected preflight_failed for malformed train; got {result}"
        )
        assert any("(T-C)" in e for e in result.get("errors", [])), (
            f"Expected T-C validation error in output; got {result.get('errors')}"
        )
        # THE CRITICAL ASSERTION: zero PRs opened
        assert publish_mock.call_count == 0, (
            f"Expected zero publish calls for malformed train; "
            f"got {publish_mock.call_count}"
        )
        assert run_loop_mock.call_count == 0


# ---------------------------------------------------------------------------
# 13. Finding #4 + #5: resume uses live PR head SHA (not stale ledger SHA)
#
# Pre-fix: resume read rec.upstream_merge_sha (which held the draft head SHA
# — overloading the field); if the branch was force-pushed, the injected ref
# would be stale.
# Post-fix: resume reads rec.head_sha AND fetches live SHA via
# _live_pr_head_sha_fn; head_sha stored separately from upstream_merge_sha.


class TestResumeUsesLiveHeadSha:
    """Resume path prefers live PR head SHA over stale ledger SHA (Findings #4, #5)."""

    def test_live_sha_overrides_stale_ledger_sha(self, tmp_path: Path):
        """When live PR head SHA differs from ledger, live SHA is injected.

        Scenario:
          - First run: repo-a's PR was at sha-v1 (now in ledger).
          - External force-push: repo-a's branch is now at sha-v2.
          - Second run (resume): repo-a's PR is still open.
          - Downstream (repo-b) must be injected with sha-v2, not sha-v1.

        Pre-fix: injected sha was taken from rec.upstream_merge_sha (== sha-v1).
        Post-fix: live_pr_head_sha returns sha-v2 → downstream injects sha-v2.
        """
        from phase_loop_runtime.train_ledger import LedgerRecord, append_record

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Pre-populate ledger: repo-a is pr_open with stale head_sha
        append_record(
            ledger,
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md",
                status="pr_open",
                branch="feat/train-a",
                pr_url="https://github.com/owner/repo-a/pull/10",
                head_sha="sha-v1-stale",  # stale — branch was force-pushed since
                upstream_merge_sha=None,
                merge_order=0,
            ),
        )

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        injected_refs: dict = {}

        def _set_upstream_ref(workspace, channel, ref):
            injected_refs[workspace.name] = ref

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-{workspace.name}-new",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish,
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            # repo-a's PR is still open
            _pr_is_open=_pr_is_open_true,
            # Live query returns the NEW SHA (force-pushed)
            _live_pr_head_sha_fn=lambda ws, br: "sha-v2-live" if br == "feat/train-a" else None,
        )

        assert result["status"] == "completed"

        # repo-a was skipped (already pr_open); repo-b was built with injected ref
        assert "repo-b" in injected_refs, (
            "repo-b must have been injected with upstream ref during second run"
        )
        injected = injected_refs["repo-b"]
        assert injected == "sha-v2-live", (
            f"Expected live SHA 'sha-v2-live' injected into repo-b; "
            f"got {injected!r} (stale ledger SHA would be 'sha-v1-stale')"
        )

    def test_head_sha_stored_in_head_sha_field_not_upstream_merge_sha(self, tmp_path: Path):
        """Draft head SHA is stored in head_sha, not upstream_merge_sha (Finding #5).

        upstream_merge_sha is reserved for P4's merged-commit SHA.  Mixing them
        causes P4 to read a draft head SHA and falsely conclude the upstream merged.

        Pre-fix: train_runner stored head_sha in the upstream_merge_sha field.
        Post-fix: head_sha → head_sha field, upstream_merge_sha is None for pr_open.
        """
        from phase_loop_runtime.train_ledger import read_ledger

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({
                str(tmp_path / "repo-a"): {
                    "status": "published",
                    "branch": "feat/train-a",
                    "head_sha": "sha-draft-a",
                    "pr_url": "https://gh.com/a/1",
                },
                str(tmp_path / "repo-b"): {
                    "status": "published",
                    "branch": "feat/train-b",
                    "head_sha": "sha-draft-b",
                    "pr_url": "https://gh.com/b/1",
                },
            }),
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed"

        state = read_ledger(ledger)
        for node_id, rec in state.items():
            if rec.status == "pr_open":
                assert rec.head_sha is not None, (
                    f"[{node_id}] head_sha must be set on pr_open records"
                )
                assert rec.upstream_merge_sha is None, (
                    f"[{node_id}] upstream_merge_sha must be None on pr_open records "
                    f"(reserved for P4 merged SHA); got {rec.upstream_merge_sha!r}"
                )


# ---------------------------------------------------------------------------
# 14. Finding #4: downstream rebuilt when upstream was rebuilt this run
#
# Pre-fix: a node already in completed_nodes was always skipped, even if its
# upstream was rebuilt during the current run (stale injection risk).
# Post-fix: if any upstream was rebuilt this run, the downstream also rebuilds.


class TestDownstreamRebuildsWhenUpstreamRebuilt:
    """Upstream changed + downstream PR open → downstream blocked (deferred rebuild).

    REWRITTEN from the original Finding #4 test to match the deferred-rebuild
    behavior:

    Previous behavior (removed): when an upstream was rebuilt this run, the
    downstream was also rebuilt automatically.  This was non-functional because
    ``publish_from_worktree`` has no update-existing-PR path — re-publishing a
    rebuilt downstream whose draft PR is open causes ``gh pr create`` to fail.

    Current behavior: if an upstream changed (rebuilt this run OR out-of-band
    SHA advance) AND the downstream's PR is open, the downstream is blocked with
    reason ``"upstream_changed_downstream_pr_open"`` so the user can close the
    stale PR and re-run.  Automatic downstream rebuild is deferred to a future
    release that adds an update-existing-PR primitive.
    """

    def test_downstream_rebuilt_when_upstream_pr_closed_and_rebuilt(self, tmp_path: Path):
        """Upstream PR closed → upstream rebuilt this run → downstream PR open → BLOCKED.

        REWRITTEN: the old test asserted downstream was also rebuilt (completed).
        Under the deferred-rebuild policy, an upstream rebuilt this run with the
        downstream's PR still open → downstream is blocked with a clear reason,
        NOT silently re-published.

        Scenario:
          - Ledger: repo-a=pr_open (sha-a1), repo-b=pr_open (sha-b1).
          - Second run: repo-a's PR is closed (_pr_is_open returns False for its
            branch) → repo-a excluded from completed_nodes → repo-a rebuilt.
          - repo-b is in completed_nodes (its PR is still open).
          - repo-a in rebuilt_this_run + repo-b PR open → BLOCKED, not rebuilt.

        Pre-deferred behavior: repo-b would also be rebuilt (broken — no
        update-existing-PR path).
        Post-deferred behavior: repo-b is blocked with reason
        ``"upstream_changed_downstream_pr_open"``.
        """
        from phase_loop_runtime.train_ledger import LedgerRecord, append_record

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Pre-populate: both nodes were previously pr_open
        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="pr_open",
            branch="feat/train-a",
            pr_url="https://gh.com/repo-a/1",
            head_sha="sha-a1",
            merge_order=0,
        ))
        append_record(ledger, LedgerRecord(
            node_id="repo-b/specs/plan-b.md",
            status="pr_open",
            branch="feat/train-b",
            pr_url="https://gh.com/repo-b/1",
            head_sha="sha-b1",
            merge_order=1,
        ))

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        run_loop_calls: List[str] = []
        publish_calls: List[str] = []

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            run_loop_calls.append(workspace.name)
            return (None, [])

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            publish_calls.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}-new",
                "head_sha": f"sha-{workspace.name}-new",
                "pr_url": f"https://gh.com/{workspace.name}/2",
            }

        def _pr_is_open_a_closed(workspace: Path, branch: str) -> bool:
            # repo-a's PR is now closed; repo-b's PR is still open
            return branch == "feat/train-b"

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_a_closed,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        # CRITICAL (deferred-rebuild behavior):
        # repo-a was rebuilt (its PR was closed, so it goes through full build).
        assert "repo-a" in run_loop_calls, "repo-a must be rebuilt (its PR is closed)"

        # repo-b's PR is still open but its upstream (repo-a) was rebuilt →
        # BLOCKED, not silently re-published (no update-existing-PR path exists).
        assert result["status"] == "blocked", (
            f"Expected blocked (deferred-rebuild policy); got {result['status']!r}. "
            f"Pre-deferred behavior would return 'completed' after re-publishing "
            f"repo-b, which is non-functional (gh pr create fails on open PR)."
        )
        assert result["node_id"] == "repo-b/specs/plan-b.md", (
            f"Expected repo-b to be the blocked node; got {result.get('node_id')!r}"
        )
        assert result["detail"]["reason"] == "upstream_changed_downstream_pr_open", (
            f"Expected reason 'upstream_changed_downstream_pr_open'; "
            f"got {result['detail'].get('reason')!r}"
        )
        # repo-b must NOT have been re-published (no update-existing-PR path)
        assert "repo-b" not in publish_calls, (
            "repo-b must NOT be published when its upstream changed and its PR is open "
            "(deferred-rebuild policy)"
        )


# ---------------------------------------------------------------------------
# 15. T-E: workspace-channel train → preflight_failed → zero PRs
#
# Pre-fix: validate_train had no T-E rule; a workspace edge would reach the
# executor which raises UnsupportedChannelKind mid-train (after partial PRs opened).
# Post-fix: validate_train_loud fires in preflight → preflight_failed → zero PRs.


class TestWorkspaceTrainZeroPRs:
    """Workspace-channel edge in a train → T-E validation → preflight_failed → zero PRs."""

    def test_workspace_train_preflight_failed_zero_prs(self, tmp_path: Path):
        """A train with a workspace-channel edge fails at preflight, opens zero PRs.

        T-E rule: only pin (with file=) and submodule are supported for real
        consumption.  validate_train_loud fires before the per-node loop, so
        no PR is ever opened when the train is structurally invalid.

        Pre-fix: no T-E rule → workspace edge reached executor → UnsupportedChannelKind
        mid-train (after repo-a's PR was already opened — a partial draft train).
        Post-fix: T-E in preflight → zero PRs.
        """
        roadmap = parse_train_roadmap(TRAIN_WORKSPACE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
        )

        assert result["status"] == "preflight_failed", (
            f"Expected preflight_failed for workspace-channel train; got {result}"
        )
        assert any("(T-E)" in e for e in result.get("errors", [])), (
            f"Expected T-E validation error in preflight errors; got {result.get('errors')}"
        )
        # THE CRITICAL ASSERTION: zero PRs even though repo-a is a valid root node
        assert publish_mock.call_count == 0, (
            f"Expected zero publish calls for workspace-channel train; "
            f"got {publish_mock.call_count}"
        )
        assert run_loop_mock.call_count == 0


# ---------------------------------------------------------------------------
# 16. Pin channel real seam: executor writes manifest → snapshot paths published
#
# Pre-fix (Finding #2a): pin wrote a sentinel file nothing read → hollow injection.
# Post-fix: _default_executor rewrites the manifest file the downstream build reads.
# The manifest path must appear in the publish call's owned_paths (snapshot invariant).


class TestPinChannelRealSeam:
    """Pin channel: real executor writes manifest; snapshot paths include manifest."""

    def test_pin_train_manifest_written_and_in_published_paths(self, tmp_path: Path):
        """2-node pin train: real executor writes manifest.json; manifest appears in publish.

        Real seam: no _set_upstream_ref_fn stub → _default_executor runs and
        rewrites repo-b/manifest.json with the upstream SHA.  run_loop returns
        a snapshot with manifest.json in phase_owned_dirty_paths — asserting that
        path appears in the publish call's owned_paths ties the real write to the
        snapshot-publishing invariant.

        Two things proven:
          (a) manifest.json on disk contains the injected upstream SHA.
          (b) manifest.json is in the owned_paths forwarded to publish.
        """
        from types import SimpleNamespace

        # Repo-b workspace has a manifest.json the downstream build reads
        repo_b_ws = tmp_path / "repo-b"
        repo_b_ws.mkdir()
        import json as _json
        (repo_b_ws / "manifest.json").write_text(
            _json.dumps({"deps": {"repo-a": "0.0.0"}, "version": "1.0"}),
            encoding="utf-8",
        )

        repo_a_ws = tmp_path / "repo-a"
        repo_a_ws.mkdir()

        roadmap = parse_train_roadmap(TRAIN_PIN_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {
            "repo-a/specs/plan-a.md": repo_a_ws,
            "repo-b/specs/plan-b.md": repo_b_ws,
        }

        # Stub run_loop: repo-b returns snapshot with manifest.json in owned paths
        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            if workspace.name == "repo-b":
                return (
                    SimpleNamespace(
                        phase_owned_dirty_paths=("manifest.json", "src/feature.py"),
                        dirty_paths=("manifest.json", "src/feature.py"),
                    ),
                    [],
                )
            return (None, [])

        published_paths: dict = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            published_paths[workspace.name] = list(owned_paths)
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
            _run_loop=_run_loop,
            _publish=_publish,
            # _set_upstream_ref_fn intentionally NOT provided → real _default_executor
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed", (
            f"Expected completed; got {result}"
        )

        # (a) The real executor wrote the upstream SHA into manifest.json on disk
        data = _json.loads((repo_b_ws / "manifest.json").read_text(encoding="utf-8"))
        upstream_sha = "sha-repo-a"  # publish returned this for repo-a
        assert data["deps"]["repo-a"] == upstream_sha, (
            f"Expected upstream SHA {upstream_sha!r} at deps.repo-a in manifest.json "
            f"after pin injection; got {data['deps']['repo-a']!r}. "
            f"Pin injection is hollow — downstream builds against wrong upstream."
        )

        # (b) manifest.json is in repo-b's published owned_paths (snapshot publishing)
        repo_b_paths = published_paths.get("repo-b", [])
        assert "manifest.json" in repo_b_paths, (
            f"Expected 'manifest.json' in repo-b's published paths (snapshot invariant); "
            f"got {repo_b_paths!r}. The snapshot path is not being forwarded to publish."
        )


# ---------------------------------------------------------------------------
# 17. Finding #6 (re-CR): union injected channel paths into published owned_paths
#
# Pre-fix: set_upstream_ref returned None (ignored); the coordinator-injected
# manifest could be DROPPED from the PR if run_loop's snapshot excluded it.
# The existing pin test (16) passed only because the snapshot listed manifest.json.
# Post-fix: set_upstream_ref returns the modified paths; the coordinator unions
# them into owned_paths so the pin/submodule change always ships.
#
# Real-seam requirement: the run_loop snapshot MUST NOT include manifest.json;
# the published paths MUST still contain it (proving the union, not a rigged
# snapshot).  This test FAILS against the pre-fix code.


class TestUnionInjectedChannelPaths:
    """Coordinator unions injected channel paths into PR owned_paths (Finding #6 re-CR)."""

    def test_pin_manifest_in_published_paths_even_when_snapshot_excludes_it(
        self, tmp_path: Path
    ):
        """Real-seam union test: snapshot WITHOUT manifest → published paths WITH manifest.

        The run_loop stub returns a snapshot whose phase_owned_dirty_paths does NOT
        include manifest.json.  The _set_upstream_ref_fn stub returns ["manifest.json"]
        (simulating the real pin executor).  The published owned_paths must still
        contain manifest.json — proving the coordinator unions the injected paths,
        NOT that the snapshot happened to include them.

        This test FAILS against the pre-fix code (set_upstream_ref return was ignored).
        """
        from types import SimpleNamespace

        roadmap = parse_train_roadmap(TRAIN_PIN_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        # run_loop for repo-b returns a snapshot that DELIBERATELY excludes
        # manifest.json — only implementation files are listed.
        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            if workspace.name == "repo-b":
                return (
                    SimpleNamespace(
                        # manifest.json intentionally ABSENT from snapshot paths
                        phase_owned_dirty_paths=("src/consumer.py",),
                        dirty_paths=("src/consumer.py",),
                    ),
                    [],
                )
            return (None, [])

        # set_upstream_ref stub returns the injected path (simulates real executor)
        def _set_upstream_ref_returns_path(workspace, channel, ref):
            if workspace.name == "repo-b" and channel.kind == "pin":
                return [channel.params["file"]]  # e.g. ["manifest.json"]
            return []

        published_paths: dict = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            published_paths[workspace.name] = list(owned_paths)
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
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=_set_upstream_ref_returns_path,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed", f"Expected completed; got {result}"

        repo_b_paths = published_paths.get("repo-b", [])

        # THE CRITICAL ASSERTION: manifest.json must be in owned_paths even though
        # the snapshot excluded it.  Proving the union, not a rigged snapshot.
        assert "manifest.json" in repo_b_paths, (
            f"Expected 'manifest.json' in repo-b's published paths (union of injected "
            f"channel paths); got {repo_b_paths!r}. "
            f"Pre-fix bug: set_upstream_ref return was ignored → pin manifest could be "
            f"dropped from the published PR."
        )
        # Implementation file from snapshot is also present
        assert "src/consumer.py" in repo_b_paths, (
            f"Expected 'src/consumer.py' (from snapshot) in repo-b's paths; "
            f"got {repo_b_paths!r}"
        )

    def test_union_deduplicates_when_snapshot_already_includes_injected_path(
        self, tmp_path: Path
    ):
        """If the snapshot already includes the injected path, no duplicate is added."""
        from types import SimpleNamespace

        roadmap = parse_train_roadmap(TRAIN_PIN_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        def _run_loop(workspace, roadmap_path, *, run_mode="autonomous", **kwargs):
            if workspace.name == "repo-b":
                return (
                    SimpleNamespace(
                        # manifest.json IS in snapshot (snapshot owns it)
                        phase_owned_dirty_paths=("manifest.json", "src/consumer.py"),
                        dirty_paths=("manifest.json", "src/consumer.py"),
                    ),
                    [],
                )
            return (None, [])

        def _set_upstream_ref_returns_path(workspace, channel, ref):
            if workspace.name == "repo-b" and channel.kind == "pin":
                return ["manifest.json"]
            return []

        published_paths: dict = {}

        def _publish(workspace, owned_paths, *, draft, pr_body=None, **kwargs):
            assert draft is True
            published_paths[workspace.name] = list(owned_paths)
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
            _run_loop=_run_loop,
            _publish=_publish,
            _set_upstream_ref_fn=_set_upstream_ref_returns_path,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "completed"

        repo_b_paths = published_paths.get("repo-b", [])
        assert "manifest.json" in repo_b_paths
        # No duplicate: manifest.json appears exactly once
        assert repo_b_paths.count("manifest.json") == 1, (
            f"manifest.json must appear exactly once (no duplicate from union); "
            f"got {repo_b_paths!r}"
        )


# ---------------------------------------------------------------------------
# 18. Hardening: channel executor path containment and JSON safety
#
# These tests exercise _default_executor directly (not through run_train) to
# verify the hardening added in Finding #8 (re-CR):
#   (a) Path containment: file/path params that escape the workspace → ValueError
#   (b) JSON key safety: dotted key over a non-dict intermediate → ValueError
#   (c) JSON key safety: empty or malformed key → ValueError


class TestChannelExecutorHardening:
    """_default_executor rejects path escapes and malformed JSON keys."""

    def test_pin_file_path_traversal_fails_loud(self, tmp_path: Path):
        """A '../'-escaping file param → ValueError, no write outside workspace.

        The executor must resolve workspace / params["file"] and assert the result
        is strictly within workspace.resolve().  An absolute or traversal path that
        escapes the workspace must raise ValueError, never write outside it.
        """
        from phase_loop_runtime.cross_repo_channel import _default_executor

        workspace = tmp_path / "repo-b"
        workspace.mkdir()
        # A benign target outside the workspace that must NOT be written
        outside = tmp_path / "etc" / "evil.txt"
        outside.parent.mkdir(parents=True, exist_ok=True)

        # ../etc/evil.txt escapes the workspace via .. traversal
        with pytest.raises(ValueError, match="outside the workspace"):
            _default_executor(workspace, "pin", {"file": "../etc/evil.txt"}, "sha-x")

        assert not outside.exists(), (
            "Executor must not write outside the workspace on a path traversal attempt"
        )

    def test_pin_absolute_file_param_fails_loud(self, tmp_path: Path):
        """An absolute path in file= → ValueError (containment violation)."""
        from phase_loop_runtime.cross_repo_channel import _default_executor

        workspace = tmp_path / "repo-b"
        workspace.mkdir()
        outside_abs = str(tmp_path / "etc" / "evil.txt")

        with pytest.raises(ValueError, match="outside the workspace"):
            _default_executor(workspace, "pin", {"file": outside_abs}, "sha-x")

    def test_pin_json_key_over_non_dict_raises(self, tmp_path: Path):
        """Setting a dotted key when an intermediate is not a dict → ValueError.

        Pre-fix: the executor silently replaced the non-dict value with {}.
        Post-fix: raises ValueError to prevent silent data loss.
        """
        import json as _json
        from phase_loop_runtime.cross_repo_channel import _default_executor

        workspace = tmp_path / "repo-b"
        workspace.mkdir()
        # manifest.json has "deps" as a string, not a dict
        manifest = workspace / "manifest.json"
        manifest.write_text(
            _json.dumps({"deps": "not-a-dict", "version": "1.0"}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="not a dict"):
            _default_executor(
                workspace, "pin",
                {"file": "manifest.json", "key": "deps.repo-a"},
                "sha-x",
            )

        # The manifest must NOT be silently overwritten
        data = _json.loads(manifest.read_text(encoding="utf-8"))
        assert data["deps"] == "not-a-dict", (
            "manifest.json must not be modified when a non-dict intermediate is detected"
        )

    def test_pin_empty_key_raises(self, tmp_path: Path):
        """An empty key= param → ValueError (malformed key rejected)."""
        from phase_loop_runtime.cross_repo_channel import _default_executor
        import json as _json

        workspace = tmp_path / "repo-b"
        workspace.mkdir()
        manifest = workspace / "manifest.json"
        manifest.write_text(_json.dumps({}), encoding="utf-8")

        with pytest.raises(ValueError, match="malformed"):
            _default_executor(
                workspace, "pin",
                {"file": "manifest.json", "key": ""},
                "sha-x",
            )

    def test_pin_returns_modified_path(self, tmp_path: Path):
        """_default_executor for pin returns the list containing the file param."""
        from phase_loop_runtime.cross_repo_channel import _default_executor

        workspace = tmp_path / "repo-b"
        workspace.mkdir()
        # Plain version file (no key)
        result = _default_executor(
            workspace, "pin", {"file": "version.txt"}, "sha-abc"
        )
        assert result == ["version.txt"], (
            f"Expected ['version.txt'] from pin executor; got {result!r}"
        )
        assert (workspace / "version.txt").read_text(encoding="utf-8") == "sha-abc\n"


# ---------------------------------------------------------------------------
# 19. Deferred rebuild — out-of-band upstream SHA change → downstream blocked
#
# When a confirmed-open upstream PR's live head SHA differs from the ledger
# (out-of-band push), and the downstream's PR is also open, the downstream
# must be blocked with reason "upstream_changed_downstream_pr_open".
# Do NOT silently skip and do NOT attempt a re-publish.


class TestOutOfBandUpstreamBlocksDownstream:
    """Out-of-band upstream push + open downstream PR → downstream blocked."""

    def test_oob_upstream_sha_with_open_downstream_blocks(self, tmp_path: Path):
        """Upstream's live SHA ≠ ledger SHA (OOB push) → downstream blocked.

        Scenario:
          - Ledger: repo-a=pr_open (sha-v1), repo-b=pr_open (sha-b1).
          - Both PRs are still open (pr_is_open returns True for both).
          - Live SHA for repo-a is sha-v2 (different from ledger sha-v1) →
            detected as out-of-band push.
          - Live SHA for repo-b is sha-b1 (same as ledger, no OOB).
          - In the loop: repo-a is skipped (confirmed open, no changed upstreams).
          - repo-b is in completed_nodes but its upstream (repo-a) is in
            out_of_band_upstreams → BLOCKED with reason
            "upstream_changed_downstream_pr_open".

        Pre-fix: no OOB detection → repo-b silently skipped (stale injection risk).
        Post-fix: OOB detected → repo-b blocked with clear reason.
        """
        from phase_loop_runtime.train_ledger import LedgerRecord, append_record

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="pr_open",
            branch="feat/train-a",
            pr_url="https://gh.com/repo-a/1",
            head_sha="sha-v1",  # stale — will be force-pushed OOB
            merge_order=0,
        ))
        append_record(ledger, LedgerRecord(
            node_id="repo-b/specs/plan-b.md",
            status="pr_open",
            branch="feat/train-b",
            pr_url="https://gh.com/repo-b/1",
            head_sha="sha-b1",
            merge_order=1,
        ))

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        def _live_sha(workspace, branch):
            if branch == "feat/train-a":
                return "sha-v2"  # OOB push: differs from ledger sha-v1
            if branch == "feat/train-b":
                return "sha-b1"  # same as ledger
            return None

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _set_upstream_ref_fn=lambda *a, **kw: None,
            _preflight_fn=_preflight_pass,
            # both PRs still open
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=_live_sha,
        )

        assert result["status"] == "blocked", (
            f"Expected blocked (OOB detection); got {result['status']!r}"
        )
        assert result["node_id"] == "repo-b/specs/plan-b.md", (
            f"Expected repo-b to be blocked; got {result.get('node_id')!r}"
        )
        assert result["detail"]["reason"] == "upstream_changed_downstream_pr_open", (
            f"Expected reason 'upstream_changed_downstream_pr_open'; "
            f"got {result['detail'].get('reason')!r}"
        )
        # "out-of-band push" must appear in the message
        assert "out-of-band" in result["detail"]["message"].lower(), (
            f"Expected 'out-of-band' in detail message; got {result['detail'].get('message')!r}"
        )
        # Neither repo ran (both were in completed_nodes; repo-a skipped, repo-b blocked)
        assert run_loop_mock.call_count == 0, (
            "run_loop must not be called when all nodes are in completed_nodes "
            f"(repo-a skipped, repo-b blocked before run_loop)"
        )
        assert publish_mock.call_count == 0, (
            "publish must not be called on a blocked node"
        )


# ---------------------------------------------------------------------------
# 20. Hardening: missing resume SHA → block (no moving-branch-name fallback)
#
# Pre-fix: if live_sha and ledger head_sha were both None, the coordinator fell
# back to injecting the upstream branch name (a moving target) — building the
# downstream against a branch tip rather than a pinnable SHA.
# Post-fix: block with a "no resolvable SHA" error rather than injecting a
# moving branch name.


class TestMissingResumeShaBlocks:
    """Resume with no resolvable upstream SHA → downstream blocked (no branch fallback)."""

    def test_missing_head_sha_blocks_downstream_not_branch_fallback(self, tmp_path: Path):
        """No live SHA + no ledger head_sha → downstream blocked, not injected with branch name.

        Scenario:
          - Ledger: repo-a=pr_open, head_sha=None (never stored or lost).
          - Live SHA query for repo-a returns None.
          - completed_nodes["repo-a"]["head_sha"] is therefore None.
          - Downstream repo-b tries to inject: ref = head_sha → None.
          - Pre-fix: falls back to branch name → injects a moving target.
          - Post-fix: raises "no resolvable SHA" → downstream blocked.
        """
        from phase_loop_runtime.train_ledger import LedgerRecord, append_record

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # repo-a is pr_open but head_sha was never recorded
        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="pr_open",
            branch="feat/train-a",
            pr_url="https://gh.com/repo-a/1",
            head_sha=None,  # missing — never stored
            merge_order=0,
        ))

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        inject_log: list = []

        def _set_upstream_ref_recording(workspace, channel, ref):
            inject_log.append({"workspace": workspace.name, "ref": ref})
            return []

        publish_mock = MagicMock()
        run_loop_mock = MagicMock()

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=run_loop_mock,
            _publish=publish_mock,
            _set_upstream_ref_fn=_set_upstream_ref_recording,
            _preflight_fn=_preflight_pass,
            # repo-a's PR is open (so it ends up in completed_nodes)
            _pr_is_open=_pr_is_open_true,
            # live query also returns None
            _live_pr_head_sha_fn=lambda ws, br: None,
        )

        # repo-b must be blocked (missing SHA)
        assert result["status"] == "blocked", (
            f"Expected blocked (missing SHA); got {result['status']!r}"
        )
        assert result["node_id"] == "repo-b/specs/plan-b.md", (
            f"Expected repo-b to be the blocked node; got {result.get('node_id')!r}"
        )
        # The block reason must mention the missing SHA, not inject a branch name
        assert "no resolvable SHA" in result["detail"]["reason"], (
            f"Expected 'no resolvable SHA' in blocked reason; "
            f"got {result['detail'].get('reason')!r}"
        )
        # The branch name "feat/train-a" must NOT have been passed to set_upstream_ref
        for entry in inject_log:
            assert entry["ref"] != "feat/train-a", (
                f"Branch name 'feat/train-a' was injected as a moving-target ref "
                f"(pre-fix bug: head_sha fallback to branch name); entry={entry!r}"
            )
        assert publish_mock.call_count == 0, (
            "publish must not be called when the downstream injection is blocked"
        )


# ---------------------------------------------------------------------------
# SHOULD-FIX 3: multi-phase nodes must not publish partial draft PRs

class TestMultiPhaseNodeGuard:
    """A node whose run_loop leaves any phase at 'planned' must not publish a
    partial draft PR.

    SHOULD-FIX 3 (plans/pr35-cr-reconciliation.md): run_train calls run_loop
    once with default max_phases=1.  A >1-phase roadmap stops after the first
    phase; remaining phases stay at 'planned'.  Publishing at this point ships
    a half-built PR.  The guard raises from within the try/except around
    run_loop, so the node is recorded as blocked in the ledger and publish is
    never called.
    """

    def _make_snapshot_with_planned(self, planned_phases: list) -> "StateSnapshot":
        """Build a fake StateSnapshot that has some phases still at 'planned'."""
        from phase_loop_runtime.models import StateSnapshot, utc_now
        phases = {"P1": "awaiting_phase_closeout"}
        for ph in planned_phases:
            phases[ph] = "planned"
        return StateSnapshot(
            timestamp=utc_now(),
            repo="fake-repo",
            roadmap="specs/plan.md",
            phases=phases,
            current_phase="P1",
        )

    def _make_complete_snapshot(self) -> "StateSnapshot":
        """Build a fake StateSnapshot where all phases are complete/awaiting."""
        from phase_loop_runtime.models import StateSnapshot, utc_now
        return StateSnapshot(
            timestamp=utc_now(),
            repo="fake-repo",
            roadmap="specs/plan.md",
            phases={"P1": "awaiting_phase_closeout"},
            current_phase="P1",
        )

    def test_planned_phase_in_snapshot_blocks_node(self, tmp_path: Path):
        """run_loop snapshot with a 'planned' phase → node blocked, publish not called."""
        from phase_loop_runtime.train_roadmap import parse_train_roadmap
        from phase_loop_runtime.train_ledger import append_record, LedgerRecord, read_ledger

        roadmap_md = """\
# Release Train: multi-phase-guard

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
"""
        roadmap = parse_train_roadmap(roadmap_md)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_calls: list = []

        def _publish(workspace, owned_paths, *, draft, **kw):
            publish_calls.append(workspace.name)
            return {"status": "published", "branch": "feat/x", "head_sha": "sha-x", "pr_url": "https://gh/1"}

        # run_loop returns a snapshot with P2 still at 'planned'.
        snapshot_with_planned = self._make_snapshot_with_planned(["P2"])

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (snapshot_with_planned, []),
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=lambda *a, **kw: [],
            _pr_is_open=lambda *a, **kw: False,
            _live_pr_head_sha_fn=lambda *a, **kw: None,
        )

        assert result["status"] == "blocked", (
            f"SHOULD-FIX 3 violated: expected blocked (partial node), got {result['status']!r}"
        )
        assert publish_calls == [], (
            f"SHOULD-FIX 3 violated: publish called for a partial node ({publish_calls!r})"
        )
        # Ledger must record blocked so the train is resumable.
        records = read_ledger(ledger)
        blocked = [r for r in records.values() if r.status == "blocked"]
        assert blocked, "blocked record must appear in ledger for a partial node"
        assert "partial" in result["detail"]["reason"].lower() or "planned" in result["detail"]["reason"].lower(), (
            f"blocked reason should mention partial/planned phases; got {result['detail'].get('reason')!r}"
        )

    def test_all_complete_phases_allows_publish(self, tmp_path: Path):
        """run_loop snapshot with no 'planned' phases → publish proceeds normally."""
        from phase_loop_runtime.train_roadmap import parse_train_roadmap

        roadmap_md = """\
# Release Train: complete-node

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
"""
        roadmap = parse_train_roadmap(roadmap_md)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_calls: list = []

        def _publish(workspace, owned_paths, *, draft, **kw):
            publish_calls.append(workspace.name)
            return {"status": "published", "branch": "feat/x", "head_sha": "sha-x", "pr_url": "https://gh/1"}

        # Snapshot with no 'planned' phases.
        complete_snapshot = self._make_complete_snapshot()

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (complete_snapshot, []),
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=lambda *a, **kw: [],
            _pr_is_open=lambda *a, **kw: False,
            _live_pr_head_sha_fn=lambda *a, **kw: None,
        )

        assert result["status"] == "completed", (
            f"Expected completed for a fully-run node; got {result['status']!r}"
        )
        assert publish_calls == ["repo-a"], (
            f"Expected publish called for repo-a; got {publish_calls!r}"
        )

    def test_non_green_phase_in_snapshot_blocks_node(self, tmp_path: Path):
        """run_loop snapshot with a 'blocked' (non-green) phase → node blocked,
        publish NOT called.

        Closes the gemini #3/#4 combined false-green: the prior guard blocked
        only 'planned' phases, so a *failed*-phase node could publish a draft
        that later trivial-passed P4 re-verify on a no-`## Verification` plan.
        The widened guard blocks any phase not in {complete, awaiting_phase_closeout}.
        """
        from phase_loop_runtime.train_roadmap import parse_train_roadmap
        from phase_loop_runtime.train_ledger import read_ledger
        from phase_loop_runtime.models import StateSnapshot, utc_now

        roadmap_md = """\
# Release Train: non-green-guard

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
"""
        roadmap = parse_train_roadmap(roadmap_md)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_calls: list = []

        def _publish(workspace, owned_paths, *, draft, **kw):
            publish_calls.append(workspace.name)
            return {"status": "published", "branch": "feat/x", "head_sha": "sha-x", "pr_url": "https://gh/1"}

        # Single-phase node whose only phase ended 'blocked' (a real run_loop
        # failure state) — NOT 'planned'.  The prior guard would have let this
        # publish; the widened guard must block it.
        blocked_snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo="fake-repo",
            roadmap="specs/plan.md",
            phases={"P1": "blocked"},
            current_phase="P1",
        )

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (blocked_snapshot, []),
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=lambda *a, **kw: [],
            _pr_is_open=lambda *a, **kw: False,
            _live_pr_head_sha_fn=lambda *a, **kw: None,
        )

        assert result["status"] == "blocked", (
            f"Combined false-green guard violated: a 'blocked'-phase node must not "
            f"publish; got {result['status']!r}"
        )
        assert publish_calls == [], (
            f"Combined false-green guard violated: publish called for a non-green node "
            f"({publish_calls!r})"
        )
        records = read_ledger(ledger)
        assert any(r.status == "blocked" for r in records.values()), (
            "blocked record must appear in ledger for a non-green node"
        )
        assert "green" in result["detail"]["reason"].lower(), (
            f"blocked reason should mention the non-green phase state; "
            f"got {result['detail'].get('reason')!r}"
        )
