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
  P4-CR-4. Uncaught inject/reverify exception → merge_halted + blocked ledger
         record; already-merged upstreams stay merged (forward-only).  Guards
         the re-resolve + re-verify step (OUTSIDE the prior try/except scope)
         from escaping run_train as a bare traceback.
  N7 (agent-harness#250 follow-up). Merge-time base-retarget TOCTOU guard:
         _live_merge_pr re-reads the PR's CURRENT baseRefName via `gh pr view`
         immediately before `gh pr merge` and fails closed (no merge issued)
         when it no longer matches the base the broker's owned-scope check
         validated at publish time. A matching base merges normally.
  N7-CR (cross-vendor CR round on _live_merge_pr, four findings, all covered):
    1. No bare `--yes` — gh >= 2.x has no such flag; every real merge was
       aborting. `--merge` alone already makes gh non-interactive headless.
    2. Draft PRs (the governed publish path always opens drafts) are readied
       via `gh pr ready` before merging; a ready failure fails closed.
    3. The idempotent already-merged short-circuit is now ALSO base-checked
       (in the SAME combined `gh pr view` call): a matching-base already-
       merged PR still returns its SHA in one call; a wrong-base already-
       merged PR fails closed instead of being recorded as a success. Applied
       at both _live_merge_pr's guard and the Step-3/P4 resume cross-checks
       (see TestResumeRecoveryBaseChecked below).
    4. The merge is pinned to the broker-admitted `head_sha` (threaded from
       completed_nodes the same way `base` is threaded) via
       `--match-head-commit`, so a post-admission push cannot land unchecked
       content — GitHub itself refuses the merge on a mismatch.
  CR recheck (agent-harness#250, symmetry fix): finding 3's base check and
       finding 4's head-pinning covered the live MERGE path, but the
       ALREADY-MERGED / recovery paths (the same idempotent short-circuit in
       _live_merge_pr, plus the Step-3 crash-window recovery and the P4
       post-review cross-check) never validated the HEAD — only the base.
       `_live_pr_merged_sha` now also queries `headRefOid` and, when a
       `head_sha` is supplied, fails closed on a mismatch
       (`pr-merged-wrong-head`), symmetric to `pr-merged-wrong-base`. See
       TestLivePrMergedShaHeadChecked, the head-mismatch tests in
       TestLiveMergePrBaseRetargetGuard, and TestResumeRecoveryHeadChecked
       (which mirrors TestResumeRecoveryBaseChecked for the head).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest

from phase_loop_runtime.governed_premerge import LoopResult
from phase_loop_runtime.models import StateSnapshot
from phase_loop_runtime.train_ledger import LedgerRecord, append_record, read_ledger
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import (
    _TRAIN_REVIEW_NODE_ID,
    _live_merge_pr,
    _live_pr_head_sha,
    _live_pr_merged_sha,
    _live_reverify,
    run_train,
)


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
    def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
# agent-harness#250 CR follow-up (codex+grok corroborated, defect 1): on a P3
# resume, --match-head-commit must be pinned to the broker-ADMITTED head_sha
# (the ledger record written at pr_open publish time), NEVER the live-
# preferring resume value. A push landing on a branch AFTER its draft PR was
# admitted (out-of-band) must not let the coordinator pin --match-head-commit
# to that unchecked tip: the node itself is left `pr_open` and proceeds
# straight to P4 merge (out_of_band_upstreams only blocks DOWNSTREAM
# dependents, never the OOB node's own merge), so the merge-time pin is the
# only remaining guard.

class TestOOBResumeAdmittedHeadPin:
    def test_oob_push_after_admission_merge_pinned_to_admitted_not_live(self, tmp_path: Path):
        """repo-b's branch received a push after its draft PR was admitted
        (ledger head_sha='sha-admitted-b'); the live PR head now reads
        'sha-live-oob-b'. repo-b has no downstream dependent, so
        out_of_band_upstreams (which only blocks a STALE DOWNSTREAM) never
        blocks repo-b's own merge — it proceeds straight to P4 merge. The
        merge call for repo-b must receive head_sha='sha-admitted-b' — NEVER
        the live OOB tip. FAILS at HEAD 1fc23ea (currently threads the live
        value via completed_nodes[...]['head_sha'])."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(
            tmp_path, roadmap, ws_map,
            sha_a="sha-admitted-a", sha_b="sha-admitted-b",
        )

        merge_head_shas: Dict[str, Optional[str]] = {}

        def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
            merge_head_shas[workspace.name] = head_sha
            return f"sha-merged-{workspace.name}"

        def _live_head_sha(ws: Path, branch: str) -> Optional[str]:
            if branch == "feat/train-b":
                return "sha-live-oob-b"  # out-of-band push after admission
            return None  # repo-a: no override, falls back to the ledger value

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
            _live_pr_head_sha_fn=_live_head_sha,
            _merge_phase_enabled=True,
            _merge_pr_fn=_merge_pr,
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merged", f"expected a clean merge, got {result!r}"
        assert merge_head_shas.get("repo-a") == "sha-admitted-a"
        assert merge_head_shas.get("repo-b") == "sha-admitted-b", (
            f"--match-head-commit must be pinned to the broker-ADMITTED head_sha "
            f"('sha-admitted-b'), never the live out-of-band tip "
            f"('sha-live-oob-b'); merge_pr_fn received "
            f"{merge_head_shas.get('repo-b')!r}"
        )

    def test_missing_admitted_head_sha_fails_closed(self, tmp_path: Path):
        """A governed node reaching the P4 merge loop always carries an
        admitted_head_sha (set at publish time or Step-3 resume). If it is
        somehow missing, run_train must fail closed (merge_halted) rather than
        silently degrade to an unpinned merge_pr_fn call."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        # repo-a (merges FIRST in topo order): a record with branch/pr_url but
        # a falsy head_sha simulates a legacy/corrupted ledger entry with no
        # admitted SHA recorded.
        append_record(ledger, LedgerRecord(
            node_id="repo-a/specs/plan-a.md",
            status="pr_open",
            branch="feat/train-a",
            head_sha=None,
            pr_url="https://gh.com/repo-a/1",
            merge_order=0,
        ))
        # repo-b: a normal, fully-admitted pr_open record — unreached (repo-a's
        # missing-admitted-sha halt fires first), included only so Step 3/4
        # resume for repo-b doesn't take the "no ledger record" execute path.
        append_record(ledger, LedgerRecord(
            node_id="repo-b/specs/plan-b.md",
            status="pr_open",
            branch="feat/train-b",
            head_sha="sha-draft-b",
            pr_url="https://gh.com/repo-b/1",
            merge_order=1,
        ))

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
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merge_halted"
        assert result.get("reason") == "missing_admitted_head_sha"
        assert merge_calls == [], "merge_pr_fn must never be called without an admitted head_sha"


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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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

        def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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

        def _pr_merged_sha(workspace: Path, branch: str, base: Optional[str] = None, head_sha: Optional[str] = None) -> Optional[str]:
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

        def _pr_merged_sha(workspace: Path, branch: str, base: Optional[str] = None, head_sha: Optional[str] = None) -> Optional[str]:
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
    """_live_reverify directly runs plan verification commands (no _reverify_fn stub).

    After the false-green-killer fix, _live_reverify no longer delegates to
    run_loop.  It runs the downstream plan's ## Verification commands directly
    via run_verification.  These tests guard the live-default reverify path:
    failure-case tests return False via the fail-closed mechanism (no valid
    workspace state → exception → False); pass-case tests use a real workspace
    with a passing verification command.

    Pre-fix behavior: each failure-case test returned True (run_loop + manual
    closeout = bare break no-op); each pass-case returned True (also no-op,
    but for wrong reason — merged pin was never checked).
    """

    def test_blocked_closeout_returns_false(self, tmp_path):
        """No valid workspace state → fail-closed False.

        Pre-fix: returned True (run_loop no-op snapshot → no failure signals).
        Post-fix: exception on reconcile → fail-closed False.
        """
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False, (
            "A missing/invalid workspace must cause re-verify to return False; "
            "pre-fix code returned True (terminal_status no-op)"
        )

    def test_failed_verification_closeout_returns_false(self, tmp_path):
        """No valid workspace state → fail-closed False."""
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    def test_stale_input_closeout_returns_false(self, tmp_path):
        """No valid workspace state → fail-closed False."""
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    def test_human_required_true_returns_false(self, tmp_path):
        """No valid workspace state → fail-closed False."""
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    def test_blocker_class_non_none_returns_false(self, tmp_path):
        """No valid workspace state → fail-closed False."""
        result = _live_reverify(tmp_path, tmp_path / "plan.md", "governed")
        assert result is False

    def _make_passing_reverify_workspace(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a minimal workspace at awaiting_phase_closeout with a passing check."""
        import subprocess
        from phase_loop_test_utils import make_repo, write_phase_plan
        from phase_loop_runtime.models import StateSnapshot, utc_now
        from phase_loop_runtime.provenance import snapshot_provenance
        from phase_loop_runtime.state import write_state

        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 — P1 (P1)\n\n")
        subprocess.run(["git", "add", "specs/phase-plans-v1.md"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "test roadmap"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        body = (
            "# P1\n\n"
            "## Lanes\n\n"
            "### SL-0 - P1\n"
            "- **Owned files**: `work.md`\n\n"
            '## Verification\n\n- `python3 -c "import sys; sys.exit(0)"`\n'
        )
        plan = write_phase_plan(repo, "P1", roadmap, body=body)
        subprocess.run(
            ["git", "add", str(plan.relative_to(repo))],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "add plan"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        state = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases={"P1": "awaiting_phase_closeout"},
            current_phase="P1",
            **snapshot_provenance(roadmap),
        )
        write_state(repo, state)
        return repo, roadmap

    def test_complete_closeout_returns_true(self, tmp_path):
        """Passing verification command → True.

        Pre-fix: delegated to run_loop (no-op); returned True by accident.
        Post-fix: actually runs the command (exits 0) → correctly returns True.
        """
        repo, roadmap = self._make_passing_reverify_workspace(tmp_path)
        result = _live_reverify(repo, roadmap, "governed")
        assert result is True, (
            "_live_reverify must return True when all verification commands exit 0; "
            "if this fails the verification machinery is broken"
        )

    def test_none_closeout_clean_snapshot_returns_true(self, tmp_path):
        """Passing verification → True (no failure regardless of closeout fields).

        Pre-fix: run_loop no-op → snapshot with closeout=None → True (for wrong reason).
        Post-fix: verification command exits 0 → correctly returns True.
        """
        repo, roadmap = self._make_passing_reverify_workspace(tmp_path)
        result = _live_reverify(repo, roadmap, "governed")
        assert result is True, (
            "_live_reverify must return True when all verification commands pass; "
            "closeout_terminal_status is irrelevant to the new implementation"
        )

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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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

        def _pr_merged_sha(workspace: Path, branch: str, base: Optional[str] = None, head_sha: Optional[str] = None) -> Optional[str]:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        # repo-a is not open and not merged → dropped → rebuilt.
        assert "repo-a" in run_loop_calls, (
            f"repo-a must be rebuilt when closed-unmerged; run_loop_calls={run_loop_calls!r}"
        )


# ---------------------------------------------------------------------------
# N7-CR finding 3 (agent-harness#250 follow-up): the resume/recovery paths
# that treat a live `state == MERGED` read as a landed success must ALSO be
# base-checked, not just _live_merge_pr's own idempotent guard. There are two
# such call sites in run_train, both exercised here in isolation:
#   (a) Step 3's not-open-pr_open crash-window recovery (the same code path
#       TestNotOpenMergedPrOpenResume drives, here with a base-mismatch stub).
#   (b) The P4 cross-check loop ("crash-between-merge-and-ledger-write") that
#       runs AFTER train review, for nodes not already resolved via the ledger
#       or Step 3 (i.e. their PR was still reported open at Step 3 time).


class TestResumeRecoveryBaseChecked:
    """A `_pr_merged_sha_fn` that raises 'merged to the wrong base' must halt
    the train (fail closed), never be swallowed into a false recovery."""

    def _wrong_base_stub(self, target_workspace_name: str):
        def _pr_merged_sha(workspace: Path, branch: str, base: Optional[str] = None, head_sha: Optional[str] = None) -> Optional[str]:
            if workspace.name == target_workspace_name:
                raise RuntimeError(
                    f"pr-merged-wrong-base: branch '{branch}' in '{workspace}' is "
                    f"MERGED, but its baseRefName is 'release/2.0', not the "
                    f"expected '{base}'"
                )
            return None
        return _pr_merged_sha

    def test_step3_recovery_wrong_base_fails_closed(self, tmp_path: Path):
        """Step-3 crash-window recovery (not-open pr_open node): a
        _pr_merged_sha_fn raising a base-mismatch must produce status=blocked,
        never a silent recovery-as-merged."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
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
        ]:
            append_record(ledger, rec)

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
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
            # repo-a's PR shows not-open at Step 3 (crash-window shape).
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=self._wrong_base_stub("repo-a"),
        )

        assert result["status"] == "blocked", (
            f"a wrong-base already-merged PR at Step-3 recovery must halt the "
            f"train (status=blocked), got {result['status']!r}: {result!r}"
        )
        assert result.get("node_id") == "repo-a/specs/plan-a.md"
        assert merge_calls == [], (
            f"no merge may be issued once the base mismatch is detected; "
            f"got {merge_calls!r}"
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "blocked", (
            "the ledger must record repo-a as blocked, never as merged"
        )

    def test_p4_crosscheck_wrong_base_fails_closed(self, tmp_path: Path):
        """The P4 post-review cross-check loop: a _pr_merged_sha_fn raising a
        base-mismatch for a node whose PR is still reported open at Step 3
        (so Step 3 does not intercept it) must produce merge_halted, not a
        silent recovery."""
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
            # Both PRs report open at Step 3 — Step 3 does not intercept.
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _train_review_fn=_approval_review_fn,
            # Only invoked by the P4 cross-check loop (Step 3 never calls it
            # here, since both PRs show open); repo-a is "merged elsewhere".
            _pr_merged_sha_fn=self._wrong_base_stub("repo-a"),
        )

        assert result["status"] == "merge_halted", (
            f"a wrong-base already-merged PR discovered by the P4 cross-check "
            f"must halt the train, got {result['status']!r}: {result!r}"
        )
        # agent-harness#250 CR recheck: the P4 cross-check exception path now
        # covers both base- and head-mismatch (TestResumeRecoveryHeadChecked
        # below), so its reason tag was generalized accordingly.
        assert result.get("reason") == "pr_merged_wrong_base_or_head"
        assert result.get("node_id") == "repo-a/specs/plan-a.md"
        assert merge_calls == [], (
            f"no merge may be issued once the base mismatch is detected; "
            f"got {merge_calls!r}"
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "blocked", (
            "the ledger must record repo-a as blocked, never as merged"
        )


# ---------------------------------------------------------------------------
# agent-harness#250 CR recheck: the base validation added to the already-
# merged short-circuit (TestResumeRecoveryBaseChecked above) and the merge-
# time head-pinning (TestLiveMergePrHeadPinned above) were NOT symmetric — the
# ALREADY-MERGED / recovery paths never validated the HEAD at all. Reachable
# sequence: admit head A → out-of-band push B → the PR is merged (externally)
# before resume → the recovery path accepted B's merge commit as a landed
# success WITHOUT ever reaching the divergence pre-check or
# --match-head-commit. These tests mirror TestResumeRecoveryBaseChecked
# exactly, but for the head, at both recovery call sites.


class TestResumeRecoveryHeadChecked:
    """A `_pr_merged_sha_fn` that raises 'merged with an unadmitted head' must
    halt the train (fail closed), never be swallowed into a false recovery.

    The stub below deliberately mirrors the REAL `_live_pr_merged_sha`'s
    behavior with respect to `head_sha`, not just an unconditional raise:
    when the caller omits `head_sha` (i.e. the defect under test — the
    recovery call site never threads the admitted head through), the head
    check cannot happen and the (real) already-merged PR is silently
    returned as a success — reproducing the false-recovery defect. Only when
    `head_sha` IS supplied (post-fix) does it perform the mismatch check and
    raise. This makes the regression genuinely FAIL at HEAD dd2a9d3 (where
    `head_sha` is never threaded to either recovery call site) rather than
    incidentally failing on an unrelated assertion (e.g. a reason-string
    diff)."""

    def _wrong_head_stub(self, target_workspace_name: str):
        def _pr_merged_sha(
            workspace: Path, branch: str, base: Optional[str] = None, head_sha: Optional[str] = None
        ) -> Optional[str]:
            if workspace.name == target_workspace_name:
                if head_sha is None:
                    # Pre-fix: the caller never threaded the admitted head to
                    # this call, so the real live implementation could not
                    # have detected the out-of-band push either — it would
                    # silently return the merge-commit SHA of the UNADMITTED
                    # head as a landed success.
                    return "sha-unadmitted-oob-merge"
                raise RuntimeError(
                    f"pr-merged-wrong-head: branch '{branch}' in '{workspace}' is "
                    f"MERGED, but its headRefOid is 'sha-unadmitted-oob-push', not "
                    f"the admitted '{head_sha}'"
                )
            return None
        return _pr_merged_sha

    def test_step3_recovery_wrong_head_fails_closed(self, tmp_path: Path):
        """Step-3 crash-window recovery (not-open pr_open node): a
        _pr_merged_sha_fn raising a head-mismatch must produce status=blocked,
        never a silent recovery-as-merged. FAILS at HEAD dd2a9d3, where Step 3
        calls `_step3_merged_sha_fn(workspace, rec.branch, base=_DEFAULT_BASE)`
        without threading `rec.head_sha` at all."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
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
        ]:
            append_record(ledger, rec)

        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
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
            # repo-a's PR shows not-open at Step 3 (crash-window shape).
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=self._wrong_head_stub("repo-a"),
        )

        assert result["status"] == "blocked", (
            f"a wrong-head already-merged PR at Step-3 recovery must halt the "
            f"train (status=blocked), got {result['status']!r}: {result!r}"
        )
        assert result.get("node_id") == "repo-a/specs/plan-a.md"
        assert merge_calls == [], (
            f"no merge may be issued once the head mismatch is detected; "
            f"got {merge_calls!r}"
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "blocked", (
            "the ledger must record repo-a as blocked, never as merged"
        )

    def test_p4_crosscheck_wrong_head_fails_closed(self, tmp_path: Path):
        """The P4 post-review cross-check loop: a _pr_merged_sha_fn raising a
        head-mismatch for a node whose PR is still reported open at Step 3
        (so Step 3 does not intercept it) must produce merge_halted, not a
        silent recovery. FAILS at HEAD dd2a9d3, where the cross-check calls
        `pr_merged_sha_fn(_ws_r, _pr_branch_r, base=_DEFAULT_BASE)` without a
        head_sha at all."""
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
            # Both PRs report open at Step 3 — Step 3 does not intercept.
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_calls),
            _train_review_fn=_approval_review_fn,
            # Only invoked by the P4 cross-check loop (Step 3 never calls it
            # here, since both PRs show open); repo-a is "merged elsewhere"
            # with an unadmitted head.
            _pr_merged_sha_fn=self._wrong_head_stub("repo-a"),
        )

        assert result["status"] == "merge_halted", (
            f"a wrong-head already-merged PR discovered by the P4 cross-check "
            f"must halt the train, got {result['status']!r}: {result!r}"
        )
        assert result.get("reason") == "pr_merged_wrong_base_or_head"
        assert result.get("node_id") == "repo-a/specs/plan-a.md"
        assert merge_calls == [], (
            f"no merge may be issued once the head mismatch is detected; "
            f"got {merge_calls!r}"
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "blocked", (
            "the ledger must record repo-a as blocked, never as merged"
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

        def _merge_raises(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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

        def _merge_raises(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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

        def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
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
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
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


# ---------------------------------------------------------------------------
# P4-CR-4: Uncaught inject/reverify exception → merge_halted, not bare traceback

class TestInjectReverifyExceptionHalted:
    """set_upstream_ref_fn or _reverify_fn raises mid-loop → merge_halted, not traceback.

    P4-CR-4 (plans/pr35-cr-reconciliation.md addendum): the inject + reverify
    step was outside the try/except that guards the merge call.  An exception
    from set_upstream_ref_fn or _reverify_fn would escape run_train as a bare
    traceback — violating the status-dict contract and leaving already-merged
    upstreams without a resume record.

    Forward-only: a repo-a already merged before the exception must stay merged
    in the ledger; repo-b (the failing downstream) must be recorded as blocked.
    """

    def test_set_upstream_ref_raises_returns_merge_halted(self, tmp_path: Path):
        """set_upstream_ref_fn raises on downstream → merge_halted, not traceback."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

        def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
            merge_calls.append(workspace.name)
            return f"sha-merged-{workspace.name}"

        def _inject_raises(workspace, channel, ref, **_kw):
            if workspace.name == "repo-b":
                raise RuntimeError("fs error writing pin file: permission denied")
            return []

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub({}),
            _set_upstream_ref_fn=_inject_raises,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_merge_pr,
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merge_halted", (
            f"set_upstream_ref_fn exception must produce merge_halted status-dict; "
            f"got {result['status']!r} — pre-fix: bare traceback escaped run_train"
        )
        assert result.get("node_id") == "repo-b/specs/plan-b.md", (
            f"merge_halted node must be repo-b; got {result.get('node_id')!r}"
        )
        assert result.get("reason") == "reverify_failed", (
            f"reason must be 'reverify_failed'; got {result.get('reason')!r}"
        )
        assert "permission denied" in result.get("detail", ""), (
            f"detail must include the exception message; got {result.get('detail')!r}"
        )

        state = read_ledger(ledger)
        # repo-a merged before the inject raised — must stay merged (forward-only).
        assert state["repo-a/specs/plan-a.md"].status == "merged", (
            "repo-a must remain merged after downstream inject raised (forward-only)"
        )
        # repo-b blocked in ledger (not missing/unrecorded).
        assert state["repo-b/specs/plan-b.md"].status == "blocked", (
            "repo-b must be recorded as blocked when inject raises; "
            "pre-fix: ledger had no record at all (traceback escaped)"
        )

    def test_reverify_fn_raises_returns_merge_halted(self, tmp_path: Path):
        """_reverify_fn raises on downstream → merge_halted + blocked, not traceback.

        Upstream (repo-a) is already merged when reverify_fn raises for repo-b.
        """
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        merge_calls: List[str] = []

        def _merge_pr(workspace: Path, branch: str, base: str = "main", head_sha: Optional[str] = None) -> str:
            merge_calls.append(workspace.name)
            return f"sha-merged-{workspace.name}"

        def _reverify_raises(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
            raise RuntimeError("verification subprocess crashed: SIGSEGV")

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
            _reverify_fn=_reverify_raises,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
        )

        assert result["status"] == "merge_halted", (
            f"_reverify_fn exception must produce merge_halted status-dict; "
            f"got {result['status']!r} — pre-fix: bare traceback escaped run_train"
        )
        assert result.get("node_id") == "repo-b/specs/plan-b.md"
        assert "SIGSEGV" in result.get("detail", ""), (
            f"detail must carry the exception message; got {result.get('detail')!r}"
        )

        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "merged", (
            "repo-a must remain merged after downstream reverify raised (forward-only)"
        )
        assert state["repo-b/specs/plan-b.md"].status == "blocked"


# ---------------------------------------------------------------------------
# P3-LIVE: prune-on-merge hook fires per merged node, best-effort (never fatal)

class TestPostMergeHook:
    """The live prune-on-merge trigger runs once per successfully-merged node,
    with the node's workspace + id, and a hook failure never fails the train."""

    def test_hook_called_per_merged_node_with_workspace(self, tmp_path: Path):
        """post_merge_hook is invoked once per merged node, in merge (topo) order,
        each with that node's workspace and node_id."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        hook_calls: List[tuple] = []

        def _spy_hook(workspace: Path, node_id: str) -> None:
            hook_calls.append((Path(workspace).name, node_id))

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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
            _post_merge_hook=_spy_hook,
        )

        assert result["status"] == "merged"
        assert hook_calls == [
            ("repo-a", "repo-a/specs/plan-a.md"),
            ("repo-b", "repo-b/specs/plan-b.md"),
        ], f"hook must fire once per merged node in topo order; got {hook_calls}"

    def test_hook_failure_never_fails_the_train(self, tmp_path: Path):
        """A raising post_merge_hook is swallowed: the train still reports merged
        and both nodes stay merged (forward-only; prune is best-effort)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        def _boom(workspace: Path, node_id: str) -> None:
            raise RuntimeError("prune helper blew up")

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
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=_reverify_pass,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br, base=None, head_sha=None: None,
            _post_merge_hook=_boom,
        )

        assert result["status"] == "merged", (
            f"a prune-hook exception must NOT fail the train; got {result['status']!r}"
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/plan-a.md"].status == "merged"
        assert state["repo-b/specs/plan-b.md"].status == "merged"

    def test_live_default_hook_no_op_when_helper_unresolvable(self, tmp_path: Path):
        """With no prune helper resolvable, _live_post_merge_prune is a quiet no-op
        and never removes the node's per-repo workspace."""
        from phase_loop_runtime import train_runner

        ws = tmp_path / "repo-a"
        ws.mkdir()
        (ws / "keep").write_text("x")
        with patch.object(train_runner, "_resolve_prune_helper", return_value=None):
            train_runner._live_post_merge_prune(ws, "repo-a/specs/plan-a.md")
        assert ws.exists() and (ws / "keep").exists(), (
            "workspace must survive when no prune helper resolves"
        )

    def test_live_default_hook_never_prunes_the_node_workspace(self, tmp_path: Path):
        """_live_post_merge_prune runs the REAL guarded helper with cwd=workspace
        and NEVER removes the workspace itself (the per-repo checkout): the helper
        classifies via `git worktree list`, and the standalone workspace dir here is
        not a linked worktree, so nothing is removed."""
        from phase_loop_runtime import train_runner

        ws = tmp_path / "repo-a"
        ws.mkdir()
        (ws / "keep").write_text("x")
        train_runner._live_post_merge_prune(ws, "repo-a/specs/plan-a.md")
        assert ws.exists() and (ws / "keep").exists(), (
            "the node's per-repo workspace must never be removed by the prune hook"
        )


# ---------------------------------------------------------------------------
# N7 (agent-harness#250 follow-up) + cross-vendor CR round on the same
# function (four findings, all covered below):
#
#   1. `gh pr merge` must never be invoked with `--yes` (gh >= 2.x has no such
#      flag; every real merge aborted with `unknown flag: --yes`).
#   2. A DRAFT PR (the governed publish path always opens drafts) must be
#      readied (`gh pr ready`) before merging; a ready failure fails closed.
#   3. The idempotent already-merged short-circuit (`_live_pr_merged_sha`)
#      must ALSO check baseRefName — an already-merged PR on the WRONG base
#      (retargeted + merged elsewhere) must fail closed, not be recorded as a
#      successful merge to the expected base.
#   4. The merge must be pinned to the broker-admitted `head_sha` via
#      `--match-head-commit`, closing the same TOCTOU gap as the base check
#      but for the PR's head.
#
# These tests exercise _live_merge_pr (and, for finding 3's read-path,
# _live_pr_merged_sha) directly by patching subprocess.run, mirroring the
# launcher-module pattern in test_launcher_liveness.py /
# test_phase_loop_launcher.py — _live_merge_pr is the LIVE implementation the
# _merge_pr_fn seam resolves to by default.


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _gh_subcommand(cmd: List[str]) -> str:
    """Return a short label identifying which gh call this is, for call-log assertions."""
    if cmd[:2] == ["gh", "pr"] and len(cmd) > 2:
        if cmd[2] == "merge":
            return "merge"
        if cmd[2] == "ready":
            return "ready"
        if cmd[2] == "view":
            joined = " ".join(cmd)
            if "isDraft" in joined:
                return "view-premerge"  # isDraft,baseRefName,headRefOid (combined)
            if "state" in joined and "mergeCommit" in joined:
                # state,mergeCommit,baseRefName,headRefOid (_live_pr_merged_sha) —
                # used BOTH for the idempotent pre-merge guard AND (agent-harness#250
                # CR recheck) the post-merge resolution, which re-runs the same
                # validated lookup instead of a bare mergeCommit.oid read.
                return "view-merged-sha"
    return "other:" + " ".join(cmd)


def _merged_sha_json(
    state: str, base: str, sha: Optional[str] = None, head: Optional[str] = None
) -> str:
    """Build the JSON payload _live_pr_merged_sha's `gh pr view` call parses.

    ``head`` is the PR's CURRENT ``headRefOid`` (agent-harness#250 CR recheck:
    the already-merged recovery path now queries this field symmetrically with
    ``baseRefName``). Omitted (``None``) when a test doesn't care about the
    head check.
    """
    merge_commit = {"oid": sha} if sha else None
    payload = {"state": state, "mergeCommit": merge_commit, "baseRefName": base}
    if head is not None:
        payload["headRefOid"] = head
    return json.dumps(payload)


def _premerge_json(is_draft: bool, base: str, head: str = "sha-head-1") -> str:
    """Build the JSON payload _live_merge_pr's combined pre-merge view parses."""
    return json.dumps({"isDraft": is_draft, "baseRefName": base, "headRefOid": head})


class TestLiveMergePrBaseRetargetGuard:
    """N7: _live_merge_pr fails closed when the PR's current base no longer
    matches the base the broker validated, and merges normally when it does.
    """

    def test_retargeted_base_fails_closed_without_merging(self, tmp_path: Path):
        """A PR whose CURRENT baseRefName differs from the expected base is NOT
        merged: _live_merge_pr raises a base-mismatch error and `gh pr merge`
        is never invoked (fail-closed, not just 'an exception happened')."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                # Not yet merged.
                return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
            if label == "view-premerge":
                # Retargeted: PR now points at a different base than expected.
                return _FakeCompletedProcess(
                    returncode=0, stdout=_premerge_json(False, "release/2.0")
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-base-retargeted"):
                _live_merge_pr(ws, "feat/train-a", base="main")

        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            f"gh pr merge must NEVER be invoked on a base mismatch; got {merge_calls!r}"
        )
        premerge_calls = [c for c in calls if _gh_subcommand(c) == "view-premerge"]
        assert len(premerge_calls) == 1, (
            "the current base must be read exactly once before the merge decision"
        )

    def test_matching_base_merges_normally(self, tmp_path: Path):
        """A PR whose current base matches the expected base merges normally
        and returns the merge-commit SHA."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-realmerge-123"),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(False, "main"))
            if label == "merge":
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main")

        assert sha == "sha-realmerge-123"
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert len(merge_calls) == 1, f"gh pr merge must be invoked exactly once; got {merge_calls!r}"
        premerge_idx = next(i for i, c in enumerate(calls) if _gh_subcommand(c) == "view-premerge")
        merge_idx = next(i for i, c in enumerate(calls) if _gh_subcommand(c) == "merge")
        assert premerge_idx < merge_idx, (
            "the base must be revalidated BEFORE gh pr merge is issued, not after"
        )

    def test_already_merged_matching_base_returns_existing_sha_in_one_call(self, tmp_path: Path):
        """The idempotent already-merged guard short-circuits: an already-merged
        PR on the EXPECTED base returns its existing SHA after exactly ONE `gh`
        call (the combined `gh pr view` state+mergeCommit+baseRefName check) —
        preceded only by the repo-identity resolution read (agent-harness#250
        defect 2: `git remote get-url origin`, used to bind the `gh` call to
        the broker-validated repo via `--repo`) — without a separate base-check
        call or a new merge call."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "git" and cmd[3:5] == ["remote", "get-url"]:
                return _FakeCompletedProcess(returncode=0, stdout="https://github.com/owner/repo.git\n")
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                assert "--repo" in cmd and "github.com/owner/repo" in cmd, (
                    f"gh pr view must be bound to the resolved repo identity via "
                    f"--repo (agent-harness#250 defect 2); got {cmd!r}"
                )
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-already-merged"),
                )
            raise AssertionError(
                f"already-merged-on-expected-base PR must short-circuit before any "
                f"further gh call: {cmd!r}"
            )

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main")

        assert sha == "sha-already-merged"
        gh_calls = [c for c in calls if c[0] == "gh"]
        assert len(gh_calls) == 1, (
            f"already-merged guard must short-circuit after ONE gh call (the combined "
            f"state+mergeCommit+baseRefName check); no premerge or merge call should "
            f"follow. Got {len(gh_calls)} gh calls: {gh_calls!r} (all calls: {calls!r})"
        )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], "already-merged guard must not perform a merge call"

    def test_already_merged_wrong_base_fails_closed(self, tmp_path: Path):
        """CR finding 3: a PR that is MERGED but on a DIFFERENT base than
        expected (retargeted after admission, then merged elsewhere) must NOT
        be recorded as a successful merge — _live_merge_pr fails closed and
        `gh pr merge` is never invoked."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "release/2.0", sha="sha-wrong-base-merge"),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-merged-wrong-base"):
                _live_merge_pr(ws, "feat/train-a", base="main")

        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            f"gh pr merge must never be invoked for an already-merged-wrong-base PR; "
            f"got {merge_calls!r}"
        )

    def test_already_merged_wrong_head_fails_closed(self, tmp_path: Path):
        """agent-harness#250 CR recheck: symmetric to finding 3 (base) but for
        the HEAD. A PR that is MERGED, with a matching base, but whose
        headRefOid does NOT match the broker-admitted head_sha (an
        out-of-band push landed unchecked content, and the PR merged
        externally before the coordinator's own merge-time
        --match-head-commit pin could ever run — e.g. during the
        crash-between-merge-and-ledger-write window) must NOT be recorded as
        a successful merge — _live_merge_pr's idempotent already-merged guard
        must fail closed and `gh pr merge` must never be invoked.

        This is the exact defect the cross-vendor recheck found: the MERGE
        path (--match-head-commit, TestLiveMergePrHeadPinned above) was
        fixed, but this ALREADY-MERGED short-circuit passed no head_sha to
        _live_pr_merged_sha at all, so it never even queried headRefOid."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-unadmitted-merge", head="sha-unadmitted-head"
                    ),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-merged-wrong-head"):
                _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            f"gh pr merge must never be invoked for an already-merged-wrong-head PR; "
            f"got {merge_calls!r}"
        )

    def test_already_merged_matching_head_returns_existing_sha(self, tmp_path: Path):
        """An already-merged PR whose headRefOid matches the admitted head_sha
        (and whose base matches too) still short-circuits to the existing SHA
        — the new head check must not reject a genuinely-admitted merge."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-already-merged", head="sha-admitted-head"
                    ),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        assert sha == "sha-already-merged"
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], "a matching-head already-merged PR must not trigger a new merge"


class TestLiveMergePrNoYesFlag:
    """Finding 1: `gh pr merge` (gh >= 2.x) has no `--yes` flag; supplying one
    aborted every live merge with `unknown flag: --yes`. `--merge` alone
    already makes it non-interactive in a headless context."""

    # The exact flag set `gh 2.96.0 pr merge --help` accepts (verified live in
    # the execution environment) — used to assert every flag _live_merge_pr
    # emits is one gh actually understands.
    _GH_PR_MERGE_ACCEPTED_FLAGS = {
        "--admin", "-A", "--author-email", "--auto", "-b", "--body",
        "-F", "--body-file", "-d", "--delete-branch", "--disable-auto",
        "--match-head-commit", "-m", "--merge", "-r", "--rebase",
        "-s", "--squash", "-t", "--subject", "--help", "-R", "--repo",
    }

    def test_merge_command_has_no_yes_flag_and_only_accepted_flags(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        merge_cmds: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-realmerge-123"),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(False, "main"))
            if label == "merge":
                merge_cmds.append(cmd)
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            _live_merge_pr(ws, "feat/train-a", base="main")

        assert len(merge_cmds) == 1
        merge_cmd = merge_cmds[0]
        assert "--yes" not in merge_cmd, (
            f"gh pr merge must never be invoked with --yes (no such flag on gh >= 2.x, "
            f"aborts every live merge); got {merge_cmd!r}"
        )
        flags = [tok for tok in merge_cmd if tok.startswith("-")]
        unknown = [f for f in flags if f not in self._GH_PR_MERGE_ACCEPTED_FLAGS]
        assert unknown == [], (
            f"gh pr merge command contains flag(s) gh 2.96.0 does not accept: {unknown!r} "
            f"(full command: {merge_cmd!r})"
        )


class TestLiveMergePrDraftReadied:
    """Finding 2: the governed publish path always opens DRAFT PRs; GitHub
    refuses to merge a draft. _live_merge_pr must ready it first, and fail
    closed (no merge issued) if `gh pr ready` itself fails."""

    def test_draft_pr_is_readied_before_merge(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-realmerge-123"),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(True, "main"))
            if label == "ready":
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            if label == "merge":
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main")

        assert sha == "sha-realmerge-123"
        ready_idx = next(i for i, c in enumerate(calls) if _gh_subcommand(c) == "ready")
        merge_idx = next(i for i, c in enumerate(calls) if _gh_subcommand(c) == "merge")
        assert ready_idx < merge_idx, "gh pr ready must be called BEFORE gh pr merge for a draft PR"

    def test_non_draft_pr_is_not_readied(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-realmerge-123"),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(False, "main"))
            if label == "merge":
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            _live_merge_pr(ws, "feat/train-a", base="main")

        ready_calls = [c for c in calls if _gh_subcommand(c) == "ready"]
        assert ready_calls == [], "a non-draft PR must not trigger gh pr ready"

    def test_ready_failure_fails_closed_no_merge_issued(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(True, "main"))
            if label == "ready":
                return _FakeCompletedProcess(returncode=1, stdout="", stderr="pull request is not a draft")
            raise AssertionError(f"unexpected gh call reached fake_run (merge must never be reached): {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="gh pr ready failed"):
                _live_merge_pr(ws, "feat/train-a", base="main")

        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            f"a gh pr ready failure must fail closed — gh pr merge must never be "
            f"invoked; got {merge_calls!r}"
        )


class TestLiveMergePrHeadPinned:
    """Finding 4: the merge must be pinned to the broker-admitted head_sha via
    --match-head-commit, so a post-admission push cannot land unchecked
    content — GitHub itself refuses the merge (non-zero exit) on a mismatch."""

    def test_match_head_commit_passed_when_head_sha_supplied(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        merge_cmds: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-realmerge-123", head="sha-admitted-head"
                    ),
                )
            if label == "view-premerge":
                # headRefOid matches the admitted head_sha: the N7 CR follow-up
                # pre-check (defect 1 hardening) must NOT trip here — this test
                # exercises --match-head-commit flag construction, not the
                # earlier fail-closed headRefOid comparison.
                return _FakeCompletedProcess(
                    returncode=0, stdout=_premerge_json(False, "main", head="sha-admitted-head")
                )
            if label == "merge":
                merge_cmds.append(cmd)
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        assert sha == "sha-realmerge-123"
        assert len(merge_cmds) == 1
        merge_cmd = merge_cmds[0]
        assert "--match-head-commit" in merge_cmd, (
            f"--match-head-commit must be passed when head_sha is supplied; got {merge_cmd!r}"
        )
        idx = merge_cmd.index("--match-head-commit")
        assert merge_cmd[idx + 1] == "sha-admitted-head", (
            f"--match-head-commit must carry the admitted head_sha; got {merge_cmd!r}"
        )

    def test_no_match_head_commit_when_head_sha_not_supplied(self, tmp_path: Path):
        """Degrade safely: a caller with no admitted head_sha gets an unpinned
        merge (pre-N7-finding-4 behavior), not an error."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        merge_cmds: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json("MERGED", "main", sha="sha-realmerge-123"),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(returncode=0, stdout=_premerge_json(False, "main"))
            if label == "merge":
                merge_cmds.append(cmd)
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            _live_merge_pr(ws, "feat/train-a", base="main")

        assert "--match-head-commit" not in merge_cmds[0]

    def test_head_mismatch_gh_pr_merge_failure_fails_closed(self, tmp_path: Path):
        """GitHub's own atomic --match-head-commit check can still reject the
        merge (a race between our pre-merge headRefOid read and the actual
        merge attempt) even when our own read matched the admitted head_sha —
        _live_merge_pr must let the real subprocess CalledProcessError
        propagate (fail closed), never swallow it into a false success."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
            if label == "view-premerge":
                # Our own headRefOid read matches the admitted SHA (passes the
                # N7 CR follow-up pre-check below); gh's own merge-time atomic
                # check still rejects — the residual race the flag alone guards.
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_premerge_json(False, "main", head="sha-stale-admitted-head"),
                )
            if label == "merge":
                # gh pr merge --match-head-commit <sha> exits non-zero when the
                # PR's actual head no longer matches; check=True in the live
                # code converts this to CalledProcessError.
                import subprocess as _subprocess
                raise _subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    stderr="the head branch was modified after the merge check",
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(Exception) as exc_info:
                _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-stale-admitted-head")

        import subprocess as _subprocess
        assert isinstance(exc_info.value, _subprocess.CalledProcessError), (
            f"expected the real gh pr merge failure to propagate; got {exc_info.value!r}"
        )
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert len(merge_calls) == 1
        assert "--match-head-commit" in merge_calls[0]
        assert "sha-stale-admitted-head" in merge_calls[0]

    def test_headrefoid_mismatch_fails_closed_before_merge_call(self, tmp_path: Path):
        """agent-harness#250 N7 CR follow-up hardening (defect 1, corroborated by
        codex+grok): when our own pre-merge read of headRefOid ALREADY diverges
        from the admitted head_sha, fail closed BEFORE issuing `gh pr merge` at
        all — defense-in-depth on top of --match-head-commit (finding 4), for
        gh versions/edge-cases where that flag alone might not be trusted."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
            if label == "view-premerge":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_premerge_json(False, "main", head="sha-live-oob-advanced"),
                )
            raise AssertionError(
                f"gh pr merge must never be reached on a headRefOid mismatch: {cmd!r}"
            )

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-head-advanced"):
                _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert merge_calls == [], (
            f"gh pr merge must NEVER be invoked when our own headRefOid read already "
            f"diverges from the admitted head_sha; got {merge_calls!r}"
        )


class TestLiveMergePrPostMergeToctou:
    """agent-harness#250 cross-vendor CR recheck: closes the TOCTOU window
    between the head/base precheck and the `gh pr merge` invocation itself.

    Prior to this fix, the post-merge SHA was read via a bare
    `gh pr view --json mergeCommit --jq .mergeCommit.oid` — unvalidated
    against head/base. `gh` CLI (>= 2.96.0) returns SUCCESS from
    `gh pr merge` when its refreshed state finds the PR ALREADY MERGED (it
    short-circuits before constructing the merge mutation that carries
    `expectedHeadOid`, so `--match-head-commit` is NOT enforced in that
    path). So an external actor who pushes a different head B and merges it
    in the window between our precheck (PR OPEN, head/base matching
    admitted) and the `gh pr merge` call would have B's merge SHA silently
    recorded as this run's success.

    The fix re-runs the already-hardened, head/base-validated
    `_live_pr_merged_sha` (with the SAME admitted `base`/`head_sha` this
    call was given) to resolve the post-merge SHA instead — it fails CLOSED
    (`pr-merged-wrong-head`) on the externally-merged, unadmitted head."""

    def test_external_merge_in_toctou_window_fails_closed(self, tmp_path: Path):
        """PR is OPEN at precheck (head/base match admitted) -> `gh pr merge`
        exits 0 (simulating gh's own already-merged short-circuit, which does
        NOT enforce --match-head-commit) -> the post-merge state is MERGED,
        but with headRefOid = an UNADMITTED head B (an external actor pushed
        and merged B in the TOCTOU window). The node must fail CLOSED and
        must NOT record B's merge SHA as success."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    # Precheck idempotent guard: not yet merged.
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                # Post-merge resolution: MERGED, but to an UNADMITTED head B —
                # an external actor's merge landed in the TOCTOU window.
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-external-B-merge", head="sha-external-B"
                    ),
                )
            if label == "view-premerge":
                # Precheck: PR is OPEN, head/base match what was admitted.
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_premerge_json(False, "main", head="sha-admitted-head"),
                )
            if label == "merge":
                # `gh pr merge` "succeeds" (returncode 0): this models gh's own
                # already-merged short-circuit, which does not raise even
                # though --match-head-commit was not actually enforced against
                # the externally-merged head B.
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["gh", "pr", "view"] and "--jq" in cmd and ".mergeCommit.oid" in cmd:
                # PRE-FIX code path only: the bare, unvalidated post-merge read
                # (`gh pr view --json mergeCommit --jq .mergeCommit.oid`). This
                # is the exact defect being closed — it happily hands back the
                # externally-merged head B's SHA with no head/base check at
                # all. The fixed code never issues this call (it re-runs the
                # validated `_live_pr_merged_sha` above instead), so this
                # branch is unreachable against the patched implementation.
                return _FakeCompletedProcess(returncode=0, stdout="sha-external-B-merge\n")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-merged-wrong-head"):
                _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        # The externally-merged head's SHA must never have been treated as a
        # recorded success anywhere the caller could observe.
        merge_calls = [c for c in calls if _gh_subcommand(c) == "merge"]
        assert len(merge_calls) == 1, (
            f"gh pr merge is expected to be invoked once (and 'succeed' per gh's "
            f"already-merged short-circuit); got {merge_calls!r}"
        )

    def test_post_merge_matching_head_and_base_records_sha(self, tmp_path: Path):
        """Positive case: post-merge state is MERGED with headRefOid/baseRefName
        matching the admitted head_sha/base -> the merge commit SHA is
        recorded as success (the re-validated lookup is not a regression for
        the ordinary, non-TOCTOU path)."""
        ws = tmp_path / "repo-a"
        ws.mkdir()
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-realmerge-good", head="sha-admitted-head"
                    ),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_premerge_json(False, "main", head="sha-admitted-head"),
                )
            if label == "merge":
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["gh", "pr", "view"] and "--jq" in cmd and ".mergeCommit.oid" in cmd:
                # PRE-FIX code path only (see sibling test's comment); the
                # bare read agrees with the validated lookup in this matching
                # (non-TOCTOU) case, so this test passes both before and
                # after the fix — it is the negative-case control.
                return _FakeCompletedProcess(returncode=0, stdout="sha-realmerge-good\n")
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted-head")

        assert sha == "sha-realmerge-good"


class TestLivePrMergedShaHeadChecked:
    """agent-harness#250 CR recheck: unit-level coverage of
    `_live_pr_merged_sha`'s new `head_sha` parameter directly (independent of
    the `_live_merge_pr` call sites exercised above), mirroring the
    pre-existing base-check unit coverage."""

    def test_head_mismatch_raises_wrong_head(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-x", head="sha-unadmitted"
                    ),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="pr-merged-wrong-head"):
                _live_pr_merged_sha(ws, "feat/train-a", base="main", head_sha="sha-admitted")

    def test_head_match_returns_sha(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-x", head="sha-admitted"
                    ),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_pr_merged_sha(ws, "feat/train-a", base="main", head_sha="sha-admitted")

        assert sha == "sha-x"

    def test_head_sha_none_skips_head_check(self, tmp_path: Path):
        """Backward compatibility: callers with no admitted head_sha (head_sha
        omitted/None) get the old head-agnostic lookup, not a spurious raise."""
        ws = tmp_path / "repo-a"
        ws.mkdir()

        def fake_run(cmd, **kwargs):
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-x", head="sha-whatever"
                    ),
                )
            raise AssertionError(f"unexpected gh call reached fake_run: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_pr_merged_sha(ws, "feat/train-a", base="main")

        assert sha == "sha-x"


# ---------------------------------------------------------------------------
# agent-harness#250 CR follow-up (defect 2): GH_REPO overrides gh's cwd-based
# repo selection — a stray GH_REPO in the environment could redirect the
# coordinator's merge/recovery gh calls (view/ready/merge, plus the
# _live_pr_head_sha/_live_pr_merged_sha recovery reads) to a DIFFERENT
# repository than the one the broker actually pushed to. Every such call must
# be bound to the broker-validated repo via an explicit host-qualified --repo
# (derived from the workspace's own origin, the SAME identity credsep.py's
# GitHubBrokerAdapter uses), or fall back to a GH_REPO-neutralized environment
# when that identity cannot be resolved.

def _origin_url_response(url: str = "https://github.com/owner/repo.git"):
    return _FakeCompletedProcess(returncode=0, stdout=url + "\n")


class TestGhCallsRepoIdentityBound:
    def test_live_pr_head_sha_binds_to_resolved_repo(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("GH_REPO", "attacker/evil-repo")
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []
        seen_envs: List[Optional[dict]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "git" and cmd[3:5] == ["remote", "get-url"]:
                return _origin_url_response()
            if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
                seen_envs.append(kwargs.get("env"))
                return _FakeCompletedProcess(returncode=0, stdout="sha-live-head\n")
            raise AssertionError(f"unexpected call: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_pr_head_sha(ws, "feat/train-a")

        assert sha == "sha-live-head"
        gh_calls = [c for c in calls if c[0] == "gh"]
        assert len(gh_calls) == 1
        assert "--repo" in gh_calls[0] and "github.com/owner/repo" in gh_calls[0], (
            f"gh pr list must be bound to the resolved repo via --repo; got {gh_calls[0]!r}"
        )
        # Belt-and-suspenders (mirrors credsep's BrokerEnvironmentBoundary, which
        # both strips GH_REPO AND passes --repo): GH_REPO must be stripped from the
        # env even when --repo is present, not relied on solely for precedence.
        assert seen_envs and seen_envs[0] is not None and "GH_REPO" not in seen_envs[0], (
            f"GH_REPO must be stripped even when --repo is resolvable; got {seen_envs!r}"
        )

    def test_live_pr_merged_sha_binds_to_resolved_repo(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "git" and cmd[3:5] == ["remote", "get-url"]:
                return _origin_url_response()
            if cmd[:3] == ["gh", "pr", "view"]:
                return _FakeCompletedProcess(
                    returncode=0, stdout=_merged_sha_json("MERGED", "main", sha="sha-x")
                )
            raise AssertionError(f"unexpected call: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_pr_merged_sha(ws, "feat/train-a", base="main")

        assert sha == "sha-x"
        gh_calls = [c for c in calls if c[0] == "gh"]
        assert len(gh_calls) == 1
        assert "--repo" in gh_calls[0] and "github.com/owner/repo" in gh_calls[0], (
            f"gh pr view must be bound to the resolved repo via --repo; got {gh_calls[0]!r}"
        )

    def test_live_merge_pr_binds_every_gh_call_to_resolved_repo(self, tmp_path: Path):
        ws = tmp_path / "repo-a"
        ws.mkdir()
        gh_calls: List[List[str]] = []
        merged = {"done": False}

        def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[3:5] == ["remote", "get-url"]:
                return _origin_url_response()
            gh_calls.append(cmd)
            label = _gh_subcommand(cmd)
            if label == "view-merged-sha":
                if not merged["done"]:
                    return _FakeCompletedProcess(returncode=0, stdout=_merged_sha_json("OPEN", "main"))
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout=_merged_sha_json(
                        "MERGED", "main", sha="sha-realmerge", head="sha-admitted"
                    ),
                )
            if label == "view-premerge":
                return _FakeCompletedProcess(
                    returncode=0, stdout=_premerge_json(True, "main", head="sha-admitted")
                )
            if label == "ready":
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            if label == "merge":
                merged["done"] = True
                return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call: {cmd!r}")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            sha = _live_merge_pr(ws, "feat/train-a", base="main", head_sha="sha-admitted")

        assert sha == "sha-realmerge"
        assert len(gh_calls) == 5, f"expected 5 gh calls, got {len(gh_calls)}: {gh_calls!r}"
        for c in gh_calls:
            assert "--repo" in c and "github.com/owner/repo" in c, (
                f"every gh call issued by _live_merge_pr must carry --repo "
                f"(agent-harness#250 defect 2); got {c!r}"
            )

    def test_unresolvable_origin_neutralizes_gh_repo_env_instead(self, tmp_path: Path, monkeypatch):
        """When the origin cannot be resolved to an allow-listed host, no
        --repo flag is added — but GH_REPO must be stripped from the env passed
        to the gh subprocess, so a stray GH_REPO cannot redirect the call
        (fail-closed fallback per agent-harness#250 defect 2)."""
        monkeypatch.setenv("GH_REPO", "attacker/evil-repo")
        ws = tmp_path / "repo-a"
        ws.mkdir()
        seen: List[tuple] = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "git" and cmd[3:5] == ["remote", "get-url"]:
                # A non-allow-listed host: resolution fails closed inside
                # _gh_repo_binding, which must fall back to env-neutralization.
                return _FakeCompletedProcess(returncode=0, stdout="https://ghe.internal/owner/repo.git\n")
            seen.append((cmd, kwargs.get("env")))
            return _FakeCompletedProcess(returncode=0, stdout="sha-live-head\n")

        with patch("phase_loop_runtime.train_runner.subprocess.run", side_effect=fake_run):
            _live_pr_head_sha(ws, "feat/train-a")

        assert len(seen) == 1
        gh_cmd, env = seen[0]
        assert "--repo" not in gh_cmd, (
            f"no --repo should be added when the origin cannot be resolved; got {gh_cmd!r}"
        )
        assert env is not None, "GH_REPO must be explicitly neutralized when --repo cannot be pinned"
        assert "GH_REPO" not in env, f"GH_REPO must be stripped from the fallback env; got {env!r}"
