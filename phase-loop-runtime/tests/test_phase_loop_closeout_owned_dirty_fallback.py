"""Regression tests for the closeout auto-classification fallback.

Regenesis v37 hit a repeating failure where codex's EmitPhaseCloseout
left `phase_owned_dirty_paths` empty even though `dirty_paths` contained
files that plainly matched the active plan's owned-files glob. The
runner blocked closeout with ``missing_phase_owned_dirty_paths`` and the
operator had to manually commit on every phase.

The fix: when the dirty path list is non-empty AND every dirty path is
owned by the active plan, auto-classify them as phase-owned and proceed
to commit. If any dirty path is NOT owned by the plan, the blocker must
still be emitted.
"""

from __future__ import annotations

import subprocess

import pytest

from phase_loop_runtime.injection import (
    _extract_plan_owned_files,
    _render_baml_closeout_instruction,
)
from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import _perform_phase_closeout
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_closeout_autoclassifies_when_phase_owned_dirty_paths_empty_but_dirty_paths_match_plan(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    (repo / "README.md").write_text("phase output written by execute\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Simulate codex emitting empty phase_owned_dirty_paths despite valid
    # dirty_paths and the file plainly matching README.md owned-glob.
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("README.md",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo,
        roadmap,
        "CONTRACT",
        snapshot,
        resolve_profile("execute"),
        action="execute",
        closeout_mode="commit",
    )

    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete", f"expected complete, got {status}"
    assert event.metadata["closeout"]["closeout_action"] == "commit"
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "expected a new commit"
    # The reclassified paths should be recorded for audit.
    assert event.metadata["closeout"].get("closeout_dirty_paths_autoclassified") == ["README.md"]


def test_standalone_closeout_prompt_populates_active_plan_owned_files(tmp_path):
    # #58: the standalone (non-governed) closeout prompt used to hardcode an empty
    # `plan_owned_files`, so the executor saw a blank "Active plan owned files"
    # section and reported empty phase_owned_dirty_paths — tripping the closeout
    # missing_phase_owned_dirty_paths refusal even for a plan with explicit lane
    # ownership. The prompt must now carry the plan's declared owned files.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo, "FIXTURE", roadmap, owned_files=("src/adapters/boundary.ts", "tests/boundary.test.ts")
    )
    commit_fixture_paths(repo, "add FIXTURE plan", plan)

    owned = _extract_plan_owned_files(repo, roadmap, plan)
    assert owned == ("src/adapters/boundary.ts", "tests/boundary.test.ts")

    instruction = _render_baml_closeout_instruction(
        phase_alias="FIXTURE",
        plan_produces=(),
        plan_owned_files=owned,
        include_schema_description=True,
    )
    assert "Active plan owned files:" in instruction
    assert "src/adapters/boundary.ts" in instruction
    assert "tests/boundary.test.ts" in instruction

    # A control-only / unplanned phase (no plan) stays empty — no spurious ownership.
    assert _extract_plan_owned_files(repo, roadmap, None) == ()


@pytest.mark.dotfiles_integration
def test_build_prompt_bundle_wires_plan_owned_files_end_to_end(tmp_path):
    # #58 (CR: codex/grok): the helper-level test would still pass if build_prompt_bundle
    # re-hardcoded plan_owned_files=(). Prove the actual call-site wiring: the owned
    # files reach the rendered bundle body. (dotfiles: build_prompt_bundle resolves the
    # skills bundle.)
    from phase_loop_runtime.injection import build_prompt_bundle

    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo, "FIXTURE", roadmap, owned_files=("src/adapters/boundary.ts", "tests/boundary.test.ts")
    )
    commit_fixture_paths(repo, "add FIXTURE plan", plan)
    bundle = build_prompt_bundle(
        repo=repo,
        harness_target="codex",
        action="execute",
        roadmap=roadmap,
        phase="FIXTURE",
        plan=plan,
    )
    assert "Active plan owned files:" in bundle.body
    assert "src/adapters/boundary.ts" in bundle.body
    assert "tests/boundary.test.ts" in bundle.body


PARTIAL_PLAN = """# PARTIAL

## Lanes

### SL-0 - Owned
- **Owned files**: `owned_a.py`, `owned_b.py`
- **Interfaces provided**: a
- **Interfaces consumed**: none
"""


def test_closeout_partial_classify_commits_owned_subset_and_blocks_on_unowned_remainder(tmp_path):
    # OWNFIX #36-item1: reproduced from the real <fleet-stack> INVENTORY run, where the
    # executor emitted empty phase_owned_dirty_paths and one of N dirty paths
    # (a test the plan under-enumerated) was unowned. The old all-or-nothing fallback
    # blocked ALL verified-owned paths. The fix: auto-classify and commit the owned
    # subset, then surface the genuinely-unowned remainder via closeout_scope_violation
    # (human_required) so an autonomous loop stops cleanly instead of stranding work.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "owned_b.py").write_text("b\n", encoding="utf-8")
    (repo / "stray_test.py").write_text("not declared by the plan\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"},
        current_phase="PARTIAL",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "owned_b.py", "stray_test.py"),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    # Owned subset was committed (verified work preserved, no manual intervention).
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "expected the owned subset to be committed"
    assert event.metadata["closeout"].get("closeout_dirty_paths_autoclassified") == ["owned_a.py", "owned_b.py"]
    committed = _git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
    assert "owned_a.py" in committed and "owned_b.py" in committed
    assert "stray_test.py" not in committed
    # The genuinely-unowned remainder is surfaced loudly, not stranded.
    assert status == "blocked"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert event.blocker["human_required"] is True
    assert "stray_test.py" in event.blocker["blocker_summary"]
    assert event.metadata["closeout"]["unowned_dirty_paths"] == ["stray_test.py"]
    # Verification passed; the block is scope, not verification.
    assert event.metadata["closeout"]["verification_status"] == "passed"
    # stray_test.py is still dirty (left for the operator to declare / break-glass).
    assert "stray_test.py" in _git(repo, "status", "--short").stdout


def test_closeout_gate_soft_commits_all_safe_unowned_remainder(tmp_path):
    # GATE: a verified phase whose only beyond-ownership dirty paths are SAFE
    # (docs/plans/handoffs/config_nonsource) auto-commits them as a recorded `soft`
    # CloseoutException — no blocker.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "notes.md").write_text("a stray doc beyond ownership\n", encoding="utf-8")

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "notes.md"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    assert status == "complete", f"expected complete, got {status}: {event.blocker!r}"
    assert event.blocker is None
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "owned_a.py" in committed and "notes.md" in committed
    exceptions = event.metadata["closeout"]["closeout_exceptions"]
    assert len(exceptions) == 1
    assert exceptions[0]["exception_kind"] == "soft"
    assert exceptions[0]["sensitivity_class"] == "docs"
    assert "notes.md" in exceptions[0]["paths"]
    assert event.metadata["closeout"]["verification_status"] == "passed"


def test_closeout_gate_blocks_on_unsafe_unowned_remainder(tmp_path):
    # GATE: any UNSAFE beyond-ownership path still blocks (closeout_scope_violation).
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unowned source\n", encoding="utf-8")

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "rogue.py"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    assert status == "blocked"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert event.blocker["human_required"] is True
    assert "rogue.py" in event.blocker["blocker_summary"]
    # owned subset still preserved.
    assert "owned_a.py" in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout


def test_closeout_gate_mixed_commits_safe_blocks_only_unsafe(tmp_path):
    # GATE: mixed remainder — owned + SAFE commit; block only on the UNSAFE subset.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "notes.md").write_text("safe doc\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unsafe source\n", encoding="utf-8")

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "notes.md", "rogue.py"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "owned_a.py" in committed and "notes.md" in committed
    assert "rogue.py" not in committed
    assert status == "blocked"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert event.metadata["closeout"]["unowned_dirty_paths"] == ["rogue.py"]
    # the SAFE path was recorded as a soft exception even though the phase blocked.
    kinds = {e["exception_kind"] for e in event.metadata["closeout"]["closeout_exceptions"]}
    assert "soft" in kinds
    assert "rogue.py" in _git(repo, "status", "--short").stdout


def test_closeout_break_glass_commits_unsafe_remainder_with_reason(tmp_path):
    # BREAKGLASS: a non-empty operator reason folds the source/ci/lockfile UNSAFE
    # remainder into the closeout commit as a recorded `break_glass` CloseoutException
    # (reason set, in the shared tally) — no blocker.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unowned source the operator accepts\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "rogue.py"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
        allow_unowned_reason="hotfix: vendored upstream patch, owner sign-off in #123",
    )

    assert status == "complete", f"expected complete, got {status}: {event.blocker!r}"
    assert event.blocker is None
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "owned_a.py" in committed and "rogue.py" in committed
    assert head_before != _git(repo, "rev-parse", "HEAD").stdout.strip()
    # rogue.py recorded as a break_glass exception carrying the reason.
    exceptions = event.metadata["closeout"]["closeout_exceptions"]
    bg = [e for e in exceptions if e["exception_kind"] == "break_glass"]
    assert len(bg) == 1
    assert "rogue.py" in bg[0]["paths"]
    assert bg[0]["reason"] and "hotfix" in bg[0]["reason"]
    assert bg[0]["sensitivity_class"] == "source"
    # nothing left dirty — the remainder was emptied by the override.
    assert "rogue.py" not in _git(repo, "status", "--short").stdout
    assert event.metadata["closeout"]["verification_status"] == "passed"


def test_closeout_break_glass_empty_reason_blocks_operator_override_missing_reason(tmp_path):
    # BREAKGLASS defensive backstop (programmatic run_loop callers; CLI rejects this
    # pre-run_loop): an override attempt with an empty reason must NOT commit the
    # unsafe paths and must emit operator_override_missing_reason (not
    # closeout_scope_violation).
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unowned source\n", encoding="utf-8")

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "rogue.py"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
        allow_unowned_reason="   ",
    )

    assert status == "blocked"
    assert event.blocker["blocker_class"] == "operator_override_missing_reason"
    assert "rogue.py" in event.blocker["blocker_summary"]
    # the unsafe path was NOT committed under a blank reason.
    assert "rogue.py" not in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "rogue.py" in _git(repo, "status", "--short").stdout
    # owned subset still preserved (the override attempt does not discard verified work).
    assert "owned_a.py" in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout


SECRET_REMAINDER_PLAN = PARTIAL_PLAN


def test_closeout_break_glass_secrets_never_committed_even_with_reason(tmp_path):
    # BREAKGLASS hard carve-out: secrets are NEVER break-glassable. With a non-empty
    # reason and a mixed remainder (source + secret), the source path is force-committed
    # as break_glass but the secret is held back and still blocks with
    # closeout_scope_violation — a one-line reason cannot commit a secret to history.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=SECRET_REMAINDER_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unowned source\n", encoding="utf-8")
    (repo / ".env").write_text("API_TOKEN=supersecret\n", encoding="utf-8")

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"}, current_phase="PARTIAL",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "rogue.py", ".env"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
        allow_unowned_reason="operator override for the source patch",
    )

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    # source force-committed under the reason; the secret was held back.
    assert "owned_a.py" in committed and "rogue.py" in committed
    assert ".env" not in committed
    # and the closeout still blocks on the secret, regardless of the reason.
    assert status == "blocked"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert event.blocker["human_required"] is True
    assert ".env" in event.blocker["blocker_summary"]
    assert event.metadata["closeout"]["unowned_dirty_paths"] == [".env"]
    assert ".env" in _git(repo, "status", "--short").stdout
    # the source path was still recorded as a break_glass exception.
    kinds = {e["exception_kind"] for e in event.metadata["closeout"]["closeout_exceptions"]}
    assert "break_glass" in kinds


def test_closeout_still_blocks_when_dirty_paths_are_not_plan_owned(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    # Touch a path that the plan does NOT own.
    foreign = repo / "src" / "foreign.txt"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("not owned by CONTRACT\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("src/foreign.txt",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo,
        roadmap,
        "CONTRACT",
        snapshot,
        resolve_profile("execute"),
        action="execute",
        closeout_mode="commit",
    )

    assert status == "blocked"
    assert event.blocker is not None
    assert event.blocker["blocker_class"] == "dirty_worktree_conflict"
    assert event.metadata["closeout"]["closeout_refusal_reason"] == "missing_phase_owned_dirty_paths"
    # No commit should have happened.
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
