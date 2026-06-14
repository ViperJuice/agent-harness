#!/usr/bin/env python3
"""
validate_roadmap.py — mechanical lint for phase-plan roadmap specs.

Usage:
    validate_roadmap.py <roadmap-path>

Checks the multi-phase roadmap file (e.g., `specs/phase-plans-v1.md`) that
`/claude-plan-phase` will consume. Exit 0 on clean pass; non-zero with a
human-readable failure list on stderr.

Checks (all run even if earlier ones fail so the author sees every issue):

  (A) Required top-level headings present:
        # <title>
        ## Context
        ## Phases
        ## Top Interface-Freeze Gates
        ## Phase Dependency DAG
        ## Execution Notes
        ## Verification
      Optional but recommended: ## Architecture North Star, ## Assumptions,
      ## Non-Goals, ## Cross-Cutting Principles, ## Acceptance Criteria.

  (B) Each `### Phase N — <Name> (<ALIAS>)` block contains:
        **Objective**
        **Exit criteria**            (≥1 checkbox item `- [ ]`)
        **Scope notes**
        **Key files**
        **Depends on**               (alias list or `(none)`)
        **Produces**                 (IF-gate list or `(none)`)
      Plus: `**Non-goals**` recommended but not required.

  (C) Phase numbers non-decreasing. Aliases unique.

  (D) IF-gate IDs match `IF-0-<ALIAS>-\\d+`. Every gate listed under
      `## Top Interface-Freeze Gates` must appear in exactly one phase's
      `**Produces**` block, and vice versa.

  (E) `**Depends on**` references only existing earlier-phase aliases.
      Root phases (≥1 required) declare `(none)`.

  (F) Phase Dependency DAG (built from `**Depends on**` edges) is acyclic
      — topological sort must succeed.

  (G) Every phase with only one implied lane must explicitly tag itself
      a preamble / interface-only phase in `**Scope notes**`.

Design: zero external deps (stdlib only). Parses by regex on stable
headings — not a full Markdown parser.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data model

@dataclass
class Phase:
    number: int
    name: str
    alias: str
    objective: str = ""
    exit_criteria: List[str] = field(default_factory=list)
    scope_notes: str = ""
    non_goals: str = ""
    key_files: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)  # aliases, or [] if (none)
    produces: List[str] = field(default_factory=list)    # IF-gate ids
    raw_body: str = ""


# ---------------------------------------------------------------------------
# IO

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"error: could not read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Parsing

TOP_HEADING_RE = re.compile(r"^## +(?P<name>[^\n]+?)\s*$", re.MULTILINE)
# Accept `Phase 1`, `Phase 2A`, and alias inside parens with optional trailing
# annotation after comma: `(P6A, parallel after P1)`.
PHASE_HEADING_RE = re.compile(
    r"^### +Phase\s+(?P<num>\d+)(?P<decimal>\.\d+)?(?P<letter>[A-Z]?)\s*[—\-]\s*(?P<name>.+?)\s*"
    r"\(\s*(?P<alias>[A-Za-z0-9]+)(?:\s*,[^)]*)?\s*\)\s*$",
    re.MULTILINE,
)
ANY_PHASE_HEADING_RE = re.compile(r"^### +Phase\s+\d+(?:\.\d+)?[A-Z]?\b.*$", re.MULTILINE)
FIELD_RE_TEMPLATE = r"^\*\*{label}\*\*\s*\n(?P<body>(?:(?!^\*\*|^### |^## ).*\n?)+)"
# Used to pluck alias-looking tokens from a Depends-on line.
ALIAS_TOKEN_RE = re.compile(r"`([A-Za-z][A-Za-z0-9]*)`|\b([A-Z][A-Z0-9]{1,40}|[Pp]\d+[A-Za-z]?)\b")


def _extract_top_sections(text: str) -> Dict[str, str]:
    """Return mapping of level-2 heading → body text up to next level-2 heading."""
    sections: Dict[str, str] = {}
    matches = list(TOP_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group("name").strip()] = text[start:end]
    return sections


def _extract_phases(text: str) -> List[Phase]:
    phases: List[Phase] = []
    matches = list(PHASE_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else _next_top_heading(text, body_start)
        body = text[body_start:body_end]
        phase = Phase(
            number=int(m.group("num")),
            name=m.group("name").strip(),
            alias=m.group("alias").strip(),
            raw_body=body,
        )
        phase.objective = _field(body, "Objective")
        phase.exit_criteria = _checkbox_items(_field(body, "Exit criteria"))
        phase.scope_notes = _field(body, "Scope notes")
        phase.non_goals = _field(body, "Non-goals")
        phase.key_files = _bullet_items(_field(body, "Key files"))
        phase.depends_on = _parse_depends_on(_field(body, "Depends on"))
        phase.produces = _parse_produces(_field(body, "Produces"))
        phases.append(phase)
    return phases


def _next_top_heading(text: str, start: int) -> int:
    m = TOP_HEADING_RE.search(text, start)
    return m.start() if m else len(text)


def _field(body: str, label: str) -> str:
    pat = re.compile(FIELD_RE_TEMPLATE.format(label=re.escape(label)), re.MULTILINE)
    m = pat.search(body)
    return m.group("body").strip() if m else ""


def _checkbox_items(block: str) -> List[str]:
    return [
        line.strip()[6:].strip()
        for line in block.splitlines()
        if line.strip().startswith("- [ ]") or line.strip().startswith("- [x]")
    ]


def _bullet_items(block: str) -> List[str]:
    return [
        line.strip().lstrip("-").strip()
        for line in block.splitlines()
        if line.strip().startswith("- ")
    ]


def _parse_depends_on(block: str) -> List[str]:
    """Extract phase aliases from a Depends-on block.

    Accepts bulleted lines with free-form prose after the alias
    (e.g. `- P4 merged (documenting before shipping)` or `- BROWNSTATE`).
    Extracts alias-looking tokens from each line. Returns [] for `(none)`.
    """
    stripped = block.strip()
    if not stripped or stripped.lower() in {"(none)", "none"}:
        return []
    items: List[str] = []
    for line in stripped.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or line.lower() in {"(none)", "none"}:
            continue
        for m in ALIAS_TOKEN_RE.finditer(line):
            token = (m.group(1) or m.group(2) or "").strip()
            if token:
                items.append(token.upper())
    return items


def _parse_produces(block: str) -> List[str]:
    """Extract IF-gate IDs from a Produces block.

    Accepts entries with parenthetical annotations like
    `IF-0-P3-1 (unblocks P4)`. Non-gate prose (e.g., descriptive text
    from a terminal phase) is ignored — terminal phases may leave this
    empty.
    """
    stripped = block.strip()
    if not stripped or stripped.lower() in {"(none)", "none"}:
        return []
    return [f"IF-0-{alias}-{n}" for alias, n in IF_GATE_RE.findall(stripped)]


# ---------------------------------------------------------------------------
# Checks

REQUIRED_TOP_HEADINGS = [
    "Context",
    "Phases",
    "Top Interface-Freeze Gates",
    "Phase Dependency DAG",
    "Execution Notes",
    "Verification",
]

IF_GATE_RE = re.compile(r"\bIF-0-([A-Za-z0-9]+)-(\d+)\b")
PREAMBLE_MARKER_RE = re.compile(r"preamble\s*/\s*interface-only|interface-freeze-only|preamble phase", re.IGNORECASE)


def check_phase_heading_format(text: str, errors: List[str]) -> None:
    """Fail with a specific message when phase headings omit aliases."""
    for m in ANY_PHASE_HEADING_RE.finditer(text):
        heading = m.group(0).strip()
        if PHASE_HEADING_RE.match(heading):
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        errors.append(
            f"(B) line {line_no}: invalid phase heading `{heading}`; "
            "expected `### Phase N — <Name> (<ALIAS>)`"
        )


def check_required_headings(sections: Dict[str, str], errors: List[str]) -> None:
    # Match a required heading as long as some present heading starts with
    # that name. Allows e.g. `## Verification (whole-refactor, after P5 merge)`.
    present_prefixes = [h.split("(", 1)[0].strip().lower() for h in sections.keys()]
    missing: List[str] = []
    for required in REQUIRED_TOP_HEADINGS:
        if not any(p == required.lower() or p.startswith(required.lower()) for p in present_prefixes):
            missing.append(required)
    if missing:
        errors.append(f"(A) missing required level-2 headings: {', '.join(missing)}")


def check_phase_fields(phases: List[Phase], errors: List[str]) -> None:
    if not phases:
        errors.append("(B) no phases found — expected at least one `### Phase N — <Name> (<ALIAS>)`")
        return
    for ph in phases:
        loc = f"Phase {ph.number} ({ph.alias})"
        if not ph.objective:
            errors.append(f"(B) {loc}: missing **Objective**")
        if not ph.exit_criteria:
            errors.append(f"(B) {loc}: **Exit criteria** missing or has no `- [ ]` checkboxes")
        if not ph.scope_notes:
            errors.append(f"(B) {loc}: missing **Scope notes**")
        if not ph.key_files:
            errors.append(f"(B) {loc}: **Key files** missing or empty")
        # Depends on must exist textually; roots use `(none)`.
        if "**Depends on**" not in ph.raw_body:
            errors.append(f"(B) {loc}: missing **Depends on** block (use `(none)` for roots)")
        # Produces is optional — terminal phases may publish no gates.


def check_numbering_and_aliases(phases: List[Phase], errors: List[str]) -> None:
    """Phase numbers non-decreasing; aliases unique.

    Same number across letter suffixes is allowed (Phase 2A, 2B, 6A, 6B).
    """
    seen_aliases: Set[str] = set()
    last_num = 0
    for ph in phases:
        if ph.number < last_num:
            errors.append(f"(C) phase number {ph.number} ({ph.alias}) decreases from previous ({last_num})")
        last_num = max(last_num, ph.number)
        if ph.alias in seen_aliases:
            errors.append(f"(C) duplicate alias: {ph.alias}")
        seen_aliases.add(ph.alias)


def check_if_gates(phases: List[Phase], sections: Dict[str, str], errors: List[str]) -> None:
    gates_section = sections.get("Top Interface-Freeze Gates", "")
    declared: Set[str] = set()
    for m in IF_GATE_RE.finditer(gates_section):
        gate_id = f"IF-0-{m.group(1)}-{m.group(2)}"
        declared.add(gate_id)

    # Validate gate IDs reference real phases.
    valid_aliases = {ph.alias for ph in phases}
    for g in declared:
        parts = g.split("-")
        alias = parts[2]
        if alias not in valid_aliases:
            errors.append(f"(D) gate {g} names alias '{alias}' that is not a defined phase")

    # Each phase's Produces entries must be well-formed IF-IDs whose alias
    # segment matches the phase's own alias.
    produced_global: Set[str] = set()
    for ph in phases:
        for g in ph.produces:
            owner = g.split("-")[2]
            if owner != ph.alias:
                errors.append(
                    f"(D) Phase {ph.number} ({ph.alias}): produces {g} but its alias segment is '{owner}', "
                    f"not this phase's alias"
                )
            if g in produced_global:
                errors.append(f"(D) gate {g} is declared in multiple phases' **Produces** blocks")
            produced_global.add(g)

    # Symmetry: every declared top-level gate must be produced by some phase.
    # The reverse is relaxed: a phase may produce a gate it doesn't bother
    # listing at the top (the top-level section is a summary, not authoritative).
    only_declared = declared - produced_global
    for g in sorted(only_declared):
        errors.append(f"(D) gate {g} listed in `## Top Interface-Freeze Gates` but not in any phase's **Produces**")


def check_depends_on(phases: List[Phase], errors: List[str]) -> List[Phase]:
    aliases: Set[str] = {ph.alias for ph in phases}
    seen_so_far: Set[str] = set()
    roots: List[Phase] = []
    for ph in phases:
        for dep in ph.depends_on:
            if dep not in aliases:
                errors.append(f"(E) Phase {ph.number} ({ph.alias}): **Depends on** references unknown alias '{dep}'")
            elif dep not in seen_so_far and dep != ph.alias:
                # Allow forward refs only between equal-numbered sibling phases (rare).
                # Otherwise warn.
                errors.append(
                    f"(E) Phase {ph.number} ({ph.alias}): **Depends on** references '{dep}' "
                    f"which is not an earlier phase in document order"
                )
        if not ph.depends_on:
            roots.append(ph)
        seen_so_far.add(ph.alias)
    if not roots:
        errors.append("(E) no root phases found — at least one phase must have `**Depends on**` = `(none)`")
    return roots


def check_dag_acyclic(phases: List[Phase], errors: List[str]) -> None:
    graph: Dict[str, List[str]] = {ph.alias: list(ph.depends_on) for ph in phases}
    # Kahn's algorithm on reverse edges: in-degree = number of phases depending on me.
    # We treat edges as dep → phase (dep must finish before phase).
    edges: Dict[str, List[str]] = {ph.alias: [] for ph in phases}
    indeg: Dict[str, int] = {ph.alias: 0 for ph in phases}
    for ph in phases:
        for dep in ph.depends_on:
            if dep in edges:
                edges[dep].append(ph.alias)
                indeg[ph.alias] += 1
    queue = [a for a, d in indeg.items() if d == 0]
    visited = 0
    while queue:
        cur = queue.pop(0)
        visited += 1
        for nb in edges[cur]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                queue.append(nb)
    if visited != len(phases):
        unresolved = [a for a, d in indeg.items() if d > 0]
        errors.append(f"(F) cycle detected in phase dependencies; unresolved: {', '.join(unresolved)}")


def check_lane_count_hint(phases: List[Phase], errors: List[str]) -> None:
    """Encourage ≥2 lanes per phase for parallelism.

    Accepts as sufficient any of:
      - `Single lane` stated explicitly (intentional exception)
      - preamble / interface-only marker
      - numeric lane count (`2 lanes`, `2–3 lanes`)
      - partition language (`lane A`, `owns`, `disjoint`, `partition`)
    """
    numeric_re = re.compile(r"\b(\d+)(?:\s*[\-–]\s*\d+)?\s+lanes?\b", re.IGNORECASE)
    partition_re = re.compile(
        r"\blane\s+[A-Z0-9]+\b|\bpartition|\bdisjoint|\bowns\b|\bsingle lane\b",
        re.IGNORECASE,
    )
    for ph in phases:
        if PREAMBLE_MARKER_RE.search(ph.scope_notes):
            continue
        if numeric_re.search(ph.scope_notes):
            continue
        if partition_re.search(ph.scope_notes):
            continue
        errors.append(
            f"(G) Phase {ph.number} ({ph.alias}): **Scope notes** gives no lane count or partition hint. "
            f"Add e.g., 'decompose into N lanes', 'Single lane' with justification, or mark as preamble/interface-only."
        )


# ---------------------------------------------------------------------------
# Entry point

def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <roadmap-path>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    text = _read(path)

    errors: List[str] = []
    sections = _extract_top_sections(text)
    phases = _extract_phases(text)

    check_required_headings(sections, errors)
    check_phase_heading_format(text, errors)
    check_phase_fields(phases, errors)
    check_numbering_and_aliases(phases, errors)
    check_if_gates(phases, sections, errors)
    check_depends_on(phases, errors)
    check_dag_acyclic(phases, errors)
    check_lane_count_hint(phases, errors)

    if errors:
        print(f"validate_roadmap: {len(errors)} issue(s) in {path}", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1

    print(f"validate_roadmap: OK — {len(phases)} phase(s) in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
