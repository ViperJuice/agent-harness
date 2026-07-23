"""FAB Lane D (Consiliency/agent-harness#191) — gate-status composition,
authenticity cross-check, immutable-material re-verify, and agent-review-gate
wiring (closeout + governed_premerge promotion re-assertion). Deliberately
UNMARKED (no ``dotfiles_integration``), so CI's ``-m "not dotfiles_integration"``
runs this module (the goal-id-inc2 lesson). Uses REAL temporary git
repositories for every path that exercises `fab_canonical.equivalent()` — no
mocked git for the core equivalence recompute."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import closeout_validators as cv
from phase_loop_runtime import fab_canonical as fc
from phase_loop_runtime import fab_delta as fd
from phase_loop_runtime import fab_gate as fg
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime import governed_premerge as gp
from phase_loop_runtime.panel_invoker import SeatOutcomeRecord

_GIT = shutil.which("git")


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _rev_parse(repo: Path, ref: str = "HEAD") -> str:
    return _run(repo, "rev-parse", ref).stdout.strip()


def _content_ref(byte: str) -> str:
    return f"sha256:{byte * 64}"


_STRONG_MANIFEST = """
[auth_security]
globs = ["**/auth/**", "**/*secret*"]
""".strip()


def _finding(id_: str, *, severity: str = "block", status: str = "clean", path_scope: tuple[str, ...] = ()) -> fp.Finding:
    return fp.Finding(id=id_, severity=severity, status=status, path_scope=path_scope, body_ref=_content_ref("0"))


def _seat(
    seat_key: str,
    *,
    vendor_leg: str = "codex",
    epoch: int = 1,
    required: bool = True,
    verdict: str | None = "AGREE",
    status: str = "OK",
    finding_ids: tuple[str, ...] = (),
    artifact_digest: str = "1" * 64,
    evidence_digest: str = "2" * 64,
) -> fp.ProvenanceSeat:
    return fp.ProvenanceSeat(
        seat_key=seat_key,
        vendor_leg=vendor_leg,
        required=required,
        status=status,
        epoch=epoch,
        artifact_digest=artifact_digest,
        evidence_digest=evidence_digest,
        verdict=verdict,
        finding_ids=finding_ids,
    )


def _durable_from_seat(seat: fp.ProvenanceSeat, *, attempt_id: str = "a1", completed_at: str = "2026-01-01T00:00:00Z") -> SeatOutcomeRecord:
    """Build the durable ``SeatOutcomeRecord`` that AUTHENTICATES `seat` (every
    cross-checked field copied verbatim) — the "real review actually ran and
    matches" fixture."""
    return SeatOutcomeRecord(
        seat_key=seat.seat_key,
        vendor_leg=seat.vendor_leg,
        required=seat.required,
        status=seat.status,
        attempt_id=attempt_id,
        epoch=seat.epoch,
        artifact_digest=seat.artifact_digest,
        completed_at=completed_at,
        evidence_digest=seat.evidence_digest,
        reason=None,
    )


class GitRepoTestCase(unittest.TestCase):
    """Base fixture (mirrors `test_fab_canonical_b.GitRepoTestCase`): a working
    tree with a github.com-shaped `origin` (resolved-only, never fetched) and a
    real local bare `fetchsrc` remote `equivalent()`'s `origin=` points at."""

    REPO_SLUG = "github.com/testorg/testrepo"

    def setUp(self) -> None:
        if _GIT is None:  # pragma: no cover - CI always has git
            self.skipTest("git not available")
        self._tmp = tempfile.mkdtemp(prefix="fab-gate-d-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.origin_dir = Path(self._tmp) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin_dir)], check=True)
        self.repo = Path(self._tmp) / "work"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        _run(self.repo, "config", "user.email", "t@example.com")
        _run(self.repo, "config", "user.name", "Test")
        _run(self.repo, "remote", "add", "origin", "git@github.com:testorg/testrepo.git")
        _run(self.repo, "remote", "add", "fetchsrc", str(self.origin_dir))

    def write(self, relpath: str, content: bytes | str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def commit(self, message: str) -> str:
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return _rev_parse(self.repo)

    def push_main(self, ref: str = "HEAD") -> None:
        _run(self.repo, "push", "-q", "-f", "fetchsrc", f"{ref}:refs/heads/main")

    def digest(self, base_sha: str, head_sha: str) -> str:
        return fc.patch_digest(self.repo, base_sha, head_sha, repo_slug=self.REPO_SLUG)

    # -- provenance-building helpers ---------------------------------------

    def base_binding(self, base_sha: str) -> fp.BaseBinding:
        return fp.BaseBinding(ref_identity=f"{self.REPO_SLUG}#main", base_sha=base_sha)

    def manifest_ref(self, base_sha: str) -> fp.BoundaryManifestRef:
        return fp.BoundaryManifestRef(path=fd.BOUNDARY_MANIFEST_PATH, source_rev=base_sha, digest="d" * 64)

    def candidate(self, base_sha: str, head_sha: str, *, reviewed_material_digest: str | None = None) -> fp.CandidateRecord:
        pd = self.digest(base_sha, head_sha)
        scope = fp.ReviewScope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, reviewed_material_digest=reviewed_material_digest, covers_patch_digest=pd)
        return fp.CandidateRecord(head_sha=head_sha, review_scope=scope, patch_digest=pd)

    def build_artifact(
        self,
        *,
        base_sha: str,
        candidate: fp.CandidateRecord,
        seats: tuple[fp.ProvenanceSeat, ...] = (),
        findings: tuple[fp.Finding, ...] = (),
        delta_chain: tuple[fp.DeltaReviewRecord, ...] = (),
        material_digests: tuple[fp.MaterialDigest, ...] = (),
    ) -> fp.ReviewProvenanceArtifact:
        return fp.ReviewProvenanceArtifact.build(
            repo=self.REPO_SLUG,
            base=self.base_binding(base_sha),
            boundary_manifest=self.manifest_ref(base_sha),
            candidate=candidate,
            seats=seats,
            findings=findings,
            delta_chain=delta_chain,
            material_digests=material_digests,
        )

    def persist(self, run_id: str, artifact: fp.ReviewProvenanceArtifact, seats: tuple[fp.ProvenanceSeat, ...]) -> None:
        fp.write_provenance(self.repo, run_id, artifact)
        for seat in seats:
            fg.append_seat_outcome(self.repo, run_id, _durable_from_seat(seat))


# --------------------------------------------------------------------------- #
# Acceptance criterion 6 — exact-head degenerate case (empty delta_chain)
# --------------------------------------------------------------------------- #


class ExactHeadDegenerateTest(GitRepoTestCase):
    def test_acceptance_6_exact_head_still_supported(self):
        self.write("a.py", "hello\n")
        base = self.commit("c0 base")
        self.push_main()
        self.write("a.py", "hello world\n")
        head = self.commit("c1 reviewed head == live head")

        seats = (_seat("codex:x:high", finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("a.py",)),)
        candidate = self.candidate(base, head)
        artifact = self.build_artifact(base_sha=base, candidate=candidate, seats=seats, findings=findings)
        self.persist("run-exact-head", artifact, seats)

        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-exact-head", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_PASS)
        self.assertEqual(gate.reviewed_sha, head)
        self.assertEqual(gate.reviewed_sha, candidate.head_sha)
        # `reviewed_sha` and `final_pr_head_sha` legitimately COINCIDE in this
        # exact-head case (candidate head == live head), but they are always
        # TWO INDEPENDENT fields — never one field silently standing in for
        # the other (design §8, T16).
        self.assertEqual(gate.final_pr_head_sha, head)
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_EQUIVALENT)
        self.assertEqual(gate.deltas, ())
        self.assertEqual(gate.carried_forward_findings, ())
        self.assertEqual(gate.re_reviewed_findings, ())


# --------------------------------------------------------------------------- #
# Acceptance criteria 1/2 — disjoint delta passes; unrelated byte invalidates
# --------------------------------------------------------------------------- #


class DeltaChainGateTest(GitRepoTestCase):
    def _acceptance_1_setup(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c0 base: boundary manifest in force")
        self.push_main()

        self.write("pkg/a.py", "large reviewed content a\n")
        self.write("pkg/b.py", "large reviewed content b\n")
        candidate_head = self.commit("c1 large reviewed patch")

        candidate_seats = (
            _seat("codex:x:high", epoch=1, finding_ids=("f1", "f2")),
            _seat("gemini:y:high", vendor_leg="gemini", epoch=1, finding_ids=("f1", "f2")),
        )
        findings = (
            _finding("f1", path_scope=("pkg/a.py",)),
            _finding("f2", path_scope=("pkg/b.py",)),
        )
        candidate = self.candidate(base, candidate_head)
        artifact0 = self.build_artifact(base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings)
        c0 = artifact0.chain_digest

        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta")
        delta_record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=candidate_head,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        self.assertFalse(delta_record.escalation.required)
        self.assertEqual(sorted(delta_record.carried_forward_finding_ids), ["f1", "f2"])

        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-acc1", artifact, candidate_seats)
        return base, candidate_head, delta_head

    def test_acceptance_1_disjoint_clean_delta_passes_without_whole_patch_rereview(self):
        base, candidate_head, delta_head = self._acceptance_1_setup()
        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-acc1", live_base_ref_name="main", live_head_sha=delta_head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_PASS)
        self.assertEqual(gate.reviewed_sha, candidate_head)  # NEVER the delta/live head
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_EQUIVALENT)
        self.assertEqual(sorted(gate.carried_forward_findings), ["f1", "f2"])
        self.assertFalse(gate.escalation.required)

    def test_acceptance_2_unrelated_byte_invalidates(self):
        base, candidate_head, delta_head = self._acceptance_1_setup()
        self.write("pkg/c.py", "small disjoint delta\nEXTRA UNRELATED BYTE\n")
        drifted_head = self.commit("c3 unrelated extra byte, not part of any reviewed delta")

        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-acc1", live_base_ref_name="main", live_head_sha=drifted_head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertEqual(gate.equivalence_verified.reason, fc.REASON_CONTENT_DRIFT)


# --------------------------------------------------------------------------- #
# Acceptance criterion 3 — rebase/conflict invalidates at gate; the §4.4
# promotion re-assertion catches a merge-outside-head change.
# --------------------------------------------------------------------------- #


class RebaseAndPromotionTest(GitRepoTestCase):
    def _setup_simple_exact_head(self):
        self.write("a.py", "hello\n")
        base = self.commit("c0")
        self.push_main()
        _run(self.repo, "checkout", "-qb", "pr1")
        self.write("a.py", "hello world\n")
        head = self.commit("c1 on pr1")

        seats = (_seat("codex:x:high", finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("a.py",)),)
        candidate = self.candidate(base, head)
        artifact = self.build_artifact(base_sha=base, candidate=candidate, seats=seats, findings=findings)
        self.persist("run-acc3", artifact, seats)
        return base, head

    def test_acceptance_3_rebase_invalidates_at_gate(self):
        base, head = self._setup_simple_exact_head()

        _run(self.repo, "checkout", "-q", "main")
        self.write("unrelated.py", "advance\n")
        self.commit("advance main")
        self.push_main()
        _run(self.repo, "checkout", "-q", "pr1")
        _run(self.repo, "rebase", "-q", "main")
        rebased_head = _rev_parse(self.repo)

        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-acc3", live_base_ref_name="main", live_head_sha=rebased_head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(gate.equivalence_verified.reason.startswith(fc.REASON_BASE_SHA_MISMATCH))

    def test_acceptance_3_promotion_reassertion_refuses_merge_outside_head(self):
        """The gate PASSED against `head`, but the merge that actually lands
        resolves a conflict OUTSIDE the reviewed head (I4b) — the §4.4
        promotion-time re-assertion, run immediately before merge, must
        refuse regardless of `run_mode`."""
        base, head = self._setup_simple_exact_head()
        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-acc3", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_PASS)

        artifact = fp.read_provenance(self.repo, "run-acc3")
        binding = fg.resolve_equivalence_binding(artifact)

        # Simulate "merge resolved outside the head": the actual merge commit
        # that would land introduces content the reviewed head never saw.
        self.write("a.py", "hello world -- resolved differently at merge time\n")
        merged_head = self.commit("c2 conflict resolution not part of the reviewed head")

        check = gp.FabPromotionCheck(
            binding=binding, repo_dir=self.repo, live_base_ref_name="main", live_head_sha=merged_head, origin="fetchsrc"
        )
        result = gp.run_governed_premerge_loop(
            artifact="irrelevant-non-fab-artifact",
            author_executor="codex",
            run_mode="autonomous",
            fab_promotion_check=check,
        )
        self.assertFalse(result.mergeable)
        self.assertIsNotNone(result.terminal_blocker)
        self.assertFalse(result.terminal_blocker["human_required"])
        self.assertEqual(result.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertEqual(result.reason, "fab_promotion_reassertion_failed")

    def test_promotion_reassertion_passes_through_when_still_equivalent(self):
        base, head = self._setup_simple_exact_head()
        artifact = fp.read_provenance(self.repo, "run-acc3")
        binding = fg.resolve_equivalence_binding(artifact)
        check = gp.FabPromotionCheck(
            binding=binding, repo_dir=self.repo, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        result = gp.run_governed_premerge_loop(
            artifact="irrelevant", author_executor="codex", run_mode="autonomous", fab_promotion_check=check
        )
        self.assertTrue(result.mergeable)
        self.assertIsNone(result.terminal_blocker)

    def test_no_fab_promotion_check_is_byte_neutral(self):
        """Default (`fab_promotion_check=None`) — every existing caller's
        behavior is unchanged."""
        result = gp.run_governed_premerge_loop(artifact="x", author_executor="codex", run_mode="autonomous")
        self.assertTrue(result.mergeable)
        self.assertFalse(result.ran)
        self.assertEqual(result.reason, "autonomous")


# --------------------------------------------------------------------------- #
# Acceptance criterion 4 — boundary-surface delta forces whole-patch
# escalation; a delta-only round cannot satisfy it (T5), a whole-patch round
# that resolves the reopened finding, corroborated by seats, can.
# --------------------------------------------------------------------------- #


class EscalationGateTest(GitRepoTestCase):
    def _base_and_candidate(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base")
        self.push_main()
        candidate_seats = (_seat("codex:x:high", epoch=1, finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("pkg/a.py",)),)
        candidate = self.candidate(base, base)
        return base, candidate, candidate_seats, findings

    def test_acceptance_4_delta_only_scope_cannot_satisfy_escalation(self):
        base, candidate, candidate_seats, findings = self._base_and_candidate()
        artifact0 = self.build_artifact(base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings)
        c0 = artifact0.chain_digest

        self.write("src/auth/login.py", "touches a protected surface\n")
        delta_head = self.commit("c1 touches auth boundary")

        with self.assertRaises(fd.EscalationInvalid):
            fd.build_delta_round(
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=candidate.patch_digest,
                parent_chain_digest=c0,
                delta_head_sha=delta_head,
                findings=findings,
                resolved_finding_ids=(),
                delta_round_seats=(),
                review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
                status=fp.DELTA_STATUS_REVIEWED_CLEAN,  # contradiction with escalation.required
            )

    def test_acceptance_4_whole_patch_escalation_satisfied_passes(self):
        base, candidate, candidate_seats, findings = self._base_and_candidate()
        artifact0 = self.build_artifact(base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings)
        c0 = artifact0.chain_digest

        self.write("src/auth/login.py", "touches a protected surface, whole-patch reviewed\n")
        delta_head = self.commit("c1 touches auth boundary, whole-patch reviewed clean")
        full_digest = self.digest(base, delta_head)

        delta_seats = (_seat("codex:x:high", epoch=2, finding_ids=("f1",)),)
        delta_record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=("f1",),
            delta_round_seats=delta_seats,
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers_patch_digest=full_digest),
            status=fp.DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        )
        self.assertTrue(delta_record.escalation.required)
        self.assertEqual(delta_record.escalation.trigger, "auth_security")

        all_seats = candidate_seats + delta_seats
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=all_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-acc4", artifact, all_seats)

        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-acc4", live_base_ref_name="main", live_head_sha=delta_head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_PASS)
        self.assertTrue(gate.escalation.required)
        self.assertEqual(gate.escalation.trigger, "auth_security")
        self.assertEqual(gate.re_reviewed_findings, ("f1",))


# --------------------------------------------------------------------------- #
# Acceptance criterion 5 — provenance auditable + metadata-only + AUTHENTIC:
# T13 fabricated seat, T14 mutated material.
# --------------------------------------------------------------------------- #


class AuthenticityAndMaterialTest(GitRepoTestCase):
    def test_acceptance_5_fabricated_seat_with_no_durable_record_invalidates(self):
        self.write("a.py", "hello\n")
        base = self.commit("c0")
        self.push_main()
        self.write("a.py", "hello world\n")
        head = self.commit("c1")

        seats = (_seat("codex:x:high", finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("a.py",)),)
        candidate = self.candidate(base, head)
        artifact = self.build_artifact(base_sha=base, candidate=candidate, seats=seats, findings=findings)
        # Write provenance but DO NOT persist the corroborating durable seat
        # outcome (T13's exploit: a hand-written provenance vouching for a
        # seat that never ran).
        fp.write_provenance(self.repo, "run-fab-seat", artifact)

        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-fab-seat", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        self.assertEqual(gate.equivalence_verified.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertIn("provenance_invalid", gate.equivalence_verified.reason)

    def test_acceptance_5_mutated_material_snapshot_invalidates(self):
        self.write("a.py", "hello\n")
        base = self.commit("c0")
        self.push_main()
        self.write("a.py", "hello world\n")
        head = self.commit("c1")

        material_source = self.repo / "review-material.txt"
        material_source.write_text("the exact bytes the seats reviewed\n", encoding="utf-8")
        digests = fp.snapshot_material(self.repo, "run-material", (str(material_source),))
        aggregate = fp.aggregate_material_digest(digests)

        seats = (_seat("codex:x:high", finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("a.py",)),)
        candidate = self.candidate(base, head, reviewed_material_digest=aggregate)
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=seats, findings=findings, material_digests=digests
        )
        self.persist("run-material", artifact, seats)

        # Sanity: passes BEFORE the post-review edit.
        gate_before = fg.compose_gate_status(
            repo=self.repo, run_id="run-material", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate_before.status, fp.GATE_STATUS_PASS)

        # A post-review edit of the underlying (mutable) material file.
        material_source.write_text("TAMPERED bytes the seats never actually saw\n", encoding="utf-8")

        gate_after = fg.compose_gate_status(
            repo=self.repo, run_id="run-material", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate_after.status, fp.GATE_STATUS_BLOCK)
        self.assertEqual(gate_after.equivalence_verified.result, fp.EQUIVALENCE_INVALIDATED)


# --------------------------------------------------------------------------- #
# verdict_binds_to_equivalent (T16) — two independent facts ANDed
# --------------------------------------------------------------------------- #


class VerdictBindsToEquivalentTest(unittest.TestCase):
    def _gate_status(self, *, reviewed_sha: str, equivalence_result: str) -> fp.GateStatus:
        return fp.GateStatus(
            reviewed_sha=reviewed_sha,
            equivalence_verified=fp.EquivalenceVerified(result=equivalence_result, candidate_head_sha=reviewed_sha),
            escalation=fp.Escalation(required=False, trigger=None),
            status=fp.GATE_STATUS_PASS if equivalence_result == fp.EQUIVALENCE_EQUIVALENT else fp.GATE_STATUS_BLOCK,
        )

    def test_true_only_when_both_facts_hold(self):
        finding = cv.ReviewFinding(code="c", reason="r", reviewed_sha="a" * 40)
        gate = self._gate_status(reviewed_sha="a" * 40, equivalence_result=fp.EQUIVALENCE_EQUIVALENT)
        self.assertTrue(fg.verdict_binds_to_equivalent(finding, gate))

    def test_false_when_sha_does_not_bind(self):
        finding = cv.ReviewFinding(code="c", reason="r", reviewed_sha="b" * 40)
        gate = self._gate_status(reviewed_sha="a" * 40, equivalence_result=fp.EQUIVALENCE_EQUIVALENT)
        self.assertFalse(fg.verdict_binds_to_equivalent(finding, gate))

    def test_false_when_equivalence_invalidated_even_if_sha_binds(self):
        finding = cv.ReviewFinding(code="c", reason="r", reviewed_sha="a" * 40)
        gate = self._gate_status(reviewed_sha="a" * 40, equivalence_result=fp.EQUIVALENCE_INVALIDATED)
        self.assertFalse(fg.verdict_binds_to_equivalent(finding, gate))

    def test_reviewed_sha_is_never_final_pr_head_sha(self):
        """T16: a gate status's `reviewed_sha` must be the CANDIDATE head, not
        whatever `final_pr_head_sha` happens to be (they may coincide in the
        exact-head case, but they are never THE SAME FIELD)."""
        gate = fp.GateStatus(
            reviewed_sha="a" * 40,
            final_pr_head_sha="b" * 40,
            equivalence_verified=fp.EquivalenceVerified(result=fp.EQUIVALENCE_EQUIVALENT),
            escalation=fp.Escalation(required=False, trigger=None),
            status=fp.GATE_STATUS_PASS,
        )
        self.assertNotEqual(gate.reviewed_sha, gate.final_pr_head_sha)
        finding = cv.ReviewFinding(code="c", reason="r", reviewed_sha="b" * 40)  # bound to the LIVE head, not reviewed_sha
        self.assertFalse(fg.verdict_binds_to_equivalent(finding, gate))


# --------------------------------------------------------------------------- #
# cross_check_seat_authenticity (T13) — "usable terminal" status is checked
# case-INSENSITIVELY (the codebase has no single frozen casing convention for
# this free-form field), but AGREEMENT between the two records stays STRICT.
# --------------------------------------------------------------------------- #


class SeatAuthenticityCaseInsensitivityTest(unittest.TestCase):
    def test_lowercase_ok_is_a_usable_terminal(self):
        seat = _seat("codex:x:high", status="ok", finding_ids=("f1",))
        durable = _durable_from_seat(seat)  # status="ok", matches exactly
        fg.cross_check_seat_authenticity((seat,), (durable,))  # must not raise

    def test_uppercase_ok_is_a_usable_terminal(self):
        seat = _seat("codex:x:high", status="OK", finding_ids=("f1",))
        durable = _durable_from_seat(seat)
        fg.cross_check_seat_authenticity((seat,), (durable,))  # must not raise

    def test_timeout_is_never_usable_regardless_of_case(self):
        for bad_status in ("TIMEOUT", "timeout", "DEGRADED", "ERROR", "EMPTY"):
            seat = _seat("codex:x:high", status=bad_status, finding_ids=("f1",))
            durable = _durable_from_seat(seat)
            with self.assertRaises(fg.SeatAuthenticityInvalid):
                fg.cross_check_seat_authenticity((seat,), (durable,))

    def test_status_agreement_between_records_stays_case_sensitive(self):
        """The USABILITY check tolerates case; AGREEMENT between the
        provenance seat and its durable record does NOT — a genuine casing
        MISMATCH between the two independently-authored records is still a
        real divergence signal (no normalization across the trust boundary)."""
        seat = _seat("codex:x:high", status="OK", finding_ids=("f1",))
        durable = _durable_from_seat(seat)
        durable = dataclasses.replace(durable, status="ok")  # differs only in case
        with self.assertRaises(fg.SeatAuthenticityInvalid):
            fg.cross_check_seat_authenticity((seat,), (durable,))


# --------------------------------------------------------------------------- #
# governed_premerge promotion re-assertion — GOVERNED-mode branch (the
# autonomous-mode branch is covered by RebaseAndPromotionTest above).
# --------------------------------------------------------------------------- #


class GovernedModePromotionOverrideTest(unittest.TestCase):
    def _binding(self) -> fc.EquivalenceBinding:
        return fc.EquivalenceBinding(
            repo_slug="github.com/testorg/testrepo",
            base_ref_name="main",
            base_sha="a" * 40,
            expected_head_digest="b" * 64,
        )

    def _promoted_invoke(self, **_kwargs):
        from phase_loop_runtime.governed_review import GateResult

        return GateResult(ran=True, promoted=True, degraded=False, findings=(), reason=None)

    def test_governed_convergence_still_gets_overridden_by_failed_promotion_check(self):
        """The `if gate.promoted:` branch inside the governed loop — not just
        the autonomous no-op branch — must also honor a failed §4.4
        re-assertion, preserving the round/finding bookkeeping semantics of a
        blocked LoopResult."""
        check = gp.FabPromotionCheck(
            binding=self._binding(), repo_dir="/irrelevant", live_base_ref_name="main", live_head_sha="c" * 40
        )
        fake_equivalent = lambda *a, **k: fp.EquivalenceResult(result=fp.EQUIVALENCE_INVALIDATED, reason="content_drift")
        result = gp.run_governed_premerge_loop(
            artifact="x",
            author_executor="codex",
            run_mode="governed",
            invoke=self._promoted_invoke,
            fab_promotion_check=check,
            fab_equivalent_fn=fake_equivalent,
        )
        self.assertFalse(result.mergeable)
        self.assertTrue(result.ran)
        self.assertEqual(result.rounds, 1)
        self.assertFalse(result.terminal_blocker["human_required"])
        self.assertEqual(result.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertEqual(result.reason, "fab_promotion_reassertion_failed")

    def test_governed_convergence_passes_through_when_still_equivalent(self):
        check = gp.FabPromotionCheck(
            binding=self._binding(), repo_dir="/irrelevant", live_base_ref_name="main", live_head_sha="c" * 40
        )
        fake_equivalent = lambda *a, **k: fp.EquivalenceResult(result=fp.EQUIVALENCE_EQUIVALENT)
        result = gp.run_governed_premerge_loop(
            artifact="x",
            author_executor="codex",
            run_mode="governed",
            invoke=self._promoted_invoke,
            fab_promotion_check=check,
            fab_equivalent_fn=fake_equivalent,
        )
        self.assertTrue(result.mergeable)
        self.assertIsNone(result.terminal_blocker)


# --------------------------------------------------------------------------- #
# Closeout-validator wiring — non-human review_gate_block, warn-default,
# opt-in block, inert when not applicable.
# --------------------------------------------------------------------------- #


class CloseoutValidatorTest(GitRepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_backup = dict(__import__("os").environ)
        self.addCleanup(lambda: __import__("os").environ.clear() or __import__("os").environ.update(self._env_backup))
        # Defensive re-registration: several SIBLING validator test modules
        # (e.g. test_closeout_validator_hook.py) call the module-level
        # `clear_closeout_validators()` in their own setUp/tearDown and never
        # restore the built-ins afterward — a pre-existing cross-file
        # test-isolation gap in this shared, process-global registry (those
        # files re-register their OWN validator function directly after
        # clearing, so they're unaffected; this module goes through the
        # PUBLIC `run_closeout_validators` registry path on purpose, to
        # exercise the real end-to-end wiring, so it must be robust to that
        # gap). `register_closeout_validator` is idempotent (no-op if already
        # present), so this is always safe.
        cv.register_closeout_validator(fg.fab_gate_validator)

    def _passing_run(self) -> tuple[str, str]:
        self.write("a.py", "hello\n")
        base = self.commit("c0")
        self.push_main()
        self.write("a.py", "hello world\n")
        head = self.commit("c1")
        seats = (_seat("codex:x:high", finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("a.py",)),)
        candidate = self.candidate(base, head)
        artifact = self.build_artifact(base_sha=base, candidate=candidate, seats=seats, findings=findings)
        self.persist("run-closeout", artifact, seats)
        return base, head

    def _blocked_run(self) -> tuple[str, str]:
        base, head = self._passing_run()
        self.write("a.py", "hello world + unrelated drift\n")
        drifted = self.commit("c2 drift")
        return base, drifted

    def test_inert_without_fab_gate_inputs(self):
        ctx = cv.CloseoutContext(phase_alias="P1", plan_path="plan.md", repo_root=str(self.repo))
        self.assertEqual(fg.fab_gate_validator(ctx), [])

    def test_inert_when_run_id_has_no_provenance(self):
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "no-such-run", "live_base_ref_name": "main", "live_head_sha": "a" * 40},
        )
        self.assertEqual(fg.fab_gate_validator(ctx), [])

    def test_warn_default_records_but_does_not_block(self):
        import os

        os.environ.pop(cv.REVIEW_MODE_ENV, None)  # default = warn
        base, drifted = self._blocked_run()
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "run-closeout", "live_base_ref_name": "main", "live_head_sha": drifted, "origin": "fetchsrc"},
        )
        findings = cv.run_closeout_validators(ctx)
        fab_findings = [f for f in findings if f.code == fg.FAB_GATE_FINDING_CODE]
        self.assertEqual(len(fab_findings), 1)
        self.assertEqual(fab_findings[0].severity, "warn")  # forced to warn by the default mode
        self.assertFalse(fab_findings[0].blocker_class is None)

        update = cv.apply_review_findings(findings=findings, terminal={}, automation={}, blocker={})
        self.assertNotEqual(update["terminal"].get("terminal_status"), "blocked")

    def test_opt_in_block_mode_blocks_non_human(self):
        import os

        os.environ[cv.REVIEW_MODE_ENV] = "block"
        base, drifted = self._blocked_run()
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "run-closeout", "live_base_ref_name": "main", "live_head_sha": drifted, "origin": "fetchsrc"},
        )
        findings = cv.run_closeout_validators(ctx)
        update = cv.apply_review_findings(findings=findings, terminal={}, automation={}, blocker={})
        self.assertEqual(update["terminal"]["terminal_status"], "blocked")
        self.assertFalse(update["automation"]["human_required"])
        self.assertEqual(update["automation"]["blocker_class"], "review_gate_block")

    def test_passing_gate_emits_no_finding(self):
        import os

        os.environ[cv.REVIEW_MODE_ENV] = "block"
        base, head = self._passing_run()
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "run-closeout", "live_base_ref_name": "main", "live_head_sha": head, "origin": "fetchsrc"},
        )
        findings = cv.run_closeout_validators(ctx)
        fab_findings = [f for f in findings if f.code == fg.FAB_GATE_FINDING_CODE]
        self.assertEqual(fab_findings, [])
