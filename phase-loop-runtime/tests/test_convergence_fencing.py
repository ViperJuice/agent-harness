import pytest

from phase_loop_runtime.convergence.fencing import FencedAdmissionFactory, validate_attempt_lease


def test_fenced_admission_is_complete_and_stale_leases_fail():
    factory = FencedAdmissionFactory()
    lease = factory.lease(train_id="train", node_id="node", action="publish", lease_epoch=2, attempt_id="attempt")
    approval = factory.approval(roadmap_digest="roadmap", effective_code="head", base_sha="base", dependency_shas=("dep",), verification_plan_digest="plan", verification_artifact_digest="artifact")
    request = factory.create(lease=lease, approval=approval, expected_version_predicate="head == head", authority_domain_scope="repo")
    assert request.lease_epoch == 2 and request.approval_digest == approval.approval_digest
    with pytest.raises(PermissionError):
        validate_attempt_lease(lease, latest_epoch=3)
