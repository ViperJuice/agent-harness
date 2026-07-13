"""Post-merge downstream refresh and bound re-verification orchestration."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .contracts import BrokerRequest, BrokerVerb
from .fencing import FencedAdmissionFactory


class DownstreamRefreshStatus(str, Enum):
    REFRESHED = "refreshed"
    CONFLICT = "conflict"
    VERIFICATION_BLOCKED = "verification_blocked"
    BROKER_BLOCKED = "broker_blocked"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class DownstreamRefreshRequest:
    train_id: str
    node_id: str
    repository: str
    branch: str
    merged_sha: str
    owned_paths: tuple[str, ...]
    verification_plan_digest: str
    roadmap_digest: str
    base_sha: str
    dependency_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownstreamRefreshResult:
    status: DownstreamRefreshStatus
    reason: str = ""
    verification_digest: str | None = None


def refresh_downstream_after_merge(
    request: DownstreamRefreshRequest,
    *,
    refresh_channel: Callable[[str], bool],
    verify: Callable[[], bytes | str | None],
    broker,
    factory: FencedAdmissionFactory,
    lease_epoch: int,
    authority_scope: str,
) -> DownstreamRefreshResult:
    if not request.merged_sha:
        return DownstreamRefreshResult(DownstreamRefreshStatus.CONFLICT, "missing exact merged SHA")
    if not refresh_channel(request.merged_sha):
        return DownstreamRefreshResult(DownstreamRefreshStatus.CONFLICT, "downstream refresh conflict")
    artifact = verify()
    if not artifact:
        return DownstreamRefreshResult(DownstreamRefreshStatus.VERIFICATION_BLOCKED, "verification evidence unavailable")
    digest = hashlib.sha256(artifact.encode() if isinstance(artifact, str) else artifact).hexdigest()
    approval = factory.approval(roadmap_digest=request.roadmap_digest, effective_code=request.merged_sha, base_sha=request.base_sha, dependency_shas=request.dependency_shas, verification_plan_digest=request.verification_plan_digest, verification_artifact_digest=digest)
    lease = factory.lease(train_id=request.train_id, node_id=request.node_id, action="publish", lease_epoch=lease_epoch)
    admission = factory.create(lease=lease, approval=approval, expected_version_predicate=f"head == {request.merged_sha}", authority_domain_scope=authority_scope)
    outcome = broker.execute(BrokerRequest(BrokerVerb.PUBLISH, admission, request.repository, request.branch, request.merged_sha, request.owned_paths))
    if not outcome.accepted:
        status = DownstreamRefreshStatus.AMBIGUOUS if outcome.reason == "outcome_ambiguous" else DownstreamRefreshStatus.BROKER_BLOCKED
        return DownstreamRefreshResult(status, outcome.reason, digest)
    return DownstreamRefreshResult(DownstreamRefreshStatus.REFRESHED, verification_digest=digest)
