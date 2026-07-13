"""Opt-in construction of a live, credential-capable GitHub broker client.

This is the *only* helper that assembles a broker able to perform a real GitHub
mutation.  It is never auto-instantiated: legacy ``run_train`` callers that pass
no ``coordinator_runtime`` (or a runtime with ``broker_client=None``) publish
exactly as before.  A caller wanting broker-mediated publication builds a client
here and attaches it to :class:`CoordinatorRuntime.broker_client`.

The wired client enforces every already-merged safety property: linearizable
admission, permanent fail-closed ``outcome_ambiguous_blocked`` evidence, canonical
``(repo, branch, head_sha)`` idempotency, and the adapter's exact-published-head
verification.  Only the ``publish_committed_branch``/``github`` verb is SUPPORTED
(see ``provider_contracts``); the service refuses every other verb.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable

from phase_loop_runtime.convergence.contracts import AdmissionRequest
from phase_loop_runtime.convergence.provider_contracts import PROVIDER_COMPLETION_CLASSIFICATIONS

from .admission import BrokerAdmissionPolicy, LinearizableAdmissionStore
from .credsep import ALLOWED_ORIGIN_HOSTS, GitHubBrokerAdapter
from .evidence import BrokerEvidenceStore
from .verbs import BrokerClient, BrokerService


def _default_admission_policy(_request: AdmissionRequest) -> bool:
    """Admit any structurally-valid admission request.

    ``AdmissionRequest.__post_init__`` already rejects a request missing any
    fencing field, so a request that reaches the policy is well-formed.  Epoch
    staleness and idempotency-key conflicts are enforced inside
    ``LinearizableAdmissionStore.admit`` regardless of this policy.
    """
    return True


def build_github_broker_client(
    repo_path: Path,
    *,
    broker_root: Path,
    admission_policy: BrokerAdmissionPolicy | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> BrokerClient:
    """Wire a live GitHub broker client.

    Parameters
    ----------
    repo_path:
        Worktree the :class:`GitHubBrokerAdapter` runs git/gh against.
    broker_root:
        Durable directory for the admission log + terminal-evidence log.  MUST
        live OUTSIDE ``repo_path`` (e.g. ``CoordinatorRuntime.coordinator_root``)
        so broker state never dirties the worktree being published — a dirty
        worktree trips the publish staged-diff audit and the train clean-worktree
        preflight.
    admission_policy:
        Optional admission gate; defaults to admitting any well-formed request.
    run:
        Injectable subprocess runner (tests pass a fake to mock the git/gh seam).

    Returns
    -------
    BrokerClient
        A :class:`BrokerService` bound to the global (verb-gated) contracts, so
        only ``publish_committed_branch``/``github`` can execute.
    """
    admission_store = LinearizableAdmissionStore(
        Path(broker_root),
        admission_policy or _default_admission_policy,
    )
    evidence_store = BrokerEvidenceStore(Path(broker_root))
    adapter = GitHubBrokerAdapter(Path(repo_path), run=run)
    return BrokerService(
        admission_store,
        evidence_store,
        adapter,
        contracts=PROVIDER_COMPLETION_CLASSIFICATIONS,
    )


def _repo_store_slug(repo: str) -> str:
    """Stable, filesystem-safe subdir name for a repo's per-repo broker store.

    ``BrokerRequest.repo`` is an arbitrary absolute workspace path, so hash it rather
    than embed the path.  A short hex prefix is collision-free in practice and keeps
    the on-disk layout readable.
    """
    return hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]


class _RoutingBrokerService:
    """A :class:`BrokerClient` that routes each request to a PER-REPO broker service.

    ``build_github_broker_client`` fixes ONE ``repo_path`` at construction, so a single
    client can only faithfully serve one repo — a multi-repo ``run_train`` threading one
    ``coordinator_runtime.broker_client`` across every node would run
    ``git -C <wrong-repo>`` and trip the branch/head guard on node 2+.

    Critically, each repo gets its OWN admission + evidence store under
    ``broker_root/<repo-slug>`` — the stores are NOT shared.  ``epoch_blocked`` is a
    GLOBAL scan over a store (``any(state is OUTCOME_AMBIGUOUS_BLOCKED)``) and an
    ambiguous terminal is durable + permanent, and it fires on BENIGN transients
    (push-unconfirmed / remote-read-failed / pr-unconfirmed / remote-head-mismatch /
    pr-head-unconfirmed).  A shared store would therefore let one repo's transient
    hiccup permanently fail-close every OTHER repo in the train (and, with an
    un-namespaced ``broker_root``, other trains too).  Per-repo stores scope the
    fail-closed epoch to exactly the repo whose mutation became ambiguous — the correct
    blast radius: repo A's unknown state says nothing about repo B's independent remote.
    The caller namespaces ``broker_root`` per train (see the ``run-train`` CLI), closing
    the cross-train dimension.
    """

    def __init__(
        self,
        broker_root: Path,
        *,
        admission_policy: BrokerAdmissionPolicy,
        run: Callable[..., subprocess.CompletedProcess],
        allowed_hosts,
        contracts=PROVIDER_COMPLETION_CLASSIFICATIONS,
    ) -> None:
        self._broker_root = Path(broker_root)
        self._admission_policy = admission_policy
        self._run = run
        self._allowed_hosts = allowed_hosts
        self._contracts = contracts
        self._services: dict[str, BrokerService] = {}

    def _service_for(self, repo: str) -> BrokerService:
        service = self._services.get(repo)
        if service is None:
            root = self._broker_root / _repo_store_slug(repo)
            service = BrokerService(
                LinearizableAdmissionStore(root, self._admission_policy),
                BrokerEvidenceStore(root),
                GitHubBrokerAdapter(Path(repo), run=self._run, allowed_hosts=self._allowed_hosts),
                contracts=self._contracts,
            )
            self._services[repo] = service
        return service

    def execute(self, request):
        return self._service_for(request.repo).execute(request)


def build_routing_broker_client(
    *,
    broker_root: Path,
    admission_policy: BrokerAdmissionPolicy | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    allowed_hosts=ALLOWED_ORIGIN_HOSTS,
) -> BrokerClient:
    """Wire a live GitHub broker client that serves a MULTI-repo train.

    Like :func:`build_github_broker_client` but routes per ``BrokerRequest.repo``: the
    git/gh adapter is bound to the request's repo, AND each repo gets its own admission
    + evidence store under ``broker_root/<repo-slug>``.  Per-repo stores are load-bearing
    for safety, not just routing — a shared store's GLOBAL ``epoch_blocked`` would let one
    repo's ambiguous outcome (reachable via a benign transient) permanently fail-close
    every other repo.  See :class:`_RoutingBrokerService`.

    Parameters
    ----------
    broker_root:
        Durable parent directory for the per-repo admission + evidence stores.  MUST
        live OUTSIDE every node's worktree, and the caller SHOULD namespace it per train
        (e.g. ``<ledger-dir>/broker/<train-stem>``) so unrelated trains never share an
        epoch.
    admission_policy:
        Optional admission gate; defaults to admitting any well-formed request.
    run:
        Injectable subprocess runner (tests pass a fake to mock the git/gh seam).
    allowed_hosts:
        Origin-host allow-list applied to every per-request adapter (github.com-only
        by default); a self-hosted/GHE fleet passes its own set.
    """
    return _RoutingBrokerService(
        Path(broker_root),
        admission_policy=admission_policy or _default_admission_policy,
        run=run,
        allowed_hosts=allowed_hosts,
    )
