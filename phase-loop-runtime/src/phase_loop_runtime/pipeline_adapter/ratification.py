from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..git_topology import collect_git_topology
from ..events import append_payload
from ..models import utc_now
from .merge_policy import MergePolicy


TRIGGER_PATH = Path(".pipeline") / "ratification-trigger.json"


def emit_ratification_passed(
    repo_root: Path,
    roadmap_version: str,
    phase_alias: str,
    ratification_gate: str,
    merge_policy: MergePolicy,
    audit: dict[str, Any],
    *,
    roadmap_path: Path | None = None,
) -> None:
    repo = Path(repo_root)
    payload = _payload(repo, roadmap_version, phase_alias, ratification_gate, merge_policy, audit)
    event = {
        "timestamp": utc_now(),
        "repo": str(repo),
        "roadmap": roadmap_version,
        "phase": phase_alias,
        "event_type": "ratification.passed",
        "action": "ratification",
        "status": "passed",
        "source": "pipeline_adapter",
        "schema_version": 2,
        "payload": payload,
        "git_topology": collect_git_topology(repo),
    }
    append_payload(
        repo,
        event,
        roadmap=roadmap_path or repo / "specs" / f"phase-plans-{roadmap_version}.md",
    )

    trigger = repo / TRIGGER_PATH
    trigger.parent.mkdir(parents=True, exist_ok=True)
    trigger.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _payload(
    repo: Path,
    roadmap_version: str,
    phase_alias: str,
    ratification_gate: str,
    merge_policy: MergePolicy,
    audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "roadmap_version": roadmap_version,
        "phase_alias": phase_alias,
        "ratification_gate": ratification_gate,
        "merge_policy": merge_policy.to_json(),
        "audit": dict(audit),
        "pipeline_branch": _current_branch(repo),
        "default_branch": _default_branch(repo),
        "head_sha": _git_output_or_empty(repo, "rev-parse", "HEAD") or None,
        "merge_pr_title": f"{roadmap_version} phase {phase_alias} ratification: {ratification_gate}",
    }


def _current_branch(repo: Path) -> str:
    return _git_output_or_empty(repo, "branch", "--show-current") or "detached"


def _default_branch(repo: Path) -> str:
    remote_head = _git_output_or_empty(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if remote_head.startswith("origin/"):
        return remote_head.removeprefix("origin/")
    # Authoritative fallback: ask the remote for its HEAD when origin/HEAD is unset.
    # The previous fallback used @{upstream} which returns the current branch's
    # tracking ref and could mis-identify the pipeline branch as the default.
    ls_remote = _git_output_or_empty(repo, "ls-remote", "--symref", "origin", "HEAD")
    for line in ls_remote.splitlines():
        if line.startswith("ref: refs/heads/"):
            ref = line.split("\t", 1)[0].removeprefix("ref: refs/heads/").strip()
            if ref:
                return ref
    return "main"


def _git_output_or_empty(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""
