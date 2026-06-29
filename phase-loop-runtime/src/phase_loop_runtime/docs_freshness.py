"""Docs-freshness closeout gate (issue #18).

A phase-loop roadmap can close **green** — clean tree, pushed ``main``, release
workflow passed — while its public docs are stale or absent. The existing
``doc_delta_validator`` cannot catch this: it is *diff-keyed* (it fires only
when a public surface is already in ``changed_paths``), so files that *should*
have changed but didn't are doubly invisible. And under the default
``PHASE_LOOP_REVIEW=warn`` every registry finding is forced to ``warn`` and
never blocks (see ``closeout_validators``).

This module is the *path-keyed* complement: it enumerates public-doc surfaces
**from the filesystem** (and ``.claude/docs-catalog.json`` when present), scans
their *contents* for stale/placeholder tokens, and — for **release/package
phases only** — blocks ``complete`` independent of ``PHASE_LOOP_REVIEW``,
mirroring ``_apply_verification_evidence_gate`` (a hard, non-registry gate
gated on its own ``PHASE_LOOP_DOCS_FRESHNESS`` knob, default ``hard``).

Design seam (research open-question 1, option a): validators stay pure. The
**runner pre-scans** the repo via :func:`scan_docs_freshness` and threads the
resulting dict into ``build_phase_loop_closeout``; the gate consumes the dict
and never touches the filesystem from inside the closeout-validator contract.

Default-safe posture:

* Ordinary (non-release) phases are **unaffected** — the scan reports
  ``skipped`` and never blocks, preserving autonomy-first elsewhere.
* No pre-scan data (unwired call site) → ``skipped``, never blocked.
* False-positive control: only *unambiguous* placeholder tokens block; fuzzy
  signals (stale package-count claims, unlabeled versions) are warn-tier. An
  explicit ``<!-- freshness-ok -->`` marker on a line suppresses that line.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable, Mapping

# Enforcement knob — like PHASE_LOOP_VERIFY_ENFORCE, this gate has its OWN
# control so the hard block is independent of the global PHASE_LOOP_REVIEW
# warn-forcing default. "hard" (default) blocks release phases; "warn" records
# only; "off" disables the scan entirely.
DOCS_FRESHNESS_ENV = "PHASE_LOOP_DOCS_FRESHNESS"
DOCS_FRESHNESS_MODES: tuple[str, ...] = ("off", "warn", "hard")
DEFAULT_DOCS_FRESHNESS_MODE = "hard"

# An inline marker that suppresses freshness flagging for the line it appears on
# (operator escape hatch for legitimate uses of an otherwise-suspicious token).
FRESHNESS_OK_MARKER = "freshness-ok"

# Public-doc surfaces to enumerate from the filesystem. Kept aligned with
# models.PUBLIC_SURFACE_GLOBS' doc subset but extended to package-level READMEs
# and release notes — the exact surfaces issue #18 found stale.
PUBLIC_DOC_GLOBS: tuple[str, ...] = (
    "README.md",
    "**/README.md",
    "CHANGELOG.md",
    "CHANGELOG*",
    "**/CHANGELOG.md",
    "RELEASE_NOTES*",
    "RELEASES*",
    "docs/RELEASE*.md",
)

# Directories never worth scanning (vendored / generated / VCS).
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv"}
)

# Unambiguous placeholders — a release doc carrying one of these (outside an
# inline-code span) is stale by construction. Kept DELIBERATELY TIGHT: a hard
# gate that fires on a token that legitimately lives in shipped docs would block
# a real release fleet-wide (worse than the bug for normal operation). The
# issue's smoking gun was `recovery commit pending`. TODO/FIXME/XXX are common
# enough in real prose docs to be WARN-tier, not block.
BLOCK_TOKENS: tuple[str, ...] = (
    "recovery commit pending",
    "commit pending",
    "TBD",
    "<placeholder>",
)
# `pending` alone is too common in prose; only the qualified forms above block.
# Bare-word block tokens that need word-boundary care are handled in _line_hits.
_BARE_BLOCK_WORDS: tuple[str, ...] = ("TBD",)

# Fuzzy / advisory signals — likely-stale but false-positive-prone. WARN only.
WARN_TOKENS: tuple[str, ...] = (
    "publishes three",
    "publishes two",
    "is a skeleton",
    "still a skeleton",
    "TODO",
    "FIXME",
    "XXX",
)
# Warn-tier bare words needing word-boundary care (so "TODO" ≠ "METHODOLOGY").
_BARE_WARN_WORDS: tuple[str, ...] = ("TODO", "FIXME", "XXX")

# Release-artifact globs — if a phase's changed paths touch any of these, it is
# release/package-shaped. Used as the heuristic fallback when the plan carries
# no explicit release declaration.
RELEASE_ARTIFACT_GLOBS: tuple[str, ...] = (
    "**/package.json",
    "package.json",
    "CHANGELOG.md",
    "**/CHANGELOG.md",
    "RELEASE_NOTES*",
    "**/Cargo.toml",
    "**/pyproject.toml",
    "**/setup.cfg",
    "**/version.txt",
    "**/VERSION",
    "**/__version__.py",
    ".github/workflows/release*.yml",
    ".github/workflows/release*.yaml",
    ".github/workflows/publish*.yml",
)


def resolve_docs_freshness_mode(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = str(env.get(DOCS_FRESHNESS_ENV) or "").strip().lower()
    return value if value in DOCS_FRESHNESS_MODES else DEFAULT_DOCS_FRESHNESS_MODE


def _glob_match(path: str, patterns: Iterable[str]) -> bool:
    path = path.strip()
    if not path:
        return False
    for pattern in patterns:
        if fnmatchcase(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
            return True
    return False


def is_explicit_release_phase(
    plan_frontmatter: Mapping[str, Any] | None = None,
) -> bool:
    """True only when the plan *explicitly* declares itself a release phase.

    This is the **authoritative** release marker — ``phase_loop_mutation:
    release_dispatch`` (emitted by the plan-phase skill) or a release-shaped
    ``phase_type``. It is the ONLY signal eligible to drive the **hard block**
    in :func:`scan_docs_freshness`: hard-blocking an ordinary phase fleet-wide
    on a stale token in any public doc is a worse failure than the bug it
    catches, so the hard gate must be opt-in via frontmatter.

    The artifact-glob heuristic (see :func:`is_release_phase`) deliberately
    does NOT satisfy this predicate — it only governs whether the scan runs and
    can emit warn-tier evidence.
    """
    if not plan_frontmatter:
        return False
    mutation = str(plan_frontmatter.get("phase_loop_mutation") or "").strip().lower()
    if mutation == "release_dispatch":
        return True
    ptype = str(plan_frontmatter.get("phase_type") or "").strip().lower()
    return ptype in ("release", "package", "roadmap_completion", "roadmap-completion")


def is_release_phase(
    *,
    plan_frontmatter: Mapping[str, Any] | None = None,
    changed_paths: Iterable[str] = (),
) -> bool:
    """True if this phase is release/package/roadmap-completion shaped.

    Layered, default-safe signal used to decide **whether the freshness scan
    runs at all** (not whether it may hard-block — see
    :func:`is_explicit_release_phase` for that):

    1. Explicit: the plan frontmatter declares ``phase_loop_mutation:
       release_dispatch`` (the authoritative release marker the plan-phase skill
       already emits).
    2. Heuristic fallback: the phase's changed paths touch a release artifact
       (manifests / version files / changelog / release workflows). Keying
       release *detection* off changed paths is sound — release phases do bump
       versions — and is distinct from keying *freshness* off changed paths
       (which this module deliberately does NOT do).

    No signal → not a release phase → the gate is inert. A heuristic-only match
    (no explicit frontmatter) lets the scan run and record **warn-tier**
    evidence, but can NEVER produce a ``blocked`` status.
    """
    if is_explicit_release_phase(plan_frontmatter):
        return True
    return _glob_match_any(changed_paths, RELEASE_ARTIFACT_GLOBS)


def _glob_match_any(paths: Iterable[str], patterns: tuple[str, ...]) -> bool:
    return any(_glob_match(str(p), patterns) for p in paths)


def parse_plan_frontmatter(plan_path: str | Path | None) -> dict[str, str]:
    if not plan_path:
        return {}
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def _catalog_surfaces(repo: Path) -> list[str]:
    """Repo-relative doc paths declared in ``.claude/docs-catalog.json`` (if any).

    The catalog is a list of objects (``{"path": ...}``) or bare strings. We are
    permissive: anything that yields a path string is included. Absent/malformed
    catalog → empty list (filesystem glob enumeration is the fallback).
    """
    catalog = repo / ".claude" / "docs-catalog.json"
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    surfaces: list[str] = []
    items = data if isinstance(data, list) else data.get("docs", []) if isinstance(data, Mapping) else []
    for item in items or []:
        if isinstance(item, str):
            surfaces.append(item)
        elif isinstance(item, Mapping):
            path = item.get("path") or item.get("file")
            if isinstance(path, str):
                surfaces.append(path)
    return surfaces


def enumerate_public_docs(repo: Path) -> list[str]:
    """Path-keyed enumeration of public-doc surfaces (repo-relative, sorted).

    Filesystem walk matched against :data:`PUBLIC_DOC_GLOBS`, unioned with any
    surfaces declared in ``.claude/docs-catalog.json``. NOT keyed off
    ``changed_paths`` — that is the entire point (issue #18's blind spot).
    """
    found: set[str] = set()
    if repo.is_dir():
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            rel_dir = Path(dirpath).relative_to(repo)
            for name in filenames:
                rel = (rel_dir / name).as_posix()
                if _glob_match(rel, PUBLIC_DOC_GLOBS):
                    found.add(rel)
    for rel in _catalog_surfaces(repo):
        candidate = repo / rel
        if candidate.is_file():
            found.add(Path(rel).as_posix())
    return sorted(found)


@dataclass(frozen=True)
class DocsFreshnessHit:
    path: str
    line: int
    token: str
    severity: str  # "block" | "warn"
    excerpt: str

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "token": self.token,
            "severity": self.severity,
            "excerpt": self.excerpt,
        }


def _strip_inline_code(line: str) -> str:
    """Blank out inline-code spans (`...`) so a doc that *documents* a stale
    token (always backticked) is not flagged as itself being stale. Replaces
    span contents with spaces to preserve column alignment cheaply."""
    out: list[str] = []
    in_code = False
    for ch in line:
        if ch == "`":
            in_code = not in_code
            out.append(" ")
        else:
            out.append(" " if in_code else ch)
    return "".join(out)


def _line_hits(line: str) -> list[tuple[str, str]]:
    """Return (token, severity) hits in a single line. Empty if suppressed."""
    if FRESHNESS_OK_MARKER in line:
        return []
    # Tokens inside inline-code spans are documentation, not stale content.
    line = _strip_inline_code(line)
    hits: list[tuple[str, str]] = []
    lowered = line.lower()
    block_matched = False
    for token in BLOCK_TOKENS:
        if token in _BARE_BLOCK_WORDS:
            # Bare words: require them to stand as a token, not a substring
            # (avoid e.g. "TBD" inside a longer identifier). Word-boundary test.
            if _word_present(line, token):
                hits.append((token, "block"))
                block_matched = True
        elif token.lower() in lowered:
            hits.append((token, "block"))
            block_matched = True
    # Dedupe block substrings (e.g. "recovery commit pending" also contains
    # "commit pending") — one block hit per line is enough to gate.
    if block_matched:
        hits = [h for h in hits if h[1] == "block"][:1]
        return hits
    for token in WARN_TOKENS:
        if token in _BARE_WARN_WORDS:
            if _word_present(line, token):
                hits.append((token, "warn"))
        elif token.lower() in lowered:
            hits.append((token, "warn"))
    return hits


def _word_present(line: str, word: str) -> bool:
    import re

    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", line) is not None


def scan_docs_freshness(
    repo: str | Path | None,
    *,
    plan_path: str | Path | None = None,
    changed_paths: Iterable[str] = (),
    plan_frontmatter: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Runner-side pre-scan. Pure of closeout state; returns a JSON-able dict.

    Result shape (threaded into ``build_phase_loop_closeout`` as
    ``docs_freshness=...`` and surfaced in the closeout payload):

        {
          "status": "passed" | "skipped" | "blocked",
          "mode": "off" | "warn" | "hard",
          "is_release_phase": bool,
          "surfaces_scanned": [<repo-relative paths>],
          "hits": [DocsFreshnessHit, ...],
          "blocking_hits": [<subset that are severity=block>],
        }

    ``status``:
      * ``skipped`` — not a release phase, or mode ``off``, or no repo.
      * ``blocked`` — release phase, mode ``hard``, and ≥1 block-severity hit.
      * ``passed``  — release phase, scanned, no blocking hit (warn hits may
        still be recorded as evidence).
    """
    mode = resolve_docs_freshness_mode(env)
    fm = dict(plan_frontmatter or {})
    if not fm and plan_path:
        fm = parse_plan_frontmatter(plan_path)
    explicit = is_explicit_release_phase(fm)
    release = is_release_phase(plan_frontmatter=fm, changed_paths=changed_paths)

    base: dict[str, Any] = {
        "status": "skipped",
        "mode": mode,
        "is_release_phase": release,
        "explicit_release": explicit,
        "surfaces_scanned": [],
        "hits": [],
        "blocking_hits": [],
    }
    if mode == "off" or not release or not repo:
        return base
    repo_path = Path(repo)
    if not repo_path.is_dir():
        return base

    surfaces = enumerate_public_docs(repo_path)
    hits: list[DocsFreshnessHit] = []
    for rel in surfaces:
        try:
            text = (repo_path / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for token, severity in _line_hits(line):
                hits.append(
                    DocsFreshnessHit(
                        path=rel,
                        line=lineno,
                        token=token,
                        severity=severity,
                        excerpt=line.strip()[:200],
                    )
                )
    # The HARD block is opt-in via explicit release frontmatter. A heuristic-only
    # release phase (artifact-glob match, no frontmatter) scans and records
    # evidence, but block-severity hits are downgraded to warn so an ordinary
    # changelog/dep bump can NEVER hard-block the fleet.
    if not explicit:
        hits = [
            h if h.severity == "warn" else replace(h, severity="warn")
            for h in hits
        ]
    blocking = [h for h in hits if h.severity == "block"]
    # In "warn" mode the gate records but never blocks. Only explicit-release
    # phases in "hard" mode with a block-severity hit are blocked.
    if blocking and mode == "hard" and explicit:
        status = "blocked"
    else:
        status = "passed"
    return {
        "status": status,
        "mode": mode,
        "is_release_phase": release,
        "explicit_release": explicit,
        "surfaces_scanned": surfaces,
        "hits": [h.to_json() for h in hits],
        "blocking_hits": [h.to_json() for h in blocking],
    }
