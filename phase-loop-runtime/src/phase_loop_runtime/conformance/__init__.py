"""``phase_loop_runtime.conformance`` -- the named conformance surface.

This package preserves the existing public ``.consiliency/`` conformance
imports while providing a namespace for outside-agent contract pin helpers.
"""
from __future__ import annotations

from ..consiliency_gates import (
    CONSILIENCY_GATES_ENV,
    CONSILIENCY_GATES_MODES,
    DEFAULT_CONSILIENCY_GATES_MODE,
    resolve_consiliency_gates_mode,
    scan_consiliency_gates,
)
from ..consiliency_ingest import evaluate_governance_scope
from ..git_discipline import evaluate_git_discipline, self_heal_partition

__all__ = [
    "scan_consiliency_gates",
    "resolve_consiliency_gates_mode",
    "CONSILIENCY_GATES_ENV",
    "CONSILIENCY_GATES_MODES",
    "DEFAULT_CONSILIENCY_GATES_MODE",
    "evaluate_git_discipline",
    "self_heal_partition",
    "evaluate_governance_scope",
]
