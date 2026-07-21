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

from phase_loop_runtime.convergence.broker import build_github_broker_client, build_routing_broker_client
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


def _fake_git_gh(*, remote_sha: str = _HEAD, pr_head: str = _HEAD, pr_base: str = "main"):
    """Return a fake ``run`` dispatching the adapter's git/gh calls."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            sub = cmd[3:]  # after: git -C <path>
            if sub[:2] == ["branch", "--show-current"]:
                return CompletedProcess(cmd, 0, stdout=_BRANCH + "\n", stderr="")
            if sub[0] == "rev-parse":
                return CompletedProcess(cmd, 0, stdout=_HEAD + "\n", stderr="")
            if sub[0] == "diff":  # #202/#250 server-authoritative scope diff (owns plan.md), -z NUL-delimited
                return CompletedProcess(cmd, 0, stdout=b"plan.md\0", stderr=b"")
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
                body = json.dumps([{"headRefOid": pr_head, "url": _URL, "baseRefName": pr_base}])
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


# (f) Routing broker: ONE client serves a MULTI-repo train ---------------------
def _routing_fake(repos, seen_paths):
    """git/gh fake that responds PER repo path, recording which path git ran under."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            path = cmd[2]  # after: git -C <path>
            seen_paths.append(path)
            meta = repos[path]
            sub = cmd[3:]
            if sub[:2] == ["branch", "--show-current"]:
                return CompletedProcess(cmd, 0, stdout=meta["branch"] + "\n", stderr="")
            if sub[0] == "rev-parse":
                return CompletedProcess(cmd, 0, stdout=meta["head"] + "\n", stderr="")
            if sub[0] == "diff":  # #202/#250 server-authoritative scope diff (owns plan.md), -z NUL-delimited
                return CompletedProcess(cmd, 0, stdout=b"plan.md\0", stderr=b"")
            if sub[0] == "log":
                return CompletedProcess(cmd, 0, stdout=f"{meta['branch']} subject\n", stderr="")
            if sub[0] == "push":
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if sub[0] == "ls-remote":
                return CompletedProcess(cmd, 0, stdout=f'{meta["head"]}\trefs/heads/{meta["branch"]}\n', stderr="")
            if sub[:2] == ["remote", "get-url"]:
                return CompletedProcess(cmd, 0, stdout="https://github.com/owner/repo.git\n", stderr="")
        if cmd[0] == "gh":
            meta = repos[str(kwargs.get("cwd"))]
            if cmd[1:3] == ["pr", "create"]:
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[1:3] == ["pr", "list"]:
                return CompletedProcess(cmd, 0, stdout=json.dumps([{"headRefOid": meta["head"], "url": meta["url"], "baseRefName": meta.get("base", "main")}]), stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def _routing_request(repo_path, meta, key):
    admission = AdmissionRequest("attempt", 1, "fence", "digest", "head == committed", "scope", key)
    return BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, repo_path, meta["branch"], meta["head"], ("plan.md",), base=meta.get("base", "main"))


def test_routing_broker_binds_each_request_to_its_own_repo(tmp_path):
    repos = {
        "/ws/alpha": {"branch": "feat/alpha", "head": "a" * 40, "url": "https://gh/pr/alpha"},
        "/ws/beta": {"branch": "feat/beta", "head": "b" * 40, "url": "https://gh/pr/beta"},
    }
    seen_paths: list = []
    broker = build_routing_broker_client(broker_root=tmp_path / "coord", run=_routing_fake(repos, seen_paths))

    for path, meta in repos.items():
        result = broker.execute(_routing_request(path, meta, key=f"k-{path}"))
        assert result.accepted, f"{path} not accepted"
        assert result.publish_result.pr_url == meta["url"], f"{path} routed to the wrong repo's PR"
        assert result.publish_result.head_sha == meta["head"]

    # Each node's git ran under ITS OWN worktree path — the whole point of routing.
    assert "/ws/alpha" in seen_paths and "/ws/beta" in seen_paths


def test_routing_broker_dedups_within_a_repo_not_across(tmp_path):
    # Each repo has its OWN store, and within a repo the de-dup key
    # sha256(repo\0branch\0head) makes a replay under a fresh admission key return the
    # prior result; a different repo is a distinct store + triple (a real second effect).
    repos = {
        "/ws/alpha": {"branch": "feat/alpha", "head": "a" * 40, "url": "https://gh/pr/alpha"},
        "/ws/beta": {"branch": "feat/beta", "head": "b" * 40, "url": "https://gh/pr/beta"},
    }
    creates = {"n": 0}

    def counting(cmd, **kwargs):
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
            creates["n"] += 1
        return _routing_fake(repos, [])(cmd, **kwargs)

    broker = build_routing_broker_client(broker_root=tmp_path / "coord", run=counting)
    first = broker.execute(_routing_request("/ws/alpha", repos["/ws/alpha"], key="k1"))
    replay = broker.execute(_routing_request("/ws/alpha", repos["/ws/alpha"], key="k2-different"))
    other = broker.execute(_routing_request("/ws/beta", repos["/ws/beta"], key="k3"))

    assert creates["n"] == 2, "alpha's replay must de-dup (1 real effect); beta is a distinct triple"
    assert replay.publish_result == first.publish_result
    assert other.publish_result.pr_url == repos["/ws/beta"]["url"]


def test_one_repo_ambiguous_outcome_does_not_poison_other_repos(tmp_path):
    """A benign transient making repo alpha's publish ambiguous must NOT fail-close beta.

    ``epoch_blocked`` is a global scan over a store, an ambiguous terminal is durable +
    permanent, and it fires on benign transients (here: alpha's ls-remote read fails).
    With a SHARED store this would set the global epoch and beta would raise
    ``PermissionError('epoch permanently blocked')``.  Per-repo stores scope the
    fail-closed epoch to ONLY alpha (agent-harness#208 CR).
    """
    repos = {
        "/ws/alpha": {"branch": "feat/alpha", "head": "a" * 40, "url": "https://gh/pr/alpha"},
        "/ws/beta": {"branch": "feat/beta", "head": "b" * 40, "url": "https://gh/pr/beta"},
    }

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            path = cmd[2]
            meta = repos[path]
            sub = cmd[3:]
            if sub[:2] == ["branch", "--show-current"]:
                return CompletedProcess(cmd, 0, stdout=meta["branch"] + "\n", stderr="")
            if sub[0] == "rev-parse":
                return CompletedProcess(cmd, 0, stdout=meta["head"] + "\n", stderr="")
            if sub[0] == "diff":  # #202/#250 server-authoritative scope diff (owns plan.md), -z NUL-delimited
                return CompletedProcess(cmd, 0, stdout=b"plan.md\0", stderr=b"")
            if sub[0] == "log":
                return CompletedProcess(cmd, 0, stdout="subject\n", stderr="")
            if sub[0] == "push":
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if sub[0] == "ls-remote":
                if path == "/ws/alpha":  # benign transient: remote read fails -> ambiguous
                    return CompletedProcess(cmd, 1, stdout="", stderr="network hiccup")
                return CompletedProcess(cmd, 0, stdout=f'{meta["head"]}\trefs/heads/{meta["branch"]}\n', stderr="")
            if sub[:2] == ["remote", "get-url"]:
                return CompletedProcess(cmd, 0, stdout="https://github.com/owner/repo.git\n", stderr="")
        if cmd[0] == "gh":
            meta = repos[str(kwargs.get("cwd"))]
            if cmd[1:3] == ["pr", "create"]:
                return CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[1:3] == ["pr", "list"]:
                return CompletedProcess(cmd, 0, stdout=json.dumps([{"headRefOid": meta["head"], "url": meta["url"], "baseRefName": meta.get("base", "main")}]), stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    broker = build_routing_broker_client(broker_root=tmp_path / "coord", run=fake_run)

    alpha = broker.execute(_routing_request("/ws/alpha", repos["/ws/alpha"], key="ka"))
    assert not alpha.accepted
    assert alpha.evidence.terminal_state == "outcome_ambiguous_blocked"

    # beta must still publish — alpha's ambiguous epoch is scoped to alpha's store.
    beta = broker.execute(_routing_request("/ws/beta", repos["/ws/beta"], key="kb"))
    assert beta.accepted, "beta was fail-closed by alpha's ambiguous outcome (shared-epoch poison)"
    assert beta.publish_result.pr_url == repos["/ws/beta"]["url"]
