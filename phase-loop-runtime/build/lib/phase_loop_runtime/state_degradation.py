from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import utc_now
from .runtime_paths import ensure_phase_loop_excluded, phase_loop_executor_degradation_file


DEMOTION_MODES = ("proof_gated", "manual_only")


@dataclass(frozen=True)
class ExecutorDegradationRecord:
    since: str
    ttl_seconds: int
    demoted_to: str
    reason: str
    source_phase: str
    blocker_summary: str

    def to_json(self) -> dict[str, object]:
        return {
            "blocker_summary": self.blocker_summary,
            "demoted_to": self.demoted_to,
            "reason": self.reason,
            "since": self.since,
            "source_phase": self.source_phase,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_json(cls, data: object) -> "ExecutorDegradationRecord":
        if not isinstance(data, dict):
            raise ValueError("degradation record must be an object")
        demoted_to = str(data["demoted_to"])
        if demoted_to not in DEMOTION_MODES:
            raise ValueError("invalid demotion mode")
        ttl_seconds = int(data["ttl_seconds"])
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        since = str(data["since"])
        _parse_timestamp(since)
        return cls(
            since=since,
            ttl_seconds=ttl_seconds,
            demoted_to=demoted_to,
            reason=str(data["reason"]),
            source_phase=str(data["source_phase"]),
            blocker_summary=str(data["blocker_summary"]),
        )


def load_degradation(repo: Path) -> dict[str, ExecutorDegradationRecord]:
    path = phase_loop_executor_degradation_file(repo)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    records: dict[str, ExecutorDegradationRecord] = {}
    for executor, value in data.items():
        try:
            records[str(executor)] = ExecutorDegradationRecord.from_json(value)
        except (KeyError, TypeError, ValueError):
            continue
    return records


def record_degradation(
    repo: Path,
    executor: str,
    reason: str,
    source_phase: str,
    blocker_summary: str,
    ttl_seconds: int,
    demoted_to: str = "proof_gated",
) -> None:
    if demoted_to not in DEMOTION_MODES:
        raise ValueError("invalid demotion mode")
    ensure_phase_loop_excluded(repo)
    records = load_degradation(repo)
    records[str(executor)] = ExecutorDegradationRecord(
        since=utc_now(),
        ttl_seconds=int(ttl_seconds),
        demoted_to=demoted_to,
        reason=str(reason),
        source_phase=str(source_phase),
        blocker_summary=str(blocker_summary),
    )
    _write_degradation(repo, records)


def clear(repo: Path) -> None:
    try:
        phase_loop_executor_degradation_file(repo).unlink()
    except FileNotFoundError:
        return


def active_degraded_executors(repo: Path, *, now: datetime | None = None) -> set[str]:
    current = now or datetime.now(timezone.utc)
    return {
        executor
        for executor, record in load_degradation(repo).items()
        if not _expired(record, current)
    }


def _write_degradation(repo: Path, records: dict[str, ExecutorDegradationRecord]) -> None:
    path = phase_loop_executor_degradation_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="executor-degradation.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({key: records[key].to_json() for key in sorted(records)}, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _expired(record: ExecutorDegradationRecord, now: datetime) -> bool:
    return _parse_timestamp(record.since) + timedelta(seconds=record.ttl_seconds) <= now


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
