from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .runtime_paths import ensure_phase_loop_excluded, phase_loop_dir
from .runtime_paths import _path_slug


@dataclass(frozen=True)
class DispatchLockContention(RuntimeError):
    lock_path: Path
    holder_pid: int | None
    holder_started_at: float | None
    roadmap: str | None

    @property
    def elapsed_seconds(self) -> int | None:
        if self.holder_started_at is None:
            return None
        return max(0, int(time.time() - self.holder_started_at))

    def blocker_summary(self, roadmap: Path) -> str:
        pid = self.holder_pid if self.holder_pid is not None else "unknown"
        elapsed = self.elapsed_seconds
        elapsed_text = f"{elapsed}s" if elapsed is not None else "unknown seconds"
        holder_roadmap = self.roadmap or str(roadmap)
        return (
            "Concurrent dispatch refused: roadmap "
            f"{holder_roadmap} is locked by PID {pid} for {elapsed_text} "
            f"(lock: {self.lock_path})."
        )


class DispatchLock:
    def __init__(self, repo: Path, roadmap: Path) -> None:
        self.repo = repo
        self.roadmap = roadmap
        self.path = dispatch_lock_path(repo, roadmap)
        self._handle = None

    def acquire(self) -> "DispatchLock":
        ensure_phase_loop_excluded(self.repo)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = _read_holder_metadata(handle)
            handle.close()
            raise DispatchLockContention(
                lock_path=self.path,
                holder_pid=_int_or_none(holder.get("pid")),
                holder_started_at=_float_or_none(holder.get("started_at")),
                roadmap=str(holder.get("roadmap") or "") or None,
            ) from exc

        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "started_at": time.time(),
                "repo": str(self.repo),
                "roadmap": str(self.roadmap),
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "DispatchLock":
        if self._handle is not None:
            return self
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def dispatch_lock_path(repo: Path, roadmap: Path) -> Path:
    try:
        roadmap_key = str(roadmap.resolve().relative_to(repo.resolve()))
    except ValueError:
        roadmap_key = str(roadmap.resolve())
    return phase_loop_dir(repo) / _path_slug(roadmap_key) / "dispatch.lock"


def _read_holder_metadata(handle) -> dict[str, object]:
    try:
        handle.seek(0)
        raw = handle.read().strip()
        payload = json.loads(raw) if raw else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
