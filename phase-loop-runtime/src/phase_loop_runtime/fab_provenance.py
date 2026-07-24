"""FAB (Consiliency/agent-harness#191) Lane A — provenance schema, hash chain, and
trust root.

Ground: `plans/design-fab-191-delta-review.md` (v2, panel-reviewed). This module
implements ONLY Lane A (see design §9): the frozen provenance/gate-status
dataclasses + JSON (de)serializers, the §6.2 hash chain construction/verification,
the §6.1 trust-root write/read API (harness-only-written, run-store-keyed), and the
§6.4 immutable-material snapshot/reverify primitives. It deliberately does NOT
implement:

  * the canonical binary `patch_digest` equivalence math (design §3/§4 — Lane B).
    The ONE narrow exception (agent-harness#191 CR / F3): `reject_client_supplied_provenance`
    shells out to `git ls-files` to ask a boundary question — "is this exact path
    git-tracked in this repo" — because a tracked path is one a PR branch commit
    can literally place content at. This is NOT the Lane-B patch-digest/equivalence
    math (no diffing, no blob hashing, no merge-base); it is a single yes/no probe
    used only to narrow an already-computed run-store path, and it fails safe (see
    `_is_git_tracked`'s docstring for the exact fail-open/fail-closed split);
  * delta-chain traversal, carry-forward, or escalation DECISION logic (design
    §5.3-§5.5 — Lane C — though the frozen `DeltaReviewRecord` shape and the hash
    chain that binds it are defined here);
  * gate wiring into `governed_premerge`/closeout (design §8 composition — Lane D).

Fields that a later lane computes (e.g. `candidate.patch_digest`,
`DeltaReviewRecord.resulting_head_digest`, `equivalence`) are typed/serialized here
but left `None`/absent until that lane populates them — Lane A only freezes their
TYPE and serialization slot.

FROZEN INTERFACE (IF-0-FAB-A-1) — B/C/D code against this without renegotiation:
  * Schema strings: `SCHEMA_REVIEW_PROVENANCE = "fab.review-provenance.v2"`,
    `SCHEMA_DELTA_REVIEW = "fab.delta-review"`, `SCHEMA_GATE_STATUS =
    "fab.gate-status.v2"`.
  * `artifact_digest` canonicalization: SHA-256 over the artifact's JSON payload
    (sorted keys, tight separators — see `verification_evidence._canonical_artifact_digest`,
    REUSED here, not reimplemented) with `artifact_digest` itself the ONLY
    self-excluded field (mirrors #243's `log_sha256` posture).
  * Hash-chain formula (design §5.1/§6.2): the candidate round digest
    `C0 = H(policy || review_scope || material_digests || findings || base_binding || None)`
    and each delta round `Ci = H(policy_i || review_scope_i || material_digests_i ||
    findings_i || C_{i-1})`. The `||` concatenation is realized as a canonical JSON
    OBJECT keyed by field name (`{"policy":..., "review_scope":..., "material_digests":...,
    "findings":..., "parent_chain_digest":..., ["base_binding":...]}`) hashed with the
    SAME reused canonicalization helper — a naive byte concatenation of the
    components would be ambiguous at field boundaries (a collision-prone
    normalization the design's §3.5 "no normalization" principle forbids), so a
    keyed JSON envelope is the unambiguous realization of "||" (see module-level
    "design ambiguities resolved" note below).
  * agent-harness#191 CR tightenings (B/C/D must code against these, not the pre-CR
    shape): (1) `from_json`/`from_dict` are STRICT — an unknown top-level or nested
    field, or a duplicate JSON object key anywhere in the document, raises
    `ProvenanceInvalid` (F1); (2) `Finding.body_ref` is REQUIRED (`str`, not
    `str | None`) — constructing/loading a `Finding` with no body reference raises
    `ProvenanceInvalid` (F5); (3) `verify_chain` requires a `status=reviewed-clean`
    delta round to carry a non-null `parent_digest`, and requires `parent_digest`
    whenever the prior round's patch digest is known, not only when both happen to
    be present (F2); (4) `reverify_material` takes a new REQUIRED keyword-only
    `expected_reviewed_material_digest` argument, checked against the new
    `aggregate_material_digest(material_digests)` primitive (F4).

Design ambiguities resolved in this lane (see also the closing-report to the
orchestrator):
  1. **Seat field name**: the design's §6.5 JSON example spells the seat's vendor
     field `vendor_family`, but the Lane A task brief requires it to match
     `panel_invoker.SeatOutcomeRecord` field-for-field (`vendor_leg`) so Lane D's
     §6.3 cross-check can compare records directly without a name-mapping layer.
     This module uses `vendor_leg` (the task brief's explicit instruction wins).
  2. **"||" realization**: see "Hash-chain formula" above.
  3. **`findings_i` / `material_digests_i` / `policy_i` per delta round**: the
     §5.1 pseudocode block doesn't re-list these as `DeltaReviewRecord` fields, but
     §5.5 says "each artifact/delta record carries `review_scope`" and the schema
     needs SOMETHING concrete to hash into `Ci`. This module makes them explicit
     `DeltaReviewRecord` fields: `policy` (the boundary-manifest reference in force
     for that round — constant across the chain in the frozen-manifest case, but
     recorded per round so a manifest-swap can never be omitted from the hash),
     `review_scope`, `material_digests` (this round's own reviewed material, which
     may be empty if it reuses the candidate's), and — since findings are held
     canonically once at the artifact's top level (to avoid re-duplicating
     `body_ref`-bearing records every round) — the round's contribution to
     `findings_i` is the deterministic triple of sorted finding-ID lists
     (`resolved_finding_ids`, `carried_forward_finding_ids`, `reopened_finding_ids`)
     it already carries.
  4. **`reverify_material` semantics**: design §6.4 says the gate "re-hashes the
     snapshot" — implying the immutable run-store COPY is authoritative (edits to
     the mutable original are harmless because the gate never re-reads it). The
     Lane A task brief separately requires that "editing the underlying file after
     snapshot is DETECTED (reverify fails)". Both are honored: `reverify_material`
     re-hashes BOTH the immutable snapshot copy (primary authority — proves what
     was captured at review time) AND the live `ref` path (a drift check), and
     fails closed if EITHER no longer matches the recorded digest. This is a
     strictly safer default for a security boundary; Lane D can decide whether
     live-drift alone should hard-block or merely re-trigger review, but Lane A's
     primitive never silently tolerates it.
  5. **`run_id` resolution**: no existing helper resolves an opaque run id to its
     run-store directory. `phase_loop_runtime.observability.run_artifacts` already
     names each run's root `phase_loop_runs_dir(repo) / run_id` (see
     `runtime_paths.phase_loop_runs_dir`); this module assumes `run_id` IS that
     directory name (the same convention `SeatOutcomeRecord` persistence and
     `verification.json` already live under) and adds a minimal
     `provenance_dir_for_run` helper on top of it. It does not itself allocate run
     ids — callers pass the run id the harness already produced for the run.
  6. **Trust-root path enforcement is location-narrowing, not authorship-proving
     (agent-harness#191 CR / F3)**: `reject_client_supplied_provenance` cannot
     prove a provenance blob was written BY the harness — path location is not
     authorship. It enforces the narrower, honest, LOCAL invariant Lane A can
     actually check: the candidate path resolves to the run-store path for the
     given `run_id` AND that path is not itself git-tracked (so a PR branch commit
     cannot spoof it by literally committing a file there — `.phase-loop/` is
     excluded only via the local, non-committed `.git/info/exclude`, not a
     committed `.gitignore`; see `runtime_paths.EXCLUDE_ENTRIES` and the new §6.1a
     trust-model note in the design doc). Full authorship enforcement — the gate
     reading provenance from trusted run-state, with `run_id` resolved from a
     trusted source rather than PR input — is a Lane D requirement this storage
     layer enables but does not itself perform.
  7. **`Finding.body_ref` is REQUIRED, not optional (agent-harness#191 CR / F5)**:
     every legitimate `Finding` in this design originates from a seat's review
     output (§6.5's illustrative schema always shows a populated `body_ref`; the
     module's own "metadata-only" posture is meaningless without a traceable
     origin). There is no design-described finding type that legitimately lacks
     one — a bodyless finding is an unaudited gap, not a structural/advisory case
     — so `body_ref=None` now fails closed at construction rather than being
     silently accepted.
  8. **`reverify_material` binds to `review_scope.reviewed_material_digest`
     (agent-harness#191 CR / F4)**: design §6.4 requires the rehashed snapshot to
     equal the artifact's CLAIMED `reviewed_material_digest`, not merely to be
     internally self-consistent. `reverify_material` now takes that expected
     digest as a REQUIRED keyword argument (an optional/skippable check would
     recreate the exact "None skips the check" fail-open this finding is about)
     and aggregates the per-ref digests via the new `aggregate_material_digest`
     (REUSING `_encode_and_digest` — one canonicalization path, not a second one;
     mirrors finding 3's "two canonical encodings" lesson).

Fail-closed discipline (this is a security trust root): unknown, ambiguous,
oversized, malformed, or unrepresentable input NEVER silently passes — every load
path raises a typed `ProvenanceInvalid` (or a subclass). This now explicitly
includes an on-disk JSON payload with an UNKNOWN top-level or nested field, or a
DUPLICATE JSON object key: both are rejected via a strict `object_pairs_hook`
parse plus a per-`from_dict` known-field check, so a parsed object always
faithfully represents the audited bytes and nothing un-audited rides along outside
the digest (agent-harness#191 CR / F1). Additive only: nothing in
`panel_invoker.py` / `verification_evidence.py` is modified — their helpers are
imported and reused, never re-implemented.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Sequence

from .runtime_paths import phase_loop_runs_dir
from .verification_evidence import _canonical_artifact_digest

# --------------------------------------------------------------------------- #
# Frozen schema identifiers (IF-0-FAB-A-1)
# --------------------------------------------------------------------------- #

SCHEMA_REVIEW_PROVENANCE = "fab.review-provenance.v2"
SCHEMA_DELTA_REVIEW = "fab.delta-review"
SCHEMA_GATE_STATUS = "fab.gate-status.v2"

# design §5.4/§5.2: typed delta status enum — never inferred from prose.
DELTA_STATUS_REVIEWED_CLEAN = "reviewed-clean"
DELTA_STATUS_ESCALATED_WHOLE_PATCH = "escalated-whole-patch"
DELTA_STATUS_PENDING = "pending"
DELTA_STATUS_INVALIDATED = "invalidated"
_VALID_DELTA_STATUSES = frozenset(
    {
        DELTA_STATUS_REVIEWED_CLEAN,
        DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        DELTA_STATUS_PENDING,
        DELTA_STATUS_INVALIDATED,
    }
)

# design §5.5: review_scope.mode enum.
REVIEW_SCOPE_WHOLE_PATCH = "whole-patch"
REVIEW_SCOPE_DELTA_ONLY = "delta-only"
_VALID_REVIEW_SCOPE_MODES = frozenset({REVIEW_SCOPE_WHOLE_PATCH, REVIEW_SCOPE_DELTA_ONLY})

# panel_invoker.terminal_verdict's frozen output set (imported by name, not
# re-derived, so this module and panel_invoker can never silently diverge on
# what a "verdict" is).
_VALID_VERDICTS = frozenset({"AGREE", "PARTIALLY AGREE", "DISAGREE"})

# design §8: gate-status.result / equivalence.result enum.
EQUIVALENCE_EQUIVALENT = "EQUIVALENT"
EQUIVALENCE_INVALIDATED = "INVALIDATED"
_VALID_EQUIVALENCE_RESULTS = frozenset({EQUIVALENCE_EQUIVALENT, EQUIVALENCE_INVALIDATED})

GATE_STATUS_PASS = "pass"
GATE_STATUS_BLOCK = "review_gate_block"
_VALID_GATE_STATUSES = frozenset({GATE_STATUS_PASS, GATE_STATUS_BLOCK})

# design §6.5: `body_ref` is a content-ref DIGEST, never inline review text
# (finding 2's "metadata-only" / `serialize_seat_outcome` posture). Frozen shape:
# "sha256:<64 lowercase hex chars>". Anything else (a sentence, a URL, empty) is
# rejected fail-closed at construction — this is the concrete mechanism that
# enforces "a record cannot carry raw review prose".
_CONTENT_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Oversize guard (mirrors agent-harness#243's MAX_ARTIFACT_BYTES posture, sized up
# because a long delta chain accumulates many rounds' metadata over a long-lived
# PR): a provenance artifact past this bound is a tampered/runaway payload,
# rejected fail-closed BEFORE it is parsed or trusted.
MAX_PROVENANCE_ARTIFACT_BYTES = 8 * 1024 * 1024
# Gate-status is a small echo record (ids + digests, no findings/seats detail) —
# a much tighter cap is appropriate and still generous.
MAX_GATE_STATUS_BYTES = 512 * 1024
# A single delta round, standalone (e.g. exchanged before being appended to an
# artifact's delta_chain) — bounded well under the full-artifact cap.
MAX_DELTA_REVIEW_RECORD_BYTES = 1 * 1024 * 1024

# Immutable-material snapshot: stream hashing in 1 MiB chunks (mirrors the
# established #114 pattern in panel_invoker.py's `_context_ref_entry`,
# panel_invoker.py:652-658 — same chunk size, same never-buffer-whole posture).
_MATERIAL_HASH_CHUNK_BYTES = 1 << 20
MATERIAL_SNAPSHOT_DIRNAME = "fab-material"
PROVENANCE_FILENAME = "fab-provenance.json"

# run_id is a directory-name component under `.phase-loop/runs/` (see
# `observability.run_artifacts`'s own naming) — restrict to a safe charset so a
# hostile/malformed run_id can never escape the run store (defense in depth on
# top of the resolved-path containment check in `provenance_dir_for_run`).
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


# --------------------------------------------------------------------------- #
# Exceptions — fail-closed, typed, never silent
# --------------------------------------------------------------------------- #


class ProvenanceInvalid(ValueError):
    """Fail-closed sentinel: raised whenever a provenance/gate-status payload is
    oversized, malformed, contains a surrogate, fails to (de)serialize, fails its
    self-digest, or is otherwise not trustworthy. Unknown/ambiguous input always
    raises this (or a subclass) — it is never silently accepted or fail-open."""


class ProvenanceNotFound(ProvenanceInvalid):
    """The run store has no provenance artifact for the given run id."""


class ChainVerificationError(ProvenanceInvalid):
    """A hash-chain recompute or contiguity check failed (design §6.2/T13/I8) —
    e.g. a spliced/fabricated round, a reordered round, or a broken
    `parent_digest`/`parent_chain_digest` link."""


# --------------------------------------------------------------------------- #
# Canonical encoding — reuse verification_evidence's canonicalization, never
# reimplement it (task instruction; also closes design finding 3's "two
# canonical encodings" concern by keeping exactly ONE JSON canonicalization path
# for every non-path-bearing digest in this module).
# --------------------------------------------------------------------------- #


def _encode_and_digest(payload: Mapping[str, Any], *, exclude: str | None = None) -> str:
    """SHA-256 over `payload` canonicalized exactly like
    `verification_evidence._canonical_artifact_digest` (sorted keys, tight
    separators) — REUSED, not reimplemented. `exclude`, when given, is stripped
    from `payload` BEFORE calling the shared helper (which itself only strips its
    own hardcoded `"log_sha256"` key — a no-op here since that key never appears
    in a FAB payload). This is how a differently-named self-excluded field
    (`artifact_digest` here, vs. #243's `log_sha256`) is supported without
    touching `verification_evidence.py`. Any `json.dumps` failure (a
    non-serializable value slipping into an `Any`-typed field, e.g. `policy`)
    fails CLOSED as `ProvenanceInvalid`, never silently."""
    material = {k: v for k, v in payload.items() if k != exclude} if exclude else dict(payload)
    try:
        return _canonical_artifact_digest(material)
    except (TypeError, ValueError) as exc:
        raise ProvenanceInvalid(f"json.dumps failed while canonicalizing payload: {exc}") from exc


def _scan_for_surrogates(value: Any, *, _path: str = "$") -> None:
    """Recursively fail closed on any lone UTF-16 surrogate in a string value
    (design §3.5 / task item 3). A lone surrogate (e.g. from an unpaired
    `\\ud800` JSON escape) parses fine under `json.loads` but cannot be encoded
    back to UTF-8 — an ambiguous, unrepresentable state this module refuses to
    carry forward."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProvenanceInvalid(f"surrogate in value at {_path} (fail-closed): {exc}") from exc
        return
    if isinstance(value, Mapping):
        for key, sub in value.items():
            _scan_for_surrogates(key, _path=f"{_path}.<key>")
            _scan_for_surrogates(sub, _path=f"{_path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _scan_for_surrogates(sub, _path=f"{_path}[{i}]")


def _strict_object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Trust-root strict parse (agent-harness#191 CR / F1): a DUPLICATE key within
    the same JSON object is rejected fail-closed. Plain `json.loads` silently keeps
    only the LAST value for a repeated key — for a security trust root that means
    a byte-identical file could parse to two different logical objects depending
    on which key "wins", which is exactly the kind of ambiguity this module's
    fail-closed posture forbids elsewhere (surrogates, oversize, malformed JSON).
    `object_pairs_hook` is invoked by `json.loads` for EVERY JSON object in the
    document (top-level and nested, including array elements), so passing this as
    the hook makes duplicate-key rejection recursive for free."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceInvalid(
                f"duplicate JSON object key {key!r} (fail-closed, strict trust-root parse — "
                "the on-disk bytes must map to exactly one logical object)"
            )
        result[key] = value
    return result


def _reject_unknown_keys(d: Mapping[str, Any], cls: type, *, context: str) -> None:
    """Trust-root strict parse (agent-harness#191 CR / F1): reject any key in `d`
    that is not one of `cls`'s own dataclass field names. Every frozen dataclass in
    this module has a `to_dict()` whose keys are exactly its field names, so this
    generic check — driven by the dataclass fields themselves, never a
    hand-maintained duplicate list that could silently drift — is the exact inverse
    of `to_dict()`: nothing can ride along in the JSON that the schema doesn't know
    about and that therefore never entered the digest."""
    known = {f.name for f in fields(cls)}
    unknown = set(d.keys()) - known
    if unknown:
        raise ProvenanceInvalid(
            f"{context}: unknown field(s) {sorted(unknown)!r} (fail-closed, strict trust-root "
            "load — an unaudited field must never ride along outside the digest)"
        )


def _load_json_fail_closed(text: str, *, max_bytes: int) -> dict[str, Any]:
    """Shared fail-closed JSON load: oversize / malformed-JSON / duplicate-key /
    surrogate-in-value all raise `ProvenanceInvalid` (never a silent pass, never
    fail-open). Unknown TOP-LEVEL/nested fields are rejected separately, by each
    `from_dict` via `_reject_unknown_keys` (this function only handles what a raw
    JSON parse itself can catch: shape, size, and duplicate keys)."""
    if not isinstance(text, str):
        raise ProvenanceInvalid("provenance payload must be a JSON text string")
    # Byte length, not char length — a payload can be ASCII-escaped (\\uXXXX) and
    # still exceed the bound in true bytes-on-disk terms; encode with
    # surrogatepass so a malicious lone-surrogate payload can't dodge the size
    # check by raising UnicodeEncodeError here instead of being sized.
    size = len(text.encode("utf-8", errors="surrogatepass"))
    if size > max_bytes:
        raise ProvenanceInvalid(f"payload exceeds max size {max_bytes} bytes (got {size})")
    try:
        data = json.loads(text, object_pairs_hook=_strict_object_pairs_hook)
    except json.JSONDecodeError as exc:
        raise ProvenanceInvalid(f"malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProvenanceInvalid("provenance payload must be a JSON object")
    _scan_for_surrogates(data)
    return data


# --------------------------------------------------------------------------- #
# Small strict-parsing helpers (fail closed on any missing/mistyped field)
# --------------------------------------------------------------------------- #


def _req(d: Mapping[str, Any], key: str) -> Any:
    if key not in d:
        raise ProvenanceInvalid(f"missing required field: {key!r}")
    return d[key]


def _req_str(d: Mapping[str, Any], key: str) -> str:
    v = _req(d, key)
    if not isinstance(v, str):
        raise ProvenanceInvalid(f"field {key!r} must be a string")
    return v


def _opt_str(d: Mapping[str, Any], key: str) -> str | None:
    v = d.get(key)
    if v is not None and not isinstance(v, str):
        raise ProvenanceInvalid(f"field {key!r} must be a string or null")
    return v


def _req_bool(d: Mapping[str, Any], key: str) -> bool:
    v = _req(d, key)
    if not isinstance(v, bool):
        raise ProvenanceInvalid(f"field {key!r} must be a boolean")
    return v


def _req_int(d: Mapping[str, Any], key: str) -> int:
    v = _req(d, key)
    if not isinstance(v, int) or isinstance(v, bool):
        raise ProvenanceInvalid(f"field {key!r} must be an integer")
    return v


def _tuple_str(d: Mapping[str, Any], key: str) -> tuple[str, ...]:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(item, str) for item in v):
        raise ProvenanceInvalid(f"field {key!r} must be a list of strings")
    return tuple(v)


def _validate_content_ref(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not _CONTENT_REF_RE.match(value):
        raise ProvenanceInvalid(
            f"field {field_name!r} must be a content-ref digest 'sha256:<64 hex>', "
            f"never inline text (metadata-only posture, fail-closed): {value!r}"
        )
    return value


# --------------------------------------------------------------------------- #
# Shared component records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class BaseBinding:
    """design §6.5 `base`."""

    ref_identity: str
    base_sha: str

    def to_dict(self) -> dict[str, Any]:
        return {"ref_identity": self.ref_identity, "base_sha": self.base_sha}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "BaseBinding":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(ref_identity=_req_str(d, "ref_identity"), base_sha=_req_str(d, "base_sha"))


@dataclass(frozen=True, kw_only=True)
class BoundaryManifestRef:
    """design §6.5 `boundary_manifest` / §5.4 (manifest pinned at the reviewed
    base revision — Lane A only carries the reference+digest; Lane C computes and
    enforces it)."""

    path: str
    source_rev: str
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "source_rev": self.source_rev, "digest": self.digest}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "BoundaryManifestRef":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            path=_req_str(d, "path"),
            source_rev=_req_str(d, "source_rev"),
            digest=_req_str(d, "digest"),
        )


@dataclass(frozen=True, kw_only=True)
class ReviewScope:
    """design §5.5 — proof a whole-patch round saw the whole patch."""

    mode: str
    reviewed_material_digest: str | None = None
    covers_patch_digest: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in _VALID_REVIEW_SCOPE_MODES:
            raise ProvenanceInvalid(f"review_scope.mode must be one of {sorted(_VALID_REVIEW_SCOPE_MODES)}, got {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reviewed_material_digest": self.reviewed_material_digest,
            "covers_patch_digest": self.covers_patch_digest,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ReviewScope":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            mode=_req_str(d, "mode"),
            reviewed_material_digest=_opt_str(d, "reviewed_material_digest"),
            covers_patch_digest=_opt_str(d, "covers_patch_digest"),
        )


@dataclass(frozen=True, kw_only=True)
class CandidateRecord:
    """design §6.5 `candidate`. `patch_digest` is Lane B's TYPE slot — `None`
    until Lane B computes it; `head_sha` is #88's `reviewed_sha`, preserved as-is
    (design §8 — never overwritten by an equivalence claim)."""

    head_sha: str
    review_scope: ReviewScope
    patch_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "head_sha": self.head_sha,
            "patch_digest": self.patch_digest,
            "review_scope": self.review_scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "CandidateRecord":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            head_sha=_req_str(d, "head_sha"),
            patch_digest=_opt_str(d, "patch_digest"),
            review_scope=ReviewScope.from_dict(_req(d, "review_scope")),
        )


@dataclass(frozen=True, kw_only=True)
class ProvenanceSeat:
    """design §6.5 `seats[]`. Field names are DELIBERATELY aligned with
    `panel_invoker.SeatOutcomeRecord` (`seat_key`, `vendor_leg`, `required`,
    `status`, `epoch`, `artifact_digest`, `evidence_digest`) — not the design
    doc's illustrative `vendor_family` spelling — so Lane D's §6.3 cross-check can
    compare provenance seats against the durable `SeatOutcomeRecord` field-for-
    field without a name-mapping layer (resolved ambiguity #1, module docstring)."""

    seat_key: str
    vendor_leg: str
    required: bool
    status: str
    epoch: int
    artifact_digest: str
    evidence_digest: str
    verdict: str | None = None
    finding_ids: tuple[str, ...] = ()
    # FAB activation piece 2 (design v6 #1): a UNIQUE per-invocation seat-INSTANCE
    # id. `seat_key` is explicitly non-unique (advisor_board.Seat.seat_key,
    # POSITIONALLY distinguished), so the gate keys completeness/verdict/finding
    # cross-checks on this instance id — never on `seat_key`. Keyword-defaulted
    # `None` so every Lane A/B/C/D fixture that predates the id stays valid.
    seat_instance_id: str | None = None

    def __post_init__(self) -> None:
        if self.verdict is not None and self.verdict not in _VALID_VERDICTS:
            raise ProvenanceInvalid(f"seat verdict must be one of {sorted(_VALID_VERDICTS)} or null, got {self.verdict!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat_key": self.seat_key,
            "vendor_leg": self.vendor_leg,
            "required": self.required,
            "verdict": self.verdict,
            "status": self.status,
            "epoch": self.epoch,
            "artifact_digest": self.artifact_digest,
            "evidence_digest": self.evidence_digest,
            "finding_ids": list(self.finding_ids),
            "seat_instance_id": self.seat_instance_id,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProvenanceSeat":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            seat_key=_req_str(d, "seat_key"),
            vendor_leg=_req_str(d, "vendor_leg"),
            required=_req_bool(d, "required"),
            verdict=_opt_str(d, "verdict"),
            status=_req_str(d, "status"),
            epoch=_req_int(d, "epoch"),
            artifact_digest=_req_str(d, "artifact_digest"),
            evidence_digest=_req_str(d, "evidence_digest"),
            finding_ids=_tuple_str(d, "finding_ids"),
            seat_instance_id=_opt_str(d, "seat_instance_id"),
        )


@dataclass(frozen=True, kw_only=True)
class Finding:
    """design §6.5 `findings[]`. METADATA-ONLY (finding 2's `serialize_seat_outcome`
    posture): `body_ref` is a content-ref DIGEST, never inline review text —
    enforced structurally by `_validate_content_ref` (rejects anything that is not
    exactly `sha256:<64 hex>`, including `None`-vs-prose confusion).

    `body_ref` is REQUIRED (agent-harness#191 CR / F5 — decision, not reflexive
    compliance): every `Finding` this design produces originates from a seat's
    review output (§6.5's schema always shows a populated `body_ref`), and there is
    no described finding type that legitimately lacks a review-body reference — an
    auto-derived/structural signal (e.g. a boundary-manifest escalation trigger)
    is carried on `Escalation.trigger`, never as a bodyless `Finding`. A finding
    with no body reference is therefore an unaudited gap, not a legitimate case:
    `body_ref=None` fails closed at construction (`ProvenanceInvalid`), tightening
    frozen interface IF-0-FAB-A-1."""

    id: str
    severity: str
    status: str
    path_scope: tuple[str, ...] = ()
    body_ref: str

    def __post_init__(self) -> None:
        if self.body_ref is None:
            raise ProvenanceInvalid(
                "Finding.body_ref is REQUIRED (fail-closed, F5 decision): every finding must "
                "reference the review-body content it originated from — a bodyless finding is an "
                "unauditable trust-root gap, not a legitimate structural/advisory case"
            )
        _validate_content_ref(self.body_ref, field_name="body_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "status": self.status,
            "path_scope": list(self.path_scope),
            "body_ref": self.body_ref,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Finding":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            id=_req_str(d, "id"),
            severity=_req_str(d, "severity"),
            status=_req_str(d, "status"),
            path_scope=_tuple_str(d, "path_scope"),
            body_ref=_req_str(d, "body_ref"),
        )


@dataclass(frozen=True, kw_only=True)
class VerificationEvidenceRef:
    """design §6.5 `verification_evidence[]` — a pointer to a #243-sealed
    `verification.json`, not a copy of its contents."""

    kind: str
    artifact_seal: str
    path_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "artifact_seal": self.artifact_seal, "path_ref": self.path_ref}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "VerificationEvidenceRef":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            kind=_req_str(d, "kind"),
            artifact_seal=_req_str(d, "artifact_seal"),
            path_ref=_req_str(d, "path_ref"),
        )


@dataclass(frozen=True, kw_only=True)
class MaterialDigest:
    """design §6.4/§6.5 `material_digests[]` — one immutable-snapshot entry."""

    ref: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MaterialDigest":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(ref=_req_str(d, "ref"), sha256=_req_str(d, "sha256"))


@dataclass(frozen=True, kw_only=True)
class Escalation:
    """design §5.4/§5.2 `escalation` — a typed field, never inferred from prose."""

    required: bool
    trigger: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"required": self.required, "trigger": self.trigger}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Escalation":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(required=_req_bool(d, "required"), trigger=_opt_str(d, "trigger"))


@dataclass(frozen=True, kw_only=True)
class EquivalenceResult:
    """design §6.5 `equivalence` — Lane B/D's TYPE slot; `None` at the
    provenance-artifact level until Lane B computes it."""

    result: str
    expected_head_digest: str | None = None
    observed_head_digest: str | None = None
    reason: str | None = None
    live_base_sha: str | None = None
    final_pr_head_sha: str | None = None

    def __post_init__(self) -> None:
        if self.result not in _VALID_EQUIVALENCE_RESULTS:
            raise ProvenanceInvalid(f"equivalence.result must be one of {sorted(_VALID_EQUIVALENCE_RESULTS)}, got {self.result!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_head_digest": self.expected_head_digest,
            "observed_head_digest": self.observed_head_digest,
            "result": self.result,
            "reason": self.reason,
            "live_base_sha": self.live_base_sha,
            "final_pr_head_sha": self.final_pr_head_sha,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EquivalenceResult":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            expected_head_digest=_opt_str(d, "expected_head_digest"),
            observed_head_digest=_opt_str(d, "observed_head_digest"),
            result=_req_str(d, "result"),
            reason=_opt_str(d, "reason"),
            live_base_sha=_opt_str(d, "live_base_sha"),
            final_pr_head_sha=_opt_str(d, "final_pr_head_sha"),
        )


# --------------------------------------------------------------------------- #
# Hash chain (design §5.1/§6.2)
# --------------------------------------------------------------------------- #


def compute_round_chain_digest(
    *,
    policy: Any,
    review_scope: Any,
    material_digests: Any,
    findings: Any,
    parent_chain_digest: str | None,
    base_binding: Any | None = None,
) -> str:
    """The frozen chain-digest primitive (design §5.1/§6.2):

        C0 = H(policy || review_scope || material_digests || findings || base_binding || None)
        Ci = H(policy_i || review_scope_i || material_digests_i || findings_i || C_{i-1})

    Realized as SHA-256 over a canonical JSON OBJECT keyed by field name (the
    unambiguous form of `||` — see module docstring resolved-ambiguity #2),
    reusing `verification_evidence`'s canonicalization (sorted keys, tight
    separators) rather than a hand-rolled encoder. `base_binding=None` selects the
    C0 (candidate-round) shape; a delta round omits it. Deterministic: identical
    inputs always produce the identical digest; ANY field change changes it.
    Fail-closed on a non-JSON-serializable component (e.g. an unexpected object
    slipped into `policy`)."""
    payload: dict[str, Any] = {
        "policy": policy,
        "review_scope": review_scope,
        "material_digests": material_digests,
        "findings": findings,
        "parent_chain_digest": parent_chain_digest,
    }
    if base_binding is not None:
        payload["base_binding"] = base_binding
    return _encode_and_digest(payload)


def _delta_findings_component(record_like: Mapping[str, Any]) -> dict[str, list[str]]:
    """The deterministic `findings_i` chain-hash component for a delta round —
    the sorted triple of finding-ID lists it carries (see module docstring
    resolved-ambiguity #3: full `Finding` records live once at the artifact's
    top level, not duplicated per round)."""
    return {
        "resolved": sorted(record_like["resolved_finding_ids"]),
        "carried_forward": sorted(record_like["carried_forward_finding_ids"]),
        "reopened": sorted(record_like["reopened_finding_ids"]),
    }


@dataclass(frozen=True, kw_only=True)
class DeltaReviewRecord:
    """design §5.1 `DeltaReviewRecord` (`fab.delta-review`). Carries its own
    `policy`/`review_scope`/`material_digests` (per resolved-ambiguity #3) so its
    `chain_digest` is independently recomputable without consulting sibling
    rounds for anything but `parent_chain_digest`.

    `delta_round_seats` (agent-harness#191 CR, Lane D finding 1): the SAME
    `delta_round_seats` `fab_delta.build_delta_round` already accepts and
    corroborates `resolved_finding_ids`/`reopened_finding_ids` against at
    construction time (`require_seat_corroboration`) — previously discarded
    after that one-time check, never persisted. Lane D's gate needs this round's
    OWN seats to re-authenticate them against the durable `SeatOutcomeRecord`
    ledger (§6.3) and re-run the corroboration check independently at gate
    time, so a round built any other way (e.g. loaded from JSON, never routed
    through `build_delta_round`) cannot claim `reviewed-clean` on an
    unsubstantiated or empty seat set. Deliberately NOT folded into
    `chain_digest` — mirrors the artifact-level `seats` field, which the
    design's C0/Ci hash formula (§5.1) also excludes; seat authenticity is a
    SEPARATE, durable-ledger-backed check (§6.3), not a hash-chain concern.
    Still tamper-evident: it rides inside `DeltaReviewRecord.to_dict()`, which
    is folded into the WHOLE-ARTIFACT `artifact_digest` self-check
    (`ReviewProvenanceArtifact.from_dict`) whenever this record is loaded as
    part of a `delta_chain`."""

    schema: str = SCHEMA_DELTA_REVIEW
    policy: Any = None
    review_scope: ReviewScope
    material_digests: tuple[MaterialDigest, ...] = ()
    parent_digest: str | None
    parent_chain_digest: str | None
    chain_digest: str
    delta_head_sha: str
    delta_changed_paths: tuple[str, ...] = ()
    delta_commits: tuple[str, ...] = ()
    resolved_finding_ids: tuple[str, ...] = ()
    carried_forward_finding_ids: tuple[str, ...] = ()
    reopened_finding_ids: tuple[str, ...] = ()
    resulting_head_digest: str | None = None
    status: str
    escalation: Escalation
    delta_round_seats: tuple[ProvenanceSeat, ...] = ()

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_DELTA_REVIEW:
            raise ProvenanceInvalid(f"delta record schema must be {SCHEMA_DELTA_REVIEW!r}, got {self.schema!r}")
        if self.status not in _VALID_DELTA_STATUSES:
            raise ProvenanceInvalid(f"delta status must be one of {sorted(_VALID_DELTA_STATUSES)}, got {self.status!r}")

    @classmethod
    def build(
        cls,
        *,
        policy: Any,
        review_scope: ReviewScope,
        material_digests: Sequence[MaterialDigest],
        parent_digest: str | None,
        parent_chain_digest: str | None,
        delta_head_sha: str,
        delta_changed_paths: Sequence[str],
        delta_commits: Sequence[str],
        resolved_finding_ids: Sequence[str],
        carried_forward_finding_ids: Sequence[str],
        reopened_finding_ids: Sequence[str],
        resulting_head_digest: str | None,
        status: str,
        escalation: Escalation,
        delta_round_seats: Sequence[ProvenanceSeat] = (),
    ) -> "DeltaReviewRecord":
        """Construct a delta round, computing `chain_digest` from the other
        fields (never accepted as caller-supplied — that would let a caller
        assert an unearned digest)."""
        material_tuple = tuple(material_digests)
        findings_component = _delta_findings_component(
            {
                "resolved_finding_ids": resolved_finding_ids,
                "carried_forward_finding_ids": carried_forward_finding_ids,
                "reopened_finding_ids": reopened_finding_ids,
            }
        )
        chain_digest = compute_round_chain_digest(
            policy=policy,
            review_scope=review_scope.to_dict(),
            material_digests=[m.to_dict() for m in material_tuple],
            findings=findings_component,
            parent_chain_digest=parent_chain_digest,
        )
        return cls(
            policy=policy,
            review_scope=review_scope,
            material_digests=material_tuple,
            parent_digest=parent_digest,
            parent_chain_digest=parent_chain_digest,
            chain_digest=chain_digest,
            delta_head_sha=delta_head_sha,
            delta_changed_paths=tuple(delta_changed_paths),
            delta_commits=tuple(delta_commits),
            resolved_finding_ids=tuple(resolved_finding_ids),
            carried_forward_finding_ids=tuple(carried_forward_finding_ids),
            reopened_finding_ids=tuple(reopened_finding_ids),
            resulting_head_digest=resulting_head_digest,
            status=status,
            escalation=escalation,
            delta_round_seats=tuple(delta_round_seats),
        )

    def recompute_chain_digest(self) -> str:
        """Recompute `chain_digest` from this record's OWN fields (does not
        trust the stored value) — the building block `verify_chain` uses."""
        findings_component = _delta_findings_component(
            {
                "resolved_finding_ids": self.resolved_finding_ids,
                "carried_forward_finding_ids": self.carried_forward_finding_ids,
                "reopened_finding_ids": self.reopened_finding_ids,
            }
        )
        return compute_round_chain_digest(
            policy=self.policy,
            review_scope=self.review_scope.to_dict(),
            material_digests=[m.to_dict() for m in self.material_digests],
            findings=findings_component,
            parent_chain_digest=self.parent_chain_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "policy": self.policy,
            "review_scope": self.review_scope.to_dict(),
            "material_digests": [m.to_dict() for m in self.material_digests],
            "parent_digest": self.parent_digest,
            "parent_chain_digest": self.parent_chain_digest,
            "chain_digest": self.chain_digest,
            "delta_head_sha": self.delta_head_sha,
            "delta_changed_paths": list(self.delta_changed_paths),
            "delta_commits": list(self.delta_commits),
            "resolved_finding_ids": list(self.resolved_finding_ids),
            "carried_forward_finding_ids": list(self.carried_forward_finding_ids),
            "reopened_finding_ids": list(self.reopened_finding_ids),
            "resulting_head_digest": self.resulting_head_digest,
            "status": self.status,
            "escalation": self.escalation.to_dict(),
            "delta_round_seats": [s.to_dict() for s in self.delta_round_seats],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DeltaReviewRecord":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            schema=_req_str(d, "schema"),
            policy=d.get("policy"),
            review_scope=ReviewScope.from_dict(_req(d, "review_scope")),
            material_digests=tuple(MaterialDigest.from_dict(m) for m in d.get("material_digests", [])),
            parent_digest=_opt_str(d, "parent_digest"),
            parent_chain_digest=_opt_str(d, "parent_chain_digest"),
            chain_digest=_req_str(d, "chain_digest"),
            delta_head_sha=_req_str(d, "delta_head_sha"),
            delta_changed_paths=_tuple_str(d, "delta_changed_paths"),
            delta_commits=_tuple_str(d, "delta_commits"),
            resolved_finding_ids=_tuple_str(d, "resolved_finding_ids"),
            carried_forward_finding_ids=_tuple_str(d, "carried_forward_finding_ids"),
            reopened_finding_ids=_tuple_str(d, "reopened_finding_ids"),
            resulting_head_digest=_opt_str(d, "resulting_head_digest"),
            status=_req_str(d, "status"),
            escalation=Escalation.from_dict(_req(d, "escalation")),
            delta_round_seats=tuple(ProvenanceSeat.from_dict(s) for s in d.get("delta_round_seats", [])),
        )

    def to_json(self) -> str:
        """Deterministic, round-trippable JSON (sorted keys, tight separators)
        for a STANDALONE delta round (e.g. exchanged before being appended to
        an artifact's `delta_chain`) — deliverable item 1's `to_json()`/
        `from_json()` requirement applies to all three record types, not just
        `ReviewProvenanceArtifact`/`GateStatus`."""
        try:
            return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ProvenanceInvalid(f"json.dumps failed while serializing delta record: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> "DeltaReviewRecord":
        """The fail-closed loader for a standalone delta round: oversize /
        malformed-JSON / surrogate-in-value all raise `ProvenanceInvalid`."""
        data = _load_json_fail_closed(text, max_bytes=MAX_DELTA_REVIEW_RECORD_BYTES)
        return cls.from_dict(data)


# --------------------------------------------------------------------------- #
# ReviewProvenanceArtifact (design §6.5, `fab.review-provenance.v2`)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class ReviewProvenanceArtifact:
    schema: str = SCHEMA_REVIEW_PROVENANCE
    repo: str
    base: BaseBinding
    boundary_manifest: BoundaryManifestRef
    candidate: CandidateRecord
    seats: tuple[ProvenanceSeat, ...] = ()
    findings: tuple[Finding, ...] = ()
    verification_evidence: tuple[VerificationEvidenceRef, ...] = ()
    material_digests: tuple[MaterialDigest, ...] = ()
    delta_chain: tuple[DeltaReviewRecord, ...] = ()
    chain_digest: str
    equivalence: EquivalenceResult | None = None
    artifact_digest: str

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_REVIEW_PROVENANCE:
            raise ProvenanceInvalid(f"artifact schema must be {SCHEMA_REVIEW_PROVENANCE!r}, got {self.schema!r}")

    # -- construction -------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        repo: str,
        base: BaseBinding,
        boundary_manifest: BoundaryManifestRef,
        candidate: CandidateRecord,
        seats: Sequence[ProvenanceSeat] = (),
        findings: Sequence[Finding] = (),
        verification_evidence: Sequence[VerificationEvidenceRef] = (),
        material_digests: Sequence[MaterialDigest] = (),
        delta_chain: Sequence[DeltaReviewRecord] = (),
        equivalence: EquivalenceResult | None = None,
    ) -> "ReviewProvenanceArtifact":
        """Construct a provenance artifact, computing `chain_digest` (the C0
        candidate digest, or the final delta round's digest when `delta_chain` is
        non-empty — design §6.2: "the final chain_digest is what the gate binds
        its PASS to") and `artifact_digest` (self-excluded, #243-style) — neither
        is ever caller-supplied."""
        material_tuple = tuple(material_digests)
        findings_tuple = tuple(findings)
        delta_tuple = tuple(delta_chain)
        c0 = compute_round_chain_digest(
            policy=boundary_manifest.to_dict(),
            review_scope=candidate.review_scope.to_dict(),
            material_digests=[m.to_dict() for m in material_tuple],
            findings=[f.to_dict() for f in findings_tuple],
            parent_chain_digest=None,
            base_binding={"repo": repo, "base": base.to_dict()},
        )
        final_chain_digest = delta_tuple[-1].chain_digest if delta_tuple else c0
        instance = cls(
            repo=repo,
            base=base,
            boundary_manifest=boundary_manifest,
            candidate=candidate,
            seats=tuple(seats),
            findings=findings_tuple,
            verification_evidence=tuple(verification_evidence),
            material_digests=material_tuple,
            delta_chain=delta_tuple,
            chain_digest=final_chain_digest,
            equivalence=equivalence,
            artifact_digest="",  # placeholder; excluded from its own digest, filled below
        )
        digest = _artifact_self_digest(instance)
        return cls(
            repo=instance.repo,
            base=instance.base,
            boundary_manifest=instance.boundary_manifest,
            candidate=instance.candidate,
            seats=instance.seats,
            findings=instance.findings,
            verification_evidence=instance.verification_evidence,
            material_digests=instance.material_digests,
            delta_chain=instance.delta_chain,
            chain_digest=instance.chain_digest,
            equivalence=instance.equivalence,
            artifact_digest=digest,
        )

    def compute_c0(self) -> str:
        """Recompute the candidate-round (C0) digest from this artifact's OWN
        top-level fields — the anchor `verify_chain` checks `delta_chain[0]`
        (or, absent deltas, `chain_digest` itself) against."""
        return compute_round_chain_digest(
            policy=self.boundary_manifest.to_dict(),
            review_scope=self.candidate.review_scope.to_dict(),
            material_digests=[m.to_dict() for m in self.material_digests],
            findings=[f.to_dict() for f in self.findings],
            parent_chain_digest=None,
            base_binding={"repo": self.repo, "base": self.base.to_dict()},
        )

    # -- serialization --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "base": self.base.to_dict(),
            "boundary_manifest": self.boundary_manifest.to_dict(),
            "candidate": self.candidate.to_dict(),
            "seats": [s.to_dict() for s in self.seats],
            "findings": [f.to_dict() for f in self.findings],
            "verification_evidence": [v.to_dict() for v in self.verification_evidence],
            "material_digests": [m.to_dict() for m in self.material_digests],
            "delta_chain": [d.to_dict() for d in self.delta_chain],
            "chain_digest": self.chain_digest,
            "equivalence": self.equivalence.to_dict() if self.equivalence is not None else None,
            "artifact_digest": self.artifact_digest,
        }

    def to_json(self) -> str:
        """Deterministic, round-trippable JSON (sorted keys, tight separators —
        same canonicalization as the digest, so a byte-for-byte re-encode of a
        loaded artifact reproduces the identical text)."""
        payload = self.to_dict()
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ProvenanceInvalid(f"json.dumps failed while serializing artifact: {exc}") from exc

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ReviewProvenanceArtifact":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        schema = _req_str(d, "schema")
        if schema != SCHEMA_REVIEW_PROVENANCE:
            raise ProvenanceInvalid(f"artifact schema must be {SCHEMA_REVIEW_PROVENANCE!r}, got {schema!r}")
        equivalence_raw = d.get("equivalence")
        instance = cls(
            schema=schema,
            repo=_req_str(d, "repo"),
            base=BaseBinding.from_dict(_req(d, "base")),
            boundary_manifest=BoundaryManifestRef.from_dict(_req(d, "boundary_manifest")),
            candidate=CandidateRecord.from_dict(_req(d, "candidate")),
            seats=tuple(ProvenanceSeat.from_dict(s) for s in d.get("seats", [])),
            findings=tuple(Finding.from_dict(f) for f in d.get("findings", [])),
            verification_evidence=tuple(
                VerificationEvidenceRef.from_dict(v) for v in d.get("verification_evidence", [])
            ),
            material_digests=tuple(MaterialDigest.from_dict(m) for m in d.get("material_digests", [])),
            delta_chain=tuple(DeltaReviewRecord.from_dict(rec) for rec in d.get("delta_chain", [])),
            chain_digest=_req_str(d, "chain_digest"),
            equivalence=EquivalenceResult.from_dict(equivalence_raw) if equivalence_raw is not None else None,
            artifact_digest=_req_str(d, "artifact_digest"),
        )
        # Integrity: the self-excluded artifact_digest must recompute exactly.
        # A trust-root load that skipped this would let a field-edited artifact
        # load "successfully" and defer detection to whichever caller happens to
        # remember to check separately — fail closed HERE instead.
        recomputed = _artifact_self_digest(instance)
        if recomputed != instance.artifact_digest:
            raise ProvenanceInvalid(
                "artifact_digest mismatch (fail-closed): the provenance artifact was edited after write "
                f"(recomputed={recomputed!r}, recorded={instance.artifact_digest!r})"
            )
        return instance

    @classmethod
    def from_json(cls, text: str) -> "ReviewProvenanceArtifact":
        """The fail-closed loader (task item 3): oversize / malformed-JSON /
        surrogate-in-value / artifact_digest-mismatch all raise
        `ProvenanceInvalid`, never a silent pass."""
        data = _load_json_fail_closed(text, max_bytes=MAX_PROVENANCE_ARTIFACT_BYTES)
        return cls.from_dict(data)


def _artifact_self_digest(artifact: ReviewProvenanceArtifact) -> str:
    """`artifact_digest` — the ONE self-excluded field (design §6/#243 posture):
    computing the digest excludes `artifact_digest` itself; editing ANY other
    field changes it."""
    return _encode_and_digest(artifact.to_dict(), exclude="artifact_digest")


# --------------------------------------------------------------------------- #
# Chain verification (design §6.2 — splice/reorder/break detection)
# --------------------------------------------------------------------------- #


def verify_chain(artifact: ReviewProvenanceArtifact) -> None:
    """Recompute EVERY `chain_digest` in `artifact` (the candidate round's
    implicit C0 plus every `delta_chain` entry's own recorded `chain_digest`) and
    check CONTIGUITY end to end:

      * `delta_chain[0].parent_chain_digest == C0` (recomputed, not trusted);
      * `delta_chain[i].parent_chain_digest == delta_chain[i-1].chain_digest`;
      * `delta_chain[i].parent_digest == delta_chain[i-1].resulting_head_digest`
        (or `== candidate.patch_digest` for `i == 0`) — REQUIRED (agent-harness#191
        CR / F2), not merely checked-when-present, whenever the PARENT side is
        recorded (Lane B populates these; Lane A only checks the LINK, not the
        underlying patch-digest math): if the prior round's patch digest is known
        but `record.parent_digest` is `None`, that is a BROKEN link, not an
        absent-therefore-skipped one — §5.1 requires DUAL-link contiguity
        (`parent_digest` AND `parent_chain_digest`), not single-link;
      * a `status = reviewed-clean` record MUST carry a non-null `parent_digest`
        unconditionally (design §5.1: "A `reviewed-clean` delta MUST carry a
        linking parent_digest") — even in the degenerate case where the prior
        patch digest itself is not yet recorded, a round claiming to be
        carry-forward-eligible clean review cannot be unlinked;
      * `artifact.chain_digest == (delta_chain[-1].chain_digest if delta_chain else C0)`.

    Raises `ChainVerificationError` (a `ProvenanceInvalid` subclass) on the FIRST
    break — a spliced fabricated round, a reordered round, an unlinked
    `reviewed-clean` round, or a broken `parent_digest`/`parent_chain_digest` link
    all fail this. Never returns a bool; a caller that wants "is it valid" should
    catch the exception — this mirrors the module's fail-closed-not-silent
    posture."""
    c0 = artifact.compute_c0()
    prior_chain_digest = c0
    prior_patch_digest = artifact.candidate.patch_digest
    for index, record in enumerate(artifact.delta_chain):
        recomputed = record.recompute_chain_digest()
        if recomputed != record.chain_digest:
            raise ChainVerificationError(
                f"delta_chain[{index}].chain_digest does not recompute "
                f"(recorded={record.chain_digest!r}, recomputed={recomputed!r}) — fabricated/tampered round"
            )
        if record.parent_chain_digest != prior_chain_digest:
            raise ChainVerificationError(
                f"delta_chain[{index}].parent_chain_digest broken "
                f"(expected={prior_chain_digest!r}, got={record.parent_chain_digest!r}) — reordered/spliced round"
            )
        if record.status == DELTA_STATUS_REVIEWED_CLEAN and record.parent_digest is None:
            raise ChainVerificationError(
                f"delta_chain[{index}] is status={DELTA_STATUS_REVIEWED_CLEAN!r} but carries no "
                "parent_digest (fail-closed, §5.1 dual-link contiguity: a reviewed-clean delta MUST "
                "carry a linking parent_digest)"
            )
        if prior_patch_digest is not None:
            if record.parent_digest is None:
                raise ChainVerificationError(
                    f"delta_chain[{index}].parent_digest is null but the prior round's patch digest "
                    f"is recorded ({prior_patch_digest!r}) — fail-closed, §5.1 dual-link contiguity "
                    "requires parent_digest whenever the parent side is known, not only when present"
                )
            if record.parent_digest != prior_patch_digest:
                raise ChainVerificationError(
                    f"delta_chain[{index}].parent_digest broken "
                    f"(expected={prior_patch_digest!r}, got={record.parent_digest!r}) — reordered/spliced round"
                )
        prior_chain_digest = record.chain_digest
        prior_patch_digest = record.resulting_head_digest if record.resulting_head_digest is not None else prior_patch_digest
    expected_final = artifact.delta_chain[-1].chain_digest if artifact.delta_chain else c0
    if artifact.chain_digest != expected_final:
        raise ChainVerificationError(
            f"artifact.chain_digest does not match the final round "
            f"(expected={expected_final!r}, got={artifact.chain_digest!r})"
        )


# --------------------------------------------------------------------------- #
# GateStatus (design §8, `fab.gate-status.v2`)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class GateDeltaEntry:
    delta_head_sha: str
    delta_digest: str | None = None
    status: str

    def __post_init__(self) -> None:
        if self.status not in _VALID_DELTA_STATUSES:
            raise ProvenanceInvalid(f"gate delta status must be one of {sorted(_VALID_DELTA_STATUSES)}, got {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"delta_head_sha": self.delta_head_sha, "delta_digest": self.delta_digest, "status": self.status}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "GateDeltaEntry":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            delta_head_sha=_req_str(d, "delta_head_sha"),
            delta_digest=_opt_str(d, "delta_digest"),
            status=_req_str(d, "status"),
        )


@dataclass(frozen=True, kw_only=True)
class EquivalenceVerified:
    """design §8 `equivalence_verified` — the SEPARATE, independently-verified
    proof distinct from #88's `reviewed_sha` (finding 5). Lane B/D populate the
    digests/reason; Lane A defines the slot."""

    result: str
    candidate_head_sha: str | None = None
    delta_head_shas: tuple[str, ...] = ()
    expected_head_digest: str | None = None
    observed_head_digest: str | None = None
    base_sha: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.result not in _VALID_EQUIVALENCE_RESULTS:
            raise ProvenanceInvalid(f"equivalence_verified.result must be one of {sorted(_VALID_EQUIVALENCE_RESULTS)}, got {self.result!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "candidate_head_sha": self.candidate_head_sha,
            "delta_head_shas": list(self.delta_head_shas),
            "expected_head_digest": self.expected_head_digest,
            "observed_head_digest": self.observed_head_digest,
            "base_sha": self.base_sha,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EquivalenceVerified":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        return cls(
            result=_req_str(d, "result"),
            candidate_head_sha=_opt_str(d, "candidate_head_sha"),
            delta_head_shas=_tuple_str(d, "delta_head_shas"),
            expected_head_digest=_opt_str(d, "expected_head_digest"),
            observed_head_digest=_opt_str(d, "observed_head_digest"),
            base_sha=_opt_str(d, "base_sha"),
            reason=_opt_str(d, "reason"),
        )


@dataclass(frozen=True, kw_only=True)
class GateStatus:
    schema: str = SCHEMA_GATE_STATUS
    reviewed_sha: str
    prior_review_digest: str | None = None
    chain_digest: str | None = None
    deltas: tuple[GateDeltaEntry, ...] = ()
    final_pr_head_sha: str | None = None
    equivalence_verified: EquivalenceVerified | None = None
    carried_forward_findings: tuple[str, ...] = ()
    re_reviewed_findings: tuple[str, ...] = ()
    escalation: Escalation = field(default_factory=lambda: Escalation(required=False, trigger=None))
    waiver: str | None = None
    status: str = GATE_STATUS_BLOCK

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_GATE_STATUS:
            raise ProvenanceInvalid(f"gate-status schema must be {SCHEMA_GATE_STATUS!r}, got {self.schema!r}")
        if self.status not in _VALID_GATE_STATUSES:
            raise ProvenanceInvalid(f"gate status must be one of {sorted(_VALID_GATE_STATUSES)}, got {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "reviewed_sha": self.reviewed_sha,
            "prior_review_digest": self.prior_review_digest,
            "chain_digest": self.chain_digest,
            "deltas": [d.to_dict() for d in self.deltas],
            "final_pr_head_sha": self.final_pr_head_sha,
            "equivalence_verified": self.equivalence_verified.to_dict() if self.equivalence_verified is not None else None,
            "carried_forward_findings": list(self.carried_forward_findings),
            "re_reviewed_findings": list(self.re_reviewed_findings),
            "escalation": self.escalation.to_dict(),
            "waiver": self.waiver,
            "status": self.status,
        }

    def to_json(self) -> str:
        try:
            return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ProvenanceInvalid(f"json.dumps failed while serializing gate status: {exc}") from exc

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "GateStatus":
        _reject_unknown_keys(d, cls, context=f"{cls.__name__}.from_dict")
        schema = _req_str(d, "schema")
        if schema != SCHEMA_GATE_STATUS:
            raise ProvenanceInvalid(f"gate-status schema must be {SCHEMA_GATE_STATUS!r}, got {schema!r}")
        equivalence_raw = d.get("equivalence_verified")
        return cls(
            schema=schema,
            reviewed_sha=_req_str(d, "reviewed_sha"),
            prior_review_digest=_opt_str(d, "prior_review_digest"),
            chain_digest=_opt_str(d, "chain_digest"),
            deltas=tuple(GateDeltaEntry.from_dict(x) for x in d.get("deltas", [])),
            final_pr_head_sha=_opt_str(d, "final_pr_head_sha"),
            equivalence_verified=EquivalenceVerified.from_dict(equivalence_raw) if equivalence_raw is not None else None,
            carried_forward_findings=_tuple_str(d, "carried_forward_findings"),
            re_reviewed_findings=_tuple_str(d, "re_reviewed_findings"),
            escalation=Escalation.from_dict(_req(d, "escalation")),
            waiver=_opt_str(d, "waiver"),
            status=_req_str(d, "status"),
        )

    @classmethod
    def from_json(cls, text: str) -> "GateStatus":
        data = _load_json_fail_closed(text, max_bytes=MAX_GATE_STATUS_BYTES)
        return cls.from_dict(data)


# --------------------------------------------------------------------------- #
# Trust root (design §6.1) — harness-only-written, run-store-keyed
# --------------------------------------------------------------------------- #


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ProvenanceInvalid(f"invalid run id (fail-closed): {run_id!r}")
    return run_id


def provenance_dir_for_run(repo: Path, run_id: str) -> Path:
    """The run-store directory for `run_id`'s FAB provenance material — the same
    durable `.phase-loop/runs/<run_id>/` root `observability.run_artifacts` and
    `SeatOutcomeRecord` persistence already use (module docstring resolved-
    ambiguity #5). Fail closed on a malformed run id or a resolved path that
    would escape the run store (defense in depth on top of the charset guard)."""
    _validate_run_id(run_id)
    runs_root = phase_loop_runs_dir(Path(repo))
    candidate = runs_root / run_id
    resolved = candidate.resolve()
    runs_root_resolved = runs_root.resolve()
    if resolved != runs_root_resolved and runs_root_resolved not in resolved.parents:
        raise ProvenanceInvalid(f"run id resolves outside the run store (fail-closed): {run_id!r}")
    return candidate


def provenance_path_for_run(repo: Path, run_id: str) -> Path:
    return provenance_dir_for_run(repo, run_id) / PROVENANCE_FILENAME


def atomic_write_text_durable(path: Path, text: str) -> None:
    """Atomically write `text` to `path` and make it DURABLE before returning
    (agent-harness#191 CR round 7 / codex#4): fsync the temp file's contents AND
    the parent directory entry before/after the rename, so the FAB ordering
    invariant "provenance + round records are on stable storage BEFORE the branch
    ref advances" holds across a host/kernel crash — a crash must never preserve
    the ref update while losing the authoritative gate record. Mirrors the
    seat-outcome ledger's existing fsync posture (`fab_gate.append_seat_outcome`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)  # atomic within the run store
    dir_fd = os.open(str(path.parent), getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_provenance(repo: Path, run_id: str, artifact: ReviewProvenanceArtifact) -> Path:
    """Intended-harness-only write path (design §6.1): persists `artifact` to the
    durable run store keyed by `run_id`. This is the ONLY function in this module
    that writes to the authoritative run-store path — but this is a CODING
    CONVENTION Lane A follows, not an enforced authorship guarantee: nothing in
    this module (or process boundary) stops a co-resident process with filesystem
    write access to the run store from also writing to this exact path. Proving
    "the harness, and only the harness, wrote this" is NOT this function's job —
    see the trust-model note at `reject_client_supplied_provenance` and design
    §6.1a. What this function DOES guarantee: it never accepts an arbitrary
    caller-chosen destination — the write always targets the run-store path for
    `run_id`, never a PR-branch checkout or a client-supplied path."""
    path = provenance_path_for_run(repo, run_id)
    # Durable (fsync'd) write — provenance must be on stable storage BEFORE the
    # branch ref advances (CR round 7 / codex#4).
    atomic_write_text_durable(path, artifact.to_json())
    return path


def read_provenance(repo: Path, run_id: str) -> ReviewProvenanceArtifact:
    """The gate's provenance READ path (design §6.1): reads ONLY from the run
    store, keyed by `run_id` — there is no parameter through which a caller can
    substitute a different (e.g. client-supplied / PR-branch) blob as the source
    of truth. Raises `ProvenanceNotFound` (a `ProvenanceInvalid` subclass) when
    the run store has nothing for `run_id` — it never falls back to any other
    candidate location. This proves ONLY that the bytes came from the expected
    LOCATION and pass integrity (`artifact_digest` recompute in `from_json`); it
    does NOT prove the bytes were authored by the harness — see the trust-model
    note at `reject_client_supplied_provenance` and design §6.1a. Whether `run_id`
    itself is trustworthy (resolved from trusted review-run output vs. accepted
    from PR-controlled input) is the CALLER's responsibility — Lane D's."""
    path = provenance_path_for_run(repo, run_id)
    # #243 precedent (verification_evidence.load_verification_artifact): stat
    # the oversize bound BEFORE reading, so a tampered/runaway file is rejected
    # without ever being pulled fully into memory. A missing-file stat OSError
    # falls through to the read_text below, which raises the canonical
    # ProvenanceNotFound.
    try:
        if path.stat().st_size > MAX_PROVENANCE_ARTIFACT_BYTES:
            raise ProvenanceInvalid(
                f"run-store provenance for run_id={run_id!r} exceeds max size "
                f"{MAX_PROVENANCE_ARTIFACT_BYTES} bytes (fail-closed, not read)"
            )
    except OSError:
        pass
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProvenanceNotFound(f"no run-store provenance for run_id={run_id!r} (fail-closed): {exc}") from exc
    return ReviewProvenanceArtifact.from_json(text)


def _is_git_tracked(repo: Path, path: Path) -> bool:
    """Boundary probe for `reject_client_supplied_provenance` (agent-harness#191
    CR / F3).

    THREAT-MODEL BOUNDARY (agent-harness#191 CR rounds 3-4, stated once so it is
    answered-by-design, not re-litigated): `git` is TRUSTED CI plumbing. The
    attacker in scope controls repo CONTENTS (a PR branch can COMMIT a file at the
    run-store path — that is the actual exploit this probe defeats: a tracked file
    at a fixed pathspec makes `git ls-files -- <path>` print that path -> non-empty
    -> tracked -> REJECT, verified by `test_path_at_authoritative_location_but_git
    _tracked_is_rejected`). The attacker does NOT control the BYTES of the trusted
    CI git binary's stdout. So we parse git's documented output (`.strip()` +
    exact "true"/"false"/empty comparisons) rather than byte-exact stdout: a
    hypothetical `" false\n"` / whitespace-only `ls-files` output for a tracked
    file is not reachable within this boundary (it would require compromising the
    trusted git binary, at which point it could emit "false"/"" directly and the
    whole trust model has already collapsed). Byte-exact stdout parsing of trusted
    git output is deliberately NOT done — it would trade a non-reachable fail-open
    for a reachable fail-CLOSED regression on legitimate git version/platform
    whitespace variance. Residual (parser completeness on a trusted input) tracked
    separately, non-blocking.

    NOT the Lane-B patch-digest/equivalence machinery (no diffing, no
    blob hashing, no merge-base math); a single yes/no question: "does git already
    track a file at this exact path in `repo`'s working tree". A tracked path is
    one a PR branch COMMIT can place content at — `.phase-loop/` is excluded from
    git only via the local, per-clone, non-committed `.git/info/exclude`
    (`runtime_paths.EXCLUDE_ENTRIES`), never a committed `.gitignore`, and at least
    one file (`'.phase-loop/handoffs/claude-plan-detailed.md'`) is already tracked
    under it in this very repo — proof the directory is not a reliable boundary by
    itself.

    Fail behavior, stated explicitly because it differs by cause — the polarity
    is: uncertainty REJECTS, only a DEFINITIVE, successful "not tracked" result
    is treated as safe (agent-harness#191 CR round 2 / fail-open fix — a prior
    version of this function inverted this and returned `False` — safe — on
    git-probe ERRORS, which let a hung/erroring `git` subprocess or a fatal
    git failure smuggle a client-supplied provenance blob past the trust root):
      * `repo` is not a git working tree at all -> returns `False` (this guard
        does not apply; the tracked-path spoof only exists inside a real git
        checkout, and `reject_client_supplied_provenance`'s path-equality check
        still holds regardless — untested repos, like this module's own
        tempdir-based tests, are not silently exempted from the OTHER check,
        only from this one). This is recognized ONLY by git's own deterministic,
        well-known `rev-parse` failure signature for "no repository here"
        (`returncode == 128` and `"not a git repository"` in stderr) — a CLEAN,
        reproducible negative answer, not an ambiguous one;
      * ANY OTHER git-probe failure — timeout, `OSError` (missing/unrunnable
        `git`), an unexpected nonzero return code (including other fatal errors,
        e.g. corruption, permissions, locked refs — NOT the specific "not a git
        repository" signature above), or output that isn't the exact expected
        shape — is undetermined tracked-status and returns `True` (fail CLOSED:
        never a silent pass). This applies identically to BOTH the
        `rev-parse --is-inside-work-tree` probe and the `ls-files` probe below;
      * the `ls-files` probe never uses `--error-unmatch` (whose nonzero exit
        conflates "not tracked" with real git errors); it uses plain
        `git ls-files -- <path>`, which — on a clean `returncode == 0` — prints
        the path iff it is tracked and prints NOTHING iff it is not. Only that
        clean rc==0-and-empty-output combination is treated as definitively
        untracked/safe; a nonzero return code (incl. fatal rc 128) is an
        ambiguous git failure and fails CLOSED, same as above."""
    repo = Path(repo)
    try:
        probe = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Ambiguous: could be a hung/broken `git` inside a REAL work tree.
        # Fail CLOSED — never silently treat this as "not a repo".
        return True
    if probe.returncode != 0:
        stderr = (probe.stderr or "").strip()
        if probe.returncode == 128 and "not a git repository" in stderr:
            # Git's own deterministic, reproducible signal that `repo` is
            # genuinely not a git working tree at all — the guard does not
            # apply (see docstring). A CLEAN negative, not an error.
            return False
        # Any OTHER nonzero exit is an ambiguous git failure that could be
        # occurring INSIDE a real working tree (corruption, permissions,
        # locked refs, ...). Fail CLOSED — never treat as "not a repo".
        return True
    stdout = probe.stdout.strip()
    if stdout != "true":
        # Only git's EXACT, well-formed negative ("false", e.g. run from inside a
        # bare `.git` dir) is a definitive not-inside-work-tree answer where the
        # guard genuinely does not apply. Any OTHER rc==0 output — empty,
        # malformed, or unexpected — is NOT a definitive negative and must fail
        # CLOSED (treat as tracked/unsafe), never be read as "safely untracked"
        # (agent-harness#191 CR round 3: malformed-but-rc0 rev-parse output must
        # not smuggle a client blob past the trust root).
        if stdout == "false":
            return False
        return True
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        rel = path
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--", str(rel)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Ambiguous: fail CLOSED.
        return True
    if result.returncode != 0:
        # Any git-level failure here (incl. fatal rc 128) is ambiguous, not a
        # definitive "not tracked" answer. Fail CLOSED.
        return True
    # A clean, successful rc==0 result: plain `ls-files` (no --error-unmatch)
    # prints the path iff it is tracked, nothing iff it is not — definitive,
    # not merely "no error was raised".
    return result.stdout.strip() != ""


def reject_client_supplied_provenance(candidate_path: Path, repo: Path, run_id: str) -> None:
    """Trust-root assertion helper (agent-harness#191 CR / F3 — rewritten to stop
    over-claiming). PATH LOCATION ALONE DOES NOT PROVE HARNESS AUTHORSHIP: the run
    store lives inside the repo tree at `.phase-loop/runs/`, and that directory is
    excluded from git only via the LOCAL, per-clone, non-committed
    `.git/info/exclude` — never a committed `.gitignore` (a file is already
    tracked under `.phase-loop/` in this repo, proving the boundary is not
    robust). A prior version of this helper claimed path-equality alone proved
    authorship; that claim was false and has been removed.

    What this helper actually enforces — the narrower, honest invariant Lane A
    CAN check locally, purely from the filesystem/git state available here:
      1. `candidate_path` resolves to EXACTLY the run-store path
         `provenance_path_for_run(repo, run_id)` for the given `run_id`; AND
      2. that path is NOT itself a path git already tracks in `repo` (see
         `_is_git_tracked`) — so a PR branch cannot spoof the run-store artifact
         by literally committing a file at that exact location.

    This is location-narrowing, not authorship-proving. It does NOT (and, from
    Lane A alone, cannot) prove the bytes were produced by a trusted harness
    process rather than some OTHER co-resident process with write access to the
    run store — that residual is consciously deferred to breakglass (same class
    as Consiliency/agent-harness#273). Full authorship enforcement requires the
    gate to read provenance from TRUSTED run-state with `run_id` resolved from a
    trusted source (never PR-controlled input) — that is a Lane D requirement
    (design §6.1a / §9) this storage layer enables but does not itself perform."""
    authoritative = provenance_path_for_run(repo, run_id).resolve()
    candidate_resolved = Path(candidate_path).resolve()
    if candidate_resolved != authoritative:
        raise ProvenanceInvalid(
            f"refusing provenance at {candidate_path} (fail-closed): does not resolve to the "
            f"run-store path {authoritative} for run_id={run_id!r}"
        )
    if _is_git_tracked(repo, candidate_resolved):
        raise ProvenanceInvalid(
            f"refusing provenance at {candidate_resolved} (fail-closed): this path is GIT-TRACKED "
            f"in {repo} — a PR branch commit can place content at this exact path, so run-store "
            "LOCATION alone cannot distinguish harness-written bytes from PR-controlled bytes here "
            "(design §6.1a); this helper does not claim to prove authorship, only that the path is "
            "neither a caller-chosen location nor a git-committable one"
        )


# --------------------------------------------------------------------------- #
# Immutable review material (design §6.4/T14)
# --------------------------------------------------------------------------- #


def _stream_sha256(path: Path) -> str:
    """SHA-256 of `path`'s bytes, streamed in 1 MiB chunks (mirrors the #114
    pattern already used for `context_refs` hashing in
    `panel_invoker._context_ref_entry`, panel_invoker.py:652-658 — same chunk
    size, same never-buffer-whole-file posture; a standalone re-implementation
    because that function is coupled to manifest-string rendering + soft-warn
    semantics that don't apply here, but the streaming convention is preserved
    byte-for-byte)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_MATERIAL_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_material(repo: Path, run_id: str, context_refs: Sequence[str]) -> tuple[MaterialDigest, ...]:
    """design §6.4: snapshot the referenced `context_refs` BYTES into the run
    store (an immutable copy, distinct from the mutable original) and record a
    SHA-256 `MaterialDigest` per ref. A missing/unreadable ref fails closed
    (`ProvenanceInvalid`) — never a silent-empty snapshot (mirrors the
    `context_refs` fail-closed posture in `panel_invoker._context_ref_entry`)."""
    snapshot_dir = provenance_dir_for_run(repo, run_id) / MATERIAL_SNAPSHOT_DIRNAME
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    digests: list[MaterialDigest] = []
    for ref in context_refs:
        source = Path(ref)
        if not source.is_file():
            raise ProvenanceInvalid(f"context_ref does not exist or is not a file (fail-closed, not silent-empty): {ref}")
        try:
            digest = _stream_sha256(source)
        except OSError as exc:
            raise ProvenanceInvalid(f"context_ref is not readable (fail-closed, not silent-empty): {ref} ({exc})") from exc
        dest = snapshot_dir / f"{digest}{source.suffix}"
        if not dest.exists():
            shutil.copyfile(source, dest)
        digests.append(MaterialDigest(ref=str(source.resolve()), sha256=digest))
    return tuple(digests)


def aggregate_material_digest(material_digests: Sequence[MaterialDigest]) -> str:
    """The canonical aggregation of per-ref `MaterialDigest` entries into ONE
    reviewed-material digest — design §5.5/§6.4's `review_scope.reviewed_material_digest`
    (agent-harness#191 CR / F4: this function is the frozen definition of "however
    `reviewed_material_digest` is meant to be computed", since neither v1 nor v2 of
    the design spelled it out concretely). Deterministic regardless of input order:
    the `(ref, sha256)` pairs are SORTED before encoding, so appending refs in a
    different order never changes the result. Hashed via the SAME `_encode_and_digest`
    helper every other non-path-bearing digest in this module uses — ONE canonical
    encoding, not a second hand-rolled one (finding 3's "two canonical encodings"
    lesson). A caller populating `review_scope.reviewed_material_digest` (Lane C/D,
    when constructing a review round from `snapshot_material`'s output) MUST use
    this same function so both sides of `reverify_material`'s equality check are
    computed identically."""
    ordered = sorted(
        ({"ref": m.ref, "sha256": m.sha256} for m in material_digests),
        key=lambda entry: (entry["ref"], entry["sha256"]),
    )
    return _encode_and_digest({"material_digests": ordered})


def reverify_material(
    repo: Path,
    run_id: str,
    material_digests: Sequence[MaterialDigest],
    *,
    expected_reviewed_material_digest: str,
) -> None:
    """design §6.4/T14 gate-time re-verification. For EACH recorded
    `MaterialDigest`:

      1. re-hash the immutable SNAPSHOT copy in the run store (the primary
         authority — proves the bytes the seats actually saw haven't been
         corrupted since review time) — mismatch/missing -> fail closed;
      2. re-hash the LIVE `ref` path as a drift check — a post-review edit of
         the underlying (mutable) original is thereby DETECTED rather than
         silently tolerated (module docstring resolved-ambiguity #4).

    Then (agent-harness#191 CR / F4 — the binding step v1 of this function
    omitted): the per-ref digests are aggregated via `aggregate_material_digest`
    and REQUIRED to equal `expected_reviewed_material_digest` — the artifact's
    claimed `review_scope.reviewed_material_digest`. `expected_reviewed_material_digest`
    is a REQUIRED keyword-only argument, deliberately NOT optional: an
    optional/skippable check would recreate the exact fail-open this finding
    describes (a snapshot whose per-ref digests are internally stable but whose
    AGGREGATE does not match what the artifact claims the seats reviewed would
    silently pass). Raises `ProvenanceInvalid` on the first mismatch: per-ref
    snapshot, per-ref live-drift, or the aggregate binding."""
    snapshot_dir = provenance_dir_for_run(repo, run_id) / MATERIAL_SNAPSHOT_DIRNAME
    for entry in material_digests:
        candidates = sorted(snapshot_dir.glob(f"{entry.sha256}*"))
        if not candidates:
            raise ProvenanceInvalid(f"material snapshot missing for {entry.ref} (fail-closed): expected digest {entry.sha256}")
        snapshot_digest = _stream_sha256(candidates[0])
        if snapshot_digest != entry.sha256:
            raise ProvenanceInvalid(
                f"material snapshot digest mismatch for {entry.ref} (fail-closed): "
                f"recorded={entry.sha256!r} recomputed={snapshot_digest!r}"
            )
        live_path = Path(entry.ref)
        if not live_path.is_file():
            raise ProvenanceInvalid(f"material live ref no longer exists (fail-closed, edit detected): {entry.ref}")
        try:
            live_digest = _stream_sha256(live_path)
        except OSError as exc:
            raise ProvenanceInvalid(f"material live ref not readable (fail-closed): {entry.ref} ({exc})") from exc
        if live_digest != entry.sha256:
            raise ProvenanceInvalid(
                f"material live ref was edited after snapshot (fail-closed, edit detected): {entry.ref} "
                f"(recorded={entry.sha256!r} live={live_digest!r})"
            )
    aggregate = aggregate_material_digest(material_digests)
    if aggregate != expected_reviewed_material_digest:
        raise ProvenanceInvalid(
            "reviewed-material aggregate mismatch (fail-closed, design §6.4 binding — agent-harness#191 "
            f"CR / F4): recomputed aggregate over {len(material_digests)} ref(s) = {aggregate!r}, but the "
            f"artifact's claimed review_scope.reviewed_material_digest = {expected_reviewed_material_digest!r} "
            "— the material that was reverified does not match what the seats claim to have reviewed"
        )
