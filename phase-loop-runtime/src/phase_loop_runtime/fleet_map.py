"""CS-0.7 realized-edge / fleet-map v0 extractor.

Extracts the **realized** cross-repo interface graph from a set of repo paths —
deliberately NOT a package-lockfile dependency scan. The Consiliency
standardization roadmap's verified premise is that between the core repos
there are ~zero package-level deps; the real cross-repo edges are git+ref
pins, copied-literal (vendored) contract/schema drift, and hard-coded
host-path references in source. A lockfile-only scan renders those edges
invisible (see `run_lockfile_baseline_scan`, and
`test_lockfile_baseline_scan_is_empty_over_the_same_fixture_tree` in
tests/test_fleet_map.py, which proves the point over a fixture tree that
also carries ordinary, unrelated third-party manifest deps).

Three edge kinds (`EDGE_KINDS`):
  - pin: a `*/tools/agent-harness.pin.json` / `*greenfield-contract.lock.json`
    file, or a `git+<scheme>://...#subdirectory=...`-style ref inside a
    dotfiles/bootstrap.sh-style script.
  - copied-literal: a same-named contract/schema file vendored into two or
    more repos whose contents hash-diverge (drift).
  - host-path: a hard-coded sibling path like `/home/<user>/code/<repo>/...`
    found in source.

Every edge carries a `maturity_label` (`MATURITY_LABELS`) grading how strong
the evidence is: `presence-only` (the artifact exists but the target repo
could not be confirmed among the scanned repos), `hash-checked` (a content
hash comparison produced the finding), or `realized-edge-observed` (the
target repo is one of the scanned repos, so the edge is concretely
resolved).

This module is v0: no network calls, no git remote resolution — everything is
static file inspection over the repo paths the caller supplies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import itertools
import json
from pathlib import Path
import re
from typing import Any, Iterable


EDGE_KINDS = ("pin", "copied-literal", "host-path", "lockfile-dep")
MATURITY_LABELS = ("presence-only", "hash-checked", "realized-edge-observed")

# Directories skipped everywhere a repo tree is walked — VCS internals and
# dependency/build caches carry no realized fleet-map evidence and are large.
_SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}

_SOURCE_EXTENSIONS = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".sh", ".mts", ".cts"}
_CONTRACT_EXTENSIONS = {".json", ".yaml", ".yml"}

_HOST_PATH_RE = re.compile(r"/home/[\w.\-]+/code/([\w.\-]+)")
_GIT_PIN_RE = re.compile(r"git\+[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'#]+(?:#subdirectory=[^\s\"']+)?")

_LOCKFILE_MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "requirements.txt",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
}


@dataclass(frozen=True)
class FleetEdge:
    from_repo: str
    to_repo: str
    kind: str
    evidence: str
    maturity_label: str

    def key(self) -> tuple[str, str, str, str]:
        return (self.from_repo, self.to_repo, self.kind, self.evidence)

    def to_json(self) -> dict[str, Any]:
        return {
            "from_repo": self.from_repo,
            "to_repo": self.to_repo,
            "kind": self.kind,
            "evidence": self.evidence,
            "maturity_label": self.maturity_label,
        }


@dataclass(frozen=True)
class SetupDiagnostic:
    repo: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"repo": self.repo, "message": self.message}


@dataclass(frozen=True)
class FleetMapResult:
    repos: tuple[str, ...]
    edges: tuple[FleetEdge, ...] = field(default_factory=tuple)
    lockfile_baseline_edges: tuple[FleetEdge, ...] = field(default_factory=tuple)
    setup_diagnostics: tuple[SetupDiagnostic, ...] = field(default_factory=tuple)

    def has_setup_errors(self) -> bool:
        return bool(self.setup_diagnostics)

    def to_json(self) -> dict[str, Any]:
        return {
            "repos": list(self.repos),
            "counts": {
                "repos": len(self.repos),
                "edges": len(self.edges),
                "lockfile_baseline_edges": len(self.lockfile_baseline_edges),
                "setup_errors": len(self.setup_diagnostics),
            },
            "edges": [edge.to_json() for edge in self.edges],
            "lockfile_baseline_edges": [edge.to_json() for edge in self.lockfile_baseline_edges],
            "setup_diagnostics": [item.to_json() for item in self.setup_diagnostics],
        }

    def render_text(self) -> str:
        lines = ["Fleet Map (realized cross-repo interface graph)", f"repos: {', '.join(self.repos)}", ""]
        if self.setup_diagnostics:
            lines.append("Setup diagnostics:")
            for diagnostic in self.setup_diagnostics:
                lines.append(f"  {diagnostic.repo}: {diagnostic.message}")
            lines.append("")
        if not self.edges:
            lines.append("Edges: none")
        else:
            lines.append("Edges:")
            for edge in self.edges:
                lines.append(f"  [{edge.kind}] {edge.from_repo} -> {edge.to_repo} ({edge.maturity_label}) {edge.evidence}")
        lines.append("")
        lines.append(f"Lockfile-only baseline: {len(self.lockfile_baseline_edges)} edge(s)")
        return "\n".join(lines)


def build_fleet_map(repos: Iterable[str | Path]) -> FleetMapResult:
    """Extract the realized-edge fleet map over `repos` (see module docstring)."""
    resolved, repo_names, setup_diagnostics = _resolve_repos(repos)

    edges: list[FleetEdge] = []
    for repo_name, repo_path in resolved.items():
        edges.extend(_scan_pin_edges(repo_path, repo_name, repo_names))
        edges.extend(_scan_host_path_edges(repo_path, repo_name, repo_names))
    edges.extend(_scan_copied_literal_edges(resolved))

    baseline = _run_lockfile_baseline(resolved, repo_names)

    return FleetMapResult(
        repos=tuple(sorted(repo_names)),
        edges=_dedupe_and_sort(edges),
        lockfile_baseline_edges=_dedupe_and_sort(baseline),
        setup_diagnostics=tuple(sorted(setup_diagnostics, key=lambda item: (item.repo, item.message))),
    )


def run_lockfile_baseline_scan(repos: Iterable[str | Path]) -> tuple[FleetEdge, ...]:
    """Package-lockfile-only baseline: scans manifests for cross-repo git deps.

    This is the counterfactual the CS-0.7 premise argues against: run this
    over the same fixture tree `build_fleet_map` finds three edges in, and it
    comes back empty, even though the fixture repos carry ordinary
    (non-cross-repo) third-party manifest deps — the emptiness is not because
    nothing was scanned, but because package-level deps aren't how these
    repos are actually wired together.
    """
    resolved, repo_names, _ = _resolve_repos(repos)
    return _dedupe_and_sort(_run_lockfile_baseline(resolved, repo_names))


# --------------------------------------------------------------------------
# repo resolution
# --------------------------------------------------------------------------


def _resolve_repos(repos: Iterable[str | Path]) -> tuple[dict[str, Path], set[str], list[SetupDiagnostic]]:
    resolved: dict[str, Path] = {}
    diagnostics: list[SetupDiagnostic] = []
    for repo_input in repos:
        repo = Path(repo_input).expanduser().resolve()
        label = str(repo)
        if not repo.exists() or not repo.is_dir():
            diagnostics.append(SetupDiagnostic(repo=label, message="repo path is missing or not a directory"))
            continue
        resolved[repo.name] = repo
    return resolved, set(resolved), diagnostics


def _iter_files(repo: Path) -> Iterable[Path]:
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if _SKIP_DIR_NAMES & set(path.relative_to(repo).parts[:-1]):
            continue
        yield path


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# pin edges
# --------------------------------------------------------------------------


def _is_pin_file(path: Path) -> bool:
    if path.name == "agent-harness.pin.json" and path.parent.name == "tools":
        return True
    return path.name.endswith("greenfield-contract.lock.json")


def _scan_pin_edges(repo: Path, repo_name: str, known_repos: set[str]) -> list[FleetEdge]:
    edges: list[FleetEdge] = []
    for path in _iter_files(repo):
        if path.suffix == ".json" and _is_pin_file(path):
            edges.extend(_scan_pin_json(path, repo, repo_name, known_repos))
        elif path.suffix in {".sh", ".bash"}:
            edges.extend(_scan_pin_script(path, repo, repo_name, known_repos))
    return edges


def _scan_pin_json(path: Path, repo: Path, repo_name: str, known_repos: set[str]) -> list[FleetEdge]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_repo = payload.get("repo") or payload.get("url") or payload.get("source")
    if not isinstance(raw_repo, str) or not raw_repo:
        return []
    to_repo = _last_path_segment(raw_repo)
    if not to_repo or to_repo == repo_name:
        return []
    return [
        FleetEdge(
            from_repo=repo_name,
            to_repo=to_repo,
            kind="pin",
            evidence=f"{_relative(path, repo)}:1",
            maturity_label="realized-edge-observed" if to_repo in known_repos else "presence-only",
        )
    ]


def _scan_pin_script(path: Path, repo: Path, repo_name: str, known_repos: set[str]) -> list[FleetEdge]:
    edges: list[FleetEdge] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line_number, line in enumerate(lines, start=1):
        for match in _GIT_PIN_RE.finditer(line):
            to_repo, _ref, _subdirectory = _parse_git_pin_url(match.group(0))
            if not to_repo or to_repo == repo_name:
                continue
            edges.append(
                FleetEdge(
                    from_repo=repo_name,
                    to_repo=to_repo,
                    kind="pin",
                    evidence=f"{_relative(path, repo)}:{line_number}",
                    maturity_label="realized-edge-observed" if to_repo in known_repos else "presence-only",
                )
            )
    return edges


def _parse_git_pin_url(raw: str) -> tuple[str | None, str | None, str | None]:
    subdirectory: str | None = None
    remainder = raw
    if "#subdirectory=" in remainder:
        remainder, _, subdirectory = remainder.partition("#subdirectory=")
    ref: str | None = None
    scheme_sep = remainder.find("://")
    url_part = remainder
    if scheme_sep != -1 and "@" in remainder[scheme_sep + 3 :]:
        url_part, _, ref = remainder.rpartition("@")
    path = url_part.split("://", 1)[-1]
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None, ref, subdirectory
    repo_name = segments[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[: -len(".git")]
    return repo_name or None, ref, subdirectory


def _last_path_segment(value: str) -> str | None:
    trimmed = value.strip().rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    segments = [segment for segment in re.split(r"[/:]", trimmed) if segment]
    return segments[-1] if segments else None


# --------------------------------------------------------------------------
# host-path edges
# --------------------------------------------------------------------------


def _scan_host_path_edges(repo: Path, repo_name: str, known_repos: set[str]) -> list[FleetEdge]:
    edges: list[FleetEdge] = []
    for path in _iter_files(repo):
        if path.suffix not in _SOURCE_EXTENSIONS:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            for match in _HOST_PATH_RE.finditer(line):
                to_repo = match.group(1)
                if to_repo == repo_name:
                    continue
                edges.append(
                    FleetEdge(
                        from_repo=repo_name,
                        to_repo=to_repo,
                        kind="host-path",
                        evidence=f"{_relative(path, repo)}:{line_number}",
                        maturity_label="realized-edge-observed" if to_repo in known_repos else "presence-only",
                    )
                )
    return edges


# --------------------------------------------------------------------------
# copied-literal (vendored contract/schema drift) edges
# --------------------------------------------------------------------------


def _is_contract_literal_candidate(path: Path) -> bool:
    if path.suffix not in _CONTRACT_EXTENSIONS:
        return False
    if _is_pin_file(path):
        return False
    return "contracts" in path.parts or "schemas" in path.parts or "contract" in path.stem or "schema" in path.stem


def _scan_copied_literal_edges(resolved: dict[str, Path]) -> list[FleetEdge]:
    by_basename: dict[str, list[tuple[str, Path]]] = {}
    for repo_name, repo_path in resolved.items():
        for path in _iter_files(repo_path):
            if _is_contract_literal_candidate(path):
                by_basename.setdefault(path.name, []).append((repo_name, path))

    edges: list[FleetEdge] = []
    for basename, occurrences in by_basename.items():
        if len(occurrences) < 2:
            continue
        for (repo_a, path_a), (repo_b, path_b) in itertools.combinations(occurrences, 2):
            if repo_a == repo_b:
                continue
            hash_a = _hash_file(path_a)
            hash_b = _hash_file(path_b)
            if hash_a is None or hash_b is None or hash_a == hash_b:
                continue
            from_repo, from_path = (repo_a, path_a) if repo_a < repo_b else (repo_b, path_b)
            to_repo = repo_b if from_repo == repo_a else repo_a
            edges.append(
                FleetEdge(
                    from_repo=from_repo,
                    to_repo=to_repo,
                    kind="copied-literal",
                    evidence=f"{_relative(from_path, resolved[from_repo])}:1",
                    maturity_label="hash-checked",
                )
            )
    return edges


def _hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# --------------------------------------------------------------------------
# lockfile-only baseline (the counterfactual)
# --------------------------------------------------------------------------


def _run_lockfile_baseline(resolved: dict[str, Path], known_repos: set[str]) -> list[FleetEdge]:
    edges: list[FleetEdge] = []
    for repo_name, repo_path in resolved.items():
        for path in _iter_files(repo_path):
            if path.name not in _LOCKFILE_MANIFEST_NAMES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in _GIT_PIN_RE.finditer(line):
                    to_repo, _ref, _subdirectory = _parse_git_pin_url(match.group(0))
                    if not to_repo or to_repo == repo_name or to_repo not in known_repos:
                        continue
                    edges.append(
                        FleetEdge(
                            from_repo=repo_name,
                            to_repo=to_repo,
                            kind="lockfile-dep",
                            evidence=f"{_relative(path, repo_path)}:{line_number}",
                            maturity_label="realized-edge-observed",
                        )
                    )
    return edges


def _dedupe_and_sort(edges: Iterable[FleetEdge]) -> tuple[FleetEdge, ...]:
    seen: dict[tuple[str, str, str, str], FleetEdge] = {}
    for edge in edges:
        seen[edge.key()] = edge
    return tuple(sorted(seen.values(), key=lambda edge: edge.key()))
