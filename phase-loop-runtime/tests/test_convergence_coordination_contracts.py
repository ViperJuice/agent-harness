from phase_loop_runtime.convergence.contracts import (
    AdmissionRequest,
    AuthoritySource,
    BrokerRequest,
    BrokerVerb,
    InvalidationTrigger,
    evaluate_resource_isolation,
)


def test_broker_verbs_and_shared_admission_shape_are_exact():
    assert {item.value for item in BrokerVerb} == {"publish", "merge", "release", "package", "publish_committed_branch"}
    assert set(AdmissionRequest.__dataclass_fields__) == {
        "attempt_id", "lease_epoch", "fence_token", "approval_digest", "expected_version_predicate",
        "authority_domain_scope", "idempotency_key",
    }
    admission = AdmissionRequest("a", 1, "f", "d", "head == abc", "repo", "idempotent")
    assert BrokerRequest(BrokerVerb.PUBLISH, admission, "repo", "branch", "abc", ("a.py",)).admission is admission


def test_authority_and_invalidation_values_are_normative_and_exact():
    assert {item.value for item in AuthoritySource} == {
        "roadmap_intent", "event_log_active_operation", "git_commit_or_pr_head_implementation",
        "merged_sha_merged_state", "registry_or_manifest_released_state", "transcripts_or_phase_loop_recovery_evidence",
    }
    assert {item.value for item in InvalidationTrigger} == {
        "effective_code_changed", "roadmap_changed", "base_sha_changed", "dependency_sha_changed",
        "verification_plan_digest_changed",
    }


def test_resource_isolation_is_fail_closed_for_same_repo_overlap_and_unknown_evidence():
    safe = evaluate_resource_isolation(left_repo="a", right_repo="b", left_owned_paths=("a.py",), right_owned_paths=("b.py",), frozen_shared_interfaces=True)
    assert safe.parallel_safe
    assert not evaluate_resource_isolation(left_repo="a", right_repo="a", left_owned_paths=("a.py",), right_owned_paths=("b.py",), frozen_shared_interfaces=True).parallel_safe
    assert not evaluate_resource_isolation(left_repo="a", right_repo="b", left_owned_paths=("a.py",), right_owned_paths=("a.py",), frozen_shared_interfaces=True).parallel_safe
    assert not evaluate_resource_isolation(left_repo="a", right_repo="b", left_owned_paths=("a.py",), right_owned_paths=("b.py",), frozen_shared_interfaces=False).parallel_safe
    assert not evaluate_resource_isolation(left_repo="a", right_repo="b", left_owned_paths=("a.py",), right_owned_paths=("b.py",), frozen_shared_interfaces=True, release_publication=True).parallel_safe
