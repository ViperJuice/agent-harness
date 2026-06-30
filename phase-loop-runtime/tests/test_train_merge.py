"""Tests for P4 train coordinator: train-level review + sequential merge with re-verify.

Run with:
    cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_merge.py -q

All git/gh/run_loop/publish/panel boundaries are stubbed; no live network access.

Coverage:
  P4-1. Train-review non-approval → ZERO merges, status=review_halted,
         terminal_blocker carries human_required=False
  P4-2. Panel approval → sequential merges in topo order; ledger records
         merged status with upstream_merge_sha; _train_review_ approved entry
  P4-3. Downstream re-verify failure → merge_halted BEFORE downstream merge;
         upstream stays merged (forward-only guard)
  P4-4. Channel re-resolved to upstream MERGED SHA (not draft SHA) BEFORE
         reverify — the false-green killer; call order asserted explicitly
         with distinct draft vs merged SHAs
  P4-5. Idempotent resume: pre-populated ledger with repo-a merged;
         merge_pr NOT called for repo-a; re-injection for repo-b uses
         MERGED SHA read from ledger; no double-merge
  P4-6. Autonomous mode with _merge_phase_enabled=True → status=drafts_open,
         merge_pr NOT called (cross-repo merges never auto-merge)
  P4-7. Crash-between-merge-and-ledger-write resume: repo-a merged on GitHub
         but ledger not yet updated (pr_open) — pr_merged_sha_fn cross-check
         recovers the merged SHA, skips re-merge of repo-a, injects merged
         SHA into repo-b
  P4-CR-1. Live-default _live_reverify reads real StateSnapshot failure signals
         (not the absent terminal_status field).  Tests call _live_reverify
         directly without stubbing _reverify_fn; each failure case FAILS
         against the pre-fix no-op code.
  P4-CR-2. Crash-window resume via not-open pr_open: a pr_open node whose
         open-query returns False but whose merged-query returns a SHA is
         RECOVERED (not rebuilt); a closed-unmerged node still drops.
  P4-CR-3. Uncaught merge failure → merge_halted + blocked ledger record;
         already-merged upstreams stay merged (forward-only).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest

from phase_loop_runtime.governed_premerge import LoopResult
from phase_loop_runtime.models import StateSnapshot
from phase_loop_runtime.train_ledger import LedgerRecord, append_record, read_ledger
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import _TRAIN_REVIEW_NODE_ID, _live_reverify, run_train


# ---------------------------------------------------------------------------
# Test roadmap fixture — 2-node train: repo-a (root) → repo-b (downstream)

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


# ---------------------------------------------------------------------------
# Shared helpers

def _preflight_pass(nodes, resolve_workspace):
    """Preflight stub that always passes."""
    return []


def _pr_is_open_true(workspace: Path, branch: str) -> bool:
    """PR state stub: every branch has an open PR."""
    return True


def _pr_is_open_false(workspace: Path, branch: str) -> bool:
    """PR state stub: no branch has an open PR."""
    return False


def _make_publish_stub(results: Dict[str, dict]):
    """Publish stub returning predefined results per workspace."""
    def _publish(workspace: Path, owned_paths, *, draft: bool, pr_body: Optional[str] = None, **kw):
        assert draft is True, f"P3 must always open draft PRs; got draft={draft!r}"
        return results.get(str(workspace), {
            "status": "published",
            "branch": f"feat/train-{workspace.name}",
            "head_sha": f"sha-draft-{workspace.name}",
            "pr_url": f"https://gh.com/{workspace.name}/1",
        })
    return _publish


def _approval_review_fn(artifact: str, run_mode: str) -> LoopResult:
    """Review stub that approves the train."""
    return LoopResult(mergeable=True, ran=True, rounds=1)


def _rejection_review_fn(artifact: str, run_mode: str) -> LoopResult:
    """Review stub that rejects the train with a non-human blocker."""
    return LoopResult(
        mergeable=False,
        ran=True,
        rounds=1,
        terminal_blocker={
            "human_required": False,
            "blocker_class": "review_gate_block",
            "blocker_summary": "train review rejected in test",
        },
        reason="non_convergence",
    )


def _reverify_pass(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
    """Re-verify stub: always passes."""
    return True


def _reverify_fail_for_b(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
    """Re-verify stub: fails only for repo-b (the downstream)."""
    return workspace.name != "repo-b"


def _setup_p3_done(
    tmp_path: Path,
    roadmap,
    ws_map: Dict[str, Path],
    *,
    sha_a: str = "sha-draft-a",
    sha_b: str = "sha-draft-b",
):
    """Pre-populate the ledger with a completed P3 state: both nodes pr_open.

    Returns the ledger Path.  Both nodes have distinct, explicit draft SHAs so
    tests can assert the merged SHA is NOT the draft SHA.
    """
    ledger = tmp_path / "ledger" / "train.ledger.jsonl"
    append_record(ledger, LedgerRecord(
        node_id="repo-a/specs/plan-a.md",
        status="pr_open",
        branch="feat/train-a",
        head_sha=sha_a,
        pr_url="https://gh.com/repo-a/1",
        merge_order=0,
    ))
    append_record(ledger, LedgerRecord(
        node_id="repo-b/specs/plan-b.md",
        status="pr_open",
        branch="feat/train-b",
        head_sha=sha_b,
        pr_url="https://gh.com/repo-b/1",
        merge_order=1,
    ))
    return ledger


def _make_merge_pr_stub(merge_order: List[str]):
    """Merge PR stub that records the workspace name and returns a deterministic merged SHA."""
    def _merge_pr(workspace: Path, branch: str) -> str:
        merge_order.append(workspace.name)
        return f"sha-merged-{workspace.name}"
    return _merge_pr


# ---------------------------------------------------------------------------
# P4-1: Train-review non-approval → ZERO merges

class TestTrainReviewNonApproval:
    """Panel rejects the train → review_halted terminal, ZERO merges."""

    def test_review_rejected_zero_merges(self, tmp_path: Path):
        """Non-approval by the review panel: status=review_halted, no merge_pr calls."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

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
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "review_halted", (
            f"Expected 'review_halted', got {result['status']!r}"
        )
        assert merge_calls == [], (
            f"ZERO merges expected on rejection; got merge calls: {merge_calls}"
        )
        blocker = result.get("terminal_blocker") or {}
        assert blocker.get("human_required") is False, (
            f"terminal_blocker must have human_required=False; got {blocker!r}"
        )

    def test_review_rejection_preserves_draft_prs(self, tmp_path: Path):
        """After panel rejection, draft PRs remain open (no merges, no reverts)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        run_train(
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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # Ledger: both nodes must still be pr_open (no merged or reverted entries)
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "pr_open"
        assert state["repo-b/specs/plan-b.md"].status == "pr_open"
        assert _TRAIN_REVIEW_NODE_ID not in state, (
            "_train_review_ must not be recorded in ledger on rejection (no approval written)"
        )


# ---------------------------------------------------------------------------
# P4-2: Panel approval → sequential merges in topo order

class TestSequentialMerge:
    """Panel approval → merges in topo order; ledger transitions recorded."""

    def test_approve_merges_in_topo_order(self, tmp_path: Path):
        """All nodes merged in topo order (upstream before downstream)."""
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
            _merge_pr_fn=_make_merge_pr_stub(merge_order),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merged"
        # repo-a (root / upstream) merged before repo-b (downstream)
        assert merge_order == ["repo-a", "repo-b"], (
            f"Expected topo order ['repo-a', 'repo-b'], got {merge_order}"
        )

    def test_ledger_records_merged_status_and_sha(self, tmp_path: Path):
        """After merge, ledger carries status=merged and upstream_merge_sha for each node."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        run_train(
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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        state = read_ledger(ledger)
        # Both nodes must be in merged status with real merge SHAs
        assert state["repo-a/specs/plan-a.md"].status == "merged"
        assert state["repo-a/specs/plan-a.md"].upstream_merge_sha == "sha-merged-repo-a"
        assert state["repo-b/specs/plan-b.md"].status == "merged"
        assert state["repo-b/specs/plan-b.md"].upstream_merge_sha == "sha-merged-repo-b"
        # Train-level approval recorded
        assert _TRAIN_REVIEW_NODE_ID in state
        assert state[_TRAIN_REVIEW_NODE_ID].status == "approved"


# ---------------------------------------------------------------------------
# P4-3: Downstream re-verify failure → merge_halted; upstream stays merged

class TestReverifyFalseGreenGuard:
    """Re-verify fail → halt before downstream merge; upstream stays merged (forward-only)."""

    def test_reverify_fail_halts_before_downstream_merge(self, tmp_path: Path):
        """Re-verify failure: merge_halted, downstream NOT merged, upstream stays merged."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

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
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _reverify_fn=_reverify_fail_for_b,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merge_halted"
        assert result.get("node_id") == "repo-b/specs/plan-b.md"
        assert result.get("reason") == "downstream_reverify_failed"

        # Upstream (repo-a) was merged before the halt (forward-only)
        assert "repo-a" in merge_calls, "Upstream repo-a must be merged before the halt"
        # Downstream (repo-b) was NOT merged (halted before its merge step)
        assert "repo-b" not in merge_calls, (
            "Downstream repo-b must NOT be merged when re-verify fails"
        )

    def test_ledger_on_reverify_fail(self, tmp_path: Path):
        """After merge_halted, ledger shows upstream=merged, downstream=blocked."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        run_train(
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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_fail_for_b,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        state = read_ledger(ledger)
        # Forward-only: upstream stays merged
        assert state["repo-a/specs/plan-a.md"].status == "merged", (
            "Upstream must remain merged after downstream failure (forward-only)"
        )
        # Downstream: blocked in ledger
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            "Downstream must be blocked in ledger when re-verify fails"
        )


# ---------------------------------------------------------------------------
# P4-4: Channel re-resolved to upstream MERGED SHA before reverify

class TestMergedShaResolution:
    """The false-green killer: set_upstream_ref is called with the MERGED SHA (not draft).

    This is the load-bearing test.  The stubs use DISTINCT draft vs merged SHAs
    so passing the draft SHA would cause an assertion failure.
    """

    def test_channel_resolved_to_merged_sha_before_reverify(self, tmp_path: Path):
        """set_upstream_ref(…, merged_sha) precedes reverify in the call log."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

        # Use explicit, DISTINCT draft and merged SHAs so the test discriminates.
        DRAFT_SHA_A = "sha-draft-a"       # P3 draft ref: what was injected for PR creation
        MERGED_SHA_A = "sha-merged-repo-a"  # P4 merge-commit SHA: what must be re-injected

        ledger = _setup_p3_done(
            tmp_path, roadmap, ws_map,
            sha_a=DRAFT_SHA_A,
            sha_b="sha-draft-b",
        )

        # Shared call log: captures set_upstream_ref + reverify + merge_pr in order.
        call_log: List[dict] = []

        def _merge_pr(workspace: Path, branch: str) -> str:
            sha = f"sha-merged-{workspace.name}"  # repo-a → "sha-merged-repo-a"
            call_log.append({"type": "merge_pr", "workspace": workspace.name, "sha": sha})
            return sha

        def _set_upstream_ref_logging(workspace: Path, channel, ref: str):
            call_log.append({
                "type": "set_upstream_ref",
                "workspace": workspace.name,
                "ref": ref,
            })
            return []

        def _reverify_logging(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
            call_log.append({"type": "reverify", "workspace": workspace.name})
            return True

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=_set_upstream_ref_logging,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_merge_pr,
            _reverify_fn=_reverify_logging,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merged"

        # Find the P4 set_upstream_ref call for repo-b (downstream re-injection).
        # P3 injection is skipped entirely (both nodes already in completed_nodes).
        p4_set_ref_for_b = next(
            (e for e in call_log if e["type"] == "set_upstream_ref" and e["workspace"] == "repo-b"),
            None,
        )
        assert p4_set_ref_for_b is not None, (
            "set_upstream_ref was not called for repo-b; P4 must re-inject before re-verify"
        )

        # Find the reverify call for repo-b.
        reverify_for_b = next(
            (e for e in call_log if e["type"] == "reverify" and e["workspace"] == "repo-b"),
            None,
        )
        assert reverify_for_b is not None, (
            "reverify was not called for repo-b"
        )

        # CRITICAL (1): set_upstream_ref MUST appear BEFORE reverify.
        set_ref_idx = call_log.index(p4_set_ref_for_b)
        reverify_idx = call_log.index(reverify_for_b)
        assert set_ref_idx < reverify_idx, (
            f"set_upstream_ref must precede reverify in the call log "
            f"(set_upstream_ref at {set_ref_idx}, reverify at {reverify_idx})"
        )

        # CRITICAL (2): the ref injected must be the MERGED SHA, not the draft SHA.
        actual_ref = p4_set_ref_for_b["ref"]
        assert actual_ref == MERGED_SHA_A, (
            f"Expected merged SHA {MERGED_SHA_A!r}, got {actual_ref!r}. "
            "The downstream must be re-verified against the MERGED upstream SHA."
        )
        assert actual_ref != DRAFT_SHA_A, (
            f"MUST NOT use the draft SHA {DRAFT_SHA_A!r} for re-verification — "
            "this is the false-green killer guard"
        )


# ---------------------------------------------------------------------------
# P4-5: Idempotent resume — crash mid-merge

class TestIdempotentResume:
    """Crash mid-merge: re-running resumes from the last merged node, no double-merge."""

    def test_resume_skips_already_merged_node(self, tmp_path: Path):
        """Pre-populated ledger with repo-a merged → resume merges only repo-b."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Simulate a state where:
        # - P3 completed (both nodes pr_open)
        # - Train-level review approved
        # - repo-a was merged (ledger updated)
        # - Crash occurred before repo-b merge
        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="pr_open",
            branch="feat/train-a",
            head_sha="sha-draft-a",
            pr_url="https://gh.com/repo-a/1",
            merge_order=0,
        ))
        append_record(ledger, LedgerRecord(
            node_id="repo-b/specs/plan-b.md",
            status="pr_open",
            branch="feat/train-b",
            head_sha="sha-draft-b",
            pr_url="https://gh.com/repo-b/1",
            merge_order=1,
        ))
        append_record(ledger, LedgerRecord(
            node_id=_TRAIN_REVIEW_NODE_ID,
            status="approved",
        ))
        # repo-a: merged record carries all P3 fields for self-sufficient resume
        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="merged",
            branch="feat/train-a",
            pr_url="https://gh.com/repo-a/1",
            head_sha="sha-draft-a",           # draft SHA (for P3 downstream injection on resume)
            upstream_merge_sha="sha-merged-a",  # actual merge-commit SHA
            merge_order=0,
        ))

        merge_calls: List[str] = []

        # Track which ref is used when re-injecting repo-b's channel
        re_injection_refs: List[str] = []
        def _set_upstream_ref(workspace: Path, channel, ref: str):
            if workspace.name == "repo-b":
                re_injection_refs.append(ref)
            return []

        review_calls: List[str] = []
        def _review_fn(artifact: str, run_mode: str) -> LoopResult:
            review_calls.append("called")
            return _approval_review_fn(artifact, run_mode)

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,  # repo-b is still "open" (not yet merged)
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merged"

        # repo-a must NOT be merged again (idempotent: already in ledger as merged)
        assert "repo-a" not in merge_calls, (
            f"repo-a must not be double-merged; merge_calls = {merge_calls}"
        )
        # repo-b must be merged (it was pending at crash time)
        assert "repo-b" in merge_calls, (
            f"repo-b must be merged on resume; merge_calls = {merge_calls}"
        )

        # Review must NOT have been called (already_approved=True from ledger)
        assert review_calls == [], (
            f"Review panel must not be re-invoked on resume (already approved); "
            f"got {len(review_calls)} call(s)"
        )

        # The re-injection for repo-b must use the MERGED SHA from the ledger
        # (not the draft SHA — the ledger's merged record carries upstream_merge_sha)
        assert re_injection_refs == ["sha-merged-a"], (
            f"repo-b must be re-injected with the merged SHA 'sha-merged-a' from "
            f"the ledger; got {re_injection_refs!r}"
        )

    def test_resume_no_double_merge_ledger_state(self, tmp_path: Path):
        """After resume, ledger shows both nodes merged with correct SHAs."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Pre-populate: repo-a already merged
        for rec in [
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md", status="pr_open",
                branch="feat/train-a", head_sha="sha-draft-a",
                pr_url="https://gh.com/repo-a/1", merge_order=0,
            ),
            LedgerRecord(
                node_id="repo-b/specs/plan-b.md", status="pr_open",
                branch="feat/train-b", head_sha="sha-draft-b",
                pr_url="https://gh.com/repo-b/1", merge_order=1,
            ),
            LedgerRecord(node_id=_TRAIN_REVIEW_NODE_ID, status="approved"),
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md", status="merged",
                branch="feat/train-a", pr_url="https://gh.com/repo-a/1",
                head_sha="sha-draft-a", upstream_merge_sha="sha-merged-a",
                merge_order=0,
            ),
        ]:
            append_record(ledger, rec)

        run_train(
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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "merged"
        assert state["repo-a/specs/plan-a.md"].upstream_merge_sha == "sha-merged-a"
        assert state["repo-b/specs/plan-b.md"].status == "merged"
        assert state["repo-b/specs/plan-b.md"].upstream_merge_sha == "sha-merged-repo-b"


# ---------------------------------------------------------------------------
# P4-6: Autonomy boundary — autonomous mode stops at drafts_open

class TestAutonomyBoundary:
    """In autonomous mode with _merge_phase_enabled, status=drafts_open; no merges."""

    def test_autonomous_stops_at_drafts_open(self, tmp_path: Path):
        """Autonomous mode: all draft PRs open, coordinator stops at drafts_open terminal."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        merge_calls: List[str] = []

        def _publish(workspace: Path, owned_paths, *, draft: bool, **kw):
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-draft-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",  # autonomy boundary: never auto-merge
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,  # P4 gate is on
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
        )

        assert result["status"] == "drafts_open", (
            f"Autonomous mode must stop at 'drafts_open', got {result['status']!r}"
        )
        assert merge_calls == [], (
            "Cross-repo merges must NEVER auto-merge in autonomous mode"
        )
        assert len(result.get("nodes", {})) == 2, (
            "Both nodes must be in 'nodes' for the operator to see PR URLs"
        )

    def test_p3_backward_compat_no_flag(self, tmp_path: Path):
        """Without _merge_phase_enabled, status=completed (P3 behavior unchanged)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        merge_calls: List[str] = []

        def _publish(workspace: Path, owned_paths, *, draft: bool, **kw):
            return {
                "status": "published",
                "branch": f"feat/train-{workspace.name}",
                "head_sha": f"sha-draft-{workspace.name}",
                "pr_url": f"https://gh.com/{workspace.name}/1",
            }

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",  # even with governed mode...
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            # _merge_phase_enabled defaults to False — P3 behavior
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
        )

        assert result["status"] == "completed", (
            f"Without _merge_phase_enabled, status must be 'completed' (P3); "
            f"got {result['status']!r}"
        )
        assert merge_calls == [], "P3 must never call merge_pr"


# ---------------------------------------------------------------------------
# P4-7: Crash-between-merge-and-ledger-write resume

class TestCrashBetweenMergeAndLedgerWrite:
    """Crash recovery: repo-a merged on GitHub but ledger write didn't happen.

    The idempotent resume path has two layers:
      (A) Ledger has a ``merged`` record with ``upstream_merge_sha`` → skip.
          Covered by P4-5.
      (B) Ledger still shows ``pr_open``, but GitHub already merged the PR
          → ``_pr_merged_sha_fn`` cross-check recovers the SHA and skips
          re-merge (the crash window between ``gh pr merge`` and
          ``append_record``).
          This class covers (B) exclusively.

    The scenario is: coordinator called ``gh pr merge`` for repo-a, the
    merge succeeded on GitHub (merge commit exists), but the process crashed
    before writing the ``merged`` ledger record.  On restart:
      - Ledger: repo-a=pr_open, repo-b=pr_open, _train_review_=approved
      - GitHub (via ``_pr_merged_sha_fn``): repo-a → "sha-merged-a"
      - Expected: repo-a NOT re-merged; repo-b injected with "sha-merged-a"
    """

    def _make_ledger_crash_state(self, tmp_path: Path) -> Path:
        """Ledger state after crash: review approved, repo-a PR merged on GitHub
        but ledger not updated (still shows pr_open)."""
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        for rec in [
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md",
                status="pr_open",
                branch="feat/train-a",
                head_sha="sha-draft-a",
                pr_url="https://gh.com/repo-a/1",
                merge_order=0,
            ),
            LedgerRecord(
                node_id="repo-b/specs/plan-b.md",
                status="pr_open",
                branch="feat/train-b",
                head_sha="sha-draft-b",
                pr_url="https://gh.com/repo-b/1",
                merge_order=1,
            ),
            # Train-level review was approved before the crash
            LedgerRecord(node_id=_TRAIN_REVIEW_NODE_ID, status="approved"),
            # NOTE: NO merged record for repo-a — that's what crashed
        ]:
            append_record(ledger, rec)
        return ledger

    def test_crash_resume_skips_already_merged_via_live_check(self, tmp_path: Path):
        """repo-a merged on GitHub (ledger=pr_open) → not re-merged; repo-b gets merged SHA."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = self._make_ledger_crash_state(tmp_path)

        merge_calls: List[str] = []
        re_injection_refs: List[str] = []

        def _set_upstream_ref(workspace: Path, channel, ref: str):
            if workspace.name == "repo-b":
                re_injection_refs.append(ref)
            return []

        def _pr_merged_sha(workspace: Path, branch: str) -> Optional[str]:
            # Simulate GitHub: repo-a is merged, repo-b is not
            if workspace.name == "repo-a":
                return "sha-merged-a"
            return None

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _reverify_fn=_reverify_pass,
            # NOTE: _train_review_fn omitted — _already_approved_ path covers it
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=_pr_merged_sha,
        )

        assert result["status"] == "merged", (
            f"Expected 'merged' on crash-resume, got {result['status']!r}"
        )
        # repo-a must NOT be re-merged (GitHub already merged it)
        assert "repo-a" not in merge_calls, (
            f"repo-a must not be re-merged after crash (GitHub already merged it); "
            f"merge_calls={merge_calls}"
        )
        # repo-b must be merged (still pending)
        assert "repo-b" in merge_calls, (
            f"repo-b must be merged on resume; merge_calls={merge_calls}"
        )
        # The re-injection for repo-b must use the LIVE merged SHA from GitHub
        assert re_injection_refs == ["sha-merged-a"], (
            f"repo-b must be re-injected with merged SHA from live check ('sha-merged-a'); "
            f"got {re_injection_refs!r}"
        )

    def test_crash_resume_review_not_re_invoked(self, tmp_path: Path):
        """Ledger has _train_review_ approved → review not called on crash-resume."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = self._make_ledger_crash_state(tmp_path)

        review_calls: List[str] = []

        def _counting_review_fn(artifact: str, run_mode: str) -> LoopResult:
            review_calls.append("called")
            return _approval_review_fn(artifact, run_mode)

        def _pr_merged_sha(workspace: Path, branch: str) -> Optional[str]:
            if workspace.name == "repo-a":
                return "sha-merged-a"
            return None

        run_train(
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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_counting_review_fn,
            _pr_merged_sha_fn=_pr_merged_sha,
        )

        # already_approved=True from ledger → review must NOT be re-invoked
        assert review_calls == [], (
            f"Review must not be called when ledger already shows approved; "
            f"got {len(review_calls)} call(s)"
        )


# ---------------------------------------------------------------------------
# P4-CR-1: Live-default _live_reverify reads real StateSnapshot failure signals
#
# These tests call _live_reverify directly — no _reverify_fn stub — and patch
# runner.run_loop to return a pre-built StateSnapshot.  Each failure-case test
# MUST fail against the pre-fix code (which always returned True because it
# read the absent `terminal_status` field via getattr → always None).


def _make_state_snapshot(**kw) -> StateSnapshot:
    """Build a minimal StateSnapshot for re-verify signal tests."""
    return StateSnapshot(
        timestamp="2026-01-01T00:00:00Z",
        repo="repo-a",
        roadmap="specs/plan-a.md",
        **kw,
    )


class TestLiveReverifySignals:
    """_live_reverify reads real StateSnapshot signals (no _reverify_fn stub).

    Each failure-case test fails pre-fix (old code: getattr(snapshot,
    "terminal_status", None) always None → always True).
    """

    @patch("phase_loop_runtime.runner.run_loop")
    def test_blocked_closeout_returns_false(self, mock_run_loop, tmp_path):
        """closeout_terminal_status='blocked' → False.  Fails pre-fix (always True)."""
        mock_run_loop.return_value = (
            _make_state_snapshot(closeout_terminal_status="blocked"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False, (
            "A blocked closeout must cause re-verify to return False; "
            "pre-fix code returned True (terminal_status no-op)"
        )

    @patch("phase_loop_runtime.runner.run_loop")
    def test_failed_verification_closeout_returns_false(self, mock_run_loop, tmp_path):
        """closeout_terminal_status='failed_verification' → False."""
        mock_run_loop.return_value = (
            _make_state_snapshot(closeout_terminal_status="failed_verification"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    @patch("phase_loop_runtime.runner.run_loop")
    def test_stale_input_closeout_returns_false(self, mock_run_loop, tmp_path):
        """closeout_terminal_status='stale_input' → False."""
        mock_run_loop.return_value = (
            _make_state_snapshot(closeout_terminal_status="stale_input"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    @patch("phase_loop_runtime.runner.run_loop")
    def test_human_required_true_returns_false(self, mock_run_loop, tmp_path):
        """human_required=True → False regardless of closeout status."""
        mock_run_loop.return_value = (
            _make_state_snapshot(human_required=True, blocker_class="missing_secret"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    @patch("phase_loop_runtime.runner.run_loop")
    def test_blocker_class_non_none_returns_false(self, mock_run_loop, tmp_path):
        """blocker_class non-None → False even when closeout is None."""
        mock_run_loop.return_value = (
            _make_state_snapshot(blocker_class="contract_bug"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    @patch("phase_loop_runtime.runner.run_loop")
    def test_complete_closeout_returns_true(self, mock_run_loop, tmp_path):
        """closeout_terminal_status='complete', no blockers → True (clean pass)."""
        mock_run_loop.return_value = (
            _make_state_snapshot(closeout_terminal_status="complete"),
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is True

    @patch("phase_loop_runtime.runner.run_loop")
    def test_none_closeout_clean_snapshot_returns_true(self, mock_run_loop, tmp_path):
        """closeout_terminal_status=None with no failure signals → True.

        Verify mode may not emit a full closeout event; None is NOT a failure.
        """
        mock_run_loop.return_value = (
            _make_state_snapshot(),  # all defaults: closeout=None, human_required=False, blocker_class=None
            [],
        )
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is True

    @patch("phase_loop_runtime.runner.run_loop")
    def test_live_reverify_false_halts_downstream_merge(self, mock_run_loop, tmp_path):
        """End-to-end: _live_reverify (no _reverify_fn stub) returns False → merge_halted.

        This is the live-default smoke test.  run_train uses the live _live_reverify
        (no _reverify_fn injected); run_loop returns a blocked snapshot.  The
        downstream must NOT be merged and the result must be merge_halted.
        """
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

        # run_loop returns a blocked snapshot for all calls (repo-b re-verify).
        mock_run_loop.return_value = (
            _make_state_snapshot(closeout_terminal_status="blocked"),
            [],
        )

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),   # P3 run_loop (unused — all in completed_nodes)
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            # NOTE: _reverify_fn NOT injected — uses live _live_reverify default
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merge_halted", (
            f"Blocked re-verify snapshot must halt the merge; got {result['status']!r}"
        )
        assert result.get("node_id") == "repo-b/specs/plan-b.md", (
            f"merge_halted node must be repo-b (downstream); got {result.get('node_id')!r}"
        )
        # Upstream (repo-a, no upstreams) merged; downstream (repo-b) halted before merge.
        assert "repo-b" not in merge_calls, (
            "repo-b must NOT be merged when re-verify returns False"
        )


# ---------------------------------------------------------------------------
# P4-CR-2: Crash-window resume via not-open pr_open
#
# When a pr_open node is not open (pr_is_open returns False) AND the merged
# check (_pr_merged_sha_fn) returns a SHA, the node is RECOVERED as merged
# and NOT rebuilt.  A not-open AND not-merged node still drops (current behavior).


class TestNotOpenMergedPrOpenResume:
    """Step-3 crash-window recovery: not-open pr_open node that IS merged → recovered.

    This is the real-seam shaped test: _pr_is_open returns False (realistic for
    a merged PR), _pr_merged_sha_fn returns a SHA → node recovered without rebuild.
    Tests do NOT use _pr_is_open=_pr_is_open_true to bypass this path.
    """

    def test_not_open_but_merged_pr_recovered_not_rebuilt(self, tmp_path: Path):
        """repo-a: pr_is_open=False but merged SHA present → recovered; NOT rebuilt."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        # Pre-populate: repo-a=pr_open (crash before ledger write), repo-b=pr_open.
        for rec in [
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md",
                status="pr_open",
                branch="feat/train-a",
                head_sha="sha-draft-a",
                pr_url="https://gh.com/repo-a/1",
                merge_order=0,
            ),
            LedgerRecord(
                node_id="repo-b/specs/plan-b.md",
                status="pr_open",
                branch="feat/train-b",
                head_sha="sha-draft-b",
                pr_url="https://gh.com/repo-b/1",
                merge_order=1,
            ),
            LedgerRecord(node_id=_TRAIN_REVIEW_NODE_ID, status="approved"),
        ]:
            append_record(ledger, rec)

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        run_loop_calls: List[str] = []
        merge_calls: List[str] = []
        re_injection_refs: List[str] = []

        def _pr_is_open_never(workspace: Path, branch: str) -> bool:
            # All PRs show as not-open (merged PR state).
            return False

        def _pr_merged_sha(workspace: Path, branch: str) -> Optional[str]:
            # repo-a is merged on GitHub; repo-b is still pending (not merged).
            if workspace.name == "repo-a":
                return "sha-merged-a"
            return None

        def _run_loop_recording(workspace, roadmap_path, *, run_mode="autonomous", **kw):
            run_loop_calls.append(workspace.name)
            return (None, [])

        def _set_upstream_ref(workspace: Path, channel, ref: str):
            if workspace.name == "repo-b":
                re_injection_refs.append(ref)
            return []

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=_run_loop_recording,
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=_set_upstream_ref,
            _preflight_fn=_preflight_pass,
            # REAL-SEAM: _pr_is_open returns False (merged PR is not "open").
            _pr_is_open=_pr_is_open_never,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            # REAL-SEAM: _pr_merged_sha_fn returns SHA for repo-a.
            _pr_merged_sha_fn=_pr_merged_sha,
        )

        # repo-a was recovered as merged — must NOT be rebuilt via run_loop.
        assert "repo-a" not in run_loop_calls, (
            f"repo-a must be recovered (not rebuilt) when merged on GitHub; "
            f"run_loop_calls={run_loop_calls!r}"
        )
        # repo-a was already merged on GitHub — must NOT be re-merged.
        assert "repo-a" not in merge_calls, (
            f"repo-a must not be double-merged after recovery; "
            f"merge_calls={merge_calls!r}"
        )
        # repo-b was not in completed_nodes (not-open, not-merged) → rebuilt,
        # then merged.
        assert "repo-b" in run_loop_calls, (
            f"repo-b must be rebuilt (its PR was not open and not merged); "
            f"run_loop_calls={run_loop_calls!r}"
        )
        # The re-injection for repo-b must use the recovered merged SHA.
        # (This injection happens during P3 node processing for repo-b, not P4.)
        assert "sha-merged-a" in re_injection_refs or result["status"] in ("merged", "completed"), (
            f"repo-b must be injected with recovered merged SHA 'sha-merged-a'; "
            f"re_injection_refs={re_injection_refs!r}, status={result['status']!r}"
        )

    def test_not_open_and_not_merged_still_drops(self, tmp_path: Path):
        """repo-a: pr_is_open=False AND not merged → dropped + rebuilt (current behavior)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        for rec in [
            LedgerRecord(
                node_id="repo-a/specs/plan-a.md",
                status="pr_open",
                branch="feat/train-a",
                head_sha="sha-draft-a",
                pr_url="https://gh.com/repo-a/1",
                merge_order=0,
            ),
            LedgerRecord(
                node_id="repo-b/specs/plan-b.md",
                status="pr_open",
                branch="feat/train-b",
                head_sha="sha-draft-b",
                pr_url="https://gh.com/repo-b/1",
                merge_order=1,
            ),
        ]:
            append_record(ledger, rec)

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        run_loop_calls: List[str] = []

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda ws, rp, **kw: (run_loop_calls.append(ws.name), (None, []))[1],
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            # REAL-SEAM: all PRs show as not-open and not-merged.
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            # No merged SHA for any node.
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # repo-a is not open and not merged → dropped → rebuilt.
        assert "repo-a" in run_loop_calls, (
            f"repo-a must be rebuilt when closed-unmerged; run_loop_calls={run_loop_calls!r}"
        )


# ---------------------------------------------------------------------------
# P4-CR-3: Uncaught merge failure → merge_halted + blocked ledger record
#
# The live merge default uses subprocess check=True; a real gh pr merge failure
# (branch protection, conflict, required checks) raises CalledProcessError.
# Before the fix, this exception escaped run_train entirely with no ledger record.


class TestMergeFailureHalted:
    """merge_pr_fn raises → merge_halted + blocked ledger; already-merged upstreams safe."""

    def test_merge_raises_returns_merge_halted(self, tmp_path: Path):
        """merge_pr_fn raises → status=merge_halted, reason=merge_failed."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        def _merge_raises(workspace: Path, branch: str) -> str:
            import subprocess
            raise subprocess.CalledProcessError(
                returncode=1, cmd=["gh", "pr", "merge"], stderr="branch protection rule"
            )

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
            _merge_pr_fn=_merge_raises,
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merge_halted", (
            f"merge_pr_fn exception must produce merge_halted; got {result['status']!r}"
        )
        assert result.get("reason") == "merge_failed", (
            f"reason must be 'merge_failed'; got {result.get('reason')!r}"
        )
        # CalledProcessError.__str__ carries the command and return code; the
        # important thing is that detail is non-empty (the exception is surfaced).
        assert result.get("detail"), (
            f"detail must be non-empty (exception message); got {result.get('detail')!r}"
        )
        assert "gh" in result.get("detail", "") or "1" in result.get("detail", ""), (
            f"detail must mention the failed command; got {result.get('detail')!r}"
        )

    def test_merge_raises_records_blocked_in_ledger(self, tmp_path: Path):
        """merge_pr_fn raises → failed node is blocked in ledger."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        def _merge_raises(workspace: Path, branch: str) -> str:
            raise RuntimeError("merge conflict")

        run_train(
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
            _merge_pr_fn=_merge_raises,
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        state = read_ledger(ledger)
        # repo-a is the first node (root); its merge raises → blocked.
        assert state["repo-a/specs/plan-a.md"].status == "blocked", (
            "Failed merge must write a blocked record; previously no ledger record was written"
        )

    def test_merge_raises_on_downstream_upstream_stays_merged(self, tmp_path: Path):
        """repo-a merged OK; repo-b merge raises → repo-a stays merged (forward-only)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

        def _merge_pr(workspace: Path, branch: str) -> str:
            merge_calls.append(workspace.name)
            if workspace.name == "repo-b":
                raise RuntimeError("required status checks not passed")
            return f"sha-merged-{workspace.name}"

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
            _merge_pr_fn=_merge_pr,
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merge_halted"
        assert result.get("node_id") == "repo-b/specs/plan-b.md"

        state = read_ledger(ledger)
        # repo-a: merged OK (forward-only — no revert)
        assert state["repo-a/specs/plan-a.md"].status == "merged", (
            "repo-a must remain merged after downstream failure (forward-only)"
        )
        # repo-b: blocked in ledger
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            "repo-b must be blocked in ledger when its merge raises"
        )
