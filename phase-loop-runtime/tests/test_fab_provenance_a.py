"""FAB Lane A (Consiliency/agent-harness#191) — provenance schema, hash chain, and
trust root. Deliberately UNMARKED (no ``dotfiles_integration``), so CI's
``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2 lesson)."""

from __future__ import annotations

import dataclasses
import json
import subprocess as _subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        epoch=2,
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
        epoch=3,
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
        # standalone to_json()/from_json() (not just the dict path)
        text = d1.to_json()
        json_round_tripped = fp.DeltaReviewRecord.from_json(text)
        self.assertEqual(json_round_tripped, d1)
        self.assertEqual(json_round_tripped.to_json(), text)

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
            epoch=4,
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

    def test_reviewed_clean_delta_with_null_parent_digest_invalidates(self):
        """F2 repro: on the pre-fix module, `verify_chain` only checked
        `record.parent_digest` when it was non-null (`if ... and record.parent_digest
        is not None and ...`). A reviewed-clean delta with a POPULATED candidate
        patch_digest but parent_digest=None PASSED — violating §5.1's required
        dual-link contiguity. Must now INVALIDATE."""
        base = _make_base()
        bm = _make_boundary_manifest()
        candidate = _make_candidate(patch_digest="f" * 64)  # parent side IS recorded
        art0 = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm, candidate=candidate,
        )
        c0 = art0.compute_c0()
        unlinked = fp.DeltaReviewRecord.build(
            epoch=5,
            policy=bm.to_dict(),
            review_scope=fp.ReviewScope(mode="delta-only"),
            material_digests=(),
            parent_digest=None,  # NOT linked, despite candidate.patch_digest being populated
            parent_chain_digest=c0,
            delta_head_sha="h1" + "0" * 38,
            delta_changed_paths=("a/b.py",),
            delta_commits=("c1" + "0" * 38,),
            resolved_finding_ids=(),
            carried_forward_finding_ids=(),
            reopened_finding_ids=(),
            resulting_head_digest="p1" + "0" * 62,
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            escalation=fp.Escalation(required=False),
        )
        art = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm,
            candidate=candidate, delta_chain=(unlinked,),
        )
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(art)

    def test_reviewed_clean_delta_with_null_parent_digest_invalidates_even_without_prior_patch_digest(self):
        """Design §5.1: "A reviewed-clean delta MUST carry a linking parent_digest"
        — unconditionally, not only when the prior patch digest happens to be
        recorded."""
        base = _make_base()
        bm = _make_boundary_manifest()
        candidate = _make_candidate(patch_digest=None)  # parent side NOT recorded
        art0 = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm, candidate=candidate,
        )
        c0 = art0.compute_c0()
        unlinked = fp.DeltaReviewRecord.build(
            epoch=6,
            policy=bm.to_dict(),
            review_scope=fp.ReviewScope(mode="delta-only"),
            material_digests=(),
            parent_digest=None,
            parent_chain_digest=c0,
            delta_head_sha="h1" + "0" * 38,
            delta_changed_paths=("a/b.py",),
            delta_commits=("c1" + "0" * 38,),
            resolved_finding_ids=(),
            carried_forward_finding_ids=(),
            reopened_finding_ids=(),
            resulting_head_digest="p1" + "0" * 62,
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            escalation=fp.Escalation(required=False),
        )
        art = fp.ReviewProvenanceArtifact.build(
            repo="github.com/Consiliency/agent-harness", base=base, boundary_manifest=bm,
            candidate=candidate, delta_chain=(unlinked,),
        )
        with self.assertRaises(fp.ChainVerificationError):
            fp.verify_chain(art)


class FailClosedLoadTest(unittest.TestCase):
    def test_oversize_payload_invalidates(self):
        huge = json.dumps({"schema": "fab.review-provenance.v2", "pad": "x" * (fp.MAX_PROVENANCE_ARTIFACT_BYTES + 1)})
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(huge)

    def test_oversize_gate_status_invalidates(self):
        huge = json.dumps({"schema": "fab.gate-status.v2", "pad": "x" * (fp.MAX_GATE_STATUS_BYTES + 1)})
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.GateStatus.from_json(huge)

    def test_oversize_delta_review_record_invalidates(self):
        huge = json.dumps({"schema": "fab.delta-review", "pad": "x" * (fp.MAX_DELTA_REVIEW_RECORD_BYTES + 1)})
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.DeltaReviewRecord.from_json(huge)

    def test_oversize_run_store_provenance_rejected_before_full_read(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            path = fp.provenance_path_for_run(repo, run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x" * (fp.MAX_PROVENANCE_ARTIFACT_BYTES + 1), encoding="utf-8")
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.read_provenance(repo, run_id)

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
                epoch=2,
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

    def test_strict_load_rejects_unknown_top_level_field(self):
        """F1 repro: on the pre-fix module, an artifact JSON with an extra
        unknown field silently loaded (the field is dropped, not rejected). A
        trust-root load must reject it fail-closed."""
        art = _build_artifact()
        data = json.loads(art.to_json())
        data["totally_unaudited_field"] = "sneaky payload riding outside the digest"
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(json.dumps(data))

    def test_strict_load_rejects_unknown_nested_field(self):
        art = _build_artifact()
        data = json.loads(art.to_json())
        data["candidate"]["review_scope"]["sneaky"] = "x"
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(json.dumps(data))

    def test_strict_load_rejects_duplicate_top_level_key(self):
        """F1 repro: plain json.loads silently collapses a duplicate key to its
        LAST value; a trust-root load must reject the ambiguity outright."""
        dup_text = (
            '{"schema": "fab.review-provenance.v2", "schema": "fab.review-provenance.v2", '
            '"repo": "x", "repo": "y"}'
        )
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(dup_text)

    def test_strict_load_rejects_duplicate_nested_key(self):
        dup_text = (
            '{"schema": "fab.review-provenance.v2", "repo": "x", '
            '"base": {"ref_identity": "a", "ref_identity": "b", "base_sha": "' + "a" * 40 + '"}}'
        )
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.ReviewProvenanceArtifact.from_json(dup_text)


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

    def test_path_at_authoritative_location_but_git_tracked_is_rejected(self):
        """F3 repro/fix: path EQUALITY alone does not prove harness authorship —
        `.phase-loop/` is excluded only via the local, non-committed
        `.git/info/exclude`, never a committed `.gitignore`. A PR branch COMMIT
        can place a tracked file at the exact run-store path. Simulate that: git
        init a repo, commit a file at the authoritative provenance path (as a PR
        author could), and confirm `reject_client_supplied_provenance` still
        refuses it even though the location matches exactly."""
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

            run_id = "20260723T000000Z-00-test-run"
            authoritative_path = fp.provenance_path_for_run(repo, run_id)
            authoritative_path.parent.mkdir(parents=True, exist_ok=True)
            # A PR-branch-committed file sitting at the EXACT run-store path.
            rogue_art = _build_artifact(candidate=_make_candidate(patch_digest="9" * 64))
            authoritative_path.write_text(rogue_art.to_json(), encoding="utf-8")
            subprocess.run(["git", "add", str(authoritative_path.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "spoof"], cwd=repo, check=True)

            # Path equality holds exactly, but the path is git-tracked -> refuse.
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reject_client_supplied_provenance(authoritative_path, repo, run_id)

    def test_path_at_authoritative_location_untracked_is_accepted(self):
        """The companion positive case: the same location, written by
        `write_provenance` (never committed to git) — not blocked by the
        git-tracked guard."""
        import subprocess

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

            run_id = "20260723T000000Z-00-test-run"
            art = _build_artifact()
            path = fp.write_provenance(repo, run_id, art)
            fp.reject_client_supplied_provenance(path, repo, run_id)

    def test_git_probe_rev_parse_timeout_fails_closed(self):
        """Round-2 CR (codex + gemini, agent-harness#191): a `git rev-parse`
        TIMEOUT is undetermined tracked-status, not a "safe/not-a-repo" answer
        — must be treated as tracked/unsafe (`_is_git_tracked` -> True), so
        `reject_client_supplied_provenance` REJECTS rather than silently
        accepting a client-supplied provenance blob."""
        repo = Path("/nonexistent/does-not-matter")
        with mock.patch(
            "subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd="git", timeout=10)
        ):
            self.assertTrue(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_rev_parse_oserror_fails_closed(self):
        """`git` binary missing/unrunnable during the `rev-parse` probe -> an
        OSError, which is undetermined tracked-status, not "not a repo"."""
        repo = Path("/nonexistent/does-not-matter")
        with mock.patch("subprocess.run", side_effect=OSError("no such file")):
            self.assertTrue(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_rev_parse_fatal_nonrepo_message_stays_untracked(self):
        """The ONE legitimate carve-out: git's own deterministic, reproducible
        "not a git repository" fatal message (rc 128) is a clean negative
        answer, not an ambiguous error — the guard genuinely does not apply,
        so this must still return False (preserves the tempdir-based
        test-scaffolding exemption used elsewhere in this file)."""
        repo = Path("/nonexistent/does-not-matter")

        def fake_run(cmd, **kwargs):
            result = mock.Mock()
            result.returncode = 128
            result.stdout = ""
            result.stderr = "fatal: not a git repository (or any of the parent directories): .git"
            return result

        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertFalse(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_rev_parse_other_fatal_error_fails_closed(self):
        """A DIFFERENT fatal git failure (rc 128, but NOT the "not a git
        repository" signature — e.g. corruption) is ambiguous: it could be
        occurring inside a REAL working tree. Must fail closed, not be
        conflated with the genuine not-a-repo carve-out."""
        repo = Path("/nonexistent/does-not-matter")

        def fake_run(cmd, **kwargs):
            result = mock.Mock()
            result.returncode = 128
            result.stdout = ""
            result.stderr = "fatal: index file corrupt"
            return result

        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertTrue(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_rev_parse_rc0_malformed_output_fails_closed(self):
        """Round-3 CR (codex, agent-harness#191): a clean rc==0 `rev-parse` whose
        stdout is NOT the exact well-formed "true"/"false" (empty, malformed, or
        unexpected) is NOT a definitive not-inside-work-tree answer and must fail
        CLOSED (-> True), never be read as "safely untracked". Only exact "false"
        takes the definitive-negative branch."""
        repo = Path("/nonexistent/does-not-matter")

        for bogus in ("", "  ", "maybe", "TRUE", "false\nextra"):
            def fake_run(cmd, _bogus=bogus, **kwargs):
                result = mock.Mock()
                result.returncode = 0
                result.stdout = _bogus
                result.stderr = ""
                return result

            with mock.patch("subprocess.run", side_effect=fake_run):
                self.assertTrue(
                    fp._is_git_tracked(repo, repo / "x"),
                    msg=f"rc0 rev-parse stdout={bogus!r} must fail closed (True)",
                )

    def test_git_probe_ls_files_nonzero_fatal_fails_closed(self):
        """`git ls-files` returning a nonzero rc (incl. fatal rc 128) after a
        successful `rev-parse` is an ambiguous git failure, NOT a definitive
        "not tracked" answer — must fail closed (this was the concrete F3
        fail-open: the old code used `--error-unmatch` and treated ANY
        nonzero rc, including fatal errors, as "not tracked")."""
        repo = Path("/nonexistent/does-not-matter")
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            result = mock.Mock()
            if calls["n"] == 1:
                result.returncode = 0
                result.stdout = "true\n"
                result.stderr = ""
            else:
                result.returncode = 128
                result.stdout = ""
                result.stderr = "fatal: unable to read tree object"
            return result

        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertTrue(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_ls_files_timeout_fails_closed(self):
        """`git ls-files` hanging/timing out after a successful `rev-parse`
        is undetermined tracked-status -> fail closed."""
        repo = Path("/nonexistent/does-not-matter")
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                result = mock.Mock()
                result.returncode = 0
                result.stdout = "true\n"
                result.stderr = ""
                return result
            raise _subprocess.TimeoutExpired(cmd="git", timeout=10)

        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertTrue(fp._is_git_tracked(repo, repo / "x"))

    def test_git_probe_error_paths_cause_reject_client_supplied_provenance_to_raise(self):
        """End-to-end: an ambiguous git-probe failure at the authoritative
        run-store path must cause `reject_client_supplied_provenance` to
        RAISE (fail closed), not silently accept — this is the actual trust-
        root consequence of the `_is_git_tracked` polarity bug."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            art = _build_artifact()
            path = fp.write_provenance(repo, run_id, art)

            with mock.patch(
                "subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd="git", timeout=10)
            ):
                with self.assertRaises(fp.ProvenanceInvalid):
                    fp.reject_client_supplied_provenance(path, repo, run_id)


class ImmutableMaterialTest(unittest.TestCase):
    def test_snapshot_and_rehash_equality(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"reviewed content" * 5000)  # exceed one hash chunk easily if small

            digests = fp.snapshot_material(repo, run_id, [str(source)])
            self.assertEqual(len(digests), 1)
            # Re-verifying immediately (no edit), against the correctly-aggregated
            # digest, must pass.
            expected = fp.aggregate_material_digest(digests)
            fp.reverify_material(repo, run_id, digests, expected_reviewed_material_digest=expected)

    def test_editing_underlying_file_after_snapshot_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"original reviewed bytes")

            digests = fp.snapshot_material(repo, run_id, [str(source)])
            expected = fp.aggregate_material_digest(digests)
            source.write_bytes(b"an edit made AFTER review")

            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reverify_material(repo, run_id, digests, expected_reviewed_material_digest=expected)

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
                fp.reverify_material(repo, run_id, [fake_entry], expected_reviewed_material_digest="1" * 64)

    def test_aggregate_mismatch_against_claimed_reviewed_material_digest_invalidates(self):
        """F4 repro: a snapshot whose PER-REF digests are stable (pass steps 1+2)
        but whose AGGREGATE does not equal what the artifact CLAIMS the seats
        reviewed must invalidate — the binding design §6.4 requires."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_id = "20260723T000000Z-00-test-run"
            source = repo / "material.txt"
            source.write_bytes(b"stable, unedited content")

            digests = fp.snapshot_material(repo, run_id, [str(source)])
            # Per-ref snapshot+live checks would pass on their own; the artifact's
            # CLAIMED reviewed_material_digest is for an unrelated material set.
            bogus_claim = "deadbeef" * 8
            self.assertNotEqual(fp.aggregate_material_digest(digests), bogus_claim)
            with self.assertRaises(fp.ProvenanceInvalid):
                fp.reverify_material(repo, run_id, digests, expected_reviewed_material_digest=bogus_claim)

    def test_aggregate_material_digest_is_order_independent(self):
        a = fp.MaterialDigest(ref="/a", sha256="1" * 64)
        b = fp.MaterialDigest(ref="/b", sha256="2" * 64)
        self.assertEqual(
            fp.aggregate_material_digest([a, b]),
            fp.aggregate_material_digest([b, a]),
        )


class MetadataOnlyTest(unittest.TestCase):
    """Acceptance criterion 5 (design §9): provenance auditable + metadata-only."""

    def test_finding_body_ref_must_be_a_content_ref_digest(self):
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.Finding(
                id="f1", severity="block", status="clean",
                body_ref="This finding says the endpoint is missing auth.",  # raw prose
            )

    def test_finding_body_ref_none_is_rejected(self):
        """F5 decision: body_ref is REQUIRED, not optional. Every legitimate
        Finding this design produces originates from a seat's review output
        (§6.5's schema always shows a populated body_ref) and there is no
        described finding type that legitimately lacks one — a purely
        structural/advisory signal (e.g. a boundary-manifest escalation trigger)
        is carried on Escalation.trigger, never as a bodyless Finding. A finding
        with body_ref=None is therefore an unaudited gap, and now fails closed at
        construction instead of being silently accepted."""
        with self.assertRaises(fp.ProvenanceInvalid):
            fp.Finding(id="f1", severity="block", status="clean", body_ref=None)

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
