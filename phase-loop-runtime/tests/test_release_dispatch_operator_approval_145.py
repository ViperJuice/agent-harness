"""agent-harness#145 — runner-side operator-approval injection for release-dispatch.

The runner resolves + freshness-scopes the typed operator approval a release-dispatch
launch requires and injects it into the launch/state/event context (so SL-0 verifies
it from runner context instead of ``record_status=absent_from_runner_context``), and
fail-closes to a sticky ``admin_approval`` blocker when a fresh valid record cannot be
injected (absent / malformed / secret-bearing / stale). Target-coverage stays with the
child's SL-0 gate — the runner does not re-implement ``covers()``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import (
    _resolve_release_dispatch_operator_approval,
    operator_approval_record_path,
    run_loop,
)
from phase_loop_test_utils import make_repo, write_named_roadmap, write_phase_plan


def _release_plan(repo: Path, roadmap: Path, phase: str = "SHIP", *, requires_approval: bool = True) -> Path:
    fm = {"phase_loop_mutation": "release_dispatch"}
    if requires_approval:
        fm["phase_loop_requires_operator_approval"] = "true"
    return write_phase_plan(repo, phase, roadmap, extra_frontmatter=fm)


def _approval_payload(repo: Path, roadmap: Path, *, phase: str = "SHIP", targets=("pkg:consiliency",)) -> dict:
    return {
        "approved_targets": list(targets),
        "approved_at": "2026-07-11T00:00:00Z",
        "source": "codex-task-1",
        "watch_owner": "operator@example.com",
        "roadmap": str(roadmap.resolve().relative_to(repo.resolve())),
        "phase": phase,
        "run_id": "run-1",
    }


def _write_approval(repo: Path, payload: dict) -> None:
    path = operator_approval_record_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _resolve(repo, roadmap, plan):
    return _resolve_release_dispatch_operator_approval(repo, roadmap, plan, "SHIP")


def test_absent_record_fail_closes_admin_approval():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap)
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert metadata is None
        assert blocker is not None
        assert blocker.blocker_class == "admin_approval"
        # admin_approval is human_required + sticky (never auto-repaired).
        assert blocker.to_blocker()["human_required"] is True
        assert blocker.metadata["record_status"] == "absent"


def test_malformed_and_secret_bearing_records_fail_closed_not_crash():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap)

        _write_approval_raw = operator_approval_record_path(repo)
        _write_approval_raw.parent.mkdir(parents=True, exist_ok=True)
        _write_approval_raw.write_text("{ not valid json", encoding="utf-8")
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert metadata is None and blocker.blocker_class == "admin_approval"
        assert blocker.metadata["record_status"] == "malformed"

        # A secret-bearing key is rejected by operator_approval_from -> fail closed.
        secret = _approval_payload(repo, roadmap)
        secret["api_token"] = "sk-leak"
        _write_approval(repo, secret)
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert metadata is None and blocker.blocker_class == "admin_approval"
        assert blocker.metadata["record_status"] == "malformed"


def test_stale_record_wrong_phase_fail_closes():
    # The freshness-scope discriminator: an approval written for a DIFFERENT phase
    # must not authorize this launch — proves the gate is not a rubber stamp.
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap)
        _write_approval(repo, _approval_payload(repo, roadmap, phase="SOMEOTHERPHASE"))
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert metadata is None
        assert blocker.blocker_class == "admin_approval"
        assert blocker.metadata["record_status"] == "stale"


def test_stale_record_wrong_roadmap_fail_closes():
    # Same discriminator on the roadmap axis: an approval scoped to a different
    # roadmap must not authorize this one (CR: grok — wrong-roadmap was untested).
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap)
        payload = _approval_payload(repo, roadmap)
        payload["roadmap"] = "specs/some-other-roadmap-v9.md"
        _write_approval(repo, payload)
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert metadata is None
        assert blocker.blocker_class == "admin_approval"
        assert blocker.metadata["record_status"] == "stale"


def test_fresh_valid_record_is_injected_no_blocker():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap)
        _write_approval(repo, _approval_payload(repo, roadmap, targets=("pkg:consiliency", "deploy:prod")))
        metadata, blocker = _resolve(repo, roadmap, plan)
        assert blocker is None
        assert metadata is not None
        assert metadata["kind"] == "operator_approval"
        assert metadata["approved_targets"] == ["pkg:consiliency", "deploy:prod"]
        # metadata-only — no secret keys leaked.
        assert "secret" not in " ".join(map(str, metadata.keys())).lower()


def test_non_opted_in_release_dispatch_plan_has_no_gate():
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        plan = _release_plan(repo, roadmap, requires_approval=False)
        assert _resolve(repo, roadmap, plan) == (None, None)


def test_run_loop_release_dispatch_without_approval_blocks_before_launch():
    # Integration: an approval-requiring release-dispatch execute with no record emits
    # admin_approval BEFORE any child launch.
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        _release_plan(repo, roadmap)
        with patch(
            "phase_loop_runtime.runner.launch_with_spec",
            return_value=LaunchResult(command=["codex", "exec"], returncode=0),
        ) as fake_launch:
            snapshot, _results = run_loop(repo, roadmap, phase="SHIP")
        fake_launch.assert_not_called()
        assert snapshot.blocker_class == "admin_approval"
        assert snapshot.human_required is True


def test_run_loop_approval_gated_release_dispatch_requires_observe():
    # #145 (CR): a VALID approval under --no-observe must fail CLOSED (the injection
    # surface — the child-read launch metadata — only exists for observed runs), never
    # launch without the approval reaching the child. Blocks before launch, no dotfiles.
    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        _release_plan(repo, roadmap)
        _write_approval(repo, _approval_payload(repo, roadmap))
        with patch(
            "phase_loop_runtime.runner.launch_with_spec",
            return_value=LaunchResult(command=["codex", "exec"], returncode=0),
        ) as fake_launch:
            snapshot, _results = run_loop(repo, roadmap, phase="SHIP", observe=False)
        fake_launch.assert_not_called()
        assert snapshot.blocker_class == "admin_approval"
        assert snapshot.human_required is True


@pytest.mark.dotfiles_integration
def test_run_loop_release_dispatch_with_fresh_approval_launches_and_injects_metadata():
    # Happy path proven end-to-end (CI/dotfiles): a fresh valid approval launches AND
    # the approval metadata is actually injected into the child-read launch metadata +
    # the persisted launch event — not merely "not blocked" (CR: codex/grok — the old
    # assertion passed even if injection was removed).
    from phase_loop_runtime.events import read_events

    with tempfile.TemporaryDirectory() as td:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("SHIP", "Ship"),))
        _release_plan(repo, roadmap)
        _write_approval(repo, _approval_payload(repo, roadmap, targets=("pkg:consiliency", "deploy:prod")))
        with patch(
            "phase_loop_runtime.runner.launch_with_spec",
            return_value=LaunchResult(command=["codex", "exec"], returncode=0),
        ) as fake_launch:
            snapshot, _results = run_loop(repo, roadmap, phase="SHIP", observe=True)
        fake_launch.assert_called_once()
        assert snapshot.blocker_class != "admin_approval"
        # The approval was injected into the child-read launch metadata file.
        launch_meta_path = fake_launch.call_args.kwargs.get("log_path")
        # And it is durable in the launch event metadata.
        approvals = [
            (e.get("metadata") or {}).get("operator_approval")
            for e in read_events(repo)
            if isinstance(e.get("metadata"), dict) and (e.get("metadata") or {}).get("operator_approval")
        ]
        assert approvals, "operator_approval must be recorded in the launch event metadata"
        assert approvals[-1]["kind"] == "operator_approval"
        assert approvals[-1]["approved_targets"] == ["pkg:consiliency", "deploy:prod"]
