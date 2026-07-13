"""Live-enablement tests for the single SUPPORTED broker verb.

The git/gh seam is mocked (``run`` injection) — NO live GitHub is contacted.
Covers: (a) matching remote head -> effect_terminal_observed + real url +
PublishCommittedBranchResult; (b) remote-head mismatch -> outcome_ambiguous_blocked
(NOT success); (c) idempotent replay returns the prior result; (d) ONLY
publish_committed_branch/github is SUPPORTED and every other verb is refused
fail-closed; (e) the live broker builder constructs a working client.
"""
from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from phase_loop_runtime.convergence.broker import build_github_broker_client
from phase_loop_runtime.convergence.broker.admission import LinearizableAdmissionStore
from phase_loop_runtime.convergence.broker.credsep import GitHubBrokerAdapter
from phase_loop_runtime.convergence.broker.evidence import BrokerEvidenceStore
from phase_loop_runtime.convergence.broker.verbs import BrokerService
from phase_loop_runtime.convergence.contracts import (
    AdmissionRequest,
    BrokerRequest,
    BrokerVerb,
    PublishCommittedBranchResult,
)
from phase_loop_runtime.convergence.provider_contracts import (
    PROVIDER_COMPLETION_CLASSIFICATIONS,
    ProviderCompletionClassification,
)

_BRANCH = "feat/live-enable"
_HEAD = "a" * 40
_URL = "https://github.com/o/r/pull/7"


def _admission(key: str) -> AdmissionRequest:
    return AdmissionRequest("attempt", 1, "fence", "digest", "head == committed", "scope", key)


def _request(admission_key: str, *, verb: BrokerVerb = BrokerVerb.PUBLISH_COMMITTED_BRANCH) -> BrokerRequest:
    return BrokerRequest(verb, _admission(admission_key), "o/r", _BRANCH, _HEAD, ("plan.md",))


def _fake_git_gh(*, remote_sha: str = _HEAD, pr_head: str = _HEAD):
    """Return a fake ``run`` dispatching the adapter's git/gh calls."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            sub = cmd[3:]  # after: git -C <path>
            if sub[:2] == ["branch", "--show-current"]:
                return CompletedProcess(cmd, 0, stdout=_BRANCH + "\n", stderr="")
            if sub[0] == "rev-parse":
                return CompletedProcess(cmd, 0, stdout=_HEAD + "\n", stderr="")
            if sub[0] == "log":
                return CompletedProcess(cmd, 0, stdout="commit subject line\n", stderr="")
            if sub[0] == "push":
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if sub[0] == "ls-remote":
                return CompletedProcess(cmd, 0, stdout=f"{remote_sha}\trefs/heads/{_BRANCH}\n", stderr="")
            if sub[:2] == ["remote", "get-url"]:
                return CompletedProcess(cmd, 0, stdout="https://github.com/owner/repo.git\n", stderr="")
        if cmd[0] == "gh":
            if cmd[1:3] == ["pr", "create"]:
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[1:3] == ["pr", "list"]:
                body = json.dumps([{"headRefOid": pr_head, "url": _URL}])
                return CompletedProcess(cmd, 0, stdout=body, stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def _service(root: Path, run) -> BrokerService:
    return BrokerService(
        LinearizableAdmissionStore(root, lambda _req: True),
        BrokerEvidenceStore(root),
        GitHubBrokerAdapter(root / "worktree", run=run),
        contracts=PROVIDER_COMPLETION_CLASSIFICATIONS,  # the REAL, verb-gated matrix
    )


# (a) --------------------------------------------------------------------------
def test_matching_remote_head_yields_effect_terminal_observed_with_real_url(tmp_path):
    svc = _service(tmp_path, _fake_git_gh(remote_sha=_HEAD, pr_head=_HEAD))
    result = svc.execute(_request("key-1"))
    assert result.accepted
    assert result.evidence.terminal_state == "effect_terminal_observed"
    assert isinstance(result.publish_result, PublishCommittedBranchResult)
    assert result.publish_result.pr_url == _URL
    assert result.publish_result.head_sha == _HEAD


# (b) --------------------------------------------------------------------------
def test_remote_head_mismatch_fails_closed_to_ambiguous_not_success(tmp_path):
    # Remote advertises a DIFFERENT sha than we pushed: never inferred as no-effect,
    # never fabricated as success — a permanent ambiguous block.
    svc = _service(tmp_path, _fake_git_gh(remote_sha="b" * 40, pr_head=_HEAD))
    result = svc.execute(_request("key-1"))
    assert not result.accepted
    assert result.publish_result is None
    assert result.evidence.terminal_state == "outcome_ambiguous_blocked"


# (c) --------------------------------------------------------------------------
def test_idempotent_replay_returns_prior_result(tmp_path):
    run = _fake_git_gh()
    calls = {"n": 0}

    def counting_run(cmd, **kwargs):
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
            calls["n"] += 1
        return run(cmd, **kwargs)

    svc = _service(tmp_path, counting_run)
    first = svc.execute(_request("key-1"))
    replay = svc.execute(_request("key-2"))  # DIFFERENT admission key, same triple
    assert calls["n"] == 1, "canonical triple must de-dup: one real effect only"
    assert replay.accepted
    assert replay.publish_result == first.publish_result
    assert replay.publish_result.pr_url == _URL


# (d) --------------------------------------------------------------------------
def test_only_publish_committed_branch_github_is_supported():
    supported = [
        (c.verb, c.provider)
        for c in PROVIDER_COMPLETION_CLASSIFICATIONS
        if c.classification is ProviderCompletionClassification.SUPPORTED
    ]
    assert supported == [("publish_committed_branch", "github")]
    # No non-github provider is present at all (absence == gated / fail-closed).
    assert all(c.provider == "github" for c in PROVIDER_COMPLETION_CLASSIFICATIONS)


@pytest.mark.parametrize("verb", [BrokerVerb.PUBLISH, BrokerVerb.MERGE, BrokerVerb.RELEASE, BrokerVerb.PACKAGE])
def test_every_other_verb_is_refused_before_start(tmp_path, verb):
    class _ExplodingAdapter:
        def execute(self, request):  # pragma: no cover - must never run
            raise AssertionError("gated verb must be refused before the adapter is called")

    svc = BrokerService(
        LinearizableAdmissionStore(tmp_path, lambda _req: True),
        BrokerEvidenceStore(tmp_path),
        _ExplodingAdapter(),
        contracts=PROVIDER_COMPLETION_CLASSIFICATIONS,
    )
    result = svc.execute(_request("key-1", verb=verb))
    assert not result.accepted
    assert result.reason == "provider_not_supported"
    assert result.evidence.terminal_state == "rejected_before_start"


# (e) --------------------------------------------------------------------------
def test_live_broker_builder_constructs_working_client(tmp_path):
    broker_root = tmp_path / "coordinator"  # OUTSIDE the worktree by construction
    repo_path = tmp_path / "worktree"
    client = build_github_broker_client(
        repo_path,
        broker_root=broker_root,
        run=_fake_git_gh(),
    )
    result = client.execute(_request("key-1"))
    assert result.accepted
    assert result.publish_result.pr_url == _URL
    # Broker state is durable under broker_root, never inside the published worktree.
    assert (broker_root / "evidence.jsonl").exists()
    assert not (repo_path / "evidence.jsonl").exists()
