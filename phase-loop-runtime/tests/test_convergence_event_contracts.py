from pathlib import Path

import pytest

from phase_loop_runtime.train_ledger import (
    CoordinatorEvent,
    CoordinatorEventKind,
    ConvergenceResultEnvelope,
    ConvergenceResultStatus,
    LedgerRecord,
    append_record,
    normalize_legacy_ledger_record,
    read_ledger,
)


def test_event_schema_is_versioned_and_intent_is_distinct_from_outcome():
    expected = {
        "kind", "train_id", "node_id", "roadmap_path", "roadmap_digest", "workspace_id",
        "branch", "base_ref", "base_sha", "head_sha", "phase", "action", "owned_paths",
        "executor", "model", "upstream_dep_shas", "verification_artifact", "verification_digest",
        "seat_outcomes", "pr_identity", "merge_sha", "release_identity", "attempt_id", "epoch",
        "timestamp", "blocker_reason", "event_schema_version", "transition_model_version",
        "invalidation_model_version",
    }
    assert set(CoordinatorEvent.__dataclass_fields__) == expected
    assert {item.value for item in CoordinatorEventKind} == {"intent", "outcome"}
    assert CoordinatorEventKind.INTENT is not CoordinatorEventKind.OUTCOME


def test_result_statuses_are_exact_and_unknown_values_are_rejected():
    assert {item.value for item in ConvergenceResultStatus} == {
        "completed", "verified", "blocked", "needs_clarification", "degraded", "failed"
    }
    with pytest.raises(ValueError):
        ConvergenceResultStatus("unknown")
    assert ConvergenceResultEnvelope(ConvergenceResultStatus.VERIFIED, "attempt-1").status.value == "verified"


def test_legacy_mapping_preserves_reader_writer_behavior_without_false_evidence(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    record = LedgerRecord(node_id="repo/plan", status="pr_open", branch="feature/x", head_sha="abc")
    append_record(path, record)
    assert read_ledger(path)[record.node_id].head_sha == "abc"
    normalized = normalize_legacy_ledger_record(record)
    assert normalized.kind is CoordinatorEventKind.OUTCOME
    assert normalized.roadmap_digest is None
    assert normalized.verification_artifact is None
    assert normalized.merge_sha is None
    assert normalized.epoch is None
