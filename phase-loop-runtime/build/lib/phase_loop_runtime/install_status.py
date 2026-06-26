from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .baml_modular import BamlValidationError, export_function_schema
from .skill_install import REQUIRED_SKILLS
from .skill_paths import HARNESS_DEFAULT_SKILL_ROOTS, resolve_skill_bundle_root


def build_install_status(repo: Path, harnesses: tuple[str, ...] | None = None) -> dict[str, Any]:
    selected = harnesses or tuple(HARNESS_DEFAULT_SKILL_ROOTS)
    records = tuple(_harness_record(harness) for harness in selected)
    payload = {
        "schema": "phase-loop-install-status.v1",
        "summary": summarize_install_status(records),
        "harnesses": records,
        "console_scripts": _console_scripts(),
        "baml_closeout_schema": _baml_schema_status(),
        "dev_skills_ignore": _dev_skills_ignore(repo),
    }
    _assert_redacted(payload)
    return payload


def summarize_install_status(records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    if not records:
        return "unknown"
    if all(record.get("skill_parity") == "complete" for record in records):
        return "installed"
    if any(record.get("root_status") == "missing" for record in records):
        return "partial"
    return "attention"


def _harness_record(harness: str) -> dict[str, Any]:
    root = resolve_skill_bundle_root(harness)
    missing = tuple(
        f"{harness}-{skill}"
        for skill in REQUIRED_SKILLS
        if not (root / f"{harness}-{skill}" / "SKILL.md").is_file()
    )
    return {
        "harness": harness,
        "root_status": "present" if root.exists() else "missing",
        "skill_parity": "complete" if not missing else "missing",
        "required_skill_count": len(REQUIRED_SKILLS),
        "missing_skill_count": len(missing),
        "missing_skills": missing,
    }


def _console_scripts() -> dict[str, str]:
    return {
        "phase-loop": "available" if shutil.which("phase-loop") else "missing",
        "codex-phase-loop": "available" if shutil.which("codex-phase-loop") else "missing",
    }


def _baml_schema_status() -> dict[str, str]:
    try:
        schema = export_function_schema("EmitPhaseCloseout")
    except BamlValidationError:
        return {"status": "missing", "schema": "EmitPhaseCloseout"}
    return {"status": "available", "schema": str(schema.get("title") or "EmitPhaseCloseout")}


def _dev_skills_ignore(repo: Path) -> dict[str, str]:
    gitignore = repo / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    ignored = "/.dev-skills/" in lines or ".dev-skills/" in lines
    return {
        "directory": "present" if (repo / ".dev-skills").exists() else "missing",
        "gitignore_entry": "present" if ignored else "missing",
    }


def _assert_redacted(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    forbidden = ("/home/", "/Users/", "/mnt/", "op://", "AKIA", "ghp_")
    leaked = [token for token in forbidden if token in serialized]
    if re.search(r"\bsk-[A-Za-z0-9]{8,}", serialized):
        leaked.append("sk-*")
    if leaked:
        raise ValueError(f"install status contains forbidden metadata token: {leaked[0]}")
