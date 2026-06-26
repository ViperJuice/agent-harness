from __future__ import annotations

import re
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path

from .models import (
    LaneDependency,
    LaneIRDiagnostic,
    LaneTaskSet,
    PhasePlanIR,
    PhasePlanLane,
)


LANE_HEADING_RE = re.compile(
    r"^###\s+(?P<id>(?:SL|SG|LG|CG|GM|OP|CMD)-\d+[A-Z]?|[A-Z][A-Z0-9]+-\d+[A-Z]?|Lane\s+\d+|Swim\s*lane\s+\d+)\s*(?:[-—:]\s*(?P<name>.+?))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
LANE_INDEX_LINE_RE = re.compile(
    r"^\s*-?\s*(?P<id>(?:SL|SG|LG|CG|GM|OP|CMD)-\d+[A-Z]?|[A-Z][A-Z0-9]+-\d+[A-Z]?|Lane\s+\d+|Swim\s*lane\s+\d+)\s*(?:[-—:]\s*(?P<name>.*?))?(?:;\s*(?P<meta>.*))?$",
    re.IGNORECASE,
)
FIELD_RE = re.compile(r"^\s*(?:-\s+)?\*\*(?P<label>[^*]+)\*\*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
TASK_RE = re.compile(r"^\s*-\s*(?P<bucket>test|impl|verify)\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
ROADMAP_PHASE_RE = re.compile(
    r"^###\s+Phase\s+\d+(?:\.\d+)?[A-Z]?\s+.*?\((?P<alias>[A-Z][A-Z0-9._-]*)(?:\s*,[^)]*)?\)[ \t]*(?:\S[^\n]*)?$",
    re.MULTILINE,
)
ROADMAP_DEPENDS_RE = re.compile(
    r"^\s*\*\*Depends on\*\*\s*$\n(?P<body>.*?)(?=^\s*\*\*[A-Z][^*]*\*\*|^---\s*$|^##\s+\S|^###\s+Phase|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
ROADMAP_DAG_SECTION_RE = re.compile(
    r"^##\s+Phase Dependency DAG\s*$\n(?P<body>.*?)(?=^##\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
ROADMAP_ARROW_RE = re.compile(r"\s*(?:[-=]+>|[─-]+>|→)\s*")
ROADMAP_ALIAS_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9._-]*\b")


def iter_waves(roadmap_path: Path) -> Iterator[tuple[str, ...]]:
    """Yield roadmap phase aliases in dependency waves."""
    text = roadmap_path.read_text(encoding="utf-8")
    aliases = tuple(match.group("alias").strip().upper() for match in ROADMAP_PHASE_RE.finditer(text))
    if not aliases:
        return

    dependencies = _roadmap_phase_dependencies(text, aliases)
    emitted: set[str] = set()
    remaining = set(aliases)
    while remaining:
        wave = tuple(alias for alias in aliases if alias in remaining and dependencies[alias] <= emitted)
        if not wave:
            cycle_aliases = tuple(alias for alias in aliases if alias in remaining)
            raise LaneIRDiagnostic(
                kind="cycle",
                message=f"roadmap phase dependency cycle detected: {' -> '.join(cycle_aliases)}",
                details={"cycle": cycle_aliases},
            )
        yield wave
        emitted.update(wave)
        remaining.difference_update(wave)


def parse_phase_plan_ir(plan: Path) -> PhasePlanIR:
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return PhasePlanIR(
            plan_path=str(plan),
            metadata={},
            diagnostics=(
                LaneIRDiagnostic(
                    kind="missing_lane_sections",
                    message=f"phase plan is unreadable: {plan}",
                ),
            ),
        )

    from .discovery import parse_dispatch_hints, parse_execution_policy, parse_frontmatter

    metadata = parse_frontmatter(text)
    merge_policy = None
    diagnostics: list[LaneIRDiagnostic] = []
    if "merge_policy" in metadata:
        try:
            from .pipeline_adapter.merge_policy import parse as parse_merge_policy

            merge_policy = parse_merge_policy(metadata)
        except Exception as exc:
            diagnostics.append(
                LaneIRDiagnostic(
                    kind="unsupported_lane_policy",
                    message=f"invalid merge_policy frontmatter: {exc}",
                    blocker_class="contract_bug",
                )
            )
    try:
        execution_policy = parse_execution_policy(plan, kind="plan")
    except ValueError as exc:
        execution_policy = None
        diagnostics.append(
            LaneIRDiagnostic(
                kind="unsupported_lane_policy",
                message=str(exc),
                blocker_class="contract_bug",
            )
        )
    dispatch_hints = parse_dispatch_hints(plan, kind="plan")
    lane_index = _parse_lane_index(text)
    sections = parse_lane_sections(text)
    if not sections:
        diagnostics.append(
            LaneIRDiagnostic(
                kind="missing_lane_sections",
                message="phase plan does not contain lane sections",
            )
        )

    lanes: list[PhasePlanLane] = []
    for lane_id, heading, body in sections:
        index_data = lane_index.get(lane_id, {})
        lane_diagnostics, lane = _parse_lane(plan, lane_id, heading, body, index_data, execution_policy)
        diagnostics.extend(lane_diagnostics)
        lanes.append(lane)

    diagnostics.extend(validate_lane_ir(lanes))
    dependencies = tuple(
        LaneDependency(source_lane_id=dependency, target_lane_id=lane.lane_id)
        for lane in lanes
        for dependency in lane.depends_on
    )
    return PhasePlanIR(
        plan_path=str(plan),
        metadata=metadata,
        lanes=tuple(lanes),
        dependencies=dependencies,
        diagnostics=tuple(diagnostics),
        execution_policy=execution_policy,
        dispatch_hints=dispatch_hints,
        merge_policy=merge_policy,
    )


def _roadmap_phase_dependencies(text: str, aliases: tuple[str, ...]) -> dict[str, set[str]]:
    alias_set = set(aliases)
    dependencies = {alias: set() for alias in aliases}
    phase_matches = list(ROADMAP_PHASE_RE.finditer(text))
    for index, match in enumerate(phase_matches):
        alias = match.group("alias").strip().upper()
        end = phase_matches[index + 1].start() if index + 1 < len(phase_matches) else len(text)
        section = text[match.end() : end]
        depends_match = ROADMAP_DEPENDS_RE.search(section)
        if not depends_match:
            continue
        for dependency in ROADMAP_ALIAS_TOKEN_RE.findall(depends_match.group("body")):
            dependency = dependency.upper()
            if dependency in alias_set and dependency != alias:
                dependencies[alias].add(dependency)

    if not any(dependencies.values()):
        for source, target in _roadmap_dag_edges(text, alias_set):
            if source != target:
                dependencies[target].add(source)
    return dependencies


def _roadmap_dag_edges(text: str, aliases: set[str]) -> tuple[tuple[str, str], ...]:
    match = ROADMAP_DAG_SECTION_RE.search(text)
    if not match:
        return ()
    edges: list[tuple[str, str]] = []
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```") or line.startswith("#"):
            continue
        line = re.sub(r"\([^)]*\)", "", line)
        parts = [part.strip() for part in ROADMAP_ARROW_RE.split(line) if part.strip()]
        if len(parts) < 2:
            continue
        chain: list[str] = []
        for part in parts:
            tokens = [token.upper() for token in ROADMAP_ALIAS_TOKEN_RE.findall(part) if token.upper() in aliases]
            if len(tokens) == 1:
                chain.append(tokens[0])
        edges.extend((left, right) for left, right in zip(chain, chain[1:]))
    return tuple(edges)


def parse_lane_sections(text: str) -> tuple[tuple[str, str, str], ...]:
    starts = list(LANE_HEADING_RE.finditer(text))
    sections: list[tuple[str, str, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        lane_id = _normalize_lane_id(start.group("id"))
        sections.append((lane_id, start.group(0).strip(), text[start.start() : end]))
    return tuple(sections)


def validate_lane_ir(lanes: list[PhasePlanLane] | tuple[PhasePlanLane, ...]) -> tuple[LaneIRDiagnostic, ...]:
    diagnostics: list[LaneIRDiagnostic] = []
    lane_ids = {lane.lane_id for lane in lanes}
    for lane in lanes:
        for dependency in lane.depends_on:
            if dependency not in lane_ids:
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="malformed_dependencies",
                        lane_id=lane.lane_id,
                        message=f"{lane.lane_id} depends on unknown lane {dependency}",
                        details={"dependency": dependency},
                    )
                )
        for blocked in lane.blocks:
            if blocked not in lane_ids:
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="malformed_dependencies",
                        lane_id=lane.lane_id,
                        message=f"{lane.lane_id} blocks unknown lane {blocked}",
                        details={"blocked": blocked},
                    )
                )

    diagnostics.extend(_cycle_diagnostics(lanes))
    diagnostics.extend(_ownership_diagnostics(lanes))
    diagnostics.extend(_producer_dependency_diagnostics(lanes))
    return tuple(diagnostics)


def detect_reducer_lane(lane_id: str, name: str, body: str) -> str:
    haystack = f"{lane_id} {name} {body}".lower()
    if "acceptance" in haystack or "exit criteria" in haystack:
        return "acceptance_reducer"
    if "compatibility" in haystack:
        return "compatibility_reducer"
    if "verification" in haystack or "verify" in haystack:
        return "verification_reducer"
    if "summary" in haystack or "synthesize" in haystack or "final" in haystack:
        return "summary_reducer"
    return "none"


def _parse_lane(
    plan: Path,
    lane_id: str,
    heading: str,
    body: str,
    index_data: dict[str, object],
    execution_policy,
) -> tuple[list[LaneIRDiagnostic], PhasePlanLane]:
    from .discovery import execution_policy_for_lane

    diagnostics: list[LaneIRDiagnostic] = []
    fields = _section_fields(body)
    name = _heading_name(heading, lane_id) or str(index_data.get("name") or lane_id)
    raw_owned = fields.get("owned files")
    owned_files: tuple[str, ...] = ()
    read_only = False
    if raw_owned is None:
        diagnostics.append(
            LaneIRDiagnostic(
                kind="missing_owned_files",
                lane_id=lane_id,
                message=f"{lane_id} is missing an owned-files contract",
                details={"heading": heading},
            )
        )
    elif raw_owned.strip().lower().startswith(("none", "(none")):
        read_only = True
    else:
        owned = tuple(item.strip() for item in re.findall(r"`([^`]+)`", raw_owned) if item.strip())
        if not owned:
            diagnostics.append(
                LaneIRDiagnostic(
                    kind="malformed_owned_files",
                    lane_id=lane_id,
                    message=f"{lane_id} owned-files contract must use backtick-delimited literals",
                    details={"heading": heading},
                )
            )
        owned_files = owned

    depends_on = tuple(_lane_list(str(index_data.get("depends_on") or fields.get("depends on") or "")))
    blocks = tuple(_lane_list(str(index_data.get("blocks") or fields.get("blocks") or "")))
    tasks = _task_set(body)
    verification_commands = tuple(
        command for command in tasks.verify if command.startswith("`") and command.endswith("`")
    )
    verification_commands = tuple(command.strip("`") for command in verification_commands)
    policy = None
    if execution_policy is not None:
        try:
            policy = execution_policy_for_lane(execution_policy, "execute", lane_id)
        except ValueError as exc:
            diagnostics.append(
                LaneIRDiagnostic(
                    kind="unsupported_lane_policy",
                    lane_id=lane_id,
                    message=str(exc),
                )
            )

    return diagnostics, PhasePlanLane(
        lane_id=lane_id,
        name=name,
        heading=heading,
        owned_files=owned_files,
        read_only=read_only,
        depends_on=depends_on,
        blocks=blocks,
        interfaces_provided=_interface_list(fields.get("interfaces provided", "")),
        interfaces_consumed=_interface_list(fields.get("interfaces consumed", "")),
        tasks=tasks,
        verification_commands=verification_commands,
        parallel_safe=_yes_no(str(index_data.get("parallel_safe") or fields.get("parallel-safe") or "")),
        reducer_kind=detect_reducer_lane(lane_id, name, body),
        execution_policy=policy,
    )


def _parse_lane_index(text: str) -> dict[str, dict[str, object]]:
    match = re.search(r"^##\s+Lane Index.*?\n(?P<body>.*?)(?=^##\s+\S|\Z)", text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if not match:
        return {}
    result: dict[str, dict[str, object]] = {}
    current: str | None = None
    for line in match.group("body").splitlines():
        lane_match = LANE_INDEX_LINE_RE.match(line)
        if lane_match:
            current = _normalize_lane_id(lane_match.group("id"))
            data = result.setdefault(current, {})
            if lane_match.group("name"):
                data["name"] = lane_match.group("name").strip()
            if lane_match.group("meta"):
                _merge_index_meta(data, lane_match.group("meta"))
            continue
        if current and ":" in line:
            _merge_index_meta(result[current], line.strip())
    return result


def _merge_index_meta(data: dict[str, object], raw: str) -> None:
    for key, value in re.findall(r"(Depends on|Blocks|Parallel-safe)\s*:\s*([^;]+)", raw, re.IGNORECASE):
        normalized = key.lower().replace("-", "_").replace(" ", "_")
        data[normalized] = value.strip()


def _section_fields(section: str) -> dict[str, str]:
    lines = section.splitlines()
    fields: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = FIELD_RE.match(lines[index])
        if not match:
            index += 1
            continue
        label = match.group("label").strip().lower()
        parts = [match.group("value").strip()]
        index += 1
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                break
            if FIELD_RE.match(lines[index]) or stripped.startswith("#") or TASK_RE.match(lines[index]):
                break
            if "`" in stripped or stripped.startswith("-"):
                parts.append(stripped)
                index += 1
                continue
            break
        fields[label] = " ".join(parts)
    return fields


def _task_set(section: str) -> LaneTaskSet:
    buckets: dict[str, list[str]] = {"test": [], "impl": [], "verify": [], "other": []}
    for match in TASK_RE.finditer(section):
        buckets[match.group("bucket").lower()].append(match.group("value").strip())
    return LaneTaskSet(
        test=tuple(buckets["test"]),
        impl=tuple(buckets["impl"]),
        verify=tuple(buckets["verify"]),
        other=tuple(buckets["other"]),
    )


def _interface_list(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    quoted = re.findall(r"`([^`]+)`", raw)
    values = quoted or re.split(r",|\band\b", raw)
    cleaned: list[str] = []
    for value in values:
        item = value.strip().strip(".")
        if not item or item.lower().startswith(("none", "pre-existing")):
            continue
        if item not in cleaned:
            cleaned.append(item)
    return tuple(cleaned)


def _lane_list(raw: str) -> tuple[str, ...]:
    raw = raw.strip()
    if not raw or raw.lower().startswith(("(none", "none")):
        return ()
    values = re.findall(r"(?:SL|SG|LG|CG|GM|OP|CMD)-\d+[A-Z]?|[A-Z][A-Z0-9]+-\d+[A-Z]?|Lane\s+\d+|Swim\s*lane\s+\d+", raw, re.IGNORECASE)
    return tuple(dict.fromkeys(_normalize_lane_id(value) for value in values))


def _cycle_diagnostics(lanes: list[PhasePlanLane] | tuple[PhasePlanLane, ...]) -> tuple[LaneIRDiagnostic, ...]:
    graph = {lane.lane_id: lane.depends_on for lane in lanes}
    diagnostics: list[LaneIRDiagnostic] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(lane_id: str, path: tuple[str, ...]) -> None:
        if lane_id in visiting:
            cycle = (*path, lane_id)
            diagnostics.append(
                LaneIRDiagnostic(
                    kind="cycle",
                    lane_id=lane_id,
                    message=f"lane dependency cycle detected: {' -> '.join(cycle)}",
                    details={"cycle": cycle},
                )
            )
            return
        if lane_id in visited:
            return
        visiting.add(lane_id)
        for dependency in graph.get(lane_id, ()):
            if dependency in graph:
                visit(dependency, (*path, lane_id))
        visiting.remove(lane_id)
        visited.add(lane_id)

    for lane_id in graph:
        visit(lane_id, ())
    return tuple(diagnostics)


def _ownership_diagnostics(lanes: list[PhasePlanLane] | tuple[PhasePlanLane, ...]) -> tuple[LaneIRDiagnostic, ...]:
    diagnostics: list[LaneIRDiagnostic] = []
    writers = [lane for lane in lanes if not lane.read_only]
    for index, left in enumerate(writers):
        for right in writers[index + 1 :]:
            if _patterns_overlap_any(left.owned_files, right.owned_files):
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="overlapping_write_ownership",
                        lane_id=right.lane_id,
                        message=f"{left.lane_id} and {right.lane_id} claim overlapping owned files",
                        details={"left": left.lane_id, "right": right.lane_id},
                    )
                )
    return tuple(diagnostics)


def _producer_dependency_diagnostics(lanes: list[PhasePlanLane] | tuple[PhasePlanLane, ...]) -> tuple[LaneIRDiagnostic, ...]:
    providers: dict[str, str] = {}
    for lane in lanes:
        for interface in lane.interfaces_provided:
            providers.setdefault(interface, lane.lane_id)
    diagnostics: list[LaneIRDiagnostic] = []
    for lane in lanes:
        for interface in lane.interfaces_consumed:
            provider = providers.get(interface)
            if provider and provider != lane.lane_id and provider not in lane.depends_on:
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="missing_producer_dependency",
                        lane_id=lane.lane_id,
                        message=f"{lane.lane_id} consumes {interface} from {provider} without depending on it",
                        details={"interface": interface, "producer": provider},
                    )
                )
    return tuple(diagnostics)


def _patterns_overlap_any(left_patterns: tuple[str, ...], right_patterns: tuple[str, ...]) -> bool:
    return any(_patterns_overlap(left, right) for left in left_patterns for right in right_patterns)


def _patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_has_glob = _has_glob(left)
    right_has_glob = _has_glob(right)
    if left_has_glob and fnmatchcase(right, left):
        return True
    if right_has_glob and fnmatchcase(left, right):
        return True
    if left_has_glob and right_has_glob:
        left_prefix = _pattern_prefix(left)
        right_prefix = _pattern_prefix(right)
        return bool(left_prefix and right_prefix and (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)))
    return False


def _has_glob(pattern: str) -> bool:
    return any(token in pattern for token in ("*", "?", "["))


def _pattern_prefix(pattern: str) -> str:
    match = re.match(r"^[^*?\[]+", pattern)
    return match.group(0).rstrip("/") if match else ""


def _heading_name(heading: str, lane_id: str) -> str:
    text = re.sub(r"^###\s+", "", heading).strip()
    text = re.sub(rf"^{re.escape(lane_id)}\s*[-—:]?\s*", "", text, flags=re.IGNORECASE).strip()
    return text


def _normalize_lane_id(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).upper().replace("SWIM LANE", "SWIM-LANE").replace("LANE ", "LANE-")


def _yes_no(value: str) -> bool:
    return value.strip().lower() in {"yes", "true", "1"}
