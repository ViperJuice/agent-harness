from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ROADMAP_LEDGER_SCHEMA_VERSION = 2
# Phase headings accept integer phase numbers (Phase 1, Phase 2, ...) and
# decimal sub-phase numbers (Phase 2.1, Phase 3.5, ...). Decimal sub-phases
# are added post-hoc when an executor formalizes a recurring operator-fix
# pattern (e.g. v24's Phase 2.1 ADOPTBUNDLEREFRESH formalizing the
# adoption-bundle refresh that ran as operator-fix in v23 and v24).
# Without the (?:\.\d+)? group, decimal-phase aliases fail phase_sha256
# lookup → status_provenance_matches returns False → reducer rejects
# manual_repair/complete events → state stuck in stale state.
PHASE_HEADING_RE = re.compile(r"^###\s+Phase\s+\d+(?:\.\d+)?\s+.*?\(([A-Z][A-Z0-9._-]*)\)[ \t]*(?:\S[^\n]*)?$", re.MULTILINE)


@dataclass(frozen=True)
class PhaseProvenance:
    alias: str
    roadmap_sha256: str
    phase_sha256: str


@dataclass(frozen=True)
class ValidationFinding:
    line_number: int
    raw_text: str
    reason: str
    suggested_fix: str


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


def validate_roadmap_phase_headings(roadmap: Path) -> list[ValidationFinding]:
    text = roadmap.read_text()
    findings: list[ValidationFinding] = []
    seen_aliases: dict[str, int] = {}
    alias_re = re.compile(r"\(([^\s()]+)\)[ \t]*(?:\S[^\n]*)?$")
    valid_alias_re = re.compile(r"^[A-Z][A-Z0-9._-]*$")
    fix = (
        "Use `### Phase <number> <dash> Title (ALIAS)` where <dash> is either "
        "ASCII hyphen-minus `-` or em-dash `—` (both accepted) and alias matches "
        "[A-Z][A-Z0-9._-]*. The constraint is the alias-in-parens, not the dash style."
    )

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not re.match(r"^###\s+Phase\b", line):
            continue
        strict_match = PHASE_HEADING_RE.match(line)
        alias_match = alias_re.search(line)
        alias = alias_match.group(1) if alias_match else None
        normalized_alias = alias.upper() if alias else None

        if alias is not None and not valid_alias_re.fullmatch(alias):
            findings.append(
                ValidationFinding(
                    line_number=line_number,
                    raw_text=line,
                    reason=f"invalid-alias: alias {alias!r} does not match [A-Z][A-Z0-9._-]*",
                    suggested_fix=fix,
                )
            )
        elif strict_match is None:
            findings.append(
                ValidationFinding(
                    line_number=line_number,
                    raw_text=line,
                    reason="loose-match: heading starts with `### Phase` but did not yield a phase_sha256 entry",
                    suggested_fix=fix,
                )
            )

        if normalized_alias and valid_alias_re.fullmatch(normalized_alias):
            first_line = seen_aliases.get(normalized_alias)
            if first_line is None:
                seen_aliases[normalized_alias] = line_number
            else:
                findings.append(
                    ValidationFinding(
                        line_number=line_number,
                        raw_text=line,
                        reason=f"duplicate-alias: alias {normalized_alias} first appeared on line {first_line}",
                        suggested_fix="Give each roadmap phase a unique alias in the final parenthesized token.",
                    )
                )

    return findings


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
