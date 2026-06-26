from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from .closeout import phase_loop_closeout_diagnostic
from .git_topology import attach_git_topology, collect_git_topology
from .models import LoopEvent, WorkUnitEventMetadata
from .runtime_paths import ensure_phase_loop_excluded, phase_loop_event_file, phase_loop_event_read_files


def event_path(repo: Path) -> Path:
    return phase_loop_event_file(repo)


def event_read_paths(repo: Path) -> tuple[Path, ...]:
    return phase_loop_event_read_files(repo)


def append_event(repo: Path, event: LoopEvent) -> None:
    ensure_phase_loop_excluded(repo)
    event = _event_with_closeout_metadata(event)
    event = attach_git_topology(repo, event)
    path = event_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_jsonl(path, event.to_json())


def append_work_unit_event(repo: Path, event: WorkUnitEventMetadata, *, roadmap: Path | None = None) -> None:
    ensure_phase_loop_excluded(repo)
    payload = event.to_json()
    closeout = _extract_work_unit_closeout(payload)
    if closeout is not None:
        payload["metadata"] = _pipeline_metadata(closeout)
    payload.update(
        {
            "repo": str(repo),
            "roadmap": str(roadmap or ""),
            "phase": event.identity.phase,
            "action": "work_unit",
            "status": event.status,
            "source": "work_unit_ledger",
            "schema_version": 2,
            "git_topology": collect_git_topology(repo),
        }
    )
    path = event_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_jsonl(path, payload)


def _append_jsonl(path: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def read_events(repo: Path) -> list[dict]:
    events: list[dict] = []
    for path in event_read_paths(repo):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                event.setdefault("schema_version", 1)
                events.append(event)
    return events


def read_work_unit_events(repo: Path, work_unit_id: str | None = None) -> list[dict]:
    records: list[dict] = []
    for event in read_events(repo):
        if event.get("event_kind") != "work_unit":
            continue
        work_unit = event.get("work_unit")
        if not isinstance(work_unit, dict):
            metadata = event.get("metadata")
            work_unit = metadata.get("work_unit") if isinstance(metadata, dict) else None
        if not isinstance(work_unit, dict):
            continue
        if work_unit_id is not None and work_unit.get("work_unit_id") != work_unit_id:
            continue
        records.append(event)
    return records


def _event_with_closeout_metadata(event: LoopEvent) -> LoopEvent:
    metadata = dict(event.metadata or {})
    closeout = _extract_closeout(metadata)
    if closeout is None:
        return event
    metadata.update(_pipeline_metadata(closeout))
    return replace(event, metadata=metadata)


def _extract_closeout(metadata: dict) -> dict | None:
    closeout = metadata.get("phase_loop_closeout")
    if not isinstance(closeout, dict):
        terminal = metadata.get("terminal_summary")
        closeout = terminal.get("phase_loop_closeout") if isinstance(terminal, dict) else None
    if isinstance(closeout, dict) and phase_loop_closeout_diagnostic(closeout) is None:
        return closeout
    return None


def _extract_work_unit_closeout(payload: dict) -> dict | None:
    work_unit = payload.get("work_unit")
    if not isinstance(work_unit, dict):
        return None
    closeout_summary = work_unit.get("closeout_summary")
    closeout = closeout_summary.get("phase_loop_closeout") if isinstance(closeout_summary, dict) else None
    if isinstance(closeout, dict) and phase_loop_closeout_diagnostic(closeout) is None:
        return closeout
    return None


def _pipeline_metadata(closeout: dict) -> dict:
    source_bundle = closeout.get("source_bundle") if isinstance(closeout.get("source_bundle"), dict) else {}
    artifacts = closeout.get("artifacts") if isinstance(closeout.get("artifacts"), dict) else {}
    verification = closeout.get("verification") if isinstance(closeout.get("verification"), dict) else {}
    blocker = closeout.get("blocker") if isinstance(closeout.get("blocker"), dict) else {}
    impact = closeout.get("source_truth_impact") if isinstance(closeout.get("source_truth_impact"), dict) else {}
    return {
        "phase_loop_closeout": closeout,
        "phase_alias": closeout.get("phase_alias") or closeout.get("phase"),
        "pipeline_phase_id": source_bundle.get("phase_id") or closeout.get("pipeline_phase_id"),
        "source_bundle_path": source_bundle.get("path") or closeout.get("source_bundle_path"),
        "source_bundle_sha256": source_bundle.get("sha256") or closeout.get("source_bundle_sha256"),
        "pipeline_mode": source_bundle.get("pipeline_mode"),
        "verification_status": verification.get("status") or closeout.get("verification_status"),
        "changed_paths": artifacts.get("changed_paths") or closeout.get("changed_paths") or [],
        "evidence_refs": artifacts.get("evidence_refs") or closeout.get("evidence_refs") or [],
        "blocker_class": blocker.get("blocker_class"),
        "source_truth_impact": impact,
        "canonical_refresh_recommended": impact.get("canonical_refresh_recommended", False),
        "canonical_refresh_reason_codes": impact.get("canonical_refresh_reason_codes") or [],
        "changed_path_boundaries": impact.get("changed_path_boundaries") or [],
        "changed_path_categories": sorted(
            {
                str(item.get("category"))
                for item in impact.get("changed_path_boundaries") or []
                if isinstance(item, dict) and item.get("category")
            }
        ),
    }
