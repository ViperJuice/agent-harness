"""Behavior-neutral coordination contracts shared by future runtime and broker phases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class BrokerVerb(str, Enum):
    PUBLISH = "publish"
    MERGE = "merge"
    RELEASE = "release"
    PACKAGE = "package"
    PUBLISH_COMMITTED_BRANCH = "publish_committed_branch"


@dataclass(frozen=True)
class AdmissionRequest:
    """The sole fencing shape shared by RUNTIME and BROKER."""

    attempt_id: str
    lease_epoch: int
    fence_token: str
    approval_digest: str
    expected_version_predicate: str
    authority_domain_scope: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not all((self.attempt_id, self.fence_token, self.approval_digest, self.expected_version_predicate, self.authority_domain_scope, self.idempotency_key)):
            raise ValueError("admission requests require every fencing field")


@dataclass(frozen=True)
class BrokerRequest:
    """A future broker request bound to one shared admission request."""

    verb: BrokerVerb
    admission: AdmissionRequest
    repo: str
    branch: str
    head_sha: str
    owned_paths: Tuple[str, ...]
    # Base ref the broker independently re-diffs head_sha against to verify that the
    # admitted owned_paths cover the branch's actual mutation (agent-harness#202). The
    # broker uses `origin/<base>...head_sha` (three-dot), matching the #201 coordinator.
    base: str = "main"
    draft: bool = True
    pr_body: str = ""


@dataclass(frozen=True)
class BrokerTerminalEvidence:
    """Effect or no-effect evidence keyed by the admission idempotency key."""

    idempotency_key: str
    terminal_state: str
    evidence_reference: str


@dataclass(frozen=True)
class PublishCommittedBranchResult:
    """Frozen result shape for publish_committed_branch."""

    branch: str
    head_sha: str
    pr_url: str


class AuthoritySource(str, Enum):
    ROADMAP = "roadmap_intent"
    EVENT_LOG = "event_log_active_operation"
    GIT_HEAD = "git_commit_or_pr_head_implementation"
    MERGED_SHA = "merged_sha_merged_state"
    REGISTRY_MANIFEST = "registry_or_manifest_released_state"
    RECOVERY_EVIDENCE = "transcripts_or_phase_loop_recovery_evidence"


class InvalidationTrigger(str, Enum):
    EFFECTIVE_CODE_CHANGED = "effective_code_changed"
    ROADMAP_CHANGED = "roadmap_changed"
    BASE_SHA_CHANGED = "base_sha_changed"
    DEPENDENCY_SHA_CHANGED = "dependency_sha_changed"
    VERIFICATION_PLAN_DIGEST_CHANGED = "verification_plan_digest_changed"


@dataclass(frozen=True)
class ReconciliationBinding:
    """Versioned authority decision and the invalidations that require recomputation."""

    authority: AuthoritySource
    authority_version: str
    invalidation_model_version: str
    invalidation_triggers: Tuple[InvalidationTrigger, ...] = ()


@dataclass(frozen=True)
class ResourceIsolationDecision:
    """Fail-closed decision explaining whether two future units may run concurrently."""

    parallel_safe: bool
    reason: str


def evaluate_resource_isolation(
    *,
    left_repo: str,
    right_repo: str,
    left_owned_paths: Tuple[str, ...],
    right_owned_paths: Tuple[str, ...],
    frozen_shared_interfaces: bool,
    same_repo_mutation: bool = False,
    topological_merge: bool = False,
    release_publication: bool = False,
    evidence_known: bool = True,
) -> ResourceIsolationDecision:
    """Apply the FREEZE fail-closed concurrency predicate without scheduling work."""
    if not evidence_known:
        return ResourceIsolationDecision(False, "unknown evidence")
    if same_repo_mutation or left_repo == right_repo:
        return ResourceIsolationDecision(False, "same-repo mutation serializes")
    if topological_merge:
        return ResourceIsolationDecision(False, "topological merges serialize")
    if release_publication:
        return ResourceIsolationDecision(False, "release publication serializes")
    if not frozen_shared_interfaces:
        return ResourceIsolationDecision(False, "shared interfaces are not frozen")
    if not left_owned_paths or not right_owned_paths:
        return ResourceIsolationDecision(False, "owned-path evidence is incomplete")
    if set(left_owned_paths) & set(right_owned_paths):
        return ResourceIsolationDecision(False, "owned paths overlap")
    return ResourceIsolationDecision(True, "disjoint paths with frozen interfaces")
