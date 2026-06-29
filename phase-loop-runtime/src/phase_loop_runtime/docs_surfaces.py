"""Canonical public/release surface taxonomy (docs-freshness v4 P1, IF-0-P1-1).

Single source of truth for "what is a public surface a change might need docs
for." `models.PUBLIC_SURFACE_GLOBS` and `release_guard.RELEASE_AFFECTING_PATTERNS`
re-export from HERE, so there is exactly one definition (no third parallel copy —
the drift the v4 roadmap's panel reconciliation insisted on avoiding).

Two tiers:
  - RELEASE_AFFECTING_PATTERNS — release-class (version/manifest/install-posture/
    release-workflow). A change here is held to the strict, relevance-bound
    decision contract.
  - GENERAL_PUBLIC_GLOBS — general user-visible surfaces. A change here must carry
    at least a *recorded* decision (closing the silent-absence leak).

`RELEASE_DOC_REQUIREMENTS` is the relevance binding (anti-rubber-stamp): a changed
release surface is only satisfied when its *required* doc surface(s) also changed,
so a README whitespace edit cannot stand in for a missing CHANGELOG entry.

This module depends on stdlib only (no intra-package imports) to stay cycle-free.
"""
from __future__ import annotations

import fnmatch
from typing import Iterable

# Release-class surfaces: a change here affects what/how the project ships.
RELEASE_AFFECTING_PATTERNS = (
    ".github/workflows/**",
    "CHANGELOG*",
    "RELEASE*",
    "VERSION",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "poetry.lock",
    "requirements*.txt",
    "**/package.json",
    "docs/release/**",
    "docs/releases/**",
    "docs/release*.md",
    "scripts/*release*",
    "scripts/download-release.py",
)

# General public surfaces (user-visible; docs may track). Mirrors the historical
# models.PUBLIC_SURFACE_GLOBS set.
GENERAL_PUBLIC_GLOBS = (
    "**/cli.py",
    "**/*.proto",
    "**/openapi*.json",
    "**/openapi*.yaml",
    "**/*.openapi.*",
    "**/schema*.json",
    "README.md",
    "CHANGELOG.md",
    "**/_contract_docs/**",
)

# Durable doc surfaces — what a "doc change" can be.
DOC_SURFACE_GLOBS = (
    "README.md",
    "**/README.md",
    "CHANGELOG.md",
    "**/CHANGELOG*",
    "RELEASE*",
    "docs/**",
    "**/_contract_docs/**",
)

# Relevance binding (IF-0-P1-1): a changed release surface requires its mapped doc
# surface(s) to ALSO have changed. (release-surface pattern -> required doc patterns)
RELEASE_DOC_REQUIREMENTS = (
    ("VERSION", ("CHANGELOG*",)),
    ("pyproject.toml", ("CHANGELOG*",)),
    ("setup.cfg", ("CHANGELOG*",)),
    ("setup.py", ("CHANGELOG*",)),
    ("uv.lock", ("CHANGELOG*",)),
    ("poetry.lock", ("CHANGELOG*",)),
    ("requirements*.txt", ("CHANGELOG*",)),
    ("**/package.json", ("CHANGELOG*", "**/README.md")),
    (".github/workflows/**", ("CHANGELOG*", "docs/release*")),
    ("scripts/*release*", ("CHANGELOG*", "docs/release*")),
)


def _match(path: str, pattern: str) -> bool:
    """fnmatch with the historical `**/` prefix special-case (matches at root too)."""
    p = path.replace("\\", "/")
    if fnmatch.fnmatchcase(p, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(p, pattern[3:]):
        return True
    return False


def _any(path: str, patterns: Iterable[str]) -> bool:
    return any(_match(path, pat) for pat in patterns)


def is_release_surface(path: str) -> bool:
    return _any(path, RELEASE_AFFECTING_PATTERNS)


def is_general_public_surface(path: str) -> bool:
    return _any(path, GENERAL_PUBLIC_GLOBS)


def is_doc_surface(path: str) -> bool:
    return _any(path, DOC_SURFACE_GLOBS)


def classify_surface(path: str) -> str | None:
    """`"release"` | `"general"` | None. Release-class wins (it's the stricter tier)."""
    if is_release_surface(path):
        return "release"
    if is_general_public_surface(path):
        return "general"
    return None


def required_docs_for(path: str) -> tuple[str, ...]:
    """The doc surface pattern(s) a changed release surface must be matched by."""
    reqs: list[str] = []
    for pat, docs in RELEASE_DOC_REQUIREMENTS:
        if _match(path, pat):
            reqs.extend(docs)
    return tuple(dict.fromkeys(reqs))
