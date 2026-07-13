"""Credential boundary and GitHub publish adapter."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

from phase_loop_runtime.convergence.contracts import BrokerRequest, BrokerTerminalEvidence, PublishCommittedBranchResult

MUTATION_CREDENTIAL_KEYS = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})
# Repo-redirect env var: gh resolves its target repo from GH_REPO over cwd's origin,
# so a stray GH_REPO could send a `gh pr create` to a DIFFERENT repo while the
# push+ls-remote (bound to origin) still match — an undetected wrong-repo PR. The
# host is instead pinned by the host-qualified `--repo host/owner/repo` (which beats
# GH_HOST/gh-config), so GH_HOST is left intact to preserve GHE config routing.
REPO_REDIRECT_KEYS = frozenset({"GH_REPO"})
def strip_mutation_credentials(environment: Mapping[str, str]) -> dict[str, str]: return {k: v for k, v in environment.items() if k not in MUTATION_CREDENTIAL_KEYS}
class BrokerEnvironmentBoundary:
    def environment_for(self, role: str, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if environment is None else environment)
        # The broker keeps its mutation credential (to push) but NEVER the repo-redirect
        # vars; every other role is stripped of credentials.
        return {k: v for k, v in env.items() if k not in REPO_REDIRECT_KEYS} if role == "broker" else strip_mutation_credentials(env)
def build_non_force_branch_ref(branch: str) -> str:
    if not branch or branch.startswith("-") or branch in {"main", "master", "develop", "release"}: raise ValueError("unsafe branch")
    return f"refs/heads/{branch}"
# Origin-host invariant (fail-closed): the broker only publishes to an allow-listed
# host, so a `gh --repo host/owner/repo` derived from the origin url can NEVER be
# mis-bound to a look-alike host by any URL-text edge (custom port, IPv6, ssh alias,
# twin instance).  Default is github.com-only; a self-hosted/GHE fleet passes its own
# allow-list explicitly.  This retires the whole host-parse edge class at the boundary.
ALLOWED_ORIGIN_HOSTS = frozenset({"github.com"})
class GitHubBrokerAdapter:
    def __init__(self, repo_path: Path, run=subprocess.run, allowed_hosts: frozenset[str] = ALLOWED_ORIGIN_HOSTS) -> None:
        self.repo_path, self.run, self.allowed_hosts = repo_path, run, allowed_hosts
    def _output(self, *args: str) -> str:
        return self.run(["git", "-C", str(self.repo_path), *args], capture_output=True, text=True, check=True).stdout.strip()
    def _origin_url(self) -> str:
        # The single canonical origin URL the broker validates AND operates through
        # explicitly (push + ls-remote), so the mutation can never be redirected by the
        # `origin` alias (remote.origin.pushurl / url.*.pushInsteadOf) to an unvalidated
        # target.  This is git's FETCH url; the broker deliberately publishes to the
        # canonical repo it validated, not a local triangular pushurl.
        return self._output("remote", "get-url", "origin")
    def _origin_repo(self) -> str:
        return self._slug_for(self._origin_url())
    def _slug_for(self, url: str) -> str:
        # Resolve a git URL to a HOST-QUALIFIED `host/owner/repo` slug so `gh` is bound
        # with `--repo host/owner/repo` (highest precedence — beats a stray
        # GH_REPO/GH_HOST/cwd/gh-config), pinning the PR to the SAME host AND repo the
        # push targets.  Fail-closed if the URL cannot be resolved or is not allow-listed.
        if "://" in url:  # scheme://[user@]host[:port]/owner/repo(.git)
            scheme = url.split("://", 1)[0].lower()
            rest = url.split("://", 1)[1].split("@", 1)[-1]
            authority, _, path = rest.partition("/")
            # Fail-closed on authorities --repo cannot faithfully pin: an IPv6 literal
            # (mis-split below) or a non-default http(s) API port (silently dropped →
            # gh would hit the default-port host, a twin-host risk).  ssh transport
            # ports are irrelevant to the gh API host, so they are allowed.
            if authority.startswith("[") or authority.count(":") > 1:
                raise ValueError(f"unsupported IPv6/authority in origin {url!r}")
            host, _, port = authority.partition(":")
            default_port = {"https": "443", "http": "80"}.get(scheme)
            if scheme in ("http", "https") and port and port != default_port:
                raise ValueError(f"non-default {scheme} port in origin {url!r} cannot be pinned by --repo")
        else:  # scp-like: [user@]host:owner/repo(.git)
            hostpart, sep, path = url.partition(":")
            host = hostpart.split("@", 1)[-1]
            if not sep:
                raise ValueError(f"cannot resolve origin host/owner/repo from {url!r}")
        if path.endswith(".git"):
            path = path[:-4]
        path = path.strip("/")
        parts = path.split("/")
        if not host or len(parts) != 2 or not all(parts):
            raise ValueError(f"cannot resolve origin host/owner/repo from {url!r}")
        if host not in self.allowed_hosts:
            # Origin-host invariant: refuse to publish to a host outside the allow-list.
            # This is the class-closing gate — no URL-text edge (port/IPv6/alias/twin)
            # can mis-bind a gh call to a look-alike host, because a non-allow-listed
            # host fails closed here (-> outcome_ambiguous_blocked), never a live PR.
            raise ValueError(f"origin host {host!r} not in allowed broker hosts {sorted(self.allowed_hosts)}")
        return f"{host}/{path}"
    def _ambiguous(self, request: BrokerRequest, reference: str):
        # v5 rule: a failed/empty remote read is NEVER inferred as no_effect and
        # NEVER fabricated as success — it is a permanent ambiguous block.
        return None, BrokerTerminalEvidence(request.admission.idempotency_key, "outcome_ambiguous_blocked", reference)

    def execute(self, request: BrokerRequest):
        if self._output("branch", "--show-current") != request.branch or self._output("rev-parse", "HEAD") != request.head_sha: raise ValueError("branch/head mismatch")
        origin_url = self._origin_url()          # ONE canonical url, used explicitly everywhere
        origin_repo = self._slug_for(origin_url) # validate allow-list + derive the gh slug from the SAME url
        ref = build_non_force_branch_ref(request.branch)
        # Push + ls-remote target the EXPLICIT validated url, never the `origin` alias,
        # so remote.origin.pushurl / url.*.pushInsteadOf cannot redirect the mutation.
        pushed = self.run(["git", "-C", str(self.repo_path), "push", origin_url, ref], capture_output=True, text=True)
        if pushed.returncode: return self._ambiguous(request, "push-unconfirmed")
        # `gh pr create` REQUIRES --title (+ --body) when non-interactive; the bare
        # `--draft`/`--fill` form aborts headless ("must provide --title and --body").
        # Derive a deterministic title from the branch HEAD's commit subject and pass
        # the request's pr_body verbatim (empty body is valid once --title is present).
        # --head pins the branch explicitly so gh never infers a wrong head.
        title = self._output("log", "-1", "--format=%s") or request.branch
        args = ["gh", "pr", "create", "--repo", origin_repo, "--head", request.branch, "--title", title, "--body", request.pr_body or title]
        if request.draft: args.append("--draft")
        created = self.run(args, cwd=self.repo_path, capture_output=True, text=True)
        if created.returncode: return self._ambiguous(request, "pr-unconfirmed")
        # Exact-published-head verification: READ the remote and confirm the branch
        # head on origin equals the pushed sha, then resolve the REAL PR url and
        # confirm its headRefOid matches.  Only then is the effect terminally observed.
        remote = self.run(["git", "-C", str(self.repo_path), "ls-remote", origin_url, ref], capture_output=True, text=True)
        if remote.returncode: return self._ambiguous(request, "remote-read-failed")
        remote_sha = remote.stdout.split("\t", 1)[0].strip() if remote.stdout.strip() else ""
        if not remote_sha: return self._ambiguous(request, "remote-branch-absent")
        if remote_sha != request.head_sha: return self._ambiguous(request, "remote-head-mismatch")
        listed = self.run(["gh", "pr", "list", "--repo", origin_repo, "--head", request.branch, "--json", "url,headRefOid"], cwd=self.repo_path, capture_output=True, text=True)
        if listed.returncode: return self._ambiguous(request, "pr-read-failed")
        try:
            prs = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            return self._ambiguous(request, "pr-read-unparsable")
        match = next((p for p in prs if p.get("headRefOid") == request.head_sha and p.get("url")), None)
        if match is None: return self._ambiguous(request, "pr-head-unconfirmed")
        return PublishCommittedBranchResult(request.branch, request.head_sha, match["url"]), BrokerTerminalEvidence(request.admission.idempotency_key, "effect_terminal_observed", match["url"])
