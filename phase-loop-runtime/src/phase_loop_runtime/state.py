from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .git_topology import attach_git_topology
from .models import StateSnapshot, WorkUnitState, utc_now
from .runtime_paths import ensure_phase_loop_excluded, phase_loop_state_file, phase_loop_state_read_file


def state_path(repo: Path) -> Path:
    return phase_loop_state_file(repo)


def state_read_path(repo: Path) -> Path:
    return phase_loop_state_read_file(repo)


def write_state(repo: Path, snapshot: StateSnapshot) -> None:
    ensure_phase_loop_excluded(repo)
    snapshot = attach_git_topology(repo, snapshot)
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="state.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot.to_json(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_state(repo: Path) -> StateSnapshot | None:
    path = state_read_path(repo)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    work_units = _valid_work_unit_records(data.get("work_units", {}))
    latest_work_unit = data.get("latest_work_unit")
    if isinstance(latest_work_unit, dict):
        try:
            WorkUnitState.from_json(latest_work_unit)
        except (KeyError, TypeError, ValueError):
            latest_work_unit = None
    else:
        latest_work_unit = None
    return StateSnapshot(
        timestamp=data["timestamp"],
        repo=data["repo"],
        roadmap=data["roadmap"],
        phases=data.get("phases", {}),
        current_phase=data.get("current_phase"),
        last_action=data.get("last_action"),
        model=data.get("model"),
        reasoning_effort=data.get("reasoning_effort"),
        source=data.get("source"),
        override_reason=data.get("override_reason"),
        human_required=data.get("human_required", False),
        blocker_class=data.get("blocker_class"),
        blocker_summary=data.get("blocker_summary"),
        required_human_inputs=tuple(data.get("required_human_inputs", ())),
        access_attempts=tuple(data.get("access_attempts", ())),
        dirty_paths=tuple(data.get("dirty_paths", ())),
        phase_owned_dirty_paths=tuple(data.get("phase_owned_dirty_paths", ())),
        unowned_dirty_paths=tuple(data.get("unowned_dirty_paths", ())),
        pre_existing_dirty_paths=tuple(data.get("pre_existing_dirty_paths", ())),
        phase_owned_dirty=data.get("phase_owned_dirty", False),
        terminal_summary=data.get("terminal_summary"),
        latest_metric=data.get("latest_metric"),
        metrics_summary=data.get("metrics_summary"),
        closeout_terminal_status=data.get("closeout_terminal_status"),
        closeout_summary=data.get("closeout_summary"),
        work_units=work_units,
        latest_work_unit=latest_work_unit,
        schema_version=data.get("schema_version", 1),
        roadmap_sha256=data.get("roadmap_sha256"),
        phase_sha256=data.get("phase_sha256", {}),
        ledger_warnings=tuple(data.get("ledger_warnings", ())),
        git_topology=data.get("git_topology"),
    )


def _valid_work_unit_records(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    records: dict[str, dict] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            state = WorkUnitState.from_json(value)
        except (KeyError, TypeError, ValueError):
            continue
        records[str(key)] = state.to_json()
    return records


def load_work_unit_state(repo: Path) -> dict[str, WorkUnitState]:
    snapshot = load_state(repo)
    if snapshot is None:
        return {}
    records: dict[str, WorkUnitState] = {}
    for key, value in snapshot.work_units.items():
        if not isinstance(value, dict):
            continue
        try:
            state = WorkUnitState.from_json(value)
        except (KeyError, TypeError, ValueError):
            continue
        records[state.work_unit_id or str(key)] = state
    return records


def write_work_unit_state(repo: Path, state: WorkUnitState, *, roadmap: Path | None = None) -> StateSnapshot:
    snapshot = load_state(repo)
    work_units = dict(snapshot.work_units) if snapshot else {}
    work_units[state.work_unit_id] = state.to_json()
    phases = dict(snapshot.phases) if snapshot else {}
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap or (Path(snapshot.roadmap) if snapshot else "")),
        phases=phases,
        current_phase=snapshot.current_phase if snapshot else state.identity.phase,
        last_action=snapshot.last_action if snapshot else "work_unit",
        human_required=snapshot.human_required if snapshot else False,
        blocker_class=snapshot.blocker_class if snapshot else None,
        blocker_summary=snapshot.blocker_summary if snapshot else None,
        required_human_inputs=snapshot.required_human_inputs if snapshot else (),
        access_attempts=snapshot.access_attempts if snapshot else (),
        dirty_paths=snapshot.dirty_paths if snapshot else (),
        phase_owned_dirty_paths=snapshot.phase_owned_dirty_paths if snapshot else (),
        unowned_dirty_paths=snapshot.unowned_dirty_paths if snapshot else (),
        pre_existing_dirty_paths=snapshot.pre_existing_dirty_paths if snapshot else (),
        phase_owned_dirty=snapshot.phase_owned_dirty if snapshot else False,
        terminal_summary=snapshot.terminal_summary if snapshot else None,
        latest_metric=snapshot.latest_metric if snapshot else None,
        metrics_summary=snapshot.metrics_summary if snapshot else None,
        closeout_terminal_status=snapshot.closeout_terminal_status if snapshot else None,
        closeout_summary=snapshot.closeout_summary if snapshot else None,
        work_units=work_units,
        latest_work_unit=state.to_json(),
        schema_version=snapshot.schema_version if snapshot else 2,
        roadmap_sha256=snapshot.roadmap_sha256 if snapshot else None,
        phase_sha256=snapshot.phase_sha256 if snapshot else {},
        ledger_warnings=snapshot.ledger_warnings if snapshot else (),
        git_topology=snapshot.git_topology if snapshot else None,
    )
    write_state(repo, snapshot)
    return snapshot
