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
    seat_instance_id: str | None = None,
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
        # FAB piece 2: a unique per-invocation instance id (seat_key is
        # non-unique). Default derives from seat_key+epoch, which is unique
        # within every fixture in this module (candidate epoch=1, delta epoch=2).
        seat_instance_id=seat_instance_id if seat_instance_id is not None else f"{seat_key}@{epoch}",
    )


def _durable_from_seat(seat: fp.ProvenanceSeat, *, attempt_id: str = "a1", completed_at: str = "2026-01-01T00:00:00Z") -> SeatOutcomeRecord:
    """Build the durable ``SeatOutcomeRecord`` that AUTHENTICATES `seat` (every
    cross-checked field copied verbatim, incl. the FAB piece-2 verdict /
    finding_ids / seat_instance_id) — the "real review actually ran and matches"
    fixture."""
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
        verdict=seat.verdict,
        finding_ids=seat.finding_ids,
        seat_instance_id=seat.seat_instance_id,
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
        self.write_review_round(run_id, artifact)

    def write_review_round(
        self,
        run_id: str,
        artifact: fp.ReviewProvenanceArtifact,
        *,
        expected_seats: tuple[fp.ProvenanceSeat, ...] | None = None,
        canonical_findings: tuple[fp.Finding, ...] | None = None,
        finalize: bool = True,
    ) -> None:
        """Write the harness-only durable round record the piece-2 gate now
        requires — the epoch-scoped EXPECTED-seat manifest (frozen from the
        CANDIDATE round's seats), the harness-issued round identity (bound to
        the reviewed head + reviewed material), and the canonical finding
        records. Derived from `artifact` so every legitimate PASS fixture stays
        green; individual tests override `expected_seats`/`canonical_findings`/
        `finalize` to exercise a forge path."""
        cand_seats = expected_seats if expected_seats is not None else artifact.seats
        epoch = cand_seats[0].epoch if cand_seats else 1
        fg.write_expected_seats(
            self.repo,
            run_id,
            epoch=epoch,
            expected_seats=tuple(
                fg.ExpectedSeat(
                    seat_instance_id=s.seat_instance_id,
                    seat_key=s.seat_key,
                    vendor_leg=s.vendor_leg,
                    required=s.required,
                )
                for s in cand_seats
            ),
        )
        if not finalize:
            return
        source_findings = canonical_findings if canonical_findings is not None else artifact.findings
        fg.finalize_review_round(
            self.repo,
            run_id,
            reviewed_head_sha=artifact.candidate.head_sha,
            reviewed_material_digest=artifact.candidate.review_scope.reviewed_material_digest,
            canonical_findings=tuple(
                fg.CanonicalFinding(
                    finding_id=f.id, severity=f.severity, status=f.status, body_digest=f.body_ref
                )
                for f in source_findings
            ),
        )

    # -- delta-machinery seams (blocker 5: compose defers ALL delta chains to
    #    piece 3, so the delta machinery is exercised at its OWN functions) -----

    def delta_binding(self, run_id: str, artifact: fp.ReviewProvenanceArtifact) -> None:
        """Run `_require_delta_round_seat_binding` directly against the durable
        ledger — the function these delta-seat tests actually exercise (compose
        now short-circuits on any nonempty delta_chain)."""
        fg._require_delta_round_seat_binding(artifact.delta_chain, fg.read_seat_outcomes(self.repo, run_id))

    def assert_compose_defers_delta(self, run_id: str, head: str):
        """Assert `compose_gate_status` BLOCKS a nonempty delta_chain as deferred
        to piece 3 (blocker 5 — the one production choke point)."""
        gate = fg.compose_gate_status(
            repo=self.repo, run_id=run_id, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        self.assertIn("delta_chain_deferred_to_piece3", gate.equivalence_verified.reason)
        return gate


# --------------------------------------------------------------------------- #
# Piece 2 (design v4/v5/v6) — round-authenticity forge tests: every field the
# gate reads off the client-supplied artifact must be cross-checked against a
# harness-written durable record, fail-closed on any mismatch / absence.
# --------------------------------------------------------------------------- #


class RoundAuthenticityForgeTest(GitRepoTestCase):
    def _exact_head_setup(self):
        self.write("a.py", "hello\n")
        base = self.commit("c0 base")
        self.push_main()
        self.write("a.py", "hello world\n")
        head = self.commit("c1 reviewed head")
        return base, head

    def _reason(self, run_id: str, head: str) -> str:
        gate = fg.compose_gate_status(
            repo=self.repo, run_id=run_id, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_BLOCK)
        return gate.equivalence_verified.reason or ""

    def test_wrong_verdict_seat_blocks(self):
        """The provenance seat claims AGREE but the durable record the harness
        wrote at review time says DISAGREE → BLOCK (verdict binding, v4 #2)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE", finding_ids=())
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,))
        fp.write_provenance(self.repo, "run-wrong-verdict", artifact)
        # durable record: SAME seat but verdict flipped to DISAGREE.
        durable = _durable_from_seat(seat)
        durable = dataclasses.replace(durable, verdict="DISAGREE")
        fg.append_seat_outcome(self.repo, "run-wrong-verdict", durable)
        self.write_review_round("run-wrong-verdict", artifact)
        self.assertIn("verdict", self._reason("run-wrong-verdict", head))

    def test_omitted_required_seat_blocks(self):
        """The expected-seat manifest demands TWO required instances; the
        artifact only vouches for one → the omitted required seat cannot be
        invisible → BLOCK (completeness anchored on the EXPECTED set, v5 #1)."""
        base, head = self._exact_head_setup()
        s1 = _seat("codex:x:high", verdict="AGREE")
        s2 = _seat("gemini:y:high", vendor_leg="gemini", verdict="AGREE")
        # artifact only carries s1; both durable records + BOTH expected seats
        # exist (the harness dispatched both).
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(s1,))
        fp.write_provenance(self.repo, "run-omitted", artifact)
        fg.append_seat_outcome(self.repo, "run-omitted", _durable_from_seat(s1))
        fg.append_seat_outcome(self.repo, "run-omitted", _durable_from_seat(s2))
        # expected manifest lists BOTH (the resolved invocation set).
        self.write_review_round("run-omitted", artifact, expected_seats=(s1, s2))
        self.assertIn("NO matching provenance seat", self._reason("run-omitted", head))

    def test_dropped_blocking_finding_record_blocks(self):
        """A seat logged finding f1 (durable), and f1 is a canonical block
        finding — but the artifact OMITS the Finding record (to hide it) → BLOCK
        (finding-CONTENT binding, v6 #2)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE", finding_ids=("f1",))
        # artifact carries the seat referencing f1 but NO Finding record for f1.
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,), findings=())
        fp.write_provenance(self.repo, "run-dropped-finding", artifact)
        fg.append_seat_outcome(self.repo, "run-dropped-finding", _durable_from_seat(seat))
        # canonical record for f1 exists (harness authenticated it at review time).
        self.write_review_round(
            "run-dropped-finding",
            artifact,
            canonical_findings=(_finding("f1", severity="block", status="open"),),
        )
        self.assertIn("OMITS the Finding record", self._reason("run-dropped-finding", head))

    def test_rewritten_finding_content_blocks(self):
        """The artifact keeps the id but rewrites the Finding non-blocking/clean
        vs the harness canonical record → BLOCK (finding-CONTENT binding)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE", finding_ids=("f1",))
        # artifact's Finding f1 claims status=clean; canonical says status=open.
        rewritten = _finding("f1", severity="block", status="clean")
        artifact = self.build_artifact(
            base_sha=base, candidate=self.candidate(base, head), seats=(seat,), findings=(rewritten,)
        )
        fp.write_provenance(self.repo, "run-rewritten-finding", artifact)
        fg.append_seat_outcome(self.repo, "run-rewritten-finding", _durable_from_seat(seat))
        self.write_review_round(
            "run-rewritten-finding",
            artifact,
            canonical_findings=(_finding("f1", severity="block", status="open"),),
        )
        self.assertIn("does not match the harness canonical record", self._reason("run-rewritten-finding", head))

    def test_empty_expected_manifest_blocks(self):
        """A round with an empty expected-seat manifest can never pass — no
        vacuous truth over an empty required set (v6 #3 / design ambiguity #3)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,))
        fp.write_provenance(self.repo, "run-empty-manifest", artifact)
        fg.append_seat_outcome(self.repo, "run-empty-manifest", _durable_from_seat(seat))
        self.write_review_round("run-empty-manifest", artifact, expected_seats=())
        self.assertIn("empty", self._reason("run-empty-manifest", head))

    def test_round_identity_wrong_head_blocks(self):
        """The round identity is bound to a DIFFERENT reviewed head than the
        artifact's candidate head (replay of another round's manifest) → BLOCK
        (round identity, v6 #3)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,))
        fp.write_provenance(self.repo, "run-replay", artifact)
        fg.append_seat_outcome(self.repo, "run-replay", _durable_from_seat(seat))
        fg.write_expected_seats(
            self.repo, "run-replay", epoch=1,
            expected_seats=(fg.ExpectedSeat(seat_instance_id=seat.seat_instance_id, seat_key=seat.seat_key,
                                            vendor_leg=seat.vendor_leg, required=True),),
        )
        # finalize the round bound to base (NOT the reviewed head) — a replayed
        # identity from a different round.
        fg.finalize_review_round(
            self.repo, "run-replay", reviewed_head_sha=base, reviewed_material_digest=None, canonical_findings=()
        )
        self.assertIn("replay / wrong head", self._reason("run-replay", head))

    def test_unfinalized_round_blocks(self):
        """A round record written pre-invocation but never finalized (a crash
        between provenance write and finalization) → BLOCK, never a silent
        pass (crash safety)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,))
        fp.write_provenance(self.repo, "run-unfinalized", artifact)
        fg.append_seat_outcome(self.repo, "run-unfinalized", _durable_from_seat(seat))
        self.write_review_round("run-unfinalized", artifact, finalize=False)
        self.assertIn("not finalized", self._reason("run-unfinalized", head))

    def test_missing_round_record_blocks(self):
        """A FAB-scoped run whose durable round record is absent BLOCKS (the
        anchor is read from the run store, never the artifact)."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(seat,))
        fp.write_provenance(self.repo, "run-no-round", artifact)
        fg.append_seat_outcome(self.repo, "run-no-round", _durable_from_seat(seat))
        # no write_review_round at all.
        self.assertIn("review-round record", self._reason("run-no-round", head))

    def test_dual_same_seat_key_instances_pass(self):
        """Blocker 3: two legitimate seats sharing a non-unique `seat_key` but
        distinct `seat_instance_id` must NOT collide (the old composite key
        (seat_key, vendor_leg, epoch) crashed with a spurious conflicting-record
        error). Both AGREE → PASS."""
        base, head = self._exact_head_setup()
        s1 = _seat("codex:x:high", verdict="AGREE", seat_instance_id="i1")
        s2 = _seat("codex:x:high", verdict="AGREE", seat_instance_id="i2")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(s1, s2))
        self.persist("run-dual", artifact, (s1, s2))
        gate = fg.compose_gate_status(
            repo=self.repo, run_id="run-dual", live_base_ref_name="main", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(gate.status, fp.GATE_STATUS_PASS)

    def test_one_durable_cannot_satisfy_two_expected_instances(self):
        """Blocker 3: with instance-id keying, ONE durable record cannot cover
        two distinct expected seat instances (the composite-key collapse). The
        second expected instance has no durable outcome → BLOCK."""
        base, head = self._exact_head_setup()
        s1 = _seat("codex:x:high", verdict="AGREE", seat_instance_id="i1")
        s2 = _seat("codex:x:high", verdict="AGREE", seat_instance_id="i2")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(s1, s2))
        fp.write_provenance(self.repo, "run-collapse", artifact)
        # Only ONE durable record (instance i1); i2 never recorded.
        fg.append_seat_outcome(self.repo, "run-collapse", _durable_from_seat(s1))
        # Expected manifest demands BOTH instances.
        self.write_review_round("run-collapse", artifact, expected_seats=(s1, s2))
        reason = self._reason("run-collapse", head)
        self.assertIn("i2", reason)
        self.assertIn("NO matching durable", reason)

    def test_relabeled_seat_identity_blocks(self):
        """CR round 7 / codex#6: once the join is on `seat_instance_id`, the
        artifact could keep a valid instance id but RELABEL the seat's vendor_leg
        (or seat_key/epoch). The gate must bind those fields against the durable
        record → BLOCK."""
        base, head = self._exact_head_setup()
        real = _seat("codex:x:high", vendor_leg="codex", verdict="AGREE", seat_instance_id="i1")
        # The artifact's seat keeps instance id i1 but relabels the vendor.
        forged = _seat("codex:x:high", vendor_leg="gemini", verdict="AGREE", seat_instance_id="i1")
        artifact = self.build_artifact(base_sha=base, candidate=self.candidate(base, head), seats=(forged,))
        fp.write_provenance(self.repo, "run-relabel", artifact)
        fg.append_seat_outcome(self.repo, "run-relabel", _durable_from_seat(real))  # durable = the REAL vendor
        self.write_review_round("run-relabel", artifact, expected_seats=(real,))
        reason = self._reason("run-relabel", head)
        self.assertIn("identity disagrees", reason)

    def test_nonempty_delta_chain_blocks_deferred_to_piece3(self):
        """Blocker 5: piece 2 supports only single candidate-round artifacts; any
        nonempty delta_chain reaching compose fails closed (deferred to piece 3),
        the one choke point for producer + merge-regate + validator alike."""
        base, head = self._exact_head_setup()
        seat = _seat("codex:x:high", verdict="AGREE", finding_ids=())
        candidate = self.candidate(base, head)
        c0 = self.build_artifact(base_sha=base, candidate=candidate, seats=(seat,)).chain_digest
        delta_seat = _seat("codex:x:high", epoch=2, verdict="AGREE", seat_instance_id="d1")
        # Build the round record directly (compose blocks any nonempty delta_chain
        # BEFORE verify_chain / escalation checks, so the round only needs to be a
        # structurally-valid DeltaReviewRecord the artifact can hold).
        delta = fp.DeltaReviewRecord.build(
            epoch=2,
            policy=None, review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY), material_digests=(),
            parent_digest=candidate.patch_digest, parent_chain_digest=c0, delta_head_sha=head,
            delta_changed_paths=(), delta_commits=(), resolved_finding_ids=(), carried_forward_finding_ids=(),
            reopened_finding_ids=(), resulting_head_digest=self.digest(base, head),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN, escalation=fp.Escalation(required=False, trigger=None),
            delta_round_seats=(delta_seat,),
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=(seat,), delta_chain=(delta,)
        )
        self.persist("run-delta-block", artifact, (seat, delta_seat))
        self.assertIn("delta_chain_deferred_to_piece3", self._reason("run-delta-block", head))


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
        # F1 (agent-harness#191 CR, Lane D): a `reviewed-clean` delta round must
        # carry its OWN authenticated, corroborating `delta_round_seats` — even
        # here, where nothing is resolved/reopened (the delta is disjoint from
        # every finding's path_scope, so both carry forward untouched), the
        # round still needs at least one real seat that actually reviewed it.
        delta_seats = (_seat("codex:x:high", epoch=2, finding_ids=()),)
        delta_record = fd.build_delta_round(
            epoch=3,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=candidate_head,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=delta_seats,
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        self.assertFalse(delta_record.escalation.required)
        self.assertEqual(sorted(delta_record.carried_forward_finding_ids), ["f1", "f2"])

        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-acc1", artifact, candidate_seats + delta_seats)
        return base, candidate_head, delta_head

    def test_acceptance_1_disjoint_clean_delta_resolves_and_binds(self):
        """The delta MACHINERY (carry-forward + resolution + seat binding)
        resolves a disjoint clean delta correctly. Exercised at its own
        functions — compose defers the whole delta chain to piece 3 (blocker 5)."""
        base, candidate_head, delta_head = self._acceptance_1_setup()
        artifact = fp.read_provenance(self.repo, "run-acc1")
        resolution = fg.resolve_chain_resolution(artifact)
        self.assertEqual(sorted(resolution.carried_forward_findings), ["f1", "f2"])
        self.assertFalse(resolution.escalation.required)
        self.delta_binding("run-acc1", artifact)  # must not raise
        self.assert_compose_defers_delta("run-acc1", delta_head)

    def test_acceptance_2_unrelated_byte_invalidates(self):
        # Candidate-round equivalence drift is covered by RebaseAndPromotionTest;
        # for a delta chain, compose defers to piece 3 before equivalence.
        base, candidate_head, delta_head = self._acceptance_1_setup()
        self.write("pkg/c.py", "small disjoint delta\nEXTRA UNRELATED BYTE\n")
        drifted_head = self.commit("c3 unrelated extra byte, not part of any reviewed delta")
        self.assert_compose_defers_delta("run-acc1", drifted_head)


# --------------------------------------------------------------------------- #
# Finding 1 (agent-harness#191 CR, Lane D) — a `reviewed-clean` delta round
# with zero, or uncorroborated, `delta_round_seats` of its OWN must BLOCK,
# never pass on the strength of the artifact-wide seat list alone.
# --------------------------------------------------------------------------- #


class DeltaRoundSeatBindingTest(GitRepoTestCase):
    def _base_and_candidate(self):
        # A boundary manifest MUST be in force (no-manifest fail-closed forces
        # whole-patch escalation on EVERY delta, per design §5.4) — write one
        # whose globs don't match pkg/*.py, so the disjoint delta below stays a
        # plain, non-escalated `reviewed-clean` round.
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "large reviewed content a\n")
        self.write("pkg/b.py", "large reviewed content b\n")
        base = self.commit("c0 base")
        self.push_main()
        candidate_seats = (_seat("codex:x:high", epoch=1, finding_ids=()),)
        findings = ()
        candidate = self.candidate(base, base)
        artifact0 = self.build_artifact(base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings)
        return base, candidate, candidate_seats, findings, artifact0.chain_digest

    def test_reviewed_clean_delta_with_zero_seats_blocks(self):
        """The EXACT unsafe outcome F1 closes: a `reviewed-clean` delta round
        built with `delta_round_seats=()` must never reach GATE_STATUS_PASS,
        even when it resolves/reopens nothing at all."""
        base, candidate, candidate_seats, findings, c0 = self._base_and_candidate()
        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta, zero delta-round seats")
        delta_record = fd.build_delta_round(
            epoch=4,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(),  # <-- the unsafe case
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-zero-seats", artifact, candidate_seats)

        with self.assertRaises(fg.DeltaRoundSeatBindingInvalid) as cm:
            self.delta_binding("run-zero-seats", artifact)
        self.assertIn("delta_round_seats", str(cm.exception))
        self.assert_compose_defers_delta("run-zero-seats", delta_head)

    def test_reviewed_clean_delta_with_uncorroborated_reopen_blocks(self):
        """A round's `delta_round_seats` are non-empty and authenticate fine,
        but do NOT corroborate a finding this round REOPENED (its
        `finding_ids` don't cover it) — must still BLOCK (§5.3)."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base")
        self.push_main()
        candidate_seats = (_seat("codex:x:high", epoch=1, finding_ids=("f1",)),)
        findings = (_finding("f1", path_scope=("pkg/a.py",)),)
        candidate = self.candidate(base, base)
        artifact0 = self.build_artifact(base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings)
        c0 = artifact0.chain_digest

        self.write("pkg/a.py", "reviewed content, touched again by an unrelated delta\n")
        delta_head = self.commit("c1 touches pkg/a.py again, reopening f1")
        # This seat authenticates fine but does NOT cover f1 in its finding_ids
        # — a seat that ran, but never actually verdicted the reopened finding.
        uncorroborating_seat = _seat("codex:x:high", epoch=2, finding_ids=())

        # `build_delta_round` itself enforces T4 corroboration at construction
        # time (it would raise here) — to reach Lane D's independent gate-time
        # check, load a record built as if corroboration had been bypassed by
        # constructing the `DeltaReviewRecord` directly (mirrors "a
        # loaded-from-JSON artifact was not necessarily constructed through
        # build_delta_round", which is exactly the gap F1 closes).
        delta_changed_paths = fc.enumerate_changed_paths(self.repo, base, delta_head)
        resulting_head_digest = self.digest(base, delta_head)
        cf = fd.carry_forward(findings, delta_changed_paths, suppress=False)
        self.assertEqual(cf.reopened_finding_ids, ("f1",))
        review_scope = fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY)
        raw_record = fp.DeltaReviewRecord.build(
            epoch=5,
            policy={"path": fd.BOUNDARY_MANIFEST_PATH, "source_rev": base, "digest": "d" * 64},
            review_scope=review_scope,
            material_digests=(),
            parent_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            delta_changed_paths=delta_changed_paths,
            delta_commits=(),
            resolved_finding_ids=(),
            carried_forward_finding_ids=cf.carried_forward_finding_ids,
            reopened_finding_ids=cf.reopened_finding_ids,
            resulting_head_digest=resulting_head_digest,
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            escalation=fp.Escalation(required=False, trigger=None),
            delta_round_seats=(uncorroborating_seat,),
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(raw_record,)
        )
        self.persist("run-uncorroborated", artifact, candidate_seats + (uncorroborating_seat,))

        with self.assertRaises(fg.DeltaRoundSeatBindingInvalid):
            self.delta_binding("run-uncorroborated", artifact)
        self.assert_compose_defers_delta("run-uncorroborated", delta_head)

    # ----------------------------------------------------------------------- #
    # Follow-up CR (agent-harness#191, codex-reproduced): `_require_delta_
    # round_seat_binding` authenticated a round's OWN `delta_round_seats`
    # (non-empty, §6.3 durable cross-check, finding-id corroboration) but
    # NEVER folded their VERDICT into the pass decision — so a reviewed-clean
    # delta whose required own seat DISAGREES (or never verdicted at all)
    # could still reach GATE_STATUS_PASS on the strength of the OLDER
    # artifact-wide `seats` agreeing. These tests pin the fix.
    # ----------------------------------------------------------------------- #

    def test_reviewed_clean_delta_with_disagree_required_seat_blocks(self):
        """The exact bug: an authenticated, corroborated (nothing to
        corroborate — zero findings resolved/reopened) `reviewed-clean` delta
        round whose sole required `delta_round_seat` verdicts DISAGREE, while
        the OLDER artifact-wide `candidate_seats` all AGREE, must BLOCK — the
        artifact-wide seats agreeing can never override this round's own
        required reviewer disagreeing."""
        base, candidate, candidate_seats, findings, c0 = self._base_and_candidate()
        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta, required seat disagrees")
        disagree_seat = _seat("codex:x:high", epoch=2, required=True, verdict="DISAGREE", finding_ids=())
        delta_record = fd.build_delta_round(
            epoch=6,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(disagree_seat,),
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-disagree-seat", artifact, candidate_seats + (disagree_seat,))

        with self.assertRaises(fg.DeltaRoundSeatBindingInvalid) as cm:
            self.delta_binding("run-disagree-seat", artifact)
        self.assertIn("no AGREE/PARTIALLY AGREE verdict", str(cm.exception))
        self.assert_compose_defers_delta("run-disagree-seat", delta_head)

    def test_reviewed_clean_delta_with_unverdicted_required_seat_blocks(self):
        """A required `delta_round_seat` that authenticates fine but never
        received a verdict at all (`verdict=None`) — vacuous corroboration,
        not a real blessing — must also BLOCK."""
        base, candidate, candidate_seats, findings, c0 = self._base_and_candidate()
        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta, required seat never verdicted")
        unverdicted_seat = _seat("codex:x:high", epoch=2, required=True, verdict=None, finding_ids=())
        delta_record = fd.build_delta_round(
            epoch=7,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(unverdicted_seat,),
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-unverdicted-seat", artifact, candidate_seats + (unverdicted_seat,))

        with self.assertRaises(fg.DeltaRoundSeatBindingInvalid) as cm:
            self.delta_binding("run-unverdicted-seat", artifact)
        self.assertIn("no AGREE/PARTIALLY AGREE verdict", str(cm.exception))
        self.assert_compose_defers_delta("run-unverdicted-seat", delta_head)

    def test_reviewed_clean_delta_with_only_optional_seats_blocks(self):
        """A round whose `delta_round_seats` are all non-required (even if
        they AGREE) is never affirmatively blessed — mirrors the
        artifact-level `no_required_seats` rule (design ambiguity #3), applied
        per delta round."""
        base, candidate, candidate_seats, findings, c0 = self._base_and_candidate()
        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta, only optional seats")
        optional_seat = _seat("codex:x:high", epoch=2, required=False, verdict="AGREE", finding_ids=())
        delta_record = fd.build_delta_round(
            epoch=8,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(optional_seat,),
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-optional-only", artifact, candidate_seats + (optional_seat,))

        with self.assertRaises(fg.DeltaRoundSeatBindingInvalid) as cm:
            self.delta_binding("run-optional-only", artifact)
        self.assertIn("NONE are", str(cm.exception))
        self.assert_compose_defers_delta("run-optional-only", delta_head)

    def test_reviewed_clean_delta_with_agreeing_required_seat_passes(self):
        """The legitimate case (kept green): a `reviewed-clean` delta whose
        required `delta_round_seats` AGREE, are authenticated, and corroborate
        the round's (empty) findings — reaches GATE_STATUS_PASS."""
        base, candidate, candidate_seats, findings, c0 = self._base_and_candidate()
        self.write("pkg/c.py", "small disjoint delta\n")
        delta_head = self.commit("c2 small disjoint delta, required seat agrees")
        agree_seat = _seat("codex:x:high", epoch=2, required=True, verdict="AGREE", finding_ids=())
        delta_record = fd.build_delta_round(
            epoch=9,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=candidate.patch_digest,
            parent_chain_digest=c0,
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(agree_seat,),
            review_scope=fp.ReviewScope(mode=fp.REVIEW_SCOPE_DELTA_ONLY),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        artifact = self.build_artifact(
            base_sha=base, candidate=candidate, seats=candidate_seats, findings=findings, delta_chain=(delta_record,)
        )
        self.persist("run-agree-seat", artifact, candidate_seats + (agree_seat,))

        # The legitimate case: the delta-round binding ACCEPTS (does not raise);
        # compose still defers the whole delta chain to piece 3 (blocker 5).
        self.delta_binding("run-agree-seat", artifact)  # must not raise
        self.assert_compose_defers_delta("run-agree-seat", delta_head)


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
                epoch=10,
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
            epoch=11,
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

        # The escalation machinery resolves + binds correctly; compose defers the
        # whole delta chain to piece 3 (blocker 5).
        resolution = fg.resolve_chain_resolution(artifact)
        self.assertTrue(resolution.escalation.required)
        self.assertEqual(resolution.escalation.trigger, "auth_security")
        self.delta_binding("run-acc4", artifact)  # must not raise
        self.assertEqual(sorted(resolution.re_reviewed_findings), ["f1"])
        self.assert_compose_defers_delta("run-acc4", delta_head)


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

    # -- F3 (agent-harness#191 CR) branch table ------------------------------
    # (i) no run_id -> inert; (ii) run_id present but other inputs missing ->
    # BLOCK; (iii) run_id present but ProvenanceNotFound -> BLOCK. Fail-closed
    # BY CONSTRUCTION: once a trusted run_id is present, `[]` is never again a
    # possible outcome.

    def test_inert_without_fab_gate_inputs(self):
        """(i) — no `fab_gate_inputs` mapping at all."""
        ctx = cv.CloseoutContext(phase_alias="P1", plan_path="plan.md", repo_root=str(self.repo))
        self.assertEqual(fg.fab_gate_validator(ctx), [])

    def test_inert_when_fab_gate_inputs_has_no_run_id(self):
        """(i) — `fab_gate_inputs` present but no `run_id` key: never scoped
        to FAB (the trusted scope marker was never set by the caller)."""
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"live_base_ref_name": "main", "live_head_sha": "a" * 40},
        )
        self.assertEqual(fg.fab_gate_validator(ctx), [])

    def test_blocks_when_run_id_present_but_repo_root_missing(self):
        """(ii) — `run_id` present (trusted scope marker asserted) but
        `ctx.repo_root` is unset: the gate cannot complete, so it must BLOCK,
        never silently stay inert."""
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=None,
            fab_gate_inputs={"run_id": "run-x", "live_base_ref_name": "main", "live_head_sha": "a" * 40},
        )
        findings = fg.fab_gate_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, fg.FAB_GATE_FINDING_CODE)
        self.assertEqual(findings[0].severity, "block")
        self.assertEqual(findings[0].blocker_class, "review_gate_block")

    def test_blocks_when_run_id_present_but_live_pr_identity_missing(self):
        """(ii) — `run_id` present but `live_head_sha`/`live_base_ref_name`
        missing/malformed: BLOCK, never inert."""
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "run-x"},
        )
        findings = fg.fab_gate_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "block")
        self.assertEqual(findings[0].blocker_class, "review_gate_block")

    def test_blocks_when_run_id_has_no_provenance(self):
        """(iii) — `run_id` is present (the TRUSTED scope marker) but
        `compose_gate_status` raises `ProvenanceNotFound`: BLOCK, not inert —
        a phase the harness actually delta-reviewed must never pass merely
        because its provenance write was dropped."""
        ctx = cv.CloseoutContext(
            phase_alias="P1",
            plan_path="plan.md",
            repo_root=str(self.repo),
            fab_gate_inputs={"run_id": "no-such-run", "live_base_ref_name": "main", "live_head_sha": "a" * 40},
        )
        findings = fg.fab_gate_validator(ctx)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, fg.FAB_GATE_FINDING_CODE)
        self.assertEqual(findings[0].severity, "block")
        self.assertEqual(findings[0].blocker_class, "review_gate_block")
        self.assertIn("no-such-run", findings[0].reason)

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
