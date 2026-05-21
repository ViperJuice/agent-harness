from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ROADMAP_LEDGER_SCHEMA_VERSION = 2
PHASE_HEADING_RE = re.compile(r"^###\s+Phase\s+\d+\s+.*?\(([A-Z][A-Z0-9._-]*)\)[ \t]*(?:\S[^\n]*)?$", re.MULTILINE)


@dataclass(frozen=True)
class PhaseProvenance:
    alias: str
    roadmap_sha256: str
    phase_sha256: str


def roadmap_sha256(roadmap: Path) -> str:
    return hashlib.sha256(roadmap.read_bytes()).hexdigest()


def phase_blocks(roadmap: Path) -> dict[str, str]:
    text = roadmap.read_text()
    matches = list(PHASE_HEADING_RE.finditer(text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        alias = match.group(1).strip().upper()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks[alias] = text[match.start() : end]
    return blocks


def phase_sha256(roadmap: Path, phase: str) -> str | None:
    block = phase_blocks(roadmap).get(phase.upper())
    if block is None:
        return None
    return hashlib.sha256(block.encode()).hexdigest()


def phase_provenance_map(roadmap: Path) -> dict[str, str]:
    return {alias: hashlib.sha256(block.encode()).hexdigest() for alias, block in phase_blocks(roadmap).items()}


def event_provenance(roadmap: Path, phase: str) -> dict[str, object]:
    return {
        "schema_version": ROADMAP_LEDGER_SCHEMA_VERSION,
        "roadmap_sha256": roadmap_sha256(roadmap),
        "phase_sha256": phase_sha256(roadmap, phase),
    }


def snapshot_provenance(roadmap: Path) -> dict[str, object]:
    return {
        "schema_version": ROADMAP_LEDGER_SCHEMA_VERSION,
        "roadmap_sha256": roadmap_sha256(roadmap),
        "phase_sha256": phase_provenance_map(roadmap),
    }


def status_provenance_matches(
    status: str,
    entry_roadmap_sha: str | None,
    entry_phase_sha: str | None,
    current_roadmap_sha: str | None,
    current_phase_sha: str | None,
) -> bool:
    if not entry_phase_sha or not current_phase_sha:
        return False
    if status in {"complete", "executed", "awaiting_phase_closeout", "blocked", "unknown"}:
        return entry_phase_sha == current_phase_sha
    if status in {"planned", "executing"}:
        return entry_phase_sha == current_phase_sha and entry_roadmap_sha == current_roadmap_sha
    return False


def provenance_mismatch_reason(
    status: str,
    entry_roadmap_sha: str | None,
    entry_phase_sha: str | None,
    current_roadmap_sha: str | None,
    current_phase_sha: str | None,
) -> str:
    if not entry_roadmap_sha or not entry_phase_sha:
        return "legacy"
    if not current_phase_sha:
        return "phase_missing"
    if entry_phase_sha != current_phase_sha:
        return "phase_mismatch"
    if status in {"planned", "executing"} and entry_roadmap_sha != current_roadmap_sha:
        return "roadmap_mismatch"
    return "unknown"
