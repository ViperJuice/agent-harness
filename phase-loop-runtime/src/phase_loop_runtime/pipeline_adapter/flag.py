from __future__ import annotations

import os


def branchgov_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_BRANCHGOV_ENABLE") != "false"


def trust_executor_evidence_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE") == "true"


def allow_lane_ir_override_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE") == "true"


def dispatch_lock_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_DISPATCH_LOCK") != "false"
