"""Regression test for agent-harness#219(b-i): non-zero suite/command exit is
authoritative over an executor's self-asserted ``verification_status: passed``.

The per-command exit codes in the VerificationResult are ground truth. A red
suite must fail closed — it is never softened to a warning by
``PHASE_LOOP_VERIFY_ENFORCE=warn``. Two enforcement sites carry this:

* the closeout evidence gate (``_apply_verification_evidence_gate``), and
* the runner-owned verification reduction (``_runner_verification_fails_closed``).

"Can it fail?" bar:
* Closeout: under an explicit ``PHASE_LOOP_VERIFY_ENFORCE=warn``, the pre-fix
  gate downgrades a validated non-zero artifact to a warning and the phase stays
  ``passed``/``complete``. With the fix, a ``nonzero_exit`` validation forces the
  hard path and the phase blocks — verified below by flipping the observable
  ``verification.status`` from the self-asserted ``passed``.
* Runner: the pre-fix reduction blocked only when the runner enforcement mode was
  ``hard`` (default ``warn``); ``_runner_verification_fails_closed`` returns
  ``True`` for a ``nonzero_exit`` result even under the default ``warn``.
"""

from __future__ import annotations

from pathlib import Path

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.runner import _runner_verification_fails_closed
from phase_loop_runtime.verification_evidence import run_verification


_MINIMAL_PLAN = """---
phase_loop_plan_version: 1
phase: BUILD
---
# BUILD

## Lanes

### SL-0 - BUILD
- **Owned files**: `src/build.py`
"""


def _write_plan(tmp_path: Path) -> Path:
    plan = tmp_path / "phase-plan-v1-BUILD.md"
    plan.write_text(_MINIMAL_PLAN, encoding="utf-8")
    return plan


def test_nonzero_suite_command_fails_closed(tmp_path, monkeypatch):
    # Explicit warn: the softest operator posture. The fix must still fail closed
    # on a red suite (that is the whole point of #219(b-i)).
    monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "warn")

    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".phase-loop" / "run"
    # A real VerificationResult whose single command exits non-zero.
    result = run_verification(
        repo,
        run_dir,
        commands=[["python3", "-c", "import sys; sys.exit(3)"]],
        suite_command=None,
        env_refresh=None,
        timeout_s=60.0,
    )
    assert result.commands[0].exit_code == 3

    plan = _write_plan(tmp_path)
    payload = build_phase_loop_closeout(
        phase_alias="BUILD",
        plan_path=plan,
        # The executor SELF-ASSERTS passed/complete despite the red suite.
        terminal_summary={
            "terminal_status": "complete",
            "verification_status": "passed",
            "artifact_paths": {"root": str(run_dir)},
        },
    )

    # The self-assertion is preserved for audit ...
    assert payload["verification"]["agent_reported_verification_status"] == "passed"
    # ... but the effective status is forced failed/blocked and the phase is not
    # complete, overriding the self-report under the soft (warn) posture.
    assert payload["verification"]["status"] in {"failed", "blocked"}
    assert payload["terminal_status"] != "complete"


def test_runner_reduction_blocks_nonzero_exit_under_default_warn(monkeypatch):
    # Default (unset) runner enforcement is warn. The pre-fix reduction required
    # `hard` to block on ANY finding; the fix makes nonzero_exit fail closed
    # regardless.
    monkeypatch.delenv("PHASE_LOOP_VERIFY_ENFORCE", raising=False)

    nonzero = {"ok": False, "code": "nonzero_exit", "validation": {"code": "nonzero_exit"}}
    assert _runner_verification_fails_closed(nonzero) is True

    # Softer evidence-integrity findings still respect warn (do not block under
    # the default), so warn continues to mean warn for non-exit findings.
    soft = {"ok": False, "code": "log_sha256_mismatch", "validation": {"code": "log_sha256_mismatch"}}
    assert _runner_verification_fails_closed(soft) is False

    # ... but under explicit hard they block, and an ok result never blocks.
    monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "hard")
    assert _runner_verification_fails_closed(soft) is True
    assert _runner_verification_fails_closed({"ok": True, "code": "ok"}) is False
