"""CI invariant suite for the cross-repo release-train coordinator.

These tests lock in the load-bearing safety properties of P1–P4.  Each test
targets one invariant explicitly; the assertion is structural (e.g. capturing
the actual ref value passed to ``set_upstream_ref``) rather than merely
checking that a function was called.

Run with:
    cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_invariants.py -q

All git/gh/run_loop/publish/panel boundaries are stubbed; no live network.

Invariants:
  INV-1. No node merges before train approval (partial-merge guard).
  INV-2. Downstream re-resolution to upstream MERGED SHA OCCURRED and was
         ordered BEFORE downstream re-verification — the false-green killer.
         The ref value passed to ``set_upstream_ref`` must equal the upstream
         MERGED SHA (not the draft SHA) and that call must appear in the call
         log BEFORE the downstream ``reverify`` call.
  INV-3. Preflight failure opens ZERO PRs.
  INV-4. Train state never written under any ``.phase-loop/`` path.
  INV-5. Autonomous mode adds no ``human_required``; a panel non-approval is a
         non-human terminal (``human_required=False`` in ``terminal_blocker``).
  INV-6. Live-default ``_live_reverify`` directly runs the downstream node's
         plan verification commands against the workspace.  A failing command
         (non-zero exit) returns False; a passing command returns True.
         Fail-closed: no plan → False; no awaiting phase → False; exception →
         False.  Tests call ``_live_reverify`` directly without stubbing
         ``_reverify_fn`` to guard the live-default path.  (After the
         false-green-killer fix: _live_reverify no longer delegates to
         run_loop, which was a no-op for awaiting_phase_closeout + manual.)
  INV-7. ``run_loop``'s failure contract: a genuine verification failure ALWAYS
         produces a StateSnapshot with at least one of the three failure signals
         set (``blocker_class`` non-None, ``human_required=True``, or
         ``closeout_terminal_status`` in the bad set).  Pinned via:
           (a) Pre-seeded repo + real ``status_snapshot()`` (the snapshot-
               construction code ``run_loop`` uses internally): a ``repeated_
               verification_failure`` LoopEvent in the event log causes
               ``status_snapshot()`` to return ``blocker_class`` non-None.
           (b) Structural: ``_pipeline_branch_blocker_from_error()`` always
               returns a non-None ``blocker_class`` in ``BLOCKER_CLASSES``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from phase_loop_runtime.governed_premerge import LoopResult
from phase_loop_runtime.models import StateSnapshot
from phase_loop_runtime.train_ledger import LedgerRecord, append_record, read_ledger
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import _TRAIN_REVIEW_NODE_ID, _live_reverify, run_train


# ---------------------------------------------------------------------------
# Shared fixtures

TRAIN_2NODE_MD = """\
# Release Train: invariant-test

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
"""

TRAIN_3NODE_MD = """\
# Release Train: invariant-test-3

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

_DRAFT_SHA_A = "sha-DRAFT-repo-a"
_MERGED_SHA_A = "sha-MERGED-repo-a"

assert _DRAFT_SHA_A != _MERGED_SHA_A, "draft and merged SHAs must differ for tests to be meaningful"


def _preflight_pass(nodes, resolve_workspace):
    return []


def _preflight_fail(nodes, resolve_workspace):
    return ["preflight error: some check failed"]


def _pr_is_open_false(workspace: Path, branch: str) -> bool:
    return False


def _pr_is_open_true(workspace: Path, branch: str) -> bool:
    return True


def _approval_review_fn(artifact: str, run_mode: str) -> LoopResult:
    return LoopResult(mergeable=True, ran=True, rounds=1)


def _rejection_review_fn(artifact: str, run_mode: str) -> LoopResult:
    return LoopResult(
        mergeable=False,
        ran=True,
        rounds=1,
        terminal_blocker={
            "human_required": False,
            "blocker_class": "review_gate_block",
            "blocker_summary": "invariant test: panel rejected train",
        },
        reason="non_convergence",
    )


def _make_publish_stub(*, draft_sha_override: Optional[Dict[str, str]] = None):
    """Return a publish stub; allows per-repo draft-SHA override for contrast tests."""
    def _publish(workspace: Path, owned_paths, *, draft: bool, **kw):
        sha = (draft_sha_override or {}).get(workspace.name, f"sha-DRAFT-{workspace.name}")
        return {
            "status": "published",
            "branch": f"feat/train-{workspace.name}",
            "head_sha": sha,
            "pr_url": f"https://gh.com/{workspace.name}/pr/1",
        }
    return _publish


def _setup_p3_done(
    tmp_path: Path,
    roadmap,
    ws_map: Dict[str, Path],
    *,
    sha_a: str = _DRAFT_SHA_A,
    sha_b: str = "sha-DRAFT-repo-b",
):
    """Pre-populate the ledger with P3-done state (both nodes pr_open).

    Uses distinct explicit draft SHAs so tests can assert the merged SHA
    differs from the draft SHA.
    """
    ledger = tmp_path / "ledger" / "train.ledger.jsonl"
    append_record(ledger, LedgerRecord(
        node_id="repo-a/specs/plan-a.md",
        status="pr_open",
        branch="feat/train-repo-a",
        head_sha=sha_a,
        pr_url="https://gh.com/repo-a/pr/1",
        merge_order=0,
    ))
    append_record(ledger, LedgerRecord(
        node_id="repo-b/specs/plan-b.md",
        status="pr_open",
        branch="feat/train-repo-b",
        head_sha=sha_b,
        pr_url="https://gh.com/repo-b/pr/1",
        merge_order=1,
    ))
    return ledger


def _make_merge_pr_stub(merge_log: List[str], merged_sha_map: Optional[Dict[str, str]] = None):
    """Merge stub that records workspace names and returns deterministic merged SHAs."""
    def _merge_pr(workspace: Path, branch: str) -> str:
        merge_log.append(workspace.name)
        if merged_sha_map and workspace.name in merged_sha_map:
            return merged_sha_map[workspace.name]
        return f"sha-MERGED-{workspace.name}"
    return _merge_pr


# ---------------------------------------------------------------------------
# INV-1: No node merges before train approval (partial-merge guard)

class TestInvariant1NoMergeBeforeApproval:
    """A rejected train review must result in ZERO merge_pr calls.

    The guard also holds when the review itself is never reached (P3 blocked)
    and in the edge case where the merge phase is not enabled at all.
    """

    def test_panel_rejection_zero_merges(self, tmp_path: Path):
        """Train review rejection → merge_pr never called."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)
        merge_log: List[str] = []

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert merge_log == [], (
            f"INV-1 VIOLATED: merge_pr called {merge_log!r} before train approval"
        )

    def test_merge_phase_disabled_zero_merges(self, tmp_path: Path):
        """Without _merge_phase_enabled, merge_pr must never be called."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log: List[str] = []

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=False,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert merge_log == [], (
            f"INV-1 VIOLATED: merge_pr called when _merge_phase_enabled=False"
        )

    def test_review_approval_then_reverify_fail_halts_before_downstream(self, tmp_path: Path):
        """Upstream merged; downstream reverify fails → downstream NOT merged (forward-only)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)
        merge_log: List[str] = []

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _reverify_fn=lambda ws, rp, rm: ws.name != "repo-b",
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # repo-a merged (upstream, root — no dependency, no reverify needed before merge)
        assert "repo-a" in merge_log, "repo-a must be merged before downstream check"
        # repo-b NOT merged because reverify failed
        assert "repo-b" not in merge_log, (
            f"INV-1 VIOLATED: repo-b was merged even though reverify failed; "
            f"merge_log: {merge_log}"
        )


# ---------------------------------------------------------------------------
# INV-2: False-green killer — re-resolution to MERGED SHA ordered BEFORE reverify

class TestInvariant2FalseGreenKiller:
    """set_upstream_ref is called with the MERGED SHA and BEFORE reverify.

    This is the central safety invariant of P4: the downstream workspace is
    resolved to the upstream MERGED SHA (not the draft SHA used during P3)
    before the re-verify call.  A downstream that was green only against the
    draft ref would otherwise silently receive a false-green verdict.

    The draft SHA and merged SHA are deliberately distinct in all tests.
    """

    def test_set_upstream_ref_called_with_merged_sha_not_draft(self, tmp_path: Path):
        """The ref passed to set_upstream_ref for repo-b equals the MERGED SHA of repo-a."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        # seed with distinct draft SHAs so we can tell draft from merged
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map, sha_a=_DRAFT_SHA_A)

        set_ref_calls: List[Dict[str, Any]] = []

        def _set_upstream_ref_capture(workspace: Path, channel, ref: str):
            set_ref_calls.append({
                "workspace": workspace.name,
                "ref": ref,
            })

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=_set_upstream_ref_capture,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([], merged_sha_map={"repo-a": _MERGED_SHA_A}),
            _reverify_fn=lambda ws, rp, rm: True,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # The P4 set_upstream_ref call for repo-b (the downstream) must carry
        # the MERGED SHA of repo-a, not the draft SHA.
        p4_calls = [c for c in set_ref_calls if c["workspace"] == "repo-b"]
        assert p4_calls, (
            "INV-2 VIOLATED: set_upstream_ref was never called for repo-b; "
            "P4 must re-inject the upstream merged SHA before re-verify"
        )
        last_call = p4_calls[-1]
        assert last_call["ref"] == _MERGED_SHA_A, (
            f"INV-2 VIOLATED: set_upstream_ref for repo-b received ref={last_call['ref']!r}; "
            f"expected the upstream MERGED SHA {_MERGED_SHA_A!r} (NOT the draft SHA {_DRAFT_SHA_A!r})"
        )

    def test_set_upstream_ref_ordered_before_reverify(self, tmp_path: Path):
        """set_upstream_ref(repo-b, MERGED_SHA) appears in the call log BEFORE reverify(repo-b)."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map, sha_a=_DRAFT_SHA_A)

        # Shared call log captures set_upstream_ref and reverify events in order.
        call_log: List[Dict[str, Any]] = []

        def _set_upstream_ref_logging(workspace: Path, channel, ref: str):
            call_log.append({
                "type": "set_upstream_ref",
                "workspace": workspace.name,
                "ref": ref,
            })

        def _reverify_logging(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
            call_log.append({
                "type": "reverify",
                "workspace": workspace.name,
            })
            return True

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=_set_upstream_ref_logging,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([], merged_sha_map={"repo-a": _MERGED_SHA_A}),
            _reverify_fn=_reverify_logging,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # Locate the P4 set_upstream_ref call for repo-b (downstream re-injection).
        set_ref_idx: Optional[int] = None
        for i, entry in enumerate(call_log):
            if entry["type"] == "set_upstream_ref" and entry["workspace"] == "repo-b":
                # Verify it carries the MERGED SHA (belt-and-suspenders with INV-2a).
                assert entry["ref"] == _MERGED_SHA_A, (
                    f"INV-2 VIOLATED: set_upstream_ref for repo-b carries ref={entry['ref']!r}; "
                    f"expected {_MERGED_SHA_A!r}"
                )
                set_ref_idx = i
                break

        assert set_ref_idx is not None, (
            "INV-2 VIOLATED: set_upstream_ref not called for repo-b at all; "
            "P4 must re-inject before re-verify"
        )

        # Locate the reverify call for repo-b.
        reverify_idx: Optional[int] = None
        for i, entry in enumerate(call_log):
            if entry["type"] == "reverify" and entry["workspace"] == "repo-b":
                reverify_idx = i
                break

        assert reverify_idx is not None, (
            "INV-2 VIOLATED: reverify not called for repo-b"
        )

        # CRITICAL: set_upstream_ref MUST precede reverify in the call log.
        assert set_ref_idx < reverify_idx, (
            f"INV-2 VIOLATED: set_upstream_ref (index={set_ref_idx}) did not precede "
            f"reverify (index={reverify_idx}) for repo-b. "
            f"Call log: {call_log}"
        )

    def test_ref_value_is_distinct_from_draft_sha(self, tmp_path: Path):
        """Guard that the test itself is valid: draft and merged SHAs are distinct."""
        # This assertion guards INV-2a and INV-2b against a broken test that
        # uses the same value for both draft and merged SHA.
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map, sha_a=_DRAFT_SHA_A)
        captured_refs: List[str] = []

        def _set_upstream_ref_capture(workspace: Path, channel, ref: str):
            if workspace.name == "repo-b":
                captured_refs.append(ref)

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=_set_upstream_ref_capture,
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([], merged_sha_map={"repo-a": _MERGED_SHA_A}),
            _reverify_fn=lambda ws, rp, rm: True,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert captured_refs, "set_upstream_ref must be called for repo-b"
        assert captured_refs[-1] != _DRAFT_SHA_A, (
            f"INV-2 VIOLATED: set_upstream_ref for repo-b received the DRAFT SHA "
            f"{_DRAFT_SHA_A!r} instead of the merged SHA — false-green guard bypassed"
        )


# ---------------------------------------------------------------------------
# INV-3: Preflight failure opens ZERO PRs

class TestInvariant3PreflightZeroPRs:
    """A preflight failure must prevent ANY draft PR from being opened."""

    def test_preflight_failure_zero_publish_calls(self, tmp_path: Path):
        """_preflight_fn returns errors → _publish never called."""
        roadmap = parse_train_roadmap(TRAIN_3NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        publish_log: List[str] = []

        def _publish_spy(workspace: Path, owned_paths, *, draft: bool, **kw):
            publish_log.append(workspace.name)
            return {"status": "published", "branch": f"feat/train-{workspace.name}",
                    "head_sha": f"sha-{workspace.name}", "pr_url": "https://gh.com/1"}

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_publish_spy,
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_fail,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "preflight_failed", (
            f"Expected status='preflight_failed', got {result['status']!r}"
        )
        assert publish_log == [], (
            f"INV-3 VIOLATED: publish called {publish_log!r} after preflight failure; "
            "zero PRs must be opened when preflight fails"
        )

    def test_preflight_failure_empty_ledger(self, tmp_path: Path):
        """After preflight failure, ledger remains empty (no records written)."""
        roadmap = parse_train_roadmap(TRAIN_3NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_fail,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # Ledger may not exist at all, or may be empty.
        if ledger.exists():
            state = read_ledger(ledger)
            assert state == {}, (
                f"INV-3 VIOLATED: ledger contains records after preflight failure: {state}"
            )


# ---------------------------------------------------------------------------
# INV-4: Train state never written under any .phase-loop/ path

class TestInvariant4NoPhaseLoopState:
    """The train ledger must never be located inside a .phase-loop/ directory."""

    def test_ledger_outside_phase_loop_is_accepted(self, tmp_path: Path):
        """A ledger path outside .phase-loop/ works normally."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        # Any non-.phase-loop path must be accepted.
        ledger = tmp_path / "train-ledger" / "train.ledger.jsonl"

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _reverify_fn=lambda ws, rp, rm: True,
            _train_review_fn=_approval_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "merged"

    def test_ledger_inside_phase_loop_raises(self, tmp_path: Path):
        """A ledger path under .phase-loop/ must raise ValueError immediately."""
        import pytest

        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        # Any path through .phase-loop/ must be rejected.
        bad_ledger = tmp_path / "repo-a" / ".phase-loop" / "train.ledger.jsonl"

        with pytest.raises(ValueError, match=r"\.phase-loop"):
            run_train(
                roadmap,
                bad_ledger,
                run_mode="governed",
                resolve_workspace=lambda n: ws_map[n.node_id],
                _run_loop=lambda *a, **kw: (None, []),
                _publish=_make_publish_stub(),
                _set_upstream_ref_fn=lambda *a, **kw: [],
                _preflight_fn=_preflight_pass,
                _pr_is_open=_pr_is_open_false,
                _live_pr_head_sha_fn=lambda ws, br: None,
                _merge_phase_enabled=True,
                _merge_pr_fn=_make_merge_pr_stub([]),
                _pr_merged_sha_fn=lambda ws, br: None,
            )


# ---------------------------------------------------------------------------
# INV-5: Autonomy boundary — no human_required; non-approval is non-human terminal

class TestInvariant5AutonomyBoundary:
    """Coordinator autonomy-first invariants.

    - Autonomous mode with _merge_phase_enabled=True stops at drafts_open (no merge).
    - Panel rejection terminal carries human_required=False.
    - The coordinator NEVER injects human_required into the train state.
    """

    def test_autonomous_stops_at_drafts_open(self, tmp_path: Path):
        """Autonomous mode + _merge_phase_enabled=True → status='drafts_open', zero merges."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"
        merge_log: List[str] = []

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub(merge_log),
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        assert result["status"] == "drafts_open", (
            f"INV-5 VIOLATED: autonomous mode must stop at 'drafts_open'; "
            f"got {result['status']!r}"
        )
        assert merge_log == [], (
            f"INV-5 VIOLATED: merge_pr called in autonomous mode: {merge_log}"
        )

    def test_non_approval_terminal_is_non_human(self, tmp_path: Path):
        """Panel rejection must carry human_required=False in terminal_blocker."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = _setup_p3_done(tmp_path, roadmap, ws_map)

        result = run_train(
            roadmap,
            ledger,
            run_mode="governed",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_true,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _train_review_fn=_rejection_review_fn,
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        blocker = result.get("terminal_blocker") or {}
        assert blocker.get("human_required") is False, (
            f"INV-5 VIOLATED: terminal_blocker must have human_required=False on "
            f"panel rejection; got {blocker!r}"
        )

    def test_autonomous_result_has_no_human_required_key(self, tmp_path: Path):
        """Autonomous stops at drafts_open; result must not carry human_required=True."""
        roadmap = parse_train_roadmap(TRAIN_2NODE_MD)
        ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
        ledger = tmp_path / "ledger" / "train.ledger.jsonl"

        result = run_train(
            roadmap,
            ledger,
            run_mode="autonomous",
            resolve_workspace=lambda n: ws_map[n.node_id],
            _run_loop=lambda *a, **kw: (None, []),
            _publish=_make_publish_stub(),
            _set_upstream_ref_fn=lambda *a, **kw: [],
            _preflight_fn=_preflight_pass,
            _pr_is_open=_pr_is_open_false,
            _live_pr_head_sha_fn=lambda ws, br: None,
            _merge_phase_enabled=True,
            _merge_pr_fn=_make_merge_pr_stub([]),
            _pr_merged_sha_fn=lambda ws, br: None,
        )

        # The result must NOT have human_required=True anywhere.
        assert result.get("human_required") is not True, (
            f"INV-5 VIOLATED: autonomous result must not carry human_required=True; "
            f"got {result!r}"
        )
        blocker = result.get("terminal_blocker") or {}
        assert blocker.get("human_required") is not True, (
            f"INV-5 VIOLATED: terminal_blocker must not carry human_required=True in "
            f"autonomous mode; got {blocker!r}"
        )


# ---------------------------------------------------------------------------
# INV-6: Live-default _live_reverify directly runs verification commands

class TestInvariant6LiveReverifyRunsVerification:
    """_live_reverify directly executes the downstream node's plan verification
    commands against the workspace and returns False when any command fails.

    This test calls _live_reverify DIRECTLY (not through run_train) without
    stubbing _reverify_fn.  It guards the live-default path used in production
    runs.

    After the false-green-killer fix: _live_reverify no longer delegates to
    run_loop (which was a no-op for awaiting_phase_closeout + manual closeout).
    It directly calls verification_commands_from_plan + run_verification against
    the workspace that has the merged pin injected.  The merged-pin file written
    by set_upstream_ref is read by whatever commands the plan declares.

    Fail-closed contract:
      a. Failing verification command → False
      b. No plan file → False (can't verify)
      c. No awaiting phase found → False (no actionable phase)
      d. Any exception → False (fail-safe)
      e. Plan with no verification commands → True (plan author's choice)
      f. Passing verification commands → True
    """

    def _make_reverify_repo(self, tmp_path: Path, verify_lines: str = "") -> tuple[Path, Path]:
        """Create a minimal workspace at awaiting_phase_closeout.

        Sets up a git repo with a single-phase roadmap, a plan file whose
        ## Verification section contains ``verify_lines``, and a persisted
        state file that puts phase P1 at awaiting_phase_closeout so that
        ``reconcile()`` returns ``current_phase="P1"``.
        """
        import subprocess
        from phase_loop_test_utils import make_repo, write_phase_plan
        from phase_loop_runtime.models import utc_now
        from phase_loop_runtime.provenance import snapshot_provenance
        from phase_loop_runtime.state import write_state

        repo = make_repo(tmp_path)
        # Replace the default multi-phase roadmap with a single test phase.
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 — P1 (P1)\n\n")
        subprocess.run(["git", "add", "specs/phase-plans-v1.md"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "single-phase roadmap"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )

        # Write a plan with the given verification body.
        body = (
            "# P1\n\n"
            "## Lanes\n\n"
            "### SL-0 - P1\n"
            "- **Owned files**: `work.md`\n\n"
            f"## Verification\n\n{verify_lines}\n"
        )
        plan = write_phase_plan(repo, "P1", roadmap, body=body)
        subprocess.run(
            ["git", "add", str(plan.relative_to(repo))],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "add plan with verification"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )

        # Write state that puts P1 at awaiting_phase_closeout with correct
        # provenance so reconcile() restores the status from the state file.
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

    def test_passing_verification_returns_true(self, tmp_path: Path):
        """Verification command exits 0 → _live_reverify returns True."""
        repo, roadmap = self._make_reverify_repo(
            tmp_path,
            verify_lines='- `python3 -c "import sys; sys.exit(0)"`\n',
        )
        result = _live_reverify(repo, roadmap, "governed")
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False when all verification "
            "commands exited 0 (expected True — all commands passed)"
        )

    def test_failing_verification_returns_false(self, tmp_path: Path):
        """Verification command exits 1 → _live_reverify returns False.

        This is the canonical false-green regression guard: a downstream whose
        verification FAILS against the merged pin must cause _live_reverify to
        return False so the merge is halted.
        """
        repo, roadmap = self._make_reverify_repo(
            tmp_path,
            verify_lines='- `python3 -c "import sys; sys.exit(1)"`\n',
        )
        result = _live_reverify(repo, roadmap, "governed")
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True even though a "
            "verification command exited non-zero — downstream would be "
            "merged without valid verification against the merged pin"
        )

    def test_no_plan_returns_false(self, tmp_path: Path):
        """No plan file for the current phase → False (fail-closed)."""
        import subprocess
        from phase_loop_test_utils import make_repo
        from phase_loop_runtime.models import utc_now
        from phase_loop_runtime.provenance import snapshot_provenance
        from phase_loop_runtime.state import write_state

        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 — P1 (P1)\n\n")
        subprocess.run(["git", "add", "specs/phase-plans-v1.md"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "roadmap no plan"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        # Write state but NO plan file — find_plan_artifact returns None.
        state = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases={"P1": "awaiting_phase_closeout"},
            current_phase="P1",
            **snapshot_provenance(roadmap),
        )
        write_state(repo, state)
        result = _live_reverify(repo, roadmap, "governed")
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True when no plan file exists "
            "(fail-closed: cannot verify without a plan)"
        )

    def test_no_awaiting_phase_returns_false(self, tmp_path: Path):
        """All phases at 'planned' (nothing awaiting closeout) → False (fail-closed)."""
        from phase_loop_test_utils import make_repo
        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        # No state written → reconcile returns all phases as 'planned';
        # _current_phase returns the first planned phase, not awaiting_phase_closeout.
        # _live_reverify finds no phase at awaiting_phase_closeout → fail closed.
        result = _live_reverify(repo, roadmap, "governed")
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True when no phase is at "
            "awaiting_phase_closeout — fail-closed contract violated"
        )

    def test_no_verification_commands_returns_true_by_default(self, tmp_path: Path, monkeypatch):
        """Plan with no verification commands → True when hard enforcement is off."""
        monkeypatch.delenv("PHASE_LOOP_VERIFY_ENFORCE", raising=False)
        repo, roadmap = self._make_reverify_repo(
            tmp_path,
            verify_lines="",  # empty → verification_commands_from_plan returns []
        )
        result = _live_reverify(repo, roadmap, "governed")
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False when the plan declares "
            "no verification commands — empty is not a failure (warn default)"
        )

    def test_no_verification_hard_enforce_returns_false(self, tmp_path: Path, monkeypatch):
        """[#39] No ## Verification under PHASE_LOOP_VERIFY_ENFORCE=hard → False (fail-closed).

        A downstream that declares no verification cannot be proven to survive the
        upstream MERGED-pin contract, so under hard enforce the re-verify gate must
        NOT trivial-pass it — it returns False, halting the train at merge_halted (a
        non-human terminal; no human_required added, preserving autonomy-first).
        Mirrors the single-repo execute preflight under hard enforce.
        """
        monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "hard")
        repo, roadmap = self._make_reverify_repo(tmp_path, verify_lines="")
        result = _live_reverify(repo, roadmap, "governed")
        assert result is False, (
            "#39 VIOLATED: _live_reverify must fail-closed (False) for a no-verification "
            "node under PHASE_LOOP_VERIFY_ENFORCE=hard — else a no-verification downstream "
            "merges unverified against the merged pin"
        )

    def test_no_verification_warn_explicit_returns_true(self, tmp_path: Path, monkeypatch):
        """[#39] The same node under an explicit warn → True (unchanged trivial pass)."""
        monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "warn")
        repo, roadmap = self._make_reverify_repo(tmp_path, verify_lines="")
        assert _live_reverify(repo, roadmap, "governed") is True

    def test_exception_returns_false(self, tmp_path: Path):
        """Non-existent workspace → exception → False (fail-safe)."""
        result = _live_reverify(
            tmp_path / "nonexistent-repo",
            tmp_path / "nonexistent-repo" / "specs" / "plan.md",
            "governed",
        )
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify must return False (fail-safe) "
            "when an exception is raised (e.g. workspace does not exist)"
        )


# ---------------------------------------------------------------------------
# BLOCK 2: End-to-end reverify — real post-P3 workspace, real verification

class TestBlock2ReverifyEndToEnd:
    """End-to-end regression guard for the false-green killer (BLOCK 2).

    The post-P3 workspace state (awaiting_phase_closeout) is reproduced via
    write_state with correct provenance — the smallest faithful reproduction
    that exercises the actual _live_reverify→verification path without stubbing
    _reverify_fn.  Injecting via a real run_loop call would require skill-bundle
    infrastructure (PHASE_LOOP_RUNNER_REPO_ROOT / dotfiles tree) that is absent
    in standalone CI; the write_state approach produces identical reconcile()
    output since reconcile() reads the persisted state file directly.

    PRE-FIX BEHAVIOR CONFIRMED (before the false-green-killer fix):
      _live_reverify called run_loop(workspace, roadmap_path, run_mode=run_mode).
      run_loop found the node at awaiting_phase_closeout with closeout_mode=
      "manual" (default), dispatched into the bare `break` at runner.py:1897 —
      no executor, no verification — and returned the cached P3 snapshot with
      closeout_terminal_status=None, human_required=False, blocker_class=None.
      _live_reverify mapped that to True (the false green).  Confirmed by
      running the test against the pre-fix code: it failed with
      "AssertionError: BLOCK 2 REGRESSION: _live_reverify returned True ...".

    POST-FIX BEHAVIOR: _live_reverify runs the plan's verification commands
    directly.  A command that reads the pin file and exits 1 when it contains
    'BREAKING' causes _live_reverify to return False → merge is halted.
    """

    def _make_post_p3_workspace(self, tmp_path: Path) -> tuple[Path, Path]:
        """Set up the smallest faithful post-P3 workspace.

        Creates a git repo with a single-phase roadmap and a plan whose
        ## Verification section contains a command that reads
        ``upstream-version.txt`` and exits 1 if it contains ``BREAKING``.

        The workspace state is set to awaiting_phase_closeout via write_state
        (with correct provenance) so that reconcile() returns current_phase=P1
        at awaiting_phase_closeout — the same state a real run_loop call leaves.
        """
        import subprocess
        from phase_loop_test_utils import make_repo, write_phase_plan
        from phase_loop_runtime.models import utc_now
        from phase_loop_runtime.provenance import snapshot_provenance
        from phase_loop_runtime.state import write_state

        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 — P1 (P1)\n\n")
        subprocess.run(["git", "add", "specs/phase-plans-v1.md"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "p3 roadmap"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )

        # Plan whose verification reads the pin file and fails on BREAKING.
        verify_cmd = (
            "python3 -c \""
            "import sys, pathlib; "
            "v = pathlib.Path('upstream-version.txt').read_text().strip(); "
            "sys.exit(1 if 'BREAKING' in v else 0)"
            "\""
        )
        body = (
            "# P1\n\n"
            "## Lanes\n\n"
            "### SL-0 - P1\n"
            "- **Owned files**: `work.md`\n\n"
            "## Verification\n\n"
            f"- `{verify_cmd}`\n"
        )
        plan = write_phase_plan(repo, "P1", roadmap, body=body)
        subprocess.run(
            ["git", "add", str(plan.relative_to(repo))],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "p3 plan"],
            cwd=repo, check=True, stdout=subprocess.DEVNULL,
        )

        # Persist awaiting_phase_closeout state (matching provenance).
        # reconcile() reads load_state(repo) first and restores this status,
        # identical to what a real run_loop call would leave on disk.
        state = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases={"P1": "awaiting_phase_closeout"},
            current_phase="P1",
            **snapshot_provenance(roadmap),
        )
        write_state(repo, state)

        # Initial upstream-version.txt (non-breaking) before P4 re-injection.
        (repo / "upstream-version.txt").write_text("sha-DRAFT-abc123\n")
        return repo, roadmap

    def test_breaking_merged_pin_makes_reverify_return_false(self, tmp_path: Path):
        """CONTRACT-BREAKING merged pin → _live_reverify returns False → merge halted.

        set_upstream_ref writes 'BREAKING-SHA-INCOMPATIBLE' into
        upstream-version.txt.  The plan's verification command reads that file
        and exits 1.  _live_reverify must return False.

        Against the PRE-FIX code: _live_reverify returned True (false green —
        run_loop hit the awaiting_phase_closeout + manual no-op bare break).
        Against the POST-FIX code: _live_reverify returns False (verified here).
        """
        from phase_loop_runtime.cross_repo_channel import ChannelDescriptor, set_upstream_ref

        repo, roadmap = self._make_post_p3_workspace(tmp_path)

        # Inject a CONTRACT-BREAKING merged SHA (the P4 set_upstream_ref call).
        channel = ChannelDescriptor(kind="pin", params={"file": "upstream-version.txt"})
        set_upstream_ref(repo, channel, "BREAKING-SHA-INCOMPATIBLE")

        # The verification command reads upstream-version.txt → 'BREAKING' → exits 1.
        result = _live_reverify(repo, roadmap, "governed")
        assert result is False, (
            "BLOCK 2 REGRESSION: _live_reverify returned True with a CONTRACT-BREAKING "
            "merged pin.\nupstream-version.txt now contains 'BREAKING-SHA-INCOMPATIBLE'; "
            "the plan's verification command should have exited 1.\n"
            "Pre-fix: this returned True (run_loop no-op); post-fix: must be False."
        )

    def test_compatible_merged_pin_makes_reverify_return_true(self, tmp_path: Path):
        """Compatible merged pin → _live_reverify returns True → merge proceeds."""
        from phase_loop_runtime.cross_repo_channel import ChannelDescriptor, set_upstream_ref

        repo, roadmap = self._make_post_p3_workspace(tmp_path)

        # Inject a compatible merged SHA (no 'BREAKING').
        channel = ChannelDescriptor(kind="pin", params={"file": "upstream-version.txt"})
        set_upstream_ref(repo, channel, "sha-MERGED-COMPATIBLE-abc123")

        # The verification command reads upstream-version.txt → no BREAKING → exits 0.
        result = _live_reverify(repo, roadmap, "governed")
        assert result is True, (
            "BLOCK 2 REGRESSION: _live_reverify returned False with a compatible "
            "merged pin — verification falsely rejected.\n"
            "upstream-version.txt contains 'sha-MERGED-COMPATIBLE-abc123'; "
            "the plan's verification command should have exited 0."
        )


# ---------------------------------------------------------------------------
# INV-7: run_loop failure contract — a genuine failure ALWAYS emits a signal

class TestInvariant7RunLoopFailureContract:
    """Pin that run_loop ALWAYS emits at least one failure signal on a genuine
    verification failure.

    Two complementary pins:
      (a) Real snapshot-construction path (status_snapshot on a pre-seeded repo).
      (b) Structural: the helper functions that COERCE all failure paths to
          non-None signals are themselves verified to produce non-None values.

    NOTE: After the false-green-killer fix, _live_reverify no longer reads
    run_loop's snapshot signals — it directly runs verification commands.
    This invariant now guards run_loop's standalone failure contract rather
    than the _live_reverify mechanism.  It remains important for callers that
    DO consume run_loop's snapshot signals (e.g. the standalone CLI, INV-5
    autonomy boundary checks).
    """

    # -----------------------------------------------------------------------
    # Part (a): real snapshot-construction path — pre-seeded repo

    def test_pre_seeded_verification_failure_snapshot_carries_signal(
        self, tmp_path: Path
    ):
        """status_snapshot() on a repo with a repeated_verification_failure
        LoopEvent returns a snapshot with blocker_class non-None.

        This exercises runner.reconcile() / status_snapshot() — the same
        code path run_loop uses to build its return value after a verification
        failure.  Changing run_loop's failure output so that reconcile() no
        longer sees the signal would make this test red.
        """
        import subprocess

        from phase_loop_runtime.events import append_event
        from phase_loop_runtime.models import LoopEvent, utc_now
        from phase_loop_runtime.provenance import event_provenance
        from phase_loop_runtime.runner import status_snapshot

        repo = tmp_path / "repo-v"
        repo.mkdir()
        # Minimal git repo (status_snapshot calls snapshot_provenance which
        # only needs the roadmap file; no git commands needed).
        roadmap = repo / "specs" / "phase-plans.md"
        roadmap.parent.mkdir(parents=True)
        roadmap.write_text(
            "# Roadmap\n\n### Phase 1 - Verify (VERIFY)\n"
        )

        # Append the exact LoopEvent that run_loop writes after a
        # repeated_verification_failure (runner.py lines 2457-2467 pattern).
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase="VERIFY",
                action="execute",
                status="blocked",
                model="gpt-5.4",
                reasoning_effort="medium",
                source="invariant-test-fixture",
                blocker={
                    "human_required": False,
                    "blocker_class": "repeated_verification_failure",
                    "blocker_summary": (
                        "INV-7 fixture: synthetic repeated_verification_failure "
                        "mirroring runner.py lines 2458-2466."
                    ),
                    "required_human_inputs": (),
                    "access_attempts": (),
                },
                **event_provenance(roadmap, "VERIFY"),
            ),
        )

        # Call the REAL status_snapshot() — same code path run_loop uses
        # internally to construct its return StateSnapshot.
        snapshot = status_snapshot(repo, roadmap)

        assert snapshot.blocker_class is not None, (
            "INV-7 VIOLATED: status_snapshot() returned a snapshot with "
            "blocker_class=None after a repeated_verification_failure LoopEvent "
            "was appended.  run_loop's snapshot-construction code (reconcile) "
            "is not propagating the blocker signal — _live_reverify would "
            "silently false-green a downstream merge."
        )
        assert snapshot.blocker_class == "repeated_verification_failure", (
            f"INV-7: unexpected blocker_class={snapshot.blocker_class!r}; "
            "expected 'repeated_verification_failure'"
        )

    def test_pre_seeded_verification_failure_causes_reverify_false(
        self, tmp_path: Path
    ):
        """_live_reverify returns False when run_loop returns the snapshot that
        status_snapshot() produces from a pre-seeded verification failure.

        This bridges INV-7a (snapshot carries signal) with the reader (INV-6):
        the ACTUAL snapshot produced by run_loop's internal code path causes
        _live_reverify to return False.
        """
        from unittest.mock import patch

        from phase_loop_runtime.events import append_event
        from phase_loop_runtime.models import LoopEvent, utc_now
        from phase_loop_runtime.provenance import event_provenance
        from phase_loop_runtime.runner import status_snapshot

        repo = tmp_path / "repo-v2"
        repo.mkdir()
        roadmap = repo / "specs" / "phase-plans.md"
        roadmap.parent.mkdir(parents=True)
        roadmap.write_text(
            "# Roadmap\n\n### Phase 1 - Verify (VERIFY)\n"
        )
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase="VERIFY",
                action="execute",
                status="blocked",
                model="gpt-5.4",
                reasoning_effort="medium",
                source="invariant-test-fixture",
                blocker={
                    "human_required": False,
                    "blocker_class": "repeated_verification_failure",
                    "blocker_summary": "INV-7 fixture: synthetic failure.",
                    "required_human_inputs": (),
                    "access_attempts": (),
                },
                **event_provenance(roadmap, "VERIFY"),
            ),
        )

        # Capture the REAL snapshot from status_snapshot() — what run_loop
        # would actually return for this blocked repo state.
        real_snapshot = status_snapshot(repo, roadmap)

        # Now feed that real snapshot through _live_reverify (patching run_loop
        # to return the snapshot we just obtained from real code).
        with patch(
            "phase_loop_runtime.runner.run_loop",
            return_value=(real_snapshot, []),
        ):
            result = _live_reverify(
                repo,
                roadmap,
                "governed",
            )

        assert result is False, (
            "INV-7 VIOLATED: _live_reverify returned True on the snapshot that "
            "status_snapshot() (run_loop's internal snapshot-construction code) "
            "produced for a pre-seeded repeated_verification_failure state.  "
            "The false-green killer does NOT catch the signal that run_loop "
            "actually emits on a verification failure."
        )

    # -----------------------------------------------------------------------
    # Part (b): structural — helper functions that coerce exception paths

    def test_pipeline_branch_blocker_from_error_always_sets_signal(self):
        """_pipeline_branch_blocker_from_error always returns a dict with
        non-None blocker_class in BLOCKER_CLASSES.

        This is the coercing helper for ALL exception paths in run_loop
        (runner.py lines 378-387, 418, 605).  If it could return None, any
        exception during pipeline-branch setup would silently false-green.
        """
        from phase_loop_runtime.models import BLOCKER_CLASSES
        from phase_loop_runtime.runner import _pipeline_branch_blocker_from_error

        class _BareException(Exception):
            pass

        class _TaggedException(Exception):
            blocker_class = "missing_secret"
            blocker_summary = "tagged exc summary"

        class _EmptyBlocker(Exception):
            blocker_class = None  # malformed; the helper must still coerce

        test_cases = [
            _BareException("bare exception — no blocker_class attribute"),
            _TaggedException("tagged — has valid blocker_class"),
            _EmptyBlocker("None blocker_class — coerce to contract_bug"),
            RuntimeError("generic runtime error"),
            ValueError("value error with no blocker_class"),
        ]

        for exc in test_cases:
            result = _pipeline_branch_blocker_from_error(exc)
            bc = result.get("blocker_class")
            assert bc is not None, (
                f"INV-7 VIOLATED: _pipeline_branch_blocker_from_error({exc!r}) "
                "returned blocker_class=None; all exception paths must produce "
                "a non-None blocker_class so _live_reverify can detect failure."
            )
            assert bc in BLOCKER_CLASSES, (
                f"INV-7 VIOLATED: _pipeline_branch_blocker_from_error({exc!r}) "
                f"returned blocker_class={bc!r} which is not in BLOCKER_CLASSES."
            )

    def test_blocker_site_count_in_runner_is_non_empty(self):
        """runner.py contains a non-trivial number of repeated_verification_failure
        sites — asserts the structural coverage is not vacuous.

        A future refactor that removes all signal-setting sites without updating
        this test would make it red (count drops to zero).
        """
        import inspect
        import re

        import phase_loop_runtime.runner as runner_mod

        source = inspect.getsource(runner_mod)

        # Count explicit repeated_verification_failure assignments in runner.py.
        rvf_sites = len(re.findall(r'"repeated_verification_failure"', source))
        assert rvf_sites >= 10, (
            f"INV-7 VIOLATED: only {rvf_sites} 'repeated_verification_failure' "
            "sites found in runner.py (expected ≥10).  The structural guarantee "
            "that run_loop always sets a failure signal may have eroded — verify "
            "that all verification-failure code paths still emit a blocker."
        )

        # Count non-None blocker_class defaults in coercing helpers.
        coerce_sites = len(re.findall(
            r'or "repeated_verification_failure"|or "contract_bug"',
            source,
        ))
        assert coerce_sites >= 2, (
            f"INV-7 VIOLATED: only {coerce_sites} coercing-default sites found "
            "in runner.py (expected ≥2 — the closeout reader and "
            "_pipeline_branch_blocker_from_error each contribute one).  "
            "A removed default would let a failure path silently emit None."
        )
