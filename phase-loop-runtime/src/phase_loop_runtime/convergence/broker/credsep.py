"""Credential boundary and GitHub publish adapter."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Mapping

from phase_loop_runtime.convergence.contracts import BrokerRequest, BrokerTerminalEvidence, PublishCommittedBranchResult

MUTATION_CREDENTIAL_KEYS = frozenset({"GH_TOKEN", "GITHUB_TOKEN"})
def strip_mutation_credentials(environment: Mapping[str, str]) -> dict[str, str]: return {k: v for k, v in environment.items() if k not in MUTATION_CREDENTIAL_KEYS}
class BrokerEnvironmentBoundary:
    def environment_for(self, role: str, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if environment is None else environment)
        return env if role == "broker" else strip_mutation_credentials(env)
def build_non_force_branch_ref(branch: str) -> str:
    if not branch or branch.startswith("-") or branch in {"main", "master", "develop", "release"}: raise ValueError("unsafe branch")
    return f"refs/heads/{branch}"
class GitHubBrokerAdapter:
    def __init__(self, repo_path: Path, run=subprocess.run) -> None: self.repo_path, self.run = repo_path, run
    def _output(self, *args: str) -> str:
        return self.run(["git", "-C", str(self.repo_path), *args], capture_output=True, text=True, check=True).stdout.strip()
    def _ambiguous(self, request: BrokerRequest, reference: str):
        # v5 rule: a failed/empty remote read is NEVER inferred as no_effect and
        # NEVER fabricated as success — it is a permanent ambiguous block.
        return None, BrokerTerminalEvidence(request.admission.idempotency_key, "outcome_ambiguous_blocked", reference)

    def execute(self, request: BrokerRequest):
        if self._output("branch", "--show-current") != request.branch or self._output("rev-parse", "HEAD") != request.head_sha: raise ValueError("branch/head mismatch")
        ref = build_non_force_branch_ref(request.branch)
        pushed = self.run(["git", "-C", str(self.repo_path), "push", "origin", ref], capture_output=True, text=True)
        if pushed.returncode: return self._ambiguous(request, "push-unconfirmed")
        args = ["gh", "pr", "create", "--draft"] if request.draft else ["gh", "pr", "create", "--fill"]
        created = self.run(args, cwd=self.repo_path, capture_output=True, text=True)
        if created.returncode: return self._ambiguous(request, "pr-unconfirmed")
        # Exact-published-head verification: READ the remote and confirm the branch
        # head on origin equals the pushed sha, then resolve the REAL PR url and
        # confirm its headRefOid matches.  Only then is the effect terminally observed.
        remote = self.run(["git", "-C", str(self.repo_path), "ls-remote", "origin", ref], capture_output=True, text=True)
        if remote.returncode: return self._ambiguous(request, "remote-read-failed")
        remote_sha = remote.stdout.split("\t", 1)[0].strip() if remote.stdout.strip() else ""
        if not remote_sha: return self._ambiguous(request, "remote-branch-absent")
        if remote_sha != request.head_sha: return self._ambiguous(request, "remote-head-mismatch")
        listed = self.run(["gh", "pr", "list", "--head", request.branch, "--json", "url,headRefOid"], cwd=self.repo_path, capture_output=True, text=True)
        if listed.returncode: return self._ambiguous(request, "pr-read-failed")
        try:
            prs = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            return self._ambiguous(request, "pr-read-unparsable")
        match = next((p for p in prs if p.get("headRefOid") == request.head_sha and p.get("url")), None)
        if match is None: return self._ambiguous(request, "pr-head-unconfirmed")
        return PublishCommittedBranchResult(request.branch, request.head_sha, match["url"]), BrokerTerminalEvidence(request.admission.idempotency_key, "effect_terminal_observed", match["url"])
