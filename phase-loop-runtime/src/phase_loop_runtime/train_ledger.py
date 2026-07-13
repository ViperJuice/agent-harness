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
        "head_sha":          "<draft branch HEAD SHA or null>",
        "upstream_merge_sha": "<merged SHA or null — P4 only>",
        "merge_order":       <int or null>,
        "ts":                "<ISO-8601 UTC timestamp>"
    }

Current state = last-record-wins per ``node_id`` (fold over append log).
``pr_open`` records store the draft branch HEAD SHA in ``head_sha`` (set by P3).
``merged`` records carry the actual merge-commit SHA in ``upstream_merge_sha``
(set by P4 after the merge lands).  Do NOT put a draft head in
``upstream_merge_sha`` — that field is reserved for the real merged SHA so P4
can gate-on-merge without ambiguity.

Zero external deps (stdlib only).
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# Status vocabulary (IF-0-P2-1)

VALID_STATUSES = frozenset(
    {"pending", "running", "pr_open", "approved", "merged", "blocked"}
)


EVENT_SCHEMA_VERSION = "1"
TRANSITION_MODEL_VERSION = "1"
INVALIDATION_MODEL_VERSION = "1"


class CoordinatorEventKind(str, Enum):
    """The append-only boundary represented by a coordinator event."""

    INTENT = "intent"
    OUTCOME = "outcome"


@dataclass(frozen=True)
class CoordinatorEvent:
    """Versioned, append-only coordinator event; intent and outcome never share a record."""

    kind: CoordinatorEventKind
    train_id: str
    node_id: str
    roadmap_path: str
    roadmap_digest: Optional[str]
    workspace_id: Optional[str]
    branch: Optional[str]
    base_ref: Optional[str]
    base_sha: Optional[str]
    head_sha: Optional[str]
    phase: Optional[str]
    action: str
    owned_paths: Tuple[str, ...] = ()
    executor: Optional[str] = None
    model: Optional[str] = None
    upstream_dep_shas: Tuple[str, ...] = ()
    verification_artifact: Optional[str] = None
    verification_digest: Optional[str] = None
    seat_outcomes: Tuple[str, ...] = ()
    pr_identity: Optional[str] = None
    merge_sha: Optional[str] = None
    release_identity: Optional[str] = None
    attempt_id: Optional[str] = None
    epoch: Optional[int] = None
    fence_token: Optional[str] = None
    approval_digest: Optional[str] = None
    expected_version_predicate: Optional[str] = None
    authority_domain_scope: Optional[str] = None
    idempotency_key: Optional[str] = None
    isolation_reason: Optional[str] = None
    timestamp: Optional[str] = None
    blocker_reason: Optional[str] = None
    event_schema_version: str = EVENT_SCHEMA_VERSION
    transition_model_version: str = TRANSITION_MODEL_VERSION
    invalidation_model_version: str = INVALIDATION_MODEL_VERSION

    def __post_init__(self) -> None:
        if not self.train_id or not self.node_id or not self.roadmap_path or not self.action:
            raise ValueError("coordinator events require train, node, roadmap, and action")
        if not all((self.event_schema_version, self.transition_model_version, self.invalidation_model_version)):
            raise ValueError("coordinator event version fields must be explicit")


class ConvergenceResultStatus(str, Enum):
    """Adapter-neutral convergence result statuses."""

    COMPLETED = "completed"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    NEEDS_CLARIFICATION = "needs_clarification"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class ConvergenceResultEnvelope:
    """A result returned uniformly by coordinator adapters without runtime wiring."""

    status: ConvergenceResultStatus
    attempt_id: str
    detail: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.attempt_id:
            raise ValueError("result envelopes require an attempt_id")


# ---------------------------------------------------------------------------
# Record shape

@dataclass
class LedgerRecord:
    """One append to the train ledger."""

    node_id: str
    status: str
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    head_sha: Optional[str] = None           # draft branch HEAD SHA (P3, pr_open records)
    upstream_merge_sha: Optional[str] = None  # merged-commit SHA (P4 only)
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
            head_sha=record.head_sha,
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
        head_sha=obj.get("head_sha"),
        upstream_merge_sha=obj.get("upstream_merge_sha"),
        merge_order=obj.get("merge_order"),
        ts=obj.get("ts", ""),
    )


def normalize_legacy_ledger_record(record: LedgerRecord | Mapping[str, object]) -> CoordinatorEvent:
    """Map a pre-roadmap ledger record to an explicit outcome event without invented evidence."""
    legacy = record if isinstance(record, LedgerRecord) else _dict_to_record(dict(record))
    return CoordinatorEvent(
        kind=CoordinatorEventKind.OUTCOME,
        train_id="legacy-train",
        node_id=legacy.node_id,
        roadmap_path="unknown",
        roadmap_digest=None,
        workspace_id=None,
        branch=legacy.branch,
        base_ref=None,
        base_sha=None,
        head_sha=legacy.head_sha,
        phase=None,
        action="legacy_ledger_status",
        pr_identity=legacy.pr_url,
        merge_sha=legacy.upstream_merge_sha,
        timestamp=legacy.ts or None,
        blocker_reason="legacy_status:" + legacy.status if legacy.status == "blocked" else None,
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
