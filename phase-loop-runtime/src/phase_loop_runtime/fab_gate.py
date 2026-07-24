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
    recorded provenance is broken/tampered", and it is deliberately this
    low-level primitive's caller, not `compose_gate_status` itself, that
    decides what a missing provenance means for a GIVEN `run_id` (see
    `fab_gate_validator`'s F3 branch table — once a `run_id` is present, it
    resolves this to BLOCK, never "not applicable").
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
    the global `PHASE_LOOP_REVIEW` control, opt-in `block`. Fail-closed BY
    CONSTRUCTION (agent-harness#191 CR, finding 3): inert (`[]`) ONLY when
    `CloseoutContext.fab_gate_inputs` is absent or carries no `run_id` (the
    TRUSTED FAB-scope marker, resolved by the CALLER, never from `ctx.
    terminal`/`ctx.automation`) — once a `run_id` is present, every other
    failure (missing `repo_root`/live-PR inputs, or `ProvenanceNotFound` for
    that claimed `run_id`) is a `block`, never a silent `[]`; see
    `fab_gate_validator`'s own docstring for the exact branch table.
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
from .fab_delta import (
    DeltaBindingInvalid,
    ResolvedClaimUnverified,
    enforce_review_scope_for_escalation,
    require_seat_corroboration,
    validate_delta_binds_to_parent,
)
from .fab_provenance import (
    DELTA_STATUS_ESCALATED_WHOLE_PATCH,
    DELTA_STATUS_REVIEWED_CLEAN,
    EQUIVALENCE_EQUIVALENT,
    GATE_STATUS_BLOCK,
    GATE_STATUS_PASS,
    DeltaReviewRecord,
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
    _CONTENT_REF_RE,  # reused content-ref shape check, not reimplemented
    _encode_and_digest,  # reused canonical digest, not reimplemented
    _load_json_fail_closed,  # reused fail-closed JSON load, not reimplemented
    _reject_unknown_keys,  # reused strict unknown-key rejection
    _req,
    _req_bool,
    _req_int,
    _req_str,
    _strict_object_pairs_hook,  # reused strict-parse discipline, not reimplemented
    _tuple_str,
    aggregate_material_digest,  # noqa: F401 - re-exported for callers/tests
    atomic_write_text_durable,
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


class DeltaRoundSeatBindingInvalid(ProvenanceInvalid):
    """agent-harness#191 CR, Lane D finding 1: a resolved-status
    (`reviewed-clean`/`escalated-whole-patch`) delta round has NO
    `delta_round_seats` of its own, its seats fail the §6.3 durable
    authenticity cross-check, or its `resolved_finding_ids`/
    `reopened_finding_ids` are not corroborated by ITS OWN round's seats
    (§5.3, `fab_delta.require_seat_corroboration`, reused). Lane C's
    `build_delta_round` deliberately delegated ENFORCING this to Lane D for
    any record it did not itself construct (e.g. one loaded from JSON) — this
    is where that delegation is honored: the artifact-wide `seats` list and
    its single "at least one required seat" check (`cross_check_seat_
    authenticity` / `no_required_seats` below) are NOT sufficient on their
    own, because neither binds a SPECIFIC delta round to the seats that
    actually reviewed THAT round's claims."""


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
        raw_finding_ids = d.get("finding_ids", ())
        if not isinstance(raw_finding_ids, (list, tuple)) or not all(
            isinstance(x, str) for x in raw_finding_ids
        ):
            raise ValueError("finding_ids must be a list of strings")
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
            verdict=(str(d["verdict"]) if d.get("verdict") is not None else None),
            finding_ids=tuple(raw_finding_ids),
            seat_instance_id=(
                str(d["seat_instance_id"]) if d.get("seat_instance_id") is not None else None
            ),
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


# --------------------------------------------------------------------------- #
# Piece 2 (design v5 #1 / v6 #1,#2,#3) — the harness-only-written durable ROUND
# record: the expected-seat manifest (completeness denominator, frozen BEFORE
# invocation), the harness-authenticated canonical finding records (severity +
# status + body_ref digest), and the harness-issued ROUND IDENTITY (bound to
# the reviewed HEAD + reviewed material). This is the trust root the gate
# verifies a client-supplied `ReviewProvenanceArtifact` AGAINST, field by field.
# It lives as ONE JSON file siblings to `fab-provenance.json`, written
# harness-only via the same `provenance_dir_for_run` run-store root.
# --------------------------------------------------------------------------- #

REVIEW_ROUND_FILENAME = "fab-review-round.json"
SCHEMA_REVIEW_ROUND = "fab.review-round.v1"

# FAB piece 3b (agent-harness#191) G1 — PER-EPOCH round records. Each review
# round (the candidate round + every delta round) has its OWN durable round
# record (expected-seat manifest + round identity + canonical findings) at a
# per-epoch path, so a later delta round never clobbers an earlier round's
# anchors (piece 2 wrote/OVERWROTE a single `fab-review-round.json`). The
# candidate round is ALWAYS epoch 1 — a FIXED value, never derived from the
# client-supplied `artifact.seats[*].epoch` (an attacker must not choose which
# file is "the candidate's"); delta rounds use their `DeltaReviewRecord.epoch`.
FAB_CANDIDATE_EPOCH = 1
_MAX_REVIEW_ROUND_BYTES = 4 * 1024 * 1024


class ReviewRoundInvalid(ProvenanceInvalid):
    """The harness-written durable review-round record for a FAB-scoped run is
    absent, oversized, malformed, fails its self-digest, is not finalized, or
    its bound identity (epoch / reviewed HEAD / reviewed material) does not
    match the client-supplied artifact — any of which fails the gate closed.
    A subclass of `ProvenanceInvalid` (never `ProvenanceNotFound`) so a missing
    round record for a run the caller scoped to FAB resolves to BLOCK inside
    `compose_gate_status`, never a silent pass."""


@dataclass(frozen=True, kw_only=True)
class ExpectedSeat:
    """One entry in the epoch-scoped EXPECTED-seat manifest (design v5 #1 / v6
    #1): the concrete, resolved invocation the harness DISPATCHED, keyed by a
    UNIQUE `seat_instance_id`. This — not the produced provenance-seat set — is
    the completeness denominator: a required expected seat that timed out /
    degraded / never recorded is still demanded by the gate."""

    seat_instance_id: str
    seat_key: str
    vendor_leg: str
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat_instance_id": self.seat_instance_id,
            "seat_key": self.seat_key,
            "vendor_leg": self.vendor_leg,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ExpectedSeat":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            seat_instance_id=_req_str(d, "seat_instance_id"),
            seat_key=_req_str(d, "seat_key"),
            vendor_leg=_req_str(d, "vendor_leg"),
            required=_req_bool(d, "required"),
        )


@dataclass(frozen=True, kw_only=True)
class CanonicalFinding:
    """A HARNESS-AUTHENTICATED canonical finding record (design v6 #2): the gate
    reads a client-supplied `Finding`'s top-level `severity`/`status` (via
    `_unresolved_block_findings`) and its `body_ref`; binding the id set alone
    is insufficient (an attacker keeps the id but rewrites the record
    non-blocking or omits it). Each canonical finding pins `severity` + `status`
    + `body_digest` (the `body_ref` content-ref) at review time; the gate
    requires an EXACT id→content match."""

    finding_id: str
    severity: str
    status: str
    body_digest: str

    def __post_init__(self) -> None:
        if not _CONTENT_REF_RE.match(self.body_digest):
            raise ProvenanceInvalid(
                f"CanonicalFinding.body_digest must be a 'sha256:<64 hex>' content-ref, got {self.body_digest!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "status": self.status,
            "body_digest": self.body_digest,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CanonicalFinding":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            finding_id=_req_str(d, "finding_id"),
            severity=_req_str(d, "severity"),
            status=_req_str(d, "status"),
            body_digest=_req_str(d, "body_digest"),
        )


@dataclass(frozen=True, kw_only=True)
class FabReviewRound:
    """The harness-only-written durable review-round record (design v5 #1 / v6
    #1,#2,#3). Written in TWO phases, both harness-only:

      1. BEFORE panel invocation (`write_expected_seats`) — the epoch + the
         frozen EXPECTED-seat manifest. `reviewed_head_sha`/`reviewed_material_
         digest` are unknown yet (the committed head does not exist at review
         time) and `finalized` is `False`.
      2. AFTER the closeout commit (`finalize_review_round`) — binds the
         harness-issued round identity to the reviewed HEAD + reviewed material
         and pins the canonical finding records; sets `finalized=True`. The
         expected-seat manifest is NEVER mutated by finalize (a required seat
         demanded pre-invocation can never be dropped post-hoc).

    `round_digest` self-excludes itself (mirrors `artifact_digest`): any edit to
    any other field after write is detected fail-closed on read."""

    schema: str = SCHEMA_REVIEW_ROUND
    epoch: int
    expected_seats: tuple[ExpectedSeat, ...] = ()
    reviewed_head_sha: str | None = None
    reviewed_material_digest: str | None = None
    canonical_findings: tuple[CanonicalFinding, ...] = ()
    # 3b-gate CR round 1: the harness-determined ROUND-RESOLUTION digest for a
    # DELTA round — a digest over every field the gate reads off the client
    # `DeltaReviewRecord` to make a decision (status / resulting_head_digest /
    # escalation / finding-flow / delta_changed_paths / chain-topology), computed
    # by the harness over its OWN honest record (`_delta_resolution_digest`). The
    # gate recomputes it over the client record and requires equality, so those
    # round-resolution fields are bound to the durable record exactly as the seats
    # already are. `None` on the candidate round (no delta resolution).
    resolution_digest: str | None = None
    finalized: bool = False
    round_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_REVIEW_ROUND:
            raise ProvenanceInvalid(f"review-round schema must be {SCHEMA_REVIEW_ROUND!r}, got {self.schema!r}")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "epoch": self.epoch,
            "expected_seats": [s.to_dict() for s in self.expected_seats],
            "reviewed_head_sha": self.reviewed_head_sha,
            "reviewed_material_digest": self.reviewed_material_digest,
            "canonical_findings": [c.to_dict() for c in self.canonical_findings],
            "resolution_digest": self.resolution_digest,
            "finalized": self.finalized,
            "round_digest": self.round_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._payload()

    def with_digest(self) -> "FabReviewRound":
        digest = _encode_and_digest(self._payload(), exclude="round_digest")
        return dataclasses.replace(self, round_digest=digest)

    def to_json(self) -> str:
        return json.dumps(self.with_digest().to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FabReviewRound":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        schema = _req_str(d, "schema")
        if schema != SCHEMA_REVIEW_ROUND:
            raise ProvenanceInvalid(f"review-round schema must be {SCHEMA_REVIEW_ROUND!r}, got {schema!r}")
        finalized = _req(d, "finalized")
        if not isinstance(finalized, bool):
            raise ProvenanceInvalid("review-round finalized must be a boolean")
        instance = cls(
            schema=schema,
            epoch=_req_int(d, "epoch"),
            expected_seats=tuple(ExpectedSeat.from_dict(s) for s in d.get("expected_seats", [])),
            reviewed_head_sha=(_req_str(d, "reviewed_head_sha") if d.get("reviewed_head_sha") is not None else None),
            reviewed_material_digest=(
                _req_str(d, "reviewed_material_digest") if d.get("reviewed_material_digest") is not None else None
            ),
            canonical_findings=tuple(CanonicalFinding.from_dict(c) for c in d.get("canonical_findings", [])),
            resolution_digest=(_req_str(d, "resolution_digest") if d.get("resolution_digest") is not None else None),
            finalized=finalized,
            round_digest=_req_str(d, "round_digest"),
        )
        recomputed = _encode_and_digest(instance._payload(), exclude="round_digest")
        if recomputed != instance.round_digest:
            raise ReviewRoundInvalid(
                "review-round record digest mismatch (fail-closed): edited after write "
                f"(recomputed={recomputed!r}, recorded={instance.round_digest!r})"
            )
        return instance


def review_round_path_for_run(repo: Path, run_id: str, epoch: int = FAB_CANDIDATE_EPOCH) -> Path:
    """The PER-EPOCH round-record path (piece 3b G1). One file per epoch
    (`fab-review-round.e{epoch}.json`), so a later delta round never overwrites an
    earlier round's manifest / canonical findings / round identity."""
    base, _, ext = REVIEW_ROUND_FILENAME.rpartition(".")
    return provenance_dir_for_run(repo, run_id) / f"{base}.e{int(epoch)}.{ext}"


def write_expected_seats(repo: Path, run_id: str, *, epoch: int, expected_seats: Sequence[ExpectedSeat]) -> Path:
    """Phase 1 (harness-only, BEFORE panel invocation): freeze the epoch-scoped
    EXPECTED-seat manifest for THIS epoch's round record. Overwrites only THIS
    epoch's file — a later delta round writes a different epoch's file and never
    clobbers it (piece 3b G1). `reviewed_head_sha`/`reviewed_material_digest`
    are bound later by `finalize_review_round`."""
    seat_ids = [s.seat_instance_id for s in expected_seats]
    if len(set(seat_ids)) != len(seat_ids):
        raise ProvenanceInvalid(
            f"expected-seat manifest has duplicate seat_instance_id(s) (fail-closed): {sorted(seat_ids)!r}"
        )
    record = FabReviewRound(epoch=epoch, expected_seats=tuple(expected_seats), finalized=False).with_digest()
    return _write_review_round(repo, run_id, record)


def _delta_resolution_digest(record: "DeltaReviewRecord") -> str:
    """3b-gate CR round 1 — the canonical digest over the harness-determined
    ROUND-RESOLUTION fields of a DELTA round: EVERY field the gate reads off the
    client `DeltaReviewRecord` to make a decision (`verify_chain` /
    `resolve_chain_resolution` / the finding-flow logic), EXCEPT `delta_head_sha`
    and `review_scope.reviewed_material_digest` (already bound by round identity).
    The bound set is EXACTLY the dict below — adding a gate-read field to
    `DeltaReviewRecord` must be a VISIBLE change here, never a silent hole.

    The harness persists this over its OWN honest record (`finalize_review_round`
    ← the consumer's `build_delta_round` output, computed off live git); the gate
    recomputes it over the CLIENT record and requires equality — so a delta record
    whose status / resulting_head_digest / escalation / finding-flow /
    changed-paths / chain-topology / scope / policy diverges from what the harness
    recorded BLOCKS. Reuses `_encode_and_digest` (one canonicalization path).

    The bound set below is EXHAUSTIVE over the gate-read fields. `delta_head_sha`
    and `review_scope.reviewed_material_digest` are bound by round identity
    (`_cross_check_one_round`) + material re-verify. `delta_round_seats` are bound
    by seat authenticity. `material_digests` are bound by the aggregate
    reviewed_material_digest. Everything else `verify_chain` /
    `resolve_chain_resolution` / the finding-flow logic reads is here."""
    return _encode_and_digest(
        {
            "status": record.status,
            "resulting_head_digest": record.resulting_head_digest,
            "delta_changed_paths": sorted(record.delta_changed_paths),
            "escalation": record.escalation.to_dict(),
            "resolved_finding_ids": sorted(record.resolved_finding_ids),
            "reopened_finding_ids": sorted(record.reopened_finding_ids),
            "carried_forward_finding_ids": sorted(record.carried_forward_finding_ids),
            "chain_digest": record.chain_digest,
            "parent_chain_digest": record.parent_chain_digest,
            "parent_digest": record.parent_digest,
            "review_scope": record.review_scope.to_dict(),
            "policy": record.policy,
        }
    )


def finalize_review_round(
    repo: Path,
    run_id: str,
    *,
    epoch: int = FAB_CANDIDATE_EPOCH,
    reviewed_head_sha: str,
    reviewed_material_digest: str | None,
    canonical_findings: Sequence[CanonicalFinding],
    delta_record: "DeltaReviewRecord | None" = None,
) -> Path:
    """Phase 2 (harness-only, AFTER the closeout commit): bind the harness-issued
    round identity to the reviewed HEAD + reviewed material and pin the canonical
    finding records for THIS epoch's round record. The frozen `expected_seats`
    manifest is preserved verbatim — never re-derived from the produced set — so a
    required seat demanded before invocation can never be dropped here (design
    v5 #1).

    For a DELTA round, pass the harness's honest `delta_record` (the
    `build_delta_round` output, computed off live git) — its ROUND-RESOLUTION
    digest (`_delta_resolution_digest`) is bound into the durable record so the
    gate can cross-check the client artifact's delta record against it (3b-gate CR
    round 1). `None` for the candidate round (no delta resolution)."""
    existing = read_review_round(repo, run_id, epoch)
    finding_ids = [c.finding_id for c in canonical_findings]
    if len(set(finding_ids)) != len(finding_ids):
        raise ProvenanceInvalid(
            f"canonical findings have duplicate finding_id(s) (fail-closed): {sorted(finding_ids)!r}"
        )
    record = dataclasses.replace(
        existing,
        reviewed_head_sha=reviewed_head_sha,
        reviewed_material_digest=reviewed_material_digest,
        canonical_findings=tuple(canonical_findings),
        resolution_digest=(_delta_resolution_digest(delta_record) if delta_record is not None else None),
        finalized=True,
    ).with_digest()
    return _write_review_round(repo, run_id, record)


def _write_review_round(repo: Path, run_id: str, record: FabReviewRound) -> Path:
    # The file is keyed by the record's OWN epoch (G1) — so writing one epoch's
    # record can never overwrite another's.
    path = review_round_path_for_run(repo, run_id, record.epoch)
    # Durable (fsync'd) write — the round record must be on stable storage BEFORE
    # the branch ref advances (CR round 7 / codex#4).
    atomic_write_text_durable(path, record.to_json())
    return path


def read_review_round(repo: Path, run_id: str, epoch: int = FAB_CANDIDATE_EPOCH) -> FabReviewRound:
    """The gate's ONLY read path for a durable round record — from the trusted
    run store, keyed by `run_id` + `epoch`, never a client blob. Missing /
    oversized / malformed / digest-mismatch all raise `ReviewRoundInvalid` (a
    `ProvenanceInvalid`, NOT `ProvenanceNotFound`), so a FAB-scoped run whose
    round record is absent BLOCKS inside `compose_gate_status` rather than
    passing vacuously. A record whose stored `epoch` does not match the requested
    epoch also BLOCKS (a delta round must never be served the candidate's file)."""
    path = review_round_path_for_run(repo, run_id, epoch)
    try:
        if path.stat().st_size > _MAX_REVIEW_ROUND_BYTES:
            raise ReviewRoundInvalid(
                f"review-round record for run_id={run_id!r} exceeds max size {_MAX_REVIEW_ROUND_BYTES} bytes"
            )
    except OSError:
        pass
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReviewRoundInvalid(
            f"no durable review-round record for run_id={run_id!r} (fail-closed): {exc}"
        ) from exc
    data = _load_json_fail_closed(text, max_bytes=_MAX_REVIEW_ROUND_BYTES)
    record = FabReviewRound.from_dict(data)
    # Defense-in-depth (G1): the file name encodes the epoch, but re-assert the
    # stored epoch matches what was requested — a mismatched record (e.g. a
    # candidate file symlinked/renamed under a delta epoch) BLOCKS.
    if record.epoch != epoch:
        raise ReviewRoundInvalid(
            f"review-round record for run_id={run_id!r} has epoch={record.epoch!r} but epoch={epoch!r} "
            "was requested (fail-closed, G1: a round must never be served another epoch's record)"
        )
    return record


def cross_check_round_authenticity(
    repo: Path,
    run_id: str,
    artifact: ReviewProvenanceArtifact,
    durable_seat_outcomes: Sequence[SeatOutcomeRecord],
    *,
    round_reader: Callable[[int], FabReviewRound] | None = None,
) -> None:
    """The forge-resistance core, generalized to EVERY round (piece 3b G2 — was
    piece-2 candidate-only). Applies the SAME candidate-grade authentication
    (`_cross_check_one_round`) to the CANDIDATE round AND each DELTA round, each
    keyed by ITS OWN epoch's durable round record read from the trusted run store.
    Closes the two holes `_require_delta_round_seat_binding` alone left: a delta
    seat's `verdict` was never bound to its durable verdict (verdict-flip), and
    its `finding_ids` were never set-bound to the durable ledger nor content-bound
    to a per-epoch canonical record (dropped-finding).

    `round_reader` is a TEST SEAM (production passes `None` → reads each round
    record from disk by epoch). Fail-closed (`SeatAuthenticityInvalid`/
    `ReviewRoundInvalid`) on the FIRST absence / mismatch / vacuity.

    Two cross-round invariants enforced here:
      * EPOCHS DISTINCT (v5 #3): the candidate epoch (FIXED = 1, never derived
        from the client-supplied `artifact.seats[*].epoch`) plus every delta
        round's `DeltaReviewRecord.epoch` must be pairwise distinct — a collision
        would let two rounds share one durable record and evade per-round
        completeness. Fail-closed on any collision.
      * Each round's record is fetched by its epoch and validated against THAT
        round's OWN head (candidate → `candidate.head_sha`; delta →
        `delta_head_sha`), so the client-supplied epoch is only an index: a wrong
        epoch fetches a record whose head won't match → BLOCK, never a bypass."""
    reader = round_reader if round_reader is not None else (lambda epoch: read_review_round(repo, run_id, epoch))

    epochs = [FAB_CANDIDATE_EPOCH, *(d.epoch for d in artifact.delta_chain)]
    if len(set(epochs)) != len(epochs):
        raise ReviewRoundInvalid(
            f"review rounds do not have pairwise-distinct epochs (fail-closed, v5 #3): {epochs!r} — a "
            "shared epoch would let two rounds collide on one durable record and evade completeness"
        )

    _cross_check_one_round(
        reader(FAB_CANDIDATE_EPOCH),
        expected_epoch=FAB_CANDIDATE_EPOCH,
        reviewed_head_sha=artifact.candidate.head_sha,
        reviewed_material_digest=artifact.candidate.review_scope.reviewed_material_digest,
        round_seats=artifact.seats,
        artifact_findings=artifact.findings,
        durable_seat_outcomes=durable_seat_outcomes,
        delta_record=None,  # candidate round — no delta resolution
    )
    for record in artifact.delta_chain:
        _cross_check_one_round(
            reader(record.epoch),
            expected_epoch=record.epoch,
            reviewed_head_sha=record.delta_head_sha,
            reviewed_material_digest=record.review_scope.reviewed_material_digest,
            round_seats=record.delta_round_seats,
            artifact_findings=artifact.findings,
            durable_seat_outcomes=durable_seat_outcomes,
            delta_record=record,  # bind this round's resolution fields to durable
        )


def _cross_check_one_round(
    review_round: FabReviewRound,
    *,
    expected_epoch: int,
    reviewed_head_sha: str,
    reviewed_material_digest: str | None,
    round_seats: Sequence[ProvenanceSeat],
    artifact_findings: Sequence[Finding],
    durable_seat_outcomes: Sequence[SeatOutcomeRecord],
    delta_record: "DeltaReviewRecord | None" = None,
) -> None:
    """Candidate-grade authentication of ONE round (piece-2 core, parameterized
    for piece 3b G2). Drive EVERYTHING from the DURABLE side — the harness-written
    `review_round` (expected-seat manifest + round identity + canonical findings)
    and the durable `SeatOutcomeRecord` ledger — and verify THIS round's
    client-supplied `round_seats` (the candidate round's `artifact.seats`, or a
    delta round's `delta_round_seats`) AGAINST it, fail-closed on the FIRST
    absence / mismatch / vacuity.

    Steps:
      * EPOCH: the round record's stored epoch must equal `expected_epoch`
        (defense-in-depth over the path-encoded epoch — a delta round must never
        be served the candidate's record).
      * ROUND IDENTITY (v6 #3): the round must be `finalized`; its bound
        `reviewed_head_sha` must equal THIS round's head (`reviewed_head_sha`
        arg — candidate head, or the delta round's `delta_head_sha`) and its
        `reviewed_material_digest` must equal THIS round's claimed material.
      * NO VACUOUS PASS (v6 #3 / ambiguity #3): the expected-seat manifest must be
        non-empty and contain at least one required seat instance.
      * COMPLETENESS anchored on the EXPECTED set (v5 #1), keyed on
        `seat_instance_id` (v6 #1): every expected seat has (a) a durable outcome
        at that instance id whose `required` matches and — if required — whose
        `status` is a usable terminal, AND (b) a provenance seat at that id.
      * VERDICT BINDING (v4 #2): each expected seat's provenance verdict must
        equal its durable verdict (strict).
      * FINDING BINDING (v5 #2): each expected seat's provenance `finding_ids`
        must equal its durable `finding_ids` (as sets).
      * FINDING-CONTENT BINDING (v6 #2): every finding id any expected durable
        seat logged must appear in `artifact_findings` with `severity`/`status`/
        `body_ref` EXACTLY matching THIS round's canonical finding record.
      * Plus the metadata-level authenticity of EVERY seat in `round_seats`
        (`cross_check_seat_authenticity`, reused)."""
    if review_round.epoch != expected_epoch:
        raise ReviewRoundInvalid(
            f"round record epoch={review_round.epoch!r} does not match expected epoch={expected_epoch!r} "
            "(fail-closed, G2: a round must be authenticated against its OWN epoch's record)"
        )
    if not review_round.finalized:
        raise ReviewRoundInvalid(
            "durable review-round record is not finalized (fail-closed): a crash between provenance "
            "write and round finalization must never let an un-finalized round pass"
        )
    if review_round.reviewed_head_sha != reviewed_head_sha:
        raise ReviewRoundInvalid(
            f"round identity bound to reviewed_head_sha={review_round.reviewed_head_sha!r} does not match "
            f"this round's head {reviewed_head_sha!r} (fail-closed, v6 #3: replay / wrong head)"
        )
    if review_round.reviewed_material_digest != reviewed_material_digest:
        raise ReviewRoundInvalid(
            "round identity's reviewed_material_digest does not match this round's claimed "
            "review_scope.reviewed_material_digest (fail-closed, v6 #3)"
        )

    # ROUND-RESOLUTION BINDING (3b-gate CR round 1): for a DELTA round, bind every
    # gate-read round-resolution field (status / resulting_head_digest /
    # escalation / finding-flow / delta_changed_paths / chain-topology) to the
    # durable record via `_delta_resolution_digest`. The candidate round has NO
    # delta resolution — its durable `resolution_digest` MUST be absent, and a
    # delta round's MUST be present (a swapped-role record BLOCKS).
    if delta_record is None:
        if review_round.resolution_digest is not None:
            raise ReviewRoundInvalid(
                "candidate round record carries a delta resolution_digest (fail-closed): the candidate "
                "round has no delta resolution"
            )
    else:
        if review_round.resolution_digest is None:
            raise ReviewRoundInvalid(
                "delta round record has NO resolution_digest (fail-closed, 3b-gate CR round 1: the "
                "harness-determined round-resolution fields were never durably bound)"
            )
        recomputed_resolution = _delta_resolution_digest(delta_record)
        if recomputed_resolution != review_round.resolution_digest:
            raise ReviewRoundInvalid(
                "delta round's client resolution fields (status / resulting_head_digest / escalation / "
                "finding-flow / delta_changed_paths / chain-topology) do not match the harness-recorded "
                f"resolution_digest (fail-closed, 3b-gate CR round 1): recomputed={recomputed_resolution!r}, "
                f"durable={review_round.resolution_digest!r}"
            )

    expected = review_round.expected_seats
    if not expected:
        raise SeatAuthenticityInvalid(
            "durable expected-seat manifest is empty (fail-closed, no vacuous pass: a round that ran "
            "must have at least one expected seat)"
        )
    if not any(s.required for s in expected):
        raise SeatAuthenticityInvalid(
            "durable expected-seat manifest has NO required seat (fail-closed, no vacuous pass — design "
            "ambiguity #3, anchored on the EXPECTED set)"
        )

    # First run the metadata-level authenticity over EVERY seat in THIS round — a
    # fabricated seat with no durable record BLOCKS here before completeness.
    cross_check_seat_authenticity(round_seats, durable_seat_outcomes)

    durable_by_instance: dict[str, SeatOutcomeRecord] = {}
    for record in durable_seat_outcomes:
        if record.seat_instance_id is None:
            continue
        prior = durable_by_instance.get(record.seat_instance_id)
        if prior is not None and prior != record:
            raise SeatAuthenticityInvalid(
                f"durable ledger has conflicting records for seat_instance_id={record.seat_instance_id!r} "
                "(fail-closed, never resolved by pick-one)"
            )
        durable_by_instance[record.seat_instance_id] = record

    prov_by_instance: dict[str, ProvenanceSeat] = {}
    for seat in round_seats:
        if seat.seat_instance_id is None:
            # P0-2 (3b-gate CR round 1 / codex): a seat with no instance id cannot
            # be bound to the expected manifest — reject it rather than skip it, so
            # no unbound seat can ride alongside the expected set.
            raise SeatAuthenticityInvalid(
                "round provenance seat has no seat_instance_id (fail-closed): every seat must be bound "
                "to the expected manifest"
            )
        if seat.seat_instance_id in prov_by_instance:
            raise SeatAuthenticityInvalid(
                f"round has duplicate provenance seats for seat_instance_id={seat.seat_instance_id!r} "
                "(fail-closed)"
            )
        prov_by_instance[seat.seat_instance_id] = seat

    # P0-2 (3b-gate CR round 1 / codex): the provenance seat-instance set must
    # EQUAL the expected manifest — NOT merely be a superset. `_cross_check_one_
    # round` binds verdict / finding_ids only while iterating the EXPECTED
    # manifest, so an EXTRA provenance seat (outside the expected set) that reuses
    # genuine durable metadata but flips DISAGREE→AGREE / drops findings would go
    # unbound and be read as an authenticated verdict by later gate logic. Reject
    # any seat not in the expected set (the producer emits exactly the manifest
    # set — capture freezes `expected` from `panel.legs`, artifact seats mirror it).
    expected_ids = {e.seat_instance_id for e in expected}
    extra_ids = set(prov_by_instance) - expected_ids
    if extra_ids:
        raise SeatAuthenticityInvalid(
            f"round has provenance seats OUTSIDE the expected manifest (fail-closed, P0-2): {sorted(extra_ids)!r} "
            "— an extra seat's verdict/finding_ids would be unbound; the seat set must equal the expected set"
        )

    canonical_by_id = {c.finding_id: c for c in review_round.canonical_findings}
    findings_by_id: dict[str, Finding] = {}
    for f in artifact_findings:
        if f.id in findings_by_id:
            raise SeatAuthenticityInvalid(f"artifact has duplicate Finding id {f.id!r} (fail-closed)")
        findings_by_id[f.id] = f

    required_finding_ids: set[str] = set()
    for exp in expected:
        durable = durable_by_instance.get(exp.seat_instance_id)
        if durable is None:
            raise SeatAuthenticityInvalid(
                f"expected required-manifest seat_instance_id={exp.seat_instance_id!r} "
                f"(seat_key={exp.seat_key!r}) has NO durable outcome (fail-closed, v5 #1: a required seat "
                "that timed out / degraded / never recorded is still demanded)"
            )
        if durable.required != exp.required:
            raise SeatAuthenticityInvalid(
                f"expected seat {exp.seat_instance_id!r} required={exp.required!r} disagrees with durable "
                f"record required={durable.required!r} (fail-closed)"
            )
        if exp.required and not _is_usable_terminal_status(durable.status):
            raise SeatAuthenticityInvalid(
                f"required expected seat {exp.seat_instance_id!r} durable status {durable.status!r} is not a "
                f"usable terminal (fail-closed, v5 #1; usable = {sorted(USABLE_TERMINAL_SEAT_STATUSES)!r})"
            )
        prov = prov_by_instance.get(exp.seat_instance_id)
        if prov is None:
            raise SeatAuthenticityInvalid(
                f"expected seat_instance_id={exp.seat_instance_id!r} has a durable outcome but NO matching "
                "provenance seat (fail-closed, v5 #1: an omitted required/expected seat cannot be invisible)"
            )
        if prov.verdict != durable.verdict:
            raise SeatAuthenticityInvalid(
                f"expected seat {exp.seat_instance_id!r} provenance verdict={prov.verdict!r} disagrees with "
                f"durable verdict={durable.verdict!r} (fail-closed, v4 #2: verdict binding)"
            )
        if set(prov.finding_ids) != set(durable.finding_ids):
            raise SeatAuthenticityInvalid(
                f"expected seat {exp.seat_instance_id!r} provenance finding_ids {sorted(prov.finding_ids)!r} "
                f"disagree with durable finding_ids {sorted(durable.finding_ids)!r} (fail-closed, v5 #2)"
            )
        required_finding_ids.update(durable.finding_ids)

    # FINDING-CONTENT BINDING (v6 #2): every finding id a durable expected seat
    # logged must be present in the artifact AND match the harness's canonical
    # record EXACTLY on the fields the gate later reads (severity/status) plus
    # the body_ref content digest.
    for fid in sorted(required_finding_ids):
        canonical = canonical_by_id.get(fid)
        if canonical is None:
            raise SeatAuthenticityInvalid(
                f"finding id {fid!r} was logged by a durable seat but has NO harness-authenticated canonical "
                "record (fail-closed, v6 #2)"
            )
        finding = findings_by_id.get(fid)
        if finding is None:
            raise SeatAuthenticityInvalid(
                f"finding id {fid!r} is bound to a durable seat + canonical record but the artifact OMITS the "
                "Finding record (fail-closed, v6 #2: dropping the record to hide a blocking finding)"
            )
        if (
            finding.severity != canonical.severity
            or finding.status != canonical.status
            or finding.body_ref != canonical.body_digest
        ):
            raise SeatAuthenticityInvalid(
                f"finding id {fid!r} content (severity={finding.severity!r} status={finding.status!r}) does "
                f"not match the harness canonical record (severity={canonical.severity!r} "
                f"status={canonical.status!r}) (fail-closed, v6 #2: rewriting a finding non-blocking/clean)"
            )


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
    fold into provenance is not itself a forgery).

    JOIN KEY (agent-harness#191 CR round 1, blocker 3): keyed on the UNIQUE
    `seat_instance_id` whenever ANY record in the call carries one — `seat_key`
    is explicitly non-unique (positional), so two legitimate same-`seat_key`
    instances would COLLIDE on the old `(seat_key, vendor_leg, epoch)` key and
    raise a spurious conflicting-record error (a crash / permanent block for any
    run dispatching duplicate seats). No mixed-key matching: if the call uses
    instance-id keying, EVERY provenance seat AND every durable record consulted
    must carry an instance id, else fail closed (a seat that dropped its id
    cannot silently match on the weaker composite key). The legacy composite key
    is retained ONLY for a call in which NOTHING carries an instance id."""
    use_instance = any(s.seat_instance_id is not None for s in provenance_seats) or any(
        r.seat_instance_id is not None for r in durable_seat_outcomes
    )

    def _key(rec) -> tuple:
        if use_instance:
            if rec.seat_instance_id is None:
                raise SeatAuthenticityInvalid(
                    f"seat {rec.seat_key!r} is missing seat_instance_id while the round uses instance-id "
                    "keying (fail-closed, no mixed-key matching)"
                )
            return ("iid", rec.seat_instance_id)
        return ("composite", rec.seat_key, rec.vendor_leg, rec.epoch)

    index: dict[tuple, SeatOutcomeRecord] = {}
    for record in durable_seat_outcomes:
        key = _key(record)
        if key in index and index[key] != record:
            raise SeatAuthenticityInvalid(
                f"durable seat-outcome ledger has conflicting records for {key!r} (fail-closed)"
            )
        index[key] = record

    for seat in provenance_seats:
        durable = index.get(_key(seat))
        if durable is None:
            raise SeatAuthenticityInvalid(
                f"provenance seat seat_key={seat.seat_key!r} vendor_leg={seat.vendor_leg!r} "
                f"epoch={seat.epoch!r} seat_instance_id={seat.seat_instance_id!r} has NO matching durable "
                "SeatOutcomeRecord (fail-closed, T13: a hand-written provenance seat cannot vouch for a "
                "seat that never ran)"
            )
        # agent-harness#191 CR round 7 / codex#6: when the join is on
        # `seat_instance_id`, `seat_key`/`vendor_leg`/`epoch` are NOT in the join
        # key, so they must be bound EXPLICITLY — otherwise a client artifact
        # could keep a valid instance id but relabel the reviewer/vendor/epoch.
        # (In the legacy composite-key path these three ARE the key, so this is a
        # redundant-but-harmless re-check there.)
        if seat.seat_key != durable.seat_key or seat.vendor_leg != durable.vendor_leg or seat.epoch != durable.epoch:
            raise SeatAuthenticityInvalid(
                f"seat instance {seat.seat_instance_id!r} identity disagrees with its durable record "
                f"(seat_key/vendor_leg/epoch: provenance={(seat.seat_key, seat.vendor_leg, seat.epoch)!r} "
                f"durable={(durable.seat_key, durable.vendor_leg, durable.epoch)!r}) — fail-closed, T13: an "
                "instance id can never relabel the seat it vouches for"
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
# Finding 1 (agent-harness#191 CR, Lane D) — bind EVERY resolved-status delta
# round to its OWN delta_round_seats, never trusting the artifact-wide seat
# pool alone.
# --------------------------------------------------------------------------- #


def _require_delta_round_seat_binding(
    delta_chain: Sequence[DeltaReviewRecord],
    durable_seat_outcomes: Sequence[SeatOutcomeRecord],
) -> None:
    """agent-harness#191 CR, Lane D finding 1 (and the follow-up CR that
    caught F1's authentication-without-verdict-folding gap): for every round
    in `delta_chain` whose `status` is resolved/pass-eligible
    (`_RESOLVED_DELTA_STATUSES` — `reviewed-clean` OR `escalated-whole-patch`),
    require that round's OWN `delta_round_seats` (a) be non-empty, (b)
    authenticate against the durable `SeatOutcomeRecord` ledger (§6.3, reusing
    `cross_check_seat_authenticity` — the EXACT same cross-check the
    artifact-wide seat list already gets), (c) include AT LEAST ONE `required`
    seat (mirrors `compose_gate_status`'s artifact-level `no_required_seats`
    rule — design ambiguity #3, "no vacuous pass on an empty [required] seat
    set" — applied per round, not just once for the whole artifact: a round
    "blessed" only by optional seats corroborates nothing), (d) have EVERY
    `required` delta-round seat carry a non-DISAGREE verdict (reuses
    `_unresolved_required_seats` — the EXACT artifact-level "every required
    seat has a non-DISAGREE verdict" rule, design §8, folded into THIS round's
    own seats — closes the original follow-up bug: authenticating a round's
    seats never used to mean their VERDICT was checked, so a reviewed-clean
    round whose own required reviewer DISAGREED, or never verdicted at all,
    could still reach PASS on the strength of the artifact-wide seats
    agreeing), and (e) corroborate that round's OWN `resolved_finding_ids`
    (always) and `reopened_finding_ids` (only when `status == reviewed-clean`
    — mirrors `fab_delta.build_delta_round`'s own asymmetric rule: an
    `escalated-whole-patch` round is, by definition, still going BACK into
    whole-patch review, so its reopened ids have nothing yet to corroborate).
    A round that is `pending`/`invalidated` is skipped here —
    `resolve_chain_resolution` already fails the WHOLE gate closed on that
    (finding 2), so this function only needs to guard resolved-looking rounds
    from being trusted on an uncorroborated, unverdicted, or fabricated seat
    claim.

    Note (d) is DELIBERATELY separate from `require_seat_corroboration`
    (reused unmodified in (e)): that function's contract is "was this finding
    id reviewed by SOME seat at all" (design resolved-ambiguity #3 — it is not
    full-board consensus, and accepting a DISAGREE verdict as "this id was
    looked at" is correct for ITS narrower question); it is also vacuous when
    a round resolves/reopens nothing (`finding_ids == ()`), which is fine for
    that same narrower question but is NOT a safe stand-in for "did this
    round's own required reviewer actually agree" — hence (d) is a SEPARATE,
    unconditional (not gated on any finding id) required-seat-verdict check
    that fires even when the round resolves/reopens zero findings.

    Raises `DeltaRoundSeatBindingInvalid` (fail-closed) on the FIRST round
    that fails any of the five checks — a `reviewed-clean` delta with
    `delta_round_seats=()`, or one whose only required seat DISAGREES (or
    never verdicted), can never reach a PASS."""
    for index, record in enumerate(delta_chain):
        if record.status not in _RESOLVED_DELTA_STATUSES:
            continue
        if not record.delta_round_seats:
            raise DeltaRoundSeatBindingInvalid(
                f"delta_chain[{index}] (status={record.status!r}) has NO delta_round_seats of its own "
                "(fail-closed, F1: a resolved delta round with zero seats cannot be corroborated — the "
                "artifact-wide seat list is not a substitute for THIS round's own reviewers)"
            )
        try:
            cross_check_seat_authenticity(record.delta_round_seats, durable_seat_outcomes)
        except SeatAuthenticityInvalid as exc:
            raise DeltaRoundSeatBindingInvalid(
                f"delta_chain[{index}] delta_round_seats failed the §6.3 durable authenticity "
                f"cross-check (fail-closed): {exc}"
            ) from exc
        if not any(seat.required for seat in record.delta_round_seats):
            raise DeltaRoundSeatBindingInvalid(
                f"delta_chain[{index}] (status={record.status!r}) has delta_round_seats but NONE are "
                "required (fail-closed: mirrors the artifact-level no-vacuous-pass-on-an-empty-"
                "required-seat-set rule — a round blessed only by optional seats is never affirmatively "
                "reviewed)"
            )
        unresolved_round_seats = _unresolved_required_seats(record.delta_round_seats)
        if unresolved_round_seats:
            raise DeltaRoundSeatBindingInvalid(
                f"delta_chain[{index}] (status={record.status!r}) has required delta_round_seats with "
                f"no AGREE/PARTIALLY AGREE verdict (fail-closed): {unresolved_round_seats!r} — a "
                "required seat's DISAGREE (or missing) verdict on ITS OWN round is never overridden by "
                "the artifact-wide seats agreeing"
            )
        try:
            require_seat_corroboration(record.resolved_finding_ids, record.delta_round_seats)
            if record.status == DELTA_STATUS_REVIEWED_CLEAN:
                require_seat_corroboration(record.reopened_finding_ids, record.delta_round_seats)
        except ResolvedClaimUnverified as exc:
            raise DeltaRoundSeatBindingInvalid(
                f"delta_chain[{index}] has a resolved/reopened finding claim with no corroborating "
                f"delta-round seat verdict (fail-closed, §5.3): {exc}"
            ) from exc


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
    exists.

    Fail-closed (`GateChainUnresolved`, a `ProvenanceInvalid` subclass) when
    ANY round in the chain — not merely the final one (agent-harness#191 CR,
    Lane D finding 2: "the chain is valid only if every member is
    reviewed-clean [/ resolved] and contiguous", design §5.1) — is
    `pending`/`invalidated`, has no `resulting_head_digest`, or fails a
    defense-in-depth T5 review-scope re-check against ITS OWN
    `resulting_head_digest` (reuses `fab_delta.enforce_review_scope_for_
    escalation`, never re-implemented — this does NOT assume any record was
    actually built via `build_delta_round`, since a loaded-from-JSON artifact
    was not necessarily constructed through that path); also fail-closed when
    the artifact's `base.ref_identity` is malformed. An intermediate
    `pending`/`invalidated` round, or an intermediate T5 violation, is
    structurally identical to a `verify_chain` splice/reorder in spirit — it
    must never be masked by a later, resolved-looking final round."""
    if not artifact.delta_chain:
        binding = EquivalenceBinding.from_provenance_artifact(artifact)
        return ChainResolution(
            binding=binding,
            escalation=Escalation(required=False, trigger=None),
            carried_forward_findings=(),
            re_reviewed_findings=(),
        )

    for index, record in enumerate(artifact.delta_chain):
        if record.status not in _RESOLVED_DELTA_STATUSES:
            raise GateChainUnresolved(
                f"delta_chain[{index}] has status={record.status!r} (fail-closed): not a resolved, "
                f"pass-eligible state (must be one of {sorted(_RESOLVED_DELTA_STATUSES)!r}) — EVERY "
                "round in the chain must be resolved, not just the final one (F2)"
            )
        if record.resulting_head_digest is None:
            raise GateChainUnresolved(f"delta_chain[{index}] has no resulting_head_digest (fail-closed)")

        # Defense-in-depth: re-run T5 against EVERY record AS LOADED (its OWN
        # resulting_head_digest — the same value `build_delta_round` uses at
        # construction time for this exact round), never merely trusting
        # that whatever produced it already enforced this, and never
        # skipping any round but the last.
        enforce_review_scope_for_escalation(
            escalation=record.escalation,
            review_scope=record.review_scope,
            covering_patch_digest=record.resulting_head_digest,
        )

    last = artifact.delta_chain[-1]
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


def _validate_chain_binds_to_git(repo: Path, artifact: ReviewProvenanceArtifact) -> None:
    """3b-gate CR round 1 — wire `fab_delta.validate_delta_binds_to_parent` into
    the gate (it had NO production caller). For EACH delta round, recompute
    `resulting_head_digest` + `delta_changed_paths` from LIVE git off the
    (already-authenticated) `delta_head_sha` and re-check the parent chain/patch
    linkage — the T12 posture: NEVER trust a stored digest for a git-derivable
    field, even the durable one. Defense-in-depth beyond `_delta_resolution_
    digest`'s durable binding. Fail-closed (`DeltaBindingInvalid`, a
    `ProvenanceInvalid`) on the first mismatch; no-op for the candidate-only
    (empty-chain) case. MUST run AFTER `cross_check_round_authenticity` so it
    operates on authenticated records."""
    if not artifact.delta_chain:
        return
    ref_identity = artifact.base.ref_identity
    repo_slug, sep, _ref = ref_identity.partition("#")
    if not sep or not repo_slug:
        raise DeltaBindingInvalid(f"malformed base ref_identity (fail-closed): {ref_identity!r}")
    parent_head_sha = artifact.candidate.head_sha
    parent_patch_digest = artifact.candidate.patch_digest
    parent_chain_digest = artifact.compute_c0()
    for record in artifact.delta_chain:
        validate_delta_binds_to_parent(
            record,
            repo=repo,
            base_sha=artifact.base.base_sha,
            repo_slug=repo_slug,
            parent_head_sha=parent_head_sha,
            parent_patch_digest=parent_patch_digest,
            parent_chain_digest=parent_chain_digest,
        )
        parent_head_sha = record.delta_head_sha
        parent_patch_digest = record.resulting_head_digest
        parent_chain_digest = record.chain_digest


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
    delta_chain: Sequence[DeltaReviewRecord],
) -> tuple[str, ...]:
    """design ambiguity #2 (module docstring), generalized across the WHOLE
    chain (agent-harness#191 CR, Lane D finding 2 — "unresolved-finding
    evaluation consults only the final round"): a `severity=="block"` finding
    starts from its top-level `status` (`clean` == provisionally resolved —
    `Finding.status` is never mutated by a later round's carry-forward
    decision, per module docstring ambiguity #2), then folds EVERY
    `delta_chain` round's `resolved_finding_ids`/`reopened_finding_ids` IN
    ORDER: a round that reopens id `f` without ALSO resolving `f` in that SAME
    round marks `f` unresolved from that point forward; a LATER round
    resolving `f` (directly, or by reopening-and-resolving it in the same
    round) marks it resolved again. The chain-FINAL fold state — not merely
    the LAST round's own two lists — decides whether a block finding remains
    open, so an intermediate reopen a later, clean-looking round never
    mentions can never be silently masked."""
    block_ids = {f.id for f in findings if f.severity == "block"}
    resolved_state: dict[str, bool] = {f.id: (f.status == "clean") for f in findings if f.severity == "block"}
    for record in delta_chain:
        resolved_here = set(record.resolved_finding_ids)
        reopened_here = set(record.reopened_finding_ids)
        for fid in reopened_here:
            if fid not in block_ids:
                continue
            resolved_state[fid] = fid in resolved_here
        for fid in resolved_here:
            if fid not in block_ids or fid in reopened_here:
                continue  # already folded above (reopened-and-resolved-same-round)
            resolved_state[fid] = True
    return tuple(sorted(fid for fid, ok in resolved_state.items() if not ok))


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
    round_reader: Callable[[int], FabReviewRound] | None = None,
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

    `status == GATE_STATUS_PASS` iff ALL of: EVERY round in the delta chain
    (not just the final one — F2) is itself resolved/pass-eligible and the
    chain resolves to a governing binding whose live-recomputed equivalence
    is `EQUIVALENT`; EVERY provenance seat is authenticated against its
    durable `SeatOutcomeRecord` (§6.3); EVERY resolved-status delta round's
    OWN `delta_round_seats` are ALSO authenticated, include at least one
    `required` seat, have EVERY required seat carry a non-DISAGREE verdict,
    and corroborate that round's `resolved_finding_ids`/`reopened_finding_ids`
    (F1 + the follow-up CR's verdict-folding fix — `_require_delta_round_seat_
    binding`; the artifact-wide seat list is not a substitute for a round's
    own reviewers, and authenticating a round's seats is not a substitute for
    checking what they actually VERDICTED); every round's material
    re-verifies against its claimed `reviewed_material_digest` (§6.4); at
    least one artifact-level seat is `required` (design ambiguity #3 — no
    vacuous pass on an empty seat set); every required artifact-level seat has
    a non-DISAGREE verdict; and no
    `block`-severity finding remains unresolved ACROSS THE WHOLE CHAIN (F2 —
    `_unresolved_block_findings` folds every round's audit trail, not just
    the final one). Otherwise `GATE_STATUS_BLOCK`, surfaced with
    `equivalence_verified.reason` when the failure is equivalence-shaped, or a
    reason embedded in the raised/caught exception text otherwise."""
    artifact = read_provenance(repo, run_id)  # ProvenanceNotFound propagates deliberately.
    reviewed_sha = artifact.candidate.head_sha

    # DELTA CHAINS ARE NOW EVALUATED (piece 3b G2 — the piece-2 "nonempty
    # delta_chain BLOCKS" deferral is LIFTED). Forge-resistance transfers from
    # "block every chain" to "authenticate every round": `cross_check_round_
    # authenticity` now applies the candidate-grade check (verdict binding +
    # finding_ids binding + per-epoch completeness + canonical finding-content
    # binding) to the candidate round AND each delta round, keyed by each round's
    # own epoch's durable record (G1). A nonempty chain therefore no longer fails
    # closed by construction; it must AUTHENTICATE, round by round, or BLOCK.
    try:
        durable_seats = seat_outcomes if seat_outcomes is not None else read_seat_outcomes(repo, run_id)
        # INVARIANT (3b-gate CR round 1): NO gate decision may be computed from a
        # client artifact field before that field is bound to its durable record.
        # So AUTHENTICATE FIRST — bind every round field the gate reads (seats +
        # the round-RESOLUTION fields: status / resulting_head_digest / escalation
        # / finding-flow / chain-topology, via `_delta_resolution_digest`) to the
        # per-epoch durable record — THEN run `verify_chain` /
        # `resolve_chain_resolution`, which read those now-authenticated fields.
        # (Previously resolution ran first, so the equivalence binding was built
        # from UNAUTHENTICATED client `resulting_head_digest` / status / topology —
        # the exact P0-1/gemini#2/#3 bypasses.) The trust anchors are read from the
        # run store keyed by `run_id` + epoch (never the artifact); the
        # `round_reader`/`seat_outcomes` params are a test seam ONLY — every
        # PRODUCTION caller passes `None` so the gate reads from disk.
        cross_check_round_authenticity(repo, run_id, artifact, durable_seats, round_reader=round_reader)
        _require_delta_round_seat_binding(artifact.delta_chain, durable_seats)
        reverify_all_material(repo, run_id, artifact)
        # Now every client field these read is proven == durable.
        verify_chain(artifact)
        # LIVE-GIT recompute (3b-gate CR round 1 — the T12 posture, never trust a
        # stored digest for a git-derivable field): wire `validate_delta_binds_to_
        # parent` into the production path so each delta round's
        # `resulting_head_digest` + `delta_changed_paths` are recomputed from LIVE
        # git off the AUTHENTICATED `delta_head_sha`, and the parent linkage is
        # re-checked — defense-in-depth beyond the durable resolution binding. Runs
        # AFTER cross-check so it operates on authenticated records.
        _validate_chain_binds_to_git(repo, artifact)
        resolution = resolve_chain_resolution(artifact)
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
    unresolved_block = _unresolved_block_findings(artifact.findings, artifact.delta_chain)

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
    """The agent-review-gate wiring for FAB (design §9 Lane D).

    FAB is currently DORMANT (agent-harness#191 CR, finding 3, traced): no
    live producer writes `fab_gate_inputs`/provenance, and no live caller of
    `build_phase_loop_closeout` threads a real `run_id` through — so this
    validator is inert in practice TODAY. The rule below exists so it cannot
    be silently bypassed once a producer is wired, not because it currently
    fires on any real run.

    **Fail-closed BY CONSTRUCTION (F3).** `run_id` is the TRUSTED FAB-scope
    marker — per the module docstring's "`run_id` trust" note, it is
    resolved ONLY by the CALLER from its own trusted process-local
    run-allocation state, NEVER from `ctx.terminal`/`ctx.automation`
    (agent/PR-influenced self-report fields; this validator does not and
    must never fall back to reading a self-reported run_id — that would let
    an attacker stay inert simply by omitting one). Once `run_id` is
    genuinely present, this validator commits to ONE of exactly two
    outcomes — PASS-shaped `[]` or a `block` finding — never a THIRD
    "quietly not applicable" outcome that a caller could induce by dropping
    an input:

      (i)   NO `fab_gate_inputs` at all, or no `run_id` extractable from it
            → inert (`[]`). This is the ONLY inert branch: it means "this run
            was never scoped to FAB" (correct today, since nothing wires
            `fab_gate_inputs` yet — the CALLER opts a run into FAB gating
            by populating this mapping AT ALL).
      (ii)  `run_id` present but `ctx.repo_root` / `live_base_ref_name` /
            `live_head_sha` is missing or malformed → BLOCK. The gate was
            scoped to FAB (a trusted run_id exists) but cannot complete —
            an incomplete wiring must never be indistinguishable from "not
            FAB".
      (iii) `run_id` present but `compose_gate_status` raises
            `ProvenanceNotFound` → BLOCK, not inert. A trusted run_id is
            the caller's OWN assertion that this run took the FAB path;
            "no provenance recorded for a run the caller itself scoped to
            FAB" means the write path silently failed or was skipped, not
            "this is a non-FAB run" — a phase the harness ACTUALLY
            delta-reviewed must never pass by having its provenance write
            dropped after the fact.

    `fab_gate_inputs` is a plain, untyped mapping (not a typed import from
    this module) so `closeout_validators.py` never needs to import
    `fab_gate` — avoiding a `closeout_validators` <-> `fab_gate` import cycle
    (`fab_gate` already imports `CloseoutContext`/`register_closeout_
    validator`/`ReviewFinding`/`verdict_binds_to` FROM `closeout_validators`);
    optional keys once `run_id` is present: `origin` (default `"origin"`),
    `waiver` (audited echo, never silently changes the computed status —
    design §8).

    Follows `verification_evidence_validator`'s EXACT posture: `severity=
    "block"` (warn-default under the global `PHASE_LOOP_REVIEW` control,
    opt-in `block`), `blocker_class="review_gate_block"` (non-human —
    `apply_review_findings` never sets `human_required`)."""
    inputs = ctx.fab_gate_inputs
    if not inputs:
        return []  # (i) — no fab_gate_inputs at all: never scoped to FAB.
    try:
        run_id = inputs["run_id"]
    except (KeyError, TypeError):
        return []  # (i) — no run_id: the TRUSTED scope marker was never set.
    if run_id is None:
        return []  # (i) — an explicit null is still "no scope marker".
    run_id = str(run_id)

    # From here on `run_id` is present — this run IS scoped to FAB (F3). Every
    # remaining branch is a `block`, never a silent `[]`.
    if not ctx.repo_root:
        return [
            ReviewFinding(
                code=FAB_GATE_FINDING_CODE,
                reason=(
                    f"FAB gate scoped to run_id={run_id!r} but ctx.repo_root is missing "
                    "(fail-closed, F3: a FAB-scoped run must never silently stay inert)"
                ),
                severity="block",
                blocker_class="review_gate_block",
            )
        ]
    repo = Path(ctx.repo_root)

    try:
        live_base_ref_name = str(inputs["live_base_ref_name"])
        live_head_sha = str(inputs["live_head_sha"])
        origin = str(inputs.get("origin") or "origin")
        waiver = inputs.get("waiver")
        waiver = str(waiver) if waiver is not None else None
    except (KeyError, TypeError) as exc:
        return [
            ReviewFinding(
                code=FAB_GATE_FINDING_CODE,
                reason=(
                    f"FAB gate scoped to run_id={run_id!r} but live_base_ref_name/live_head_sha "
                    f"is missing or malformed (fail-closed, F3): {exc}"
                ),
                severity="block",
                blocker_class="review_gate_block",
            )
        ]

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
        # (iii) — F3: run_id is the trusted scope marker, so "no provenance
        # for a claimed run_id" is a broken/incomplete gate, NEVER "not
        # applicable". A caller that wants genuine non-FAB inertness omits
        # `fab_gate_inputs`/`run_id` entirely (branch (i)) instead.
        return [
            ReviewFinding(
                code=FAB_GATE_FINDING_CODE,
                reason=(
                    f"FAB gate scoped to run_id={run_id!r} but no provenance was recorded for it "
                    "(fail-closed, F3: a scoped gate with no provenance is broken, not inert)"
                ),
                severity="block",
                blocker_class="review_gate_block",
            )
        ]
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
