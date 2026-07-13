"""Fail-closed DAG dispatch with repository-scoped isolation locks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Iterable, Mapping

from .contracts import ResourceIsolationDecision, evaluate_resource_isolation
from phase_loop_runtime.train_ledger import ConvergenceResultEnvelope, ConvergenceResultStatus


@dataclass(frozen=True)
class RepositoryLockKey:
    repository: str


@dataclass(frozen=True)
class RepositoryDispatchRequest:
    node_id: str
    repository: str
    owned_paths: tuple[str, ...]
    frozen_shared_interfaces: bool
    action: str = "dispatch"
    topological_merge: bool = False
    release_publication: bool = False
    isolation_evidence_known: bool = True


@dataclass(frozen=True)
class DispatchDecision:
    node_id: str
    allowed: bool
    reason: str
    lock_key: RepositoryLockKey


class _RepositoryLocks:
    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, Lock] = {}

    def for_repository(self, repository: str) -> Lock:
        with self._guard:
            return self._locks.setdefault(repository, Lock())


def _isolation(left: RepositoryDispatchRequest, right: RepositoryDispatchRequest) -> ResourceIsolationDecision:
    return evaluate_resource_isolation(
        left_repo=left.repository,
        right_repo=right.repository,
        left_owned_paths=left.owned_paths,
        right_owned_paths=right.owned_paths,
        frozen_shared_interfaces=left.frozen_shared_interfaces and right.frozen_shared_interfaces,
        topological_merge=left.topological_merge or right.topological_merge,
        release_publication=left.release_publication or right.release_publication,
        evidence_known=left.isolation_evidence_known and right.isolation_evidence_known,
    )


def dispatch_ready_nodes(
    requests: Iterable[RepositoryDispatchRequest],
    execute: Callable[[RepositoryDispatchRequest], ConvergenceResultEnvelope],
    *,
    max_workers: int = 4,
    persist_decision: Callable[[DispatchDecision], None] | None = None,
) -> Mapping[str, ConvergenceResultEnvelope]:
    """Execute safe independent nodes concurrently; serialize every uncertain pair.

    The scheduler owns locks for the full work unit.  A deterministic batch is
    selected from the supplied ready order; each excluded request is scheduled
    in a later serial batch rather than being silently dropped.
    """
    pending = list(requests)
    results: dict[str, ConvergenceResultEnvelope] = {}
    locks = _RepositoryLocks()
    while pending:
        batch: list[RepositoryDispatchRequest] = []
        deferred: list[RepositoryDispatchRequest] = []
        for request in pending:
            verdict = next((_isolation(request, current) for current in batch if not _isolation(request, current).parallel_safe), None)
            decision = DispatchDecision(request.node_id, verdict is None, "independent ready nodes" if verdict is None else verdict.reason, RepositoryLockKey(request.repository))
            if persist_decision:
                persist_decision(decision)
            (batch if verdict is None else deferred).append(request)
        workers = min(max_workers, len(batch))
        def _run(request: RepositoryDispatchRequest) -> ConvergenceResultEnvelope:
            lock = locks.for_repository(request.repository)
            with lock:
                try:
                    return execute(request)
                except Exception as exc:
                    return ConvergenceResultEnvelope(ConvergenceResultStatus.FAILED, request.node_id, str(exc))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_request = {pool.submit(_run, request): request for request in batch}
            for future in as_completed(future_to_request):
                request = future_to_request[future]
                results[request.node_id] = future.result()
        pending = deferred
    return results
