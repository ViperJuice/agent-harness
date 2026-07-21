import json
import os
import unittest.mock
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
        # agent-harness#202/#250: broker's server-authoritative diff, now `-z --no-renames`
        # (NUL-delimited). _request() owns ("a.py",), so the branch changing only a.py
        # stays within the admitted scope.
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
        (("log",), "commit subject line", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
    ]


def test_remote_head_match_returns_effect_observed_with_real_url(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
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


# --- agent-harness#250 (N6, cross-vendor CR, codex): GitHub allows a PR's base to be
# retargeted after creation (or a same-head PR to simply target a different base), so
# matching ONLY headRefOid lets a PR that was scope-checked against request.base but
# is/gets retargeted to a DIFFERENT base be recorded as the successful effect — bypassing
# the #202 owned-scope check entirely (a later branch-based merge would use the PR's
# actual, unchecked base). The read-back must verify BOTH headRefOid AND baseRefName
# equal the request's before accepting the match, and fail CLOSED (ambiguous, not
# no-effect — a PR genuinely exists at that head) when the base does not match. ---
def test_pr_head_match_but_base_mismatch_fails_closed_to_ambiguous(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "some-other-base"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert result is None
    assert evidence.terminal_state == "outcome_ambiguous_blocked"
    assert evidence.evidence_reference == "pr-base-unconfirmed"


def test_pr_head_and_base_both_match_returns_effect_observed(tmp_path):
    # Positive case: headRefOid AND baseRefName both equal the request's -> accepted.
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None and result.pr_url == "https://gh/pr/9"


def test_pr_head_match_but_base_mismatch_on_non_default_base_fails_closed(tmp_path):
    # Same escape on a non-default base: the PR list can carry multiple entries with
    # the same head (e.g. the retargeted PR plus a stale one) — a base-matched entry
    # among several head-matched ones must still be found; when NONE match base, the
    # read-back must fail closed even though a head-matched PR does exist.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([
            {"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"},
        ]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py", base="release/2.0"))
    assert result is None
    assert evidence.terminal_state == "outcome_ambiguous_blocked"
    assert evidence.evidence_reference == "pr-base-unconfirmed"


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
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0b.py\0", 0),
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
        (("diff", "--name-only", "-z", "--no-renames"), b"", 0),  # branch changed nothing vs base
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
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0b.py\0", 0),  # b.py outside owned ("a.py",)
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
        (("diff", "--name-only", "-z", "--no-renames"), b"", 1),
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
        (("diff", "--name-only", "-z", "--no-renames"), b"src/pkg/mod.py\0src/pkg/other.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
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
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "release/2.0"}]), 0),
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
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
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
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
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
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),  # #202 scope check passes; origin fails after
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
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    push = next(c for c in run.calls if "push" in c)
    lsrem = next(c for c in run.calls if "ls-remote" in c)
    assert origin in push and "origin" not in push[push.index("push") + 1:], f"push not bound to explicit url: {push!r}"
    assert origin in lsrem and "origin" not in lsrem[lsrem.index("ls-remote") + 1:], f"ls-remote not bound to explicit url: {lsrem!r}"


# --- agent-harness#250 (N1/N4): `-z --no-renames` closes the rename-escape where a
# rename's DESTINATION is reported but its unowned SOURCE is hidden. ---
def test_rename_of_unowned_file_into_owned_scope_is_caught(tmp_path):
    # `git mv unowned/x.py owned/y.py` with plain --name-only reports only owned/y.py
    # (destination), passing the coverage check. With --no-renames both endpoints are
    # reported as an add + a delete, so the unowned source must be caught here.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"owned/y.py\0unowned/x.py\0", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("owned"))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert "owned-scope-exceeded" in evidence.evidence_reference
    assert "unowned/x.py" in evidence.evidence_reference
    assert not any("push" in c or "create" in c for c in run.calls)


def test_legit_rename_within_owned_scope_is_not_false_rejected(tmp_path):
    # A rename where BOTH endpoints sit under the owned tree must NOT be false-rejected:
    # --no-renames reports it as delete(owned/old.py) + add(owned/new.py), and both are
    # covered by the owned "owned" directory entry.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"owned/new.py\0owned/old.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("owned"))
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None


# --- agent-harness#250 (N5): push the EXACT validated head_sha, not the mutable branch
# ref — a ref-advance between the HEAD==head_sha check and the push must not be able to
# publish unverified content. ---
def test_push_targets_exact_head_sha_not_mutable_ref(tmp_path):
    run = _FakeRun(_base_responses() + [
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request())
    push = next(c for c in run.calls if "push" in c)
    # The pushspec must be exactly "<head_sha>:refs/heads/<branch>" — pinning the SOURCE
    # side of the refspec to the validated sha, never the bare mutable ref.
    assert f"{_HEAD}:refs/heads/{_BRANCH}" in push, f"push not pinned to head_sha: {push!r}"
    assert f"refs/heads/{_BRANCH}" not in push, f"push must not carry the bare mutable ref: {push!r}"


# --- agent-harness#250 (N6): `gh pr create` must carry --base <request.base> so the PR's
# actual base matches the base the broker scope-checked against. ---
def test_pr_create_carries_explicit_base(tmp_path):
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "release/2.0"}]), 0),
    ])
    GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py", base="release/2.0"))
    create = next(c for c in run.calls if c[:3] == ["gh", "pr", "create"])
    assert "--base" in create and create[create.index("--base") + 1] == "release/2.0", f"create lacks --base: {create!r}"


# --- agent-harness#250 (N2): reject git revision syntax / a self-referential base
# BEFORE diffing — fail closed through the existing no_effect_terminal_proven reject. ---
@pytest.mark.parametrize("bad_base", ["main~5", "main^2", "main@{upstream}", "main..other", _BRANCH])
def test_revision_syntax_or_self_referential_base_is_rejected(tmp_path, bad_base):
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        # No diff response registered: if the adapter attempted to diff on a bad base,
        # _FakeRun would raise AssertionError("unexpected command") — proving the reject
        # happens BEFORE the diff call, not just via a downstream scope check.
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py", base=bad_base))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert evidence.evidence_reference == "owned-scope-invalid-base"
    assert not any("push" in c or "diff" in c for c in run.calls)


def test_valid_base_with_slash_is_not_rejected_by_the_guard(tmp_path):
    # A legitimate non-default base (e.g. a release branch) must NOT trip the
    # revision-syntax guard merely for containing a slash.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "release/2.0"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py", base="release/2.0"))
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None


# --- agent-harness#250 (N3): empty owned_paths AND empty branch diff must not reach
# push — the pre-#250 guard only fired when owned_paths was non-empty. ---
def test_empty_owned_and_empty_diff_does_not_reach_push(tmp_path):
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning())  # owned_paths=()
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert evidence.evidence_reference == "owned-scope-empty-diff"
    assert not any("push" in c or "create" in c for c in run.calls)


# --- agent-harness#250 (IF-0-BRK-1 sharpening, cross-vendor CR): "identical parsing" on
# both sides is not a strong enough freeze if BOTH sides strip whitespace identically —
# that still approves the WRONG path when the diff path and the owned entry differ only
# by whitespace/newlines. The freeze must be filename BYTE-IDENTITY: split on \0, discard
# only the terminal empty element, never .strip()/trim an individual path. ---
def test_branch_diff_paths_preserves_embedded_newline_and_whitespace_verbatim(tmp_path):
    # A `-z` diff NUL-delimits paths but does NOT strip whitespace or escape embedded
    # newlines inside a path — the adapter must hand back exactly what git printed.
    weird = " leading-space.py"
    newline_name = "has\nnewline.py"
    stdout = f"a.py\0{weird}\0{newline_name}\0".encode()
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), stdout, 0),
    ])
    adapter = GitHubBrokerAdapter(tmp_path, run=run)
    paths = adapter._branch_diff_paths("main", _HEAD)
    assert paths == frozenset({"a.py", weird, newline_name})
    # Byte-identity, not merely "no exception": a stripped/trimmed variant must be ABSENT.
    assert "leading-space.py" not in paths
    assert "has" not in paths and "newline.py" not in paths


def test_whitespace_only_difference_is_not_covered_by_a_trimmed_owned_entry(tmp_path):
    # The adversarial case a `.strip()` bug would create: the branch actually changed
    # " a.py" (leading space) — a file OUTSIDE the admitted scope — while the admission
    # only covers "a.py" (no leading space). If either side trimmed the path, " a.py"
    # would collapse to "a.py" and be WRONGLY approved. Un-stripped, they must never
    # match: the coverage check must reject it as uncovered.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b" a.py\0", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py"))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert "owned-scope-exceeded" in evidence.evidence_reference
    assert " a.py" in evidence.evidence_reference
    assert not any("push" in c or "create" in c for c in run.calls)


def test_whitespace_padded_owned_entry_does_not_falsely_cover_a_different_clean_path(tmp_path):
    # The DANGEROUS direction of the adversarial case: the admitted scope is "a.py "
    # (trailing space — e.g. a sloppily-authored owned_paths entry), but the branch
    # actually changed the DIFFERENT file "a.py" (no trailing space). If the coverage
    # check trimmed the OWNED side, "a.py " would collapse to "a.py" and wrongly approve
    # a path that was never actually admitted — "approves the wrong path". Un-stripped,
    # "a.py" != "a.py " and the change must be rejected as uncovered.
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), b"a.py\0", 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning("a.py "))
    assert result is None
    assert evidence.terminal_state == "no_effect_terminal_proven"
    assert "owned-scope-exceeded" in evidence.evidence_reference
    assert "a.py" in evidence.evidence_reference
    assert not any("push" in c or "create" in c for c in run.calls)


def test_byte_identical_weird_filename_is_approved_end_to_end(tmp_path):
    # The legitimate-flow counterpart: a real whitespace-padded filename that IS
    # byte-identical between the branch diff and the admitted owned_paths (the normal
    # live-flow shape, where the coordinator derives owned_paths from the SAME diff)
    # must be approved and reach push — proving the fix doesn't just close the escape,
    # it also stops false-rejecting a legitimate weird filename.
    weird = "  padded.py  "
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), f"{weird}\0".encode(), 0),
        (("log",), "subject", 0),
        (("get-url",), "https://github.com/owner/repo.git", 0),
        (("push",), "", 0),
        (("create",), "", 0),
        (("ls-remote",), f"{_HEAD}\trefs/heads/{_BRANCH}", 0),
        (("list",), json.dumps([{"url": "https://gh/pr/9", "headRefOid": _HEAD, "baseRefName": "main"}]), 0),
    ])
    result, evidence = GitHubBrokerAdapter(tmp_path, run=run).execute(_request_owning(weird))
    assert evidence.terminal_state == "effect_terminal_observed"
    assert result is not None


def test_broker_and_coordinator_diff_derivation_agree_byte_for_byte_on_weird_paths(tmp_path):
    # The load-bearing coupling itself, proven directly: feed the SAME raw `-z` stdout
    # (including a whitespace-padded and a newline-embedded path) through BOTH the
    # broker's _branch_diff_paths and the coordinator's parsing logic, and assert the
    # resulting path sets are byte-identical — neither side may diverge by trimming.
    from phase_loop_runtime.train_runner import _prebuilt_owned_paths

    weird = "  padded-both-sides.py  "
    newline_name = "embedded\nnewline-file.py"
    stdout = f"a.py\0{weird}\0{newline_name}\0".encode()

    broker_run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), stdout, 0),
    ])
    broker_paths = GitHubBrokerAdapter(tmp_path, run=broker_run)._branch_diff_paths("main", _HEAD)

    class _FakeCompleted:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode, self.stderr = out, rc, b""

    with unittest.mock.patch(
        "phase_loop_runtime.train_runner.subprocess.run",
        return_value=_FakeCompleted(stdout),
    ):
        coordinator_paths = _prebuilt_owned_paths(tmp_path, "main")

    assert broker_paths == frozenset(coordinator_paths)
    assert broker_paths == frozenset({"a.py", weird, newline_name})


# --- agent-harness#250 (IF-0-BRK-1 byte-identity hole, cross-vendor CR): `text=True`
# universal-newline decoding translates the raw bytes `\r` AND `\r\n` into `\n` at decode
# time — AFTER which a NUL-split can no longer tell `a\r.py`, `a\r\n.py`, and `a\n.py`
# apart, so three DISTINCT valid git paths collapse onto the SAME Python string (a
# false-approve: the broker could admit a changed path different from the one actually
# covered by owned_paths). Bytes-capture + `os.fsdecode` (no `text=True`) must keep all
# three distinct. ---
def test_branch_diff_paths_distinguishes_cr_crlf_and_lf_filenames(tmp_path):
    cr_name = "a\rcr.py"
    crlf_name = "a\r\ncrlf.py"
    lf_name = "a\nlf.py"
    stdout = f"{cr_name}\0{crlf_name}\0{lf_name}\0".encode()
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), stdout, 0),
    ])
    paths = GitHubBrokerAdapter(tmp_path, run=run)._branch_diff_paths("main", _HEAD)
    # All three must be present, AND distinct — a text=True universal-newline collapse
    # would merge them onto a single "a\nlf.py" (or fewer) entries.
    assert paths == frozenset({cr_name, crlf_name, lf_name})
    assert len(paths) == 3


def test_branch_diff_paths_does_not_raise_on_invalid_utf8_filename(tmp_path):
    # A git path is bytes, not guaranteed UTF-8. `text=True` would raise
    # UnicodeDecodeError; bytes-capture + os.fsdecode (surrogateescape) must not crash and
    # must round-trip the raw bytes losslessly.
    invalid_bytes = b"invalid-\xffbyte.py"
    stdout = invalid_bytes + b"\0"
    run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), stdout, 0),
    ])
    paths = GitHubBrokerAdapter(tmp_path, run=run)._branch_diff_paths("main", _HEAD)
    assert len(paths) == 1
    decoded = next(iter(paths))
    assert os.fsencode(decoded) == invalid_bytes


# --- agent-harness#250 (IF-0-BRK-1 byte-identity, coupled freeze): the broker and
# coordinator derivations must agree byte-for-byte on a `\r`-bearing filename specifically
# (the exact escape codex's CR flagged), not just on whitespace/newline cases already
# covered above. ---
def test_broker_and_coordinator_agree_byte_for_byte_on_cr_bearing_filename(tmp_path):
    from phase_loop_runtime.train_runner import _prebuilt_owned_paths

    cr_name = "a\rcr.py"
    stdout = f"{cr_name}\0".encode()

    broker_run = _FakeRun([
        (("branch", "--show-current"), _BRANCH, 0),
        (("rev-parse",), _HEAD, 0),
        (("diff", "--name-only", "-z", "--no-renames"), stdout, 0),
    ])
    broker_paths = GitHubBrokerAdapter(tmp_path, run=broker_run)._branch_diff_paths("main", _HEAD)

    class _FakeCompleted:
        def __init__(self, out, rc=0):
            self.stdout, self.returncode, self.stderr = out, rc, b""

    with unittest.mock.patch(
        "phase_loop_runtime.train_runner.subprocess.run",
        return_value=_FakeCompleted(stdout),
    ):
        coordinator_paths = _prebuilt_owned_paths(tmp_path, "main")

    assert broker_paths == frozenset(coordinator_paths) == frozenset({cr_name})
