"""Stale-text scanner (docs-freshness v4 P2) — the anti-gaming content layer.

A pure, side-effect-free scanner the docs-audit (P1, Layer A) and the in-pipeline
closeout gate (P3, Layer B) both call. P1's decision contract proves a doc was
*touched*; this proves the touched text isn't *stale* — closing the gap a green
release can otherwise sail through (the `#18` "recovery commit pending" class).

It flags three families inside changed durable-doc text:
  1. **placeholders** — `pending`, `TBD`, `recovery commit pending`, `coming soon`
     (always-on; needs no external state — the core #18 win).
  2. **stale package-count phrasing** — `publishes three` / `N packages` that
     disagrees with the actual manifest package set (enabled by passing
     `expected_package_count`).
  3. **old release versions not labeled historical** — a version mention older than
     `current_version` without a `(historical)` / `(superseded)` label nearby
     (enabled by passing `current_version`).

`scan_text` is pure (text in, findings out). `scan_doc_paths` is the thin file-reading
boundary the audit uses; the core stays reusable and test-only-needs-strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

# Word-number map so "publishes three" can be compared against an int package count.
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: A version like `1.0.5` or `v0.1.4` (captured for comparison).
_VERSION_RE = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?)\b")


@dataclass(frozen=True)
class StaleScanConfig:
    """Configurable patterns + the historical-label exemption (all overridable)."""

    # Specific template/unfilled markers — NOT bare English words. Calibrated after
    # dogfooding flagged legitimate prose ("the result is pending", "a placeholder for")
    # and CHANGELOG history. `pending` only matches the release-evidence compound forms.
    placeholder_patterns: tuple[str, ...] = (
        r"recovery commit pending",
        r"\b(?:commit|sha|tag|result|recovery|release)\s+pending\b",
        r"\bpending\s+(?:commit|sha|tag|fill|backfill)\b",
        r"\bTBD\b",
        r"\bTODO\b",
        r"\bFIXME\b",
        r"\bXXX\b",
        r"coming soon",
    )
    #: A version mention is exempt from the old-version check if one of these labels
    #: appears within `historical_window` chars after it.
    historical_labels: tuple[str, ...] = (
        "historical", "superseded", "deprecated", "archived", "legacy", "previous",
    )
    historical_window: int = 60
    #: Phrases that assert a package count, e.g. "publishes three", "3 packages".
    package_count_patterns: tuple[str, ...] = (
        r"publish(?:es|ed)?\s+(\w+)\s+packages?",
        r"\b(\w+)\s+packages?\b",
    )
    case_insensitive: bool = True


DEFAULT_CONFIG = StaleScanConfig()


@dataclass(frozen=True)
class StaleFinding:
    code: str          # "placeholder" | "stale_package_count" | "unlabeled_old_version"
    line: int          # 1-based
    text: str          # the offending snippet
    detail: str = ""

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "line": self.line, "text": self.text, "detail": self.detail}


def _flags(config: StaleScanConfig) -> int:
    return re.IGNORECASE if config.case_insensitive else 0


def _historical_nearby(text: str, end: int, config: StaleScanConfig) -> bool:
    window = text[end : end + config.historical_window].lower()
    return any(label in window for label in config.historical_labels)


def _in_code_span(line: str, pos: int) -> bool:
    """True when ``pos`` falls inside a backtick code span on ``line``.

    A match that is *documenting* a placeholder (e.g. ``placeholders like
    `recovery commit pending` ``) is an example, not a real stale marker.
    """
    return line.count("`", 0, pos) % 2 == 1


def _is_changelog_doc(rel_path: str) -> bool:
    """A CHANGELOG / RELEASE notes file IS the historical version record — its old
    version entries are expected, so the unlabeled-old-version check does not apply."""
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1].upper()
    return name.startswith("CHANGELOG") or name.startswith("RELEASE") or "CHANGELOG" in name


def scan_text(
    text: str,
    *,
    config: StaleScanConfig = DEFAULT_CONFIG,
    expected_package_count: int | None = None,
    current_version: str | None = None,
) -> list[StaleFinding]:
    """Scan a doc body for stale text. Pure: same inputs → same findings.

    `expected_package_count` enables the stale-count check; `current_version` enables
    the old-unlabeled-version check. Placeholders are always scanned.
    """
    findings: list[StaleFinding] = []
    lines = text.splitlines()
    flags = _flags(config)

    # 1. placeholders (always on); skip example mentions inside `code spans`.
    for pat in config.placeholder_patterns:
        rx = re.compile(pat, flags)
        for i, line in enumerate(lines, start=1):
            for m in rx.finditer(line):
                if _in_code_span(line, m.start()):
                    continue
                findings.append(StaleFinding("placeholder", i, m.group(0), detail=f"matched /{pat}/"))

    # 2. stale package-count phrasing (opt-in via expected_package_count)
    if expected_package_count is not None:
        for pat in config.package_count_patterns:
            rx = re.compile(pat, flags)
            for i, line in enumerate(lines, start=1):
                for m in rx.finditer(line):
                    if _in_code_span(line, m.start()):
                        continue
                    raw = m.group(1).lower()
                    n = _WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else None)
                    if n is not None and n != expected_package_count:
                        findings.append(StaleFinding(
                            "stale_package_count", i, m.group(0),
                            detail=f"says {n}, manifest has {expected_package_count}",
                        ))

    # 3. old release versions not labeled historical (opt-in via current_version)
    if current_version is not None:
        cur = _version_tuple(current_version)
        for i, line in enumerate(lines, start=1):
            for m in _VERSION_RE.finditer(line):
                if _in_code_span(line, m.start()):
                    continue  # `confidence: 0.0`, code samples — not a version claim
                ver = m.group(1)
                if _version_tuple(ver) < cur and not _historical_nearby(line, m.end(), config):
                    findings.append(StaleFinding(
                        "unlabeled_old_version", i, m.group(0),
                        detail=f"older than current {current_version}, not labeled historical",
                    ))
    return findings


def _version_tuple(v: str) -> tuple[int, ...]:
    m = _VERSION_RE.search(v)
    core = m.group(1) if m else v
    out: list[int] = []
    for part in core.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def scan_doc_paths(
    repo: Path,
    doc_paths: Sequence[str],
    *,
    config: StaleScanConfig = DEFAULT_CONFIG,
    expected_package_count: int | None = None,
    current_version: str | None = None,
) -> dict[str, list[StaleFinding]]:
    """File-reading boundary: scan each doc path's current content in `repo`.

    Returns `{path: [findings]}` for paths with hits. Unreadable files are skipped
    (the surface/decision contract — P1 — already governs presence; this is content).
    """
    out: dict[str, list[StaleFinding]] = {}
    for rel in doc_paths:
        fp = repo / rel
        try:
            body = fp.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        # A CHANGELOG/RELEASE file is the historical version record — its old version
        # entries are expected, so the unlabeled-old-version check does not apply there.
        effective_version = None if _is_changelog_doc(rel) else current_version
        hits = scan_text(
            body,
            config=config,
            expected_package_count=expected_package_count,
            current_version=effective_version,
        )
        if hits:
            out[rel] = hits
    return out


def with_patterns(extra_placeholders: Sequence[str] = (), **overrides: object) -> StaleScanConfig:
    """Convenience: derive a config with extra placeholder patterns / field overrides."""
    base = DEFAULT_CONFIG
    if extra_placeholders:
        base = replace(base, placeholder_patterns=base.placeholder_patterns + tuple(extra_placeholders))
    return replace(base, **overrides) if overrides else base
