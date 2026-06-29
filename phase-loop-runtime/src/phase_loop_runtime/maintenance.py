from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .events import append_event
from .handoff import write_tui_handoff
from .launcher import LaunchResult, build_codex_command, launch
from .models import Blocker, LoopEvent, StateSnapshot, utc_now
from .observability import operator_halt_metadata, run_artifacts, stop_requested
from .profiles import resolve_profile
from .prompts import build_skill_maintenance_prompt
from .provenance import roadmap_sha256, snapshot_provenance
from .reconcile import reconcile
from .runtime_paths import ensure_phase_loop_excluded, phase_loop_dir
from .skill_inventory import (
    classify_skill_like_directories,
    inspect_bridge_skill_inventory,
    inspect_vestigial_workflow_candidates,
    inspect_workflow_skill_inventory,
)
from .state import write_state


@dataclass(frozen=True)
class MaintenanceOptions:
    min_reflections: int = 2
    apply_skill_edits: bool = False
    allow_skills: tuple[str, ...] = ()
    improvement_plan: Path | None = None


@dataclass(frozen=True)
class SyncSkillsOptions:
    harnesses: tuple[str, ...] = ("codex", "claude", "gemini", "opencode")
    apply: bool = False


def collect_reflection_inventory(repo: Path) -> dict[str, object]:
    roots = (
        Path.home() / ".codex" / "skills",
        repo / "codex-config" / "skills",
    )
    by_root: list[dict[str, object]] = []
    total = 0
    for root in roots:
        count = 0
        if root.exists():
            for reflections in root.glob("codex-*/reflections"):
                for path in reflections.rglob("*"):
                    if path.is_file() and "archive" not in path.parts:
                        count += 1
        total += count
        by_root.append({"root": str(root), "count": count})
    return {"total": total, "roots": by_root}


def validate_maintenance_options(options: MaintenanceOptions) -> Blocker | None:
    if options.apply_skill_edits and not options.allow_skills:
        return Blocker(
            human_required=True,
            blocker_class="product_decision_missing",
            blocker_summary="Skill edits require at least one --allow-skill codex-* target.",
            required_human_inputs=("--allow-skill codex-*",),
        )
    if options.apply_skill_edits and options.improvement_plan is None:
        return Blocker(
            human_required=True,
            blocker_class="product_decision_missing",
            blocker_summary="Skill edits require --improvement-plan for the approved planner artifact.",
            required_human_inputs=("--improvement-plan path",),
        )
    invalid = tuple(skill for skill in options.allow_skills if not skill.startswith("codex-"))
    if invalid:
        return Blocker(
            human_required=True,
            blocker_class="product_decision_missing",
            blocker_summary="Skill maintenance allowlist accepts only codex-* skill names.",
            required_human_inputs=("codex-* allowlist only",),
        )
    return None


def sync_bridge_skills(repo: Path, options: SyncSkillsOptions) -> dict[str, object]:
    blocker = active_loop_blocker(repo, desired_mode="sync-skills") if options.apply else None
    records = [record.to_json() for record in inspect_bridge_skill_inventory(repo, options.harnesses)]
    workflow_records = [record.to_json() for record in inspect_workflow_skill_inventory(repo, options.harnesses)]
    vestigial_records = [record.to_json() for record in inspect_vestigial_workflow_candidates(repo)]
    classification_records = [record.to_json() for record in classify_skill_like_directories(repo, options.harnesses)]
    changed: list[dict[str, str]] = []
    unrepaired: list[dict[str, str]] = []
    if blocker:
        return {
            "mode": "apply" if options.apply else "check",
            "repo": str(repo),
            "harnesses": list(options.harnesses),
            "bridge_skills": records,
            "workflow_sources": workflow_records,
            "vestigial_workflow_candidates": vestigial_records,
            "skill_classifications": classification_records,
            "changed": changed,
            "unrepaired": unrepaired,
            "blocked": True,
            "blocker": blocker.to_json(),
        }
    if options.apply:
        for record in records:
            if record.get("parity_status") == "ok":
                continue
            source_dir = record.get("source_dir")
            repair_target = record.get("repair_target")
            if not isinstance(source_dir, str) or not isinstance(repair_target, str):
                # #14: never silently skip — record what could not be repaired so
                # `--apply` cannot mimic `--check` with a clean exit 0.
                unrepaired.append(
                    {
                        "harness_target": str(record.get("harness_target")),
                        "skill_name": str(record.get("skill_name")),
                        "parity_status": str(record.get("parity_status")),
                        "reason": "no_source_resolved" if not isinstance(source_dir, str) else "no_repair_target",
                    }
                )
                continue
            _repair_bridge_skill(Path(source_dir), Path(repair_target))
            changed.append(
                {
                    "harness_target": str(record["harness_target"]),
                    "skill_name": str(record["skill_name"]),
                    "repair_target": repair_target,
                }
            )
        records = [record.to_json() for record in inspect_bridge_skill_inventory(repo, options.harnesses)]
        workflow_records = [record.to_json() for record in inspect_workflow_skill_inventory(repo, options.harnesses)]
        classification_records = [record.to_json() for record in classify_skill_like_directories(repo, options.harnesses)]
    return {
        "mode": "apply" if options.apply else "check",
        "repo": str(repo),
        "harnesses": list(options.harnesses),
        "bridge_skills": records,
        "workflow_sources": workflow_records,
        "vestigial_workflow_candidates": vestigial_records,
        "skill_classifications": classification_records,
        "changed": changed,
        "unrepaired": unrepaired,
        "blocked": False,
        "blocker": None,
    }


def run_maintenance(
    repo: Path,
    roadmap: Path,
    options: MaintenanceOptions,
    model_profile: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    dry_run: bool = False,
    json_output: bool = False,
    observe: bool = True,
    stream_output: bool = False,
    bypass_approvals: bool = False,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
    heartbeat_enabled: bool = True,
) -> tuple[StateSnapshot, list[LaunchResult]]:
    phases = reconcile(repo, roadmap).phases
    selection = resolve_profile(model_profile or "skill-maintenance", model=model, effort=effort)
    inventory = collect_reflection_inventory(repo)
    blocker = validate_maintenance_options(options) or active_loop_blocker(repo, desired_mode="skill-maintenance")
    if blocker:
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases=phases,
            current_phase=next((phase for phase, status in phases.items() if status != "complete"), None),
            last_action="maintain-skills",
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            human_required=blocker.human_required,
            blocker_class=blocker.blocker_class,
            blocker_summary=blocker.blocker_summary,
            required_human_inputs=blocker.required_human_inputs,
            **snapshot_provenance(roadmap),
        )
        _write_state_and_handoff(repo, roadmap, snapshot, results=[])
        append_event(repo, _maintenance_event(repo, roadmap, "blocked", selection, inventory, blocker=blocker))
        return snapshot, []

    prompt = build_skill_maintenance_prompt(options)
    command = build_codex_command(repo, selection, prompt, json_output=json_output, bypass_approvals=bypass_approvals)
    if stop_requested(repo):
        metadata = operator_halt_metadata(repo)
        metadata["reflection_inventory"] = inventory
        append_event(repo, _maintenance_event(repo, roadmap, "unknown", selection, inventory, command=command, metadata=metadata))
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases=phases,
            current_phase=next((phase for phase, status in phases.items() if status != "complete"), None),
            last_action="maintain-skills",
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            **snapshot_provenance(roadmap),
        )
        _write_state_and_handoff(repo, roadmap, snapshot, results=[])
        return snapshot, []

    artifacts = run_artifacts(repo, "SKILL-MAINTENANCE", "maintain-skills", 1, command) if observe else {}
    if dry_run:
        result = launch(
            command,
            dry_run=True,
            log_path=artifacts.get("log"),
            heartbeat_path=artifacts.get("heartbeat") if heartbeat_enabled else None,
            stream_output=stream_output,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            quiet_warning_seconds=quiet_warning_seconds,
            quiet_blocker_seconds=quiet_blocker_seconds,
        )
    else:
        with active_loop(repo, "skill-maintenance"):
            result = launch(
                command,
                dry_run=False,
                log_path=artifacts.get("log"),
                heartbeat_path=artifacts.get("heartbeat") if heartbeat_enabled else None,
                stream_output=stream_output,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                quiet_warning_seconds=quiet_warning_seconds,
                quiet_blocker_seconds=quiet_blocker_seconds,
            )
    if result.failed:
        append_event(
            repo,
            _maintenance_event(
                repo,
                roadmap,
                "unknown",
                selection,
                inventory,
                command=command,
                metadata={"launch": result.event_metadata(), "artifacts": {key: str(value) for key, value in artifacts.items()}},
            ),
        )
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases=phases,
            current_phase=next((phase for phase, status in phases.items() if status != "complete"), None),
            last_action="maintain-skills",
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            **snapshot_provenance(roadmap),
        )
        _write_state_and_handoff(repo, roadmap, snapshot, results=[result])
        return snapshot, [result]
    status = "planned" if dry_run else "executing"
    append_event(
        repo,
        _maintenance_event(
            repo,
            roadmap,
            status,
            selection,
            inventory,
            command=command,
            metadata={"launch": result.event_metadata(), "artifacts": {key: str(value) for key, value in artifacts.items()}} if artifacts else {"launch": result.event_metadata()},
        ),
    )
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases=phases,
        current_phase=next((phase for phase, status in phases.items() if status != "complete"), None),
        last_action="maintain-skills",
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        **snapshot_provenance(roadmap),
    )
    _write_state_and_handoff(repo, roadmap, snapshot, results=[result])
    return snapshot, [result]


def active_loop_blocker(repo: Path, desired_mode: str) -> Blocker | None:
    data = _read_active_loop(repo)
    if not data:
        return None
    mode = str(data.get("mode", ""))
    if mode and mode != desired_mode:
        return Blocker(
            human_required=True,
            blocker_class="dirty_worktree_conflict",
            blocker_summary=f"Active {mode} loop is running; refusing {desired_mode}.",
            required_human_inputs=("wait for active loop to finish",),
        )
    return None


@contextmanager
def active_loop(repo: Path, mode: str) -> Iterator[None]:
    ensure_phase_loop_excluded(repo)
    path = _active_loop_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode, "pid": os.getpid(), "timestamp": utc_now()}, sort_keys=True) + "\n")
    try:
        yield
    finally:
        try:
            if path.exists():
                data = json.loads(path.read_text())
                if data.get("pid") == os.getpid() and data.get("mode") == mode:
                    path.unlink()
        except Exception:
            pass


def _maintenance_event(repo, roadmap, status, selection, inventory, command=None, blocker=None, metadata=None):
    event_metadata = {"reflection_inventory": inventory}
    if metadata:
        event_metadata.update(metadata)
    return LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase="SKILL-MAINTENANCE",
        action="maintain-skills",
        status=status,
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        command=command,
        blocker=blocker.to_json() if blocker else None,
        metadata=event_metadata,
        schema_version=2,
        roadmap_sha256=roadmap_sha256(roadmap),
    )


def _write_state_and_handoff(repo: Path, roadmap: Path, snapshot: StateSnapshot, *, results: list[LaunchResult]) -> None:
    write_state(repo, snapshot)
    write_tui_handoff(repo, roadmap, snapshot, action="maintain-skills", results=results, mode="skill-maintenance")


def _active_loop_path(repo: Path) -> Path:
    return phase_loop_dir(repo) / "active-loop.json"


def _read_active_loop(repo: Path) -> dict[str, object] | None:
    path = _active_loop_path(repo)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"mode": "unknown"}
    pid = data.get("pid")
    if isinstance(pid, int) and not _pid_is_live(pid):
        path.unlink(missing_ok=True)
        return None
    return data


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _repair_bridge_skill(source_dir: Path, repair_target: Path) -> None:
    repair_target.parent.mkdir(parents=True, exist_ok=True)
    if repair_target.exists() or repair_target.is_symlink():
        if repair_target.is_symlink() or repair_target.is_file():
            repair_target.unlink()
        else:
            shutil.rmtree(repair_target)
    repair_target.symlink_to(source_dir, target_is_directory=True)
