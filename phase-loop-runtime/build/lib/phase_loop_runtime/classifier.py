from __future__ import annotations

import subprocess
from pathlib import Path

from .discovery import (
    WORKFLOW_EXECUTE_SKILLS,
    WORKFLOW_PLAN_SKILLS,
    _phase_complete_in_git_reality,
    find_plan_artifact,
    handoff_matches_roadmap,
    latest_workflow_handoff,
    parse_automation_status,
    parse_roadmap_phases,
    repo_identity,
)
from .models import PHASE_STATUSES
from .provenance import phase_provenance_map, roadmap_sha256, status_provenance_matches
from .state import load_state


TERMINAL_STATUSES = {"complete", "executed", "awaiting_phase_closeout", "blocked", "unknown"}


def classify_phase(repo: Path, roadmap: Path, phase: str) -> str:
    phase = phase.upper()
    phases = [p.upper() for p in parse_roadmap_phases(roadmap)]
    if phase not in phases:
        return "unknown"

    state = load_state(repo)
    if state and Path(state.roadmap).expanduser().resolve() == roadmap.resolve() and state.phases.get(phase) in PHASE_STATUSES:
        status = state.phases[phase]
        current_phase_sha = phase_provenance_map(roadmap).get(phase)
        entry_phase_sha = state.phase_sha256.get(phase)
        if (status in TERMINAL_STATUSES or status in {"executing", "planned"}) and status_provenance_matches(
            status,
            state.roadmap_sha256,
            entry_phase_sha,
            roadmap_sha256(roadmap),
            current_phase_sha,
        ):
            if status == "planned" and find_plan_artifact(repo, phase, roadmap=roadmap) is None:
                return "unplanned"
            return status

    identity = repo_identity(repo)
    for skills in (WORKFLOW_EXECUTE_SKILLS, WORKFLOW_PLAN_SKILLS):
        handoff = latest_workflow_handoff(identity, repo, roadmap, skills)
        status = (handoff or {}).get("automation_status")
        if status in PHASE_STATUSES and handoff_matches_roadmap(repo, phase, roadmap, handoff):
            return status

    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if not plan:
        # IF-0-RECONCILE-1: before declaring a phase unplanned (which would
        # re-plan it into a divergent filename), consult merged git reality — a
        # phase completed under an earlier roadmap version that was merely
        # renamed/advanced, and whose work still exists at HEAD, is complete.
        # Shared predicate (gated by PHASE_LOOP_RECONCILE_GIT_REALITY, default
        # off) so this and reconcile_against_git_reality stay in lockstep.
        if _phase_complete_in_git_reality(repo, roadmap, phase):
            return "complete"
        return "unplanned"
    automation_status = parse_automation_status(plan.read_text(encoding="utf-8")).get("automation_status")
    if automation_status in PHASE_STATUSES:
        return str(automation_status)
    if _path_status(repo, plan).startswith("??"):
        return "planned"
    if plan.exists():
        return "planned"
    return "unknown"


def classify_all(repo: Path, roadmap: Path) -> dict[str, str]:
    return {phase.upper(): classify_phase(repo, roadmap, phase) for phase in parse_roadmap_phases(roadmap)}


def _path_status(repo: Path, path: Path) -> str:
    try:
        rel = path.relative_to(repo)
    except ValueError:
        rel = path
    try:
        return subprocess.check_output(["git", "-C", str(repo), "status", "--short", "--", str(rel)], text=True).strip()
    except Exception:
        return ""
