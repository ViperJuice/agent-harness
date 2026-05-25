from __future__ import annotations

import os


def branchgov_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_BRANCHGOV_ENABLE") != "false"


def trust_executor_evidence_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE") != "false"


def allow_lane_ir_override_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE") != "false"


def dispatch_lock_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_DISPATCH_LOCK") != "false"


def parallel_dispatch_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_PARALLEL_DISPATCH") == "true"
