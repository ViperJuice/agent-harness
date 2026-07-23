"""FAB (Consiliency/agent-harness#191) Lane B — canonical bytes, equivalence,
and hostile-git hardening.

Ground: `plans/design-fab-191-delta-review.md` (v2, panel-reviewed) §3 (canonical
bytes), §4 (equivalence / base binding / invalidation). Builds on Lane A
(`fab_provenance.py` — frozen schemas, `ProvenanceInvalid`, `EquivalenceResult`,
the `EQUIVALENCE_EQUIVALENT`/`EQUIVALENCE_INVALIDATED` constants). This module
implements ONLY Lane B: it does NOT build the delta-chain/carry-forward/
escalation decision logic (design §5 — Lane C) and does NOT wire a gate or a
merge-time promotion re-assertion (design §8/§4.4 — Lane D). `equivalent()` is
written to be directly reusable by Lane D for that re-assertion, per design §9's
Lane B bullet ("make `equivalent()` reusable so Lane D can call it").

PRE-STATED TRUST BOUNDARY (mirrors Lane A §6.1a / agent-harness#276): `git` is
TRUSTED CI plumbing. The in-scope attacker controls repo CONTENTS — branch
commits, blob bytes, paths, submodule (gitlink) pointers — NOT the bytes of the
trusted git binary's own stdout. Every hardening decision below follows from
this split: we PARSE git's documented output formats (raw `-z` diff records,
`cat-file --batch` headers) rather than defending against the trusted binary
lying about its own format, and we harden against what the attacker actually
controls: a crafted/replayed `refs/replace` graft (`--no-replace-objects` +
`GIT_NO_REPLACE_OBJECTS=1` on every call), a reported sha1 OID that does not
match the real blob bytes (we compute our OWN SHA-256 of the bytes, never trust
git's `<new_oid>`), a mode/type swap (100644<->100755, file<->symlink,
file<->gitlink) that leaves content bytes unchanged, a deletion hidden by
omitting its record, and a nonzero git return code silently treated as "no
changes". `base_sha` binding is the load-bearing pin that makes hashing only the
new side of a diff sound (§3.4) — see the frozen invariant below.

FROZEN INTERFACE (IF-0-FAB-B-1) — C/D code against this without renegotiation:

  * **`patch_digest` binary format** (design §3.2-§3.5): SHA-256 over
        header = b"fab.canonical-bytes.v2\\0" + repo_slug_utf8 + b"\\0" + base_sha_ascii + b"\\0"
    followed by, for every changed path in RAW-PATH-BYTE sort order, the record
        <status ascii> \\0 <new_mode ascii> \\0 <content_sha256 hex ascii> \\0 <raw path bytes> \\0
    where `status` in {A, M, D, T} (unknown/other status -> fail-closed
    `PatchDigestInvalid`); a DELETE forces `new_mode = "000000"` and
    `content_sha256 = "-"` (a sentinel that can never collide with a real
    64-hex-char digest); `content_sha256` for every other status is OUR OWN
    `hashlib.sha256` over the actual blob bytes read via `git cat-file --batch`
    — the diff's reported `<new_oid>` (git's sha1) is used ONLY as the object
    address to fetch, never copied in as the content identity.

  * **Hash-only-new-side invariant (load-bearing, design §3.4).** The record
    stream carries no old-side bytes at all. This is sound ONLY because the old
    side of the diff is a PURE FUNCTION of `(base_sha, path)` and `base_sha` is
    itself pinned into the header AND independently re-verified at gate time
    (design §4.3 — `equivalent()`'s base-ref-identity + fresh-fetch +
    recomputed-merge-base check, below). If `base_sha` were accepted unpinned or
    unverified, hashing only the new side would be a fail-OPEN (a forged/rebased
    base could contribute unreviewed old-side bytes with nothing in the digest
    to catch it). Any code path that computes a `patch_digest` without an
    independently verified `base_sha` binding upstream reintroduces this hole —
    `equivalent()` is the ONLY place in this module that is allowed to trust a
    caller-supplied `base_sha` as reviewed, and only after re-deriving it live.

  * **rc!=0 is ALWAYS invalid, never "no changes"** (design §3.2 finding 6,
    corrects v1's `{0,1}` fail-open): every git subprocess this module runs is
    accepted ONLY on `returncode == 0`; any nonzero code — including the classic
    "differences present" `1` some git plumbing uses under `--exit-code` (which
    this module never passes) — raises `PatchDigestInvalid`. An empty raw-diff
    stream on a genuine `rc==0` (base and head are identical) is a legitimate
    zero-record digest; a nonzero rc is NEVER interpreted as "no changes".

  * **Gitlinks (mode `160000`) default REJECT** (design §3.3 finding 6): a
    changed path whose NEW mode is `160000` raises `GitlinkRejected` (a
    `PatchDigestInvalid` subclass) by default — a bare gitlink commit OID rests
    on git's own sha1 and is never accepted as content identity. `allow_gitlinks`
    is an explicit opt-in stub for a future recursive-submodule-canonicalize
    path (design §3.3 "opt-in"); passing it True today raises
    `PatchDigestInvalid` (not implemented) rather than silently accepting a
    gitlink's OID as content — there is no "quietly less strict" mode.

  * **`equivalent()` result/reason codes**: returns `fab_provenance.EquivalenceResult`
    (Lane A's frozen type — reused, not re-implemented) with
    `result in {EQUIVALENCE_EQUIVALENT, EQUIVALENCE_INVALIDATED}` and, on
    invalidation, a `reason` string with one of these STABLE PREFIXES (an
    optional `:<detail>` suffix carries a non-normative diagnostic and MUST NOT
    be pattern-matched by callers — only the prefix is frozen):
      - `"repo_identity_unresolvable"` — the live repo's origin could not be
        resolved to a host-qualified slug (§4 precondition 1).
      - `"repo_mismatch"` — live repo slug != the bound `repo_slug` (§4
        precondition 1, T10).
      - `"base_ref_retargeted"` — the caller-supplied live base ref name !=
        the bound `base_ref_name` (§4.3, I1 retarget case).
      - `"git_error"` — a git subprocess (`fetch`, `merge-base`, or the
        `patch_digest` recompute's own diff/cat-file calls) failed or returned
        a malformed/missing result (§4.5 I9, T17).
      - `"gitlink_rejected"` — the live content recompute hit a gitlink path
        under the default reject policy (§3.3 finding 6).
      - `"base_sha_mismatch"` — the freshly recomputed
        `merge-base(fetch_head, live_head)` != the bound `base_sha` (§4.2/§4.3;
        covers rebase / retarget-with-coincidental-name / conflict-resolution-
        committed-on-head / force-rewrite, I1/I2/I4).
      - `"content_drift"` — the live `patch_digest` != the bound
        `expected_head_digest`, with no upstream git/gitlink error (T2/T3/T6/T8/
        T11 manifest as this).
    NOT invalidated (design §4.5 I10/I11 — explicitly PASS, not fail-opens under
    delta identity, see design §4.2): commit-topology churn with identical net
    content, and a base-tip advance `B -> B'` while the PR itself is unchanged.
    Both fall out of the same three checks above with no special-casing — see
    the module's test suite for the worked cases.

Design ambiguities resolved in this lane (stated once, not re-litigated):

  1. **`patch_digest`'s `repo_slug` is resolved, not required positionally.**
     The task-level signature is `patch_digest(repo, base_sha, head_sha)`; the
     binary header also needs a `repo_slug` (design §3.4). Rather than force
     every caller to pre-resolve it, `patch_digest` takes an optional
     keyword-only `repo_slug: str | None = None` and, when omitted, resolves it
     itself via `credsep.resolve_broker_repo_identity(repo)` — REUSING the
     #250 host-qualified slug resolver (`convergence/broker/credsep.py`), never
     re-implementing URL parsing. `equivalent()` always resolves the LIVE slug
     itself (never accepts a caller-supplied "live slug" — that would defeat
     precondition 1) and threads the BOUND slug through to its own
     `patch_digest` recompute so the two never diverge.

  2. **`equivalent()`'s input is a narrow `EquivalenceBinding`, not the full
     Lane A artifact.** Design §4 says `equivalent(reviewed_artifact, live_pr)`;
     resolving "the chain's `expected_head_digest`" from a full
     `ReviewProvenanceArtifact` requires walking `delta_chain` and applying
     carry-forward/escalation rules — Lane C's job, explicitly out of scope
     here (design §9: Lane B "dep A", Lane C "dep A, B"). `EquivalenceBinding`
     is the minimal, already-resolved shape `equivalent()` needs (repo slug,
     base ref name, base sha, expected head digest, plus audit-only head SHAs);
     Lane C/D construct it after resolving the chain. A convenience
     `EquivalenceBinding.from_provenance_artifact()` degenerate-case
     constructor is provided for the empty-`delta_chain` case (design §6.5
     "exact-head... degenerate supported case") and fails closed (raises) if
     the artifact HAS a delta chain, rather than guessing which round governs.

  3. **The live base ref's identity check is caller-supplied, not
     git-discovered.** Design §4.3's "retargeted base" scenario (PR base
     changed `main -> release/2.0`) is a fact about a live pull request, which
     this module has no notion of (no GitHub/host API calls here — Lane D's
     job). `equivalent()` therefore takes a required `live_base_ref_name`
     keyword argument the caller resolves from the live PR/host state and
     compares it against the bound `EquivalenceBinding.base_ref_name`; Lane B's
     own tests exercise the retarget check by passing a literal different
     value, matching how Lane D will pass the live PR's actual current base
     ref name.

  4. **Per-path `content_drift` diagnostics are not path-scoped in Lane B.**
     The design's illustrative gate-status reason `"content_drift:<path>"`
     (§8) is scoped to the COMPOSED `fab.gate-status.v2` record, which Lane D
     assembles; Lane B's `equivalent()` returns the coarser `"content_drift"`
     (no path) since attributing drift to a specific path would require a
     second diff pass purely for diagnostics. Lane D may enrich this using the
     same `patch_digest` machinery if desired; Lane B does not overreach into
     that presentation concern.

Fail-closed discipline (this is a security trust root, same posture as Lane A):
unknown status characters, malformed raw-diff/cat-file records, truncated
streams, nonzero git return codes, missing/malformed `cat-file` responses, and
gitlink paths under the default policy all raise a typed `PatchDigestInvalid`
(a `fab_provenance.ProvenanceInvalid` subclass) from `patch_digest`; `equivalent()`
converts these (and its own base-ref/merge-base checks) into a typed
`EQUIVALENCE_INVALIDATED` result rather than ever silently returning
`EQUIVALENCE_EQUIVALENT` on an unrecognized state.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .convergence.broker.credsep import resolve_broker_repo_identity
from .fab_provenance import (
    EQUIVALENCE_EQUIVALENT,
    EQUIVALENCE_INVALIDATED,
    EquivalenceResult,
    ProvenanceInvalid,
    ReviewProvenanceArtifact,
)

# --------------------------------------------------------------------------- #
# Frozen constants (IF-0-FAB-B-1)
# --------------------------------------------------------------------------- #

CANONICAL_BYTES_HEADER_PREFIX = b"fab.canonical-bytes.v2\x00"

# design §3.3: the delete sentinel — a literal "-", never a valid 64-hex sha256,
# so a delete record can never collide with an add/modify of the same path.
DELETED_CONTENT_SENTINEL = "-"
DELETED_NEW_MODE = "000000"

# design §3.2: status letters this module accepts. `--no-renames` guarantees
# git never emits R/C; U (unmerged) only appears in working-tree/index diffs,
# never in a commit-to-commit `diff <base> <head>`. Anything else -> fail-closed.
_VALID_STATUSES = frozenset({"A", "M", "D", "T"})

_GITLINK_MODE = "160000"

_OID_RE = re.compile(rb"^[0-9a-f]{4,64}$")
# base_sha/head_sha must be a real, resolved object id — not revision syntax
# (`HEAD~5`, `main^`, branch names) — so the header's base_sha binding is a
# frozen point-in-time value, not a moving target re-resolved differently by
# a later call. 40 hex chars (sha1) or 64 (sha256 object format, design open
# question 3) are both accepted.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

_DIFF_TIMEOUT_SECONDS = 120
_CAT_FILE_TIMEOUT_SECONDS = 300
_GIT_GENERIC_TIMEOUT_SECONDS = 60
# design §3.3: streamed, bounded chunks (mirrors #114's 1 MiB convention, also
# used by fab_provenance._stream_sha256) — never buffer a whole blob in memory.
_CAT_FILE_CHUNK_BYTES = 1 << 20

# design §8 (adapted for Lane B's narrower equivalent()) — STABLE reason-code
# prefixes; an optional ":<detail>" suffix is diagnostic-only and unfrozen.
REASON_REPO_IDENTITY_UNRESOLVABLE = "repo_identity_unresolvable"
REASON_REPO_MISMATCH = "repo_mismatch"
REASON_BASE_REF_RETARGETED = "base_ref_retargeted"
REASON_GIT_ERROR = "git_error"
REASON_GITLINK_REJECTED = "gitlink_rejected"
REASON_BASE_SHA_MISMATCH = "base_sha_mismatch"
REASON_CONTENT_DRIFT = "content_drift"


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class PatchDigestInvalid(ProvenanceInvalid):
    """Fail-closed sentinel for `patch_digest`/`equivalent()`: raised whenever
    git returns a nonzero code, an unparseable/truncated record, a missing
    `cat-file` object, or an out-of-policy path (gitlink). A `ProvenanceInvalid`
    subclass so Lane A/B share one catchable trust-root exception family."""


class GitlinkRejected(PatchDigestInvalid):
    """A changed path's NEW mode is `160000` (gitlink) under the default
    reject policy (design §3.3 finding 6) — the content of a submodule bump
    rests on git's own sha1 commit-OID addressing and is never accepted as
    content identity without explicit, out-of-band review."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _validate_full_sha(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _FULL_SHA_RE.match(value):
        raise PatchDigestInvalid(
            f"{field_name} must be a full, resolved object id (40 or 64 lowercase hex chars), "
            f"not revision syntax or a ref name (fail-closed): {value!r}"
        )
    return value


def _git_env() -> dict[str, str]:
    # design §3.2/§4.5 (T17): belt-and-suspenders hostile-git hardening — the
    # `--no-replace-objects` CLI flag AND the GIT_NO_REPLACE_OBJECTS=1 env var,
    # on every single git invocation this module makes, so a `refs/replace/*`
    # graft can never silently substitute a different tree/blob at read time.
    env = dict(os.environ)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _run_git(repo: Path, *args: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            capture_output=True,
            env=_git_env(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PatchDigestInvalid(
            f"git {' '.join(args)!r} failed to execute in {repo} (fail-closed): {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# §3.2 — enumerate changed paths (reuse the #250 bytes/fsdecode discipline;
# extended from `--name-only` to `--raw` for per-path mode + status + oid)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _RawDiffEntry:
    status: str
    new_mode: str
    new_oid: bytes  # ascii hex, kept as bytes (used only as a cat-file key)
    path: bytes  # raw `-z` path bytes — never decoded/re-encoded (design §3.3)


def _git_diff_raw_bytes(repo: Path, base_sha: str, head_sha: str) -> bytes:
    """§3.2: `GIT_NO_REPLACE_OBJECTS=1 git -C <repo> --no-replace-objects diff
    --no-renames --no-color -z --raw --abbrev=40 <base_sha> <head_sha>`.

    Capture as BYTES (no `text=True`) — this is the SAME bytes-capture
    discipline as `train_runner._prebuilt_owned_paths` (agent-harness#250,
    train_runner.py ~lines 257-287) and `credsep.GitHubBrokerAdapter.
    _branch_diff_paths`, extended from `--name-only` to `--raw` (per-path mode
    + status + oid, not just the path). ONLY `rc == 0` is accepted (design §3.2
    finding 6 — corrects v1's fail-open `{0,1}` acceptance); any nonzero code
    is a `PatchDigestInvalid`, never treated as an empty/no-changes result."""
    completed = _run_git(
        repo,
        "diff",
        "--no-renames",
        "--no-color",
        "-z",
        "--raw",
        "--abbrev=40",
        base_sha,
        head_sha,
        timeout=_DIFF_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        stderr_text = os.fsdecode(completed.stderr).strip() if completed.stderr else ""
        raise PatchDigestInvalid(
            f"git diff {base_sha}..{head_sha} returned rc={completed.returncode} "
            f"(fail-closed, ONLY rc==0 accepted — design §3.2 finding 6): {stderr_text or '<no stderr>'}"
        )
    return completed.stdout


def _iter_raw_diff_entries(raw: bytes) -> list[_RawDiffEntry]:
    """Parse `git diff --raw -z` bytes into `_RawDiffEntry` records.

    With `-z`, each record is `:<old_mode> <new_mode> <old_oid> <new_oid>
    <status>\\0<path>\\0` (space-separated metadata fields, then a NUL, then the
    raw path bytes, then a NUL — confirmed against a live `git diff --raw -z`
    invocation; no rename/copy score suffix is possible because `--no-renames`
    is always passed). Any malformed/truncated/unexpected-status record raises
    `PatchDigestInvalid` (fail-closed) — no partial/best-effort parse."""
    tokens = raw.split(b"\x00")
    if tokens and tokens[-1] == b"":
        tokens = tokens[:-1]
    entries: list[_RawDiffEntry] = []
    i = 0
    n = len(tokens)
    while i < n:
        meta = tokens[i]
        i += 1
        if not meta.startswith(b":"):
            raise PatchDigestInvalid(f"malformed raw-diff metadata token (fail-closed): {meta!r}")
        if i >= n:
            raise PatchDigestInvalid("truncated raw-diff stream: metadata with no following path (fail-closed)")
        path = tokens[i]
        i += 1
        fields = meta.split(b" ")
        if len(fields) != 5:
            raise PatchDigestInvalid(f"malformed raw-diff metadata (expected 5 fields, fail-closed): {meta!r}")
        old_mode_b, new_mode_b, old_oid_b, new_oid_b, status_b = fields
        if not old_mode_b.startswith(b":"):
            raise PatchDigestInvalid(f"malformed raw-diff old-mode field (fail-closed): {meta!r}")
        try:
            status = status_b.decode("ascii")
            new_mode = new_mode_b.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PatchDigestInvalid(f"non-ASCII raw-diff metadata field (fail-closed): {meta!r}") from exc
        if status not in _VALID_STATUSES:
            raise PatchDigestInvalid(
                f"unexpected raw-diff status {status!r} for {path!r} (fail-closed, only "
                f"{sorted(_VALID_STATUSES)} accepted)"
            )
        if not _OID_RE.match(new_oid_b):
            raise PatchDigestInvalid(f"malformed raw-diff new-oid field (fail-closed): {new_oid_b!r}")
        entries.append(_RawDiffEntry(status=status, new_mode=new_mode, new_oid=new_oid_b, path=path))
    return entries


# --------------------------------------------------------------------------- #
# §3.3 — our own SHA-256 of the actual blob bytes at head (never git's OID)
# --------------------------------------------------------------------------- #


def _cat_file_content_hashes(repo: Path, oids: Sequence[bytes]) -> dict[bytes, str]:
    """§3.3: for each `oid` in `oids`, stream its blob bytes via
    `GIT_NO_REPLACE_OBJECTS=1 git cat-file --batch` and return OUR OWN
    `hashlib.sha256` hex digest of the bytes — never the oid itself (T3: git's
    reported sha1 is used ONLY as the object address to fetch). Streamed in
    bounded ~1 MiB chunks (never buffers a whole blob). A "missing" or
    malformed `cat-file` response line, a truncated content stream, or a
    missing trailing newline all raise `PatchDigestInvalid` (fail-closed,
    design §3.3 finding 6) — never a silently-empty/partial hash map.

    `cat-file --batch` emits responses in the SAME order objects were written
    to its stdin (git's own documented guarantee), so this reads sequentially
    without needing to correlate by echoed oid — but it still asserts the
    echoed oid matches the requested one as defense in depth against any
    stream desync."""
    if not oids:
        return {}
    try:
        proc = subprocess.Popen(
            ["git", "--no-replace-objects", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=_git_env(),
        )
    except OSError as exc:
        raise PatchDigestInvalid(f"failed to start git cat-file --batch in {repo} (fail-closed): {exc}") from exc

    writer_error: list[BaseException] = []

    def _writer() -> None:
        try:
            assert proc.stdin is not None
            for oid in oids:
                proc.stdin.write(oid + b"\n")
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:  # pragma: no cover - depends on child timing
            writer_error.append(exc)

    thread = threading.Thread(target=_writer, daemon=True)
    thread.start()

    results: dict[bytes, str] = {}
    assert proc.stdout is not None
    stdout = proc.stdout
    try:
        for requested_oid in oids:
            header_line = stdout.readline()
            if not header_line:
                raise PatchDigestInvalid(
                    f"cat-file --batch stream ended before object {requested_oid!r} was returned "
                    "(fail-closed, design §3.3 finding 6: missing object)"
                )
            header_line = header_line.rstrip(b"\n")
            parts = header_line.split(b" ")
            if len(parts) == 2 and parts[1] == b"missing":
                raise PatchDigestInvalid(
                    f"cat-file reports object missing (fail-closed, design §3.3 finding 6): {requested_oid!r}"
                )
            if len(parts) != 3:
                raise PatchDigestInvalid(
                    f"malformed cat-file --batch header for {requested_oid!r} (fail-closed): {header_line!r}"
                )
            returned_oid, _obj_type, size_field = parts
            if returned_oid != requested_oid:
                raise PatchDigestInvalid(
                    f"cat-file --batch response desync (fail-closed): requested {requested_oid!r}, "
                    f"got header for {returned_oid!r}"
                )
            try:
                size = int(size_field)
                if size < 0:
                    raise ValueError("negative size")
            except ValueError as exc:
                raise PatchDigestInvalid(
                    f"malformed cat-file --batch size field for {requested_oid!r} (fail-closed): {size_field!r}"
                ) from exc
            digest = hashlib.sha256()
            remaining = size
            while remaining > 0:
                chunk = stdout.read(min(_CAT_FILE_CHUNK_BYTES, remaining))
                if not chunk:
                    raise PatchDigestInvalid(
                        f"cat-file --batch content stream truncated for {requested_oid!r} (fail-closed)"
                    )
                digest.update(chunk)
                remaining -= len(chunk)
            trailing = stdout.read(1)
            if trailing != b"\n":
                raise PatchDigestInvalid(
                    f"cat-file --batch missing trailing newline for {requested_oid!r} (fail-closed)"
                )
            results[requested_oid] = digest.hexdigest()
    finally:
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:  # pragma: no cover
            pass
        thread.join(timeout=_CAT_FILE_TIMEOUT_SECONDS)
        try:
            stdout.close()
        except OSError:  # pragma: no cover
            pass
        try:
            proc.wait(timeout=_CAT_FILE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=_CAT_FILE_TIMEOUT_SECONDS)
    if writer_error:
        raise PatchDigestInvalid(f"cat-file --batch stdin writer failed (fail-closed): {writer_error[0]}")
    return results


# --------------------------------------------------------------------------- #
# §3.3/§3.4 — the per-path record + the ONE binary encoding
# --------------------------------------------------------------------------- #


def _build_records(repo: Path, entries: Sequence[_RawDiffEntry], *, allow_gitlinks: bool) -> list[tuple[bytes, bytes]]:
    """Build the §3.3 per-path binary records. Returns `(path_bytes,
    record_bytes)` pairs, UNSORTED (the caller sorts by raw path bytes —
    design §3.3 "sorted by raw path bytes"). Raises `GitlinkRejected` the
    moment a gitlink mode is seen, BEFORE any cat-file work — a rejected patch
    never needs its content hashed."""
    for entry in entries:
        if entry.new_mode == _GITLINK_MODE:
            if not allow_gitlinks:
                raise GitlinkRejected(
                    f"gitlink (mode {_GITLINK_MODE}) change at {entry.path!r} rejected by default policy "
                    "(design §3.3 finding 6: a submodule commit OID rests on git's own sha1 and is never "
                    "accepted as content identity — force whole-patch escalation instead)"
                )
            raise PatchDigestInvalid(
                "allow_gitlinks=True requested recursive submodule canonicalization, which is a "
                "documented STUB only (design §3.3 'opt-in' path) — not implemented; there is no "
                "silently-less-strict mode for gitlinks"
            )

    need_hash = [entry.new_oid for entry in entries if entry.status != "D"]
    hashes = _cat_file_content_hashes(repo, need_hash)

    records: list[tuple[bytes, bytes]] = []
    for entry in entries:
        if entry.status == "D":
            content_hex = DELETED_CONTENT_SENTINEL
            new_mode = DELETED_NEW_MODE
        else:
            content_hex = hashes[entry.new_oid]
            new_mode = entry.new_mode
        record = (
            entry.status.encode("ascii")
            + b"\x00"
            + new_mode.encode("ascii")
            + b"\x00"
            + content_hex.encode("ascii")
            + b"\x00"
            + entry.path
            + b"\x00"
        )
        records.append((entry.path, record))
    return records


def patch_digest(
    repo: Path,
    base_sha: str,
    head_sha: str,
    *,
    repo_slug: str | None = None,
    allow_gitlinks: bool = False,
) -> str:
    """design §3.2-§3.5 — the ONE canonical binary encoding of "what this PR
    contributes over `base_sha`" (delta identity, §3.1). SHA-256 over:

        header  = b"fab.canonical-bytes.v2\\0" + repo_slug_utf8 + b"\\0" + base_sha_ascii + b"\\0"
        stream  = header + concat(record for path in sorted(changed_paths, key=raw_bytes))

    See the module docstring (IF-0-FAB-B-1) for the full frozen record format,
    the hash-only-new-side invariant, the gitlink/rc==0/cat-file-missing
    dispositions, and resolved ambiguity #1 (why `repo_slug` is optional here).

    `base_sha`/`head_sha` MUST be full, resolved object ids (not revision
    syntax or ref names) — enforced by `_validate_full_sha` so the header's
    `base_sha` binding is a frozen point-in-time value (design §3.4 T1/T10).
    Raises `PatchDigestInvalid` (or `GitlinkRejected`) fail-closed on any
    hostile/ambiguous/malformed git state; never returns a digest for a state
    it could not fully and unambiguously canonicalize."""
    repo = Path(repo)
    base_sha = _validate_full_sha(base_sha, field_name="base_sha")
    head_sha = _validate_full_sha(head_sha, field_name="head_sha")
    if repo_slug is None:
        repo_slug = resolve_broker_repo_identity(repo)

    raw = _git_diff_raw_bytes(repo, base_sha, head_sha)
    entries = _iter_raw_diff_entries(raw)
    records = _build_records(repo, entries, allow_gitlinks=allow_gitlinks)
    records.sort(key=lambda item: item[0])  # design §3.3: sorted by RAW path bytes

    header = CANONICAL_BYTES_HEADER_PREFIX + repo_slug.encode("utf-8") + b"\x00" + base_sha.encode("ascii") + b"\x00"
    stream = header + b"".join(record for _path, record in records)
    return hashlib.sha256(stream).hexdigest()


# --------------------------------------------------------------------------- #
# §4 — equivalence, base binding, invalidation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, kw_only=True)
class EquivalenceBinding:
    """The minimal, already-resolved binding `equivalent()` needs (design §4;
    resolved ambiguity #2 — deliberately narrower than the full Lane A
    artifact, whose delta-chain resolution is Lane C's job)."""

    repo_slug: str
    base_ref_name: str
    base_sha: str
    expected_head_digest: str
    candidate_head_sha: str | None = None
    delta_head_shas: tuple[str, ...] = ()

    @classmethod
    def from_provenance_artifact(cls, artifact: ReviewProvenanceArtifact) -> "EquivalenceBinding":
        """Degenerate-case convenience (design §6.5 "exact-head... degenerate
        supported case") for an artifact with an EMPTY `delta_chain` — resolves
        directly from `artifact.candidate`. Fails closed (raises
        `PatchDigestInvalid`) if the artifact HAS a delta chain: picking the
        correct governing round across carry-forward/escalation is Lane C's
        logic, not something this constructor may guess at."""
        if artifact.delta_chain:
            raise PatchDigestInvalid(
                "EquivalenceBinding.from_provenance_artifact: artifact has a non-empty delta_chain — "
                "resolving the correct 'expected_head_digest' across delta rounds is Lane C's "
                "carry-forward/escalation logic (out of scope in Lane B); construct EquivalenceBinding "
                "explicitly instead of guessing (fail-closed)"
            )
        if artifact.candidate.patch_digest is None:
            raise PatchDigestInvalid(
                "EquivalenceBinding.from_provenance_artifact: candidate.patch_digest is not yet computed"
            )
        ref_identity = artifact.base.ref_identity
        repo_slug, sep, ref_name = ref_identity.partition("#")
        if not sep or not repo_slug or not ref_name:
            raise PatchDigestInvalid(
                f"EquivalenceBinding.from_provenance_artifact: malformed base ref_identity "
                f"(expected '<repo_slug>#<ref_name>', fail-closed): {ref_identity!r}"
            )
        return cls(
            repo_slug=repo_slug,
            base_ref_name=ref_name,
            base_sha=artifact.base.base_sha,
            expected_head_digest=artifact.candidate.patch_digest,
            candidate_head_sha=artifact.candidate.head_sha,
            delta_head_shas=(),
        )


def _invalidated(
    *,
    expected_head_digest: str | None,
    observed_head_digest: str | None,
    live_base_sha: str | None,
    final_pr_head_sha: str | None,
    reason: str,
) -> EquivalenceResult:
    return EquivalenceResult(
        result=EQUIVALENCE_INVALIDATED,
        expected_head_digest=expected_head_digest,
        observed_head_digest=observed_head_digest,
        reason=reason,
        live_base_sha=live_base_sha,
        final_pr_head_sha=final_pr_head_sha,
    )


def equivalent(
    binding: EquivalenceBinding,
    repo: Path,
    *,
    live_base_ref_name: str,
    live_head_sha: str,
    origin: str = "origin",
) -> EquivalenceResult:
    """design §4: `equivalent(reviewed_artifact, live_pr)`, realized here as
    `equivalent(binding, repo, live_base_ref_name=..., live_head_sha=...)` —
    see module docstring resolved ambiguities #2/#3 for why the signature is
    shaped this way. Checks, IN ORDER, each fail-closed:

      1. **Repo identity** (§4 precondition 1): the LIVE repo's origin resolves
         (via the SAME #250 `resolve_broker_repo_identity` Lane A/broker use)
         to `binding.repo_slug`.
      2. **Base ref identity + freshness** (§4.3): `live_base_ref_name ==
         binding.base_ref_name` (a retarget is a DIFFERENT ref identity, I1);
         `git fetch --no-tags <origin> <live_base_ref_name>` (fresh — a stale
         local ref must not decide this); `merge-base(FETCH_HEAD, live_head_sha)
         == binding.base_sha` (rebase / conflict-resolution-committed-on-head /
         force-rewrite all relocate the merge-base and are caught here — I1/I2/
         I4; a bare base-tip advance with the PR unchanged does NOT change the
         merge-base and correctly passes — I11, design §4.2).
      3. **Content equivalence** (§4 precondition 4): `patch_digest(repo,
         binding.base_sha, live_head_sha, repo_slug=binding.repo_slug)`,
         recomputed LIVE (never read from a client-supplied field — T12) ==
         `binding.expected_head_digest`.

    Every git call carries `--no-replace-objects` + `GIT_NO_REPLACE_OBJECTS=1`
    (via `_run_git`/`patch_digest`'s own calls); any `rc != 0` at any stage
    invalidates. NEVER raises for an in-scope fail-closed condition — always
    returns a typed `EquivalenceResult`; only a genuine programming-error
    exception (not one of this module's/Lane A's typed exceptions) propagates."""
    repo = Path(repo)

    try:
        live_slug = resolve_broker_repo_identity(repo)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any resolution failure invalidates
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_REPO_IDENTITY_UNRESOLVABLE}: {exc}",
        )
    if live_slug != binding.repo_slug:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_REPO_MISMATCH}: bound={binding.repo_slug!r} live={live_slug!r}",
        )

    if live_base_ref_name != binding.base_ref_name:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_BASE_REF_RETARGETED}: bound={binding.base_ref_name!r} live={live_base_ref_name!r}",
        )

    fetch = _run_git(repo, "fetch", "--no-tags", origin, live_base_ref_name, timeout=_GIT_GENERIC_TIMEOUT_SECONDS)
    if fetch.returncode != 0:
        stderr_text = os.fsdecode(fetch.stderr).strip() if fetch.stderr else ""
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_GIT_ERROR}: fetch failed: {stderr_text or '<no stderr>'}",
        )

    merge_base = _run_git(
        repo, "merge-base", "FETCH_HEAD", live_head_sha, timeout=_GIT_GENERIC_TIMEOUT_SECONDS
    )
    if merge_base.returncode != 0:
        stderr_text = os.fsdecode(merge_base.stderr).strip() if merge_base.stderr else ""
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_GIT_ERROR}: merge-base failed: {stderr_text or '<no stderr>'}",
        )
    live_base_sha = merge_base.stdout.decode("ascii", errors="strict").strip() if merge_base.stdout else ""
    if not _FULL_SHA_RE.match(live_base_sha):
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=live_base_sha or None,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_GIT_ERROR}: malformed merge-base output: {live_base_sha!r}",
        )
    if live_base_sha != binding.base_sha:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=live_base_sha,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_BASE_SHA_MISMATCH}: bound={binding.base_sha!r} recomputed={live_base_sha!r}",
        )

    try:
        observed_digest = patch_digest(repo, binding.base_sha, live_head_sha, repo_slug=binding.repo_slug)
    except GitlinkRejected as exc:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=live_base_sha,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_GITLINK_REJECTED}: {exc}",
        )
    except PatchDigestInvalid as exc:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=None,
            live_base_sha=live_base_sha,
            final_pr_head_sha=live_head_sha,
            reason=f"{REASON_GIT_ERROR}: content recompute failed: {exc}",
        )

    if observed_digest != binding.expected_head_digest:
        return _invalidated(
            expected_head_digest=binding.expected_head_digest,
            observed_head_digest=observed_digest,
            live_base_sha=live_base_sha,
            final_pr_head_sha=live_head_sha,
            reason=REASON_CONTENT_DRIFT,
        )

    return EquivalenceResult(
        result=EQUIVALENCE_EQUIVALENT,
        expected_head_digest=binding.expected_head_digest,
        observed_head_digest=observed_digest,
        reason=None,
        live_base_sha=live_base_sha,
        final_pr_head_sha=live_head_sha,
    )
