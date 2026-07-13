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
        (("get-url",), "https://github.com/owner/repo.git", 0),
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


# --- CR hardening: bind every gh call to the origin repo (no GH_REPO redirect) ---
def test_gh_calls_are_bound_to_origin_repo_slug(tmp_path):
    """gh pr create + pr list must carry --repo <origin slug> so a stray GH_REPO
    cannot open/read the PR on a different repo while the push+ls-remote match."""
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    gh = [c for c in run.calls if c and c[0] == "gh"]
    assert gh, "expected gh calls"
    for c in gh:
        # host-qualified: --repo host/owner/repo pins both host AND repo
        assert "--repo" in c and "github.com/owner/repo" in c, f"gh call not host-bound: {c!r}"


def test_unresolvable_origin_fails_closed(tmp_path):
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("get-url",), "not-a-git-url", 0),
    ])
    try:
        GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    except ValueError as e:
        assert "origin" in str(e)
    else:
        raise AssertionError("expected fail-closed on unresolvable origin")


def test_broker_env_strips_repo_redirect_but_keeps_credential_and_host():
    env = {"GH_TOKEN": "t", "GH_REPO": "evil/repo", "GH_HOST": "ghe.corp", "NORMAL": "y"}
    broker = BrokerEnvironmentBoundary().environment_for("broker", env)
    assert broker.get("GH_TOKEN") == "t"          # broker needs its credential
    assert "GH_REPO" not in broker               # the repo-redirect var is stripped
    assert broker.get("GH_HOST") == "ghe.corp"   # GH_HOST kept (host pinned by --repo; preserves GHE config)
    assert broker.get("NORMAL") == "y"


import pytest
@pytest.mark.parametrize("url,slug", [
    ("https://github.com/owner/repo.git", "github.com/owner/repo"),
    ("https://github.com/owner/repo", "github.com/owner/repo"),
    ("git@github.com:owner/repo.git", "github.com/owner/repo"),
])
def test_origin_repo_host_qualified_for_github(tmp_path, url, slug):
    run = _FakeRun([(("branch", "--show-current"), _BRANCH, 0), (("rev-parse",), _HEAD, 0), (("get-url",), url, 0)])
    assert GitHubBrokerAdapter(tmp_path, run=run)._origin_repo() == slug


# Class-closing origin-host invariant: any non-allow-listed host FAILS CLOSED by
# default; a self-hosted/GHE fleet resolves only when it passes its own allow-list.
@pytest.mark.parametrize("url", [
    "git@ghe.corp:team/svc.git", "ssh://git@ghe.corp:2222/team/svc.git", "https://ghe.corp/team/svc",
])
def test_non_allowlisted_origin_host_fails_closed_by_default(tmp_path, url):
    run = _FakeRun([(("branch", "--show-current"), _BRANCH, 0), (("rev-parse",), _HEAD, 0), (("get-url",), url, 0)])
    with pytest.raises(ValueError, match="allowed broker hosts"):
        GitHubBrokerAdapter(tmp_path, run=run)._origin_repo()

def test_ghe_origin_resolves_only_with_explicit_allowlist(tmp_path):
    run = _FakeRun([(("branch", "--show-current"), _BRANCH, 0), (("rev-parse",), _HEAD, 0),
                    (("get-url",), "git@ghe.corp:team/svc.git", 0)])
    adapter = GitHubBrokerAdapter(tmp_path, run=run, allowed_hosts=frozenset({"ghe.corp"}))
    assert adapter._origin_repo() == "ghe.corp/team/svc"

@pytest.mark.parametrize("bad", [
    "not-a-git-url", "https://github.com/onlyowner", "git@host:", "",
    "https://github.com:8443/o/r.git",  # non-default https port -> fail-closed
    "https://github.com:80/o/r",        # scheme-mismatched port (https+80) -> fail-closed
    "http://github.com:443/o/r",        # scheme-mismatched port (http+443) -> fail-closed
    "https://[::1]/team/svc.git",        # IPv6 literal -> fail-closed
])
def test_origin_repo_fails_closed_on_garbage_or_unpinnable(tmp_path, bad):
    run = _FakeRun([(("branch", "--show-current"), _BRANCH, 0), (("rev-parse",), _HEAD, 0), (("get-url",), bad, 0)])
    with pytest.raises(ValueError):
        GitHubBrokerAdapter(tmp_path, run=run)._origin_repo()


# --- Round-5 fix: push + ls-remote use the EXPLICIT validated origin URL, never the
# `origin` alias (so remote.origin.pushurl / url.*.pushInsteadOf can't redirect the
# mutation to an unvalidated same-host repo). ---
def test_push_and_lsremote_bind_to_explicit_origin_url_not_alias(tmp_path):
    origin = "https://github.com/owner/repo.git"
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    push = next(c for c in run.calls if "push" in c)
    lsrem = next(c for c in run.calls if "ls-remote" in c)
    assert origin in push and "origin" not in push[push.index("push") + 1:], f"push not bound to explicit url: {push!r}"
    assert origin in lsrem and "origin" not in lsrem[lsrem.index("ls-remote") + 1:], f"ls-remote not bound to explicit url: {lsrem!r}"
