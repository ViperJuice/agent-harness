"""#47 — order-only cross-repo train dependency edges.

An `order-only` edge means "B must merge AFTER A (freeze/merge order) but B does not
consume A's artifact" — it carries no consumption channel, so the coordinator enforces
ordering via the topo sort + sequential merge but performs NO channel injection.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from phase_loop_runtime.cross_repo_channel import (
    ChannelDescriptor,
    UnsupportedChannelKind,
    parse_channel_line,
    set_upstream_ref,
)
from phase_loop_runtime.train_ledger import default_ledger_path
from phase_loop_runtime.train_roadmap import parse_train_roadmap, validate_train_loud
from phase_loop_runtime.train_runner import run_train

ORDER_ONLY_TRAIN = """# Release Train: order-only

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md

**Depends on:** repo-a / specs/plan-a.md
**Channel:** order-only
"""


def test_order_only_channel_parses():
    assert parse_channel_line("order-only").kind == "order-only"


def test_order_only_train_validates():
    validate_train_loud(parse_train_roadmap(ORDER_ONLY_TRAIN))  # must not raise


def test_bare_none_dependency_still_rejected():
    md = ORDER_ONLY_TRAIN.replace("**Channel:** order-only", "**Channel:** (none)")
    with pytest.raises(Exception) as excinfo:
        validate_train_loud(parse_train_roadmap(md))
    assert "T-C" in str(excinfo.value)


def test_workspace_channel_still_rejected():
    md = ORDER_ONLY_TRAIN.replace("**Channel:** order-only", "**Channel:** workspace path=../x")
    with pytest.raises(Exception) as excinfo:
        validate_train_loud(parse_train_roadmap(md))
    assert "T-E" in str(excinfo.value)


def test_set_upstream_ref_order_only_raises():
    with pytest.raises(UnsupportedChannelKind):
        set_upstream_ref(Path("/tmp"), ChannelDescriptor(kind="order-only", params={}), "sha")


def test_run_train_skips_injection_for_order_only_edge(tmp_path):
    roadmap = parse_train_roadmap(ORDER_ONLY_TRAIN)
    ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
    ledger = default_ledger_path(tmp_path / "ledger", "t")

    inject_calls: list = []
    publish_calls: list = []

    def _publish(workspace, owned_paths, *, draft, **kw):
        publish_calls.append(workspace.name)
        return {
            "status": "published",
            "branch": f"feat/{workspace.name}",
            "head_sha": f"sha-{workspace.name}",
            "pr_url": f"https://gh/{workspace.name}/1",
        }

    def _inject(workspace, channel, ref):
        inject_calls.append((workspace.name, channel.kind, ref))
        return []

    def _run_loop(*a, **kw):
        # SimpleNamespace without a `phases` attr → publish-completion guard skipped;
        # phase_owned_dirty_paths provides the published owned paths.
        return types.SimpleNamespace(phase_owned_dirty_paths=["work.md"], dirty_paths=["work.md"]), []

    result = run_train(
        roadmap,
        ledger,
        run_mode="autonomous",
        resolve_workspace=lambda n: ws_map[n.node_id],
        _run_loop=_run_loop,
        _publish=_publish,
        _set_upstream_ref_fn=_inject,
        _preflight_fn=lambda *a, **kw: [],
        _pr_is_open=lambda *a, **kw: False,
        _live_pr_head_sha_fn=lambda *a, **kw: None,
    )

    # Both nodes published in topo order (upstream first) — merge order enforced.
    assert publish_calls == ["repo-a", "repo-b"], publish_calls
    # The order-only edge injects NOTHING — set_upstream_ref is never called.
    assert inject_calls == [], f"order-only edge must not inject a channel; got {inject_calls}"


def test_resume_order_only_upstream_change_does_not_block_downstream(tmp_path):
    """#47 resume fix: an order-only upstream that changed out-of-band must NOT
    spuriously block the downstream. The downstream consumes nothing from the
    upstream, so an upstream SHA change does not make it stale. (Contrast: the
    same OOB scenario with a submodule/pin channel DOES block — see
    test_train_runner's out-of-band test.)
    """
    from unittest.mock import MagicMock

    from phase_loop_runtime.train_ledger import LedgerRecord, append_record

    roadmap = parse_train_roadmap(ORDER_ONLY_TRAIN)
    ledger = tmp_path / "ledger" / "t.ledger.jsonl"
    append_record(ledger, LedgerRecord(
        node_id="repo-a/specs/plan-a.md", status="pr_open", branch="feat/a",
        pr_url="https://gh/a/1", head_sha="sha-v1", merge_order=0,
    ))
    append_record(ledger, LedgerRecord(
        node_id="repo-b/specs/plan-b.md", status="pr_open", branch="feat/b",
        pr_url="https://gh/b/1", head_sha="sha-b1", merge_order=1,
    ))
    ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}

    def _live_sha(workspace, branch):
        # repo-a's live SHA diverged from the ledger (out-of-band push).
        if branch == "feat/a":
            return "sha-v2"
        if branch == "feat/b":
            return "sha-b1"
        return None

    result = run_train(
        roadmap,
        ledger,
        run_mode="autonomous",
        resolve_workspace=lambda n: ws_map[n.node_id],
        _run_loop=MagicMock(),
        _publish=MagicMock(),
        _set_upstream_ref_fn=lambda *a, **kw: None,
        _preflight_fn=lambda *a, **kw: [],
        _pr_is_open=lambda *a, **kw: True,
        _live_pr_head_sha_fn=_live_sha,
    )

    assert result["status"] != "blocked", (
        f"#47 resume: an order-only upstream's out-of-band change must not block the "
        f"downstream (it consumes nothing); got {result}"
    )
    assert result["status"] == "completed", result
