from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


IF_GATE_RE = re.compile(r"\bIF-\d+-[A-Z0-9_-]+-\d+\b")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    warning: str | None = None
    blocker_class: str | None = None
    blocker_summary: str | None = None
    expected_gates: tuple[str, ...] = ()
    produced_gates: tuple[str, ...] = ()
    missing_gates: tuple[str, ...] = ()
    unexpected_gates: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "warning": self.warning,
            "blocker_class": self.blocker_class,
            "blocker_summary": self.blocker_summary,
            "expected_gates": list(self.expected_gates),
            "produced_gates": list(self.produced_gates),
            "missing_gates": list(self.missing_gates),
            "unexpected_gates": list(self.unexpected_gates),
        }


def extract_plan_produces(plan_path: Path) -> tuple[str, ...]:
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError:
        return ()

    gates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (
            stripped.startswith("**Produces**:")
            or stripped.startswith("Produces:")
            or "**Interfaces provided**:" in stripped
        ):
            continue
        for match in IF_GATE_RE.finditer(line):
            gate = match.group(0)
            if gate not in gates:
                gates.append(gate)
    return tuple(gates)


def validate_produced_gates(plan_path: Path, closeout_payload: dict[str, Any]) -> ValidationResult:
    terminal_status = str(
        closeout_payload.get("terminal_status")
        or closeout_payload.get("automation_status")
        or ""
    )
    expected = extract_plan_produces(plan_path)
    if "produced_if_gates" not in closeout_payload:
        if terminal_status == "complete":
            return ValidationResult(
                ok=True,
                warning="produced_if_gates missing during NATIVE compatibility window",
                expected_gates=expected,
            )
        return ValidationResult(ok=True, expected_gates=expected)

    produced_raw = _normalize_gates(closeout_payload.get("produced_if_gates"))
    # Filter to canonical IF-gate tokens. Executors sometimes emit free-text
    # description sentences in produced_if_gates (e.g. when the plan declares
    # no IF gate, codex has been observed outputting
    # ["...verified...; active plan declares no interface-freeze gate"]).
    # Treat non-IF-token entries as commentary, not as produced gates.
    produced = tuple(g for g in produced_raw if IF_GATE_RE.fullmatch(g))
    non_gate_chatter = tuple(g for g in produced_raw if not IF_GATE_RE.fullmatch(g))
    missing = tuple(gate for gate in expected if gate not in produced)
    unexpected = tuple(gate for gate in produced if gate not in expected)

    # If the plan declares no IF gates, the phase is internal/tooling and
    # cannot fail the contract check. Allow with a warning if the executor
    # emitted any chatter.
    if not expected:
        warning = None
        if non_gate_chatter:
            warning = (
                "executor produced non-IF-gate strings in produced_if_gates "
                "for a plan that declares no IF gates; treated as commentary"
            )
        return ValidationResult(
            ok=True,
            warning=warning,
            expected_gates=expected,
            produced_gates=produced,
            missing_gates=missing,
            unexpected_gates=unexpected,
        )

    if terminal_status == "complete" and (not produced or missing or unexpected):
        summary = "completed closeout produced_if_gates did not match the active phase plan"
        if not produced:
            summary = "completed closeout reported zero produced_if_gates"
        return ValidationResult(
            ok=False,
            blocker_class="contract_bug",
            blocker_summary=summary,
            expected_gates=expected,
            produced_gates=produced,
            missing_gates=missing,
            unexpected_gates=unexpected,
        )

    return ValidationResult(
        ok=True,
        expected_gates=expected,
        produced_gates=produced,
        missing_gates=missing,
        unexpected_gates=unexpected,
    )


def verification_enforcement_mode(env: Mapping[str, str] | None = None) -> str:
    value = str((env or {}).get("PHASE_LOOP_VERIFY_ENFORCE") or "").strip().lower()
    return "warn" if value == "warn" else "hard"


def _normalize_gates(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_items = re.split(r"[,\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = []
    gates: list[str] = []
    for item in raw_items:
        gate = item.strip().strip("'\"")
        if gate and gate not in gates:
            gates.append(gate)
    return tuple(gates)
