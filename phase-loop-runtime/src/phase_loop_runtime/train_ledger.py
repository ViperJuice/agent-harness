"""Durable append-only train ledger.

Records per-node status transitions for a cross-repo release train.  State
lives **entirely outside any repo's ``.phase-loop/``** — it is coordinator-
owned.

Durability model (self-consistent):
  - Each append is a single ``os.write(fd, encoded)`` on a file opened with
    ``O_APPEND``.  The OS guarantees atomicity for writes ≤ PIPE_BUF on
    local filesystems (typically 4 KiB); our records are far smaller.
  - The resume reader is **tolerant**: if the final line of the file is
    malformed (e.g. the coordinator crashed mid-write), that trailing line is
    **silently dropped** — it is not yet committed state.  A malformed line
    at any position OTHER than the last fails loud (unexpected corruption).
  - No temp-rename.  ``events.py:read_events`` does a bare ``json.loads`` and
    crashes on a truncated final line — this reader is **net-new**, not a
    mirror of that function.

Record shape (one JSON object per line)::

    {
        "node_id":           "<repo>/<roadmap>",
        "status":            "pending|running|pr_open|approved|merged|blocked",
        "branch":            "<branch-name or null>",
        "pr_url":            "<PR URL or null>",
        "upstream_merge_sha": "<SHA or null>",
        "merge_order":       <int or null>,
        "ts":                "<ISO-8601 UTC timestamp>"
    }

Current state = last-record-wins per ``node_id`` (fold over append log).
``merged`` records carry the actual merge SHA as ``upstream_merge_sha``.

Zero external deps (stdlib only).
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Status vocabulary (IF-0-P2-1)

VALID_STATUSES = frozenset(
    {"pending", "running", "pr_open", "approved", "merged", "blocked"}
)


# ---------------------------------------------------------------------------
# Record shape

@dataclass
class LedgerRecord:
    """One append to the train ledger."""

    node_id: str
    status: str
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    upstream_merge_sha: Optional[str] = None
    merge_order: Optional[int] = None
    ts: str = ""  # ISO-8601 UTC; auto-set on append if blank

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"invalid ledger status {self.status!r}; "
                f"expected one of: {', '.join(sorted(VALID_STATUSES))}"
            )

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


# ---------------------------------------------------------------------------
# Ledger path convention

def default_ledger_path(coordinator_dir: Path, train_name: str) -> Path:
    """Return the default ledger file path for a named train.

    The path is inside ``coordinator_dir`` which must **not** be under any
    repo's ``.phase-loop/``.
    """
    return coordinator_dir / f"train-{train_name}.ledger.jsonl"


# ---------------------------------------------------------------------------
# Append

def append_record(path: Path, record: LedgerRecord) -> None:
    """Atomically append ``record`` to the ledger at ``path``.

    Uses ``O_APPEND`` with a single ``os.write`` call for durability.  The
    parent directory is created if absent.

    The caller is responsible for ensuring ``path`` is outside any repo's
    ``.phase-loop/`` directory.
    """
    if not record.ts:
        record = LedgerRecord(
            node_id=record.node_id,
            status=record.status,
            branch=record.branch,
            pr_url=record.pr_url,
            upstream_merge_sha=record.upstream_merge_sha,
            merge_order=record.merge_order,
            ts=_utc_now(),
        )
    _assert_not_phase_loop(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record.to_dict(), sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Resume reader (tolerant — drops malformed trailing line)

def read_ledger(path: Path) -> Dict[str, LedgerRecord]:
    """Read the ledger, returning the **current state** per node (last-wins fold).

    Tolerant resume reader:
    - An empty file returns ``{}``.
    - A malformed **final** line (crashed mid-write) is silently dropped.
    - A malformed line at any other position raises :exc:`ValueError` (unexpected
      corruption; don't silently paper over mid-file damage).
    """
    if not path.exists():
        return {}

    raw_lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    non_empty = [ln for ln in raw_lines if ln.strip()]
    if not non_empty:
        return {}

    state: Dict[str, LedgerRecord] = {}
    for i, line in enumerate(non_empty):
        stripped = line.strip()
        is_last = i == len(non_empty) - 1
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            if is_last:
                # Tolerant: crashed mid-write — drop the trailing line
                break
            raise ValueError(
                f"malformed ledger line {i + 1} (not the final line — unexpected "
                f"corruption): {stripped!r}"
            )

        try:
            record = _dict_to_record(obj)
        except (KeyError, ValueError) as exc:
            if is_last:
                break
            raise ValueError(
                f"invalid ledger record at line {i + 1}: {exc}"
            ) from exc

        state[record.node_id] = record

    return state


def resume_state(path: Path) -> Dict[str, LedgerRecord]:
    """Alias for :func:`read_ledger` — returns the current per-node state."""
    return read_ledger(path)


# ---------------------------------------------------------------------------
# Helpers

def _dict_to_record(obj: dict) -> LedgerRecord:
    return LedgerRecord(
        node_id=obj["node_id"],
        status=obj["status"],
        branch=obj.get("branch"),
        pr_url=obj.get("pr_url"),
        upstream_merge_sha=obj.get("upstream_merge_sha"),
        merge_order=obj.get("merge_order"),
        ts=obj.get("ts", ""),
    )


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _assert_not_phase_loop(path: Path) -> None:
    """Fail loud if the ledger path is inside any repo's ``.phase-loop/``."""
    parts = path.parts
    for part in parts:
        if part in {".phase-loop", "phase-loop"}:
            raise ValueError(
                f"train ledger path '{path}' is inside a '.phase-loop/' directory — "
                f"train state must never touch any repo's .phase-loop/; "
                f"use a coordinator-owned directory instead"
            )
