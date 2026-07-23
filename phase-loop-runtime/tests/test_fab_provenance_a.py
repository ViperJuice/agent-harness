"""FAB Lane A (Consiliency/agent-harness#191) — provenance schema, hash chain, and
trust root. Deliberately UNMARKED (no ``dotfiles_integration``), so CI's
``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2 lesson)."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import fab_provenance as fp


def _content_ref(byte: str) -> str:
    return f"sha256:{byte * 64}"


def _make_base() -> fp.BaseBinding:
    return fp.BaseBinding(
        ref_identity="github.com/Consiliency/agent-harness#refs/heads/main",
        base_sha="a" * 40,
    )


def _make_boundary_manifest() -> fp.BoundaryManifestRef:
    return fp.BoundaryManifestRef(path=".advisor-board/boundaries.toml", source_rev="a" * 40, digest="b" * 64)


def _make_candidate(*, patch_digest: str | None = "f" * 64) -> fp.CandidateRecord:
    scope = fp.ReviewScope(mode="whole-patch", reviewed_material_digest="c" * 64, covers_patch_digest=patch_digest)
    return fp.CandidateRecord(head_sha="e" * 40, review_scope=scope, patch_digest=patch_digest)


def _make_seat() -> fp.ProvenanceSeat:
    return fp.ProvenanceSeat(
        seat_key="codex:gpt-5.6-sol:high",
        vendor_leg="codex",
        required=True,
        status="ok",
        epoch=3,
        artifact_digest="1" * 64,
        evidence_digest="2" * 64,
        verdict="AGREE",
        finding_ids=("f1",),
    )


def _make_finding() -> fp.Finding:
    return fp.Finding(id="f1", severity="block", status="clean", path_scope=("a/b.py",), body_ref=_content_ref("0"))


def _make_verification_evidence_ref() -> fp.VerificationEvidenceRef:
    return fp.VerificationEvidenceRef(
        kind="runner_verification_json",
        artifact_seal="3" * 64,
        path_ref=".phase-loop/runs/x/verification.json",
    )


def _make_material_digest() -> fp.MaterialDigest:
    return fp.MaterialDigest(ref="/tmp/x", sha256="4" * 64)


def _build_artifact(**overrides) -> fp.ReviewProvenanceArtifact:
    kwargs = dict(
        repo="github.com/Consiliency/agent-harness",
        base=_make_base(),
        boundary_manifest=_make_boundary_manifest(),
        candidate=_make_candidate(),
        seats=(_make_seat(),),
        findings=(_make_finding(),),
        verification_evidence=(_make_verification_evidence_ref(),),
        material_digests=(_make_material_digest(),),
    )
    kwargs.update(overrides)
    return fp.ReviewProvenanceArtifact.build(**kwargs)


def _build_delta_chain(candidate: fp.CandidateRecord, boundary_manifest: fp.BoundaryManifestRef, c0: str):
    delta_scope = fp.ReviewScope(mode="delta-only")
    d1 = fp.DeltaReviewRecord.build(
        policy=boundary_manifest.to_dict(),
        review_scope=delta_scope,
        material_digests=(),
        parent_digest=candidate.patch_digest,
        parent_chain_digest=c0,
        delta_head_sha="h1" + "0" * 38,
        delta_changed_paths=("a/b.py",),
        delta_commits=("c1" + "0" * 38,),
        resolved_finding_ids=("f1",),
        carried_forward_finding_ids=(),
        reopened_finding_ids=(),
        resulting_head_digest="p1" + "0" * 62,
        status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        escalation=fp.Escalation(required=False),
    )
    d2 = fp.DeltaReviewRecord.build(
        policy=boundary_manifest.to_dict(),
        review_scope=delta_scope,
        material_digests=(),
        parent_digest=d1.resulting_head_digest,
        parent_chain_digest=d1.chain_digest,
        delta_head_sha="h2" + "0" * 38,
        delta_changed_paths=("a/c.py",),
        delta_commits=("c2" + "0" * 38,),
        resolved_finding_ids=(),
        carried_forward_finding_ids=("f1",),
        reopened_finding_ids=(),
        resulting_head_digest="p2" + "0" * 62,
        status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        escalation=fp.Escalation(required=False),
    )
    return d1, d2


class RoundTripTest(unittest.TestCase):
    def test_provenance_artifact_round_trips_and_schema_is_exact(self):
        art = _build_artifact()
        self.assertEqual(art.schema, "fab.review-provenance.v2")
        text = art.to_json()
        loaded = fp.ReviewProvenanceArtifact.from_json(text)
        self.assertEqual(loaded, art)
        # deterministic re-encode
        self.assertEqual(loaded.to_json(), text)

    def test_delta_review_record_round_trips_and_schema_is_exact(self):
        candidate = _make_candidate()
        bm = _make_boundary_manifest()
        base = _make_base()
        findings = (_make_finding(),)
        c0 = fp.compute_round_chain_digest(
            policy=bm.to_dict(),
            review_scope=candidate.review_scope.to_dict(),
            material_digests=[],
            findings=[f.to_dict() for f in findings],
            parent_chain_digest=None,
            base_binding={"repo": "x", "base": base.to_dict()},
        )
        d1, _d2 = _build_delta_chain(candidate, bm, c0)
        self.assertEqual(d1.schema, "fab.delta-review")
        round_tripped = fp.DeltaReviewRecord.from_dict(d1.to_dict())
        self.assertEqual(round_tripped, d1)

    def test_gate_status_round_trips_and_schema_is_exact(self):
        status = fp.GateStatus(
            reviewed_sha="e" * 40,
            prior_review_digest="f" * 64,
            chain_digest="g" * 64,
            deltas=(fp.GateDeltaEntry(delta_head_sha="h" * 40, delta_digest="i" * 64, status=fp.DELTA_STATUS_REVIEWED_CLEAN),),
            final_pr_head_sha="j" * 40,
            equivalence_verified=fp.EquivalenceVerified(
                result=fp.EQUIVALENCE_EQUIVALENT,
                candidate_head_sha="e" * 40,
                delta_head_shas=("h" * 40,),
                expected_head_digest="k" * 64,
                observed_head_digest="k" * 64,
                base_sha="a" * 40,
                reason=None,
            ),
            carried_forward_findings=("f1",),
            re_reviewed_findings=("f2",),
            escalation=fp.Escalation(required=False, trigger=None),
            waiver=None,
            status=fp.GATE_STATUS_PASS,
        )
        self.assertEqual(status.schema, "fab.gate-status.v2")
        text = status.to_json()
        loaded = fp.GateStatus.from_json(text)
        self.assertEqual(loaded, status)
        self.assertEqual(loaded.to_json(), text)

    def test_gate_status_degenerate_exact_head_case(self):
        """design §8/acceptance criterion 6: exact-head behavior (empty deltas,
        candidate head == live head) is the degenerate supported case."""
        status = fp.GateStatus(reviewed_sha="e" * 40, status=fp.GATE_STATUS_BLOCK)
        self.assertEqual(status.deltas, ())
        text = status.to_json()
        self.assertEqual(fp.GateStatus.from_json(text), status)


class ArtifactDigestSelfExclusionTest(unittest.TestCase):
    def test_recompute_matches_and_only_other_field_mutation_changes_it(self):
        art = _build_artifact()
        recomputed = fp._artifact_self_digest(art)
        self.assertEqual(recomputed, art.artifact_digest)

        # Mutating another field changes the digest.
        mutated = dataclasses.replace(art, candidate=_make_candidate(patch_digest="9" * 64))
        self.assertNotEqual(fp._artifact_self_digest(mutated), art.artifact_digest)

        # Mutating ONLY artifact_digest doesn't affect recomputation (self-excluded).
        mutated_digest_only = dataclasses.replace(art, artifact_digest="deadbeef" * 8)
        self.assertEqual(fp._artifact_self_digest(mutated_digest_only), recomputed)

    def test_loading_a_field_edited_artifact_fails_closed(self):
        art = _build_artifact()
        data = json.loads(art.to_json())
        data["candidate"]["head_sha"] = "tampered" + "0" * 32
        tampered_text = json.dumps(data)
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(tampered_text)


class HashChainTest(unittest.TestCase):
    def _build_chained_artifact(self):
        base = _make_base()
        bm = _make_boundary_manifest()
        candidate = _make_candidate()
        findings = (_make_finding(),)
        art0 = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm,
            candidate=candidate, findings=findings,
        )
        c0 = art0.compute_c0()
        d1, d2 = _build_delta_chain(candidate, bm, c0)
        art = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm,
            candidate=candidate, findings=findings, delta_chain=(d1, d2),
        )
        return art, d1, d2

    def test_valid_chain_verifies_and_every_chain_digest_recomputes(self):
        art, d1, d2 = self._build_chained_artifact()
        fp.verify_chain(art)  # must not raise
        self.assertEqual(d1.recompute_chain_digest(), d1.chain_digest)
        self.assertEqual(d2.recompute_chain_digest(), d2.chain_digest)
        self.assertEqual(art.chain_digest, d2.chain_digest)

    def test_degenerate_no_delta_chain_verifies_against_c0(self):
        art = _build_artifact()
        fp.verify_chain(art)
        self.assertEqual(art.chain_digest, art.compute_c0())

    def test_reordering_rounds_fails_verification(self):
        art, d1, d2 = self._build_chained_artifact()
        reordered = dataclasses.replace(art, delta_chain=(d2, d1))
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(reordered)

    def test_splicing_a_fabricated_clean_round_fails_verification(self):
        art, d1, _d2 = self._build_chained_artifact()
        bm = _make_boundary_manifest()
        fabricated = fp.DeltaReviewRecord.build(
            policy=bm.to_dict(),
            review_scope=fp.ReviewScope(mode="delta-only"),
            material_digests=(),
            parent_digest="fabricated" + "0" * 54,
            parent_chain_digest="fabricated_chain_digest_not_linked",
            delta_head_sha="hf" + "0" * 38,
            delta_changed_paths=("a/z.py",),
            delta_commits=("cf" + "0" * 38,),
            resolved_finding_ids=(),
            carried_forward_finding_ids=("f1",),
            reopened_finding_ids=(),
            resulting_head_digest="pf" + "0" * 62,
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            escalation=fp.Escalation(required=False),
        )
        spliced = dataclasses.replace(art, delta_chain=(d1, fabricated))
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(spliced)

    def test_breaking_parent_chain_digest_link_fails_verification(self):
        art, d1, d2 = self._build_chained_artifact()
        broken = dataclasses.replace(d2, parent_chain_digest="not-the-real-parent")
        tampered = dataclasses.replace(art, delta_chain=(d1, broken))
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(tampered)

    def test_breaking_parent_digest_link_fails_verification(self):
        art, d1, d2 = self._build_chained_artifact()
        broken = dataclasses.replace(d2, parent_digest="not-the-real-patch-digest" + "0" * 39)
        tampered = dataclasses.replace(art, delta_chain=(d1, broken))
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(tampered)

    def test_top_level_chain_digest_mismatch_fails_verification(self):
        art, d1, d2 = self._build_chained_artifact()
        tampered = dataclasses.replace(art, chain_digest="not-the-final-round-digest")
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(tampered)


class FailClosedLoadTest(unittest.TestCase):
    def test_oversize_payload_invalidates(self):
        huge = json.dumps({"schema": "fab.review-provenance.v2", "pad": "x" * (fp.MAX_PROVENANCE_ARTIFACT_BYTES + 1)})
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(huge)

    def test_oversize_gate_status_invalidates(self):
        huge = json.dumps({"schema": "fab.gate-status.v2", "pad": "x" * (fp.MAX_GATE_STATUS_BYTES + 1)})
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.GateStatus.from_json(huge)

    def test_malformed_json_invalidates(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json("{not valid json")

    def test_json_dumps_failure_invalidates(self):
        art = _build_artifact()
        # `policy` is intentionally Any-typed (a later lane's boundary-manifest
        # reference); slip in a non-JSON-serializable object to exercise the
        # fail-closed wrap around json.dumps at serialize time.
        base = _make_base()
        bm = _make_boundary_manifest()
        candidate = _make_candidate()
        c0 = fp.compute_round_chain_digest(
            policy=bm.to_dict(), review_scope=candidate.review_scope.to_dict(),
            material_digests=[], findings=[], parent_chain_digest=None,
            base_binding={"repo": "x", "base": base.to_dict()},
        )
        d1, _d2 = _build_delta_chain(candidate, bm, c0)
        poisoned = dataclasses.replace(d1, policy=object())  # not JSON-serializable
        poisoned_art = dataclasses.replace(art, delta_chain=(poisoned,))
        with self.assertRaises(fp.ProvenanceInvalid):
            poisoned_art.to_json()

    def test_surrogate_in_value_invalidates(self):
        payload = {
            "schema": "fab.review-provenance.v2",
            "repo": "\ud800",  # lone surrogate: valid JSON escape, unencodable UTF-8
            "base": {"ref_identity": "x", "base_sha": "a" * 40},
            "boundary_manifest": {"path": "x", "source_rev": "a" * 40, "digest": "b" * 64},
            "candidate": {"head_sha": "e" * 40, "patch_digest": None, "review_scope": {"mode": "whole-patch"}},
            "chain_digest": "c" * 64,
            "artifact_digest": "d" * 64,
        }
        text = json.dumps(payload)
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(text)

    def test_unknown_review_scope_mode_invalidates(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewScope(mode="something-else")

    def test_unknown_delta_status_invalidates(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.DeltaReviewRecord(
                review_scope=fp.ReviewScope(mode="delta-only"),
                parent_digest=None,
                parent_chain_digest=None,
                chain_digest="x",
                delta_head_sha="h" * 40,
                status="not-a-real-status",
                escalation=fp.Escalation(required=False),
            )

    def test_unknown_gate_status_invalidates(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.GateStatus(reviewed_sha="e" * 40, status="not-pass-or-block")


class TrustRootTest(unittest.TestCase):
    def test_write_then_read_by_run_id_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            art = _build_artifact()
            path = fp.write_provenance(repo, run_id, art)
            self.assertTrue(path.exists())
            loaded = fp.read_provenance(repo, run_id)
            self.assertEqual(loaded, art)

    def test_read_missing_run_id_raises_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            with self.assertRaises(fp.ProvenanceNotFound):
                fp.read_provenance(repo, "no-such-run")

    def test_client_supplied_provenance_is_refused_as_sole_authoritative_input(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            art = _build_artifact()
            authoritative_path = fp.write_provenance(repo, run_id, art)

            # A caller-supplied blob (e.g. read from a PR branch checkout) with
            # DIFFERENT content, placed at a non-run-store path.
            rogue_art = _build_artifact(candidate=_make_candidate(patch_digest="9" * 64))
            rogue_path = repo / "pr-branch-provenance.json"
            rogue_path.write_text(rogue_art.to_json(), encoding="utf-8")

            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reject_client_supplied_provenance(rogue_path, repo, run_id)
            # The authoritative path is accepted.
            fp.reject_client_supplied_provenance(authoritative_path, repo, run_id)

            # And crucially: read_provenance has NO parameter to accept the
            # rogue blob at all — it always returns the run-store copy,
            # untouched by the rogue file's existence or content.
            reread = fp.read_provenance(repo, run_id)
            self.assertEqual(reread, art)
            self.assertNotEqual(reread, rogue_art)

    def test_run_id_path_traversal_is_rejected_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for hostile in ("../../etc/passwd", "..", "a/b", "", "x" * 300):
                with self.assertRaises(fp.ProvenanceInvalid):
                    fp.provenance_dir_for_run(repo, hostile)


class ImmutableMaterialTest(unittest.TestCase):
    def test_snapshot_and_rehash_equality(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"reviewed content" * 5000)  # exceed one hash chunk easily if small

            digests = fp.snapshot_material(repo, run_id, [str(source)])
            self.assertEqual(len(digests), 1)
            # Re-verifying immediately (no edit) must pass.
            fp.reverify_material(repo, run_id, digests)

    def test_editing_underlying_file_after_snapshot_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"original reviewed bytes")

            digests = fp.snapshot_material(repo, run_id, [str(source)])
            source.write_bytes(b"an edit made AFTER review")

            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reverify_material(repo, run_id, digests)

    def test_missing_context_ref_fails_closed_not_silent_empty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.snapshot_material(repo, run_id, [str(repo / "does-not-exist.txt")])

    def test_missing_snapshot_at_reverify_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"data")
            fake_entry = fp.MaterialDigest(ref=str(source), sha256="0" * 64)  # never snapshotted
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reverify_material(repo, run_id, [fake_entry])


class MetadataOnlyTest(unittest.TestCase):
    """Acceptance criterion 5 (design §9): provenance auditable + metadata-only."""

    def test_finding_body_ref_must_be_a_content_ref_digest(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.Finding(
                id="f1", severity="block", status="clean",
                body_ref="This finding says the endpoint is missing auth.",  # raw prose
            )

    def test_finding_body_ref_none_is_allowed(self):
        finding = fp.Finding(id="f1", severity="block", status="clean", body_ref=None)
        self.assertIsNone(finding.body_ref)

    def test_finding_body_ref_wrong_shape_rejected(self):
        for bad in ("deadbeef", "sha256:tooshort", "sha1:" + "0" * 40, "sha256:" + "g" * 64):
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.Finding(id="f1", severity="block", status="clean", body_ref=bad)

    def test_seat_field_names_match_seat_outcome_record(self):
        """design task brief: seat sub-record MUST use SeatOutcomeRecord's field
        names (seat_key, vendor_leg, required, status, epoch, artifact_digest,
        evidence_digest) so Lane D's §6.3 cross-check can compare directly."""
        from phase_loop_runtime.panel_invoker import SeatOutcomeRecord

        seat_outcome_fields = {f.name for f in dataclasses.fields(SeatOutcomeRecord)}
        provenance_seat_fields = {f.name for f in dataclasses.fields(fp.ProvenanceSeat)}
        shared = {"seat_key", "vendor_leg", "required", "status", "epoch", "artifact_digest", "evidence_digest"}
        self.assertTrue(shared.issubset(seat_outcome_fields))
        self.assertTrue(shared.issubset(provenance_seat_fields))


if __name__ == "__main__":
    unittest.main()
