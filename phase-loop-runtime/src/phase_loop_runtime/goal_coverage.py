"""agent-harness#211: decidable goal-coverage check (single source of truth).

Replaces the fuzzy acceptance-vs-exit-criteria text audit. A roadmap phase's
exit-criteria carry stable ``EC-<ALIAS>-<N>`` goal IDs (see ``roadmap_lint``); a
plan's ``## Acceptance Criteria`` items REFERENCE those IDs (item-leading) instead
of restating the goal. The check is pure set membership — every declared goal ID
must be referenced by ≥1 acceptance item — so it is fully decidable and has none of
the fuzzy audit's false-positive / fail-open failure modes.

Scope (honest): guarantees COMPLETENESS (no goal silently forgotten). It does NOT
verify ADEQUACY (that the referenced evidence actually discharges the goal) — that
stays with code review + evidence authenticity (#91). It makes weak evidence
human-reviewable at the reference point rather than hidden in a paraphrase.

Opt-in per phase: a phase with no EC-IDs is ``not_applicable`` (legacy, no gate).
Runs at plan-time (CLI), preflight, and closeout (mutation-window) — all pure
Python, no ``EmitPhaseCloseout`` schema change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import discovery
from .roadmap_lint import EC_ID_LEADING_RE, _extract_phases, check_exit_criteria_ids as _check_exit_criteria_ids


@dataclass(frozen=True)
class GoalCoverageResult:
    repo: str
    plan: str
    roadmap: str
    phase_alias: str | None
    applicable: bool
    declared_ids: tuple[str, ...]
    referenced_ids: tuple[str, ...]
    unreferenced_ids: tuple[str, ...]  # declared but not referenced -> forgotten goal
    dangling_refs: tuple[str, ...]  # referenced but not declared -> typo / stale ref
    setup_diagnostics: tuple[str, ...]

    def has_gaps(self) -> bool:
        return bool(self.unreferenced_ids or self.dangling_refs)

    def has_setup_errors(self) -> bool:
        return bool(self.setup_diagnostics)

    def not_applicable(self) -> bool:
        return not self.applicable and not self.setup_diagnostics

    def is_clean(self) -> bool:
        return not self.has_gaps() and not self.has_setup_errors()

    def blocker_summary(self) -> str:
        parts = []
        if self.unreferenced_ids:
            parts.append(f"roadmap goal(s) not referenced by any acceptance item: {sorted(self.unreferenced_ids)}")
        if self.dangling_refs:
            parts.append(f"plan references unknown goal ID(s): {sorted(self.dangling_refs)}")
        if self.setup_diagnostics:
            parts.append(f"un-auditable: {self.setup_diagnostics[0]}")
        return "; ".join(parts) or "goal coverage clean"

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "plan": self.plan,
            "roadmap": self.roadmap,
            "phase_alias": self.phase_alias,
            "applicable": self.applicable,
            "declared_ids": list(self.declared_ids),
            "referenced_ids": list(self.referenced_ids),
            "unreferenced_ids": list(self.unreferenced_ids),
            "dangling_refs": list(self.dangling_refs),
            "setup_diagnostics": list(self.setup_diagnostics),
        }

    def render_text(self) -> str:
        lines = ["Goal Coverage Audit", f"  plan: {self.plan}", f"  roadmap: {self.roadmap} (phase {self.phase_alias})"]
        if self.setup_diagnostics:
            lines.append("  SETUP ERRORS (un-auditable — not a coverage verdict):")
            lines.extend(f"    - {d}" for d in self.setup_diagnostics)
            return "\n".join(lines)
        if not self.applicable:
            lines.append("  not applicable — phase declares no EC-<ALIAS>-<N> goal IDs (legacy).")
            return "\n".join(lines)
        lines.append(f"  declared goal IDs: {len(self.declared_ids)}, referenced: {len(self.referenced_ids)}")
        for gid in sorted(self.unreferenced_ids):
            lines.append(f"  [UNREFERENCED GOAL] {gid} — no plan acceptance item references it")
        for ref in sorted(self.dangling_refs):
            lines.append(f"  [DANGLING REF] {ref} — plan references a goal ID not declared in this phase")
        if self.is_clean():
            lines.append("  clean — every roadmap goal ID is referenced by ≥1 acceptance item.")
        return "\n".join(lines)


_ACCEPTANCE_SECTION_RE = re.compile(
    r"^##\s+Acceptance\s+Criteria\s*$\n(?P<body>.*?)(?=^##\s+\S|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
# Checkbox item prefix — tolerant of a missing trailing space and an upper/lower `x`
# (CR Fable: `- [ ]EC-P1-1` and `- [X]` must not be mis-stripped/ignored).
_CHECKBOX_PREFIX_RE = re.compile(r"^- \[[ xX]\]\s*")


def _leading_ec_ids(item_text: str) -> list[str]:
    """All item-LEADING ``EC-<ALIAS>-<N>`` IDs (a 1:many item may lead with several,
    e.g. ``EC-P1-1, EC-P1-2 — proven by ...``). A mid-text prose mention does NOT
    count."""
    ids: list[str] = []
    s = item_text.strip()
    while True:
        m = EC_ID_LEADING_RE.match(s)
        if not m:
            break
        ids.append(f"EC-{m.group(1)}-{m.group(2)}")
        s = s[m.end():].lstrip(" ,")
    return ids


def extract_plan_goal_refs(plan: Path) -> set[str]:
    """Goal IDs referenced from item-leading position of the plan's
    ``## Acceptance Criteria`` checklist items only (never a global prose scan)."""
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return set()
    match = _ACCEPTANCE_SECTION_RE.search(text)
    if match is None:
        return set()
    refs: set[str] = set()
    for line in match.group("body").splitlines():
        stripped = line.strip()
        prefix = _CHECKBOX_PREFIX_RE.match(stripped)
        if prefix:
            refs.update(_leading_ec_ids(stripped[prefix.end():]))
    return refs


def phase_declares_goal_ids(roadmap: Path | str, phase: str) -> bool:
    """True iff the roadmap phase `phase` opts into goal IDs (declares any EC-<ALIAS>-<N>
    on its exit-criteria). Used to decide whether an un-resolvable plan is un-auditable
    (opted-in) vs simply out of scope (legacy)."""
    phases = _extract_phases(Path(roadmap).read_text(encoding="utf-8"))
    match = next((p for p in phases if p.alias.upper() == str(phase or "").upper()), None)
    return bool(match and match.declared_exit_criteria_ids)


def check_goal_coverage(
    repo: Path | str, plan: Path | str, roadmap: Path | str | None = None
) -> GoalCoverageResult:
    repo_path = Path(repo)
    plan_path = Path(plan)
    metadata = discovery.plan_metadata(plan_path)
    alias = metadata.get("phase")
    roadmap_path = Path(roadmap) if roadmap is not None else discovery._roadmap_from_plan(repo_path, plan_path)

    def _result(**kw: Any) -> GoalCoverageResult:
        base = dict(
            repo=str(repo_path), plan=str(plan_path), roadmap=str(roadmap_path), phase_alias=alias,
            applicable=False, declared_ids=(), referenced_ids=(), unreferenced_ids=(),
            dangling_refs=(), setup_diagnostics=(),
        )
        base.update(kw)
        return GoalCoverageResult(**base)  # type: ignore[arg-type]

    # Anchor gate — refuse to audit against a stale/alternate roadmap (validates
    # version/phase/roadmap-path/roadmap_sha256).
    diag = discovery.plan_artifact_diagnostic(repo_path, plan_path, roadmap_path, alias)
    if diag is not None:
        return _result(setup_diagnostics=(f"plan_anchor:{diag}",))

    try:
        phases = _extract_phases(roadmap_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _result(setup_diagnostics=(f"roadmap_unreadable:{exc}",))
    matches = [p for p in phases if p.alias.upper() == str(alias or "").upper()]
    if not matches:
        return _result(setup_diagnostics=(f"phase_alias_not_found:{alias}",))
    if len(matches) > 1:
        # Duplicate phase alias — a malformed roadmap (roadmap_lint (C)). Selecting the
        # first would silently exclude the other phase's goals (CR codex). Fail closed.
        return _result(setup_diagnostics=(f"duplicate_phase_alias:{alias}",))
    match = matches[0]

    # Run the FULL EC-ID reconciliation the offline `roadmap_lint` (H) check runs
    # (all-or-none, alias-scoped, UNIQUE) at RUNTIME — the gate must not trust a
    # malformed roadmap (mixed-mode, duplicate ID collapsing two goals into one, or a
    # wrong-alias ID) and silently omit a goal (CR codex). Any violation -> setup error.
    _ec_errors: list[str] = []
    _check_exit_criteria_ids([match], _ec_errors)
    if _ec_errors:
        return _result(setup_diagnostics=(f"malformed_exit_criteria_ids: {_ec_errors[0]}",))

    declared = tuple(match.declared_exit_criteria_ids)

    refs = extract_plan_goal_refs(plan_path)
    if not declared:
        # Legacy phase (no goal IDs). Only truly not_applicable when the plan ALSO
        # references no goal IDs; a plan reference to a nonexistent goal is a dangling
        # gap (CR codex — every unknown EC-ID reference is a contract_bug).
        if not refs:
            return _result(applicable=False)
        return _result(applicable=True, referenced_ids=tuple(sorted(refs)), dangling_refs=tuple(sorted(refs)))

    declared_set = set(declared)
    return _result(
        applicable=True,
        declared_ids=declared,
        referenced_ids=tuple(sorted(refs)),
        unreferenced_ids=tuple(sorted(declared_set - refs)),
        dangling_refs=tuple(sorted(refs - declared_set)),
    )
