"""v45 FOUND — frozen runner-hardening contracts (IF-0-FOUND-1..4).

Test-first coverage for the pure scheduling/ownership/reconcile contracts that
RECONCILE (IF-0-RECONCILE-1) and SCHED (IF-0-SCHED-1) build on. These pin the
exact frozen signatures; the runner main-loop wiring is intentionally out of
scope here (it lands in SCHED).
"""
from __future__ import annotations

from pathlib import Path

from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.models import PHASE_SCHEDULER_MODES, LaneIRDiagnostic
from phase_loop_runtime.discovery import (
    compute_ready_phases,
    parse_plan_ownership,
    phase_owned_files,
    reconcile_against_git_reality,
    select_ready_phase_wave,
    validate_concurrent_phase_ownership,
)
from phase_loop_test_utils import make_repo, write_named_roadmap, write_phase_plan


# --- IF-0-FOUND-1: scheduler surface -------------------------------------------


def test_phase_scheduler_modes_frozen():
    assert PHASE_SCHEDULER_MODES == ("off", "serialized", "concurrent")


def test_cli_phase_scheduler_arg_defaults_off():
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.phase_scheduler_mode == "off"


def test_cli_phase_scheduler_arg_accepts_modes():
    parser = build_parser()
    for mode in PHASE_SCHEDULER_MODES:
        # top-level arg (like --lane-scheduler): precedes the subcommand
        args = parser.parse_args(["--phase-scheduler", mode, "run"])
        assert args.phase_scheduler_mode == mode


def test_cli_phase_scheduler_rejects_unknown_mode():
    parser = build_parser()
    try:
        parser.parse_args(["--phase-scheduler", "parallel", "run"])
    except SystemExit:
        return
    raise AssertionError("unknown --phase-scheduler mode was accepted")


def test_select_ready_phase_wave_serialized_returns_one():
    waves = (("A", "B"), ("C",))
    assert select_ready_phase_wave(waves, {}, "off") == ("A",)
    assert select_ready_phase_wave(waves, {}, "serialized") == ("A",)


def test_select_ready_phase_wave_concurrent_returns_full_wave():
    waves = (("A", "B"), ("C",))
    assert select_ready_phase_wave(waves, {}, "concurrent") == ("A", "B")


def test_select_ready_phase_wave_skips_complete_and_blocked():
    waves = (("A", "B"), ("C", "D"))
    classifications = {"A": "complete", "B": "blocked"}
    # first wave fully consumed → advances to the next wave
    assert select_ready_phase_wave(waves, classifications, "concurrent") == ("C", "D")
    assert select_ready_phase_wave(waves, classifications, "serialized") == ("C",)


def test_select_ready_phase_wave_empty_when_all_done():
    waves = (("A",), ("B",))
    assert select_ready_phase_wave(waves, {"A": "complete", "B": "complete"}, "concurrent") == ()


# --- IF-0-FOUND-2: cross-phase ownership ---------------------------------------


def test_phase_owned_files_returns_plan_owned_patterns(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
    write_phase_plan(repo, "ALPHA", roadmap, owned_files=("src/a.py", "src/b.py"))
    assert phase_owned_files(repo, roadmap, "ALPHA") == ("src/a.py", "src/b.py")


def test_phase_owned_files_empty_when_unplanned(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"),))
    # no plan artifact written for ALPHA
    assert phase_owned_files(repo, roadmap, "ALPHA") == ()


def test_phase_owned_files_empty_for_control_only(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("CTRL", "Control"),))
    write_phase_plan(repo, "CTRL", roadmap)  # default body = read-only lane (no owned files)
    assert phase_owned_files(repo, roadmap, "CTRL") == ()
    # and the underlying ownership is is_control_only (valid + empty-owned)
    from phase_loop_runtime.discovery import find_plan_artifact

    plan = find_plan_artifact(repo, "CTRL", roadmap)
    assert parse_plan_ownership(repo, roadmap, plan).is_control_only is True


def test_validate_concurrent_phase_ownership_flags_overlap(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
    write_phase_plan(repo, "ALPHA", roadmap, owned_files=("src/shared.py",))
    write_phase_plan(repo, "BETA", roadmap, owned_files=("src/shared.py",))
    diagnostics = validate_concurrent_phase_ownership(repo, roadmap, ("ALPHA", "BETA"))
    assert len(diagnostics) == 1
    assert isinstance(diagnostics[0], LaneIRDiagnostic)
    assert diagnostics[0].kind == "overlapping_write_ownership"
    assert diagnostics[0].details == {"left": "ALPHA", "right": "BETA"}


def test_validate_concurrent_phase_ownership_clean_when_disjoint(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
    write_phase_plan(repo, "ALPHA", roadmap, owned_files=("src/a.py",))
    write_phase_plan(repo, "BETA", roadmap, owned_files=("src/b.py",))
    assert validate_concurrent_phase_ownership(repo, roadmap, ("ALPHA", "BETA")) == ()


# --- IF-0-FOUND-3: readiness ---------------------------------------------------


def _roadmap_with_deps(repo: Path) -> Path:
    roadmap = repo / "specs" / "phase-plans-v1.md"
    roadmap.write_text(
        "# Roadmap\n\n"
        "### Phase 1 — Found (FOUND)\n\n"
        "**Depends on**\n- (none)\n\n"
        "### Phase 2 — Branch (BRANCH)\n\n"
        "**Depends on**\n- FOUND\n\n"
        "### Phase 3 — Sched (SCHED)\n\n"
        "**Depends on**\n- FOUND\n- BRANCH\n"
    )
    return roadmap


def test_compute_ready_phases_respects_dependencies(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = _roadmap_with_deps(repo)
    # nothing completed → only the root (FOUND) is ready
    assert compute_ready_phases(roadmap, {}, set()) == ("FOUND",)
    # FOUND complete → BRANCH becomes ready (SCHED still waits on BRANCH)
    assert compute_ready_phases(roadmap, {"FOUND": "complete"}, {"FOUND"}) == ("BRANCH",)
    # FOUND + BRANCH complete → SCHED ready
    ready = compute_ready_phases(
        roadmap, {"FOUND": "complete", "BRANCH": "complete"}, {"FOUND", "BRANCH"}
    )
    assert ready == ("SCHED",)


def test_compute_ready_phases_skips_blocked(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = _roadmap_with_deps(repo)
    # FOUND done but BRANCH blocked → BRANCH excluded; SCHED not ready (BRANCH not complete)
    assert compute_ready_phases(roadmap, {"FOUND": "complete", "BRANCH": "blocked"}, {"FOUND"}) == ()


# --- IF-0-FOUND-4: reconcile hook (no-op in FOUND) -----------------------------


def test_reconcile_against_git_reality_is_identity_noop(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"),))
    classifications = {"ALPHA": "unplanned"}
    result = reconcile_against_git_reality(repo, roadmap, classifications)
    assert result == classifications
    # returns a copy, not the same object (so callers can mutate safely)
    assert result is not classifications
