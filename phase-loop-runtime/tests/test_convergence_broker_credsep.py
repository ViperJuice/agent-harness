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
        # agent-harness#202: broker's server-authoritative diff. _request() owns ("a.py",),
        # so the branch changing only a.py stays within the admitted scope.
        (("diff", "--name-only"), "a.py\n", 0),
        (("log",), "commit subject line", 0),
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


# --- agent-harness#202: broker binds the admitted owned scope to the branch's actual
# content by re-diffing origin/<base>...head_sha itself, instead of trusting the
# coordinator-supplied owned_paths. ---
def _request_owning(*owned, base="main"):
    admission = AdmissionRequest("attempt", 1, "fence", "digest", "predicate", "scope", "key")
    return BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, "repo", _BRANCH, _HEAD, tuple(owned), base=base)


def test_branch_change_outside_owned_scope_is_rejected_before_push(tmp_path):
    # The branch actually changed b.py, but the admission only covers a.py → the broker
    # must reject BEFORE pushing (no push/create/ls-remote calls happen).
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "a.py\nb.py\n", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py"))
    assert result is None
    # a definitive pre-push reject is a PROVEN no-effect (valid IN_FLIGHT->terminal), not
    # rejected_before_start (which is unreachable once intent is recorded).
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert "owned-scope-exceeded" in evidence.evidence_reference
    assert "b.py" in evidence.evidence_reference
    # fail-closed BEFORE any mutation
    assert not any("push" in c or "create" in c or "ls-remote" in c for c in run.calls)


def test_nonempty_owned_but_empty_branch_diff_is_rejected(tmp_path):
    # The admission claims owned changes (a.py) but the branch has NO diff vs base:
    # the admitted scope does not match the mutation (drift, or a gamed base==head ref)
    # -> reject before any push.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "", 0),  # branch changed nothing vs base
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py"))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert evidence.evidence_reference == "owned-scope-empty-diff"
    assert not any("push" in c for c in run.calls)


def test_scope_reject_is_a_valid_terminal_through_the_broker_service(tmp_path):
    # Regression for the IN_FLIGHT->terminal transition: BrokerService records intent
    # (PROVIDER_CALL_IN_FLIGHT) BEFORE the adapter runs, so the adapter's scope reject must
    # be a state reachable from IN_FLIGHT. no_effect_terminal_proven is; rejected_before_start
    # is NOT (record_terminal would raise -> the reject would be mislabeled adapter-exception).
    # This exercises the FULL service path the adapter-only tests above bypass.
    from phase_loop_runtime.convergence.broker.evidence import BrokerEvidenceStore
    from phase_loop_runtime.convergence.broker.verbs import BrokerService
    from phase_loop_runtime.convergence.provider_contracts import PROVIDER_COMPLETION_CLASSIFICATIONS

    class _AdmitAll:
        def admit(self, admission):
            return None

    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "a.py\nb.py\n", 0),  # b.py outside owned ("a.py",)
    ])
    service = BrokerService(
        _AdmitAll(),
        BrokerEvidenceStore(tmp_path / "evidence.jsonl"),
        GitHubBrokerAdapter(tmp_path, run=run),
        contracts=PROVIDER_COMPLETION_CLASSIFICATIONS,
    )
    outcome = service.execute(_request_owning("a.py"))
    assert outcome.accepted is False
    # the reject survives the service intact — NOT converted to adapter-exception/ambiguous.
    assert outcome.evidence.terminal_state == "no_effect_terminal_proven"
    assert "owned-scope-exceeded" in outcome.evidence.evidence_reference
    assert outcome.reason != "outcome_ambiguous"
    assert not any("push" in c for c in run.calls)

    # #202 replay semantics: a second request for the SAME (repo, branch, head_sha) replays
    # the recorded reject WITHOUT re-invoking the adapter (no new diff), even under a fresh
    # admission with drifted owned_paths. Sound because content is head_sha-bound: replay
    # publishes nothing new. Retry of the same commit stays rejected (produce a new head).
    calls_before = len(run.calls)
    replayed = service.execute(_request_owning("a.py", "b.py"))  # widened owned, same triple
    assert replayed.accepted is False
    assert replayed.evidence.terminal_state == "no_effect_terminal_proven"
    assert len(run.calls) == calls_before  # adapter not re-invoked; pure replay


def test_diff_failure_fails_closed_no_effect_no_push(tmp_path):
    # The broker cannot compute the branch diff (e.g. origin/<base> missing) → refuse to
    # publish. This is a PROVEN no-effect (no push), NOT outcome_ambiguous_blocked — a
    # purely-local read-only git failure must not permanently poison the repo's epoch.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "", 1),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py"))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert evidence.evidence_reference == "owned-scope-diff-failed"
    assert not any("push" in c for c in run.calls)


def test_directory_owned_entry_covers_changed_files_under_it(tmp_path):
    # An owned DIRECTORY entry covers files beneath it, so a real diff under src/ passes.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "src/pkg/mod.py\nsrc/pkg/other.py\n", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("src"))
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None


def test_broker_re_diffs_against_the_request_base(tmp_path):
    # The broker's diff must use origin/<request.base>...head_sha (three-dot), not a
    # hardcoded base, so it reconciles with the coordinator's derivation.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "a.py\n", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py", base="release/2.0"))
    diff = next(c for c in run.calls if "diff" in c)
    assert f"origin/release/2.0...{_HEAD}" in diff


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


# --- Live-PILOT regression: `gh pr create` MUST be a complete non-interactive argv.
# The bare `--draft`/`--fill` form aborts headless ("must provide --title and --body"),
# so a mocked-run unit test passes while the real broker cannot open ANY PR.  Assert the
# create argv carries --title + --body (+ --draft for a draft request) and pins --head. ---
def test_pr_create_is_noninteractive_with_title_body_head(tmp_path):
    body = "## Cross-repo release train\n\nbody text"
    admission = AdmissionRequest("attempt", 1, "fence", "digest", "predicate", "scope", "key")
    request = BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, "repo", _BRANCH, _HEAD, ("a.py",), draft=True, pr_body=body)
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(request)
    create = next(c for c in run.calls if c[:3] == ["gh", "pr", "create"])
    assert "--title" in create and create[create.index("--title") + 1], f"create lacks non-empty --title: {create!r}"
    assert "--body" in create and create[create.index("--body") + 1] == body, f"create lacks pr_body --body: {create!r}"
    assert "--head" in create and _BRANCH in create, f"create lacks explicit --head: {create!r}"
    assert "--draft" in create, f"draft request must carry --draft: {create!r}"


def test_unresolvable_origin_fails_closed(tmp_path):
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only"), "a.py\n", 0),  # #202 scope check passes; origin fails after
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
