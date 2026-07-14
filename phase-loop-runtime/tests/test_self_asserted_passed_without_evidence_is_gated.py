"""Regression test for agent-harness#219(b-i): a non-RG governed phase that
declares an ``automation.suite_command`` cannot reach ``complete`` on a
self-asserted ``verification_status: passed`` with no VerificationResult
artifact.

Before the fix, ``_verification_evidence_required`` returned true ONLY for the
``RG`` alias or plans literally containing ``IF-0-RG-1``/``--verification-log``.
Any other governed phase with a suite command could self-assert ``passed`` and
close ``complete`` with no evidence at all. The fix broadens the requirement to
any plan that declares ``automation.suite_command``.

"Can it fail?" bar: under the default enforcement posture (closeout defaults to
``hard``), the pre-fix code treats this plan as evidence-optional → the gate
returns ``None`` → the phase closes ``complete``. With the broadening, the
missing artifact is required → the phase blocks. A control assertion confirms a
plan WITHOUT a suite command still closes clean (no over-broadening).
"""

from __future__ import annotations

from pathlib import Path

from phase_loop_runtime.closeout import build_phase_loop_closeout


_PLAN_WITH_SUITE = """---
phase_loop_plan_version: 1
phase: BUILD
automation:
  suite_command: bash -lc "python -m pytest -q"
---
# BUILD

## Lanes

### SL-0 - BUILD
- **Owned files**: `src/build.py`
"""

_PLAN_WITHOUT_SUITE = """---
phase_loop_plan_version: 1
phase: BUILD
---
# BUILD

## Lanes

### SL-0 - BUILD
- **Owned files**: `src/build.py`
"""


def _closeout_for_plan(tmp_path: Path, name: str, content: str) -> dict:
    plan = tmp_path / name
    plan.write_text(content, encoding="utf-8")
    return build_phase_loop_closeout(
        phase_alias="BUILD",
        plan_path=plan,
        # Executor self-asserts passed/complete but attaches NO verification
        # artifact (no artifact_paths).
        terminal_summary={
            "terminal_status": "complete",
            "verification_status": "passed",
        },
    )


def test_self_asserted_passed_without_evidence_is_gated(tmp_path, monkeypatch):
    # Default enforcement posture (unset → closeout defaults to hard).
    monkeypatch.delenv("PHASE_LOOP_VERIFY_ENFORCE", raising=False)

    gated = _closeout_for_plan(tmp_path, "phase-plan-v1-BUILD.md", _PLAN_WITH_SUITE)
    assert gated["terminal_status"] != "complete", (
        "a suite-command phase must not reach complete on a self-asserted pass "
        "with no verification evidence"
    )
    assert gated["verification"]["status"] in {"failed", "blocked"}

    # Control: a plan with NO suite command (and no RG markers) is still
    # evidence-optional and closes clean — the broadening is scoped.
    ungated = _closeout_for_plan(tmp_path, "phase-plan-v1-CTRL.md", _PLAN_WITHOUT_SUITE)
    assert ungated["terminal_status"] == "complete"
    assert ungated["verification"]["status"] == "passed"
