from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .runtime_paths import phase_loop_event_file


@dataclass(frozen=True)
class MigrationResult:
    migrated: int
    already_migrated: int
    backup: str | None = None
    dry_run: bool = False

    def to_json(self) -> dict[str, object]:
        if self.dry_run:
            return {
                "would_migrate": self.migrated,
                "already_migrated": self.already_migrated,
            }
        return {
            "migrated": self.migrated,
            "already_migrated": self.already_migrated,
            "backup": self.backup,
        }


class MigrationError(RuntimeError):
    pass


def migrate_ledger(repo: Path, *, dry_run: bool, backup_suffix: str) -> MigrationResult:
    repo = Path(repo).expanduser().resolve()
    event_path = phase_loop_event_file(repo)
    source = event_path.read_text(encoding="utf-8") if event_path.exists() else ""
    lines = source.splitlines()
    trailing_newline = source.endswith("\n")

    parsed: list[dict | None] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            parsed.append(None)
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MigrationError(f"malformed JSONL at line {index}: {exc.msg}") from exc
        if not isinstance(event, dict):
            raise MigrationError(f"malformed JSONL at line {index}: expected object")
        parsed.append(event)

    migrated = 0
    already_migrated = 0
    rewritten_lines = list(lines)
    for index, event in enumerate(parsed):
        if event is None:
            continue
        if _is_executor_closeout(event, action="executor.closeout"):
            already_migrated += 1
            continue
        if not _is_executor_closeout(event, action="run"):
            continue
        migrated += 1
        rewritten = dict(event)
        rewritten["action"] = "executor.closeout"
        rewritten_lines[index] = json.dumps(rewritten, separators=(",", ":"))

    if dry_run:
        return MigrationResult(migrated=migrated, already_migrated=already_migrated, dry_run=True)

    backup_path = event_path.with_name(event_path.name + backup_suffix)
    if migrated == 0:
        backup = str(backup_path) if backup_path.exists() else None
        return MigrationResult(migrated=0, already_migrated=already_migrated, backup=backup)
    if backup_path.exists():
        raise MigrationError(f"backup path already exists: {backup_path}")

    event_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(source, encoding="utf-8")
    tmp_path = event_path.with_name(event_path.name + ".tmp-def4-migrate")
    output = "\n".join(rewritten_lines)
    if trailing_newline and output:
        output += "\n"
    tmp_path.write_text(output, encoding="utf-8")
    os.replace(tmp_path, event_path)
    return MigrationResult(migrated=migrated, already_migrated=already_migrated, backup=str(backup_path))


def _is_executor_closeout(event: dict, *, action: str) -> bool:
    if event.get("action") != action:
        return False
    metadata = event.get("metadata")
    return isinstance(metadata, dict) and isinstance(metadata.get("executor_closeout_event"), dict)
