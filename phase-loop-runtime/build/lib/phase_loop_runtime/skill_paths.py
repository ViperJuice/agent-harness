from __future__ import annotations

import os
from pathlib import Path


HARNESS_DEFAULT_SKILL_ROOTS: dict[str, Path] = {
    "claude": Path("~/.claude/skills"),
    "codex": Path("~/.codex/skills"),
    "gemini": Path("~/.gemini/skills"),
    "opencode": Path("~/.config/opencode/skills"),
}


def current_harness(harness: str | None = None) -> str:
    selected = harness or os.environ.get("PHASE_LOOP_HARNESS") or "codex"
    normalized = selected.strip().lower()
    if normalized not in HARNESS_DEFAULT_SKILL_ROOTS:
        allowed = ", ".join(sorted(HARNESS_DEFAULT_SKILL_ROOTS))
        raise ValueError(f"unsupported phase-loop harness {selected!r}; expected one of: {allowed}")
    return normalized


def resolve_skill_bundle_root(harness: str | None = None) -> Path:
    override = os.environ.get("PHASE_LOOP_SKILL_BUNDLE")
    if override:
        return Path(override).expanduser()
    return HARNESS_DEFAULT_SKILL_ROOTS[current_harness(harness)].expanduser()


def resolve_skill_helper_root(harness: str | None = None) -> Path:
    return resolve_skill_bundle_root(harness) / "_shared"


def resolve_handoff_root(repo: Path) -> Path:
    return Path(repo).expanduser().resolve() / ".dev-skills" / "handoffs"


def resolve_reflection_root(skill_name: str, harness: str | None = None) -> Path:
    if not skill_name:
        raise ValueError("skill_name is required")
    return resolve_skill_bundle_root(harness) / skill_name / "reflections"
