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


def _ppid(pid: int) -> int | None:
    """Return the parent pid of ``pid`` from /proc/<pid>/status, or None.

    Linux-only via ``/proc``. Any failure (non-Linux, missing /proc, process
    exited mid-read, unparseable field) returns None — the caller treats an
    indeterminate chain as "not my ancestor" and fails closed (competitor)."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("PPid:"):
                    return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def _pid_is_ancestor(candidate: int, of_pid: int) -> bool:
    """True iff ``candidate`` appears on ``of_pid``'s parent chain (up to init).

    This is the injection-free re-entrancy signal for #146: a nested
    release-dispatch executor is a descendant of the outer run that holds the
    lock, so the lock holder is one of its ancestors; two genuinely independent
    runs are never each other's ancestor. Walks PPid from ``of_pid`` upward and
    fails closed (returns False) the moment the chain is indeterminate — an
    unknown chain must NOT be mistaken for legitimate re-entrancy. Bounded so a
    pathological/looping /proc view can never spin.

    Scope note (intended): ancestry trusts the holder's whole process TREE, so
    every descendant of the lock-holding run re-enters. That is correct for this
    lock's purpose — it exists to keep two *independent* runs from dispatching the
    same roadmap at once (a non-descendant always blocks); serialising work WITHIN
    one run's own tree is that run's responsibility, not this file-lock's."""
    if candidate <= 0 or of_pid <= 0:
        return False
    current = of_pid
    for _ in range(64):  # generous ceiling on real ancestry depth; bounds the walk
        parent = _ppid(current)
        if parent is None or parent == current:
            return False
        if parent == candidate:
            return True
        if parent <= 1:  # reached init without finding candidate
            return False
        current = parent
    return False


def holder_is_self(
    holder_pid: int | None,
    holder_run_id: str | None,
    *,
    caller_run_id: str | None = None,
    caller_pid: int | None = None,
) -> bool:
    """True iff a contended lock's holder is the CALLER's own launch (re-entrant).

    Two positive, fail-closed signals — never a heuristic that could green-light a
    genuine competitor:

    * ``run_id`` match — the strongest signal, available once RUNCORE2 injects a
      stable ``caller_run_id`` (survives ``setsid``/process-group splits);
    * ancestry — the injection-free base fix: the holder pid is an ancestor of the
      caller pid, so the caller is running *inside* the holder's run.

    Everything else (unknown holder, no shared identity, indeterminate ancestry)
    returns False so the caller raises :class:`DispatchLockContention` exactly as
    before (fail-closed, preserves the guard against real concurrent dispatch)."""
    if caller_run_id and holder_run_id and caller_run_id == holder_run_id:
        return True
    if holder_pid is not None and holder_pid > 0:
        me = caller_pid if caller_pid is not None else os.getpid()
        # Ancestry — NOT bare pid-equality: a nested executor is a distinct child
        # whose outer run is its ancestor, while a genuine second dispatch launched
        # from the same shell is a sibling (never an ancestor) and must still block.
        if _pid_is_ancestor(holder_pid, me):
            return True
    return False


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
    def __init__(self, repo: Path, roadmap: Path, *, caller_run_id: str | None = None) -> None:
        self.repo = repo
        self.roadmap = roadmap
        # A stable per-run identity the caller may inject so a nested executor can
        # recognise its OWN outer lock across a setsid/process-group split (#146).
        # Optional: with no run_id the ancestry signal alone still resolves the
        # common nested case. RUNCORE2 owns injecting this at the runner call sites.
        self.caller_run_id = caller_run_id
        self.path = dispatch_lock_path(repo, roadmap)
        self._handle = None
        # True when acquire() found the lock already held by this caller's OWN run
        # and re-entered without taking a second flock (nothing to release).
        self.reentrant = False

    def acquire(self) -> "DispatchLock":
        ensure_phase_loop_excluded(self.repo)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            _acquire_exclusive(handle)
        except BlockingIOError as exc:
            holder = _read_holder_metadata(handle)
            handle.close()
            holder_pid = _int_or_none(holder.get("pid"))
            holder_run_id = str(holder.get("run_id") or "") or None
            # #146: a wrapped release-dispatch executor necessarily runs inside its
            # own outer run, which already holds this lock. Re-entering its OWN
            # lock is legitimate; only a DIFFERENT live dispatch is a competitor.
            if holder_is_self(holder_pid, holder_run_id, caller_run_id=self.caller_run_id):
                self.reentrant = True
                self._handle = None
                return self
            raise DispatchLockContention(
                lock_path=self.path,
                holder_pid=holder_pid,
                holder_started_at=_float_or_none(holder.get("started_at")),
                roadmap=str(holder.get("roadmap") or "") or None,
            ) from exc

        handle.seek(0)
        handle.truncate()
        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "repo": str(self.repo),
            "roadmap": str(self.roadmap),
        }
        if self.caller_run_id:
            payload["run_id"] = self.caller_run_id
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def release(self) -> None:
        # A re-entrant acquire took no new flock (self._handle is None); releasing
        # must NOT drop the outer run's lock — the no-handle guard makes it a no-op.
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
