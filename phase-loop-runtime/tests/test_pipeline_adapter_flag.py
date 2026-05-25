from __future__ import annotations

from phase_loop_runtime.pipeline_adapter.flag import (
    allow_lane_ir_override_enabled,
    branchgov_enabled,
    dispatch_lock_enabled,
    parallel_dispatch_enabled,
    trust_executor_evidence_enabled,
)


def test_branchgov_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("PHASE_LOOP_BRANCHGOV_ENABLE", raising=False)

    assert branchgov_enabled() is True


def test_branchgov_flag_exact_true_is_enabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")

    assert branchgov_enabled() is True


def test_branchgov_flag_exact_false_is_disabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "false")

    assert branchgov_enabled() is False


def test_branchgov_flag_non_canonical_values_are_enabled(monkeypatch):
    for value in ("True", "1", "yes", ""):
        monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", value)

        assert branchgov_enabled() is True


def test_trust_executor_evidence_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE", raising=False)

    assert trust_executor_evidence_enabled() is True


def test_trust_executor_evidence_flag_exact_true_is_enabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE", "true")

    assert trust_executor_evidence_enabled() is True


def test_trust_executor_evidence_flag_exact_false_is_disabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE", "false")

    assert trust_executor_evidence_enabled() is False


def test_trust_executor_evidence_flag_non_canonical_values_are_enabled(monkeypatch):
    for value in ("True", "1", "yes", ""):
        monkeypatch.setenv("PHASE_LOOP_TRUST_EXECUTOR_EVIDENCE", value)

        assert trust_executor_evidence_enabled() is True


def test_allow_lane_ir_override_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE", raising=False)

    assert allow_lane_ir_override_enabled() is True


def test_allow_lane_ir_override_flag_exact_true_is_enabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE", "true")

    assert allow_lane_ir_override_enabled() is True


def test_allow_lane_ir_override_flag_exact_false_is_disabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE", "false")

    assert allow_lane_ir_override_enabled() is False


def test_allow_lane_ir_override_flag_non_canonical_values_are_enabled(monkeypatch):
    for value in ("True", "1", "yes", ""):
        monkeypatch.setenv("PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE", value)

        assert allow_lane_ir_override_enabled() is True


def test_dispatch_lock_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("PHASE_LOOP_DISPATCH_LOCK", raising=False)

    assert dispatch_lock_enabled() is True


def test_dispatch_lock_flag_exact_false_is_disabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_DISPATCH_LOCK", "false")

    assert dispatch_lock_enabled() is False


def test_dispatch_lock_flag_non_canonical_values_are_enabled(monkeypatch):
    for value in ("true", "True", "1", "yes", ""):
        monkeypatch.setenv("PHASE_LOOP_DISPATCH_LOCK", value)

        assert dispatch_lock_enabled() is True


def test_parallel_dispatch_flag_unset_is_enabled(monkeypatch):
    monkeypatch.delenv("PHASE_LOOP_PARALLEL_DISPATCH", raising=False)

    assert parallel_dispatch_enabled() is True


def test_parallel_dispatch_flag_exact_false_is_disabled(monkeypatch):
    monkeypatch.setenv("PHASE_LOOP_PARALLEL_DISPATCH", "false")

    assert parallel_dispatch_enabled() is False


def test_parallel_dispatch_flag_non_canonical_values_are_enabled(monkeypatch):
    for value in ("true", "True", "1", "yes", ""):
        monkeypatch.setenv("PHASE_LOOP_PARALLEL_DISPATCH", value)

        assert parallel_dispatch_enabled() is True
