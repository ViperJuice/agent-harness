from phase_loop_runtime.convergence.broker.admission import LinearizableAdmissionStore
from phase_loop_runtime.convergence.broker.evidence import BrokerEvidenceStore
from phase_loop_runtime.convergence.broker.verbs import BrokerService, publish_committed_branch_idempotency_key
from phase_loop_runtime.convergence.contracts import AdmissionRequest, BrokerRequest, BrokerTerminalEvidence, BrokerVerb, PublishCommittedBranchResult
from phase_loop_runtime.convergence.provider_contracts import ProviderAutomationDisposition, ProviderCompletionClassification, ProviderCompletionContract


def test_publish_key_binds_the_canonical_triple():
    assert publish_committed_branch_idempotency_key("r", "b", "h") != publish_committed_branch_idempotency_key("r", "b", "other")


# --- Blocker 2: canonical-triple idempotency -------------------------------
# A test-local SUPPORTED contract reaches the live-capable path WITHOUT touching
# the global provider_contracts (which stays HUMAN_EXECUTED); the verb remains
# gated everywhere else.
_SUPPORTED_PCB = (
    ProviderCompletionContract(
        verb="publish_committed_branch",
        provider="github",
        classification=ProviderCompletionClassification.SUPPORTED,
        disposition=ProviderAutomationDisposition.AUTOMATED,
        status_endpoint="gh pr list",
        idempotency_key_supported="yes",
        terminal_success_evidence="remote head matches pushed sha",
        terminal_no_effect_evidence="no remote ref",
        non_late_commit_guarantee="fenced",
        guaranteed_processing_horizon="synchronous",
        expected_version_predicate="head == sha",
        revocation_affects_accepted="no",
        stabilization_drain_interval="0",
    ),
)


class _CountingAdapter:
    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return (
            PublishCommittedBranchResult(request.branch, request.head_sha, f"https://gh/pr/{self.calls}"),
            BrokerTerminalEvidence(request.admission.idempotency_key, "effect_terminal_observed", "github-observed"),
        )


def _service(tmp_path, adapter):
    return BrokerService(
        LinearizableAdmissionStore(tmp_path, lambda _: True),
        BrokerEvidenceStore(tmp_path),
        adapter,
        contracts=_SUPPORTED_PCB,
    )


def _pcb_request(admission_key, *, repo="repo", branch="feat/x", head="abc123"):
    admission = AdmissionRequest("attempt", 1, "fence", "digest", "predicate", "scope", admission_key)
    return BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, repo, branch, head, ("a.py",))


def test_same_triple_twice_single_effect_and_identical_result(tmp_path):
    adapter = _CountingAdapter()
    svc = _service(tmp_path, adapter)
    # Same (repo, branch, head_sha) under DIFFERENT admission keys.
    r1 = svc.execute(_pcb_request("key-1"))
    r2 = svc.execute(_pcb_request("key-2"))
    assert adapter.calls == 1, "canonical triple must de-dup: only a single effect"
    assert r1.publish_result is not None and r1.publish_result.pr_url == "https://gh/pr/1"
    assert r2.publish_result == r1.publish_result, "repeat returns the SAME prior result"
    assert r2.accepted


def test_replay_after_complete_returns_prior_result_not_none(tmp_path):
    adapter = _CountingAdapter()
    svc = _service(tmp_path, adapter)
    req = _pcb_request("key-1")
    first = svc.execute(req)
    replay = svc.execute(req)
    assert adapter.calls == 1
    assert replay.publish_result is not None, "replay of COMPLETED op must return the result, not None"
    assert replay.publish_result == first.publish_result
    assert replay.accepted, "idempotent recovery is accepted, not blocked"
