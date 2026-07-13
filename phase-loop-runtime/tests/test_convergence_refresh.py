from phase_loop_runtime.convergence.fencing import FencedAdmissionFactory
from phase_loop_runtime.convergence.refresh import DownstreamRefreshRequest, DownstreamRefreshStatus, refresh_downstream_after_merge


class _Broker:
    def execute(self, request):
        return type("Outcome", (), {"accepted": True, "reason": ""})()


def test_refresh_uses_exact_merge_and_bound_evidence():
    request = DownstreamRefreshRequest("train", "node", "repo", "branch", "merged", ("a.py",), "plan", "roadmap", "base")
    result = refresh_downstream_after_merge(request, refresh_channel=lambda sha: sha == "merged", verify=lambda: "proof", broker=_Broker(), factory=FencedAdmissionFactory(), lease_epoch=1, authority_scope="repo")
    assert result.status is DownstreamRefreshStatus.REFRESHED and result.verification_digest
