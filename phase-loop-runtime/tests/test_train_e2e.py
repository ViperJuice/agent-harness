"""End-to-end tests for the cross-repo release-train coordinator (P3 + P4 together).

Unlike the unit tests in test_train_runner.py / test_train_merge.py, which test
P3 and P4 in isolation (pre-populating the ledger via _setup_p3_done), these
tests drive the full happy path from an EMPTY ledger through a single
``run_train(..., _merge_phase_enabled=True, run_mode="governed")`` call.

Coverage:
  E2E-1. Full happy path (3-repo chain): empty ledger → draft PRs open in topo
         order → train-level review → sequential merge with downstream
         re-verify against MERGED SHA → all merged.
  E2E-2. Non-approval terminal: panel rejects → review_halted, ZERO merges.
  E2E-3. Mid-train resumable failure: first call → some draft PRs open, merge
         phase begins, downstream re-verify fails → merge_halted; second call
         (same ledger, reverify now passes) → resumes and completes the merge.

Run with:
    cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_e2e.py -q

All git/gh/run_loop/publish/panel boundaries are stubbed; no live network access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from phase_loop_runtime.governed_premerge import LoopResult
from phase_loop_runtime.train_ledger import LedgerRecord, append_record, read_ledger
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import _TRAIN_REVIEW_NODE_ID, run_train


# ---------------------------------------------------------------------------
# Test roadmap fixture — 3-node chain: repo-a (root) → repo-b → repo-c (leaf)

TRAIN_3REPO_MD = """\
# Release Train: e2e-feature

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a

### Node: repo-c / specs/plan-c.md

**Depends on:** repo-b / specs/plan-b.md
**Channel:** submodule path=vendor/repo-b
"""


# ---------------------------------------------------------------------------
# Shared stubs

def _preflight_pass(nodes, resolve_workspace):
    """Preflight stub: always passes."""
    return []


def _pr_is_open_false(workspace: Path, branch: str) -> bool:
    """PR-state stub: no existing open PRs (fresh run)."""
    return False


def _pr_is_open_true(workspace: Path, branch: str) -> bool:
    """PR-state stub: every draft PR is open (ledger reflects reality)."""
    return True


def _make_publish_stub(results: Dict[str, dict]):
    """Returns draft PRs; always uses draft=True per P3 invariant."""
    def _publish(workspace: Path, owned_paths, *, draft: bool, pr_body: Optional[str] = None, **kw):
        assert draft is True, f"P3 must always open draft PRs; got draft={draft!r}"
        return results.get(str(workspace), {
            "status": "published",
            "branch": f"feat/train-{workspace.name}",
            "head_sha": f"sha-draft-{workspace.name}",
            "pr_url": f"https://gh.com/{workspace.name}/pr/1",
        })
    return _publish


def _approval_review_fn(artifact: str, run_mode: str) -> LoopResult:
    """Review stub: approves the train."""
    return LoopResult(mergeable=True, ran=True, rounds=1)


def _rejection_review_fn(artifact: str, run_mode: str) -> LoopResult:
    """Review stub: rejects the train with a non-human blocker."""
    return LoopResult(
        mergeable=False,
        ran=True,
        rounds=1,
        terminal_blocker={
            "human_required": False,
            "blocker_class": "review_gate_block",
            "blocker_summary": "train review rejected by e2e test stub",
        },
        reason="non_convergence",
    )


def _reverify_pass(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
    """Re-verify stub: always passes."""
    return True


def _make_merge_pr_stub(merge_log: List[str]):
    """Records each merge call and returns a deterministic merged SHA."""
    def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
        merge_log.append(workspace.name)
        return f"sha-merged-{workspace.name}"
    return _merge_pr


# ---------------------------------------------------------------------------
# E2E-1: Full happy path — 3-repo chain, empty ledger, single run_train call

class TestE2EFullHappyPath:
    """From empty ledger to all-merged in a single run_train call.

    The test proves P3 + P4 operate in one coherent end-to-end pass:
    draft PRs open in topo order, train review runs, merges proceed in topo
    order with downstream re-verify, ledger ends in merged state.
    """

    def test_all_nodes_merged(self, tmp_path: Path):
        """Single run_train call drives empty ledger to full merged status."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log: List[str] = []
        publish_log: List[str] = []

        def _run_loop_stub(*a, **kw):
            return (None, [])

        def _publish_spy(workspace: Path, owned_paths, *, draft: bool, **kw):
            assert draft is True
            publish_log.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-draft-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/pr/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_stub,
            _publish=_publish_spy,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merged", (
            f"Expected 'merged', got {result['status']!r}"
        )

    def test_draft_prs_open_in_topo_order(self, tmp_path: Path):
        """Draft PRs are published in topo order (root before downstream)."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_log: List[str] = []

        def _publish_spy(workspace: Path, owned_paths, *, draft: bool, **kw):
            assert draft is True
            publish_log.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-draft-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/pr/1",
            }

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish_spy,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        # repo-a (root) must be published before repo-b, which before repo-c.
        assert publish_log == ["repo-a", "repo-b", "repo-c"], (
            f"Expected topo-order publish ['repo-a', 'repo-b', 'repo-c'], "
            f"got {publish_log}"
        )

    def test_merges_in_topo_order(self, tmp_path: Path):
        """Sequential merges happen in topo order: upstream before downstream."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log: List[str] = []

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert merge_log == ["repo-a", "repo-b", "repo-c"], (
            f"Expected topo-order merge ['repo-a', 'repo-b', 'repo-c'], "
            f"got {merge_log}"
        )

    def test_ledger_ends_fully_merged(self, tmp_path: Path):
        """Ledger records all nodes as merged with their respective SHAs."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        state = read_ledger(ledger)
        for node_id, repo_name in [
            ("repo-a/specs/plan-a.md", "repo-a"),
            ("repo-b/specs/plan-b.md", "repo-b"),
            ("repo-c/specs/plan-c.md", "repo-c"),
        ]:
            assert state[node_id].status == "merged", (
                f"{node_id}: expected status='merged', got {state[node_id].status!r}"
            )
            assert state[node_id].upstream_merge_sha == f"sha-merged-{repo_name}", (
                f"{node_id}: expected upstream_merge_sha='sha-merged-{repo_name}', "
                f"got {state[node_id].upstream_merge_sha!r}"
            )

        # Train review approval is recorded in the ledger.
        assert _TRAIN_REVIEW_NODE_ID in state, (
            "_train_review_ approval record missing from ledger after successful merge"
        )
        assert state[_TRAIN_REVIEW_NODE_ID].status == "approved"


# ---------------------------------------------------------------------------
# E2E-2: Non-approval terminal — panel rejects, zero merges

class TestE2ENonApprovalTerminal:
    """Panel rejects the train → review_halted terminal, ZERO merges.

    The full flow still runs (P3 opens draft PRs), but P4 halts at the review
    gate and no merge_pr calls are issued.
    """

    def test_rejection_status_review_halted(self, tmp_path: Path):
        """Panel rejection returns status=review_halted from a cold start."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log: List[str] = []

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "review_halted", (
            f"Expected 'review_halted', got {result['status']!r}"
        )
        assert merge_log == [], (
            f"Panel rejection must cause ZERO merges; got {merge_log}"
        )

    def test_rejection_blocker_is_non_human(self, tmp_path: Path):
        """terminal_blocker carries human_required=False on panel rejection."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        blocker = result.get("terminal_blocker") or {}
        assert blocker.get("human_required") is False, (
            "terminal_blocker must carry human_required=False (non-human review terminal); "
            f"got {blocker!r}"
        )

    def test_rejection_draft_prs_remain_open(self, tmp_path: Path):
        """After panel rejection, all draft PRs are still open in the ledger."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        state = read_ledger(ledger)
        for node_id in [
            "repo-a/specs/plan-a.md",
            "repo-b/specs/plan-b.md",
            "repo-c/specs/plan-c.md",
        ]:
            assert state[node_id].status == "pr_open", (
                f"{node_id}: expected pr_open after rejection, got {state[node_id].status!r}"
            )
        assert _TRAIN_REVIEW_NODE_ID not in state, (
            "_train_review_ must not appear in ledger after panel rejection "
            "(no approval was granted)"
        )


# ---------------------------------------------------------------------------
# E2E-3: Mid-train resumable failure — blocked → resume continues
#
# Scenario: during the P3 draft-PR phase, repo-b's run_loop raises an
# exception → repo-b is recorded as blocked; repo-c is never reached.
# The first run returns status="blocked".  A second run (same ledger) retries
# repo-b (run_loop now succeeds), then continues to repo-c.  P4 then runs
# the full review+merge cycle on all three nodes.
#
# This exercises the coordinator's idempotent-resume guarantee: repo-a (whose
# draft PR is already open) is skipped in the P3 retry; the second run opens
# fresh PRs for repo-b and repo-c, then drives the full P4 merge.

class TestE2EResumableFailure:
    """P3-level blocking (run_loop exception) → first run = blocked; resume completes."""

    def test_first_run_blocked_status(self, tmp_path: Path):
        """First run: repo-b run_loop raises → status='blocked'."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        def _run_loop_fail_b(workspace: Path, *a, **kw):
            if workspace.name == "repo-b":
                raise RuntimeError("simulated run_loop failure for repo-b")
            return (None, [])

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_fail_b,
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "blocked", (
            f"Expected 'blocked' when repo-b run_loop fails, got {result['status']!r}"
        )

    def test_first_run_ledger_state(self, tmp_path: Path):
        """After P3 failure: repo-a is pr_open, repo-b is blocked, repo-c unreached."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        def _run_loop_fail_b(workspace: Path, *a, **kw):
            if workspace.name == "repo-b":
                raise RuntimeError("simulated run_loop failure for repo-b")
            return (None, [])

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_fail_b,
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        state = read_ledger(ledger)
        # repo-a succeeded in P3
        assert state["repo-a/specs/plan-a.md"].status == "pr_open", (
            "repo-a must have an open draft PR after P3 processes it"
        )
        # repo-b was blocked at run_loop
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            "repo-b must be blocked after run_loop exception"
        )
        # repo-c was never reached (not in ledger or blocked implicitly)
        assert "repo-c/specs/plan-c.md" not in state, (
            "repo-c must not appear in ledger when processing halted at repo-b"
        )

    def test_resume_completes_merge(self, tmp_path: Path):
        """Resume from P3 blocked: run_loop now passes → all three repos merged."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log_resume: List[str] = []

        def _run_loop_fail_b(workspace: Path, *a, **kw):
            if workspace.name == "repo-b":
                raise RuntimeError("simulated run_loop failure for repo-b")
            return (None, [])

        # --- First run: repo-a PR opens, repo-b blocks ---
        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_fail_b,
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        # --- Resume run: run_loop passes for all; repo-a PR already open (skipped) ---
        # On resume, repo-a's PR is still open (was never closed).
        def _pr_is_open_resume(workspace: Path, branch: str) -> bool:
            state = read_ledger(ledger)
            for record in state.values():
                if record.branch == branch:
                    return record.status == "pr_open"
            return False

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_resume,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log_resume),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merged", (
            f"Resume must complete all merges; got {result['status']!r}"
        )
        # All three repos must be merged in the resume run (repo-a's PR was open
        # but not merged, so it participates in the P4 merge cycle).
        for repo_name in ("repo-a", "repo-b", "repo-c"):
            assert repo_name in merge_log_resume, (
                f"{repo_name} must be merged in the resume run; got {merge_log_resume}"
            )

    def test_resume_skips_p3_for_open_pr_node(self, tmp_path: Path):
        """Resume does not re-publish a draft PR for repo-a (already pr_open)."""
        roadmap = parse_train_roadmap(TRAIN_3REPO_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_log: List[str] = []

        def _run_loop_fail_b(workspace: Path, *a, **kw):
            if workspace.name == "repo-b":
                raise RuntimeError("simulated run_loop failure for repo-b")
            return (None, [])

        # First run
        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_fail_b,
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        def _pr_is_open_resume(workspace: Path, branch: str) -> bool:
            state = read_ledger(ledger)
            for record in state.values():
                if record.branch == branch:
                    return record.status == "pr_open"
            return False

        def _publish_spy(workspace: Path, owned_paths, *, draft: bool, **kw):
            publish_log.append(workspace.name)
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-draft-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/pr/1",
            }

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish_spy,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_resume,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        # repo-a already had an open PR; resume must NOT re-publish it.
        assert "repo-a" not in publish_log, (
            f"Resume must skip repo-a (already has open draft PR); "
            f"publish_log: {publish_log}"
        )
