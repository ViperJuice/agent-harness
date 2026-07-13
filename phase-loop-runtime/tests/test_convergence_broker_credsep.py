import json
from types import SimpleNamespace

from phase_loop_runtime.convergence.broker.credsep import BrokerEnvironmentBoundary, GitHubBrokerAdapter, build_non_force_branch_ref
from phase_loop_runtime.convergence.contracts import AdmissionRequest, BrokerRequest, BrokerVerb


def test_only_broker_role_receives_mutation_credentials():
    env = {"GH_TOKEN": "value", "NORMAL": "yes"}
    assert "GH_TOKEN" in BrokerEnvironmentBoundary().environment_for("broker", env)
    assert "GH_TOKEN" not in BrokerEnvironmentBoundary().environment_for("worker", env)
    assert build_non_force_branch_ref("feature/broker") == "refs/heads/feature/broker"


# --- Blocker 3: exact-published-head verification (injectable git/gh seam) ---
_HEAD = "abc123def456"
_BRANCH = "feat/x"


class _FakeRun:
    """Match a git/gh argv against distinctive tokens; no live GitHub."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        for tokens, stdout, rc in self.responses:
            if all(tok in args for tok in tokens):
                return SimpleNamespace(stdout=stdout, stderr="", returncode=rc)
        raise AssertionError(f"unexpected command: {args!r}")


def _request():
    admission = AdmissionRequest("attempt", 1, "fence", "digest", "predicate", "scope", "key")
    return BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, "repo", _BRANCH, _HEAD, ("a.py",))


def _base_responses():
    return [
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("push",), "", 0),
        (("create",), "", 0),
    ]


def test_remote_head_match_returns_effect_observed_with_real_url(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None and result.pr_url == "https://gh/pr/9"
    assert result.head_sha == _HEAD


def test_remote_head_mismatch_returns_ambiguous_not_success(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"deadbeef\trefs/heads/{_BRANCH}", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert result is None
    assert evidence.terminal_state == "outcome_ambiguous_blocked"
    assert evidence.evidence_reference == "remote-head-mismatch"


def test_remote_read_failure_returns_ambiguous_not_no_effect(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), "", 1),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert result is None
    # v5 rule: a failed read is ambiguous, never inferred as no_effect nor success.
    assert evidence.terminal_state == "outcome_ambiguous_blocked"
    assert evidence.evidence_reference == "remote-read-failed"


def test_pr_head_unconfirmed_returns_ambiguous(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": "other-sha"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert result is None
    assert evidence.terminal_state == "outcome_ambiguous_blocked"
