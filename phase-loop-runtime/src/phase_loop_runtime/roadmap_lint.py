"""Mechanical lint for phase-plan roadmap specs.

Single source of truth for roadmap validation. The skill-bundle script
``<harness>-phase-roadmap-builder/scripts/validate_roadmap.py`` is a thin shim
over this module, and the CLI exposes it as ``phase-loop validate-roadmap`` so
validation is always available wherever ``phase_loop_runtime`` is installed —
even on slim skill installs where the bundle ``scripts/`` dir is absent.

Checks (all run even if earlier ones fail so the author sees every issue):

  (A) Required top-level headings present.
  (B) Each ``### Phase N — <Name> (<ALIAS>)`` block carries the required fields.
  (C) Phase numbers non-decreasing; aliases unique.
  (D) IF-gate IDs match ``IF-0-<ALIAS>-\\d+`` and reconcile with phase Produces.
  (E) ``**Depends on**`` references only existing earlier-phase aliases.
  (F) The phase dependency DAG is acyclic.
  (G) Every phase declares a lane-count / partition hint (or is preamble).
  (H) EC-<ALIAS>-<N> goal IDs on exit-criteria reconcile (agent-harness#211):
      all-or-none per phase, alias-scoped, unique (gaps allowed, never renumber).

Zero external deps (stdlib only). Parses by regex on stable headings — not a
full Markdown parser.
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
    # agent-harness#211: parallel to `exit_criteria`, each element is the item-LEADING
    # `EC-<ALIAS>-<N>` goal ID or None. `exit_criteria` stays List[str] for API-compat.
    exit_criteria_ids: List[Optional[str]] = field(default_factory=list)

    @property
    def declared_exit_criteria_ids(self) -> List[str]:
        """The non-None goal IDs declared on this phase's exit-criteria, in order."""
        return [eid for eid in self.exit_criteria_ids if eid]


# ---------------------------------------------------------------------------
# Parsing

TOP_HEADING_RE = re.compile(r"^## +(?P<name>[^\n]+?)\s*$", re.MULTILINE)
PHASE_HEADING_RE = re.compile(
    r"^### +Phase\s+(?P<num>\d+)(?P<decimal>\.\d+)?(?P<letter>[A-Z]?)\s*[—\-]\s*(?P<name>.+?)\s*"
    r"\(\s*(?P<alias>[A-Za-z0-9]+)(?:\s*,[^)]*)?\s*\)\s*$",
    re.MULTILINE,
)
ANY_PHASE_HEADING_RE = re.compile(r"^### +Phase\s+\d+(?:\.\d+)?[A-Z]?\b.*$", re.MULTILINE)
FIELD_RE_TEMPLATE = r"^\*\*{label}\*\*\s*\n(?P<body>(?:(?!^\*\*|^### |^## ).*\n?)+)"
ALIAS_TOKEN_RE = re.compile(r"`([A-Za-z][A-Za-z0-9]*)`|\b([A-Z][A-Z0-9]{1,40}|[Pp]\d+[A-Za-z]?)\b")

REQUIRED_TOP_HEADINGS = [
    "Context",
    "Phases",
    "Top Interface-Freeze Gates",
    "Phase Dependency DAG",
    "Execution Notes",
    "Verification",
]

IF_GATE_RE = re.compile(r"\bIF-0-([A-Za-z0-9]+)-(\d+)\b")
# agent-harness#211: goal IDs on exit-criteria, mirroring the IF-gate scheme.
# `EC_ID_LEADING_RE` matches only an item-LEADING id (a reference/declaration counts
# only at the start of a checkbox item, never a prose mention elsewhere).
EC_ID_RE = re.compile(r"\bEC-([A-Za-z0-9]+)-(\d+)\b")
EC_ID_LEADING_RE = re.compile(r"^EC-([A-Za-z0-9]+)-(\d+)\b")
PREAMBLE_MARKER_RE = re.compile(r"preamble\s*/\s*interface-only|interface-freeze-only|preamble phase", re.IGNORECASE)


def _extract_top_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    matches = list(TOP_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group("name").strip()] = text[start:end]
    return sections


def _next_top_heading(text: str, start: int) -> int:
    m = TOP_HEADING_RE.search(text, start)
    return m.start() if m else len(text)


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
        phase.exit_criteria_ids = [_leading_ec_id(item) for item in phase.exit_criteria]
        phase.scope_notes = _field(body, "Scope notes")
        phase.non_goals = _field(body, "Non-goals")
        phase.key_files = _bullet_items(_field(body, "Key files"))
        phase.depends_on = _parse_depends_on(_field(body, "Depends on"))
        phase.produces = _parse_produces(_field(body, "Produces"))
        phases.append(phase)
    return phases


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
    stripped = block.strip()
    if not stripped or stripped.lower() in {"(none)", "none"}:
        return []
    return [f"IF-0-{alias}-{n}" for alias, n in IF_GATE_RE.findall(stripped)]


def _leading_ec_id(criterion_text: str) -> Optional[str]:
    """agent-harness#211: the item-LEADING ``EC-<ALIAS>-<N>`` goal ID of an
    exit-criterion, or None if the criterion is bare prose. Item-leading only —
    an ID mentioned mid-text does not declare/reference a goal."""
    m = EC_ID_LEADING_RE.match(criterion_text.strip())
    return f"EC-{m.group(1)}-{m.group(2)}" if m else None


# ---------------------------------------------------------------------------
# Checks

def check_phase_heading_format(text: str, errors: List[str]) -> None:
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
        if "**Depends on**" not in ph.raw_body:
            errors.append(f"(B) {loc}: missing **Depends on** block (use `(none)` for roots)")


def check_numbering_and_aliases(phases: List[Phase], errors: List[str]) -> None:
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

    valid_aliases = {ph.alias for ph in phases}
    for g in declared:
        alias = g.split("-")[2]
        if alias not in valid_aliases:
            errors.append(f"(D) gate {g} names alias '{alias}' that is not a defined phase")

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

    only_declared = declared - produced_global
    for g in sorted(only_declared):
        errors.append(f"(D) gate {g} listed in `## Top Interface-Freeze Gates` but not in any phase's **Produces**")


def check_exit_criteria_ids(phases: List[Phase], errors: List[str]) -> None:
    """agent-harness#211: reconcile `EC-<ALIAS>-<N>` goal IDs on exit-criteria.

    Opt-in ALL-OR-NONE per phase: either every exit-criterion carries a leading goal
    ID (opted in → downstream plan coverage is enforced) or none do (legacy). A phase
    mixing ID'd and bare criteria is an error — a bare criterion would otherwise be an
    ungated dropped-goal hole. Each declared ID's alias segment must equal the phase
    alias, and IDs must be unique within the phase. NOT contiguous: gaps are allowed so
    a deleted criterion never forces renumbering (which would silently re-bind a
    downstream plan's reference)."""
    for ph in phases:
        if not ph.exit_criteria:
            continue
        ids = ph.exit_criteria_ids
        declared = [eid for eid in ids if eid]
        if not declared:
            continue  # legacy bare-prose phase — not opted in, no gate
        if len(declared) != len(ph.exit_criteria):
            errors.append(
                f"(H) Phase {ph.number} ({ph.alias}): mixed exit-criteria — "
                f"{len(declared)}/{len(ph.exit_criteria)} carry an EC-{ph.alias}-<N> goal ID. "
                f"Opt-in is all-or-none: give EVERY exit-criterion an ID or none."
            )
        seen: Set[str] = set()
        for eid in declared:
            alias = eid.split("-")[1]
            if alias != ph.alias:
                errors.append(
                    f"(H) Phase {ph.number} ({ph.alias}): goal ID {eid} names alias '{alias}', "
                    f"not this phase's alias"
                )
            if eid in seen:
                errors.append(f"(H) Phase {ph.number} ({ph.alias}): duplicate goal ID {eid}")
            seen.add(eid)


def check_depends_on(phases: List[Phase], errors: List[str]) -> List[Phase]:
    aliases: Set[str] = {ph.alias for ph in phases}
    seen_so_far: Set[str] = set()
    roots: List[Phase] = []
    for ph in phases:
        for dep in ph.depends_on:
            if dep not in aliases:
                errors.append(f"(E) Phase {ph.number} ({ph.alias}): **Depends on** references unknown alias '{dep}'")
            elif dep not in seen_so_far and dep != ph.alias:
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
    word_num = r"(?:two|three|four|five|six|seven|eight|nine|ten)"
    numeric_re = re.compile(
        r"\b(?:\d+(?:\s*[\-–]\s*\d+)?|" + word_num + r"(?:\s*[\-–]\s*" + word_num + r")?)\s+lanes?\b",
        re.IGNORECASE,
    )
    partition_re = re.compile(
        r"\blane\s+[A-Z0-9]+\b|\bpartition|\bdisjoint|\bowns\b|\bsingle lane\b",
        re.IGNORECASE,
    )
    lane_token_re = re.compile(r"\b[A-Z][A-Z0-9]*-lane-[A-Za-z0-9]+\b|\bSL-[A-Za-z0-9]+\b")
    lanes_section_re = re.compile(
        r"^\*\*Lanes\*\*[^\n]*\n(?P<body>(?:(?!^\*\*|^### |^## ).*\n?)+)",
        re.MULTILINE,
    )
    for ph in phases:
        if PREAMBLE_MARKER_RE.search(ph.scope_notes):
            continue
        lanes_match = lanes_section_re.search(ph.raw_body)
        haystack = ph.scope_notes + "\n" + (lanes_match.group("body") if lanes_match else "")
        if numeric_re.search(haystack):
            continue
        if partition_re.search(haystack):
            continue
        if len(set(lane_token_re.findall(haystack))) >= 2:
            continue
        errors.append(
            f"(G) Phase {ph.number} ({ph.alias}): no lane count or partition hint in "
            f"**Scope notes** or **Lanes**. Add e.g., 'decompose into N lanes', "
            f"'Single lane' with justification, or mark as preamble/interface-only."
        )


# ---------------------------------------------------------------------------
# Public API

def lint_roadmap_text(text: str) -> List[str]:
    """Return a list of human-readable issues for roadmap markdown ``text``."""
    errors: List[str] = []
    sections = _extract_top_sections(text)
    phases = _extract_phases(text)
    check_required_headings(sections, errors)
    check_phase_heading_format(text, errors)
    check_phase_fields(phases, errors)
    check_numbering_and_aliases(phases, errors)
    check_if_gates(phases, sections, errors)
    check_exit_criteria_ids(phases, errors)
    check_depends_on(phases, errors)
    check_dag_acyclic(phases, errors)
    check_lane_count_hint(phases, errors)
    return errors


def lint_roadmap(path: Path | str) -> List[str]:
    """Return a list of issues for the roadmap at ``path`` (raises on read error)."""
    return lint_roadmap_text(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Train mode — validate a cross-repo release-train roadmap (P2)

def lint_train_roadmap_text(text: str) -> List[str]:
    """Return validation issues for a cross-repo train roadmap markdown ``text``.

    Validates the cross-repo DAG: acyclic, every depended-on node exists, the
    train is serially orderable (topo-sort), and every dependency edge carries
    a valid consumption-channel descriptor.  A non-orderable, cyclic, or
    channel-less train fails loud (returns non-empty list).

    This is a separate validation path from :func:`lint_roadmap_text` — it
    uses the train-roadmap parser (``train_roadmap.parse_train_roadmap``), not
    the phase-plan regex.
    """
    from .train_roadmap import parse_train_roadmap, validate_train

    try:
        roadmap = parse_train_roadmap(text)
    except ValueError as exc:
        return [f"(T-PARSE) {exc}"]
    return validate_train(roadmap)


def lint_train_roadmap(path: Path | str) -> List[str]:
    """Return validation issues for the train roadmap at ``path``."""
    return lint_train_roadmap_text(Path(path).read_text(encoding="utf-8"))


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        prog = Path(argv[0]).name if argv else "validate_roadmap"
        print(f"usage: {prog} [--train] <roadmap-path>", file=sys.stderr)
        return 2

    # Handle --train flag
    train_mode = False
    args = list(argv[1:])
    if "--train" in args:
        train_mode = True
        args = [a for a in args if a != "--train"]

    if len(args) != 1:
        prog = Path(argv[0]).name if argv else "validate_roadmap"
        print(f"usage: {prog} [--train] <roadmap-path>", file=sys.stderr)
        return 2

    path = Path(args[0])
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not read {path}: {exc}", file=sys.stderr)
        return 2

    if train_mode:
        errors = lint_train_roadmap_text(text)
        if errors:
            print(f"validate_roadmap (train): {len(errors)} issue(s) in {path}", file=sys.stderr)
            for e in errors:
                print(f"  • {e}", file=sys.stderr)
            return 1
        print(f"validate_roadmap (train): OK — {path}")
        return 0

    phases = _extract_phases(text)
    errors = lint_roadmap_text(text)
    if errors:
        print(f"validate_roadmap: {len(errors)} issue(s) in {path}", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        return 1
    print(f"validate_roadmap: OK — {len(phases)} phase(s) in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
