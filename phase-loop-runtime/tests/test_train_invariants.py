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
  INV-6. Live-default ``_live_reverify`` reads the real StateSnapshot failure
         signals (``closeout_terminal_status`` in the bad set, ``human_required``,
         ``blocker_class``); each failure case returns False; the clean pass
         returns True.  Tests call ``_live_reverify`` directly without stubbing
         ``_reverify_fn`` to guard the live-default path.
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
# INV-6: Live-default _live_reverify reads real StateSnapshot failure signals

class TestInvariant6LiveReverifyFailureSignals:
    """_live_reverify returns False on each StateSnapshot failure signal.

    This test calls _live_reverify DIRECTLY (not through run_train) without
    stubbing _reverify_fn.  It guards the live-default path used in production
    runs (not the test stub path used in P4 unit tests).

    The three failure signals (P4-CR-1 regression guard):
      a. closeout_terminal_status in the bad set
      b. human_required = True
      c. blocker_class is not None

    All three must independently return False.  The clean (no signal) path
    must return True.
    """

    # Build a minimal StateSnapshot with the given signal overrides.
    _BASE_SNAP_ARGS = dict(
        timestamp="2026-01-01T00:00:00Z",
        repo="test-repo",
        roadmap="specs/plan.md",
    )

    def _make_snapshot(self, **overrides) -> StateSnapshot:
        return StateSnapshot(**{**self._BASE_SNAP_ARGS, **overrides})

    def _reverify_with_snapshot(self, snapshot: StateSnapshot, tmp_path: Path) -> bool:
        """Call _live_reverify with a stubbed run_loop that returns the given snapshot."""
        from unittest.mock import patch

        # _live_reverify imports run_loop lazily from phase_loop_runtime.runner;
        # patch the canonical location so the lazy import is intercepted.
        with patch(
            "phase_loop_runtime.runner.run_loop",
            return_value=(snapshot, []),
        ):
            return _live_reverify(
                tmp_path / "repo-a",
                tmp_path / "repo-a" / "specs" / "plan.md",
                "governed",
            )

    def test_clean_snapshot_passes(self, tmp_path: Path):
        """A clean snapshot (no failure signals) returns True."""
        snap = self._make_snapshot()
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False on a clean snapshot "
            "(no failure signals set)"
        )

    def test_closeout_terminal_status_blocked_fails(self, tmp_path: Path):
        """closeout_terminal_status='blocked' → False."""
        snap = self._make_snapshot(closeout_terminal_status="blocked")
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with "
            "closeout_terminal_status='blocked'"
        )

    def test_closeout_terminal_status_stale_input_fails(self, tmp_path: Path):
        """closeout_terminal_status='stale_input' → False."""
        snap = self._make_snapshot(closeout_terminal_status="stale_input")
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with "
            "closeout_terminal_status='stale_input'"
        )

    def test_closeout_terminal_status_failed_verification_fails(self, tmp_path: Path):
        """closeout_terminal_status='failed_verification' → False."""
        snap = self._make_snapshot(closeout_terminal_status="failed_verification")
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with "
            "closeout_terminal_status='failed_verification'"
        )

    def test_closeout_terminal_status_human_required_string_fails(self, tmp_path: Path):
        """closeout_terminal_status='human_required' (string) → False."""
        snap = self._make_snapshot(closeout_terminal_status="human_required")
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with "
            "closeout_terminal_status='human_required'"
        )

    def test_closeout_terminal_status_none_passes(self, tmp_path: Path):
        """closeout_terminal_status=None (verify mode, no full closeout) → True.

        None is NOT a failure signal; verify mode may not emit a closeout event.
        """
        snap = self._make_snapshot(closeout_terminal_status=None)
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False with "
            "closeout_terminal_status=None; None is not a failure — verify mode "
            "may leave this field empty on a clean run"
        )

    def test_human_required_true_fails(self, tmp_path: Path):
        """human_required=True → False."""
        snap = self._make_snapshot(human_required=True)
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with human_required=True"
        )

    def test_human_required_false_passes(self, tmp_path: Path):
        """human_required=False (default) → True (signal absent)."""
        snap = self._make_snapshot(human_required=False)
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False with human_required=False"
        )

    def test_blocker_class_non_none_fails(self, tmp_path: Path):
        """blocker_class non-None → False."""
        snap = self._make_snapshot(blocker_class="missing_secret")
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with blocker_class='missing_secret'"
        )

    def test_blocker_class_none_passes(self, tmp_path: Path):
        """blocker_class=None (default) → True (signal absent)."""
        snap = self._make_snapshot(blocker_class=None)
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is True, (
            "INV-6 VIOLATED: _live_reverify returned False with blocker_class=None"
        )

    def test_all_signals_at_once_fails(self, tmp_path: Path):
        """All three failure signals simultaneously → False."""
        snap = self._make_snapshot(
            closeout_terminal_status="blocked",
            human_required=True,
            blocker_class="missing_secret",
        )
        result = self._reverify_with_snapshot(snap, tmp_path)
        assert result is False, (
            "INV-6 VIOLATED: _live_reverify returned True with all three "
            "failure signals active"
        )

    def test_exception_in_run_loop_fails(self, tmp_path: Path):
        """If run_loop raises, _live_reverify returns False (fail-safe)."""
        from unittest.mock import patch

        # _live_reverify imports run_loop lazily from phase_loop_runtime.runner.
        with patch(
            "phase_loop_runtime.runner.run_loop",
            side_effect=RuntimeError("run_loop exploded"),
        ):
            result = _live_reverify(
                tmp_path / "repo-a",
                tmp_path / "repo-a" / "specs" / "plan.md",
                "governed",
            )

        assert result is False, (
            "INV-6 VIOLATED: _live_reverify must return False (fail-safe) "
            "when run_loop raises an exception"
        )
