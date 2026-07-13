"""Immutable attempt leases and broker-admission bindings."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from .contracts import AdmissionRequest


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AttemptLease:
    train_id: str
    node_id: str
    action: str
    attempt_id: str
    lease_epoch: int
    fence_token: str


@dataclass(frozen=True)
class ApprovalBinding:
    roadmap_digest: str
    effective_code: str
    base_sha: str
    dependency_shas: tuple[str, ...]
    verification_plan_digest: str
    verification_artifact_digest: str
    approval_digest: str


def compute_approval_digest(*, roadmap_digest: str, effective_code: str, base_sha: str, dependency_shas: tuple[str, ...], verification_plan_digest: str, verification_artifact_digest: str) -> str:
    if not all((roadmap_digest, effective_code, base_sha, verification_plan_digest, verification_artifact_digest)):
        raise ValueError("approval evidence is incomplete")
    return _digest((roadmap_digest, effective_code, base_sha, dependency_shas, verification_plan_digest, verification_artifact_digest))


def validate_attempt_lease(lease: AttemptLease, *, latest_epoch: int | None = None) -> None:
    if not all((lease.train_id, lease.node_id, lease.action, lease.attempt_id, lease.fence_token)) or lease.lease_epoch < 1:
        raise ValueError("attempt lease is incomplete")
    expected = _digest((lease.train_id, lease.node_id, lease.action, lease.attempt_id, lease.lease_epoch))
    if lease.fence_token != expected:
        raise ValueError("attempt lease fence token does not match its binding")
    if latest_epoch is not None and lease.lease_epoch < latest_epoch:
        raise PermissionError("stale attempt lease")


class FencedAdmissionFactory:
    def lease(self, *, train_id: str, node_id: str, action: str, lease_epoch: int, attempt_id: str | None = None) -> AttemptLease:
        attempt_id = attempt_id or uuid.uuid4().hex
        token = _digest((train_id, node_id, action, attempt_id, lease_epoch))
        return AttemptLease(train_id, node_id, action, attempt_id, lease_epoch, token)

    def approval(self, *, roadmap_digest: str, effective_code: str, base_sha: str, dependency_shas: tuple[str, ...], verification_plan_digest: str, verification_artifact_digest: str) -> ApprovalBinding:
        digest = compute_approval_digest(roadmap_digest=roadmap_digest, effective_code=effective_code, base_sha=base_sha, dependency_shas=dependency_shas, verification_plan_digest=verification_plan_digest, verification_artifact_digest=verification_artifact_digest)
        return ApprovalBinding(roadmap_digest, effective_code, base_sha, dependency_shas, verification_plan_digest, verification_artifact_digest, digest)

    def create(self, *, lease: AttemptLease, approval: ApprovalBinding, expected_version_predicate: str, authority_domain_scope: str, latest_epoch: int | None = None) -> AdmissionRequest:
        validate_attempt_lease(lease, latest_epoch=latest_epoch)
        if not expected_version_predicate or not authority_domain_scope:
            raise ValueError("admission authority is incomplete")
        key = _digest((lease.attempt_id, lease.lease_epoch, lease.fence_token, approval.approval_digest, expected_version_predicate, authority_domain_scope))
        return AdmissionRequest(lease.attempt_id, lease.lease_epoch, lease.fence_token, approval.approval_digest, expected_version_predicate, authority_domain_scope, key)
