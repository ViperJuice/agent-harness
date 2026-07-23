"""FAB (Consiliency/agent-harness#191) Lane D — gate-output composition,
authenticity cross-check, immutable-material re-verify, and agent-review-gate
wiring.

Ground: `plans/design-fab-191-delta-review.md` (v2, panel-reviewed) §8 (gate
output contract `fab.gate-status.v2` + `verdict_binds_to_equivalent`), §6.3
(`SeatOutcomeRecord` cross-check, T13), §6.4 (immutable material re-verify,
T14), §4.4 (promotion-time re-assertion), §9 Lane D bullet. Builds on Lane A
(`fab_provenance.py` — frozen schemas, hash chain, trust-root run store, `
reverify_material`/`aggregate_material_digest`), Lane B (`fab_canonical.py` —
`equivalent`/`EquivalenceBinding`), and Lane C (`fab_delta.py` —
`enforce_review_scope_for_escalation`). This is the FINAL lane: it makes FAB
actually DECIDE (`status ∈ {pass, review_gate_block}`) and wires that decision
into the existing `closeout_validators` registry and the
`governed_premerge.run_governed_premerge_loop` pre-merge gate.

PRE-STATED TRUST BOUNDARY (decided per agent-harness#276 / Lanes A-C — evaluated
against, NOT re-opened): `git` is TRUSTED CI plumbing; the attacker controls
the PR BRANCH CONTENTS, not the trusted run-store bytes nor the trusted git
binary's stdout. This module reads provenance and durable `SeatOutcomeRecord`s
ONLY from the trusted run store (harness-only-written, keyed by `run_id` — Lane
A §6.1a); authenticity comes from cross-checking provenance seats against the
durable `SeatOutcomeRecord` the harness wrote during the real review run
(§6.3) — additive, since both live in the same trusted run store. The
promotion re-assertion (`governed_premerge`) reads the LIVE PR at merge
(base-ref identity + fresh fetch — Lane B `equivalent()`). This module
introduces no check that requires compromising trusted git stdout.

**`run_id` trust (design §6.1a / §9 Lane D requirement, resolved here).** Lane
A's `reject_client_supplied_provenance` narrows to "the path resolves to the
run-store location for `run_id` AND is not git-tracked" — it does NOT prove
`run_id` itself came from a trustworthy source. This module's read entry
points (`compose_gate_status`, the closeout validator) never derive `run_id`
from `CloseoutContext.terminal`/`automation` (agent/PR-influenced self-report
fields) — `run_id` and the live PR identity arrive ONLY via a dedicated,
untyped `CloseoutContext.fab_gate_inputs` mapping (see `fab_gate_validator`)
that the CALLER (the CI/runner wiring, never the reviewed agent) is
responsible for populating from ITS OWN trusted process-local run-allocation
state. This module enables and enforces the read-from-trusted-location
contract; it cannot itself prove the caller wired a genuinely trusted value in
— that residual (same class as the Lane A trust-model note) is inherent to any
system where the harness process and the reviewed code share a filesystem.

FROZEN INTERFACE (IF-0-FAB-D-1) — this lane is terminal (no further lane
consumes it), but its public names are still a stable contract other code
(train_runner, the broker, CI wiring) links against:

  * `compose_gate_status(...)` — composes `fab_provenance.GateStatus`
    (`fab.gate-status.v2`). NEVER raises for an in-scope fail-closed condition
    (mirrors `fab_canonical.equivalent()`'s posture) EXCEPT
    `fab_provenance.ProvenanceNotFound`, which propagates deliberately — "no
    provenance recorded for this run_id" is categorically different from "a
    recorded provenance is broken/tampered", and only the caller can tell
    "not applicable" from "should block" apart (see `fab_gate_validator`).
  * `verdict_binds_to_equivalent(finding, gate_status)` — design §8 finding 5:
    `verdict_binds_to(finding, gate_status.reviewed_sha)` (#88, REUSED from
    `closeout_validators`, never reimplemented) AND
    `gate_status.equivalence_verified.result == EQUIVALENT`. Two independent
    facts ANDed; `reviewed_sha` is NEVER the live/final PR head (T16).
  * `cross_check_seat_authenticity(...)` (§6.3/T13) and `reverify_all_material`
    (§6.4/T14) — the two authenticity primitives `compose_gate_status` runs;
    exposed standalone so a caller can run them independently of full gate
    composition (e.g. a diagnostic tool).
  * `fab_gate_validator` — a `@register_closeout_validator` following the
    EXACT `verification_evidence_validator`/`visual_avatar_evidence_validator`
    pattern: `severity="block"`, `blocker_class="review_gate_block"` (non-human
    — `apply_review_findings` never sets `human_required`), warn-default via
    the global `PHASE_LOOP_REVIEW` control, opt-in `block`. Inert (`[]`) when
    `CloseoutContext.fab_gate_inputs` is absent or the run recorded no FAB
    provenance (`ProvenanceNotFound`) — this validator never fabricates a
    finding about a repo/run that isn't using FAB delta review.
  * `FabPromotionCheck` / the `governed_premerge.run_governed_premerge_loop`
    wiring (design §4.4) — see that module for the promotion-time re-assertion
    itself; this module only supplies the reusable `resolve_equivalence_binding`
    the promotion check re-derives its bound tuple from.

Design ambiguities resolved in this lane (stated once, not re-litigated):

  1. **`GateStatus` does not itself carry `repo_slug`/`base_ref_identity`**
     (see the frozen §8 JSON schema — only `reviewed_sha`/`deltas`/
     `equivalence_verified`/etc.). The promotion-time re-assertion (§4.4)
     therefore does NOT try to extract a binding FROM a previously-composed
     `GateStatus`; it independently re-resolves `EquivalenceBinding` from the
     SAME trusted provenance artifact via `resolve_equivalence_binding`,
     exactly like `compose_gate_status` does at gate time. This keeps the
     public `GateStatus` record a lean, externally-consumable echo (what the
     GitHub check actually reads) while the FULL internal binding needed to
     re-verify always comes from the trusted run store, never from the
     leaner output record.

  2. **"No unresolved block finding remains" (§8) is evaluated against
     `artifact.findings`'s current top-level `status`, cross-checked against
     the FINAL delta round's own `reopened_finding_ids`/`resolved_finding_ids`
     audit trail** — not `status` alone. Findings live once at the artifact's
     top level (Lane A resolved-ambiguity #3); nothing in Lanes A-C mutates
     `Finding.status` when a later round's carry-forward decision reopens a
     previously-clean finding (`fab_delta.carry_forward` returns
     `reopened_finding_ids`, it does not rewrite `Finding.status`). A finding
     the final round's carry-forward reopened, but whose id is NOT ALSO in
     that same round's `resolved_finding_ids`, is therefore UNRESOLVED
     regardless of what `Finding.status` says — the reopen audit trail always
     wins over a possibly-stale top-level status (fail-closed: a stale
     "clean" snapshot can never mask a live reopen).

  3. **A provenance artifact with ZERO required seats cannot PASS.** Design
     §8's "every required seat has a non-DISAGREE verdict" is vacuously true
     over an empty seat set — a real gap for an artifact that never recorded
     any required reviewer at all. `compose_gate_status` additionally requires
     `any(seat.required for seat in artifact.seats)`; an artifact with no
     required seats blocks with reason `"no_required_seats"`, never silently
     passes on vacuous truth.

  4. **The durable `SeatOutcomeRecord` ledger location.** No existing writer
     persists individual `SeatOutcomeRecord`s to a FAB-run-scoped durable
     store (`panel_invoker.persist_seat_outcome` takes an injectable
     `append_sink` with no fixed destination; the only existing durable sink,
     `convergence.event_log`, is the TRAIN coordinator's own per-train-id log,
     a different trust domain from a single PR's FAB review run). This module
     defines that missing piece — `seat_outcomes_path_for_run`/
     `append_seat_outcome`/`read_seat_outcomes` — as a JSONL ledger SIBLING to
     the provenance artifact under the SAME `fab_provenance.provenance_dir_for_run`
     root, reusing `panel_invoker.serialize_seat_outcome` for the write side
     (never reimplementing seat-outcome serialization) and the SAME strict,
     duplicate-key-rejecting JSON parse discipline (`fab_provenance.
     _strict_object_pairs_hook`, reused) for the read side.

  5. **The closeout validator's `live_base_ref_name` may fall back to the
     artifact's own bound name when no live-PR host context is available.**
     A bare phase closeout (no GitHub PR object in scope) cannot always supply
     a real live base ref independently resolved from host state; when the
     caller has none, falling back to the bound name makes the retarget check
     (I1) trivially satisfied but still exercises the stronger fresh-fetch
     merge-base + live content-drift checks. The FULL security backstop
     against retarget always has real live-PR host context and lives at
     `governed_premerge`'s promotion-time re-assertion (§4.4) — see that
     module's docstring. This is a documented, deliberate scope split, not an
     oversight.

Fail-closed discipline (this is where FAB actually decides): every ambiguous
or unrepresentable state — unreadable/tampered provenance, a broken hash
chain, an unresolved delta-chain tail, a seat with no matching durable record,
a mutated material snapshot, a malformed base-ref identity — resolves to
`GATE_STATUS_BLOCK` (surfaced as a non-human `review_gate_block`), never a
silent pass. Additive only: nothing in `fab_provenance.py`, `fab_canonical.py`,
`fab_delta.py`, `panel_invoker.py`, or `closeout_validators.py` is modified
beyond the minimal, additive wiring this lane strictly needs (a new optional
`CloseoutContext.fab_gate_inputs` field and a new optional `fab_gate_inputs`
parameter on `build_phase_loop_closeout` that threads straight through);
every FAB primitive is imported and reused, never re-implemented.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .closeout_validators import (
    CloseoutContext,
    ReviewFinding,
    register_closeout_validator,
    verdict_binds_to,
)
from .fab_canonical import EquivalenceBinding, equivalent
from .fab_delta import enforce_review_scope_for_escalation
from .fab_provenance import (
    DELTA_STATUS_ESCALATED_WHOLE_PATCH,
    DELTA_STATUS_REVIEWED_CLEAN,
    EQUIVALENCE_EQUIVALENT,
    GATE_STATUS_BLOCK,
    GATE_STATUS_PASS,
    Escalation,
    EquivalenceResult,
    EquivalenceVerified,
    Finding,
    GateDeltaEntry,
    GateStatus,
    MaterialDigest,
    ProvenanceInvalid,
    ProvenanceNotFound,
    ProvenanceSeat,
    ReviewProvenanceArtifact,
    ReviewScope,
    _strict_object_pairs_hook,  # reused strict-parse discipline, not reimplemented
    aggregate_material_digest,  # noqa: F401 - re-exported for callers/tests
    provenance_dir_for_run,
    read_provenance,
    reverify_material,
    verify_chain,
)
from .panel_invoker import SeatOutcomeRecord, serialize_seat_outcome

# --------------------------------------------------------------------------- #
# Exceptions — ProvenanceInvalid subclasses so every FAB lane shares one
# catchable trust-root exception family (Lane A/B/C precedent).
# --------------------------------------------------------------------------- #


class GateChainUnresolved(ProvenanceInvalid):
    """The delta chain's final round is not in a resolved, pass-eligible
    state (`pending`/`invalidated`), has no `resulting_head_digest`, fails its
    T5 review-scope re-check, or the artifact's `base.ref_identity` is
    malformed — any of which make it impossible to derive a governing
    `EquivalenceBinding` for the gate's content-equivalence check."""


class SeatAuthenticityInvalid(ProvenanceInvalid):
    """T13: a provenance seat has no matching durable `SeatOutcomeRecord`,
    disagrees with it on a load-bearing field, a required seat's durable
    status is not a usable terminal, or the durable ledger itself carries
    conflicting records for the same (seat_key, vendor_leg, epoch) key."""


# --------------------------------------------------------------------------- #
# §6.3 — durable `SeatOutcomeRecord` ledger (design ambiguity #4)
# --------------------------------------------------------------------------- #

SEAT_OUTCOMES_FILENAME = "fab-seat-outcomes.jsonl"
_MAX_SEAT_OUTCOME_LINE_BYTES = 64 * 1024

# panel_invoker._classify_leg's ONLY "a real, conforming review actually
# happened" terminal (panel_invoker.py:928-975) is "OK" — TIMEOUT/DEGRADED/
# ERROR/EMPTY are all non-usable. Frozen here as the "usable terminal" set
# design §6.3 requires a required seat's durable status to be a member of.
#
# Compared case-INSENSITIVELY (see `_is_usable_terminal_status`) — this is a
# fixed, design-time literal set this module itself controls, not two
# untrusted records being checked for AGREEMENT (that remains a STRICT,
# case-sensitive equality below — FAB's "no normalization" doctrine still
# applies there, since divergence there is exactly what detects tampering).
# The codebase has no single frozen casing convention for this field today:
# `panel_invoker._classify_leg` returns uppercase `"OK"`, the one existing
# `SeatOutcomeRecord` construction site (`test_convergence_seat_lifecycle.py`)
# matches that, but Lane A/C's OWN `ProvenanceSeat` test fixtures use
# lowercase `"ok"` and design §6.5's schema example is also lowercase.
# `ProvenanceSeat.status`/`SeatOutcomeRecord.status` are both free-form `str`
# fields with no enum in Lane A — treating this usability check as
# case-sensitive would make it a real production landmine (every required
# seat silently failing "usable terminal" the moment a real writer happens to
# emit a different case than this literal) that no test using SELF-CONSISTENT
# fixtures on both sides could ever catch. Fail-closed still holds in either
# direction: TIMEOUT/DEGRADED/ERROR/EMPTY remain rejected regardless of case.
USABLE_TERMINAL_SEAT_STATUSES = frozenset({"OK"})


def _is_usable_terminal_status(status: str) -> bool:
    return status.upper() in USABLE_TERMINAL_SEAT_STATUSES

_SEAT_OUTCOME_FIELDS = {f.name for f in dataclasses.fields(SeatOutcomeRecord)}


def seat_outcomes_path_for_run(repo: Path, run_id: str) -> Path:
    """The trusted run-store location for durable `SeatOutcomeRecord`s for
    `run_id` — a sibling to the provenance artifact under the SAME
    `fab_provenance.provenance_dir_for_run` root, so both live in the
    identical trusted, run-id-keyed location (Lane A §6.1a)."""
    return provenance_dir_for_run(repo, run_id) / SEAT_OUTCOMES_FILENAME


def append_seat_outcome(repo: Path, run_id: str, record: SeatOutcomeRecord) -> None:
    """Harness-only-written append (mirrors `fab_provenance.write_provenance`'s
    posture): appends ONE serialized `SeatOutcomeRecord` line, reusing
    `panel_invoker.serialize_seat_outcome` (never reimplemented) for the
    encoding. This is where a caller should point `panel_invoker.
    persist_seat_outcome`'s injectable `append_sink` for a FAB review run;
    this module only defines WHERE the durable ledger lands and how it is
    read back, not WHEN a caller should call it."""
    line = serialize_seat_outcome(record)
    raw = (line + "\n").encode("utf-8")
    if len(raw) > _MAX_SEAT_OUTCOME_LINE_BYTES:
        raise ProvenanceInvalid(
            f"seat-outcome record for {record.seat_key!r} exceeds max line size "
            f"{_MAX_SEAT_OUTCOME_LINE_BYTES} bytes (fail-closed, not written)"
        )
    path = seat_outcomes_path_for_run(repo, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


def _seat_outcome_from_dict(d: Mapping[str, Any]) -> SeatOutcomeRecord:
    unknown = set(d.keys()) - _SEAT_OUTCOME_FIELDS
    if unknown:
        raise ProvenanceInvalid(
            f"seat-outcome record has unknown field(s) {sorted(unknown)!r} (fail-closed, strict "
            "trust-root parse — an unaudited field must never ride along)"
        )
    try:
        return SeatOutcomeRecord(
            seat_key=str(d["seat_key"]),
            vendor_leg=str(d["vendor_leg"]),
            required=bool(d["required"]),
            status=str(d["status"]),
            attempt_id=str(d["attempt_id"]),
            epoch=int(d["epoch"]),
            artifact_digest=str(d["artifact_digest"]),
            completed_at=str(d["completed_at"]),
            evidence_digest=str(d["evidence_digest"]),
            reason=(str(d["reason"]) if d.get("reason") is not None else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProvenanceInvalid(f"malformed seat-outcome record (fail-closed): {exc}") from exc


def read_seat_outcomes(repo: Path, run_id: str) -> tuple[SeatOutcomeRecord, ...]:
    """The gate's ONLY read path for durable `SeatOutcomeRecord`s (design
    §6.3) — reads exclusively from the trusted run-store ledger for `run_id`,
    NEVER from any client/PR-supplied blob. A missing ledger returns `()`
    (legitimate: no seats have been persisted yet, or a pre-Lane-D
    provenance artifact) — every provenance seat will then simply fail its
    cross-check for lack of a matching record, which IS the fail-closed
    behavior (see `cross_check_seat_authenticity`), so an absent ledger is
    not itself special-cased as an error here. A malformed/truncated LINE, by
    contrast, fails the ENTIRE read closed: unlike
    `convergence.event_log.read_convergence_events`'s "tolerate a torn final
    line" posture (safe there because an in-progress append is expected
    mid-write for a shared multi-node log), a torn/tampered seat-outcome
    ledger is a trust-root integrity concern this module refuses to
    partially trust."""
    path = seat_outcomes_path_for_run(repo, run_id)
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProvenanceInvalid(f"seat-outcome ledger unreadable (fail-closed): {exc}") from exc
    records: list[SeatOutcomeRecord] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line, object_pairs_hook=_strict_object_pairs_hook)
        except json.JSONDecodeError as exc:
            raise ProvenanceInvalid(
                f"seat-outcome ledger line {line_no} is malformed JSON (fail-closed): {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ProvenanceInvalid(f"seat-outcome ledger line {line_no} is not a JSON object (fail-closed)")
        records.append(_seat_outcome_from_dict(parsed))
    return tuple(records)


def cross_check_seat_authenticity(
    provenance_seats: Sequence[ProvenanceSeat],
    durable_seat_outcomes: Sequence[SeatOutcomeRecord],
) -> None:
    """design §6.3/T13: cross-check EVERY provenance seat against the durable
    `SeatOutcomeRecord` for the same (`seat_key`, `vendor_leg`, `epoch`),
    requiring agreement on `required`/terminal `status`/`artifact_digest`/
    `evidence_digest` (the join key itself proves `seat_key`/`vendor_leg`/
    `epoch` agreement). Raises `SeatAuthenticityInvalid` (fail-closed) on the
    FIRST mismatch:

      * the durable ledger carries conflicting records for the same key
        (ambiguous, never resolved by "pick one");
      * a provenance seat has NO matching durable record at all (T13's core
        exploit: a hand-written provenance vouching for a seat that never
        ran);
      * `required`/`status`/`artifact_digest`/`evidence_digest` disagree
        between the provenance seat and its durable record;
      * a REQUIRED seat's durable `status` is not a usable terminal
        (`USABLE_TERMINAL_SEAT_STATUSES`) — e.g. a required seat that only
        ever reached `TIMEOUT`/`DEGRADED`/`ERROR`/`EMPTY` cannot vouch for a
        clean review no matter what the provenance seat claims.

    Never raises on an EXTRA durable record with no corresponding provenance
    seat (a seat that ran but whose outcome the review round chose not to
    fold into provenance is not itself a forgery)."""
    index: dict[tuple[str, str, int], SeatOutcomeRecord] = {}
    for record in durable_seat_outcomes:
        key = (record.seat_key, record.vendor_leg, record.epoch)
        if key in index and index[key] != record:
            raise SeatAuthenticityInvalid(
                f"durable seat-outcome ledger has conflicting records for "
                f"seat_key={key[0]!r} vendor_leg={key[1]!r} epoch={key[2]!r} (fail-closed)"
            )
        index[key] = record

    for seat in provenance_seats:
        key = (seat.seat_key, seat.vendor_leg, seat.epoch)
        durable = index.get(key)
        if durable is None:
            raise SeatAuthenticityInvalid(
                f"provenance seat seat_key={seat.seat_key!r} vendor_leg={seat.vendor_leg!r} "
                f"epoch={seat.epoch!r} has NO matching durable SeatOutcomeRecord (fail-closed, T13: "
                "a hand-written provenance seat cannot vouch for a seat that never ran)"
            )
        if seat.required != durable.required:
            raise SeatAuthenticityInvalid(
                f"seat {seat.seat_key!r} required={seat.required!r} disagrees with durable "
                f"record required={durable.required!r} (fail-closed, T13)"
            )
        if seat.status != durable.status:
            raise SeatAuthenticityInvalid(
                f"seat {seat.seat_key!r} status={seat.status!r} disagrees with durable "
                f"record status={durable.status!r} (fail-closed, T13)"
            )
        if seat.artifact_digest != durable.artifact_digest:
            raise SeatAuthenticityInvalid(
                f"seat {seat.seat_key!r} artifact_digest disagrees with durable record "
                "(fail-closed, T13)"
            )
        if seat.evidence_digest != durable.evidence_digest:
            raise SeatAuthenticityInvalid(
                f"seat {seat.seat_key!r} evidence_digest disagrees with durable record "
                "(fail-closed, T13)"
            )
        if seat.required and not _is_usable_terminal_status(durable.status):
            raise SeatAuthenticityInvalid(
                f"required seat {seat.seat_key!r}'s durable status {durable.status!r} is not a "
                f"usable terminal (fail-closed, T13; usable = {sorted(USABLE_TERMINAL_SEAT_STATUSES)!r})"
            )


# --------------------------------------------------------------------------- #
# §6.4 — immutable review-material re-verify (T14)
# --------------------------------------------------------------------------- #


def _reverify_round_material(
    repo: Path, run_id: str, review_scope: ReviewScope, material_digests: Sequence[MaterialDigest]
) -> None:
    if review_scope.reviewed_material_digest is None:
        if material_digests:
            raise ProvenanceInvalid(
                "round records material_digests but no reviewed_material_digest claim (fail-closed, "
                "ambiguous — design §6.4 requires the aggregate binding whenever material was recorded)"
            )
        return  # no material recorded for this round; nothing to re-verify.
    reverify_material(
        repo, run_id, material_digests, expected_reviewed_material_digest=review_scope.reviewed_material_digest
    )


def reverify_all_material(repo: Path, run_id: str, artifact: ReviewProvenanceArtifact) -> None:
    """design §6.4/T14: re-verify the candidate round's material AND every
    delta round's OWN material against its OWN `review_scope.
    reviewed_material_digest`, reusing Lane A's `reverify_material` (never
    reimplemented). A post-review edit of ANY round's underlying material is
    thereby detected. Raises `ProvenanceInvalid` (fail-closed) on the first
    mismatch across the whole artifact."""
    _reverify_round_material(repo, run_id, artifact.candidate.review_scope, artifact.material_digests)
    for record in artifact.delta_chain:
        _reverify_round_material(repo, run_id, record.review_scope, record.material_digests)


# --------------------------------------------------------------------------- #
# §4/§5 — resolve the chain's governing EquivalenceBinding (reuses Lane B/C)
# --------------------------------------------------------------------------- #

_RESOLVED_DELTA_STATUSES = frozenset({DELTA_STATUS_REVIEWED_CLEAN, DELTA_STATUS_ESCALATED_WHOLE_PATCH})


@dataclass(frozen=True, kw_only=True)
class ChainResolution:
    """The result of resolving a `ReviewProvenanceArtifact`'s delta chain into
    a governing `EquivalenceBinding` plus the final round's escalation/
    carry-forward/re-review bookkeeping (design §8's `escalation`/
    `carried_forward_findings`/`re_reviewed_findings` gate-status fields)."""

    binding: EquivalenceBinding
    escalation: Escalation
    carried_forward_findings: tuple[str, ...]
    re_reviewed_findings: tuple[str, ...]


def resolve_chain_resolution(artifact: ReviewProvenanceArtifact) -> ChainResolution:
    """design §4/§5/§6.5 (acceptance criterion 6): resolve the artifact's
    GOVERNING `EquivalenceBinding` — the exact-head degenerate case
    (`delta_chain` empty) via Lane B's `EquivalenceBinding.
    from_provenance_artifact` (never re-implemented), or the last delta
    round's `resulting_head_digest` as `expected_head_digest` when a chain
    exists. Fail-closed (`GateChainUnresolved`, a `ProvenanceInvalid`
    subclass) when the final round is `pending`/`invalidated`, has no
    `resulting_head_digest`, fails a defense-in-depth T5 review-scope
    re-check (reuses `fab_delta.enforce_review_scope_for_escalation`, never
    re-implemented — this does NOT assume the record was actually built via
    `build_delta_round`, since a loaded-from-JSON artifact was not
    necessarily constructed through that path), or the artifact's
    `base.ref_identity` is malformed."""
    if not artifact.delta_chain:
        binding = EquivalenceBinding.from_provenance_artifact(artifact)
        return ChainResolution(
            binding=binding,
            escalation=Escalation(required=False, trigger=None),
            carried_forward_findings=(),
            re_reviewed_findings=(),
        )

    last = artifact.delta_chain[-1]
    if last.status not in _RESOLVED_DELTA_STATUSES:
        raise GateChainUnresolved(
            f"delta chain's final round has status={last.status!r} (fail-closed): not a resolved, "
            f"pass-eligible state (must be one of {sorted(_RESOLVED_DELTA_STATUSES)!r})"
        )
    if last.resulting_head_digest is None:
        raise GateChainUnresolved("delta chain's final round has no resulting_head_digest (fail-closed)")

    # Defense-in-depth: re-run T5 against the record AS LOADED, never merely
    # trusting that whatever produced it already enforced this.
    enforce_review_scope_for_escalation(
        escalation=last.escalation,
        review_scope=last.review_scope,
        covering_patch_digest=last.resulting_head_digest,
    )

    ref_identity = artifact.base.ref_identity
    repo_slug, sep, ref_name = ref_identity.partition("#")
    if not sep or not repo_slug or not ref_name:
        raise GateChainUnresolved(
            f"malformed base ref_identity (expected '<repo_slug>#<ref_name>', fail-closed): {ref_identity!r}"
        )

    binding = EquivalenceBinding(
        repo_slug=repo_slug,
        base_ref_name=ref_name,
        base_sha=artifact.base.base_sha,
        expected_head_digest=last.resulting_head_digest,
        candidate_head_sha=artifact.candidate.head_sha,
        delta_head_shas=tuple(record.delta_head_sha for record in artifact.delta_chain),
    )
    re_reviewed = tuple(sorted(set(last.resolved_finding_ids) | set(last.reopened_finding_ids)))
    return ChainResolution(
        binding=binding,
        escalation=last.escalation,
        carried_forward_findings=tuple(sorted(last.carried_forward_finding_ids)),
        re_reviewed_findings=re_reviewed,
    )


def resolve_equivalence_binding(artifact: ReviewProvenanceArtifact) -> EquivalenceBinding:
    """Convenience wrapper for callers (e.g. `governed_premerge`'s promotion
    re-assertion) that only need the binding, not the full
    `ChainResolution`."""
    return resolve_chain_resolution(artifact).binding


# --------------------------------------------------------------------------- #
# §8 — gate-status composition
# --------------------------------------------------------------------------- #


def _gate_delta_entries(artifact: ReviewProvenanceArtifact) -> tuple[GateDeltaEntry, ...]:
    return tuple(
        GateDeltaEntry(delta_head_sha=record.delta_head_sha, delta_digest=record.resulting_head_digest, status=record.status)
        for record in artifact.delta_chain
    )


def _unresolved_required_seats(seats: Sequence[ProvenanceSeat]) -> tuple[str, ...]:
    """design §8: "every required seat has a non-DISAGREE verdict". A seat
    with `verdict is None` (never verdicted) or `verdict == "DISAGREE"` is
    unresolved. (Authenticity — is this verdict even genuine — is a SEPARATE
    check, `cross_check_seat_authenticity`, already run before this.)"""
    return tuple(
        sorted(seat.seat_key for seat in seats if seat.required and seat.verdict not in ("AGREE", "PARTIALLY AGREE"))
    )


def _unresolved_block_findings(
    findings: Sequence[Finding],
    final_round_resolved: Sequence[str],
    final_round_reopened: Sequence[str],
) -> tuple[str, ...]:
    """design ambiguity #2 (module docstring): a `severity=="block"` finding
    is unresolved iff EITHER its top-level `status != "clean"`, OR the final
    round's carry-forward reopened it (`reopened_finding_ids`) without ALSO
    resolving it in that SAME round (`resolved_finding_ids`) — the reopen
    audit trail always wins over a possibly-stale top-level `status`."""
    resolved = set(final_round_resolved)
    reopened = set(final_round_reopened)
    unresolved: set[str] = set()
    for finding in findings:
        if finding.severity != "block":
            continue
        if finding.id in reopened and finding.id not in resolved:
            unresolved.add(finding.id)
            continue
        if finding.status != "clean":
            unresolved.add(finding.id)
    return tuple(sorted(unresolved))


def _blocked_gate_status(
    *,
    reviewed_sha: str,
    prior_review_digest: str | None,
    chain_digest: str | None,
    deltas: tuple[GateDeltaEntry, ...],
    final_pr_head_sha: str | None,
    equivalence_verified: EquivalenceVerified | None,
    carried_forward_findings: tuple[str, ...] = (),
    re_reviewed_findings: tuple[str, ...] = (),
    escalation: Escalation | None = None,
    waiver: str | None,
) -> GateStatus:
    return GateStatus(
        reviewed_sha=reviewed_sha,
        prior_review_digest=prior_review_digest,
        chain_digest=chain_digest,
        deltas=deltas,
        final_pr_head_sha=final_pr_head_sha,
        equivalence_verified=equivalence_verified,
        carried_forward_findings=carried_forward_findings,
        re_reviewed_findings=re_reviewed_findings,
        escalation=escalation if escalation is not None else Escalation(required=False, trigger=None),
        waiver=waiver,
        status=GATE_STATUS_BLOCK,
    )


def compose_gate_status(
    *,
    repo: Path,
    run_id: str,
    live_base_ref_name: str,
    live_head_sha: str,
    origin: str = "origin",
    waiver: str | None = None,
    seat_outcomes: Sequence[SeatOutcomeRecord] | None = None,
    equivalent_fn: Callable[..., EquivalenceResult] = equivalent,
) -> GateStatus:
    """design §8: compose `fab.gate-status.v2` from the TRUSTED run-store's
    provenance (`fab_provenance.read_provenance`, keyed by `run_id` — never a
    client/PR-supplied blob) plus a LIVE-recomputed `EquivalenceResult`
    (Lane B `equivalent()`) plus the §6.3/§6.4 authenticity checks.

    `reviewed_sha` is ALWAYS `artifact.candidate.head_sha` — the REAL reviewed
    SHA — and is NEVER set to `live_head_sha`/`final_pr_head_sha` (design §8,
    T16). `equivalence_verified` is a SEPARATE, independently-recomputed proof
    (never read from any client-supplied field).

    NEVER raises for an in-scope fail-closed condition (mirrors
    `fab_canonical.equivalent()`'s posture) EXCEPT `ProvenanceNotFound`, which
    propagates deliberately: "no provenance recorded for this run_id" is a
    different fact than "a recorded provenance is broken", and only the
    caller (e.g. `fab_gate_validator`) can tell "not applicable, stay inert"
    from "should block" apart.

    `status == GATE_STATUS_PASS` iff ALL of: the delta chain resolves to a
    governing binding whose live-recomputed equivalence is `EQUIVALENT`;
    EVERY provenance seat is authenticated against its durable
    `SeatOutcomeRecord` (§6.3); every round's material re-verifies against its
    claimed `reviewed_material_digest` (§6.4); at least one seat is `required`
    (design ambiguity #3 — no vacuous pass on an empty seat set); every
    required seat has a non-DISAGREE verdict; and no `block`-severity finding
    remains unresolved. Otherwise `GATE_STATUS_BLOCK`, surfaced with
    `equivalence_verified.reason` when the failure is equivalence-shaped, or a
    reason embedded in the raised/caught exception text otherwise."""
    artifact = read_provenance(repo, run_id)  # ProvenanceNotFound propagates deliberately.
    reviewed_sha = artifact.candidate.head_sha

    try:
        verify_chain(artifact)
        resolution = resolve_chain_resolution(artifact)
        durable_seats = seat_outcomes if seat_outcomes is not None else read_seat_outcomes(repo, run_id)
        cross_check_seat_authenticity(artifact.seats, durable_seats)
        reverify_all_material(repo, run_id, artifact)
    except ProvenanceInvalid as exc:
        return _blocked_gate_status(
            reviewed_sha=reviewed_sha,
            prior_review_digest=artifact.candidate.patch_digest,
            chain_digest=artifact.chain_digest,
            deltas=_gate_delta_entries(artifact),
            final_pr_head_sha=live_head_sha,
            equivalence_verified=EquivalenceVerified(
                result="INVALIDATED",
                candidate_head_sha=artifact.candidate.head_sha,
                reason=f"provenance_invalid:{exc}",
            ),
            waiver=waiver,
        )

    equivalence = equivalent_fn(
        resolution.binding, repo, live_base_ref_name=live_base_ref_name, live_head_sha=live_head_sha, origin=origin
    )
    equivalence_verified = EquivalenceVerified(
        result=equivalence.result,
        candidate_head_sha=resolution.binding.candidate_head_sha,
        delta_head_shas=resolution.binding.delta_head_shas,
        expected_head_digest=equivalence.expected_head_digest,
        observed_head_digest=equivalence.observed_head_digest,
        base_sha=equivalence.live_base_sha,
        reason=equivalence.reason,
    )

    no_required_seats = not any(seat.required for seat in artifact.seats)
    unresolved_required = _unresolved_required_seats(artifact.seats)
    unresolved_block = _unresolved_block_findings(
        artifact.findings,
        final_round_resolved=(artifact.delta_chain[-1].resolved_finding_ids if artifact.delta_chain else ()),
        final_round_reopened=(artifact.delta_chain[-1].reopened_finding_ids if artifact.delta_chain else ()),
    )

    ok = (
        equivalence.result == EQUIVALENCE_EQUIVALENT
        and not no_required_seats
        and not unresolved_required
        and not unresolved_block
    )
    status = GATE_STATUS_PASS if ok else GATE_STATUS_BLOCK

    return GateStatus(
        reviewed_sha=reviewed_sha,
        prior_review_digest=artifact.candidate.patch_digest,
        chain_digest=artifact.chain_digest,
        deltas=_gate_delta_entries(artifact),
        final_pr_head_sha=live_head_sha,
        equivalence_verified=equivalence_verified,
        carried_forward_findings=resolution.carried_forward_findings,
        re_reviewed_findings=resolution.re_reviewed_findings,
        escalation=resolution.escalation,
        waiver=waiver,
        status=status,
    )


def verdict_binds_to_equivalent(finding: ReviewFinding, gate_status: GateStatus) -> bool:
    """design §8 finding 5: `verdict_binds_to(finding, gate_status.
    reviewed_sha)` (#88, REUSED from `closeout_validators`, never
    reimplemented) AND `gate_status.equivalence_verified.result ==
    EQUIVALENT`. Two INDEPENDENT facts ANDed — never one SHA masquerading as
    the other; `reviewed_sha` is always the reviewed SHA, equivalence is a
    separate, independently-recomputed claim (T16)."""
    if not verdict_binds_to(finding, gate_status.reviewed_sha):
        return False
    if gate_status.equivalence_verified is None:
        return False
    return gate_status.equivalence_verified.result == EQUIVALENCE_EQUIVALENT


# --------------------------------------------------------------------------- #
# Closeout-validator wiring (design §9 Lane D bullet 1) — the EXACT
# verification_evidence_validator/visual_avatar_evidence_validator pattern.
# --------------------------------------------------------------------------- #

FAB_GATE_FINDING_CODE = "fab_delta_review_gate_block"


@register_closeout_validator
def fab_gate_validator(ctx: CloseoutContext) -> list[ReviewFinding]:
    """The agent-review-gate wiring for FAB (design §9 Lane D). Inert (`[]`)
    when `ctx.fab_gate_inputs` is absent/malformed, `ctx.repo_root` is unset,
    or the run recorded no FAB provenance (`ProvenanceNotFound`) — this
    validator NEVER fabricates a finding about a repo/run that is not using
    FAB delta review. `fab_gate_inputs` is a plain, untyped mapping (not a
    typed import from this module) so `closeout_validators.py` never needs to
    import `fab_gate` — avoiding a `closeout_validators` <-> `fab_gate` import
    cycle (`fab_gate` already imports `CloseoutContext`/
    `register_closeout_validator`/`ReviewFinding`/`verdict_binds_to` FROM
    `closeout_validators`); required keys: `run_id` (MUST be resolved by the
    CALLER from TRUSTED harness/review-run output, never `ctx.terminal`/
    `ctx.automation`), `live_base_ref_name`, `live_head_sha`; optional:
    `origin` (default `"origin"`), `waiver` (audited echo, never silently
    changes the computed status — design §8).

    Follows `verification_evidence_validator`'s EXACT posture: `severity=
    "block"` (warn-default under the global `PHASE_LOOP_REVIEW` control,
    opt-in `block`), `blocker_class="review_gate_block"` (non-human —
    `apply_review_findings` never sets `human_required`)."""
    inputs = ctx.fab_gate_inputs
    if not inputs:
        return []
    try:
        run_id = str(inputs["run_id"])
        live_base_ref_name = str(inputs["live_base_ref_name"])
        live_head_sha = str(inputs["live_head_sha"])
        origin = str(inputs.get("origin") or "origin")
        waiver = inputs.get("waiver")
        waiver = str(waiver) if waiver is not None else None
    except (KeyError, TypeError):
        # Malformed wiring is a caller bug, not a signal about the PR itself —
        # inert rather than a false block (explicit here for a clear audit
        # trail; `run_closeout_validators` would otherwise silently swallow a
        # raised exception from this validator the same way).
        return []
    if not ctx.repo_root:
        return []
    repo = Path(ctx.repo_root)

    try:
        gate_status = compose_gate_status(
            repo=repo,
            run_id=run_id,
            live_base_ref_name=live_base_ref_name,
            live_head_sha=live_head_sha,
            origin=origin,
            waiver=waiver,
        )
    except ProvenanceNotFound:
        return []  # this run recorded no FAB provenance — not applicable.
    except Exception:
        # A review gate must never itself crash closeout, but an exception
        # from OUR OWN composition (not one of the fail-closed typed paths
        # `compose_gate_status` already handles) is itself suspicious enough
        # to fail closed rather than silently pass.
        return [
            ReviewFinding(
                code=FAB_GATE_FINDING_CODE,
                reason="FAB gate composition raised an unexpected error (fail-closed)",
                severity="block",
                blocker_class="review_gate_block",
            )
        ]

    if gate_status.status == GATE_STATUS_PASS:
        return []

    reason = gate_status.equivalence_verified.reason if gate_status.equivalence_verified else None
    return [
        ReviewFinding(
            code=FAB_GATE_FINDING_CODE,
            reason=f"FAB delta-review gate did not pass (status={gate_status.status}); reason={reason}",
            severity="block",
            blocker_class="review_gate_block",
            body=gate_status.to_json(),
            reviewed_sha=gate_status.reviewed_sha,
        )
    ]
