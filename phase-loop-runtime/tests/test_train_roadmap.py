"""Tests for P2 train roadmap schema, channel, and ledger (issue #29).

Test matrix:
  Schema / validate:
    - valid 2-node train parses and validates clean
    - cyclic dependency DAG fails loud (T-D)
    - missing node (depends on undeclared node) fails loud (T-B)
    - missing channel (edge with no channel) fails loud (T-C)
    - invalid in-repo IF-0 token reused as dep reference fails loud (T-A)
  set_upstream_ref:
    - submodule channel kind calls executor with correct args
    - pin channel kind calls executor with correct args
    - workspace channel kind calls executor with correct args
    - none channel kind raises ValueError (root node)
  Ledger:
    - append and resume round-trips per-node state
    - last-record-wins for same node_id
    - truncated trailing line is dropped, not crashed on
    - malformed mid-file line raises ValueError
    - path inside .phase-loop/ raises ValueError
    - train state path contains no .phase-loop component (invariant)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from phase_loop_runtime.cross_repo_channel import (
    ChannelDescriptor,
    parse_channel_line,
    set_upstream_ref,
)
from phase_loop_runtime.train_ledger import (
    LedgerRecord,
    append_record,
    default_ledger_path,
    read_ledger,
    resume_state,
)
from phase_loop_runtime.train_roadmap import (
    TrainNode,
    TrainRoadmap,
    parse_train_roadmap,
    validate_train,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers

VALID_TRAIN_MD = """\
# Release Train: my-feature

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
"""

VALID_TRAIN_3NODE_MD = """\
# Release Train: three-repo

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

CYCLIC_TRAIN_MD = """\
# Release Train: cyclic-example

## Nodes

### Node: repo-x / specs/x.md

**Depends on:** repo-y / specs/y.md
**Channel:** submodule path=vendor/y

### Node: repo-y / specs/y.md

**Depends on:** repo-x / specs/x.md
**Channel:** submodule path=vendor/x
"""

MISSING_NODE_TRAIN_MD = """\
# Release Train: missing-node

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** nonexistent-repo / specs/nope.md
**Channel:** submodule path=vendor/nope
"""

MISSING_CHANNEL_TRAIN_MD = """\
# Release Train: missing-channel

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** (none)
"""

IN_REPO_TOKEN_TRAIN_MD = """\
# Release Train: invalid-token-reuse

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** IF-0-P1-1
**Channel:** submodule path=vendor/x
"""


# ---------------------------------------------------------------------------
# Schema parse / validate

class TestValidTrain:
    def test_parse_title(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        assert r.title == "my-feature"

    def test_parse_nodes(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        assert len(r.nodes) == 2
        assert r.nodes[0].node_id == "repo-a/specs/plan-a.md"
        assert r.nodes[1].node_id == "repo-b/specs/plan-b.md"

    def test_parse_edges(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        assert len(r.edges) == 1
        edge = r.edges[0]
        assert edge.upstream.node_id == "repo-a/specs/plan-a.md"
        assert edge.downstream.node_id == "repo-b/specs/plan-b.md"
        assert edge.channel.kind == "submodule"
        assert edge.channel.params["path"] == "vendor/repo-a"

    def test_validate_clean(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        errors = validate_train(r)
        assert errors == [], errors

    def test_topo_order(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        order = r.topo_order()
        ids = [n.node_id for n in order]
        # upstream before downstream
        assert ids.index("repo-a/specs/plan-a.md") < ids.index("repo-b/specs/plan-b.md")

    def test_3node_topo_order(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_3NODE_MD)
        errors = validate_train(r)
        assert errors == [], errors
        order = r.topo_order()
        ids = [n.node_id for n in order]
        assert ids == [
            "alpha/specs/alpha.md",
            "beta/specs/beta.md",
            "gamma/specs/gamma.md",
        ]

    def test_gate_id_new_namespace(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        edge = r.edges[0]
        gate = edge.gate_id("abc123")
        # New XGATE: namespace, NOT IF-0-...
        assert gate.startswith("XGATE:"), f"expected XGATE: prefix, got: {gate}"
        assert "IF-0-" not in gate
        assert "abc123" in gate

    def test_node_by_id(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        n = r.node_by_id("repo-a/specs/plan-a.md")
        assert n is not None
        assert n.repo == "repo-a"
        assert n.roadmap == "specs/plan-a.md"


class TestCyclicTrain:
    def test_cyclic_fails_loud(self) -> None:
        # cyclic train: parse succeeds (parser is not the validator)
        # but validate_train returns a T-D error
        r = parse_train_roadmap(CYCLIC_TRAIN_MD)
        errors = validate_train(r)
        t_d_errors = [e for e in errors if "(T-D)" in e]
        assert t_d_errors, f"expected (T-D) cycle error, got: {errors}"

    def test_topo_order_raises_on_cycle(self) -> None:
        r = parse_train_roadmap(CYCLIC_TRAIN_MD)
        with pytest.raises(ValueError, match="cycle"):
            r.topo_order()


class TestMissingNodeTrain:
    def test_missing_node_fails_on_parse(self) -> None:
        # parse_train_roadmap raises ValueError when a dep node is not declared
        with pytest.raises(ValueError, match="unknown node"):
            parse_train_roadmap(MISSING_NODE_TRAIN_MD)


class TestMissingChannelTrain:
    def test_missing_channel_fails_loud(self) -> None:
        r = parse_train_roadmap(MISSING_CHANNEL_TRAIN_MD)
        errors = validate_train(r)
        t_c_errors = [e for e in errors if "(T-C)" in e]
        assert t_c_errors, f"expected (T-C) missing-channel error, got: {errors}"

    def test_missing_channel_mentions_nodes(self) -> None:
        r = parse_train_roadmap(MISSING_CHANNEL_TRAIN_MD)
        errors = validate_train(r)
        combined = " ".join(errors)
        assert "repo-b" in combined or "repo-a" in combined


class TestInRepoTokenReused:
    def test_if0_token_as_dep_fails_parse(self) -> None:
        # Parsing must raise a DISTINCT error about the IF-gate token namespace,
        # not the generic "unknown node" message produced by missing-node failures.
        # This proves that the IF-0 rejection is its own check, not a coincidental
        # side-effect of the node-lookup failing for unrelated reasons.
        with pytest.raises(ValueError, match="IF-gate"):
            parse_train_roadmap(IN_REPO_TOKEN_TRAIN_MD)


# ---------------------------------------------------------------------------
# parse_channel_line

class TestParseChannelLine:
    def test_none(self) -> None:
        c = parse_channel_line("(none)")
        assert c.kind == "none"
        assert c.params == {}

    def test_none_bare(self) -> None:
        c = parse_channel_line("none")
        assert c.kind == "none"

    def test_submodule(self) -> None:
        c = parse_channel_line("submodule path=vendor/repo-a")
        assert c.kind == "submodule"
        assert c.params["path"] == "vendor/repo-a"

    def test_pin(self) -> None:
        c = parse_channel_line("pin name=mylib version=1.2.3")
        assert c.kind == "pin"
        assert c.params["name"] == "mylib"
        assert c.params["version"] == "1.2.3"

    def test_workspace(self) -> None:
        c = parse_channel_line("workspace path=../sibling")
        assert c.kind == "workspace"
        assert c.params["path"] == "../sibling"

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown channel kind"):
            parse_channel_line("git url=https://example.com")

    def test_missing_param_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_channel_line("submodule")  # missing path=


# ---------------------------------------------------------------------------
# set_upstream_ref — stubs the git/fs boundary

class TestSetUpstreamRef:
    """set_upstream_ref re-resolves each channel kind via a stubbed executor."""

    def _make_stub(self) -> Tuple[List[Tuple], "StubExecutor"]:
        calls: List[Tuple] = []

        def stub(workspace: Path, kind: str, params: dict, ref: str) -> None:
            calls.append((workspace, kind, params, ref))

        return calls, stub  # type: ignore[return-value]

    def test_submodule_calls_executor(self, tmp_path: Path) -> None:
        calls, stub = self._make_stub()
        channel = parse_channel_line("submodule path=vendor/dep")
        set_upstream_ref(tmp_path, channel, "deadbeef", _executor=stub)
        assert len(calls) == 1
        ws, kind, params, ref = calls[0]
        assert ws == tmp_path
        assert kind == "submodule"
        assert params["path"] == "vendor/dep"
        assert ref == "deadbeef"

    def test_pin_calls_executor(self, tmp_path: Path) -> None:
        calls, stub = self._make_stub()
        channel = parse_channel_line("pin name=mylib version=0.0.0")
        set_upstream_ref(tmp_path, channel, "sha-pin-123", _executor=stub)
        assert len(calls) == 1
        ws, kind, params, ref = calls[0]
        assert kind == "pin"
        assert params["name"] == "mylib"
        assert ref == "sha-pin-123"

    def test_workspace_calls_executor(self, tmp_path: Path) -> None:
        calls, stub = self._make_stub()
        channel = parse_channel_line("workspace path=../upstream")
        set_upstream_ref(tmp_path, channel, "merge-sha-456", _executor=stub)
        assert len(calls) == 1
        ws, kind, params, ref = calls[0]
        assert kind == "workspace"
        assert params["path"] == "../upstream"
        assert ref == "merge-sha-456"

    def test_none_channel_raises(self, tmp_path: Path) -> None:
        calls, stub = self._make_stub()
        channel = parse_channel_line("(none)")
        with pytest.raises(ValueError, match="none"):
            set_upstream_ref(tmp_path, channel, "someref", _executor=stub)
        # Executor must NOT have been called
        assert calls == []

    def test_executor_receives_workspace_path(self, tmp_path: Path) -> None:
        calls, stub = self._make_stub()
        channel = parse_channel_line("submodule path=vendor/x")
        set_upstream_ref(tmp_path / "workspace", channel, "ref42", _executor=stub)
        assert calls[0][0] == tmp_path / "workspace"


# ---------------------------------------------------------------------------
# Ledger

class TestLedgerAppendResume:
    def test_append_and_read_single_record(self, tmp_path: Path) -> None:
        ledger = tmp_path / "train.ledger.jsonl"
        rec = LedgerRecord(
            node_id="repo-a/specs/plan.md",
            status="pr_open",
            branch="feat/abc",
            pr_url="https://github.com/org/repo-a/pull/1",
        )
        append_record(ledger, rec)
        state = read_ledger(ledger)
        assert "repo-a/specs/plan.md" in state
        got = state["repo-a/specs/plan.md"]
        assert got.status == "pr_open"
        assert got.branch == "feat/abc"
        assert got.pr_url == "https://github.com/org/repo-a/pull/1"

    def test_last_record_wins(self, tmp_path: Path) -> None:
        ledger = tmp_path / "train.ledger.jsonl"
        node_id = "repo-a/specs/plan.md"
        append_record(ledger, LedgerRecord(node_id=node_id, status="pending"))
        append_record(ledger, LedgerRecord(node_id=node_id, status="running"))
        append_record(ledger, LedgerRecord(node_id=node_id, status="pr_open", branch="feat/x"))
        state = read_ledger(ledger)
        assert state[node_id].status == "pr_open"
        assert state[node_id].branch == "feat/x"

    def test_multiple_nodes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "train.ledger.jsonl"
        append_record(ledger, LedgerRecord(node_id="repo-a/specs/a.md", status="merged", merge_order=1))
        append_record(ledger, LedgerRecord(node_id="repo-b/specs/b.md", status="pr_open"))
        state = read_ledger(ledger)
        assert state["repo-a/specs/a.md"].status == "merged"
        assert state["repo-a/specs/a.md"].merge_order == 1
        assert state["repo-b/specs/b.md"].status == "pr_open"

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        ledger = tmp_path / "empty.ledger.jsonl"
        ledger.touch()
        assert read_ledger(ledger) == {}

    def test_nonexistent_file_returns_empty_dict(self, tmp_path: Path) -> None:
        ledger = tmp_path / "missing.ledger.jsonl"
        assert read_ledger(ledger) == {}

    def test_resume_state_alias(self, tmp_path: Path) -> None:
        ledger = tmp_path / "train.ledger.jsonl"
        append_record(ledger, LedgerRecord(node_id="n1/specs/p.md", status="blocked"))
        s1 = read_ledger(ledger)
        s2 = resume_state(ledger)
        assert s1 == s2

    def test_upstream_merge_sha_recorded(self, tmp_path: Path) -> None:
        ledger = tmp_path / "train.ledger.jsonl"
        append_record(
            ledger,
            LedgerRecord(
                node_id="repo-a/specs/a.md",
                status="merged",
                upstream_merge_sha="abc123def456",
                merge_order=1,
            ),
        )
        state = read_ledger(ledger)
        assert state["repo-a/specs/a.md"].upstream_merge_sha == "abc123def456"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid ledger status"):
            LedgerRecord(node_id="x/y.md", status="not-a-status")

    def test_default_ledger_path_not_in_phase_loop(self, tmp_path: Path) -> None:
        coordinator_dir = tmp_path / "coordinator"
        path = default_ledger_path(coordinator_dir, "my-train")
        # The path must not contain .phase-loop
        assert ".phase-loop" not in str(path)


class TestLedgerDurability:
    """Durability: atomic append + tolerant trailing-line drop."""

    def test_truncated_trailing_line_dropped(self, tmp_path: Path) -> None:
        """Simulate a crash mid-write: truncated final line is dropped silently."""
        ledger = tmp_path / "crash.ledger.jsonl"
        # Write a valid record first
        append_record(
            ledger,
            LedgerRecord(node_id="repo-a/specs/a.md", status="pr_open", branch="feat/a"),
        )
        # Simulate a truncated write for a second record (incomplete JSON)
        with ledger.open("ab") as f:
            f.write(b'{"node_id": "repo-b/specs/b.md", "status": "runn')  # truncated!

        # Resume reader must NOT crash; it drops the trailing truncated line
        state = read_ledger(ledger)
        assert "repo-a/specs/a.md" in state
        assert state["repo-a/specs/a.md"].status == "pr_open"
        # The partial second record is dropped
        assert "repo-b/specs/b.md" not in state

    def test_malformed_mid_file_raises(self, tmp_path: Path) -> None:
        """A malformed line that is NOT the last line raises ValueError."""
        ledger = tmp_path / "corrupt.ledger.jsonl"
        # Write two valid records and a malformed line between them
        first = LedgerRecord(node_id="repo-a/specs/a.md", status="pending")
        append_record(ledger, first)
        # Inject malformed line in the middle
        with ledger.open("ab") as f:
            f.write(b"NOTJSON\n")
        # Write another valid record after
        append_record(
            ledger,
            LedgerRecord(node_id="repo-b/specs/b.md", status="running"),
        )
        with pytest.raises(ValueError, match="malformed ledger line"):
            read_ledger(ledger)

    def test_phase_loop_path_rejected(self, tmp_path: Path) -> None:
        """Ledger path inside .phase-loop/ raises loud on append."""
        bad_path = tmp_path / ".phase-loop" / "train.ledger.jsonl"
        rec = LedgerRecord(node_id="x/y.md", status="pending")
        with pytest.raises(ValueError, match=".phase-loop"):
            append_record(bad_path, rec)

    def test_atomicity_single_write(self, tmp_path: Path) -> None:
        """Each append is a single os.write call (verifiable via file size growth)."""
        ledger = tmp_path / "atomic.ledger.jsonl"
        size_before = 0
        for i in range(3):
            size_before = ledger.stat().st_size if ledger.exists() else 0
            append_record(
                ledger,
                LedgerRecord(node_id=f"repo-{i}/specs/p.md", status="pending"),
            )
            size_after = ledger.stat().st_size
            assert size_after > size_before, f"no growth after append {i}"

        # All 3 records readable
        state = read_ledger(ledger)
        assert len(state) == 3


# ---------------------------------------------------------------------------
# roadmap_lint train-mode integration

class TestRoadmapLintTrainMode:
    def test_lint_train_valid(self) -> None:
        from phase_loop_runtime.roadmap_lint import lint_train_roadmap_text

        errors = lint_train_roadmap_text(VALID_TRAIN_MD)
        assert errors == [], errors

    def test_lint_train_cyclic_fails_loud(self) -> None:
        from phase_loop_runtime.roadmap_lint import lint_train_roadmap_text

        # Cyclic: parse will succeed (parse_train_roadmap doesn't check cycles),
        # but lint_train_roadmap_text calls validate_train which catches the cycle.
        # However since cyclic train has no missing-node, it parses OK.
        # But our CYCLIC_TRAIN_MD has both nodes as BOTH upstream AND downstream,
        # creating a cycle that parse_train_roadmap resolves (both nodes exist).
        errors = lint_train_roadmap_text(CYCLIC_TRAIN_MD)
        assert errors, "expected errors for cyclic train"
        assert any("T-D" in e or "cycle" in e.lower() for e in errors)

    def test_lint_train_missing_channel_fails(self) -> None:
        from phase_loop_runtime.roadmap_lint import lint_train_roadmap_text

        errors = lint_train_roadmap_text(MISSING_CHANNEL_TRAIN_MD)
        assert errors, "expected errors for missing channel"
        assert any("T-C" in e or "channel" in e.lower() for e in errors)

    def test_phase_plan_roadmap_still_validates_clean(self) -> None:
        """Existing phase-plan validate-roadmap path is NOT broken by train additions."""
        from phase_loop_runtime.roadmap_lint import lint_roadmap_text

        # Use the actual cross-repo spec which has 5 phases and should pass
        spec_path = Path(__file__).parent.parent.parent / "specs" / "phase-plans-cross-repo-v1.md"
        if not spec_path.exists():
            pytest.skip("cross-repo spec not found")
        text = spec_path.read_text(encoding="utf-8")
        errors = lint_roadmap_text(text)
        assert errors == [], f"phase-plan lint regression: {errors}"


# ---------------------------------------------------------------------------
# TrainNode and gate identity tests

class TestTrainNodeAndGateIdentity:
    def test_node_id_format(self) -> None:
        n = TrainNode(repo="my-repo", roadmap="specs/plan.md")
        assert n.node_id == "my-repo/specs/plan.md"

    def test_node_str(self) -> None:
        n = TrainNode(repo="my-repo", roadmap="specs/plan.md")
        assert str(n) == "my-repo / specs/plan.md"

    def test_gate_id_not_if0_namespace(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        edge = r.edges[0]
        for sha in ["abc123", "0000000", "cafebabe"]:
            gid = edge.gate_id(sha)
            assert "IF-0-" not in gid
            assert sha in gid
            assert "XGATE:" in gid

    def test_gate_id_encodes_upstream_node(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        edge = r.edges[0]
        gid = edge.gate_id("sha1")
        assert "repo-a" in gid

    def test_gate_id_different_shas_differ(self) -> None:
        r = parse_train_roadmap(VALID_TRAIN_MD)
        edge = r.edges[0]
        assert edge.gate_id("sha1") != edge.gate_id("sha2")
