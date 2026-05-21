"""Repo-local handoff path resolver (HANDOFFS phase).

Mirrors `shared/phase-loop/handoff_path.py` so the resolver travels with
the installed package. NEUTRALIZE will consolidate this into the broader
`skill_paths` module.
"""

from __future__ import annotations

from pathlib import Path


def resolve_handoff_path(repo: str | Path, skill_name: str) -> Path:
    return Path(repo).expanduser().resolve() / ".dev-skills" / "handoffs" / skill_name / "latest.md"
