from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .runtime_paths import ensure_phase_loop_excluded, phase_loop_dir
from .runtime_paths import _path_slug

# Platform-conditional locking primitives (issue #16).
# POSIX uses fcntl.flock; Windows uses msvcrt.locking on a 1-byte region at
# the file's current seek position. Both surface non-blocking exclusive lock
# acquisition + release through the same _acquire_exclusive / _release_exclusive
# helpers so the rest of this module stays platform-agnostic.
if sys.platform == "win32":
    import msvcrt

    _LOCK_REGION_BYTES = 1

    def _acquire_exclusive(handle) -> None:
        # msvcrt.locking locks a region of length _LOCK_REGION_BYTES starting
        # at the file's current seek position. Seek to start so concurrent
        # holders contend on the same byte. LK_NBLCK = non-blocking exclusive.
        # Raises OSError (subclass of BlockingIOError on Py3.10+? actually
        # raises OSError with errno EACCES) when contended; normalize to
        # BlockingIOError to match POSIX flock semantics.
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_REGION_BYTES)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc

    def _release_exclusive(handle) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_REGION_BYTES)
        except OSError:
            # Already released or process is exiting — let cleanup proceed.
            pass

else:
    import fcntl

    def _acquire_exclusive(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_exclusive(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
            _acquire_exclusive(handle)
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
            _release_exclusive(self._handle)
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
