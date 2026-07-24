"""FAB (Consiliency/agent-harness#191) Lane C — delta-chain binding,
clean-finding carry-forward, boundary-manifest escalation, and `review_scope`
enforcement.

Ground: `plans/design-fab-191-delta-review.md` (v2, panel-reviewed) §5 (delta
binding + carry-forward + escalation + review_scope), threat rows T4/T5/T15.
Builds on Lane A (`fab_provenance.py` — frozen schemas, hash chain,
`DeltaReviewRecord`, `Escalation`, `ReviewScope`, `Finding`,
`DELTA_STATUS_*`) and Lane B (`fab_canonical.py` — `patch_digest`,
`enumerate_changed_paths`, `read_file_at_revision`, hostile-git discipline).
This module implements ONLY Lane C (design §9): delta-chain contiguity/
binding validation, the clean-finding carry-forward decision, the
`.advisor-board/boundaries.toml` boundary manifest + escalation decision, and
`review_scope` enforcement for boundary-escalated rounds. It deliberately does
NOT build:

  * the `fab.gate-status.v2` OUTPUT composition or `verdict_binds_to_equivalent`
    (design §8 — Lane D);
  * wiring into `governed_premerge`/closeout (Lane D);
  * the §6.3 `SeatOutcomeRecord` authenticity cross-check (Lane D) — this
    module's seat-corroboration check (§5.3/T4, below) is narrower: it only
    asks "does SOME seat record in the delta round's own submitted set carry
    a terminal verdict referencing this finding id", never "is that seat
    record itself authentic against the durable `SeatOutcomeRecord` store";
  * the §4.4 promotion re-gate (Lane D).

PRE-STATED TRUST BOUNDARY (mirrors Lane A §6.1a / Lane B's module docstring /
agent-harness#276, evaluated against — not re-opened, per the Lane C task
brief): `git` is TRUSTED CI plumbing. The attacker controls repo CONTENTS —
including the boundary manifest AT THE DELTA HEAD, and any `Finding.path_scope`
/ `status` values a client might supply. Consequences this module's design
follows directly from that split:

  * the manifest that decides escalation is read AT THE REVIEWED BASE
    revision via `fab_canonical.read_file_at_revision(repo, base_sha, path)`
    (never the delta head — a delta touching the manifest path itself is
    instead FORCED into whole-patch escalation, §5.4 below) and its digest is
    folded into `chain_digest` by threading a `BoundaryManifestRef` through as
    the `policy` component `DeltaReviewRecord.build`/`compute_round_chain_digest`
    already hash (Lane A resolved-ambiguity #3) — so a delta cannot weaken the
    manifest and then be judged under the weakened rules: the rules in force
    are the ones recorded at the reviewed base;
  * carry-forward reuses the broker's OWN disjointness test
    (`convergence.broker.credsep.GitHubBrokerAdapter._covered_by_owned`,
    credsep.py:190) rather than re-implementing a path matcher — goal-id-inc2
    "reuse, don't re-implement" lesson;
  * a `resolved_finding_ids` CLAIM is never treated as a resolution by
    itself — it is REJECTED unless corroborated by an actual delta-round seat
    verdict record (T4);
  * this module never asks git to answer a question about the trusted
    binary's own stdout BYTES being forged — every hostile-git hardening it
    relies on (`--no-replace-objects`, `GIT_NO_REPLACE_OBJECTS=1`, `rc==0`-only
    acceptance) already lives in `fab_canonical.py` and is reused via
    `patch_digest`/`enumerate_changed_paths`/`read_file_at_revision`, never
    re-implemented here.

FROZEN INTERFACE (IF-0-FAB-C-1) — D codes against this without renegotiation:

  * **Boundary-manifest format.** A TOML document at (by default)
    `.advisor-board/boundaries.toml`. Each top-level TABLE is one protected
    "surface" (e.g. `shared_contract`, `startup_boundary`, `auth_security`,
    `schema`, `deployment` — design §5.4's illustrative set; section NAMES
    are NOT a closed enum, see resolved ambiguity #1 below) and MUST have
    EXACTLY one key, `globs`, a list of strings. Any other shape — a
    non-table section value, a missing `globs` key, a non-list/non-string-list
    `globs` value, extra keys in a section, or bytes that don't even parse as
    TOML — is MALFORMED (fail-closed, see the manifest-disposition table
    below).

  * **Glob semantics (frozen, matches the design's `**`/`*`/`?` prose
    exactly).** Patterns are matched, NOT against the raw `os.fsdecode`d `-z`
    diff path, but against that path's `_normalize_path_for_matching` form
    (below) — a `fnmatch`-with-globstar translation: `**` matches any
    sequence of characters INCLUDING `/` (zero or more path segments); `*`
    matches any sequence of characters EXCEPT `/` (bounded to one segment);
    `?` matches exactly one character except `/`; every other character is
    matched LITERALLY (case-sensitive, no case-folding). Matching is a FULL
    match against the whole normalized path string, anchored at both ends —
    there is NO implicit `**/` prefix, so a bare `Dockerfile*` (design
    §5.4's own example) matches only a repo-ROOT `Dockerfile*`, not
    `sub/Dockerfile`. A glob string that is empty, non-string, contains any
    character outside the safe set `[A-Za-z0-9_./*?-]`, or — after being
    component-normalized the glob-specific way (below; NOT the same way a
    changed path is — see round-8) — resolves to NO components at all, is
    MALFORMED and INVALIDATES the whole manifest (fail-closed). A leading
    `/` component is dropped like any other empty component (round-7,
    below), but an interior (or leading/trailing) `..` component is ALWAYS
    malformed for a glob (round-8 correction, below) — unlike a changed
    path's `..`, which resolves.

    **Round-6 (Consiliency/agent-harness#191) — MATCHING-layer path fix.**
    `git mktree`/`git commit-tree` accept `.`, `..`, and empty tree-entry
    names, so a crafted delta can surface a literal `./…`, `…//…`, or
    `…/../…` changed path that a start-anchored glob's naive string match
    would never see (the confirmed evasion round-6 closed:
    `.github/workflows/**` failed to match `./.github/workflows/ci.yml`).
    The fix: every changed path (and, for carry-forward, every
    `Finding.path_scope` entry) is normalized via
    `_normalize_path_for_matching` before being compared against a compiled
    glob, so `./.github/workflows/ci.yml` normalizes to
    `.github/workflows/ci.yml` and matches exactly as if it had never been
    mangled. A changed path (or `path_scope` entry) that
    `_normalize_path_for_matching` cannot resolve at all (a `..` that would
    escape the repo root) is ABNORMAL and is never matched against globs —
    it fails closed directly (escalation-required / reopen-not-carry, see
    below), never silently treated as non-matching.

    **Round-7 (Consiliency/agent-harness#191, codex round-7 CR) — the
    SYMMETRIC glob-side fix, and a correction to round-6's risk read.**
    Round-6 normalized only the PATH side of every match and left the GLOB
    side raw, reasoning that a `.`/`..`/empty glob COMPONENT was "at worst
    an inert glob... a manifest-authoring lint concern, not a security
    defect." That was WRONG: a glob whose LITERAL components are entirely
    `.`/empty/self-canceling (`./**`, `a//b`, `x/./y`) compiles to a regex
    that can NEVER match any normalized path — not a lint nit but a silent,
    TOTAL fail-open, because the normalized path side and the un-normalized
    glob side can never agree on what "the same location" means. A surface
    declared with only such a glob (`[auth] globs = ["./**"]`) escalated
    NOTHING for any changed path. Round-7's fix normalized the glob's own
    literal components — drop `.`/empty, and (as first written) resolve
    `..` by popping the previous kept component, so `./**` normalizes to
    `**` (matches everything: broad, but fail-SAFE — the fail-safe direction
    for an escalation boundary, same bias `_translate_glob_to_regex`'s
    globstar handling already applies) rather than compiling to an inert
    `^\.(?:/.*)?$`.

    **Round-8 (Consiliency/agent-harness#191, codex round-8 CR) — the
    glob-side `..` policy was ITSELF unsound, corrected.** Round-7 resolved
    a glob's `..` component the SAME way it resolved a path's `..`
    (popping the previous kept component) — but a glob's previous component
    can be `**`, a VARIABLE-LENGTH unit (zero or more segments), and there
    is no fixed segment there to pop: `**/../auth/**` round-7-normalized to
    `auth/**`, compiling to the anchored `^auth(?:/.*)?$`, which does NOT
    match `x/auth/login.py` (the normalized form of the mangled changed path
    `x/y/../auth/login.py`) — an empirically confirmed UNDER-match, i.e. a
    silent fail-open in the escalation gate: a delta that should have
    required whole-patch review (`required=True`) was instead judged
    `required=False`. The fix: `_translate_glob_to_regex` now normalizes
    globs via a DEDICATED helper, `_normalize_glob_components` — NOT
    `_normalize_path_for_matching` — which still drops `.`/empty components
    (round-7, unaffected by the `**`-length ambiguity) but REJECTS any `..`
    component outright (never resolves it): a boundary glob has no
    legitimate reason to contain "up a directory" in the first place, so
    `..` in a glob is now a CONFIGURATION ERROR (`BoundaryManifestInvalid`
    -> `MANIFEST_DISPOSITION_MALFORMED` -> escalates every delta,
    fail-closed) rather than an attempted-but-unsound resolution. This is a
    DELIBERATE ASYMMETRY, not a regression of round-7's symmetric intent: a
    CHANGED PATH always resolves to a concrete, fixed-length repo location
    (git's own on-disk `..`-collapse, unambiguous), so
    `_normalize_path_for_matching` still RESOLVES `..` for paths; a GLOB can
    contain a variable-length `**`, so resolving its `..` is unsound and it
    is instead REJECTED. A wildcard segment (`*`, `**`, `?`, or a mixed
    segment like `*.proto`) and a literal `.git` component are still never
    dropped or altered by glob normalization. A glob that normalizes to
    NOTHING (every component `.`/empty) OR that contains any `..` component
    declares a surface with NO effective, soundly-resolvable boundary and is
    MALFORMED (fail-closed, escalates every delta).

    A literal (non-wildcard) `/`-delimited segment that ASCII-lowercases to
    exactly `.git` (e.g. `.git/**`, `src/.GIT/**`, `a/.git/b`) is
    DELIBERATELY NOT rejected: a round-4 CR briefly added such a rejection on
    the premise that git's pathname verifier forbids a `.git` component
    anywhere so it could never match a real diff path, but round-5
    (self-correcting) reverted that after empirical verification showed the
    premise false for this module's own threat model — `verify_path` governs
    the index/worktree, not the raw commit-tree diffs Lane C actually
    enumerates, and Lane B's hostile-tree threat model means a `.git/...`
    changed path IS reachable via a hand-crafted tree (`git mktree`/`git
    commit-tree` permit a `.git` entry; `fsck`'s `hasDotgit` is a warning,
    not a rejection). `.git/**` is therefore a legitimate, valuable boundary
    glob — it forces escalation on exactly that hostile-tree injection — not
    a semantic-empty one. See `_translate_glob_to_regex`'s docstring for the
    full decision boundary this validator is bounded to and the empirical
    repro; Consiliency/agent-harness#279 (filed on the now-reverted premise)
    is closed as superseded.

  * **Carry-forward/escalation decision rule (design §5.3/§5.4, the
    authoritative disposition table):**

    | Manifest disposition at `base_sha`        | Escalation                                    |
    |--------------------------------------------|------------------------------------------------|
    | ANY changed path is ABNORMAL (`_normalize_path_for_matching` returns `None` — a `..` that would escape the repo root) | `required=True`, `trigger="abnormal-delta-path"` (checked FIRST, before manifest-path/disposition/glob evaluation) |
    | absent (git show fails for ANY reason)      | `required=True`, `trigger="no-boundary-manifest"` |
    | present but malformed (TOML/shape error)    | `required=True`, `trigger="malformed-boundary-manifest"` |
    | present, well-formed, delta touches the manifest PATH itself (compared on NORMALIZED form) | `required=True`, `trigger="boundary-manifest-modified"` (checked BEFORE glob evaluation, regardless of manifest disposition) |
    | present, well-formed, a NORMALIZED changed path matches a glob | `required=True`, `trigger="<section name>"` |
    | present, well-formed, no glob match, manual trigger supplied | `required=True`, `trigger="reviewer:<seat_key>"` |
    | present, well-formed, no match, no manual trigger | `required=False`, `trigger=None` |

    A `required=True` round is whole-patch: `carry_forward()` is SUPPRESSED
    (every `status=="clean"` finding is reopened, none carried) and
    `review_scope` must satisfy `enforce_review_scope_for_escalation` (below).
    A `status=="clean"` finding carries forward (without re-review) iff it is
    NOT suppressed by escalation AND its `path_scope` is non-empty, contains
    no blank entry, and NORMALIZES cleanly (no entry is ABNORMAL) AND its
    NORMALIZED `path_scope` is disjoint from the NORMALIZED
    `delta_changed_paths` (via `_covered_by_owned`, reused — fed normalized
    strings on both sides, round-6). Empty/absent/abnormal `path_scope`
    NEVER carries (fail-closed re-review, not a silent carry) even when
    disjointness would otherwise hold trivially, and an ABNORMAL changed path
    is treated as intersecting every remaining clean finding (fail-closed:
    when a changed path cannot be resolved to a real repo-relative location,
    disjointness can never be asserted).

    **Path normalization (round-6, Consiliency/agent-harness#191 — the
    MATCHING-layer fix for the confirmed evasion).** `_translate_glob_to_regex`
    no longer rejects a `.`/`..`/empty glob COMPONENT (round-3's
    path-legality validation is REMOVED — see that function's docstring):
    the fix instead normalizes the PATH side of every match, so a glob and a
    path agree on what "the same location" means regardless of how a hostile
    tree spelled the path. `_normalize_path_for_matching` splits a changed
    path (or a `path_scope` entry) on `/`, drops `.` and empty segments, and
    resolves `..` by popping the previous segment — the exact normalization
    `git checkout` would apply on disk. A path that pops `..` past the repo
    root (or normalizes to nothing at all) is ABNORMAL (`None`) and is never
    matched against a glob or a `path_scope` entry — it fails closed directly
    (whole-patch escalation / finding reopened), per the table above. This
    closes the confirmed evasion directly: `./.github/workflows/ci.yml`,
    `.github//workflows/ci.yml`, and `x/../.github/workflows/ci.yml` all
    normalize to `.github/workflows/ci.yml` and are matched by
    `.github/workflows/**` exactly as the un-mangled path would be — a
    start-anchored boundary glob can no longer be evaded by a mangled-but-
    checkout-equivalent path spelling. Lane B (`patch_digest`,
    `enumerate_changed_paths`) is untouched: normalization is applied ONLY at
    this Lane-C matching decision, never to the raw bytes Lane B hashes for
    content identity.

Design ambiguities resolved in this lane (stated once, not re-litigated):

  1. **Boundary-manifest section names are NOT a closed enum.** Design §5.4's
     TOML example lists five illustrative sections
     (`shared_contract`/`startup_boundary`/`auth_security`/`schema`/
     `deployment`), but nothing in the design text says a repo may declare
     ONLY those five. Treating the name set as closed would be a maintenance
     trap (a repo adding a legitimate sixth protected surface, e.g.
     `billing`, would have it silently ignored rather than enforced — a
     fail-OPEN on exactly the kind of declared-surface escalation this
     section exists to guarantee). This module instead validates STRUCTURE
     (every section is a table with exactly one `globs: list[str]` key) and
     accepts any section name; `evaluate_boundary_escalation` iterates
     sections in SORTED name order so the `trigger` value is deterministic
     regardless of TOML key order.

  2. **`review_scope.covers_patch_digest`'s target is round-relative, not
     always `candidate.patch_digest`.** Design §5.5's illustrative JSON
     schema shows `covers_patch_digest` on `candidate.review_scope`, where it
     trivially equals `candidate.patch_digest` (the ONLY "whole patch" that
     exists at that point in the chain). Generalizing that literally to every
     later ESCALATED DELTA round — requiring `covers_patch_digest ==
     candidate.patch_digest` (the ORIGINAL candidate's digest, computed
     rounds earlier) — would be wrong: an escalated delta's whole-patch
     review must cover the patch AS IT STANDS AT THAT DELTA'S HEAD, i.e.
     `patch_digest(base_sha, delta_head_sha)`, which is exactly
     `DeltaReviewRecord.resulting_head_digest`. `enforce_review_scope_for_escalation`
     therefore takes a generic `covering_patch_digest` keyword (not literally
     `candidate.patch_digest`); callers pass `candidate.patch_digest` for the
     candidate round and a delta round's own `resulting_head_digest`
     (`fab_canonical.patch_digest(repo, base_sha, delta_head_sha, ...)`) for
     every later round — the invariant enforced is "the seats saw a
     whole-patch review whose digest matches the FULL patch as of THIS
     round's head", which is what design §5.5's prose ("the seats provably
     received the WHOLE patch, not a delta") actually requires.

  3. **`require_seat_corroboration` checks "some seat recorded a terminal
     verdict referencing this finding id", not full-board consensus.** T4
     says a `resolved_finding_ids` CLAIM with no corroborating delta-round
     seat verdict is rejected — it does not say every required seat must
     agree, only that at least one delta-round seat record actually reviewed
     (has a non-null `verdict` and lists the id in `finding_ids`) the claimed
     finding. Requiring unanimous non-DISAGREE agreement is a gate-composition
     concern (design §8's `status == "pass"` condition) that belongs to Lane
     D, not to "was this claim reviewed at all" (Lane C's narrower, purely
     evidentiary question).

  4. **`build_delta_round`'s `status` argument is caller-supplied, not
     inferred.** Whether a NON-escalated delta round is genuinely
     `reviewed-clean` vs. still `pending` depends on whether the delta
     round's own seats actually reached a clean/non-blocking verdict — a
     judgment this module cannot make from git state alone (that is Lane D's
     `status == "pass"` composition, design §8). `build_delta_round` instead
     VALIDATES the caller-supplied `status` for INTERNAL CONSISTENCY against
     the computed `escalation` (an `escalation.required=True` round can never
     be recorded `status="reviewed-clean"` — that would let a whole-patch-
     escalated round masquerade as a narrower carry-forward-eligible delta,
     the exact `T5` shape) rather than deciding `status` itself.

Fail-closed discipline (this is a security trust root, same posture as Lanes
A/B): a missing manifest, a malformed manifest, a malformed glob, a broken
delta-chain link, an uncorroborated resolved-finding claim, and a boundary-
escalated round recording `delta-only` scope all raise a typed exception
(a `fab_provenance.ProvenanceInvalid` subclass) rather than silently passing.
Additive only: nothing in `fab_provenance.py`, `fab_canonical.py`, or
`convergence/broker/credsep.py` is modified by this module — their helpers
are imported and reused, never re-implemented.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python 3.10 floor (matches advisor_board/config.py's shim)
    import tomli as tomllib  # type: ignore[no-redef]

from .convergence.broker.credsep import GitHubBrokerAdapter
from .fab_canonical import (
    _validate_full_sha,  # reuse the SAME resolved-object-id guard Lane B uses
    enumerate_changed_paths,
    patch_digest,
    read_file_at_revision,
)
from .fab_provenance import (
    DELTA_STATUS_ESCALATED_WHOLE_PATCH,
    DELTA_STATUS_INVALIDATED,
    DELTA_STATUS_PENDING,
    DELTA_STATUS_REVIEWED_CLEAN,
    REVIEW_SCOPE_WHOLE_PATCH,
    BoundaryManifestRef,
    DeltaReviewRecord,
    Escalation,
    Finding,
    MaterialDigest,
    ProvenanceInvalid,
    ProvenanceSeat,
    ReviewScope,
)

# --------------------------------------------------------------------------- #
# Frozen constants (IF-0-FAB-C-1)
# --------------------------------------------------------------------------- #

BOUNDARY_MANIFEST_PATH = ".advisor-board/boundaries.toml"

MANIFEST_DISPOSITION_PRESENT = "present"
MANIFEST_DISPOSITION_MISSING = "missing"
MANIFEST_DISPOSITION_MALFORMED = "malformed"

ESCALATION_TRIGGER_MANIFEST_MODIFIED = "boundary-manifest-modified"
ESCALATION_TRIGGER_NO_MANIFEST = "no-boundary-manifest"
ESCALATION_TRIGGER_MALFORMED_MANIFEST = "malformed-boundary-manifest"
ESCALATION_TRIGGER_ABNORMAL_PATH = "abnormal-delta-path"
_MANUAL_TRIGGER_PREFIX = "reviewer:"

CARRY_FORWARD_REASON_DISJOINT = "disjoint"
CARRY_FORWARD_REASON_INTERSECTS = "intersects_delta"
CARRY_FORWARD_REASON_EMPTY_SCOPE = "empty_path_scope"
CARRY_FORWARD_REASON_NOT_CLEAN = "not_clean"
CARRY_FORWARD_REASON_SUPPRESSED = "suppressed_by_escalation"

# Sentinel digest folded into `policy`/`chain_digest` when NO manifest exists
# at the reviewed base at all (as distinct from a present-but-malformed one,
# which folds in the real content hash of the malformed bytes instead) — a
# fixed, versioned constant so "no manifest" is itself a stable, auditable
# fact in the chain, not an absent/None field that could be confused with
# "not yet computed".
NO_MANIFEST_DIGEST = hashlib.sha256(b"fab.no-boundary-manifest.v1").hexdigest()

# design §3.3-adjacent hostile-git posture: only these characters are ever
# accepted in a glob pattern (letters/digits/`_-./*?`) — anything else
# (character classes, brace expansion, shell metacharacters, whitespace)
# is refused outright rather than partially interpreted.
_GLOB_SAFE_RE = re.compile(r"^[A-Za-z0-9_./*?-]+$")


# --------------------------------------------------------------------------- #
# Exceptions — fail-closed, typed, never silent (ProvenanceInvalid subclasses
# so Lane A/B/C share one catchable trust-root exception family)
# --------------------------------------------------------------------------- #


class DeltaBindingInvalid(ProvenanceInvalid):
    """A `DeltaReviewRecord` does not bind to its claimed parent/live-git
    state (design §5.2) — a broken chain link, a tampered `chain_digest`, a
    `delta_changed_paths` set that does not match the live `-z` diff, or a
    `resulting_head_digest` that does not match a live `patch_digest`
    recompute."""


class BoundaryManifestInvalid(ProvenanceInvalid):
    """The boundary manifest at the reviewed base is malformed (bad TOML
    shape, a non-list/non-string `globs`, or a malformed glob pattern) — this
    is a value, not necessarily a raised exception: `load_boundary_manifest_at_base`
    CATCHES this and returns a `MANIFEST_DISPOSITION_MALFORMED` result rather
    than propagating it, so a single bad manifest always resolves to
    escalate-every-delta rather than crashing the caller."""


class EscalationInvalid(ProvenanceInvalid):
    """A manual escalation trigger was not the typed `reviewer:<seat_key>`
    shape, or a caller-supplied `status` is internally inconsistent with the
    computed `escalation` (e.g. `escalation.required=True` with
    `status="reviewed-clean"` — T5's shape)."""


class ReviewScopeRejected(ProvenanceInvalid):
    """T5: a boundary-escalated round recorded `review_scope.mode ==
    "delta-only"`, or a `covers_patch_digest` that does not match the full
    patch digest as of this round's head — escalation cannot be satisfied by
    a delta-scoped round."""


class ResolvedClaimUnverified(ProvenanceInvalid):
    """T4: a `resolved_finding_ids` (or reopened-finding) claim with no
    corroborating delta-round seat verdict on that exact finding id — a claim
    is not a resolution."""


# --------------------------------------------------------------------------- #
# §5.4 — glob semantics (frozen, IF-0-FAB-C-1)
# --------------------------------------------------------------------------- #


def _translate_segment(segment: str) -> str:
    """Translate ONE `/`-delimited path segment (guaranteed not to be exactly
    `"**"` — that's handled by the caller as a globstar unit) into a regex
    fragment: `*` -> "any chars except /", `?` -> "one char except /",
    everything else literal (`re.escape`d)."""
    out: list[str] = []
    for ch in segment:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


# Sentinel regex-fragment marker for a "zero-or-more leading path segments"
# globstar unit, so the segment-join logic below can recognize and skip the
# separator `/` it already accounts for (distinguishing it from an ordinary
# translated segment, which always needs an explicit `/` joiner before it).
_GLOBSTAR_PREFIX_UNIT = "(?:.*/)?"


def _normalize_path_for_matching(path: str) -> str | None:
    """Round-6 (Consiliency/agent-harness#191) fix for the CONFIRMED
    boundary-glob evasion: `enumerate_changed_paths` does NO normalization
    (Lane B correctly keeps its raw bytes load-bearing for content identity),
    and `git mktree`/`git commit-tree` accept `.`, `..`, and EMPTY tree-entry
    names (only a literal `/` is rejected) — so a hostile tree can surface a
    changed path like `./.github/workflows/ci.yml`,
    `.github//workflows/ci.yml`, or `x/../.github/workflows/ci.yml` that a
    START-ANCHORED boundary glob's literal-string match never sees, even
    though `git checkout`/normal diff plumbing would collapse it to the
    protected path itself. This function applies exactly that checkout-
    equivalent collapse, for the MATCHING decision only:

      * split `path` on `/`;
      * DROP every `.` segment and every EMPTY segment (leading slash,
        trailing slash, or a doubled `//` all produce one) — neither
        contributes a real path component;
      * a `..` segment POPS the previous kept segment (the standard
        `..`-resolves-to-parent rule). If there is no previous segment to
        pop (the `..` would escape the repo root — `x/../../outside`, or a
        bare leading `..`), the path is ABNORMAL: return `None` rather than
        silently dropping the segment or leaving a bogus `..` in the result.
      * every other segment (including a literal `.git`, which is a real,
        meaningful path component — see `_translate_glob_to_regex`'s
        docstring) is kept VERBATIM, in order — no case-folding, no other
        transformation.

    If the fully-resolved result is empty (e.g. `path` was `"."`, `"/"`, or
    `""`), that is ALSO abnormal: return `None`.

    Callers MUST fail closed on `None` (never treat it as "matches nothing"
    — an unresolvable path is exactly the case this function exists to catch,
    and a path this module cannot place inside the repo is never safe to
    treat as disjoint from anything). See `evaluate_boundary_escalation` (an
    abnormal changed path forces whole-patch escalation) and `carry_forward`
    (an abnormal changed path or `path_scope` entry forces reopening rather
    than carry-forward).

    **Round-7/round-8 history — glob-side reuse REMOVED
    (Consiliency/agent-harness#191).** Round-7 made `_translate_glob_to_
    regex` call this SAME function on the GLOB string itself, reasoning that
    a glob's own `.`/`..`/empty components needed the identical collapse a
    changed path gets. Round-8 (codex CR) found that reuse UNSOUND for the
    `..` case specifically: this function resolves `..` by popping the
    "previous kept segment", which is valid for a PATH (always a concrete,
    fixed-length, on-disk location — git's own real collapse semantics) but
    NOT for a GLOB, whose previous segment can be `**` (a variable-length,
    zero-or-more-segments unit with no fixed segment to pop). Confirmed
    empirically: reusing this function normalized the glob
    `**/../auth/**` to `auth/**` (popping the `**`), which under-matched a
    real mangled path that should have escalated. `_translate_glob_to_regex`
    now calls a DEDICATED glob-side helper, `_normalize_glob_components`
    (below `_translate_glob_to_regex`'s definition), which still drops
    `.`/empty components (unaffected by the `**`-length ambiguity — a no-op
    regardless of context) but REJECTS (never resolves) any `..` component
    — a boundary glob has no legitimate reason to contain "up a directory"
    at all. This function (`_normalize_path_for_matching`) is therefore used
    ONLY for changed paths and `path_scope` entries now, never for globs —
    the `..` POLICY differs by design between the two sides even though the
    `.`/empty-drop policy does not.

    Deliberately NOT used by Lane B (`fab_canonical.patch_digest`/
    `enumerate_changed_paths`): those keep raw, non-normalized bytes — this
    normalization exists ONLY for the Lane-C escalation/carry-forward
    matching decision (both the path side and, since round-7, the glob
    side), never for content-identity hashing."""
    segments: list[str] = []
    for segment in path.split("/"):
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            if not segments:
                return None  # would escape the repo root — abnormal, fail closed
            segments.pop()
            continue
        segments.append(segment)
    if not segments:
        return None
    return "/".join(segments)


def _normalize_glob_components(glob: str) -> str | None:
    """Round-8 (Consiliency/agent-harness#191, codex round-8 CR) glob-side
    component normalization — deliberately DISTINCT from
    `_normalize_path_for_matching` (which normalizes CHANGED PATHS). Round-7
    made `_translate_glob_to_regex` reuse `_normalize_path_for_matching`
    verbatim on the glob string itself, treating a glob `..` component
    exactly like a path `..` component (pop the previous kept segment).
    That was UNSOUND, confirmed by codex round-8: a glob's `**` segment
    represents ZERO-OR-MORE path segments, a variable-length unit, so
    "popping the previous kept segment" when that segment is a `**` is not
    a valid resolution — there is no fixed segment there to pop. Empirically:
    round-7's logic normalized `**/../auth/**` to `auth/**` (popping the
    `**`), which compiled to the ANCHORED pattern `^auth(?:/.*)?$` — that
    pattern does NOT match `x/auth/login.py`, the normalized form of the
    mangled changed path `x/y/../auth/login.py`, even though the glob as
    authored should protect `auth/**` everywhere. A boundary glob authored
    with a `..` after a `**` therefore silently under-protected: a real
    `required=True` escalation was silently downgraded to `required=False`
    — a fail-open in the escalation gate itself.

    The fix: a boundary glob may NEVER contain a `..` component, full stop.
    There is no legitimate reason to author "go up a directory" in an
    escalation-boundary declaration — a boundary glob names a set of
    PROTECTED repo-relative locations to match against, not a relative
    traversal to resolve — so `..` in a glob is a CONFIGURATION ERROR, not
    something to normalize away. This function returns `None` (malformed)
    the instant it sees a `..` component; the caller
    (`_translate_glob_to_regex`) raises `BoundaryManifestInvalid`, which
    propagates to `MANIFEST_DISPOSITION_MALFORMED` (whole-patch escalation
    of every delta) — fail-closed, not an attempted-but-unsound resolution.

    `.` and empty components are still dropped, unchanged from round-7:
    dropping them is a no-op regardless of what precedes them (no `**`-
    length ambiguity — `./**` -> `**`, `a//b` -> `a/b` are both still exact,
    sound rewrites of what the glob matches). Wildcard segments (`*`, `**`,
    `?`, and mixed segments like `*.proto`) and a literal `.git` component
    are kept verbatim, exactly as `_normalize_path_for_matching` keeps them
    for paths — this is the one piece of normalization logic still shared
    in spirit (drop `.`/empty) between the two sides; the `..` POLICY is
    deliberately asymmetric (paths RESOLVE `..`, per git's own real
    on-disk collapse semantics; globs REJECT it, because a glob's `**` has
    no fixed on-disk collapse to resolve against).

    Returns `None` (malformed — caller raises `BoundaryManifestInvalid`) when:
      * the glob contains ANY `..` component, at any position; or
      * the fully-normalized result is empty (every component was `.`/empty,
        e.g. `.`, `./`, `//`) — round-7's "no effective boundary" case,
        unchanged."""
    segments: list[str] = []
    for segment in glob.split("/"):
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            # Round-8: reject outright. A glob's `..` cannot be soundly
            # resolved against a preceding `**` (variable-length, no fixed
            # segment to pop) — never attempt it, fail closed instead.
            return None
        segments.append(segment)
    if not segments:
        return None
    return "/".join(segments)


def _translate_glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate ONE boundary-manifest glob into an anchored, case-sensitive
    regex, segment-wise on `/` (design's "PurePosixPath-style `**`/`*`/`?`"):

      * a segment that is EXACTLY `**` is a globstar unit: matches "zero or
        more full path segments" when it is NOT the last segment (so
        `**/contracts/**` also matches the bare top-level `contracts` dir and
        `contracts/x.py` — the globstar consumes ZERO segments and its
        following `/` disappears with it, not just the segments themselves);
        when it IS the last segment, it matches "the rest of the path,
        including nothing at all" (so `a/**` also matches `a` itself, not
        only `a/<something>`). Both directions deliberately bias toward
        MATCHING MORE, not less — the fail-safe direction for an escalation
        boundary (missing an escalation is a fail-OPEN; a spurious one is
        merely inconvenient);
      * within any OTHER segment, `*` matches any run of characters except
        `/` (bounded to that one segment) and `?` matches exactly one
        non-`/` character; every other character is literal.

    Raises `BoundaryManifestInvalid` on an empty/non-string glob, any
    character outside the safe set `[A-Za-z0-9_./*?-]`, or a glob that —
    after glob-side component normalization (`_normalize_glob_components`,
    below) — resolves to NO effective boundary at all (every component was
    `.`/empty, OR the glob contains ANY `..` component, at any position,
    which round-8 REJECTS rather than resolves) — a malformed glob
    INVALIDATES (never silently "matches nothing").

    DECISION BOUNDARY (Consiliency/agent-harness#191 Lane C — round-3 added,
    and round-6 REMOVED, a per-segment `.`/`..`/empty-component REJECTION;
    round-7 replaced that removal with component NORMALIZATION (including
    resolving `..`), the symmetric partner of round-6's path-side fix,
    below; round-8 found round-7's `..`-RESOLUTION unsound and replaced it
    with `..`-REJECTION specifically (the `.`/empty-drop stays
    normalization, unchanged) — see the round-8 paragraph below; round-4
    added and round-5 REVERTED a `.git`-component rejection; see below).
    This validator's job is SYNTACTIC-plus-NORMALIZING: reject a glob string
    that cannot possibly be a well-formed boundary declaration (empty,
    outside the safe charset, contains a `..` component, or normalizes to
    nothing) and otherwise NORMALIZE its literal `.`/empty components (via a
    glob-specific helper, not the path helper) before translating — it does
    not attempt to predict, at manifest-parse time, which real changed paths
    a glob will or won't match; it only guarantees that the glob and the
    paths it will later be compared against agree on what "the same
    location" means, without ever attempting an unsound `..` resolution
    against a variable-length `**`.

    Round-3 (REMOVED in round-6) rejected a glob containing a `.`/`..`/empty
    path COMPONENT (`./**`, `.`, `x/./y`, `a//b`, `x/`) on the premise that
    `fab_canonical.enumerate_changed_paths`'s `-z` diff paths are always
    already normalized and could never contain one, so such a glob could
    "never match a real changed path" and must be a silent downgrade.
    Empirical verification for round-6 falsified the premise the OTHER
    direction round-4/5 already falsified it for `.git`: `git mktree`/`git
    commit-tree` accept `.`, `..`, and EMPTY tree-entry names (only a literal
    `/` is ever rejected), so a hostile tree CAN surface an un-normalized
    changed path (e.g. `./.github/workflows/ci.yml`) that `evaluate_boundary_
    escalation`'s OLD literal-string match against a START-ANCHORED glob
    (`.github/workflows/**`) would silently fail to match — the CONFIRMED
    evasion this round closes. Round-3's fix (rejecting the glob) treated the
    wrong side of the comparison: the manifest's glob was never the problem;
    the unnormalized PATH being matched against it was. Round-6 therefore
    normalizes the path side of every match (`_normalize_path_for_matching`,
    used by `evaluate_boundary_escalation` and `carry_forward`), REMOVING the
    per-segment `.`/`..`/empty-component rejection this function used to do,
    on the reasoning that such a glob was now "at worst an inert glob... a
    manifest-authoring lint concern, not a fail-open."

    **That reasoning was WRONG, corrected in round-7** (Consiliency/agent-
    harness#191, codex round-7 CR): round-6 normalized only the path side and
    left THIS function's glob translation on the raw, un-normalized glob
    string. A glob whose literal components are entirely `.`/empty/self-
    canceling (`./**`, `a//b`, `x/./y`) therefore compiled to a regex that
    could NEVER match any normalized path — `./**` compiled to `^\.(?:/.*)?
    $`, which no normalized path (never containing a bare leading `.`
    component) can ever satisfy. That is not a "lint concern": a section
    declared with ONLY such a glob (`[auth] globs = ["./**"]`) escalated
    NOTHING, for any changed path — a silent, total fail-open, the exact
    dual of the path-side evasion round-6 closed. Round-7's fix normalized
    the glob string — drop-`.`/empty, and (as first written) pop-`..` —
    before translating, so `./**` normalizes to `**` (matches everything:
    broad, but fail-SAFE) instead of compiling to an inert no-op.

    **Round-8 (Consiliency/agent-harness#191, codex round-8 CR) — round-7's
    `..`-POP was ITSELF unsound, corrected.** Round-7 popped the previous
    kept glob component on `..`, exactly like the path-side helper — but a
    glob's previous component can be `**`, a variable-length (zero-or-more-
    segments) unit with no fixed segment to pop. Empirically confirmed: the
    glob `**/../auth/**` round-7-normalized to `auth/**` (popping the `**`),
    compiling to the ANCHORED `^auth(?:/.*)?$`, which does NOT match
    `x/auth/login.py` — the normalized form of the mangled changed path
    `x/y/../auth/login.py` — even though `**/../auth/**` visually reads as
    "protect `auth/**` anywhere". That is an UNDER-match: a real
    `required=True` escalation silently downgraded to `required=False`, a
    fail-open in the escalation gate. Round-8's fix: glob normalization now
    REJECTS any `..` component outright (via `_normalize_glob_components`,
    a helper dedicated to globs) rather than resolving it — a boundary glob
    has no legitimate reason to contain "up a directory" at all, so `..` in
    a glob is a CONFIGURATION ERROR (malformed, fail-closed, escalates every
    delta) rather than something to normalize away. The `.`/empty-drop half
    of round-7's fix is UNCHANGED (no `**`-length ambiguity there — dropping
    a `.`/empty component is sound regardless of context). This is a
    deliberate ASYMMETRY between the two sides of a match, not a rollback of
    round-7's intent: a changed PATH always resolves to one concrete,
    fixed-length repo location (git's real on-disk `..`-collapse), so the
    path-side helper still RESOLVES `..`; a GLOB can contain a
    variable-length `**`, so its `..` is REJECTED instead. A glob that
    normalizes to NOTHING at all (`.`, `./`, `//`) OR that contains any `..`
    component (`a/../b`, `**/../x`, `x/../**`, `../auth/**`, a bare `..`)
    declares a surface with NO effective, soundly-resolvable boundary and is
    now MALFORMED (fail-closed) — never silently accepted, and never
    resolved via an unsound pop. Removing round-3's blunt rejection
    (round-6) and replacing it with normalization (round-7), then narrowing
    that normalization's `..` handling from resolve to reject (round-8),
    keeps both sides of the match agreeing on what "the same location"
    means without ever compiling an unsound anchor from a variable-length
    glob.

    A literal `.git` path component is likewise NOT rejected (round-4 added
    such a rejection; round-5, self-correcting, reverted it after empirical
    verification showed the premise was FALSE for Lane C's actual threat
    model). Round-4 reasoned that git's pathname verifier (`verify_path`)
    unconditionally forbids a `.git` component anywhere in a tree, so a
    literal `.git` glob segment could never match a real diff path. That is
    true of `verify_path`, but `verify_path` governs the INDEX/WORKTREE (`git
    checkout`, `git add`, normal commits) — it is NOT consulted by the raw
    plumbing that builds and diffs commit-tree objects directly. `git mktree`
    happily accepts and creates a tree object with a `.git` entry; `git
    commit-tree` happily commits it; `git fsck`'s `hasDotgit` check for such
    an object is a WARNING, not an unconditional rejection. Lane B's own
    threat model (this module's PRE-STATED TRUST BOUNDARY, above, and
    `fab_canonical.py`'s hostile-git discipline) explicitly includes
    HAND-CRAFTED trees — an attacker who controls repo CONTENTS is not
    required to go through `verify_path` at all — and
    `fab_canonical.enumerate_changed_paths` is exactly a raw commit-tree `git
    diff --raw` enumeration, not an index/worktree operation. Verified
    directly: a crafted head commit (tree built via `git mktree` with a
    `.git` subtree entry, committed via `git commit-tree`) against a
    realistic base produces `git diff --no-renames -z --raw <base>
    <crafted-head>` output containing a `.git/config` changed path (rc==0).
    So `.git/config` IS a reachable changed path via a hostile tree, which
    makes `.git/**` a legitimate, VALUABLE boundary glob — matching that
    injection is exactly the protection an operator declaring it wants — not
    a semantic-empty one. See `tests/test_fab_delta_c.py`'s crafted-tree
    escalation tests for the reproduction and the corresponding positive
    coverage (both the pre-existing `.git` one and round-6's new
    start-anchored-glob evasion one).

    What this validator deliberately does NOT do: predict path-matching
    outcomes, or exhaustively reproduce every platform/filesystem-specific
    git-pathname restriction (Windows reserved device names
    `CON`/`NUL`/`AUX`/...`, NTFS alternate-data-stream `:` forms, HFS+
    Unicode-dotless `.git` homoglyphs, Windows trailing-dot/trailing-space
    trimming, and similar). Those are real but are NOT a Lane C blocker: this
    module's own PRE-STATED TRUST BOUNDARY (module docstring, mirroring Lane
    A §6.1a / Lane B / agent-harness#276) is that the boundary manifest is
    read at the REVIEWED, base-pinned revision — a trusted, already-reviewed
    artifact, not attacker-controlled delta content. Consiliency/agent-harness#279
    (filed to track this kind of exotic-form parity on round-4's now-reverted
    premise) remains closed as superseded."""
    if not isinstance(glob, str) or not glob:
        raise BoundaryManifestInvalid(f"malformed boundary glob (empty/non-string, fail-closed): {glob!r}")
    if not _GLOB_SAFE_RE.match(glob):
        raise BoundaryManifestInvalid(
            f"malformed boundary glob (only [A-Za-z0-9_./*?-] accepted, fail-closed): {glob!r}"
        )

    # Round-8 (Consiliency/agent-harness#191, codex round-8 CR): normalize
    # the glob's own literal components via `_normalize_glob_components` —
    # a DEDICATED glob-side helper, NOT `_normalize_path_for_matching`
    # (which round-7 used here and which round-8 found unsound: resolving a
    # glob `..` by popping the previous kept component is invalid when that
    # component is a `**`, a variable-length unit with no fixed segment to
    # pop). Drop `.`/empty components (round-7 behavior, unchanged) but
    # REJECT (never resolve) any `..` component — see
    # `_normalize_glob_components`'s docstring for the empirically-confirmed
    # under-match this closes. A glob whose components are ENTIRELY
    # `.`/empty (`.`, `./`, `//`) OR that contains any `..` component at all
    # resolves to NO effective boundary and is MALFORMED (fail-closed).
    normalized = _normalize_glob_components(glob)
    if normalized is None:
        raise BoundaryManifestInvalid(
            "malformed boundary glob (normalizes to no effective boundary — every component was "
            f"'.'/empty, or the glob contains a '..' component (rejected, not resolved — unsound "
            f"against a variable-length '**'); fail-closed): {glob!r}"
        )

    segments = normalized.split("/")
    n = len(segments)
    parts: list[str] = []
    i = 0
    while i < n:
        seg = segments[i]
        if seg == "**":
            j = i
            while j < n and segments[j] == "**":  # collapse a run of "**" segments
                j += 1
            if j == n:
                # Trailing globstar: the rest of the path, including nothing.
                parts.append("(?:/.*)?" if parts else ".*")
            else:
                parts.append(_GLOBSTAR_PREFIX_UNIT)
            i = j
            continue
        if parts and parts[-1] != _GLOBSTAR_PREFIX_UNIT:
            parts.append("/")
        parts.append(_translate_segment(seg))
        i += 1

    pattern = "^" + "".join(parts) + "$"
    try:
        # agent-harness#191 CR (Lane C finding 1, glob-newline fail-open): git
        # permits NEWLINE characters in filenames, and the `-z` + `os.fsdecode`
        # path preserves them verbatim. The `.`-based fragments this translator
        # emits for `**` (`_GLOBSTAR_PREFIX_UNIT`/the trailing-globstar cases
        # above) would, WITHOUT `re.DOTALL`, refuse to span an embedded `\n` —
        # so a protected-surface delta whose only difference from a covered
        # path is an embedded newline would silently escape escalation. `?`/`*`
        # are unaffected (they already compile to `[^/]`/`[^/]*` character
        # classes, which match `\n` by construction, negated-class semantics
        # are never subject to DOTALL) — `re.DOTALL` only widens what `.`
        # matches, so this cannot narrow any existing match, only widen `**`'s
        # span to include newlines, the fail-safe direction for an escalation
        # boundary (see this function's own docstring: biasing toward
        # MATCHING MORE, never less).
        return re.compile(pattern, re.DOTALL)
    except re.error as exc:  # pragma: no cover - the safe-charset guard above should preclude this
        raise BoundaryManifestInvalid(f"boundary glob failed to compile (fail-closed): {glob!r}: {exc}") from exc


# --------------------------------------------------------------------------- #
# §5.4 — boundary manifest: parse + base-pinned load
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class BoundaryManifest:
    """A successfully parsed `.advisor-board/boundaries.toml` at a pinned
    revision: `sections` (section name -> raw glob strings, for audit/
    display) and `compiled` (section name -> compiled regexes, for
    matching)."""

    sections: Mapping[str, tuple[str, ...]]
    compiled: Mapping[str, tuple[re.Pattern[str], ...]]
    digest: str
    source_rev: str
    path: str

    def to_ref(self) -> BoundaryManifestRef:
        return BoundaryManifestRef(path=self.path, source_rev=self.source_rev, digest=self.digest)


def _parse_boundary_manifest_bytes(raw: bytes, *, path: str, source_rev: str) -> BoundaryManifest:
    """Parse+validate boundary-manifest TOML bytes. Raises
    `BoundaryManifestInvalid` on: undecodable/malformed TOML, a non-table
    top level, a section that is not a table, a section missing `globs`, a
    section with keys OTHER than `globs`, a non-list-of-strings `globs`
    value, any glob that fails `_translate_glob_to_regex`, or a manifest
    that parses cleanly but declares ZERO sections (empty/comment-only
    content — finding 2, see the check below)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BoundaryManifestInvalid(f"boundary manifest is not valid UTF-8 (fail-closed): {exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise BoundaryManifestInvalid(f"malformed boundary manifest TOML (fail-closed): {exc}") from exc
    if not isinstance(data, dict):
        raise BoundaryManifestInvalid("boundary manifest must be a TOML table at the top level (fail-closed)")

    sections: dict[str, tuple[str, ...]] = {}
    compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
    for section_name, section_value in data.items():
        if not isinstance(section_value, dict):
            raise BoundaryManifestInvalid(
                f"boundary manifest section {section_name!r} must be a TOML table (fail-closed)"
            )
        if set(section_value.keys()) != {"globs"}:
            raise BoundaryManifestInvalid(
                f"boundary manifest section {section_name!r} must have EXACTLY one key 'globs' "
                f"(fail-closed): got keys {sorted(section_value.keys())!r}"
            )
        globs = section_value["globs"]
        if not isinstance(globs, list) or not globs or not all(isinstance(g, str) for g in globs):
            raise BoundaryManifestInvalid(
                f"boundary manifest section {section_name!r}.globs must be a non-empty list of strings "
                f"(fail-closed): {globs!r}"
            )
        compiled_globs = tuple(_translate_glob_to_regex(g) for g in globs)
        sections[section_name] = tuple(globs)
        compiled[section_name] = compiled_globs

    if not sections:
        # agent-harness#191 CR (Lane C finding 2, present-but-empty-manifest
        # fail-open): a TOML document with zero top-level tables (an empty
        # file, or one containing only comments) parses successfully to `{}`
        # — the loop above never runs, so `sections`/`compiled` stay empty and
        # this would otherwise be classified PRESENT + VALID with ZERO
        # compiled boundary sections. `evaluate_boundary_escalation` iterates
        # `load.manifest.compiled` and, finding nothing to match, returns
        # `required=False` — "no boundaries" would then silently PERMIT
        # carry-forward, contrary to the frozen disposition table (module
        # docstring): an empty boundary set can NEVER mean "carry everything
        # forward". Raising here routes a present-but-empty manifest through
        # the SAME `BoundaryManifestInvalid` -> `MANIFEST_DISPOSITION_MALFORMED`
        # path as a genuinely malformed one, so it gets the same fail-closed
        # `escalate-EVERY-delta` disposition as MISSING/MALFORMED.
        raise BoundaryManifestInvalid(
            "boundary manifest is present but declares ZERO protected-surface sections "
            "(empty file or comment-only content) — fail-closed: an empty boundary set can "
            "never mean carry-forward-all, so this is treated as malformed (escalate every delta)"
        )

    digest = hashlib.sha256(raw).hexdigest()
    return BoundaryManifest(sections=sections, compiled=compiled, digest=digest, source_rev=source_rev, path=path)


@dataclass(frozen=True, kw_only=True)
class BoundaryManifestLoad:
    """The result of loading the boundary manifest at a pinned base
    revision: `disposition` is one of the `MANIFEST_DISPOSITION_*` constants;
    `manifest` is populated only when `disposition == present`; `ref` is
    ALWAYS populated (present -> real content digest; missing -> the fixed
    `NO_MANIFEST_DIGEST` sentinel; malformed -> the real digest of the
    malformed bytes) so `DeltaReviewRecord.build(policy=...)` always has a
    well-defined value to fold into `chain_digest` regardless of
    disposition — an absent/broken manifest is itself a fact worth chaining
    (T15: the RULES in force, including "there were no rules", are pinned)."""

    disposition: str
    manifest: BoundaryManifest | None
    ref: BoundaryManifestRef


def load_boundary_manifest_at_base(
    repo: Path, base_sha: str, *, path: str = BOUNDARY_MANIFEST_PATH
) -> BoundaryManifestLoad:
    """design §5.4/T15: read the boundary manifest's CONTENT at `base_sha`
    (never the delta head) via `fab_canonical.read_file_at_revision` (reused,
    hostile-git-hardened `git show <base_sha>:<path>`), parse it, and return
    a `BoundaryManifestLoad`. NEVER raises for an in-scope disposition
    (missing/malformed) — both are legitimate, expected states this function
    resolves to a typed result so a caller can never forget to handle them;
    only a genuine programming-error exception would propagate."""
    base_sha = _validate_full_sha(base_sha, field_name="base_sha")
    raw = read_file_at_revision(repo, base_sha, path)
    if raw is None:
        return BoundaryManifestLoad(
            disposition=MANIFEST_DISPOSITION_MISSING,
            manifest=None,
            ref=BoundaryManifestRef(path=path, source_rev=base_sha, digest=NO_MANIFEST_DIGEST),
        )
    try:
        manifest = _parse_boundary_manifest_bytes(raw, path=path, source_rev=base_sha)
    except BoundaryManifestInvalid:
        return BoundaryManifestLoad(
            disposition=MANIFEST_DISPOSITION_MALFORMED,
            manifest=None,
            ref=BoundaryManifestRef(path=path, source_rev=base_sha, digest=hashlib.sha256(raw).hexdigest()),
        )
    return BoundaryManifestLoad(disposition=MANIFEST_DISPOSITION_PRESENT, manifest=manifest, ref=manifest.to_ref())


# --------------------------------------------------------------------------- #
# §5.4 — escalation decision
# --------------------------------------------------------------------------- #


def evaluate_boundary_escalation(
    load: BoundaryManifestLoad,
    delta_changed_paths: Sequence[str],
    *,
    manual_trigger: str | None = None,
) -> Escalation:
    """design §5.4 — the frozen escalation decision rule (IF-0-FAB-C-1's
    disposition table, module docstring). Checked in this exact order:

      1. a caller-supplied MANUAL trigger (typed `reviewer:<seat_key>`,
         never parsed from prose) always forces escalation;
      2. ANY `delta_changed_paths` entry that `_normalize_path_for_matching`
         cannot resolve (a `..` that would escape the repo root) forces
         whole-patch escalation — `trigger=ESCALATION_TRIGGER_ABNORMAL_PATH`
         (round-6: fail-closed on a path this module cannot even place
         inside the repo, checked before anything manifest-disposition- or
         glob-shaped);
      3. `delta_changed_paths` touching the manifest PATH itself (compared on
         NORMALIZED form, round-6 — a mangled path to the manifest itself
         must not evade this check either) forces whole-patch escalation,
         REGARDLESS of manifest disposition (a delta that deletes/breaks the
         manifest is still caught here even though the manifest itself would
         separately resolve to `malformed`/`missing`);
      4. a `missing`/`malformed` manifest escalates EVERY delta
         (fail-closed — "no boundaries" must never mean "carry everything
         forward");
      5. a `present` manifest escalates if any NORMALIZED changed path
         matches any glob in any section (sections iterated in SORTED name
         order for a deterministic `trigger`) — round-6: matching is against
         `_normalize_path_for_matching(changed_path)`, not the raw path, so a
         hostile-tree-mangled path (`./…`, `…//…`, `…/../…`) that collapses
         to a genuinely protected path can no longer evade a start-anchored
         glob (the confirmed evasion this round closes); round-7: the
         compiled glob itself is ALSO built from its normalized components
         (`_translate_glob_to_regex`), so a boundary glob authored with
         `.`/`..`/empty components (`./**`, `a//b`) is no longer inert
         against every normalized path either — matching is symmetric on
         both sides;
      6. otherwise, no escalation."""
    if manual_trigger is not None:
        if not manual_trigger.startswith(_MANUAL_TRIGGER_PREFIX) or manual_trigger == _MANUAL_TRIGGER_PREFIX:
            raise EscalationInvalid(
                f"manual escalation trigger must be typed {_MANUAL_TRIGGER_PREFIX}<seat_key> "
                f"(fail-closed, never parsed from prose): {manual_trigger!r}"
            )
        return Escalation(required=True, trigger=manual_trigger)

    changed = tuple(delta_changed_paths)
    normalized_changed: list[str] = []
    for raw_path in changed:
        normalized = _normalize_path_for_matching(raw_path)
        if normalized is None:
            # Fail-closed: a changed path this module cannot resolve to a
            # real repo-relative location is never safe to treat as "doesn't
            # match" — force whole-patch escalation immediately.
            return Escalation(required=True, trigger=ESCALATION_TRIGGER_ABNORMAL_PATH)
        normalized_changed.append(normalized)

    normalized_manifest_path = _normalize_path_for_matching(load.ref.path)
    if normalized_manifest_path is not None and normalized_manifest_path in normalized_changed:
        return Escalation(required=True, trigger=ESCALATION_TRIGGER_MANIFEST_MODIFIED)

    if load.disposition == MANIFEST_DISPOSITION_MISSING:
        return Escalation(required=True, trigger=ESCALATION_TRIGGER_NO_MANIFEST)
    if load.disposition == MANIFEST_DISPOSITION_MALFORMED:
        return Escalation(required=True, trigger=ESCALATION_TRIGGER_MALFORMED_MANIFEST)

    assert load.manifest is not None  # disposition == present implies this
    for section_name in sorted(load.manifest.compiled.keys()):
        patterns = load.manifest.compiled[section_name]
        for normalized_path in normalized_changed:
            if any(pattern.match(normalized_path) for pattern in patterns):
                return Escalation(required=True, trigger=section_name)

    return Escalation(required=False, trigger=None)


# --------------------------------------------------------------------------- #
# §5.3 — clean-finding carry-forward (reuses the broker's disjointness test)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class CarryForwardResult:
    carried_forward_finding_ids: tuple[str, ...]
    reopened_finding_ids: tuple[str, ...]
    reasons: Mapping[str, str] = field(default_factory=dict)


def carry_forward(
    findings: Sequence[Finding],
    delta_changed_paths: Sequence[str],
    *,
    suppress: bool = False,
) -> CarryForwardResult:
    """design §5.3: decide which `status=="clean"` findings carry forward
    (valid without re-review) across a delta.

    A finding `f` carries forward IFF ALL of:
      * `f.status == "clean"` (a non-clean/still-open finding is not a
        carry-forward candidate at all — it is excluded from BOTH output
        lists; a caller resolving it does so via `resolved_finding_ids` +
        `require_seat_corroboration`, not via this function);
      * `suppress` is False (a boundary-escalated round is whole-patch —
        carry-forward is SUPPRESSED entirely and every clean finding is
        reopened, design §5.4);
      * `f.path_scope` is non-empty AND contains NO blank entry — blank
        meaning empty, whitespace-only, OR all-`/` after stripping
        (`entry.strip().rstrip("/")` is falsy) — so `("",)`, `("  ",)`, AND
        `("/",)`/`("//",)` all NEVER carry (fail-closed re-review, design
        §5.3). Checked on CONTENT, not just sequence length: `_covered_by_owned`
        below applies its OWN `.rstrip("/")` to each owned entry and silently
        SKIPS one that comes out empty (credsep.py:204-208), so a `""` or
        `"/"`-only entry would otherwise never match anything and the
        finding would look "disjoint" from every path without ever actually
        being scoped;
      * `f.path_scope` is DISJOINT from `delta_changed_paths`, decided by
        the broker's OWN `GitHubBrokerAdapter._covered_by_owned` prefix/dir
        test (credsep.py:190) — REUSED, not re-implemented (goal-id-inc2
        lesson) — fed NORMALIZED strings on both sides (round-6, below): for
        every NORMALIZED changed path, is it covered by (equal to, or nested
        under) some NORMALIZED entry in `f.path_scope`? An intersecting
        `path_scope` reopens the finding.

    **Path normalization (round-6, Consiliency/agent-harness#191).** Both
    `delta_changed_paths` and every `f.path_scope` entry are passed through
    `_normalize_path_for_matching` before the disjointness test — the same
    fix `evaluate_boundary_escalation` applies, for the same reason: a
    hostile tree can surface a changed path like `x/../pkg/a.py` that,
    un-normalized, would never textually equal or nest under a clean owned
    scope of `pkg/a.py`, making an actually-intersecting finding look
    DISJOINT and wrongly carry forward without re-review. Two fail-closed
    consequences:

      * a changed path that `_normalize_path_for_matching` cannot resolve
        (would escape the repo root) is treated as intersecting EVERY
        remaining clean finding — disjointness can never be asserted against
        a path this module cannot place inside the repo, so every such
        finding reopens (`CARRY_FORWARD_REASON_INTERSECTS`);
      * a `path_scope` entry that fails to normalize disqualifies the WHOLE
        `path_scope` the same way a blank entry already does (below) — the
        finding reopens with `CARRY_FORWARD_REASON_EMPTY_SCOPE` rather than
        silently dropping just that one entry (dropping it would narrow the
        finding's claimed scope and could turn a real intersection into a
        false disjoint).

    Every finding id lands in exactly one of `carried_forward_finding_ids` /
    `reopened_finding_ids`, OR neither (non-clean findings) — never in both.
    `reasons` records, per finding id, WHY it landed where it did (one of the
    `CARRY_FORWARD_REASON_*` constants) for audit."""
    normalized_delta: list[str] = []
    abnormal_delta_path = False
    for raw_path in delta_changed_paths:
        normalized = _normalize_path_for_matching(raw_path)
        if normalized is None:
            abnormal_delta_path = True
        else:
            normalized_delta.append(normalized)

    carried: list[str] = []
    reopened: list[str] = []
    reasons: dict[str, str] = {}
    for f in findings:
        if f.status != "clean":
            reasons[f.id] = CARRY_FORWARD_REASON_NOT_CLEAN
            continue
        if suppress:
            reopened.append(f.id)
            reasons[f.id] = CARRY_FORWARD_REASON_SUPPRESSED
            continue
        # agent-harness#191 CR (Lane C finding 3, empty-string path_scope
        # bypasses the empty-scope guard): the guard must fail closed on the
        # CONTENT of `path_scope`'s entries, not just the sequence's length.
        # `path_scope=("",)` (or `("  ",)`) is a non-empty SEQUENCE containing
        # an empty/whitespace-only entry — it passes `Finding.__post_init__`
        # and would pass a length-only `if not f.path_scope` check, but
        # `GitHubBrokerAdapter._covered_by_owned` treats an empty `owned`
        # entry (after its own `.rstrip("/")`) as falsy and SKIPS it
        # (credsep.py:204-208: `if owned and (...)`), so it never matches any
        # changed path — the finding would be classified DISJOINT and carried
        # forward without ever actually scoping anything. The check below
        # must mirror `_covered_by_owned`'s OWN emptiness test
        # (`.rstrip("/")`, not `.strip()`) — an all-slash entry such as `"/"`
        # or `"//"` is non-blank under `.strip()` but becomes `""` under
        # `.rstrip("/")` and is therefore ALSO silently skipped by the
        # matcher (a residual bypass a `.strip()`-only guard would miss).
        # `.strip().rstrip("/")` catches blank, whitespace-only, AND
        # all-slash entries while leaving legitimate directory scopes like
        # `"pkg/"` (-> `"pkg"`) untouched.
        if not f.path_scope or any(not entry.strip().rstrip("/") for entry in f.path_scope):
            reopened.append(f.id)
            reasons[f.id] = CARRY_FORWARD_REASON_EMPTY_SCOPE
            continue
        normalized_scope: list[str] = []
        abnormal_scope_entry = False
        for entry in f.path_scope:
            normalized_entry = _normalize_path_for_matching(entry)
            if normalized_entry is None:
                abnormal_scope_entry = True
                break
            normalized_scope.append(normalized_entry)
        if abnormal_scope_entry:
            reopened.append(f.id)
            reasons[f.id] = CARRY_FORWARD_REASON_EMPTY_SCOPE
            continue
        if abnormal_delta_path:
            intersects = True
        else:
            intersects = any(
                GitHubBrokerAdapter._covered_by_owned(p, tuple(normalized_scope)) for p in normalized_delta
            )
        if intersects:
            reopened.append(f.id)
            reasons[f.id] = CARRY_FORWARD_REASON_INTERSECTS
        else:
            carried.append(f.id)
            reasons[f.id] = CARRY_FORWARD_REASON_DISJOINT
    return CarryForwardResult(
        carried_forward_finding_ids=tuple(sorted(carried)),
        reopened_finding_ids=tuple(sorted(reopened)),
        reasons=reasons,
    )


def is_carry_forward_eligible(record: DeltaReviewRecord) -> bool:
    """design §5.2: "A delta is carry-forward-eligible ONLY at
    `status == reviewed-clean`."" A record's OWN `carried_forward_finding_ids`
    must only be trusted by a downstream (future) round when this is True —
    an `escalated-whole-patch`/`pending`/`invalidated` round's carry-forward
    output (if any was even computed) is not trustworthy for that purpose."""
    return record.status == DELTA_STATUS_REVIEWED_CLEAN


# --------------------------------------------------------------------------- #
# §5.3/T4 — resolved-finding-claim corroboration
# --------------------------------------------------------------------------- #


def require_seat_corroboration(finding_ids: Sequence[str], delta_round_seats: Sequence[ProvenanceSeat]) -> None:
    """T4: a finding id claimed as resolved (or, more generally, any finding
    id a delta round's record asserts seats acted on) requires that AT LEAST
    ONE seat record in `delta_round_seats` carries a non-null `verdict` (a
    real, terminal verdict was recorded — panel_invoker's own
    `_VALID_VERDICTS`, reused via `ProvenanceSeat.__post_init__`) AND lists
    that id in its `finding_ids`. Raises `ResolvedClaimUnverified`
    (fail-closed) listing every uncorroborated id — a claim with NO
    corroborating delta-round seat verdict is REJECTED, never silently
    accepted (design resolved-ambiguity #3: this checks "was it reviewed at
    all", not full-board consensus — that composition belongs to Lane D)."""
    covered: set[str] = set()
    for seat in delta_round_seats:
        if seat.verdict is None:
            continue
        covered.update(seat.finding_ids)
    missing = sorted(fid for fid in finding_ids if fid not in covered)
    if missing:
        raise ResolvedClaimUnverified(
            f"finding id(s) {missing!r} have no corroborating delta-round seat verdict (fail-closed, "
            "T4: a claim is not a resolution)"
        )


# --------------------------------------------------------------------------- #
# §5.5/T5 — review_scope enforcement for boundary-escalated rounds
# --------------------------------------------------------------------------- #


def enforce_review_scope_for_escalation(
    *, escalation: Escalation, review_scope: ReviewScope, covering_patch_digest: str | None
) -> None:
    """design §5.5/T5: for a boundary-escalated (`escalation.required=True`)
    round, require `review_scope.mode == "whole-patch"` AND
    `review_scope.covers_patch_digest == covering_patch_digest` — the digest
    of the FULL patch as of THIS round's head (see module resolved-ambiguity
    #2 for why this is not always literally `candidate.patch_digest`). A
    non-escalated round is NOT constrained by this function (it returns
    immediately) — `review_scope` for a plain delta-scoped round is Lane D's
    gate-composition concern.

    Raises `ReviewScopeRejected` (fail-closed) if `escalation.required` and
    EITHER the mode is not `whole-patch` OR the covered digest does not
    match — a boundary-escalated delta recording `delta-only` scope, or one
    whose whole-patch review covered a DIFFERENT patch state than this
    round's actual head, is rejected: escalation cannot be satisfied by a
    delta-scoped (or stale-whole-patch) round."""
    if not escalation.required:
        return
    if review_scope.mode != REVIEW_SCOPE_WHOLE_PATCH:
        raise ReviewScopeRejected(
            f"boundary-escalated delta recorded review_scope.mode={review_scope.mode!r} (fail-closed, "
            f"T5: must be {REVIEW_SCOPE_WHOLE_PATCH!r} — escalation cannot be satisfied by a delta-scoped round)"
        )
    if covering_patch_digest is None or review_scope.covers_patch_digest != covering_patch_digest:
        raise ReviewScopeRejected(
            "boundary-escalated delta's review_scope.covers_patch_digest does not match the full patch "
            f"digest as of this round's head (fail-closed, T5): expected={covering_patch_digest!r}, "
            f"got={review_scope.covers_patch_digest!r}"
        )


# --------------------------------------------------------------------------- #
# §5.2 — delta binding validator (deliverable 1)
# --------------------------------------------------------------------------- #


def validate_delta_binds_to_parent(
    record: DeltaReviewRecord,
    *,
    repo: Path,
    base_sha: str,
    repo_slug: str,
    parent_head_sha: str,
    parent_patch_digest: str | None,
    parent_chain_digest: str | None,
) -> None:
    """design §5.2: validate that `record` (a `DeltaReviewRecord`) genuinely
    binds to its claimed parent AND to live git state. Checks, in order, all
    fail-closed (raises `DeltaBindingInvalid` on the first failure):

      1. `record.chain_digest` self-recomputes (reuses Lane A's
         `DeltaReviewRecord.recompute_chain_digest` — not re-derived here);
      2. `record.parent_chain_digest == parent_chain_digest` (the prior
         round's `C_{i-1}`, contiguity — Lane A's `verify_chain` discipline);
      3. a `status == reviewed-clean` record carries a non-null
         `parent_digest` (Lane A's dual-link rule, unconditionally);
      4. `record.parent_digest == parent_patch_digest` whenever the parent
         side is known (Lane A's `verify_chain` "required whenever known,
         not only when present" rule, CR finding F2);
      5. `record.delta_changed_paths` (sorted) matches the LIVE `-z`
         changed-path set between `parent_head_sha` and `record.delta_head_sha`
         (`fab_canonical.enumerate_changed_paths`, byte-exact, reused);
      6. `record.resulting_head_digest` matches a LIVE
         `fab_canonical.patch_digest(repo, base_sha, record.delta_head_sha,
         repo_slug=repo_slug)` recompute — never trusted from the record
         itself (mirrors Lane B's `equivalent()` "never read from a client
         field" posture, T12)."""
    recomputed_chain = record.recompute_chain_digest()
    if recomputed_chain != record.chain_digest:
        raise DeltaBindingInvalid(
            f"delta record chain_digest does not recompute (recorded={record.chain_digest!r}, "
            f"recomputed={recomputed_chain!r}) — fabricated/tampered round (fail-closed)"
        )
    if record.parent_chain_digest != parent_chain_digest:
        raise DeltaBindingInvalid(
            f"delta record parent_chain_digest broken (expected={parent_chain_digest!r}, "
            f"got={record.parent_chain_digest!r}) — reordered/spliced round (fail-closed)"
        )
    if record.status == DELTA_STATUS_REVIEWED_CLEAN and record.parent_digest is None:
        raise DeltaBindingInvalid(
            f"delta record status={DELTA_STATUS_REVIEWED_CLEAN!r} but carries no parent_digest "
            "(fail-closed, dual-link contiguity: a reviewed-clean delta MUST carry a linking parent_digest)"
        )
    if parent_patch_digest is not None:
        if record.parent_digest != parent_patch_digest:
            raise DeltaBindingInvalid(
                f"delta record parent_digest broken (expected={parent_patch_digest!r}, "
                f"got={record.parent_digest!r}) — reordered/spliced round (fail-closed)"
            )

    live_changed = enumerate_changed_paths(repo, parent_head_sha, record.delta_head_sha)
    if tuple(sorted(record.delta_changed_paths)) != live_changed:
        raise DeltaBindingInvalid(
            "delta record delta_changed_paths does not match the live -z diff set between "
            f"parent_head_sha={parent_head_sha!r} and delta_head_sha={record.delta_head_sha!r} (fail-closed): "
            f"recorded={sorted(record.delta_changed_paths)!r}, live={list(live_changed)!r}"
        )

    live_digest = patch_digest(repo, base_sha, record.delta_head_sha, repo_slug=repo_slug)
    if record.resulting_head_digest != live_digest:
        raise DeltaBindingInvalid(
            f"delta record resulting_head_digest does not match a live recompute (fail-closed): "
            f"recorded={record.resulting_head_digest!r}, recomputed={live_digest!r}"
        )


# --------------------------------------------------------------------------- #
# High-level composition (analogous to Lane B's `equivalent()`) — builds a
# DeltaReviewRecord end to end from live git state + the review's outputs.
# --------------------------------------------------------------------------- #


def build_delta_round(
    *,
    epoch: int,
    repo: Path,
    base_sha: str,
    repo_slug: str,
    parent_head_sha: str,
    parent_patch_digest: str | None,
    parent_chain_digest: str | None,
    delta_head_sha: str,
    delta_commits: Sequence[str] = (),
    findings: Sequence[Finding],
    resolved_finding_ids: Sequence[str] = (),
    delta_round_seats: Sequence[ProvenanceSeat] = (),
    review_scope: ReviewScope,
    material_digests: Sequence[MaterialDigest] = (),
    manual_escalation_trigger: str | None = None,
    manifest_path: str = BOUNDARY_MANIFEST_PATH,
    status: str = DELTA_STATUS_REVIEWED_CLEAN,
) -> DeltaReviewRecord:
    """The Lane C end-to-end composition: enumerate this delta's changed
    paths, load+evaluate the base-pinned boundary manifest, decide
    escalation, run carry-forward (suppressed under escalation), corroborate
    `resolved_finding_ids` against `delta_round_seats` (T4 — see
    `require_seat_corroboration`'s docstring for why REOPENED findings are
    NOT also required here), enforce `review_scope` under escalation (T5),
    and construct the `DeltaReviewRecord` via Lane A's `DeltaReviewRecord.build`
    — passing the loaded `BoundaryManifestRef` as `policy` so its digest is
    folded into `chain_digest` (T15).

    `status` is caller-supplied (module resolved-ambiguity #4) and is
    validated for consistency: `escalation.required=True` can never coexist
    with `status="reviewed-clean"` (raises `EscalationInvalid`) — a
    whole-patch-escalated round must be recorded as
    `escalated-whole-patch`/`pending`/`invalidated`, never as a narrower
    carry-forward-eligible clean delta."""
    delta_changed_paths = enumerate_changed_paths(repo, parent_head_sha, delta_head_sha)

    manifest_load = load_boundary_manifest_at_base(repo, base_sha, path=manifest_path)
    escalation = evaluate_boundary_escalation(
        manifest_load, delta_changed_paths, manual_trigger=manual_escalation_trigger
    )

    if escalation.required and status == DELTA_STATUS_REVIEWED_CLEAN:
        raise EscalationInvalid(
            "escalation.required=True but status='reviewed-clean' is contradictory (fail-closed, T5 shape): "
            "a boundary-escalated round is whole-patch and cannot be recorded as a narrower "
            "carry-forward-eligible clean delta"
        )
    if status not in (
        DELTA_STATUS_REVIEWED_CLEAN,
        DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        DELTA_STATUS_PENDING,
        DELTA_STATUS_INVALIDATED,
    ):
        raise EscalationInvalid(f"unknown delta status (fail-closed, never inferred from prose): {status!r}")

    cf = carry_forward(findings, delta_changed_paths, suppress=escalation.required)

    # T4/§5.3 (agent-harness#191 CR, Lane C finding 4 — "reopened findings not
    # seat-corroborated" fail-open): design §5.3's full sentence is "the delta
    # round's seats must return a verdict on exactly those [resolved_finding_
    # ids] plus every re-opened finding." A RESOLVED claim always requires
    # corroboration (unconditionally, any status). A REOPENED finding also
    # requires corroboration, but ONLY when this round is being recorded
    # `status=="reviewed-clean"`: module resolved-ambiguity #3 still holds for
    # `escalated-whole-patch`/`pending`/`invalidated` rounds — those are, by
    # definition, still going BACK into review (there is nothing yet to
    # corroborate, and requiring it would wrongly demand seat verdicts before
    # the reopened round has even run). But a NON-escalated delta that
    # reopens a clean finding (its `path_scope` intersects the delta) can be
    # recorded `status="reviewed-clean"` with ZERO delta-round seats and still
    # pass `is_carry_forward_eligible` — the reopened finding would never
    # actually be re-reviewed. Requiring corroboration here, at construction
    # time, closes that: a `reviewed-clean` round with an uncorroborated
    # reopened finding is REJECTED (`ResolvedClaimUnverified`), never silently
    # accepted.
    require_seat_corroboration(tuple(resolved_finding_ids), delta_round_seats)
    if status == DELTA_STATUS_REVIEWED_CLEAN:
        require_seat_corroboration(cf.reopened_finding_ids, delta_round_seats)

    resulting_head_digest = patch_digest(repo, base_sha, delta_head_sha, repo_slug=repo_slug)
    enforce_review_scope_for_escalation(
        escalation=escalation, review_scope=review_scope, covering_patch_digest=resulting_head_digest
    )

    return DeltaReviewRecord.build(
        epoch=epoch,
        policy=manifest_load.ref.to_dict(),
        review_scope=review_scope,
        material_digests=material_digests,
        parent_digest=parent_patch_digest,
        parent_chain_digest=parent_chain_digest,
        delta_head_sha=delta_head_sha,
        delta_changed_paths=delta_changed_paths,
        delta_commits=delta_commits,
        resolved_finding_ids=resolved_finding_ids,
        carried_forward_finding_ids=cf.carried_forward_finding_ids,
        reopened_finding_ids=cf.reopened_finding_ids,
        resulting_head_digest=resulting_head_digest,
        status=status,
        escalation=escalation,
        # agent-harness#191 CR / Lane D finding 1: persist the SAME seats just
        # corroborated above onto the record itself (previously discarded after
        # this one-time construction-time check) so Lane D's gate can
        # independently re-authenticate + re-corroborate them per round, not
        # just trust that this constructor was actually the one that built the
        # record it is now reading.
        delta_round_seats=tuple(delta_round_seats),
    )
