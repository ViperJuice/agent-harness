from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__
from .baml_modular import parse_baml_response
from .events import read_events
from .install_status import build_install_status
from .models import PIPELINE_MODE_LITERALS, StateSnapshot, require_literal, utc_now
from .reconcile import reconcile
from .skill_paths import current_harness


def build_runtime_projection(
    repo: Path,
    roadmap: Path,
    snapshot: StateSnapshot | None = None,
    pipeline_mode: str = "standalone",
) -> dict[str, object]:
    require_literal(pipeline_mode, PIPELINE_MODE_LITERALS, "pipeline mode")
    resolved = snapshot or reconcile(repo, roadmap)
    install = build_install_status(repo)
    payload: dict[str, object] = {
        "runtime_version": __version__,
        "protocol_version": "phase-loop-protocol-v1",
        "harness": current_harness(),
        "source_bundle_digest": _source_bundle_digest(repo),
        "closeout_status": _closeout_status(resolved),
        "handoff_status": "written" if (repo / ".phase-loop" / "tui-handoff.md").is_file() else "missing",
        "current_phase_boundary": resolved.current_phase or "none",
        "last_event_iso": _last_event_iso(repo) or resolved.timestamp or utc_now(),
        "install_status": str(install.get("summary") or "unknown"),
        "gitignore_init_status": _gitignore_init_status(repo),
        "operating_mode": pipeline_mode,
    }
    _assert_redacted(payload)
    parse_baml_response("DotfilesRuntimeProjection", json.dumps(payload))
    return payload


def _source_bundle_digest(repo: Path) -> str:
    for event in reversed(read_events(repo)):
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        bundle = metadata.get("pipeline_source_bundle")
        if isinstance(bundle, dict) and isinstance(bundle.get("sha256"), str) and bundle["sha256"]:
            return f"sha256:{bundle['sha256']}"
    return "none"


def _closeout_status(snapshot: StateSnapshot) -> str:
    if snapshot.closeout_terminal_status:
        return snapshot.closeout_terminal_status
    if isinstance(snapshot.terminal_summary, dict):
        status = snapshot.terminal_summary.get("terminal_status")
        if isinstance(status, str) and status:
            return status
    if snapshot.current_phase and snapshot.current_phase in snapshot.phases:
        return snapshot.phases[snapshot.current_phase]
    return "unknown"


def _last_event_iso(repo: Path) -> str | None:
    events = read_events(repo)
    if not events:
        return None
    timestamp = events[-1].get("timestamp")
    return str(timestamp) if timestamp else None


def _gitignore_init_status(repo: Path) -> str:
    gitignore = repo / ".gitignore"
    if not gitignore.is_file():
        return "missing"
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    return "present" if "/.dev-skills/" in lines or ".dev-skills/" in lines else "missing"


def _assert_redacted(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    forbidden = ("/home/", "/Users/", "/mnt/", "op://", "sk-", "AKIA", "ghp_")
    leaked = [token for token in forbidden if token in serialized]
    if leaked:
        raise ValueError(f"runtime projection contains forbidden metadata token: {leaked[0]}")
