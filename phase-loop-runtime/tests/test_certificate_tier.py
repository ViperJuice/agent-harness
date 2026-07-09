"""CERT / SCHEMA tier -- ``phase_loop_runtime.conformance.validate_certificate``.

The rung above ``hash-checked``: structural conformance of a DECLARED parity
certificate to the contract-distributed ``certificate`` schema. NOT authority /
provenance / signing (that stays gp). These tests prove:

  * a valid certificate PASSES (which requires the cross-file ``result-state``
    ``$ref`` to have resolved -- the certificate's ``overall_result_state`` /
    ``dimension_results[].result_state`` are checked against the referenced
    result-state enum),
  * a bad ``result_state`` enum value (via that same cross-file ref) is REJECTED
    -- the discriminating test that the ref actually resolved AND is enforced,
  * a missing required field is REJECTED,
  * a contract without the cert schema degrades to a neutral ``skipped`` verdict
    (no crash), matching the ``available()`` pattern.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime.conformance import (
    certificate_schema_available,
    validate_certificate,
)
from phase_loop_runtime.conformance import certificate_tier


def _dim(dimension: str, state: str) -> dict:
    return {"dimension": dimension, "result_state": state}


def _valid_certificate() -> dict:
    """A minimal certificate that satisfies every required field + the
    result-state / parity-dimension ref closure. Synthesized from the schema's
    required-field list (no digest math -- structural tier only)."""
    return {
        "schema_version": "1",
        "projection_algo_version": "p1",
        "canon_version": "v2",
        "idmodel_version": "v1",
        "kind_alignment_version": "v1",
        "permitted_freedom_vocab_version": "v1",
        "ec_revision_id": "rev-1",
        "spec_revision_digest": "a" * 64,
        "desired_graph_digest": "b" * 64,
        "ec_digest": "c" * 64,
        "code_head_sha": "d" * 40,
        "overall_result_state": "pass",
        "dimension_results": [
            _dim("completeness", "pass"),
            _dim("soundness", "pass"),
            _dim("closure", "pass"),
            _dim("prohibition", "pass"),
            _dim("revision_alignment", "pass"),
        ],
        "findings_ref": "sha256:ff",
        "digest": "e" * 64,
    }


@unittest.skipUnless(
    certificate_schema_available(),
    "installed consiliency_contract does not distribute the certificate schema",
)
class CertificateTierWithSchemaTest(unittest.TestCase):
    def test_valid_certificate_passes(self):
        verdict = validate_certificate(_valid_certificate())
        self.assertEqual(verdict["status"], "passed", verdict["findings"])
        self.assertTrue(verdict["consent"])
        self.assertEqual(verdict["findings"], [])
        self.assertEqual(verdict["maturity"], "cert-schema")

    def test_bad_result_state_enum_via_ref_is_rejected(self):
        # `green` is not a contract result_state -- proves the cross-file
        # `result-state.schema.json#/$defs/result_state` ref resolved AND is
        # enforced (a mis-wired registry would either raise or silently pass).
        cert = _valid_certificate()
        cert["overall_result_state"] = "green"
        verdict = validate_certificate(cert)
        self.assertEqual(verdict["status"], "blocked")
        self.assertTrue(
            any(f["ref"] == "overall_result_state" for f in verdict["findings"]),
            verdict["findings"],
        )
        self.assertTrue(
            any("green" in f["message"] for f in verdict["findings"]),
            verdict["findings"],
        )

    def test_bad_dimension_result_state_via_nested_ref_is_rejected(self):
        cert = _valid_certificate()
        cert["dimension_results"][0]["result_state"] = "green"
        verdict = validate_certificate(cert)
        self.assertEqual(verdict["status"], "blocked")

    def test_missing_required_field_is_rejected(self):
        cert = _valid_certificate()
        del cert["digest"]
        verdict = validate_certificate(cert)
        self.assertEqual(verdict["status"], "blocked")
        self.assertTrue(
            any("digest" in f["message"] for f in verdict["findings"]),
            verdict["findings"],
        )

    def test_non_object_certificate_is_rejected(self):
        verdict = validate_certificate("not-an-object")  # type: ignore[arg-type]
        self.assertEqual(verdict["status"], "blocked")
        self.assertEqual(verdict["findings"][0]["code"], "cert_not_object")


class CertificateTierDegradeTest(unittest.TestCase):
    def test_contract_absent_degrades_to_skipped(self):
        # Force the "no validator" path (contract/schema absent) without touching
        # the real import: patch the module-level builder to return None.
        original = certificate_tier._certificate_validator
        certificate_tier._certificate_validator = lambda: None
        try:
            verdict = validate_certificate(_valid_certificate())
        finally:
            certificate_tier._certificate_validator = original
        self.assertEqual(verdict["status"], "skipped")
        self.assertFalse(verdict["consent"])
        self.assertEqual(verdict["findings"], [])
        self.assertIn("note", verdict)

    def test_available_predicate_matches_validator_buildability(self):
        # available() and a buildable validator must agree (both key off the
        # same load_schema('certificate') probe).
        self.assertEqual(
            certificate_schema_available(),
            certificate_tier._certificate_validator() is not None,
        )


if __name__ == "__main__":
    unittest.main()
