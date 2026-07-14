"""Regression tests for the PR #220 cross-vendor CR fix round.

Covers the five findings (2 fail-open, 3 fail-closed/usability) plus the
production-path proof that the EXTRACT disposable-only shape is reachable (not
dead code). Each test is written to FAIL on the pre-fix code.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import phase_loop_runtime.runner as runner
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import _perform_phase_closeout, _worktree_clean_probe
from phase_loop_runtime.verification_evidence import (
    _resolve_suite_interpreter,
    run_verification,
)
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


# --- EXTRACT reachability: production path through reconcile (refutes dead-code) ---

def test_extract_disposable_only_shape_is_produced_by_reconcile(tmp_path):
    """The REAL EXTRACT shape (`phase_owned_dirty=False`, disposables via the
    executor's self-reported `terminal_summary.dirty_paths`) is produced by
    `reconcile` — not a hand-built snapshot. `_event_dirty_summary` reads the
    executor's terminal_summary dirty_paths directly (reconcile.py fallback), and
    the executor does NOT git-status-filter, so untracked+ignored byproducts land
    in `snapshot.dirty_paths`. Then closeout finalizes them as a no-op.
    """
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("src/lib.py",))
    (repo / ".gitignore").write_text("build/\n*.egg-info/\n.phase-loop/\n", encoding="utf-8")
    commit_fixture_paths(repo, "plan + ignores", plan, repo / ".gitignore")
    for rel in ("build/lib.txt", "pkg.egg-info/PKG-INFO", ".phase-loop/scratch"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("byproduct\n", encoding="utf-8")
    disposables = ["build/lib.txt", "pkg.egg-info/PKG-INFO", ".phase-loop/scratch"]

    # An executor closeout event carrying the RAW self-report (over-reported
    # disposables) in terminal_summary — exactly what codex emits.
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="CONTRACT",
            action="execute",
            status="awaiting_phase_closeout",
            model="fixture",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "terminal_summary": {
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "phase_owned_dirty": False,
                    "phase_owned_dirty_paths": [],
                    "dirty_paths": disposables,
                },
            },
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )

    snapshot = reconcile(repo, roadmap)
    # Proof the shape is REAL: reconcile carried the executor's disposables into
    # snapshot.dirty_paths with phase_owned_dirty=False.
    assert snapshot.current_phase == "CONTRACT"
    assert snapshot.phase_owned_dirty is False
    assert set(disposables).issubset(set(snapshot.dirty_paths)), snapshot.dirty_paths

    status, event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )
    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete"
    assert event.metadata["closeout"]["closeout_action"] == "noop_disposable_only"


def _emit_executor_closeout(repo, roadmap, phase, dirty_paths):
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase=phase,
            action="execute", status="awaiting_phase_closeout",
            model="fixture", reasoning_effort="medium", source="fixture",
            metadata={
                "terminal_summary": {
                    "terminal_status": "complete", "verification_status": "passed",
                    "phase_owned_dirty": False, "phase_owned_dirty_paths": [],
                    "dirty_paths": list(dirty_paths),
                },
            },
            **event_provenance(roadmap, phase),
        ),
    )


def test_extract_mixed_owned_plus_disposables_commits_owned(tmp_path):
    """The REAL EXTRACT shape: an extraction phase produces an OWNED deliverable
    AND over-reports byproducts. The disposable filter strips the byproducts; the
    owned file flows through the fallback commit path and commits — no conflict,
    NOT the no-op branch.
    """
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("src/*.py",))
    (repo / ".gitignore").write_text("build/\n*.egg-info/\n", encoding="utf-8")
    commit_fixture_paths(repo, "plan + ignores", plan, repo / ".gitignore")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "extracted.py").write_text("EXTRACTED = 1\n", encoding="utf-8")  # owned, untracked, real
    for rel in ("build/lib.txt", "pkg.egg-info/PKG-INFO"):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("byproduct\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _emit_executor_closeout(repo, roadmap, "CONTRACT",
                            ["src/extracted.py", "build/lib.txt", "pkg.egg-info/PKG-INFO"])
    snapshot = reconcile(repo, roadmap)
    assert snapshot.phase_owned_dirty is False
    assert "src/extracted.py" in snapshot.dirty_paths

    status, event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )
    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete"
    # Owned deliverable committed; byproducts filtered (not committed); it's a real
    # commit, not the disposables-only no-op.
    assert event.metadata["closeout"]["closeout_action"] != "noop_disposable_only"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "src/extracted.py" in committed
    assert "build/lib.txt" not in committed and "pkg.egg-info/PKG-INFO" not in committed


def test_extract_mixed_with_genuine_unowned_still_blocks(tmp_path):
    """Over-suppression guard: the disposable filter must NOT swallow a genuine
    unowned NON-ignored path — it still blocks (scope violation), owned committed.
    """
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("src/*.py",))
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    commit_fixture_paths(repo, "plan + ignores", plan, repo / ".gitignore")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "extracted.py").write_text("EXTRACTED = 1\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "lib.txt").write_text("byproduct\n", encoding="utf-8")
    (repo / "stray.py").write_text("genuinely unowned, not ignored\n", encoding="utf-8")

    _emit_executor_closeout(repo, roadmap, "CONTRACT",
                            ["src/extracted.py", "build/lib.txt", "stray.py"])
    snapshot = reconcile(repo, roadmap)

    status, event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )
    assert status == "blocked", "a genuine unowned non-ignored path must still block"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert "stray.py" in event.blocker["blocker_summary"]
    # owned deliverable still committed; byproduct not.
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "src/extracted.py" in committed
    assert "build/lib.txt" not in committed


# --- codex#2: git-probe error must NOT read as clean → must block ---

def test_worktree_clean_probe_returns_none_on_probe_failure(tmp_path):
    # A non-git directory: git status exits non-zero → probe reports None (failure),
    # NOT False/True. (`_dirty_paths` would return [] here — indistinguishable from
    # clean — which is the bug.)
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    assert _worktree_clean_probe(non_git) is None
    assert runner._dirty_paths(non_git) == []  # the unsafe behavior we guard against


def test_disposable_only_blocks_when_tree_probe_fails(tmp_path, monkeypatch):
    # The disposable-only no-op must REQUIRE a genuinely-clean probe; a probe
    # failure (None) must fall through to a block, never finalize complete.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "EXTRACT", roadmap, owned_files=("src/lib.py",))
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    commit_fixture_paths(repo, "plan + ignores", plan, repo / ".gitignore")
    (repo / "build").mkdir()
    (repo / "build" / "lib.txt").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_worktree_clean_probe", lambda _repo: None)  # simulate git probe failure

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"EXTRACT": "awaiting_phase_closeout"}, current_phase="EXTRACT",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("build/lib.txt",), closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "EXTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )
    assert status != "complete", "an unreadable tree probe must block, not finalize"
    assert event.metadata["closeout"].get("closeout_action") != "noop_disposable_only"


# --- codex#1: red suite shadowed by a log-integrity error must still fail closed ---

def _write_shadowing_artifact(run_dir: Path, command_exit: int) -> Path:
    """A parseable artifact with a non-zero command exit AND a log whose sha256
    does NOT match (tamper) — validate() returns log_sha256_mismatch, shadowing
    the nonzero_exit code."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "verification.log").write_text("actual log contents\n", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "phase_alias": "BUILD",
        "commands": [{"argv": ["pytest"], "cwd": ".", "exit_code": command_exit, "duration_s": 0.1, "log_offset": 0}],
        "env_refresh": None,
        "suite": None,
        "started_at": "2026-07-14T00:00:00Z",
        "finished_at": "2026-07-14T00:00:01Z",
        "log_sha256": hashlib.sha256(b"a DIFFERENT log than on disk").hexdigest(),  # deliberate mismatch
    }
    (run_dir / "verification.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return run_dir


def test_red_suite_shadowed_by_log_mismatch_fails_closed_under_warn(tmp_path, monkeypatch):
    from phase_loop_runtime.closeout import build_phase_loop_closeout
    from phase_loop_runtime.verification_evidence import validate_verification_artifact

    monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "warn")  # softest posture
    run_dir = tmp_path / "run"
    _write_shadowing_artifact(run_dir, command_exit=3)

    # The shadowing precondition: validate reports the log-integrity code, NOT
    # nonzero_exit — so the pre-fix `validation.code == "nonzero_exit"` check misses it.
    validation = validate_verification_artifact(run_dir / "verification.json")
    assert validation.code == "log_sha256_mismatch"
    assert validation.exit_summary and 3 in validation.exit_summary.get("commands", [])

    plan = tmp_path / "phase-plan-v1-BUILD.md"
    plan.write_text("---\nphase: BUILD\n---\n# BUILD\n", encoding="utf-8")
    payload = build_phase_loop_closeout(
        phase_alias="BUILD",
        plan_path=plan,
        terminal_summary={
            "terminal_status": "complete",
            "verification_status": "passed",
            "artifact_paths": {"root": str(run_dir)},
        },
    )
    assert payload["verification"]["agent_reported_verification_status"] == "passed"
    assert payload["verification"]["status"] in {"failed", "blocked"}, "a red suite must never be a warning"
    assert payload["terminal_status"] != "complete"


# --- codex#4: env_refresh runs pip under the resolved interpreter, not sys.executable ---

def test_env_refresh_pip_uses_resolved_interpreter(tmp_path):
    import sys

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0"\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    run_dir = repo / ".phase-loop" / "run"
    interp = _resolve_suite_interpreter(repo, repo / ".phase-loop" / "probe", python_pin=None)
    assert interp.shim_dir is not None and interp.interpreter is not None  # host default 3.10 < 3.11

    result = run_verification(
        repo, run_dir, commands=[],
        suite_command=None,
        # Pip install argv baked with sys.executable (host 3.10), as resolve_install_command does.
        env_refresh={"triggered": True, "manifests": ["pyproject.toml"], "install_argv": [sys.executable, "-m", "pip", "--version"]},
        timeout_s=60.0,
    )
    assert result.env_refresh is not None
    assert result.env_refresh.install_argv[0] == interp.interpreter, result.env_refresh.install_argv
    assert result.env_refresh.install_argv[0] != sys.executable


# --- codex#3: pin must satisfy requires-python; bare `python` gap ---

def test_pin_below_requires_python_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0"\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    # Pin an interpreter that does NOT satisfy >=3.11 (host python3.10).
    interp = _resolve_suite_interpreter(repo, repo / ".phase-loop" / "run", python_pin="python3.10")
    assert interp.shim_dir is None
    assert interp.blocker is not None and "does not satisfy requires-python" in interp.blocker


def test_bare_python_below_floor_shims_even_when_python3_satisfies(tmp_path, monkeypatch):
    # Craft a PATH where bare `python` is 3.10 (below floor) but `python3` is 3.12
    # (satisfies). The resolver must still shim (so a bare `python` in the suite is
    # not silently the old one).
    py310 = Path("/usr/bin/python3.10")
    py312 = Path("/home/viperjuice/.local/bin/python3.12")
    if not (py310.exists() and py312.exists()):
        pytest.skip("need both python3.10 and python3.12 on host")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").symlink_to(py310)       # bare python = 3.10 (below floor)
    (bindir / "python3").symlink_to(py312)      # python3 = 3.12 (satisfies)
    (bindir / "python3.12").symlink_to(py312)   # so _lowest_satisfying_interpreter can find it
    monkeypatch.setenv("PATH", str(bindir))

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "0"\nrequires-python = ">=3.11"\n', encoding="utf-8"
    )
    interp = _resolve_suite_interpreter(repo, repo / ".phase-loop" / "run", python_pin=None)
    assert interp.blocker is None
    assert interp.shim_dir is not None, "bare python below floor must force a shim even if python3 satisfies"
    # The shim's bare `python` now resolves to a satisfying interpreter.
    out = subprocess.check_output(
        [str(interp.shim_dir / "python"), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], text=True
    ).strip()
    assert tuple(int(x) for x in out.split(".")) >= (3, 11)


# --- gemini#5/#1: trusted-path collapsed owned dir with a tracked+ignored member ---

def test_trusted_path_collapsed_dir_tracked_member_commits(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "GEN", roadmap, owned_files=("gen/*.py",))
    gen = repo / "gen"
    gen.mkdir()
    (gen / "out.py").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-f", "gen/out.py")
    _git(repo, "commit", "-m", "track gen/out.py")
    (repo / ".gitignore").write_text("gen/\n", encoding="utf-8")
    commit_fixture_paths(repo, "ignore gen", plan, repo / ".gitignore")
    (gen / "out.py").write_text("v2 real work\n", encoding="utf-8")  # tracked+ignored, modified
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # TRUSTED path: executor reports the COLLAPSED owned dir in phase_owned_dirty_paths.
    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"GEN": "awaiting_phase_closeout"}, current_phase="GEN",
        phase_owned_dirty=True, phase_owned_dirty_paths=("gen/",),
        dirty_paths=("gen/",), closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "GEN", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )
    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before
    assert "gen/out.py" in _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
