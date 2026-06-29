#!/usr/bin/env python3
"""
validate_plan_doc.py — mechanical lint for phase-plan docs.

Usage:
    validate_plan_doc.py <plan-path>

Checks the plan doc produced by /claude-plan-phase before it's handed off to
/claude-execute-phase. Exit 0 on clean pass; non-zero with a human-readable
failure list on stderr.

Checks (in order; all run even if earlier ones fail so the author sees
every issue at once):

  (A) Required top-level headings present:
        # <anything>
        ## Context
        ## Interface Freeze Gates
        ## Spec Closeout Plan
        ## Lane Index & Dependencies
        ## Lanes
        ## Execution Notes
        ## Acceptance Criteria
        ## Verification
      Optional: ## Cross-Repo Gates

  (B) Lane Index stanzas parse cleanly: each SL-N has
        Depends on:    comma-separated SL-IDs or (none)
        Blocks:        comma-separated SL-IDs or (none)
        Parallel-safe: yes | no | mixed

  (C) DAG has no cycles (topological sort succeeds).

  (D) `Owned files` globs across ### SL-N sections are disjoint.
        If run inside a git repo, expand each glob via `git ls-files`
        and check for overlap. Otherwise fall back to textual prefix
        comparison — flag any exact glob duplication and warn about
        unresolvable cases.

  (E) Every `impl` task row in a lane's task table has a preceding
      `test` task in the same lane.

  (F) Every `Interfaces consumed` entry either appears in some upstream
      lane's `Interfaces provided` or is tagged "(pre-existing)".

  (I) `Spec Closeout Plan` declares `spec_delta_closeout.v1`, a valid decision,
      target surfaces, evidence paths, and `metadata_only` redaction.

Design: zero external deps (stdlib only). Parses markdown by regex on
stable headings produced by the claude-plan-phase template — not a full
Markdown parser, which would be overkill.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data model

@dataclass
class Lane:
    sl_id: str                           # e.g. "SL-1"
    name: str                            # free-text after —
    depends_on: List[str] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)
    parallel_safe: str = ""              # yes | no | mixed
    owned_globs: List[str] = field(default_factory=list)
    interfaces_provided: List[str] = field(default_factory=list)
    interfaces_consumed: List[str] = field(default_factory=list)
    task_rows: List[dict] = field(default_factory=list)  # parsed from the markdown table


# ---------------------------------------------------------------------------
# Helpers

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        _fail(f"cannot read {path}: {exc}")
        sys.exit(2)


def _top_headings(src: str) -> Set[str]:
    return {
        line[3:].strip()
        for line in src.splitlines()
        if line.startswith("## ")
    }


def _has_h1(src: str) -> bool:
    for line in src.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return True
    return False


def _parse_frontmatter(src: str) -> dict:
    lines = src.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return data
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_section(src: str, heading: str) -> str:
    """Return the body of a ## heading up to the next ## heading or EOF."""
    lines = src.splitlines()
    want = f"## {heading}"
    in_section = False
    out: List[str] = []
    for line in lines:
        if line.startswith("## ") and line.strip() == want:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            out.append(line)
    return "\n".join(out)


_SL_HEADER_RE = re.compile(r"^(SL-\d+)\s*—\s*(.+?)\s*$")
_KEY_VALUE_RE = re.compile(r"^\s*(Depends on|Blocks|Parallel-safe)\s*:\s*(.*?)\s*$")
_COMMA_SPLIT_RE = re.compile(r"[,\s]+")


def _parse_lane_index(body: str) -> List[Lane]:
    """Parse stanzas of the form:
        SL-1 — name
          Depends on: (none)
          Blocks: SL-3, SL-4
          Parallel-safe: yes
    """
    lanes: List[Lane] = []
    current: Optional[Lane] = None
    for line in body.splitlines():
        if not line.strip():
            continue
        m_header = _SL_HEADER_RE.match(line)
        if m_header:
            if current is not None:
                lanes.append(current)
            current = Lane(sl_id=m_header.group(1), name=m_header.group(2))
            continue
        if current is None:
            continue
        m_kv = _KEY_VALUE_RE.match(line)
        if not m_kv:
            continue
        key, value = m_kv.group(1), m_kv.group(2)
        if key == "Depends on":
            current.depends_on = _parse_sl_list(value)
        elif key == "Blocks":
            current.blocks = _parse_sl_list(value)
        elif key == "Parallel-safe":
            current.parallel_safe = value.split()[0] if value.strip() else ""
    if current is not None:
        lanes.append(current)
    return lanes


def _parse_sl_list(value: str) -> List[str]:
    value = value.strip()
    if not value or value.lower() == "(none)":
        return []
    return [tok.strip() for tok in re.split(r",\s*", value) if tok.strip()]


# Regex for per-lane ### SL-N sections inside ## Lanes
_LANE_SECTION_RE = re.compile(r"^###\s+(SL-\d+)\b.*$", re.MULTILINE)


def _split_lane_sections(lanes_body: str) -> Dict[str, str]:
    """Map SL-N -> section body (text between this ### heading and the next)."""
    matches = list(_LANE_SECTION_RE.finditer(lanes_body))
    out: Dict[str, str] = {}
    for idx, m in enumerate(matches):
        sl_id = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(lanes_body)
        out[sl_id] = lanes_body[start:end]
    return out


_BULLET_VALUE_RE = re.compile(
    r"^\s*-\s*\*\*(Scope|Owned files|Interfaces provided|Interfaces consumed)\*\*\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)


def _parse_lane_section(body: str) -> dict:
    """Extract Scope, Owned files, Interfaces provided, Interfaces consumed,
    and task rows from a single ### SL-N section body."""
    out = {
        "owned_globs": [],
        "interfaces_provided": [],
        "interfaces_consumed": [],
        "task_rows": [],
    }
    # Collect bullet values (may span multiple lines if formatted oddly;
    # stick to single-line bullets per the template).
    for line in body.splitlines():
        m = _BULLET_VALUE_RE.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2)
        items = _split_inline_items(val)
        if key == "owned files":
            out["owned_globs"] = items
        elif key == "interfaces provided":
            out["interfaces_provided"] = items
        elif key == "interfaces consumed":
            out["interfaces_consumed"] = items
    # Parse task table rows. The plan template uses:
    #   | Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
    out["task_rows"] = _parse_task_table(body)
    return out


_CODE_TOKEN_RE = re.compile(r"`([^`]+)`")


def _split_inline_items(raw: str) -> List[str]:
    """Extract items from an inline list. Prefer backtick-quoted tokens; else
    fall back to comma-splitting. Normalizes '(none)' to [].

    Propagates an inline '(pre-existing)' annotation onto EVERY extracted
    token so downstream checks (e.g., _check_f_interfaces_trace) can see
    the annotation even when the user wrote it outside the backticks,
    e.g. ``- **Interfaces consumed**: `IPlugin` (pre-existing)``.
    """
    s = raw.strip()
    if not s or s.lower() in {"(none)", "none", "—"}:
        return []
    has_pre_existing = "(pre-existing)" in s.lower()
    tokens = _CODE_TOKEN_RE.findall(s)
    if tokens:
        items = [t.strip() for t in tokens if t.strip()]
    else:
        items = [tok.strip().strip("`") for tok in re.split(r",\s*", s) if tok.strip()]
    if has_pre_existing:
        items = [f"{t} (pre-existing)" for t in items]
    return items


def _parse_task_table(body: str) -> List[dict]:
    """Parse the markdown task table out of a lane body.

    Expected header (case-insensitive):
      | Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
    """
    rows: List[dict] = []
    lines = body.splitlines()
    header_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        if (
            line.startswith("|")
            and "Task ID" in line
            and "Type" in line
            and "Depends" in line
        ):
            header_idx = idx
            break
    if header_idx is None:
        return rows
    # Skip the separator row (---|---|).
    for line in lines[header_idx + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 2:
            continue
        task_id = cells[0].strip("`")
        task_type = cells[1].strip("`").lower()
        rows.append({"task_id": task_id, "type": task_type})
    return rows


# ---------------------------------------------------------------------------
# Checks

Findings = List[str]  # human-readable error strings


def _check_a_required_headings(src: str) -> Findings:
    out: Findings = []
    if not _has_h1(src):
        out.append("(A) missing top-level `# <PHASE_ID>:` heading")
    required = [
        "Context",
        "Interface Freeze Gates",
        "Lane Index & Dependencies",
        "Lanes",
        "Execution Notes",
        "Acceptance Criteria",
        "Verification",
    ]
    found = _top_headings(src)
    for heading in required:
        if heading not in found:
            out.append(f"(A) missing required heading: `## {heading}`")
    return out


def _check_frontmatter(src: str, path: Path, repo_root: Optional[Path]) -> Findings:
    out: Findings = []
    metadata = _parse_frontmatter(src)
    required = ("phase_loop_plan_version", "phase", "roadmap", "roadmap_sha256")
    for key in required:
        if not metadata.get(key):
            out.append(f"(FM) missing required frontmatter field: `{key}`")
    if out:
        return out
    if metadata.get("phase_loop_plan_version") != "1":
        out.append("(FM) `phase_loop_plan_version` must be `1`")
    roadmap_value = metadata.get("roadmap", "")
    roadmap_path = Path(roadmap_value)
    if repo_root is not None:
        resolved_roadmap = roadmap_path if roadmap_path.is_absolute() else repo_root / roadmap_path
        if not resolved_roadmap.exists():
            out.append(f"(FM) `roadmap` does not resolve from repo root: `{roadmap_value}`")
        else:
            expected_sha = _sha256_file(resolved_roadmap)
            if metadata.get("roadmap_sha256") != expected_sha:
                out.append("(FM) `roadmap_sha256` does not match the referenced roadmap")
            try:
                expected_rel = str(resolved_roadmap.resolve().relative_to(repo_root.resolve()))
            except ValueError:
                expected_rel = str(resolved_roadmap.resolve())
            if roadmap_value != expected_rel:
                out.append(f"(FM) `roadmap` must be repo-relative canonical path `{expected_rel}`")
    elif not roadmap_value:
        out.append("(FM) `roadmap` must name the roadmap path")
    if path.name:
        phase = metadata.get("phase", "").upper()
        if phase and not path.name.upper().endswith(f"-{phase}.MD"):
            out.append(f"(FM) `phase` `{phase}` does not match plan filename `{path.name}`")
    return out


def _check_b_lane_index_parses(lanes: List[Lane]) -> Findings:
    out: Findings = []
    if not lanes:
        out.append("(B) `## Lane Index & Dependencies` has no parseable `SL-N — name` stanzas")
        return out
    for lane in lanes:
        if not lane.parallel_safe:
            out.append(f"(B) {lane.sl_id}: missing `Parallel-safe:` line")
        elif lane.parallel_safe not in {"yes", "no", "mixed"}:
            out.append(
                f"(B) {lane.sl_id}: `Parallel-safe: {lane.parallel_safe}` not in {{yes, no, mixed}}"
            )
    return out


def _check_c_dag_acyclic(lanes: List[Lane]) -> Findings:
    """Kahn's algorithm. Nodes with no incoming edges go first; remove them
    and their outgoing edges; repeat. If we can't drain the graph, there's
    a cycle."""
    out: Findings = []
    if not lanes:
        return out
    # Validate referenced IDs exist.
    known_ids = {lane.sl_id for lane in lanes}
    for lane in lanes:
        for dep in lane.depends_on:
            if dep not in known_ids:
                out.append(f"(C) {lane.sl_id}: depends on unknown lane `{dep}`")
    if out:
        return out
    in_deg: Dict[str, int] = {lane.sl_id: len(lane.depends_on) for lane in lanes}
    reverse_edges: Dict[str, List[str]] = {lane.sl_id: [] for lane in lanes}
    for lane in lanes:
        for dep in lane.depends_on:
            reverse_edges[dep].append(lane.sl_id)
    queue = [sl for sl, d in in_deg.items() if d == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for dependent in reverse_edges[node]:
            in_deg[dependent] -= 1
            if in_deg[dependent] == 0:
                queue.append(dependent)
    if visited != len(lanes):
        unresolved = [sl for sl, d in in_deg.items() if d > 0]
        out.append(f"(C) lane DAG has a cycle; unresolved after topological sort: {unresolved}")
    return out


def _git_ls_files(repo_root: Path) -> Optional[List[str]]:
    try:
        r = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    return [line for line in r.stdout.splitlines() if line]


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Minimal glob to regex: ** → any depth; * → any chars not /; everything else literal."""
    s = pattern.strip("`").strip()
    # Escape regex specials, then un-escape our own glob metacharacters.
    esc = re.escape(s)
    esc = esc.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", ".")
    return re.compile("^" + esc + "$")


def _check_d_owned_files_disjoint(
    lane_sections: Dict[str, dict],
    repo_root: Optional[Path],
) -> Findings:
    out: Findings = []
    tracked: Optional[List[str]] = None
    if repo_root is not None:
        tracked = _git_ls_files(repo_root)

    # First: exact glob duplication check.
    glob_origin: Dict[str, str] = {}
    for sl_id, parsed in lane_sections.items():
        for glob in parsed["owned_globs"]:
            norm = glob.strip().strip("`")
            if not norm:
                continue
            if norm in glob_origin and glob_origin[norm] != sl_id:
                out.append(
                    f"(D) duplicate owned glob `{norm}` in {glob_origin[norm]} and {sl_id}"
                )
            else:
                glob_origin[norm] = sl_id

    if tracked is None:
        # No repo context — skip expansion, only the exact-duplicate check ran.
        out.append(
            "(D) WARN: not running inside a git repo; owned-file disjointness only checked for exact duplicates"
        )
        return out

    # Expand each lane's globs to a concrete file set, then intersect.
    lane_file_sets: Dict[str, Set[str]] = {}
    for sl_id, parsed in lane_sections.items():
        matched: Set[str] = set()
        for glob in parsed["owned_globs"]:
            norm = glob.strip().strip("`")
            if not norm:
                continue
            rex = _glob_to_regex(norm)
            matched |= {p for p in tracked if rex.match(p)}
        lane_file_sets[sl_id] = matched

    sl_ids = sorted(lane_file_sets.keys())
    for i in range(len(sl_ids)):
        for j in range(i + 1, len(sl_ids)):
            a, b = sl_ids[i], sl_ids[j]
            inter = lane_file_sets[a] & lane_file_sets[b]
            if inter:
                sample = sorted(inter)[:5]
                out.append(
                    f"(D) file-ownership overlap between {a} and {b}: {len(inter)} files "
                    f"(first few: {sample})"
                )
    return out


def _check_owned_files_are_concrete(lane_sections_raw: Dict[str, str]) -> Findings:
    out: Findings = []
    for sl_id, body in lane_sections_raw.items():
        for line in body.splitlines():
            m = _BULLET_VALUE_RE.match(line)
            if not m or m.group(1).lower() != "owned files":
                continue
            raw = m.group(2).strip()
            if raw.lower() in {"(none)", "none", "—"}:
                continue
            for item in [part.strip() for part in raw.split(",") if part.strip()]:
                if not re.fullmatch(r"`[^`]+`", item):
                    out.append(
                        f"(D) {sl_id}: `Owned files` must be comma-separated backticked concrete paths/globs; "
                        f"found prose or unquoted entry `{item}`"
                    )
    return out


def _check_e_test_before_impl(lane_sections: Dict[str, dict]) -> Findings:
    out: Findings = []
    for sl_id, parsed in lane_sections.items():
        seen_test = False
        for row in parsed["task_rows"]:
            tt = row["type"]
            if tt == "test":
                seen_test = True
            elif tt == "impl" and not seen_test:
                out.append(
                    f"(E) {sl_id} task {row['task_id']}: `impl` appears before any `test` in the same lane"
                )
    return out


def _check_f_interfaces_trace(
    lane_sections: Dict[str, dict],
    lane_sections_raw: Optional[Dict[str, str]] = None,
) -> Findings:
    out: Findings = []
    provided: Set[str] = set()
    for parsed in lane_sections.values():
        for sym in parsed["interfaces_provided"]:
            provided.add(_normalize_interface(sym))
    for sl_id, parsed in lane_sections.items():
        # Fall back to raw body scan when the token's annotation was
        # separated from it during parsing (e.g. ``backtick`` (pre-existing)).
        raw_body = (lane_sections_raw or {}).get(sl_id, "")
        for sym in parsed["interfaces_consumed"]:
            norm = _normalize_interface(sym)
            if not norm:
                continue
            if "pre-existing" in sym.lower():
                continue
            # Raw-body fallback: the token itself appears with a (pre-existing)
            # annotation somewhere in the lane body (maybe on a different line).
            if raw_body:
                pattern = rf"`?{re.escape(norm)}`?\s*\(pre-existing\)"
                if re.search(pattern, raw_body, re.IGNORECASE):
                    continue
            if norm not in provided:
                out.append(
                    f"(F) WARN: {sl_id} consumes `{sym}` but no upstream lane provides it "
                    f"(mark '(pre-existing)' if it exists outside P-this-phase)"
                )
    return out


def _check_g_grep_paired_with_tests(src: str) -> Findings:
    """Every acceptance criterion that uses `rg`/`grep` as its sole assertion
    must cite a test file in the same bullet. A bare grep is defeatable by
    renaming a symbol to pass the regex — pair it with a behavioral test."""
    out: Findings = []
    body = _extract_section(src, "Acceptance Criteria")
    if not body:
        return out
    # Iterate top-level bullets. A bullet is a line starting with "- [ ]" or "- [x]".
    # Multi-line bullets continue until the next top-level bullet or blank line.
    lines = body.splitlines()
    current: List[str] = []
    bullets: List[str] = []
    for line in lines:
        if line.lstrip().startswith("- ["):
            if current:
                bullets.append("\n".join(current))
            current = [line]
        elif current:
            if not line.strip():
                bullets.append("\n".join(current))
                current = []
            else:
                current.append(line)
    if current:
        bullets.append("\n".join(current))
    for bullet in bullets:
        low = bullet.lower()
        has_grep = (
            re.search(r"\brg\s+-?\w*\b", bullet) is not None
            or re.search(r"\bgrep\s+-?\w*\b", bullet) is not None
        )
        if not has_grep:
            continue
        # Cite a test file if anything in the bullet looks like a test path:
        # `tests/` or `test_` or a `.py` with a test-ish stem.
        cites_test = (
            "tests/" in low
            or re.search(r"\btest_\w+\.py\b", low) is not None
            or re.search(r"\.py\b", low) is not None
            and ("test" in low or "pytest" in low)
        )
        if not cites_test:
            first_line = bullet.strip().splitlines()[0][:90]
            out.append(
                f"(G) WARN: acceptance criterion uses grep/rg without citing a paired test "
                f"file — rename-defeat risk: {first_line!r}"
            )
    return out


def _check_h_eager_reexport(src: str) -> Findings:
    """If Execution Notes says SL-0 adds re-exports to a __init__.py, require
    the doc to also specify the __getattr__ lazy pattern. Eager top-level
    re-exports break package load when a later lane drops or renames the
    exported symbol."""
    out: Findings = []
    body = _extract_section(src, "Execution Notes")
    if not body:
        return out
    low = body.lower()
    mentions_reexport = ("re-export" in low) or ("reexport" in low)
    mentions_init_py = "__init__.py" in low
    if mentions_reexport and mentions_init_py and "__getattr__" not in low:
        out.append(
            "(H) WARN: Execution Notes mentions re-exports in __init__.py but does not "
            "specify the `__getattr__` lazy pattern — eager top-level imports break "
            "package load when a later lane drops or renames the exported symbol"
        )
    return out


_SPEC_DELTA_DECISIONS = {
    "no_spec_delta",
    "roadmap_amendment",
    "canonical_spec_update",
    "governed_pipeline_refresh",
    "mirror_cutover_required",
    "dotfiles_skill_source_update",
    "human_source_judgment_required",
}


def _check_i_spec_closeout_plan(src: str) -> Findings:
    out: Findings = []
    body = _extract_section(src, "Spec Closeout Plan")
    if not body.strip():
        return ["(I) missing required `## Spec Closeout Plan` section"]
    for token in ("spec_delta_closeout.v1", "target surfaces", "evidence paths", "metadata_only"):
        if token not in body:
            out.append(f"(I) Spec Closeout Plan missing `{token}`")
    decision_match = re.search(r"(?im)^\s*-\s*decision\s*:\s*`?([a-z_]+)`?\s*$", body)
    if not decision_match:
        out.append("(I) Spec Closeout Plan missing `decision: <literal>`")
    elif decision_match.group(1) not in _SPEC_DELTA_DECISIONS:
        allowed = ", ".join(sorted(_SPEC_DELTA_DECISIONS))
        out.append(
            f"(I) invalid Spec Closeout Plan decision `{decision_match.group(1)}`; allowed: {allowed}"
        )
    return out


def _normalize_interface(sym: str) -> str:
    """Strip backticks, parens, and leading/trailing whitespace. Drop annotations like '(pre-existing)'."""
    s = sym.strip().strip("`").strip()
    # Drop any trailing parenthesized annotation.
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Main

def _fail(msg: str) -> None:
    print(f"validate_plan_doc: {msg}", file=sys.stderr)


def _check_j_docs_lane(src: str) -> Findings:
    """rigor-v1 P2: warn when no terminal docs-sweep lane is present.

    plan-phase requires a no-opt-out docs lane so a public-surface change cannot
    silently skip its doc footprint. Autonomy-first: this is a WARN (recorded,
    non-blocking); promote to an error once adopted across the fleet.
    """
    for m in re.finditer(r"^###\s+SL-\d+\s*[—-]\s*(.+?)\s*$", src, re.MULTILINE):
        # Word-bounded so "Docker"/"Docusaurus" don't masquerade as a docs lane.
        if re.search(r"\bdocs?\b|\bdocumentation\b", m.group(1), re.IGNORECASE):
            return []
    if re.search(r"SL-docs|docs[-\s]sweep", src, re.IGNORECASE):
        return []
    return [
        "(J) WARN: no terminal docs-sweep lane found (a `### SL-N — …docs…` lane). "
        "plan-phase requires a no-opt-out docs lane; add a terminal "
        "`SL-N — Documentation sweep` lane or promote this check to an error once adopted."
    ]


def _acceptance_bullets(src: str) -> List[str]:
    body = _extract_section(src, "Acceptance Criteria")
    if not body:
        return []
    bullets: List[str] = []
    current: List[str] = []
    for line in body.splitlines():
        if line.lstrip().startswith("- ["):
            if current:
                bullets.append("\n".join(current))
            current = [line]
        elif current:
            if not line.strip():
                bullets.append("\n".join(current))
                current = []
            else:
                current.append(line)
    if current:
        bullets.append("\n".join(current))
    return bullets


def _check_k_acceptance_testable(src: str) -> Findings:
    """rigor-v1 P5: warn on acceptance criteria that name no proving command.

    A testable bullet cites something checkable — a command/path/symbol in
    backticks, a path, an HTTP verb+status, or a comparison/return assertion.
    A prose bullet ("users can log in") is not mechanically checkable.
    Autonomy-first WARN; promotion to error is opt-in.
    """
    out: Findings = []
    # Case-sensitive on purpose: uppercase HTTP verbs are real assertions, but
    # lowercase "get"/"post"/"put" are ordinary English. No bare "/\w" (it matched
    # "and/or", "TCP/IP") and no bare "returns" (matched "user returns home").
    testable = re.compile(
        r"`[^`]+`"                                        # backticked command/path/symbol
        r"|\b(GET|POST|PUT|PATCH|DELETE)\b.*?\b\d{3}\b"   # HTTP verb (uppercase) + status code
        r"|==|!=|>=|<=|->"                                # comparison / return arrow
        r"|\bexit code\b|\bstatus code\b"
        r"|\btests?/|\btest_\w|\bpytest\b"                # cites a test
    )
    for bullet in _acceptance_bullets(src):
        if not testable.search(bullet):
            first = bullet.strip().splitlines()[0][:90]
            out.append(
                f"(K) WARN: acceptance criterion is not mechanically testable — "
                f"name the proving command/path/assertion: {first!r}"
            )
    return out


def _check_l_ui_visual_verification(src: str) -> Findings:
    """rigor-v1 P6: warn when a plan touches UI/visual files but the Verification
    section names no browser/screenshot step. Autonomy-first WARN."""
    if not re.search(r"\.(tsx|jsx|vue|svelte|css|scss)\b|/components/", src, re.IGNORECASE):
        return []
    verif = _extract_section(src, "Verification") or ""
    if re.search(r"playwright|screenshot|browser|in-chrome|visual", verif, re.IGNORECASE):
        return []
    return [
        "(L) WARN: plan touches UI/visual surfaces but `## Verification` names no "
        "browser/screenshot/Playwright step — add a visual check (and a visually "
        "observable acceptance criterion) for UI changes."
    ]


# docs-freshness v4 P3 — VENDORED copy of the canonical release-surface taxonomy.
# This script is a stdlib-only bundled skill script and CANNOT import the runtime.
# DRIFT-GUARD: must stay byte-equal to
# `phase_loop_runtime.docs_surfaces.RELEASE_AFFECTING_PATTERNS` — a test in the
# runtime suite (`test_docs_surfaces_drift`) fails if this copy diverges.
_VENDORED_RELEASE_AFFECTING_PATTERNS: Tuple[str, ...] = (
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
# Doc surfaces that satisfy a release change's documentation footprint.
_VENDORED_RELEASE_DOC_GLOBS: Tuple[str, ...] = (
    "CHANGELOG*",
    "RELEASE*",
    "**/README*",
    "README*",
    "docs/release*",
    "docs/release/**",
    "docs/releases/**",
    "docs/operations*",
    "**/operations*.md",
)


def _glob_match(path: str, pattern: str) -> bool:
    p = path.replace("\\", "/")
    if fnmatch.fnmatchcase(p, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatchcase(p, pattern[3:])


def _check_m_release_docs_underscope(lane_sections_parsed: Dict[str, dict]) -> Findings:
    """docs-freshness v4 P3: warn when lanes touch a release/manifest surface but
    no lane owns a release-doc surface (README/CHANGELOG/operations).

    The #18 under-scope case caught at plan time. Autonomy-first WARN (recorded,
    non-blocking) — the non-bypassable enforcement is the Layer A `docs-audit` CI
    gate; this is early feedback. Stdlib-only: uses the vendored taxonomy above.
    """
    owned: List[str] = []
    for sec in lane_sections_parsed.values():
        owned.extend(sec.get("owned_globs") or [])
    if not owned:
        return []
    release_touched = sorted(
        g for g in owned
        if any(_glob_match(g, pat) for pat in _VENDORED_RELEASE_AFFECTING_PATTERNS)
    )
    if not release_touched:
        return []
    docs_covered = any(
        any(_glob_match(g, pat) for pat in _VENDORED_RELEASE_DOC_GLOBS) for g in owned
    )
    if docs_covered:
        return []
    return [
        "(M) WARN: a lane owns a release/manifest surface "
        f"({', '.join(release_touched)}) but no lane owns a release-doc surface "
        "(README / CHANGELOG / docs/release / operations). Add the public docs to a "
        "lane's owned files or the docs-audit CI gate will block the merge."
    ]


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        _fail("usage: validate_plan_doc.py <plan-path>")
        return 2
    path = Path(argv[1])
    if not path.exists():
        _fail(f"plan doc not found: {path}")
        return 2

    src = _read(path)

    # Work out if we're inside a git repo for the disjointness expansion.
    repo_root: Optional[Path] = None
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path.parent,
            capture_output=True,
            text=True,
            check=True,
        )
        repo_root = Path(r.stdout.strip())
    except Exception:
        repo_root = None

    findings: Findings = []
    findings.extend(_check_frontmatter(src, path, repo_root))
    findings.extend(_check_a_required_headings(src))

    lane_index_body = _extract_section(src, "Lane Index & Dependencies")
    lanes = _parse_lane_index(lane_index_body)
    findings.extend(_check_b_lane_index_parses(lanes))
    findings.extend(_check_c_dag_acyclic(lanes))

    lanes_body = _extract_section(src, "Lanes")
    lane_sections_raw = _split_lane_sections(lanes_body)
    lane_sections_parsed = {
        sl_id: _parse_lane_section(body) for sl_id, body in lane_sections_raw.items()
    }

    # Every lane listed in the Lane Index should also have a ### section.
    missing_sections = [
        lane.sl_id for lane in lanes if lane.sl_id not in lane_sections_parsed
    ]
    for sl_id in missing_sections:
        findings.append(f"(B) {sl_id}: no matching `### {sl_id}` section under `## Lanes`")

    findings.extend(_check_owned_files_are_concrete(lane_sections_raw))
    findings.extend(_check_d_owned_files_disjoint(lane_sections_parsed, repo_root))
    findings.extend(_check_e_test_before_impl(lane_sections_parsed))
    findings.extend(_check_f_interfaces_trace(lane_sections_parsed, lane_sections_raw))
    findings.extend(_check_g_grep_paired_with_tests(src))
    findings.extend(_check_h_eager_reexport(src))
    findings.extend(_check_i_spec_closeout_plan(src))
    findings.extend(_check_j_docs_lane(src))
    findings.extend(_check_k_acceptance_testable(src))
    findings.extend(_check_l_ui_visual_verification(src))
    findings.extend(_check_m_release_docs_underscope(lane_sections_parsed))

    # Partition findings into errors vs warnings.
    errors = [f for f in findings if "WARN" not in f]
    warnings = [f for f in findings if "WARN" in f]

    if warnings:
        for w in warnings:
            print(w, file=sys.stderr)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(
            f"validate_plan_doc: {len(errors)} error(s), {len(warnings)} warning(s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"validate_plan_doc: OK — {len(lanes)} lanes, {len(warnings)} warning(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
