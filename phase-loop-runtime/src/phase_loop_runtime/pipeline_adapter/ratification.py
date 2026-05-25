from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..git_topology import collect_git_topology
from ..runtime_paths import ensure_phase_loop_excluded, phase_loop_event_file
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
) -> None:
    repo = Path(repo_root)
    payload = _payload(repo, roadmap_version, phase_alias, ratification_gate, merge_policy, audit)
    ensure_phase_loop_excluded(repo)
    event_path = phase_loop_event_file(repo)
    event_path.parent.mkdir(parents=True, exist_ok=True)
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
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")

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
    upstream = _git_output_or_empty(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.startswith("origin/"):
        return upstream.removeprefix("origin/")
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
