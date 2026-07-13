import json
from pathlib import Path

from phase_loop_runtime import convergence


FIXTURE = Path(__file__).parent / "fixtures" / "convergence" / "freeze-metadata.json"


def test_import_surface_exposes_every_freeze_contract():
    for name in (
        "CoordinatorEvent", "ConvergenceResultEnvelope", "PROVIDER_COMPLETION_CLASSIFICATIONS",
        "AdmissionRequest", "BrokerRequest", "AuthoritySource", "InvalidationTrigger",
        "ResourceIsolationDecision", "evaluate_resource_isolation",
    ):
        assert hasattr(convergence, name)


def test_metadata_only_fixtures_have_current_provenance_and_required_fail_closed_cases():
    payload = json.loads(FIXTURE.read_text())
    provenance = payload["provenance"]
    assert provenance["redaction_posture"] == "metadata_only"
    assert provenance["repository_head"]
    assert len(provenance["roadmap_digest"]) == 64
    assert provenance["unavailable_review_sources"]
    cases = {item["case"] for item in payload["fixtures"]}
    assert {
        "forged_completion_evidence", "malformed_result_envelope", "capability_overclaim",
        "stale_delayed_seat_write", "mixed_version_envelope", "action_outside_bounds", "crash",
        "partition", "stale_worker", "delayed_commit", "exact_head", "degraded_seat", "ambiguous_outcome",
    } <= cases
    assert {item["expected"] for item in payload["fixtures"]} == {"fail_closed"}
    assert "credential" not in json.dumps(payload).lower()


def test_baseline_is_explicitly_unavailable_instead_of_fabricated():
    baseline = json.loads(FIXTURE.read_text())["baseline"]
    for measurement in baseline.values():
        assert measurement["status"] == "unavailable"
        assert measurement["reason"]
