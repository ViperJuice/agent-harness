from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from .models import BLOCKER_CLASSES, CLOSEOUT_SCHEMA, DISPATCH_CAPABILITIES, EXECUTORS, PHASE_STATUSES


VERIFICATION_STATUSES = tuple(CLOSEOUT_SCHEMA["properties"]["verification_status"]["enum"])


@dataclass(frozen=True)
class DriftFinding:
    repo: str
    field: str
    literal: str
    source: str
    first_seen: str | None = None

    def key(self) -> tuple[str, str, str]:
        return (self.repo, self.field, self.literal)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "field": self.field,
            "literal": self.literal,
            "source": self.source,
            "first_seen": self.first_seen,
        }


@dataclass(frozen=True)
class DriftCount:
    repo: str
    field: str
    literal: str
    count: int
    first_seen: str | None
    sources: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "field": self.field,
            "literal": self.literal,
            "count": self.count,
            "first_seen": self.first_seen,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class SetupDiagnostic:
    repo: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"repo": self.repo, "message": self.message}


@dataclass(frozen=True)
class RepoScanSummary:
    repo: str
    terminal_summaries_scanned: int = 0
    events_scanned: int = 0
    malformed_events: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "terminal_summaries_scanned": self.terminal_summaries_scanned,
            "events_scanned": self.events_scanned,
            "malformed_events": self.malformed_events,
        }


@dataclass(frozen=True)
class DriftAuditResult:
    repos: tuple[RepoScanSummary, ...]
    days: int
    scope: str
    cutoff_timestamp: str
    allowlists: dict[str, tuple[str, ...]]
    drift_counts: tuple[DriftCount, ...] = field(default_factory=tuple)
    setup_diagnostics: tuple[SetupDiagnostic, ...] = field(default_factory=tuple)

    def has_drift(self) -> bool:
        return bool(self.drift_counts)

    def has_setup_errors(self) -> bool:
        return bool(self.setup_diagnostics)

    def is_clean(self) -> bool:
        return not self.has_drift() and not self.has_setup_errors()

    def to_json(self) -> dict[str, Any]:
        return {
            "days": self.days,
            "scope": self.scope,
            "cutoff_timestamp": self.cutoff_timestamp,
            "allowlists": {key: list(values) for key, values in sorted(self.allowlists.items())},
            "repos": [repo.to_json() for repo in self.repos],
            "counts": {
                "repos": len(self.repos),
                "drift_findings": sum(item.count for item in self.drift_counts),
                "setup_errors": len(self.setup_diagnostics),
            },
            "drift": [item.to_json() for item in self.drift_counts],
            "setup_diagnostics": [item.to_json() for item in self.setup_diagnostics],
        }

    def render_text(self) -> str:
        lines = [
            "Closeout Drift Audit",
            f"scope: {self.scope}",
            f"days: {self.days}",
            f"cutoff: {self.cutoff_timestamp}",
            "",
        ]
        for repo in self.repos:
            lines.extend(
                [
                    f"Repo: {repo.repo}",
                    f"  terminal summaries scanned: {repo.terminal_summaries_scanned}",
                    f"  events scanned: {repo.events_scanned}",
                    f"  malformed events: {repo.malformed_events}",
                ]
            )
        if self.setup_diagnostics:
            lines.append("")
            lines.append("Setup diagnostics:")
            for diagnostic in self.setup_diagnostics:
                lines.append(f"  {diagnostic.repo}: {diagnostic.message}")
        lines.append("")
        if not self.drift_counts:
            lines.append("Drift: none")
            return "\n".join(lines)
        lines.append("Drift:")
        current_field = None
        for count in self.drift_counts:
            if count.field != current_field:
                current_field = count.field
                lines.append(f"  {count.field}:")
            first_seen = count.first_seen or "unknown"
            lines.append(f"    {count.literal}: count={count.count} first_seen={first_seen} repo={count.repo}")
        return "\n".join(lines)


def run_drift_audit(repos: Iterable[str | Path], days: int, scope: str) -> DriftAuditResult:
    if days < 0:
        return DriftAuditResult(
            repos=(),
            days=days,
            scope=scope,
            cutoff_timestamp=_now().isoformat().replace("+00:00", "Z"),
            allowlists=_allowlists(),
            setup_diagnostics=(SetupDiagnostic(repo="", message="days must be non-negative"),),
        )
    if scope not in {"closeout", "all-events"}:
        return DriftAuditResult(
            repos=(),
            days=days,
            scope=scope,
            cutoff_timestamp=_now().isoformat().replace("+00:00", "Z"),
            allowlists=_allowlists(),
            setup_diagnostics=(SetupDiagnostic(repo="", message="scope must be closeout or all-events"),),
        )

    now = _now()
    cutoff = now - timedelta(days=days)
    repo_summaries: list[RepoScanSummary] = []
    setup_diagnostics: list[SetupDiagnostic] = []
    findings: list[DriftFinding] = []

    for repo_input in repos:
        repo = Path(repo_input).expanduser().resolve()
        repo_label = str(repo)
        if not repo.exists() or not repo.is_dir():
            setup_diagnostics.append(SetupDiagnostic(repo=repo_label, message="repo path is missing or not a directory"))
            continue
        phase_loop = repo / ".phase-loop"
        if not phase_loop.exists():
            setup_diagnostics.append(SetupDiagnostic(repo=repo_label, message=".phase-loop directory is missing"))
            continue
        repo_findings, summary = _scan_repo(repo, cutoff, scope)
        findings.extend(repo_findings)
        repo_summaries.append(summary)

    return DriftAuditResult(
        repos=tuple(sorted(repo_summaries, key=lambda item: item.repo)),
        days=days,
        scope=scope,
        cutoff_timestamp=cutoff.isoformat().replace("+00:00", "Z"),
        allowlists=_allowlists(),
        drift_counts=_collapse_findings(findings),
        setup_diagnostics=tuple(sorted(setup_diagnostics, key=lambda item: (item.repo, item.message))),
    )


def _scan_repo(repo: Path, cutoff: datetime, scope: str) -> tuple[list[DriftFinding], RepoScanSummary]:
    findings: list[DriftFinding] = []
    terminal_summaries_scanned = 0
    events_scanned = 0
    malformed_events = 0

    for path in sorted((repo / ".phase-loop" / "runs").glob("**/terminal-summary.json")):
        event_time = _file_timestamp(path)
        if event_time and event_time < cutoff:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            malformed_events += 1
            continue
        terminal_summaries_scanned += 1
        findings.extend(_scan_payload(payload, repo=str(repo), source=_relative(path, repo), first_seen=_timestamp_from_payload(payload) or _format_time(event_time)))

    events_path = repo / ".phase-loop" / "events.jsonl"
    if events_path.exists():
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
            malformed_events += 1
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed_events += 1
                continue
            event_time = _timestamp_from_payload(event)
            if event_time and _parse_time(event_time) and _parse_time(event_time) < cutoff:
                continue
            if scope == "closeout" and not _is_closeout_class_event(event):
                continue
            events_scanned += 1
            findings.extend(
                _scan_payload(
                    event,
                    repo=str(repo),
                    source=f"{_relative(events_path, repo)}:{line_number}",
                    first_seen=event_time,
                )
            )

    return findings, RepoScanSummary(
        repo=str(repo),
        terminal_summaries_scanned=terminal_summaries_scanned,
        events_scanned=events_scanned,
        malformed_events=malformed_events,
    )


def _scan_payload(payload: Any, *, repo: str, source: str, first_seen: str | None) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    for path, key, value in _walk_items(payload):
        field = _field_for(path, key)
        if field is None or not isinstance(value, str):
            continue
        allowed = _allowlists()[field]
        if value not in allowed and value != "none":
            findings.append(DriftFinding(repo=repo, field=field, literal=value, source=source, first_seen=first_seen))
    return findings


def _walk_items(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_str = str(key)
            yield path, key_str, child
            yield from _walk_items(child, path + (key_str,))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from _walk_items(child, path)
            elif path:
                yield path, path[-1], child


def _field_for(path: tuple[str, ...], key: str) -> str | None:
    if key in {"terminal_status", "closeout_terminal_status"}:
        return "terminal_status"
    if key == "verification_status":
        return "verification_status"
    if key == "blocker_class":
        return "blocker_class"
    if key in {"executor", "selected_executor", "parent_executor", "child_executor", "target_executor", "harness_target"}:
        return "executor"
    if key == "capability" or key == "required_capability" or path[-1:] == ("required_capabilities",):
        return "dispatch_capability"
    return None


def _is_closeout_class_event(event: dict[str, Any]) -> bool:
    action = str(event.get("action", ""))
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return (
        "closeout" in action
        or action in {"manual_repair", "phase_complete", "phase_reopen", "reconcile"}
        or any(key in metadata for key in ("terminal_summary", "automation", "closeout"))
    )


def _collapse_findings(findings: Iterable[DriftFinding]) -> tuple[DriftCount, ...]:
    buckets: dict[tuple[str, str, str], list[DriftFinding]] = {}
    for finding in findings:
        buckets.setdefault(finding.key(), []).append(finding)
    counts: list[DriftCount] = []
    for (repo, field, literal), items in buckets.items():
        first_seen_values = sorted(item.first_seen for item in items if item.first_seen)
        sources = tuple(sorted({item.source for item in items}))
        counts.append(
            DriftCount(
                repo=repo,
                field=field,
                literal=literal,
                count=len(items),
                first_seen=first_seen_values[0] if first_seen_values else None,
                sources=sources,
            )
        )
    return tuple(sorted(counts, key=lambda item: (item.field, item.literal, item.repo)))


def _allowlists() -> dict[str, tuple[str, ...]]:
    return {
        "terminal_status": PHASE_STATUSES,
        "verification_status": VERIFICATION_STATUSES,
        "blocker_class": BLOCKER_CLASSES,
        "executor": EXECUTORS,
        "dispatch_capability": DISPATCH_CAPABILITIES,
    }


def _timestamp_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("timestamp", "created_at", "updated_at"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("timestamp", "created_at", "updated_at"):
            value = metadata.get(key)
            if isinstance(value, str):
                return value
    return None


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _file_timestamp(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def _now() -> datetime:
    return datetime.now(timezone.utc)
