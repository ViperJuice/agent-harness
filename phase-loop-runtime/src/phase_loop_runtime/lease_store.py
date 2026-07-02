"""CS-0.10c -- local-file `LeaseStore` + soft leases.

Implements the `LeaseStore` side of the CS-0.10b coordination contract
(`consiliency_contract`'s `lease.schema.json` / `lease-event.schema.json` /
`lease-store-protocol.schema.json`, contract >=0.2.0) so a fleet of local
agents working the same repo(s) in parallel can claim path-sets without
stepping on each other. Full design: `COORDINATION-deconfliction.md` on
consiliency-portal (`plans/unification/consiliency-standardization/`).

Two layers, kept deliberately separate (mirrors the contract's own split):

* :func:`project` -- a PURE function folding an ordered append-only
  lease-event stream down to the current-lease view at a given instant. It
  takes events only, nothing else, by construction (the sole-truth guardrail
  from `coordination-channel-protocol.schema.json`: a coordination message may
  PROMPT an actor to call a store op, but never itself mutates lease state --
  there is no code path here for a message to even reach the projection).
  This is the function the contract's `lease-*`/`coordination-*` conformance
  vectors are asserted against directly (see
  `tests/test_lease_store_conformance.py`).
* :class:`LeaseStore` -- the local-file backend: appends schema-valid
  lease-events to `.consiliency/leases/events.jsonl` and answers
  `acquire`/`renew`/`release`/`query` by re-running `project`/`project_all`
  over that log. SOFT MODE ONLY: this backend never declares an atomic
  backend (`atomic_backend = False`, always), so a requested hard lease
  degrades to soft here per the contract's
  `atomicity.degrade_without_atomic_backend` -- a real atomic compare-and-set
  hard lease needs an off-device/coordinator backend (CS-0.10d, deferred).
  Give-way policy is REROUTE, not block: an `acquire()` that collides with an
  active lease (same `lease_id`, or an overlapping `scope` held by someone
  else) returns a conflict result carrying the blocking lease so the caller
  can go work something else -- it never raises, blocks, or preempts. TTL is
  authoritative: an unrenewed lease past `heartbeat_at + ttl_seconds`
  (exclusive boundary) is already free, so a dead holder can never freeze a
  path forever.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from consiliency_contract import load_schema

from .consiliency_layout import consiliency_root
from .models import utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover -- POSIX-only advisory lock
    fcntl = None  # type: ignore[assignment]

LEASES_DIRNAME = "leases"
EVENTS_FILENAME = "events.jsonl"

#: The failure modes the contract's operation_semantics pin (used to build
#: `protocol_descriptor()` and as the closed set every `reason` we return
#: comes from).
FAILURE_MODES: tuple[str, ...] = ("conflict", "not-holder", "not-found", "expired")


def leases_root(repo: Path) -> Path:
    return consiliency_root(repo) / LEASES_DIRNAME


def events_path(repo: Path) -> Path:
    return leases_root(repo) / EVENTS_FILENAME


# ---------------------------------------------------------------------------
# Vendored schemas / validators (built lazily, never copied -- CS-0.5/0.11
# convention: all shapes come from the installed `consiliency_contract`).
# ---------------------------------------------------------------------------


def lease_schema() -> dict[str, Any]:
    return load_schema("lease")


def lease_event_schema() -> dict[str, Any]:
    return load_schema("lease_event")


def lease_store_protocol_schema() -> dict[str, Any]:
    return load_schema("lease_store_protocol")


def lease_validator() -> Draft202012Validator:
    return Draft202012Validator(lease_schema())


def lease_event_validator() -> Draft202012Validator:
    """`lease-event.schema.json`'s `lease` property `$ref`s `lease.schema.json`
    by `$id` -- wire the two schemas together via a `referencing` registry so
    an acquire event's nested lease snapshot is validated too."""
    lease = lease_schema()
    registry = Registry().with_resources([(lease["$id"], Resource.from_contents(lease))])
    return Draft202012Validator(lease_event_schema(), registry=registry)


def protocol_descriptor() -> dict[str, Any]:
    """The contract 0.2.0-pinned `LeaseStore` protocol descriptor this module
    implements. Every field `lease-store-protocol.schema.json` pins is a
    `const` (or a closed enum) -- building this and validating it against
    that schema is a conformance self-check that our declared semantics
    haven't drifted from the contract."""
    return {
        "schema": "consiliency.lease_store_protocol.v1",
        "operations": ["acquire", "renew", "release", "query"],
        "operation_semantics": {
            "acquire": {
                "request": "lease_id, holder, ttl_seconds, mode, scope, phase",
                "response": "granted-lease | conflict",
                "idempotent": False,
                "holder_only": False,
                "rejects": list(FAILURE_MODES),
            },
            "renew": {
                "request": "lease_id, holder",
                "response": "renewed-lease | rejected",
                "idempotent": True,
                "holder_only": True,
                "rejects": list(FAILURE_MODES),
            },
            "release": {
                "request": "lease_id, holder",
                "response": "released | rejected",
                "idempotent": True,
                "holder_only": True,
                "rejects": list(FAILURE_MODES),
            },
            "query": {
                "request": "lease_id | scope",
                "response": "current-lease | empty",
                "idempotent": True,
                "holder_only": False,
                "rejects": list(FAILURE_MODES),
            },
        },
        "failure_modes": list(FAILURE_MODES),
        "source_of_truth": "lease-store",
        "atomicity": {"hard_requires_atomic_acquire": True, "degrade_without_atomic_backend": "soft"},
        "expiry": {
            "ttl_authoritative": True,
            "heartbeat_renews": True,
            "auto_expiry": True,
            "expires_at_formula": "heartbeat_at + ttl_seconds",
            "boundary": "exclusive",
        },
        "granularity_ladder": {"default": "path-set", "opt_in": "symbol", "out_of_scope": "line"},
        "backends": ["local-file", "portal", "coordinator"],
    }


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def _parse_ts(value: str) -> datetime:
    # matches the rest of the runtime's convention (state_degradation.py,
    # observability.py, ...): plain `fromisoformat("...Z")` raises on 3.10.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _expires_at(heartbeat_at: str, ttl_seconds: int) -> datetime:
    return _parse_ts(heartbeat_at) + timedelta(seconds=ttl_seconds)


def is_expired(lease: Mapping[str, Any], now: str) -> bool:
    """Exclusive boundary (`lease-store-protocol.schema.json`'s
    `expiry.boundary`): a lease is held over `[acquired_at, expires_at)`; at
    `now == expires_at` it is ALREADY expired."""
    return _parse_ts(now) >= _expires_at(str(lease["heartbeat_at"]), int(lease["ttl_seconds"]))


# ---------------------------------------------------------------------------
# The pure conformance core
# ---------------------------------------------------------------------------


def resolve_effective_mode(mode: str, atomic_backend: bool) -> str:
    """Hard mode is granted only over an atomic backend
    (`atomicity.hard_requires_atomic_acquire`); anything else degrades to
    soft (`atomicity.degrade_without_atomic_backend`)."""
    return "hard" if mode == "hard" and atomic_backend else "soft"


def project(
    events: Sequence[Mapping[str, Any]],
    lease_id: str,
    now: str,
    *,
    atomic_backend: bool = False,
) -> dict[str, Any] | None:
    """Fold an ordered append-only lease-event stream (the flat
    `coordination-scenario.schema.json` event shape -- `event/lease_id/holder/
    at` plus `ttl_seconds/mode/scope/phase` on an `acquire`) down to the
    current-lease view for one `lease_id`, evaluated at `now`.

    Pure and events-only by construction: there is no parameter here a
    coordination message could ever occupy, which is what makes the
    sole-truth guardrail structural rather than a runtime check.
    """
    state: dict[str, Any] | None = None
    for event in events:
        if event.get("lease_id") != lease_id:
            continue
        kind = event.get("event")
        if kind == "acquire":
            state = {
                "schema": "consiliency.lease.v1",
                "lease_id": lease_id,
                "holder": event["holder"],
                "acquired_at": event["at"],
                "ttl_seconds": event["ttl_seconds"],
                "heartbeat_at": event["at"],
                "mode": resolve_effective_mode(event.get("mode", "soft"), atomic_backend),
                "scope": dict(event["scope"]),
                "phase": event["phase"],
            }
        elif kind == "renew":
            if state is not None:
                state = {**state, "heartbeat_at": event["at"]}
        elif kind in ("release", "expire"):
            state = None
    if state is not None and is_expired(state, now):
        state = None
    return state


def project_all(
    events: Sequence[Mapping[str, Any]],
    now: str,
    *,
    atomic_backend: bool = False,
) -> dict[str, dict[str, Any]]:
    """`project()` every `lease_id` the stream ever mentions; the result is
    exactly the map of currently ACTIVE leases (an expired/released/never-
    granted `lease_id` is simply absent, not present-with-null)."""
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        lease_id = event.get("lease_id")
        if isinstance(lease_id, str) and lease_id not in seen:
            seen.add(lease_id)
            ordered_ids.append(lease_id)
    active: dict[str, dict[str, Any]] = {}
    for lease_id in ordered_ids:
        view = project(events, lease_id, now, atomic_backend=atomic_backend)
        if view is not None:
            active[lease_id] = view
    return active


def _ever_acquired(events: Sequence[Mapping[str, Any]], lease_id: str) -> bool:
    return any(event.get("event") == "acquire" and event.get("lease_id") == lease_id for event in events)


# ---------------------------------------------------------------------------
# Scope overlap -- acquire-on-intent give-way over the path-set granularity
# ---------------------------------------------------------------------------


def _normalize_selector_prefix(selector: str) -> str:
    """Strip a trailing glob suffix (`/**`, `/*`, `*`) so two path-set
    selectors can be compared as plain path prefixes."""
    value = selector
    while value.endswith("/**") or value.endswith("/*"):
        value = value.rsplit("/", 1)[0]
    return value.rstrip("*").rstrip("/")


def _paths_overlap(a: str, b: str) -> bool:
    prefix_a, prefix_b = _normalize_selector_prefix(a), _normalize_selector_prefix(b)
    if prefix_a == prefix_b:
        return True
    return prefix_a.startswith(prefix_b + "/") or prefix_b.startswith(prefix_a + "/")


def _scope_paths(scope: Mapping[str, Any]) -> list[str]:
    selectors = [str(s) for s in (scope.get("selector") or [])]
    if scope.get("granularity") == "symbol":
        # a symbol selector anchors "<path>::<symbol>" -- only the path
        # component participates in path-set overlap checks.
        return [selector.split("::", 1)[0] for selector in selectors]
    return selectors


def scope_overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two lease scopes claim any of the same ground. `repo`
    granularity always overlaps (it claims everything); `symbol` only
    overlaps on an exact symbol match; `path-set` (the default) overlaps on a
    shared path or path-ancestor relationship."""
    if a.get("granularity") == "repo" or b.get("granularity") == "repo":
        return True
    if a.get("granularity") == "symbol" and b.get("granularity") == "symbol":
        return bool(set(a.get("selector") or []) & set(b.get("selector") or []))
    return any(_paths_overlap(pa, pb) for pa in _scope_paths(a) for pb in _scope_paths(b))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquireResult:
    granted: bool
    lease: dict[str, Any] | None
    give_way: str | None  # "reroute" on conflict; matches COORDINATION-deconfliction.md
    conflict: dict[str, Any] | None  # the blocking lease, when granted is False
    degraded: bool  # requested hard, granted soft (no atomic backend)
    reason: str | None  # one of FAILURE_MODES, or None when granted

    def to_json(self) -> dict[str, Any]:
        return {
            "granted": self.granted,
            "lease": self.lease,
            "give_way": self.give_way,
            "conflict": self.conflict,
            "degraded": self.degraded,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RenewResult:
    renewed: bool
    lease: dict[str, Any] | None
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {"renewed": self.renewed, "lease": self.lease, "reason": self.reason}


@dataclass(frozen=True)
class ReleaseResult:
    released: bool
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {"released": self.released, "reason": self.reason}


# ---------------------------------------------------------------------------
# The local-file backend
# ---------------------------------------------------------------------------


class LeaseStore:
    """Local-file `LeaseStore` backend (CS-0.10c). Single machine, cooperative
    agents. SOFT MODE ONLY: `atomic_backend` is always `False` here, so a
    requested hard lease always degrades to soft -- there is no cross-machine
    (or even cross-process-race-free) atomic compare-and-set available
    locally; that needs the off-device/Portal backend (CS-0.10d).
    """

    backend = "local-file"
    atomic_backend = False

    def __init__(self, repo: str | Path):
        self.repo = Path(repo)

    # -- storage -----------------------------------------------------------

    def _events_file(self) -> Path:
        return events_path(self.repo)

    def _read_raw_events(self) -> list[dict[str, Any]]:
        path = self._events_file()
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def _append_raw_event(self, event: Mapping[str, Any]) -> None:
        path = self._events_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(dict(event), sort_keys=True) + "\n")
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _to_scenario_event(event: Mapping[str, Any]) -> dict[str, Any]:
        """Flatten a stored `lease-event.schema.json`-shaped event (an
        `acquire` nests the full lease snapshot under `lease`) into the flat
        shape `project()` folds over."""
        flat: dict[str, Any] = {
            "event": event["event"],
            "lease_id": event["lease_id"],
            "holder": event["holder"],
            "at": event["at"],
        }
        lease = event.get("lease")
        if lease is not None:
            flat.update(
                ttl_seconds=lease["ttl_seconds"],
                mode=lease["mode"],
                scope=lease["scope"],
                phase=lease["phase"],
            )
        return flat

    def _scenario_events(self) -> list[dict[str, Any]]:
        return [self._to_scenario_event(event) for event in self._read_raw_events()]

    # -- operations ----------------------------------------------------------

    def acquire(
        self,
        *,
        lease_id: str,
        holder: str,
        ttl_seconds: int,
        mode: str,
        scope: Mapping[str, Any],
        phase: str,
        now: str | None = None,
    ) -> AcquireResult:
        """Acquire-on-intent over a path-set (default granularity). NOT
        idempotent: re-issuing acquire against an already-active `lease_id`
        always conflicts -- extend a held lease with `renew()` instead. On
        any conflict (same `lease_id` already held, or an overlapping scope
        held by someone else) this returns a result carrying the blocking
        lease and `give_way="reroute"` -- it never blocks or raises."""
        now = now or utc_now()
        active = project_all(self._scenario_events(), now, atomic_backend=self.atomic_backend)

        existing = active.get(lease_id)
        if existing is not None:
            return AcquireResult(
                granted=False, lease=None, give_way="reroute", conflict=existing, degraded=False, reason="conflict"
            )

        conflicting = next(
            (
                other
                for other_id, other in active.items()
                if other_id != lease_id and other["holder"] != holder and scope_overlaps(scope, other["scope"])
            ),
            None,
        )
        if conflicting is not None:
            return AcquireResult(
                granted=False, lease=None, give_way="reroute", conflict=conflicting, degraded=False, reason="conflict"
            )

        effective_mode = resolve_effective_mode(mode, self.atomic_backend)
        degraded = mode == "hard" and effective_mode == "soft"
        lease = {
            "schema": "consiliency.lease.v1",
            "lease_id": lease_id,
            "holder": holder,
            "acquired_at": now,
            "ttl_seconds": ttl_seconds,
            "heartbeat_at": now,
            "mode": effective_mode,
            "scope": dict(scope),
            "phase": phase,
        }
        raw_event = {
            "schema": "consiliency.lease_event.v1",
            "event": "acquire",
            "lease_id": lease_id,
            "holder": holder,
            "at": now,
            "lease": lease,
        }
        lease_event_validator().validate(raw_event)
        self._append_raw_event(raw_event)
        return AcquireResult(granted=True, lease=lease, give_way=None, conflict=None, degraded=degraded, reason=None)

    def renew(self, *, lease_id: str, holder: str, now: str | None = None) -> RenewResult:
        """Idempotent, holder-only: extends `heartbeat_at` so the lease stays
        active past its original TTL. Rejects `not-holder` if someone else
        holds it, `expired`/`not-found` if there's nothing to renew."""
        now = now or utc_now()
        raw_events = self._read_raw_events()
        scenario_events = [self._to_scenario_event(event) for event in raw_events]
        current = project(scenario_events, lease_id, now, atomic_backend=self.atomic_backend)
        if current is None:
            reason = "expired" if _ever_acquired(scenario_events, lease_id) else "not-found"
            return RenewResult(renewed=False, lease=None, reason=reason)
        if current["holder"] != holder:
            return RenewResult(renewed=False, lease=None, reason="not-holder")

        raw_event = {"schema": "consiliency.lease_event.v1", "event": "renew", "lease_id": lease_id, "holder": holder, "at": now}
        lease_event_validator().validate(raw_event)
        self._append_raw_event(raw_event)
        return RenewResult(renewed=True, lease={**current, "heartbeat_at": now}, reason=None)

    def release(self, *, lease_id: str, holder: str, now: str | None = None) -> ReleaseResult:
        """Idempotent, holder-only. Releasing an already-free lease (never
        held, already released, or auto-expired) is a no-op success -- that's
        what idempotent means here. Someone else's active lease rejects
        `not-holder`."""
        now = now or utc_now()
        raw_events = self._read_raw_events()
        scenario_events = [self._to_scenario_event(event) for event in raw_events]
        current = project(scenario_events, lease_id, now, atomic_backend=self.atomic_backend)
        if current is None:
            return ReleaseResult(released=True, reason=None)
        if current["holder"] != holder:
            return ReleaseResult(released=False, reason="not-holder")

        raw_event = {"schema": "consiliency.lease_event.v1", "event": "release", "lease_id": lease_id, "holder": holder, "at": now}
        lease_event_validator().validate(raw_event)
        self._append_raw_event(raw_event)
        return ReleaseResult(released=True, reason=None)

    def query(
        self,
        *,
        lease_id: str | None = None,
        path: str | None = None,
        scope: Mapping[str, Any] | None = None,
        now: str | None = None,
    ) -> dict[str, Any] | None:
        """The current-lease view, projected from the event log ONLY -- no
        coordination-channel input exists in this signature to mutate it.
        Query by exact `lease_id`, or by `path`/`scope` to find whatever
        active lease (if any) claims that ground."""
        now = now or utc_now()
        scenario_events = self._scenario_events()
        if lease_id is not None:
            return project(scenario_events, lease_id, now, atomic_backend=self.atomic_backend)

        query_scope = scope if scope is not None else ({"granularity": "path-set", "selector": [path]} if path else None)
        if query_scope is None:
            raise ValueError("query() requires lease_id, path, or scope")
        for other in project_all(scenario_events, now, atomic_backend=self.atomic_backend).values():
            if scope_overlaps(query_scope, other["scope"]):
                return other
        return None

    def list_active(self, *, now: str | None = None) -> dict[str, dict[str, Any]]:
        now = now or utc_now()
        return project_all(self._scenario_events(), now, atomic_backend=self.atomic_backend)
