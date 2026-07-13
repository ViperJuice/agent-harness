"""Bounded, credential-stripped adapter execution primitives."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from phase_loop_runtime.convergence.contracts import AdmissionRequest
from phase_loop_runtime.train_ledger import ConvergenceResultEnvelope, ConvergenceResultStatus

_DENIED_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")
_ALLOWED_ACTIONS = frozenset({"execute", "repair", "review"})

@dataclass(frozen=True)
class AdapterExecutionRequest:
    attempt_id: str
    admission: AdmissionRequest
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    allowed_action: str
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or self.timeout_seconds <= 0 or self.allowed_action not in _ALLOWED_ACTIONS:
            raise ValueError("adapter request is outside bounded execution contract")
        if self.admission.attempt_id != self.attempt_id:
            raise ValueError("adapter request must preserve admission attempt id")


def run_bounded(request: AdapterExecutionRequest, *, expected_prefix: str) -> ConvergenceResultEnvelope:
    if not request.argv[0].startswith(expected_prefix):
        return ConvergenceResultEnvelope(ConvergenceResultStatus.BLOCKED, request.attempt_id, "adapter command outside provider bounds")
    env = {key: value for key, value in os.environ.items() if key not in _DENIED_ENV}
    try:
        result = subprocess.run(request.argv, cwd=request.cwd, env=env, text=True, capture_output=True, timeout=request.timeout_seconds, start_new_session=True, check=False)
    except subprocess.TimeoutExpired:
        return ConvergenceResultEnvelope(ConvergenceResultStatus.DEGRADED, request.attempt_id, "adapter timed out")
    except OSError as exc:
        return ConvergenceResultEnvelope(ConvergenceResultStatus.FAILED, request.attempt_id, str(exc)[:256])
    detail = (result.stdout or result.stderr or "")[:1024]
    if result.returncode:
        return ConvergenceResultEnvelope(ConvergenceResultStatus.FAILED, request.attempt_id, detail)
    try:
        payload = json.loads(result.stdout)
        status = ConvergenceResultStatus(payload.get("status", "completed"))
    except (ValueError, json.JSONDecodeError):
        status = ConvergenceResultStatus.COMPLETED
    return ConvergenceResultEnvelope(status, request.attempt_id, detail)
