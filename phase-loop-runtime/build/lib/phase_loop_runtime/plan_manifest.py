from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .discovery import PLAN_RE


SCHEMA_VERSION = 1
# Repo-relative default only; resolution always joins this against an explicit
# repo root supplied by the caller (see _manifest_path) -- never an implicit
# fleet-absolute hardcode (DECOUPLE SL-2).
MANIFEST_PATH = PurePosixPath("plans/manifest.json")
PLAN_TYPES = {"phase", "detailed"}
PLAN_STATUSES = {"imported", "committed", "executing", "completed", "failed", "orphaned"}
TRANSITIONS = {
    "imported": {"executing", "orphaned"},
    "committed": {"executing", "orphaned"},
    "executing": {"completed", "failed", "orphaned"},
}


@dataclass(frozen=True)
class DotfilesPlanRef:
    slug: str
    file: str
    type: str
    status: str


@dataclass(frozen=True)
class DotfilesPlanLifecycleEvent:
    transition: str
    by: str
    at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DotfilesPlanEntry:
    slug: str
    file: str
    type: str
    status: str
    created_at: str
    updated_at: str
    owner_skill: str
    handoff_ref: str | None = None
    reflection_ref: str | None = None
    task_summary: str | None = None
    acceptance_criteria_count: int | None = None
    roadmap_ref: DotfilesPlanRef | None = None
    phase_alias: str | None = None
    if_gates_produced: tuple[str, ...] = ()
    lanes: tuple[str, ...] = ()
    lifecycle: tuple[DotfilesPlanLifecycleEvent, ...] = ()


@dataclass(frozen=True)
class DotfilesPlanManifest:
    schema_version: int = SCHEMA_VERSION
    plans: tuple[DotfilesPlanEntry, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


def read_manifest(repo: Path) -> DotfilesPlanManifest:
    manifest_path = _manifest_path(repo)
    if not manifest_path.exists():
        return DotfilesPlanManifest()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest JSON is malformed at line {exc.lineno} column {exc.colno}") from exc
    manifest = _manifest_from_json(data)
    if manifest.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema_version: {manifest.schema_version}")
    return manifest


def append_entry(repo: Path, entry: DotfilesPlanEntry) -> None:
    manifest = read_manifest(repo)
    entries = {existing.slug: existing for existing in manifest.plans}
    entries[entry.slug] = entry
    _write_manifest(repo, DotfilesPlanManifest(plans=tuple(entries[slug] for slug in sorted(entries))))


def update_lifecycle(repo: Path, slug: str, transition: str, by: str, metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    manifest = read_manifest(repo)
    now = _utc_now()
    entries: list[DotfilesPlanEntry] = []
    updated = False
    for entry in manifest.plans:
        if entry.slug != slug:
            entries.append(entry)
            continue
        allowed = TRANSITIONS.get(entry.status, set())
        if transition not in allowed:
            raise ValueError(f"invalid lifecycle transition for {slug}: {entry.status} -> {transition}")
        event = DotfilesPlanLifecycleEvent(transition=transition, by=by, at=now, metadata=metadata)
        entries.append(
            DotfilesPlanEntry(
                slug=entry.slug,
                file=entry.file,
                type=entry.type,
                status=transition,
                created_at=entry.created_at,
                updated_at=now,
                owner_skill=entry.owner_skill,
                handoff_ref=entry.handoff_ref,
                reflection_ref=entry.reflection_ref,
                task_summary=entry.task_summary,
                acceptance_criteria_count=entry.acceptance_criteria_count,
                roadmap_ref=entry.roadmap_ref,
                phase_alias=entry.phase_alias,
                if_gates_produced=entry.if_gates_produced,
                lanes=entry.lanes,
                lifecycle=(*entry.lifecycle, event),
            )
        )
        updated = True
    if not updated:
        raise ValueError(f"manifest entry not found: {slug}")
    _write_manifest(repo, DotfilesPlanManifest(plans=tuple(entries)))


def validate_manifest(manifest_path: Path) -> ValidationResult:
    errors: list[str] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ValidationResult(False, ("manifest file does not exist",))
    except json.JSONDecodeError as exc:
        return ValidationResult(False, (f"manifest JSON is malformed at line {exc.lineno} column {exc.colno}",))
    if not isinstance(data, dict):
        return ValidationResult(False, ("manifest must be an object",))
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    plans = data.get("plans")
    if not isinstance(plans, list):
        errors.append("plans must be an array")
        return ValidationResult(False, tuple(errors))
    repo = manifest_path.parent.parent
    seen: set[str] = set()
    for index, raw_entry in enumerate(plans):
        label = f"plans[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{label} must be an object")
            continue
        slug = raw_entry.get("slug")
        if not isinstance(slug, str) or not slug:
            errors.append(f"{label}.slug is required")
        elif slug in seen:
            errors.append(f"{label}.slug duplicates {slug}")
        else:
            seen.add(slug)
        _validate_common_entry(label, raw_entry, repo, errors)
        if raw_entry.get("type") == "phase":
            _validate_phase_entry(label, raw_entry, errors)
        elif raw_entry.get("type") == "detailed":
            _validate_detailed_entry(label, raw_entry, errors)
        _validate_lifecycle(label, raw_entry.get("lifecycle"), errors)
    return ValidationResult(not errors, tuple(errors))


def import_existing_phase_plans(repo: Path) -> DotfilesPlanManifest:
    entries: list[DotfilesPlanEntry] = []
    for plan_path in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
        match = PLAN_RE.search(plan_path.name)
        if not match:
            continue
        version, phase_alias = match.groups()
        rel_path = plan_path.relative_to(repo).as_posix()
        slug = f"{version}-{phase_alias}"
        timestamp = _timestamp_for_path(plan_path)
        roadmap_file = _frontmatter_value(plan_path, "roadmap")
        roadmap_ref = (
            DotfilesPlanRef(
                slug=Path(roadmap_file).stem,
                file=roadmap_file,
                type="phase",
                status="imported",
            )
            if roadmap_file
            else None
        )
        entries.append(
            DotfilesPlanEntry(
                slug=slug,
                file=rel_path,
                type="phase",
                status="imported",
                created_at=timestamp,
                updated_at=timestamp,
                owner_skill="codex-plan-phase",
                roadmap_ref=roadmap_ref,
                phase_alias=phase_alias,
                if_gates_produced=_extract_if_gates(plan_path),
                lanes=_extract_lanes(plan_path),
            )
        )
    return DotfilesPlanManifest(plans=tuple(entries))


def _manifest_path(repo: Path) -> Path:
    return Path(repo) / MANIFEST_PATH


def _write_manifest(repo: Path, manifest: DotfilesPlanManifest) -> None:
    manifest_path = _manifest_path(repo)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_manifest_to_json(manifest), indent=2, sort_keys=True) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") == payload:
        return
    manifest_path.write_text(payload, encoding="utf-8")


def _manifest_to_json(manifest: DotfilesPlanManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "plans": [_entry_to_json(entry) for entry in manifest.plans],
    }


def _entry_to_json(entry: DotfilesPlanEntry) -> dict[str, Any]:
    return {
        "acceptance_criteria_count": entry.acceptance_criteria_count,
        "created_at": entry.created_at,
        "file": entry.file,
        "handoff_ref": entry.handoff_ref,
        "if_gates_produced": list(entry.if_gates_produced),
        "lanes": list(entry.lanes),
        "lifecycle": [_event_to_json(event) for event in entry.lifecycle],
        "owner_skill": entry.owner_skill,
        "phase_alias": entry.phase_alias,
        "reflection_ref": entry.reflection_ref,
        "roadmap_ref": _ref_to_json(entry.roadmap_ref) if entry.roadmap_ref else None,
        "slug": entry.slug,
        "status": entry.status,
        "task_summary": entry.task_summary,
        "type": entry.type,
        "updated_at": entry.updated_at,
    }


def _event_to_json(event: DotfilesPlanLifecycleEvent) -> dict[str, Any]:
    return {
        "at": event.at,
        "by": event.by,
        "metadata": event.metadata,
        "transition": event.transition,
    }


def _ref_to_json(ref: DotfilesPlanRef) -> dict[str, Any]:
    return {"file": ref.file, "slug": ref.slug, "status": ref.status, "type": ref.type}


def _manifest_from_json(data: Any) -> DotfilesPlanManifest:
    if not isinstance(data, dict):
        raise ValueError("manifest must be an object")
    plans = data.get("plans", [])
    if not isinstance(plans, list):
        raise ValueError("manifest plans must be an array")
    return DotfilesPlanManifest(
        schema_version=int(data.get("schema_version", 0)),
        plans=tuple(_entry_from_json(entry) for entry in plans),
    )


def _entry_from_json(data: Any) -> DotfilesPlanEntry:
    if not isinstance(data, dict):
        raise ValueError("manifest entry must be an object")
    roadmap_ref = data.get("roadmap_ref")
    return DotfilesPlanEntry(
        slug=str(data.get("slug", "")),
        file=str(data.get("file", "")),
        type=str(data.get("type", "")),
        status=str(data.get("status", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        owner_skill=str(data.get("owner_skill", "")),
        handoff_ref=data.get("handoff_ref"),
        reflection_ref=data.get("reflection_ref"),
        task_summary=data.get("task_summary"),
        acceptance_criteria_count=data.get("acceptance_criteria_count"),
        roadmap_ref=_ref_from_json(roadmap_ref) if roadmap_ref is not None else None,
        phase_alias=data.get("phase_alias"),
        if_gates_produced=tuple(data.get("if_gates_produced") or ()),
        lanes=tuple(data.get("lanes") or ()),
        lifecycle=tuple(_event_from_json(event) for event in data.get("lifecycle") or ()),
    )


def _event_from_json(data: Any) -> DotfilesPlanLifecycleEvent:
    if not isinstance(data, dict):
        raise ValueError("manifest lifecycle event must be an object")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("manifest lifecycle metadata must be an object")
    return DotfilesPlanLifecycleEvent(
        transition=str(data.get("transition", "")),
        by=str(data.get("by", "")),
        at=str(data.get("at", "")),
        metadata=metadata,
    )


def _ref_from_json(data: Any) -> DotfilesPlanRef:
    if not isinstance(data, dict):
        raise ValueError("manifest plan ref must be an object")
    return DotfilesPlanRef(
        slug=str(data.get("slug", "")),
        file=str(data.get("file", "")),
        type=str(data.get("type", "")),
        status=str(data.get("status", "")),
    )


def _validate_common_entry(label: str, entry: dict[str, Any], repo: Path, errors: list[str]) -> None:
    for field_name in ("file", "type", "status", "created_at", "updated_at", "owner_skill"):
        if not isinstance(entry.get(field_name), str) or not entry.get(field_name):
            errors.append(f"{label}.{field_name} is required")
    if entry.get("type") not in PLAN_TYPES:
        errors.append(f"{label}.type must be phase or detailed")
    if entry.get("status") not in PLAN_STATUSES:
        errors.append(f"{label}.status is invalid")
    file_value = entry.get("file")
    if isinstance(file_value, str) and file_value:
        if Path(file_value).is_absolute() or ".." in PurePosixPath(file_value).parts:
            errors.append(f"{label}.file must be repo-relative")
        elif not (repo / file_value).exists():
            errors.append(f"{label}.file does not exist")


def _validate_phase_entry(label: str, entry: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(entry.get("phase_alias"), str) or not entry.get("phase_alias"):
        errors.append(f"{label}.phase_alias is required for phase entries")
    if entry.get("task_summary") is not None or entry.get("acceptance_criteria_count") is not None:
        errors.append(f"{label} mixes detailed-only fields into a phase entry")
    if not isinstance(entry.get("if_gates_produced"), list):
        errors.append(f"{label}.if_gates_produced must be an array")
    if not isinstance(entry.get("lanes"), list):
        errors.append(f"{label}.lanes must be an array")
    roadmap_ref = entry.get("roadmap_ref")
    if roadmap_ref is not None and not isinstance(roadmap_ref, dict):
        errors.append(f"{label}.roadmap_ref must be an object or null")


def _validate_detailed_entry(label: str, entry: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(entry.get("task_summary"), str) or not entry.get("task_summary"):
        errors.append(f"{label}.task_summary is required for detailed entries")
    if not isinstance(entry.get("acceptance_criteria_count"), int):
        errors.append(f"{label}.acceptance_criteria_count is required for detailed entries")
    if entry.get("roadmap_ref") is not None or entry.get("phase_alias") is not None:
        errors.append(f"{label} mixes phase-only fields into a detailed entry")
    if entry.get("if_gates_produced") not in (None, []):
        errors.append(f"{label}.if_gates_produced must be empty for detailed entries")
    if entry.get("lanes") not in (None, []):
        errors.append(f"{label}.lanes must be empty for detailed entries")


def _validate_lifecycle(label: str, lifecycle: Any, errors: list[str]) -> None:
    if not isinstance(lifecycle, list):
        errors.append(f"{label}.lifecycle must be an array")
        return
    for index, event in enumerate(lifecycle):
        event_label = f"{label}.lifecycle[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{event_label} must be an object")
            continue
        for field_name in ("transition", "by", "at"):
            if not isinstance(event.get(field_name), str) or not event.get(field_name):
                errors.append(f"{event_label}.{field_name} is required")
        if not isinstance(event.get("metadata"), dict):
            errors.append(f"{event_label}.metadata must be an object")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_for_path(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _frontmatter_value(path: Path, field_name: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    match = re.search(rf"^{re.escape(field_name)}:\s*(.+?)\s*$", text[4:end], re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_if_gates(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(dict.fromkeys(re.findall(r"\bIF-[A-Za-z0-9._-]+", text)))


def _extract_lanes(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(dict.fromkeys(re.findall(r"^###\s+(SL-\d+[A-Z]?)\b", text, re.MULTILINE)))
