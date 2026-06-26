from __future__ import annotations

import os


def branchgov_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_BRANCHGOV_ENABLE") != "false"


def branchgov_override_explicit() -> bool:
    """True only when branch governance is EXPLICITLY opted into (issue #83).

    Distinct from ``branchgov_enabled()`` (which is True for both the unset
    default and ``"true"``). The default-on case is exactly the bug surface: a
    locally-committed roadmap on the operator's branch would be orphaned by the
    convention-branch switch. The orphan-refusal preflight must therefore key on
    this *explicit* signal — set by ``--allow-branchgov`` (which exports
    ``PHASE_LOOP_BRANCHGOV_ENABLE=true``) — so the unset default still refuses
    and only a deliberate ``"true"`` (or ``--allow-branchgov``) switches anyway.
    """
    return os.environ.get("PHASE_LOOP_BRANCHGOV_ENABLE") == "true"


def trust_executor_evidence_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE") != "false"


def allow_lane_ir_override_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE") != "false"


def dispatch_lock_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_DISPATCH_LOCK") != "false"


def parallel_dispatch_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_PARALLEL_DISPATCH") != "false"


def reconcile_git_reality_enabled() -> bool:
    # SAFE CUTOVER: defaults OFF (opt-in), unlike the flags above. The active body
    # wires into classify_phase — the universal classifier — so on our own runtime
    # the feature lands inert and is flipped on deliberately after validation.
    return os.environ.get("PHASE_LOOP_RECONCILE_GIT_REALITY") == "true"


def concurrent_real_exec_integration_enabled() -> bool:
    # SAFE CUTOVER: defaults OFF (opt-in). Switches the concurrent scheduler's
    # worktree integration from the committed-only merge (integrate_phase_worktree)
    # to dirty-work transport (transfer_phase_worktree_dirty), which lands a real
    # executor's uncommitted phase-owned work onto the pipeline branch so the
    # parent closeout commits it. Only active under `--phase-scheduler concurrent`
    # (itself opt-in); flipped on deliberately after a real concurrent run
    # validates the transport on this runtime's own repo.
    return os.environ.get("PHASE_LOOP_CONCURRENT_REAL_EXEC") == "true"
