"""FAB (Consiliency/agent-harness#191) activation — piece 2 PRODUCER.

The producer is the atomic, flag-gated transaction that turns a PASSING governed
pre-merge review into a harness-authenticated `ReviewProvenanceArtifact` — but
ONLY when it can do so HONESTLY. It is invoked from the phase closeout (behind
`PHASE_LOOP_FAB`, byte-neutral when off) in two phases:

  1. `capture_review_at_invocation` (pre-commit, at review): freeze the
     epoch-scoped EXPECTED-seat manifest (the resolved invocation set), snapshot
     the reviewed bundle bytes into the run store, and persist a durable
     `SeatOutcomeRecord` per panel leg (real verdict + status + digests + a
     unique `seat_instance_id`) via `fab_gate.append_seat_outcome`. These durable
     records are the trust anchor; the artifact built later is verified AGAINST
     them, never trusted on its own (the anti-tautology property — the seats are
     captured from the REAL panel at invocation, not synthesized from the review
     return value).
  2. `finalize_and_gate` (post-commit): run the enforced HONESTY GATE
     (single-reviewed-commit-covers-PR, post-hook `commit^`/tree verify,
     non-empty, and the COMPLETE-REVIEW-REPRESENTATION predicate over the
     reviewed diff), then — only if it holds — build the artifact from the
     DURABLE records, `write_provenance`, `finalize_review_round` (bind the
     harness-issued round identity to the reviewed head + material), and run a
     DEDICATED HARD `compose_gate_status` that BLOCKS on non-pass regardless of
     `PHASE_LOOP_REVIEW` (never routed through the warn-downgradable closeout
     validator registry). Any honesty-gate failure ⇒ NO provenance ⇒ the closeout
     falls back to the existing non-FAB path; any hard-gate non-pass ⇒ the
     closeout BLOCKS.

Scope: piece 2 records the CANDIDATE round only (`delta_chain=()`). The
consumer delta-review shortcut, the durable coordinator admission record, and
committed-range re-review are piece 3 (Consiliency/agent-harness#191 follow-up),
NOT built here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .fab_canonical import (
    patch_digest,
    resolve_broker_repo_identity,
)
from .fab_gate import (
    ExpectedSeat,
    append_seat_outcome,
    compose_gate_status,
    finalize_review_round,
    read_seat_outcomes,
    review_round_path_for_run,
    write_expected_seats,
)
from .fab_provenance import (
    GATE_STATUS_PASS,
    REVIEW_SCOPE_WHOLE_PATCH,
    BaseBinding,
    BoundaryManifestRef,
    CandidateRecord,
    ProvenanceInvalid,
    ProvenanceSeat,
    ReviewProvenanceArtifact,
    ReviewScope,
    aggregate_material_digest,
    provenance_dir_for_run,
    snapshot_material,
    write_provenance,
)
from .fab_delta import BOUNDARY_MANIFEST_PATH
from .panel_invoker import PanelResult, SeatOutcomeRecord, terminal_verdict

# The reviewed bundle bytes, snapshotted into the run store as the round's
# immutable review material (design §6.4). A stable run-store file (not the
# ephemeral in-memory bundle) so `reverify_material`'s live-drift re-hash has a
# durable ref to check.
REVIEWED_BUNDLE_FILENAME = "fab-reviewed-bundle.md"
# The completeness verdict + digest of the EXACT diff text the panel saw
# (blocker-4 binding: the reviewed bytes, not a fresh re-run), persisted at
# invocation and compared against the committed range at finalize.
REVIEWED_DIFF_FILENAME = "fab-reviewed-diff.json"

# The staged-index-diff sentinels `governed_bundle.staged_index_diff` substitutes
# when it could NOT faithfully render the change (nonzero rc / decode failure /
# timeout / empty / no paths). If the panel was shown any of these, it did NOT
# see the changed bytes → FAIL CLOSED. Kept in sync with `staged_index_diff`.
_DIFF_ELISION_SENTINELS = (
    "(staged diff unavailable)",
    "(empty staged diff)",
    "(no staged paths)",
)


# The durable "safe to finalize" marker (CR round-2 crash safety). Written as the
# STRICT LAST step ONLY when the producer determined the closeout is safe to
# complete — an affirmative FAB gate PASS or an honest non-FAB DECLINE. It is
# NEVER written on a hard-gate BLOCK or on any exception between commit and gate
# decision. So its ABSENCE on a FAB-scoped run means "attempt 1 blocked or
# crashed" → the resume/noop path must re-gate and fail closed.
CLEARED_FILENAME = "fab-closeout-cleared.json"


def fab_run_id_for_reviewed_tree(reviewed_tree: str) -> str:
    """The deterministic, harness-computed run id a FAB candidate round is keyed
    by, derived from the REVIEWED (staged) tree sha — known pre-commit, when the
    seats are captured and the expected manifest is frozen (the committed head
    does not exist yet). NOT derived from any PR/agent-controlled field. Piece 3
    binds this at admission time in a durable coordinator record; piece 2 uses it
    only within a single self-contained closeout, so a merge-side recompute from
    the head is intentionally NOT part of the contract."""
    return f"fab-{reviewed_tree}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_fab_scoped(repo: Path, run_id: str) -> bool:
    """True iff this run was scoped to FAB — i.e. `capture_review_at_invocation`
    ran and froze the expected-seat manifest (the durable round record exists).
    The resume/noop path uses this to tell a FAB commit from a plain one."""
    try:
        return review_round_path_for_run(repo, run_id).exists()
    except ProvenanceInvalid:
        return False


def mark_closeout_cleared(repo: Path, run_id: str) -> None:
    """Write the durable "safe to finalize" marker (STRICT LAST step). Only call
    on an affirmative FAB PASS or an honest DECLINE — never on a block/crash."""
    path = provenance_dir_for_run(repo, run_id) / CLEARED_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"cleared": True, "at": _utc_now_iso()}, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def is_closeout_cleared(repo: Path, run_id: str) -> bool:
    try:
        return (provenance_dir_for_run(repo, run_id) / CLEARED_FILENAME).exists()
    except ProvenanceInvalid:
        return False


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)


def _seat_instance_id(run_id: str, epoch: int, seat_key: str, index: int) -> str:
    return f"{run_id}:{epoch}:{seat_key}:{index}"


def _diff_text_elision(text: str) -> str | None:
    """Return a reason iff the (already-rendered) diff `text` the panel saw is an
    elision — a `staged_index_diff` sentinel or a binary-elided marker — else
    `None`. This is the text-level half of the completeness predicate; the
    numstat probe is the independent structural half."""
    for sentinel in _DIFF_ELISION_SENTINELS:
        if sentinel in text:
            return f"reviewed diff carries the elision sentinel {sentinel!r}"
    if "Binary files" in text and "differ" in text:
        return "reviewed diff renders a binary file only as 'Binary files ... differ'"
    return None


def _numstat_binary_elision(repo: Path, left: str | None, right: str | None, paths: Sequence[str]) -> str | None:
    """Return a reason iff `git diff [left] [right] --numstat` (staged when
    left/right are None) shows a binary / attribute-suppressed (`-\\t-`) path,
    fails, has an unexpected shape, or is empty. Independent structural evidence
    the seats saw every changed byte — never trusts the rendered diff alone."""
    rev_args = [a for a in (left, right) if a is not None]
    numstat = _git(repo, "diff", *rev_args, "--numstat", *(["--cached"] if not rev_args else []), "--", *paths)
    if numstat.returncode != 0:
        return f"git diff --numstat failed (fail-closed): {(numstat.stderr or '').strip()!r}"
    saw = False
    for line in numstat.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            return f"unexpected numstat line (fail-closed): {line!r}"
        saw = True
        if parts[0] == "-" or parts[1] == "-":
            return f"changed path {'/'.join(parts[2:])!r} is binary/attribute-suppressed (numstat '-\\t-')"
    if not saw:
        return "reviewed diff enumerated zero changed paths (fail-closed, non-empty required)"
    return None


def _normalized_diff_digest(text: str) -> str:
    """SHA-256 over the diff text with trailing whitespace stripped (matching
    `staged_index_diff`'s own `.rstrip()`), so the reviewed (`git diff --cached`)
    and committed (`git diff base head`) representations — byte-identical because
    they compare the same tree pair — digest identically."""
    return _sha256_hex(text.rstrip().encode("utf-8"))


def capture_review_at_invocation(
    repo: Path,
    run_id: str,
    panel: PanelResult,
    *,
    epoch: int,
    reviewed_bundle_text: str,
    reviewed_diff_text: str,
    closeout_dirty_paths: Sequence[str],
) -> None:
    """Phase 1 (pre-commit, harness-only): snapshot the reviewed bundle bytes,
    persist the completeness + digest of the EXACT diff text the panel was shown
    (blocker-4 binding), freeze the epoch-scoped EXPECTED-seat manifest (the
    RESOLVED invocation set — `invoke_panel` emits exactly one terminal leg per
    dispatched seat, so a required seat that timed out / degraded still appears
    here and is still demanded by the gate), and persist a durable
    `SeatOutcomeRecord` per leg with its REAL `terminal_verdict` + status +
    evidence digest.

    The durable seat records are the trust anchor: the artifact `finalize_and_
    gate` builds later mirrors these (built FROM the durable ledger, read back
    from disk), and the gate re-reads them from the run store — so a forged
    artifact whose seats do not match the durable ledger BLOCKS.

    `reviewed_diff_text` is the STAGED diff text the panel actually reviewed
    (`governed_bundle.staged_index_diff` output — sentinel-substituted on any
    render failure). We record whether it was complete + its digest; `finalize_
    and_gate` fails closed unless the committed range re-renders to the SAME
    digest and is itself complete — so a transient render failure that fed the
    seats a sentinel can never be "fixed up" by a later successful re-run."""
    reviewed_bytes = reviewed_bundle_text.encode("utf-8")
    reviewed_artifact_digest = _sha256_hex(reviewed_bytes)
    run_dir = provenance_dir_for_run(repo, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / REVIEWED_BUNDLE_FILENAME).write_bytes(reviewed_bytes)

    # Record the reviewed diff's completeness + digest — from what the panel SAW
    # (`reviewed_diff_text`), plus an independent numstat probe over the staged
    # index. Any elision ⇒ complete=False ⇒ no provenance at finalize.
    reason = _diff_text_elision(reviewed_diff_text) or _numstat_binary_elision(
        repo, None, None, closeout_dirty_paths
    )
    reviewed_diff_record = {
        "complete": reason is None,
        "digest": None if reason is not None else _normalized_diff_digest(reviewed_diff_text),
        "reason": reason,
    }
    (run_dir / REVIEWED_DIFF_FILENAME).write_text(
        json.dumps(reviewed_diff_record, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

    expected: list[ExpectedSeat] = []
    for index, leg in enumerate(panel.legs):
        seat_key = leg.seat_key or leg.leg
        instance_id = _seat_instance_id(run_id, epoch, seat_key, index)
        verdict = terminal_verdict(leg.text)
        evidence_digest = _sha256_hex((leg.text or "").encode("utf-8"))
        durable = SeatOutcomeRecord(
            seat_key=seat_key,
            vendor_leg=leg.leg,
            required=True,
            status=leg.status,
            attempt_id=f"{run_id}:{epoch}:{index}",
            epoch=epoch,
            artifact_digest=reviewed_artifact_digest,
            completed_at=_utc_now_iso(),
            evidence_digest=evidence_digest,
            reason=None,
            verdict=verdict,
            finding_ids=(),
            seat_instance_id=instance_id,
        )
        append_seat_outcome(repo, run_id, durable)
        expected.append(
            ExpectedSeat(
                seat_instance_id=instance_id,
                seat_key=seat_key,
                vendor_leg=leg.leg,
                required=True,
            )
        )
    write_expected_seats(repo, run_id, epoch=epoch, expected_seats=tuple(expected))


@dataclass(frozen=True, kw_only=True)
class ProducerOutcome:
    """The result of the post-commit producer transaction.

      * `wrote_provenance=False` — the honesty gate did not hold (multi-commit
        PR / hook-mutated tree / empty closeout / incomplete review
        representation) or the merge-base could not be resolved. No provenance
        was written; the closeout falls back to the existing non-FAB path.
        `skipped_reason` names why. NOT a block.
      * `wrote_provenance=True, blocked=False` — provenance written and the
        dedicated hard gate PASSED.
      * `wrote_provenance=True, blocked=True` — provenance written but the hard
        gate did NOT pass; the closeout MUST block (`block_reason`)."""

    wrote_provenance: bool
    run_id: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    skipped_reason: str | None = None


def _bind_reviewed_to_committed_representation(
    repo: Path, run_id: str, base_sha: str, head_sha: str, closeout_dirty_paths: Sequence[str]
) -> str | None:
    """Return `None` when the reviewed diff (what the panel SAW, recorded at
    capture) was complete AND the committed range `base..head` re-renders to the
    SAME bytes and is itself complete — else a fail-closed reason (blocker-4
    binding). Tree-equality alone does NOT prove the seats saw every changed
    byte, and `staged_index_diff` can feed the seats a sentinel on a transient
    render failure; a fresh re-run must never "fix that up". So this:

      1. reads the reviewed-diff record persisted at invocation — absent or
         `complete=False` ⇒ the panel saw an elision ⇒ no provenance;
      2. re-renders the COMMITTED range, decode-checks it, runs the numstat
         elision probe, and requires it be complete;
      3. requires the committed representation's digest to EQUAL the reviewed
         digest — reviewed ≠ committed bytes ⇒ no provenance."""
    record_path = provenance_dir_for_run(repo, run_id) / REVIEWED_DIFF_FILENAME
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"reviewed-diff record missing/unreadable (fail-closed): {exc}"
    if not isinstance(record, dict) or not record.get("complete") or not record.get("digest"):
        return f"the reviewed diff the panel saw was not complete (fail-closed): {record.get('reason') if isinstance(record, dict) else record!r}"
    reviewed_digest = record["digest"]

    # Re-render the committed range and require it decode as text.
    try:
        raw = subprocess.run(
            ["git", "-C", str(repo), "diff", base_sha, head_sha, "--", *closeout_dirty_paths],
            capture_output=True, timeout=30,
        )
        if raw.returncode != 0:
            return f"committed-range git diff failed (fail-closed): {(raw.stderr or b'').decode('utf-8', 'replace').strip()!r}"
        committed_text = raw.stdout.decode("utf-8", errors="strict")
    except (subprocess.SubprocessError, UnicodeDecodeError) as exc:
        return f"committed-range diff not fully text-representable (fail-closed): {exc}"
    gap = _diff_text_elision(committed_text) or _numstat_binary_elision(
        repo, base_sha, head_sha, closeout_dirty_paths
    )
    if gap is not None:
        return gap
    if _normalized_diff_digest(committed_text) != reviewed_digest:
        return (
            "committed representation digest does not match what the panel reviewed (fail-closed): the "
            "reviewed and committed diffs differ"
        )
    return None


def _resolve_merge_base(repo: Path, origin: str, base_ref_name: str, head_sha: str) -> str | None:
    """Resolve `merge-base(origin/<base_ref_name>, head_sha)` EXACTLY the way
    `fab_canonical.equivalent()` does (fetch `origin base_ref_name` → FETCH_HEAD →
    merge-base), so the base_sha the producer binds is byte-identical to what the
    gate's live equivalence re-check will recompute. Returns `None` (fail-closed
    → no provenance) on any git failure — a base that cannot be honestly resolved
    is a base FAB refuses to vouch for."""
    fetch = _git(repo, "fetch", "--no-tags", origin, base_ref_name)
    if fetch.returncode != 0:
        return None
    mb = _git(repo, "merge-base", "FETCH_HEAD", head_sha)
    if mb.returncode != 0:
        return None
    sha = mb.stdout.strip()
    return sha or None


def finalize_and_gate(
    repo: Path,
    run_id: str,
    *,
    epoch: int,
    reviewed_base_sha: str,
    reviewed_tree: str,
    committed_head_sha: str,
    closeout_dirty_paths: Sequence[str],
    base_ref_name: str,
    origin: str = "origin",
) -> ProducerOutcome:
    """Phase 2 (post-commit, harness-only). Runs the enforced honesty gate, and
    only if it holds, builds + writes provenance and runs the DEDICATED HARD
    gate. See module docstring for the transaction order and the fail-open /
    block semantics of `ProducerOutcome`."""
    # -- Honesty gate (fail-closed → no provenance, fall back) ---------------
    if not closeout_dirty_paths:
        return ProducerOutcome(wrote_provenance=False, skipped_reason="empty_closeout")

    # Post-hook parent/tree verify: the commit that actually landed (post any
    # pre-commit hooks) must have the reviewed base as its parent AND the exact
    # reviewed tree — a hook that mutated the tree, or a moved HEAD, invalidates
    # the review.
    parent = _git(repo, "rev-parse", f"{committed_head_sha}^")
    if parent.returncode != 0 or parent.stdout.strip() != reviewed_base_sha:
        return ProducerOutcome(
            wrote_provenance=False,
            skipped_reason="multi_commit_or_moved_head",  # commit^ != reviewed base (parent moved / >1 parent)
        )
    tree = _git(repo, "rev-parse", f"{committed_head_sha}^{{tree}}")
    if tree.returncode != 0 or tree.stdout.strip() != reviewed_tree:
        return ProducerOutcome(wrote_provenance=False, skipped_reason="hook_mutated_tree")

    # Single reviewed commit covers the PR: precommit HEAD == merge-base(origin/
    # base, head). A multi-commit PR (reviewed base is ahead of the merge-base)
    # is OUT OF SCOPE → no provenance, never silently attached.
    merge_base = _resolve_merge_base(repo, origin, base_ref_name, committed_head_sha)
    if merge_base is None:
        return ProducerOutcome(wrote_provenance=False, skipped_reason="merge_base_unresolved")
    if merge_base != reviewed_base_sha:
        return ProducerOutcome(wrote_provenance=False, skipped_reason="multi_commit_pr_out_of_scope")

    # Complete review representation: bind what the panel SAW (recorded at
    # invocation) to the committed range (same digest + itself complete).
    representation_gap = _bind_reviewed_to_committed_representation(
        repo, run_id, merge_base, committed_head_sha, closeout_dirty_paths
    )
    if representation_gap is not None:
        return ProducerOutcome(wrote_provenance=False, skipped_reason=f"incomplete_review_representation:{representation_gap}")

    # -- Build the artifact FROM the durable records (never the panel) --------
    durable = read_seat_outcomes(repo, run_id)
    if not durable:
        # A FAB round that reached finalize with no durable seats is a broken
        # capture — never emit vacuous provenance.
        return ProducerOutcome(wrote_provenance=False, skipped_reason="no_durable_seats")

    try:
        repo_slug = resolve_broker_repo_identity(repo)
        pd = patch_digest(repo, merge_base, committed_head_sha, repo_slug=repo_slug)
    except Exception as exc:  # noqa: BLE001 - fail-closed, never crash closeout
        return ProducerOutcome(wrote_provenance=False, skipped_reason=f"patch_digest_failed:{exc}")

    provenance_seats = tuple(
        ProvenanceSeat(
            seat_key=d.seat_key,
            vendor_leg=d.vendor_leg,
            required=d.required,
            status=d.status,
            epoch=d.epoch,
            artifact_digest=d.artifact_digest,
            evidence_digest=d.evidence_digest,
            verdict=d.verdict,
            finding_ids=d.finding_ids,
            seat_instance_id=d.seat_instance_id,
        )
        for d in durable
        if d.epoch == epoch
    )

    try:
        material_digests = snapshot_material(
            repo, run_id, [str((provenance_dir_for_run(repo, run_id) / REVIEWED_BUNDLE_FILENAME))]
        )
    except ProvenanceInvalid as exc:
        return ProducerOutcome(wrote_provenance=False, skipped_reason=f"material_snapshot_failed:{exc}")
    reviewed_material_digest = aggregate_material_digest(material_digests)

    manifest_bytes = _read_manifest_bytes(repo, merge_base)
    boundary_manifest = BoundaryManifestRef(
        path=BOUNDARY_MANIFEST_PATH, source_rev=merge_base, digest=_sha256_hex(manifest_bytes)
    )
    review_scope = ReviewScope(
        mode=REVIEW_SCOPE_WHOLE_PATCH,
        reviewed_material_digest=reviewed_material_digest,
        covers_patch_digest=pd,
    )
    candidate = CandidateRecord(head_sha=committed_head_sha, review_scope=review_scope, patch_digest=pd)
    artifact = ReviewProvenanceArtifact.build(
        repo=repo_slug,
        base=BaseBinding(ref_identity=f"{repo_slug}#{base_ref_name}", base_sha=merge_base),
        boundary_manifest=boundary_manifest,
        candidate=candidate,
        seats=provenance_seats,
        findings=(),  # a clean candidate-round pass carries no unresolved findings (piece 2)
        material_digests=material_digests,
        delta_chain=(),
    )
    write_provenance(repo, run_id, artifact)

    # Bind the harness-issued round identity to the reviewed head + material.
    finalize_review_round(
        repo,
        run_id,
        reviewed_head_sha=committed_head_sha,
        reviewed_material_digest=reviewed_material_digest,
        canonical_findings=(),
    )

    # -- Dedicated HARD gate: block on non-pass, NEVER warn-downgraded --------
    gate_status = compose_gate_status(
        repo=repo,
        run_id=run_id,
        live_base_ref_name=base_ref_name,
        live_head_sha=committed_head_sha,
        origin=origin,
    )
    if gate_status.status == GATE_STATUS_PASS:
        return ProducerOutcome(wrote_provenance=True, run_id=run_id, blocked=False)
    reason = gate_status.equivalence_verified.reason if gate_status.equivalence_verified else None
    return ProducerOutcome(
        wrote_provenance=True,
        run_id=run_id,
        blocked=True,
        block_reason=f"FAB producer hard gate did not pass (status={gate_status.status}); reason={reason}",
    )


def _read_manifest_bytes(repo: Path, rev: str) -> bytes:
    """The boundary-manifest bytes at `rev` (empty when absent) — recorded as a
    metadata reference on the candidate round (Lane C enforces the manifest on
    DELTA rounds; piece 2's candidate round only pins the reference + digest)."""
    show = _git(repo, "show", f"{rev}:{BOUNDARY_MANIFEST_PATH}")
    if show.returncode != 0:
        return b""
    return (show.stdout or "").encode("utf-8")
