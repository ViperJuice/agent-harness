"""Operator-callable evidence audit.

Spot-checks dirty-tree artifacts for the fake-evidence patterns surfaced
in the regen VISUALMATCH 2026-05-22 incident:

1. duplicate-content — multiple cited files share the same sha256
   (e.g., "19 prototype PNGs" all having md5 8d7f1750)
2. uniform-numeric — numeric arrays > 3 elements where all values are
   within epsilon (e.g., 19/19 similarity values at 0.999999)
3. missing-references — JSON artifacts cite path-shaped strings that
   don't exist on disk

This is a Tier 1.5 helper: codifies the operator spot-check protocol
that catches evidence faking the v20 IF-gate Tier 1 validator (which
only matches names, not content) can't see. Full Tier 2 evidence
verification with runner enforcement is deferred to a future roadmap.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# Heuristic-shaped string that might be a file path. Triggers on slashes;
# we filter out URLs and obviously non-path strings downstream.
_PATH_HINT_RE = re.compile(r"^[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,8}$")
# Skip when the "string" is actually a URL or known non-path
_NON_PATH_PREFIXES = ("http://", "https://", "git@", "ssh://", "file://")


@dataclass(frozen=True)
class DuplicateContentFinding:
    sha256: str
    paths: tuple[str, ...]
    size_bytes: int


@dataclass(frozen=True)
class UniformNumericFinding:
    json_artifact: str
    json_pointer: str  # e.g., "$.routes[*].similarity"
    array_length: int
    unique_values: int
    sample_value: float


@dataclass(frozen=True)
class MissingReferenceFinding:
    json_artifact: str
    json_pointer: str
    missing_path: str


@dataclass
class EvidenceAuditResult:
    repo: str
    files_scanned: int = 0
    json_artifacts_scanned: int = 0
    duplicate_content: list[DuplicateContentFinding] = field(default_factory=list)
    uniform_numeric: list[UniformNumericFinding] = field(default_factory=list)
    missing_references: list[MissingReferenceFinding] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (self.duplicate_content or self.uniform_numeric or self.missing_references)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "files_scanned": self.files_scanned,
            "json_artifacts_scanned": self.json_artifacts_scanned,
            "is_clean": self.is_clean(),
            "duplicate_content": [
                {"sha256": f.sha256, "paths": list(f.paths), "size_bytes": f.size_bytes}
                for f in self.duplicate_content
            ],
            "uniform_numeric": [
                {
                    "json_artifact": f.json_artifact,
                    "json_pointer": f.json_pointer,
                    "array_length": f.array_length,
                    "unique_values": f.unique_values,
                    "sample_value": f.sample_value,
                }
                for f in self.uniform_numeric
            ],
            "missing_references": [
                {"json_artifact": f.json_artifact, "json_pointer": f.json_pointer, "missing_path": f.missing_path}
                for f in self.missing_references
            ],
        }


def _git_dirty_paths(repo: Path) -> list[str]:
    # --untracked-files=all so untracked directories are expanded to
    # individual file entries rather than collapsed to "dir/" — otherwise
    # we miss the actual files-in-untracked-dir case.
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        # First 2 chars are status; rest is path (possibly with -> for renames)
        path = line[3:]
        if "->" in path:
            path = path.split("->", 1)[1].strip()
        paths.append(path.strip().strip('"'))
    return paths


def _sha256_of_file(path: Path, max_bytes: int = 50 * 1024 * 1024) -> tuple[str, int] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        return None
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256()
            h.update(f.read())
        return h.hexdigest(), size
    except OSError:
        return None


def detect_duplicate_content(
    files: Iterable[Path], min_duplicates: int = 3
) -> list[DuplicateContentFinding]:
    """Flag when N or more files share the same sha256.

    min_duplicates default of 3 is intentional — the regen incident had
    19 identical files; a value of 2 would false-positive on legitimate
    duplicates (e.g., template files copied verbatim). The pattern we
    want to catch is "many supposedly-distinct artifacts all the same."
    """
    by_hash: dict[str, list[tuple[str, int]]] = {}
    for p in files:
        if not p.is_file():
            continue
        h = _sha256_of_file(p)
        if h is None:
            continue
        sha, size = h
        by_hash.setdefault(sha, []).append((str(p), size))
    findings: list[DuplicateContentFinding] = []
    for sha, entries in by_hash.items():
        if len(entries) < min_duplicates:
            continue
        findings.append(
            DuplicateContentFinding(
                sha256=sha,
                paths=tuple(p for p, _ in entries),
                size_bytes=entries[0][1],
            )
        )
    return findings


def _walk_json(obj: Any, pointer: str = "$") -> Iterable[tuple[str, Any]]:
    yield pointer, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_json(v, f"{pointer}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_json(v, f"{pointer}[{i}]")


def detect_uniform_numeric(
    json_path: Path, min_array_length: int = 4, epsilon: float = 1e-6
) -> list[UniformNumericFinding]:
    """Flag numeric arrays where all values are within epsilon of each other.

    Catches the regen pattern: 19/19 similarity scores at exactly 0.999999.
    Real comparison output has natural variance; uniform-to-6-decimals across
    a >=4-element array is a strong template-fill signal.

    Default min_array_length=4 because legitimate 2-3 element arrays often
    DO have identical values (e.g., [true, true] or [0, 0, 0] for a 3-axis
    score). 4+ identical values is suspicious.
    """
    try:
        text = json_path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    findings: list[UniformNumericFinding] = []
    # Collect all numeric arrays we encounter directly
    for pointer, value in _walk_json(data):
        if not isinstance(value, list):
            continue
        numerics = [x for x in value if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if len(numerics) != len(value):
            # mixed-type arrays don't count
            continue
        if len(numerics) < min_array_length:
            continue
        unique = {round(x / epsilon) for x in numerics}
        if len(unique) == 1:
            findings.append(
                UniformNumericFinding(
                    json_artifact=str(json_path),
                    json_pointer=pointer,
                    array_length=len(numerics),
                    unique_values=1,
                    sample_value=float(numerics[0]),
                )
            )
            continue
        # Also catch the "all entries in an array of objects have identical numeric field"
        # pattern: iterate first-level-object-array → extract each object's numeric fields →
        # check uniformity. This catches "every entry has similarity=0.999999".
    # Second pass: object arrays where every object shares an identical numeric field
    for pointer, value in _walk_json(data):
        if not isinstance(value, list) or len(value) < min_array_length:
            continue
        if not all(isinstance(item, dict) for item in value):
            continue
        # For each common numeric field, check uniformity
        common_keys = set(value[0].keys())
        for item in value[1:]:
            common_keys &= set(item.keys())
        for key in common_keys:
            field_values = [item.get(key) for item in value]
            numerics = [v for v in field_values if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if len(numerics) != len(field_values):
                continue
            unique = {round(x / epsilon) for x in numerics}
            if len(unique) == 1:
                findings.append(
                    UniformNumericFinding(
                        json_artifact=str(json_path),
                        json_pointer=f"{pointer}[*].{key}",
                        array_length=len(numerics),
                        unique_values=1,
                        sample_value=float(numerics[0]),
                    )
                )
    return findings


def detect_missing_references(
    json_path: Path, repo: Path
) -> list[MissingReferenceFinding]:
    """Flag JSON string values that look like paths but don't resolve on disk.

    Catches the pattern where artifacts cite paths that were never created
    (a planted manifest with no actual files behind it).
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    findings: list[MissingReferenceFinding] = []
    for pointer, value in _walk_json(data):
        if not isinstance(value, str) or not value:
            continue
        if any(value.startswith(prefix) for prefix in _NON_PATH_PREFIXES):
            continue
        if not _PATH_HINT_RE.match(value):
            continue
        if "/" not in value:
            continue
        # Resolve relative to repo
        candidate = (repo / value).resolve() if not Path(value).is_absolute() else Path(value)
        if not candidate.exists():
            findings.append(
                MissingReferenceFinding(
                    json_artifact=str(json_path),
                    json_pointer=pointer,
                    missing_path=value,
                )
            )
    return findings


def run_evidence_audit(
    repo: Path,
    *,
    dirty_only: bool = True,
    min_duplicates: int = 3,
    uniform_epsilon: float = 1e-6,
    uniform_min_length: int = 4,
) -> EvidenceAuditResult:
    """Run all three detectors against the repo's dirty (or full) tree.

    dirty_only=True scopes the audit to currently-modified/untracked paths,
    which is the typical pre-reconcile use case. dirty_only=False audits
    every tracked file (slower; useful for forensic sweeps).
    """
    repo = repo.expanduser().resolve()
    result = EvidenceAuditResult(repo=str(repo))

    if dirty_only:
        rels = _git_dirty_paths(repo)
        files = [repo / p for p in rels if (repo / p).is_file()]
    else:
        files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]

    result.files_scanned = len(files)

    # Duplicate-content detector
    result.duplicate_content = detect_duplicate_content(files, min_duplicates=min_duplicates)

    # Per-JSON-artifact detectors
    json_artifacts = [p for p in files if p.suffix == ".json"]
    result.json_artifacts_scanned = len(json_artifacts)
    for jp in json_artifacts:
        result.uniform_numeric.extend(
            detect_uniform_numeric(jp, min_array_length=uniform_min_length, epsilon=uniform_epsilon)
        )
        result.missing_references.extend(detect_missing_references(jp, repo))

    return result


def render_text(result: EvidenceAuditResult) -> str:
    """Human-readable rendering for the CLI."""
    lines = [
        f"evidence-audit: {result.repo}",
        f"  files scanned:           {result.files_scanned}",
        f"  json artifacts scanned:  {result.json_artifacts_scanned}",
        f"  duplicate-content findings:  {len(result.duplicate_content)}",
        f"  uniform-numeric findings:    {len(result.uniform_numeric)}",
        f"  missing-references findings: {len(result.missing_references)}",
    ]
    if result.is_clean():
        lines.append("")
        lines.append("CLEAN — no fake-evidence patterns detected.")
        return "\n".join(lines)
    lines.append("")
    lines.append("SUSPECT — review before reconciling:")
    for f in result.duplicate_content:
        lines.append(
            f"  duplicate-content (sha256={f.sha256[:12]}, size={f.size_bytes}B): {len(f.paths)} files share this hash"
        )
        for p in f.paths[:5]:
            lines.append(f"    {p}")
        if len(f.paths) > 5:
            lines.append(f"    ...and {len(f.paths) - 5} more")
    for f in result.uniform_numeric:
        lines.append(
            f"  uniform-numeric: {f.json_artifact} {f.json_pointer} — "
            f"{f.array_length} entries all = {f.sample_value!r}"
        )
    for f in result.missing_references:
        lines.append(
            f"  missing-reference: {f.json_artifact} {f.json_pointer} -> {f.missing_path!r}"
        )
    return "\n".join(lines)
