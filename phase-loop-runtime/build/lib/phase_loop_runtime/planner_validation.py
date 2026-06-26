from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .models import (
    BLOCKER_CLASSES,
    DISPATCH_CAPABILITIES,
    EXECUTORS,
    PHASE_STATUSES,
    PRODUCT_LOOP_ACTIONS,
)


VERIFICATION_STATUSES = ("not_run", "passed", "failed", "blocked")
EXECUTION_POLICY_DEFAULT_SELECTORS = ("work-unit defaults",)

DISPATCH_SECTION_RE = re.compile(
    r"^#{2,3}\s+Dispatch Hints\s*$\n(?P<body>.*?)(?=^#{1,3}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
DISPATCH_SUBSECTION_RE = re.compile(
    r"^#{3,4}\s+(?P<label>[^\n#]+?)\s*$\n(?P<body>.*?)(?=^#{3,4}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
DISPATCH_LINE_RE = re.compile(
    r"^\s*-\s*(?:(?P<action>[A-Za-z][\w-]*)\s+)?"
    r"(?P<label>preferred executors|allowed executors|fallback executors|disabled executors|required capabilities)"
    r"\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
EXECUTION_POLICY_SECTION_RE = re.compile(
    r"^#{2,3}\s+Execution Policy\s*$\n(?P<body>.*?)(?=^#{1,3}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
EXECUTION_POLICY_LINE_RE = re.compile(r"^\s*-\s*(?P<selector>[^:]+?)\s*:\s*(?P<value>.+?)\s*$", re.MULTILINE)
EXECUTOR_ASSIGNMENT_RE = re.compile(r"(?:^|[,;])\s*executor\s*=\s*(?P<value>`[^`]+`|[^,;]+)", re.IGNORECASE)
CLOSEOUT_LITERAL_RE = re.compile(
    r"(?:^|[{,\n])\s*[\"']?(?P<field>terminal_status|verification_status|blocker_class)[\"']?\s*[:=]\s*"
    r"(?P<value>`[^`]+`|\"[^\"]+\"|'[^']+'|[^,\n}]+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ValidationFinding:
    field_path: str
    literal: str
    allowed_values: tuple[str, ...]
    suggested_fix: str


def validate_plan_dispatch_hints(
    plan_text: str,
    *,
    dispatch_capabilities: Iterable[str] | None = None,
    executors: Iterable[str] | None = None,
    product_loop_actions: Iterable[str] | None = None,
) -> list[ValidationFinding]:
    """Validate planner-emitted protocol literals without mutating state."""

    dispatch_capabilities = tuple(dispatch_capabilities or DISPATCH_CAPABILITIES)
    executors = tuple(executors or EXECUTORS)
    product_loop_actions = tuple(product_loop_actions or PRODUCT_LOOP_ACTIONS)
    text = str(plan_text or "")
    findings: list[ValidationFinding] = []

    findings.extend(_validate_dispatch_hints(text, dispatch_capabilities, executors, product_loop_actions))
    findings.extend(_validate_execution_policy(text, executors, product_loop_actions))
    findings.extend(_validate_closeout_literals(text))
    return findings


def _validate_dispatch_hints(
    text: str,
    dispatch_capabilities: tuple[str, ...],
    executors: tuple[str, ...],
    product_loop_actions: tuple[str, ...],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for section in DISPATCH_SECTION_RE.finditer(text):
        bodies = [("default", section.group("body"))]
        bodies.extend((m.group("label").strip().lower(), m.group("body")) for m in DISPATCH_SUBSECTION_RE.finditer(section.group("body")))
        for bucket, body in bodies:
            if bucket not in {"default", "roadmap default", "plan default"} and bucket not in product_loop_actions:
                findings.append(_finding("dispatch_hints.selector", bucket, product_loop_actions))
            for line in DISPATCH_LINE_RE.finditer(body):
                action = (line.group("action") or bucket).strip().lower()
                if action not in {"default", "roadmap default", "plan default"} and action not in product_loop_actions:
                    findings.append(_finding("dispatch_hints.selector", action, product_loop_actions))
                label = line.group("label").strip().lower().replace(" ", "_")
                values = _split_literals(line.group("value"))
                allowed = dispatch_capabilities if label == "required_capabilities" else executors
                for index, literal in enumerate(values):
                    if literal not in allowed:
                        findings.append(_finding(f"dispatch_hints.{label}[{index}]", literal, allowed))
    return _dedupe(findings)


def _validate_execution_policy(
    text: str,
    executors: tuple[str, ...],
    product_loop_actions: tuple[str, ...],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    allowed_selectors = (*EXECUTION_POLICY_DEFAULT_SELECTORS, *product_loop_actions, "SL-<N>")
    for section in EXECUTION_POLICY_SECTION_RE.finditer(text):
        for line in EXECUTION_POLICY_LINE_RE.finditer(section.group("body")):
            selector = _clean_literal(line.group("selector"))
            selector_key = selector.lower()
            if not (
                selector_key in EXECUTION_POLICY_DEFAULT_SELECTORS
                or selector_key in product_loop_actions
                or re.fullmatch(r"SL-\d+[A-Z]?", selector, re.IGNORECASE)
            ):
                findings.append(_finding("execution_policy.selector", selector, allowed_selectors))
            for assignment in EXECUTOR_ASSIGNMENT_RE.finditer(line.group("value")):
                literal = _clean_literal(assignment.group("value"))
                if literal not in executors:
                    findings.append(_finding(f"execution_policy.{selector}.executor", literal, executors))
    return _dedupe(findings)


def _validate_closeout_literals(text: str) -> list[ValidationFinding]:
    allowed_by_field = {
        "terminal_status": PHASE_STATUSES,
        "verification_status": VERIFICATION_STATUSES,
        "blocker_class": (*BLOCKER_CLASSES, "none"),
    }
    findings: list[ValidationFinding] = []
    for match in CLOSEOUT_LITERAL_RE.finditer(text):
        field = match.group("field")
        literal = _clean_literal(match.group("value"))
        if literal in {"null", "None"}:
            continue
        allowed = allowed_by_field[field]
        if literal not in allowed:
            findings.append(_finding(f"closeout.{field}", literal, allowed))
    return _dedupe(findings)


def _split_literals(value: str) -> list[str]:
    return [literal for literal in (_clean_literal(part) for part in value.split(",")) if literal]


def _clean_literal(value: str) -> str:
    literal = str(value or "").strip().rstrip(",")
    literal = literal.split(" #", 1)[0].strip()
    if (literal.startswith("`") and literal.endswith("`")) or (literal.startswith('"') and literal.endswith('"')) or (
        literal.startswith("'") and literal.endswith("'")
    ):
        literal = literal[1:-1].strip()
    return literal


def _finding(field_path: str, literal: str, allowed_values: Iterable[str]) -> ValidationFinding:
    allowed = tuple(allowed_values)
    return ValidationFinding(
        field_path=field_path,
        literal=literal,
        allowed_values=allowed,
        suggested_fix=f"Use one of: {', '.join(allowed)}",
    )


def _dedupe(findings: Iterable[ValidationFinding]) -> list[ValidationFinding]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ValidationFinding] = []
    for finding in findings:
        key = (finding.field_path, finding.literal)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped
