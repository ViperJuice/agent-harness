from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from importlib import util
from pathlib import Path
from typing import Iterable

from .discovery import parse_frontmatter, repo_identity
from .runtime_paths import phase_loop_dir, phase_loop_event_file, phase_loop_state_file


TERMINAL_PHASE_STATES = {"complete", "unplanned", "none"}
ALLOWED_RECENT_ACTIONS = {"closeout", "manual_repair"}


@dataclass(frozen=True)
class MigrationRecord:
    skill_name: str
    source: str
    target: str
    action: str
    status: str


def _shared_resolver():
    # Import directly from the package so resolution works whether running
    # from source tree or installed via pip. The package-local module mirrors
    # shared/phase-loop/handoff_path.py until NEUTRALIZE consolidates them.
    from .handoff_path import resolve_handoff_path
    return resolve_handoff_path


def _legacy_skill_roots(home: Path | None = None) -> tuple[Path, ...]:
    home = Path(home or Path.home()).expanduser()
    return (
        home / ".claude" / "skills",
        home / ".codex" / "skills",
        home / ".gemini" / "skills",
        home / ".gemini" / "antigravity" / "skills",
        home / ".config" / "opencode" / "skills",
    )


def _quiesced(repo: Path) -> bool:
    return quiescence_blocker(repo) is None


def quiescence_blocker(repo: Path) -> str | None:
    loop_dir = phase_loop_dir(repo)
    state_path = phase_loop_state_file(repo)
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return f"state file is unreadable: {state_path}"
        phases = data.get("phases")
        if isinstance(phases, dict):
            for phase, status in phases.items():
                if str(status) not in TERMINAL_PHASE_STATES:
                    return f"phase {phase} is {status}"
    event_path = phase_loop_event_file(repo)
    now = time.time()
    if event_path.exists():
        try:
            lines = event_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return f"event ledger is unreadable: {event_path}"
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = _event_epoch(event.get("timestamp"))
            if timestamp is None or now - timestamp > 60:
                continue
            action = str(event.get("action") or "")
            if action not in ALLOWED_RECENT_ACTIONS:
                return f"recent event action {action or 'unknown'} is active"
    if loop_dir.exists():
        lock = next(loop_dir.glob("*.lock"), None)
        if lock is not None:
            return f"lock file exists: {lock.name}"
    return None


def migrate_handoffs(repo: Path, *, apply: bool = False, home: Path | None = None) -> tuple[MigrationRecord, ...]:
    repo = repo.expanduser().resolve()
    if apply:
        blocker = quiescence_blocker(repo)
        if blocker is not None:
            return (
                MigrationRecord(
                    skill_name="",
                    source="",
                    target="",
                    action="blocked",
                    status=f"not_quiesced: {blocker}",
                ),
            )
    records: list[MigrationRecord] = []
    for latest in _candidate_latest_files(home):
        records.extend(_records_for_latest(repo, latest, apply=apply))
    return tuple(records)


def records_to_json(records: Iterable[MigrationRecord]) -> str:
    return json.dumps([asdict(record) for record in records], indent=2, sort_keys=True)


def _candidate_latest_files(home: Path | None) -> Iterable[Path]:
    for root in _legacy_skill_roots(home):
        if not root.exists():
            continue
        yield from sorted(root.glob("*/handoffs/**/latest.md"))


def _records_for_latest(repo: Path, latest: Path, *, apply: bool) -> tuple[MigrationRecord, ...]:
    skill_name = _skill_name_from_latest(latest)
    text = _read_text(latest)
    if text is None:
        return (_record(skill_name, latest, repo, "skip", "unreadable"),)
    if not _matches_current_repo(repo, text):
        return (_record(skill_name, latest, repo, "skip", "other_repo_or_malformed"),)
    target_latest = _shared_resolver()(repo, skill_name)
    source_files = sorted(path for path in latest.parent.iterdir() if path.is_file() and path.suffix == ".md")
    records: list[MigrationRecord] = []
    for source in source_files:
        target = target_latest.parent / source.name
        action, status = _migrate_one(source, target, apply=apply)
        records.append(MigrationRecord(skill_name, str(source), str(target), action, status))
    return tuple(records)


def _migrate_one(source: Path, target: Path, *, apply: bool) -> tuple[str, str]:
    if target.exists():
        if _read_text(source) == _read_text(target):
            if apply:
                source.unlink()
            return ("noop", "already_migrated")
        return ("skip", "target_exists_different")
    if not apply:
        return ("move", "dry_run")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return ("move", "migrated")


def _skill_name_from_latest(latest: Path) -> str:
    for parent in latest.parents:
        if parent.name == "handoffs":
            return parent.parent.name
    return latest.parents[3].name


def _matches_current_repo(repo: Path, text: str) -> bool:
    frontmatter = parse_frontmatter(text)
    if not frontmatter:
        return False
    identity = repo_identity(repo)
    repo_root = frontmatter.get("repo_root")
    if repo_root:
        try:
            if Path(repo_root).expanduser().resolve() != repo:
                return False
        except OSError:
            return False
    repo_key = frontmatter.get("repo")
    if repo_key and repo_key not in {identity.repo_hash, str(repo)}:
        return False
    branch_slug = frontmatter.get("branch_slug")
    if branch_slug and branch_slug != identity.branch_slug:
        return False
    return bool(repo_root or repo_key)


def _record(skill_name: str, source: Path, repo: Path, action: str, status: str) -> MigrationRecord:
    target = _shared_resolver()(repo, skill_name) if skill_name else Path("")
    return MigrationRecord(skill_name, str(source), str(target), action, status)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _event_epoch(raw: object) -> float | None:
    if not isinstance(raw, str):
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
