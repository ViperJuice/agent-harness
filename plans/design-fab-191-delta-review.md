# Design: FAB — Advisor-board first-class delta review with reviewed-byte equivalence (agent-harness#191)

Status: DESIGN v2 (no production code). Ground: `Consiliency/agent-harness` @ origin/main `46aa1bd`.
Author lane: FAB lane-a. Consumers: implementation lanes A/B/C/D (see §9).
v2 revises v1 after a cross-vendor panel (codex DISAGREE, grok named-fixes, gemini degraded). §0
maps each panel finding to its closure; the body is rewritten to enforce them.

This is a **security boundary**: a false byte-equivalence lets *unreviewed code land*. The whole
design is fail-CLOSED — every unknown, ambiguous, or unrepresentable state invalidates and forces
(re-)review. It **extends** the SHA-bound gate (#88), by-reference review material (#114), the
broker byte-identity path (#250), and the whole-artifact seal (#243); it does not fork them.

---

## 0. Panel review resolution (v1 → v2)

| # | Panel finding | v1 defect | v2 closure |
|---|---|---|---|
| 1 | **Base-currency mis-framed** (grok, correct) | v1 T1 claimed merge-base equality closes a "merge injects unreviewed L bytes" hole. | **§4.2**: under the chosen **delta identity** (`base_sha..head` = what THIS PR contributes), a bare base-tip advance `B→B'` is NOT a fail-open — those bytes arrived via *other* reviewed merges. Merge-base equality is retained but re-scoped to what it actually proves (no rebase / no conflict-merge committed *on the PR head* / no retarget). The genuine residual — **merge-time conflict resolution performed OUTSIDE the head** (e.g. GitHub UI) after a gate pass — is closed by (a) an operator **branch-protection "require branches up to date" + merge-queue** control and (b) a runtime **promotion-time re-assertion** of the bound `(base_ref_identity, base_sha, head_sha, delta_digest)` tuple (§4.4). Codex's edge: bind the **base REF identity** (not just SHA) and `git fetch` it fresh at the gate (§4.3). |
| 2 | **Authenticity ≠ integrity** (codex, grok) | v1 relied on a self-recomputable `artifact_digest` (integrity only). | **§6**: trust root made explicit. Provenance is **harness-only-written** to the run store; client-supplied provenance is never the sole gate input. The gate **cross-checks** recorded seats against the durable `SeatOutcomeRecord` (`panel_invoker.py:288`), carrying `required` / terminal `status` / `artifact_digest` / `evidence_digest`. Review material (#114 `context_refs`) is made **immutable**: bytes snapshotted (content hash pinned) at review time and re-verified at gate time. Provenance is a **hash chain** over `{policy, review_scope, material digests, findings, prior provenance}`, not merely patch digests. |
| 3 | **Two canonical encodings** (grok) | v1 §3.3 (binary NUL records) and §3.4 (JSON digested like `_canonical_artifact_digest`) were two serializations of the *same* security digest. | **§3**: ONE canonical encoding for `patch_digest` — **binary header + NUL-delimited raw-byte records** (paths stay raw bytes, no JSON escaping). The JSON `_canonical_artifact_digest` canonicalization is reserved for the *provenance artifact* digest (a distinct, non-path-bearing object). The `base_sha` binding is frozen as **load-bearing** (old-side bytes = pure function of `(base_sha, path)`, valid only because `base_sha` is pinned + verified). |
| 4 | **Escalation / manifest downgrade** (codex) | v1 lacked a typed delta status, a proof the whole-patch round saw the whole patch, and manifest-tamper protection. | **§5**: explicit delta `status` enum; a `review_scope` field carrying a **reviewed-material digest** that proves the recorded seats saw the whole patch on a whole-patch round; the boundary manifest is **pinned + hashed into the chain at the reviewed BASE revision** (a delta cannot weaken it then be judged under the weakened rules); a delta touching the **manifest path itself → forced escalation**; manual escalation is a **typed field**, not inferred from prose; glob semantics frozen. |
| 5 | **#88 exact-SHA falsified** (codex) | v1 synthesized `reviewed_sha = final_pr_head_sha` — asserting the final head was reviewed (false). | **§8**: the original `reviewed_sha` is **preserved**; equivalence is a **separate, independently-verified proof** (`equivalence_verified` naming candidate + delta head SHAs + `delta_digest`). The gate output names the real reviewed SHA and the equivalence proof **distinctly**; `fab.gate-status.v2` carries candidate + every `delta_head_sha`. |
| 6 | **Hostile Git** (codex) | v1 trusted git object resolution and accepted `rc==1`. | **§3.2 / §4.5**: every gate git call runs with `GIT_NO_REPLACE_OBJECTS=1` (`--no-replace-objects`); gitlinks (`160000`) are recursively canonicalized or **rejected/escalated** (a rehashed gitlink OID still rests on git's sha1); git `rc != 0` (no `--exit-code`) **invalidates fail-closed**, and a bare `rc==1` is never accepted as success. |

**Not-applicable / caveats reported to orchestrator:** none of the six is rejected — all are real on
a security boundary. One scoping caveat on finding 1: the branch-protection "require up to date"
control is an **operator-configured** GitHub setting the runtime cannot itself enforce; v2 therefore
adds the runtime-side **promotion-time tuple re-assertion (§4.4)** as the fail-closed backstop that
holds *even if* branch protection is misconfigured. Honesty note: v1's base-currency framing was
over-claimed (it came from an earlier advisor call framing the base advance as a `merge3(L)` hole);
grok's delta-identity reasoning is correct and v2 adopts it — see §4.2.

---

## 1. Problem restated (from #191)

A high-risk PR gets several advisor-board rounds. A whole-patch review binds a verdict to an exact
head SHA (#88), but when one blocker is fixed the next round re-reviews the entire (e.g. 2.4 MB /
400-file) patch even though only a small delta changed and the unaffected findings are still valid.
There is no first-class, machine-verifiable way to (a) carry clean findings forward, (b) review only
the delta, and (c) *prove* the eventual PR head is byte-equivalent to `reviewed candidate + accepted
deltas`. FAB adds that.

---

## 2. Existing machinery this design extends (grounding)

| Concern | Where it lives today | What FAB reuses |
|---|---|---|
| SHA-bound verdict | `closeout_validators.py`: `ReviewFinding.reviewed_sha`, `verdict_binds_to(finding, head_sha)` (exact match; `None` → fail-closed) | Preserved **as-is**. FAB adds a **separate** `equivalence_verified` proof (§8); it never overwrites `reviewed_sha`. |
| Canonical changed-path bytes | `credsep.py`: `_branch_diff_paths` = `git diff --name-only -z --no-renames origin/<base>...<head>`; bytes (no `text=True`); split `b"\0"`; `os.fsdecode` | FAB's `patch_digest` uses the **identical** bytes + NUL-split + `os.fsdecode` policy, at **content** granularity (`--raw` + per-blob SHA-256). |
| Canonical digest of a payload | `verification_evidence.py`: `_canonical_artifact_digest` = SHA-256 over `json.dumps(sort_keys=True, separators=(",",":"))` minus one derived field; sealed in a log trailer | Reused for the **provenance-artifact** digest ONLY (a non-path object). **Not** reused for `patch_digest` (§3 uses raw bytes). |
| Metadata-only seat outcome + **authenticity anchor** | `panel_invoker.py:288` `SeatOutcomeRecord{seat_key, vendor_leg, required, status, epoch, artifact_digest, evidence_digest, …}`; `serialize_seat_outcome` (sorted-keys JSON, no raw text) | The **durable authenticity anchor**: the gate cross-checks provenance seats against these records (§6.3), carrying `required` / `status` / `artifact_digest` / `evidence_digest`. |
| Seat / verdict / finding model | `advisor_board/schema.py`: `Seat`, `Board`, `vendor_family`; `panel_invoker.terminal_verdict` → `AGREE | PARTIALLY AGREE | DISAGREE` | Per-seat verdict + finding IDs recorded against those seats. |
| True by-reference material | #114 `PanelRequest.context_refs` (inject a path+metadata manifest, never bytes; hash streamed in 1 MiB chunks) | Delta round passes the delta patch + touched seams as `context_refs`; §6.4 makes the referenced bytes **immutable** (snapshot + re-verify). |
| Host-qualified repo identity | `credsep.py`: `resolve_host_qualified_repo_slug` / `resolve_broker_repo_identity` (allow-listed host, fail-closed) | Binds provenance to the exact repo identity; **base ref identity** binding (§4.3) reuses the same resolver. |

---

## 3. Canonical bytes — ONE encoding (req 1 & 4; the equivalence primitive)

> **One-line summary.** `patch_digest` = SHA-256 over a **binary** stream: a canonical header
> `(schema, repo slug, base_sha)` followed by, for each changed path sorted by raw path bytes, the
> NUL-delimited record `status \0 new_mode \0 content_sha256 \0 <raw path bytes> \0` — where
> `content_sha256` is **our own** SHA-256 of the **actual blob bytes at the target** (never git's
> OID), with **no** whitespace/EOL/normalization. There is exactly ONE such encoding (finding 3).

### 3.1 Identity choice: net-content (delta) identity — decided, and its scope

Identity is the **net content this PR contributes**, i.e. `base_sha .. head` — not commit topology,
not patch text. This is both what reviewers review and what a clean merge applies. Consequences,
stated so an implementation reviewer does not misread them:

- A topology change (reorder / no-op add-then-revert) whose **net content is identical** is a
  **PASS** (only the final tree merges). #191's "extra commit … invalidate" language names *typical
  causes of byte drift*, not topology checks — a genuinely no-op extra commit changes no reviewed
  bytes. (Threat T7, §7.)
- A concurrent **base-tip advance `B→B'`** is **also fine and expected** (finding 1 / §4.2): those
  bytes are *not* this PR's contribution; they entered `main` through their own reviewed PRs. Delta
  identity is precisely what makes this sound.

Patch/hunk text is rejected as identity (context lines, hunk headers, rename detection,
`core.quotepath`, binary elision make it unstable → normalization → collision → fail-OPEN).

### 3.2 Enumerating changed paths (reuse #250 policy; hostile-git hardened — finding 6)

```
GIT_NO_REPLACE_OBJECTS=1 git -C <repo> --no-replace-objects \
  diff --no-renames --no-color -z --raw --abbrev=40 <base_sha> <head_sha>
```
- `--raw -z`: one NUL record per path: `:<old_mode> <new_mode> <old_oid> <new_oid> <status>\0<path>\0`.
- `--no-renames`: a rename → delete(source) + add(dest) — identical to `_branch_diff_paths`, closing the `git mv unowned/x owned/y` escape.
- `--no-replace-objects` + `GIT_NO_REPLACE_OBJECTS=1`: a `refs/replace/*` object cannot silently substitute a different tree/blob at read time (finding 6).
- **Capture as bytes** (no `text=True`), split `b"\0"`, `os.fsdecode` per element — byte-for-byte the #250 policy (no universal-newline collapse of `a\r.py`/`a\r\n.py`/`a\n.py`; no `UnicodeDecodeError`).
- **Return-code (finding 6, corrects v1):** with **no** `--exit-code`, a healthy `git diff` returns **`0`**. Accept **only `rc == 0`** (matching `_branch_diff_paths`, which does `if completed.returncode: return None`). v1's `{0,1}` was a fail-OPEN — `rc==1` here indicates an *error*, not "differences present"; any nonzero → **INVALIDATE**, never an empty/partial set treated as "no changes".

### 3.3 Per-path canonical record (mode + status + type, not just bytes)

For each changed path, in path-byte sort order, append the binary record:

```
<status ascii> 0x00 <new_mode ascii> 0x00 <content_sha256 hex ascii> 0x00 <raw path bytes> 0x00
```
- `status` ∈ `{A,M,D,T}` (add / modify / delete / typechange). A **delete** carries `status=D`, `new_mode=000000`, `content_sha256 = "-"` (a distinct sentinel — a delete can never collide with an add of the same path). Removing a path from the record set changes the sorted stream → digest mismatch (T11).
- `new_mode` from `--raw` (`100644`/`100755`/`120000` symlink / `160000` gitlink). Hashing it closes the **pure-mode-change** (`100644→100755`, identical bytes) and **type-change** (file↔symlink, file↔gitlink) collisions (T8).
- `content_sha256` = **our own** `hashlib.sha256` of the **actual blob bytes at head**, via `GIT_NO_REPLACE_OBJECTS=1 git cat-file --batch` (streamed, bounded like #114's 1 MiB chunking). A `cat-file` "missing"/malformed line → **INVALIDATE** (finding 6). We **never** trust the git `<new_oid>` — that is git's sha1 (T3).
- `<raw path bytes>` — the `-z` path exactly (`os.fsencode` of the `os.fsdecode`d element round-trips to the same bytes). **Never** trimmed/normalized/JSON-escaped (raw bytes are why the encoding is binary, not JSON — finding 3).
- **Gitlinks (`160000`) (finding 6):** the "content" of a submodule is a commit OID, whose integrity rests on git's sha1. Default: **REJECT → force escalation** (the whole-patch reviewer must inspect the submodule bump). Opt-in: recursively canonicalize the submodule's own `patch_digest` and embed *that* SHA-256 as the content hash. A bare gitlink OID is never accepted as content identity.

### 3.4 Header + digest construction (base_sha binding is LOAD-BEARING — finding 3)

The stream is prefixed with a fixed binary header:

```
b"fab.canonical-bytes.v2\0" + repo_slug_utf8 + b"\0" + base_sha_ascii + b"\0"
```
then all §3.3 records, then `patch_digest = hashlib.sha256(stream).hexdigest()`.

**Invariant (frozen in IF-0-FAB-B-1):** we hash **only the NEW side** (target blob bytes). This is
sound **only because** the old side is a pure function of `(base_sha, path)` and `base_sha` is
**pinned and independently verified** (§4.3). If `base_sha` were unpinned or unverified, hashing only
the new side would be a fail-OPEN. The header therefore binds `base_sha` and `repo_slug` directly
into the digest so a digest computed against a different base or repo can never compare equal
(T1/T10).

### 3.5 What is deliberately NOT normalized

No whitespace collapse, no EOL folding, no Unicode NFC/NFD, no path-case folding. Each is a collision
= fail-OPEN (mirrors #243's rejection of `.strip()` and #250's rejection of `text=True`). The JSON
`_canonical_artifact_digest` path (used only for the §6 *provenance* digest) additionally mandates:
any `json.dumps` failure, or any surrogate in a value it must serialize, → **INVALIDATE** (finding
3) — but that path never carries raw file paths (those live only in the binary §3 stream).

---

## 4. Equivalence, base binding & invalidation (req 4; findings 1 & 6)

`equivalent(reviewed_artifact, live_pr)` := ALL of, in order, each fail-closed:

1. **Repo identity** — `repo_slug(live) == reviewed.repo_slug` (host-qualified, #250 resolver).
2. **Base ref identity + freshness (§4.3)** — the live base *ref identity* equals the bound one, fetched fresh; and `merge-base(fresh base ref, live head) == reviewed.base_sha` (§4.2).
3. **Delta chain** — a contiguous, authenticated parent-linked chain from `reviewed.candidate.patch_digest` (§5, §6), every member `status = reviewed-clean`.
4. **Content equivalence** — `patch_digest(base_sha .. live_head)` (recomputed live, §3) == the chain's `expected_head_digest` (= the last accepted delta's `resulting_head_digest`). Never read from a client field.

### 4.2 What merge-base equality actually proves (finding 1 — corrected framing)

Under delta identity, `patch_digest(B..head_reviewed) == patch_digest(B..head_live)` means the two
heads contribute the **same net bytes over the same pinned base `B`**. A concurrent base advance
`B→B'` does **not** break this and is **not** a fail-open: what a clean merge applies is exactly this
PR's delta, and the `B'−B` bytes were reviewed in their own PRs. So merge-base equality is **not**
guarding against base advance (v1's over-claim, removed). It DOES prove, positively:

- **no rebase** of the PR onto a different base (a rebase changes the merge-base);
- **no conflict-resolution merge committed *on the PR head*** that silently moved the fork point;
- **no force-rewrite** of history that relocates the merge-base.

**Genuine residual — merge-time conflict resolution OUTSIDE the head.** If, after a gate PASS, the
merge itself must resolve a conflict against `B'` (done in the GitHub UI, not on the reviewed head),
new unreviewed bytes land. Closed by §4.4 (promotion re-gate) + the operator control in §4.4.

### 4.3 Base **ref identity** binding + fresh fetch (codex edge, finding 1)

Bind, at review time, not just `base_sha` but the **authoritative base ref identity**:
`(repo_slug, base_ref_name)` resolved against the **origin** the #250 resolver validates (never a
stale local ref). At gate time:

1. `git fetch --no-tags <origin> <base_ref_name>` (fresh; a stale local `main` must not decide the gate).
2. Require the PR's live base ref identity `==` the bound one — a **retargeted** base (PR base changed `main → release/2.0`) is a **different ref identity** and **INVALIDATES** even if a merge-base SHA coincidentally matches.
3. Recompute `merge-base(FETCH_HEAD, live head)` and require `== reviewed.base_sha`.

All git calls carry `--no-replace-objects`; any `rc != 0` → INVALIDATE (finding 6).

### 4.4 Promotion-time re-assertion (finding 1 residual closure)

Equivalence proven at gate time is **re-asserted at promotion/merge time**. FAB records the bound
tuple `(repo_slug, base_ref_identity, base_sha, head_sha, expected_head_digest)`; the promotion path
(the broker / `governed_premerge`) **re-runs §4 (steps 1–4) against the live PR immediately before
merge** and refuses to merge on any change → non-human `review_gate_block`. This is the runtime
fail-closed backstop for "conflict resolved outside the head after a pass". Operators SHOULD ALSO
enable GitHub branch protection **"require branches up to date before merging" + a merge queue**, so a
base advance forces the head to incorporate `B'` (and re-gate) *before* merge — but the runtime
re-assertion holds even if that setting is absent/misconfigured.

### 4.5 Enumerated invalidation triggers (fail-closed default: unknown/ambiguous → INVALIDATE)

| # | Trigger | Detected by |
|---|---|---|
| I1 | Rebase / retarget / history-rewrite relocating the fork point | §4.3 base-ref identity + merge-base |
| I2 | Force-update changing net content | precondition 4 digest |
| I3 | Extra commit that changes net content | precondition 4 digest |
| I4 | Conflict resolution **committed on the head** | §4.2 merge-base and/or digest |
| I4b | Conflict resolution performed **outside the head** at merge | §4.4 promotion re-assertion |
| I5 | EOL / mode / type / gitlink drift | §3.3 record hash |
| I6 | Any single-byte content drift | §3.3 blob SHA-256 |
| I7 | Repo / host mismatch (replay) | precondition 1 |
| I8 | Broken / non-contiguous / unauthenticated delta chain | precondition 3 + §6 |
| I9 | git rc≠0, `cat-file` missing/malformed, oversize/malformed provenance, `json.dumps` failure, surrogate in JSON value | fail-closed at point of failure |
| I10 | Reordered commits / no-op extra commit, same net content, base current | **NOT invalidated → PASS** (T7); safe under delta identity |
| I11 | Concurrent base-tip advance `B→B'`, PR unchanged | **NOT invalidated → PASS** (§4.2); `B'−B` reviewed elsewhere; conflict case caught by I4b |

---

## 5. Delta binding + carry-forward + escalation (req 2 & 3; finding 4)

### 5.1 Provenance chain (hash chain, not just patch digests — finding 2)

```
ReviewProvenanceArtifact v2 (reviewed candidate)
  chain_digest C0 = H(policy ‖ review_scope ‖ material_digests ‖ findings ‖ base binding ‖ ∅)
  candidate.patch_digest P0, base_sha B, base_ref_identity, seats/verdicts/findings F, status
DeltaReviewRecord (delta i)
  chain_digest Ci = H(policy_i ‖ review_scope_i ‖ material_digests_i ‖ findings_i ‖ C_{i-1})
  parent_digest = P_{i-1}, parent_chain_digest = C_{i-1}
  delta_head_sha, delta_changed_paths, delta_commits (audit)
  resolved_finding_ids ⊆ ids(F), carried_forward_finding_ids, reopened_finding_ids
  resulting_head_digest P_i = patch_digest(B .. delta_head_sha)
  status ∈ {reviewed-clean, escalated-whole-patch, pending, invalidated}
  escalation {required: bool, trigger: <typed enum | reviewer_seat_key | null>}
```
`base_sha B` and `base_ref_identity` are **constant across the whole chain** (a delta that moves the
base is I1, not a delta). The chain is valid iff **contiguous** (`parent_digest` + `parent_chain_digest`
both link) and every `chain_digest` recomputes.

### 5.2 Delta binding (req 2) + typed status (finding 4)

A delta binds to its parent by `parent_digest` + `parent_chain_digest`, plus the byte-exact
`delta_changed_paths` (§3.2 `-z` set) and `delta_head_sha`. `resulting_head_digest` is recomputed
live. `status` is an **explicit enum** (above) — never inferred from prose. A delta is only
carry-forward-eligible at `status = reviewed-clean`.

### 5.3 Clean-finding carry-forward (req 3)

Finding `f` carries forward (valid without re-review) iff:
- `f.status == clean` (recorded non-blocking, or resolved-then-verified blocker), AND
- `f.path_scope` is **disjoint** from `delta_changed_paths` — decided by the broker's own
  `_covered_by_owned`-style prefix/dir test, **reused not re-implemented** (goal-id-inc2 lesson).
  Intersecting → **re-opened**; empty/absent `path_scope` → **re-review** (fail-closed).

`resolved_finding_ids` asserts which blockers the delta claims to fix; the delta round's seats must
return a verdict on **exactly** those plus every re-opened finding. A `resolved` claim with **no
corroborating delta-round seat verdict** on that finding is **rejected** — a claim is not a resolution
(T4).

### 5.4 Escalation — decidable, declared-surface, downgrade-proof (finding 4)

Escalation is decided from a committed manifest `.advisor-board/boundaries.toml` of **path globs** per
protected surface (goal-coverage-211 "point the checklist at a declared set" lesson):

```toml
[shared_contract]   globs = ["**/contracts/**", "**/*.proto", "**/schema/**"]
[startup_boundary]  globs = ["**/main.py", "**/__main__.py", "**/wsgi.py", "Dockerfile*", "**/entrypoint*"]
[auth_security]     globs = ["**/auth/**", "**/credsep.py", "**/*secret*", "**/security/**"]
[schema]            globs = ["**/migrations/**", "**/*.sql", "**/schema.*"]
[deployment]        globs = ["**/deploy/**", "**/*.tf", "**/helm/**", ".github/workflows/**"]
```

Rules:
- **Glob semantics FROZEN** (IF-0-FAB-C-1): `PurePosixPath`-style `**`/`*`/`?`, matched against the `os.fsdecode`d `-z` path, case-**sensitive**, no implicit prefix; a malformed glob → INVALIDATE.
- **Manifest pinned at the reviewed BASE revision** (finding 4): the manifest content is read at `base_sha` (not at the delta head), hashed, and its digest folded into `chain_digest` (§5.1). A delta therefore **cannot weaken the manifest and then be judged under the weakened rules** — the rules in force are the ones at the reviewed base.
- A delta whose `delta_changed_paths` **touches the manifest path itself** → **forced whole-patch escalation** (the weakening must itself be whole-patch reviewed).
- Any `delta_changed_paths` ∩ any boundary glob → `escalation.required = true`, `trigger = <section>`; the delta round is whole-patch (carry-forward suppressed, all findings re-opened).
- **Manual escalation is a typed field** `escalation.trigger = "reviewer:<seat_key>"` set by a seat, never parsed from review prose.
- **No-manifest / malformed manifest → escalate EVERY delta** (fail-closed): "no boundaries" must never mean "carry everything forward".

### 5.5 `review_scope` — proof the whole-patch round saw the whole patch (finding 4)

Each artifact/delta record carries `review_scope`:

```jsonc
"review_scope": {
  "mode": "whole-patch" | "delta-only",
  "reviewed_material_digest": "<sha256 of the immutable material snapshot the seats received, §6.4>",
  "covers_patch_digest": "<the patch_digest the material corresponds to>"
}
```
For a **whole-patch** round the gate requires `review_scope.mode == "whole-patch"` AND
`covers_patch_digest == candidate.patch_digest` (the seats provably received the whole patch, not a
delta). A boundary-escalated delta that records `delta-only` scope is **rejected** (T5) — escalation
cannot be satisfied by a delta-scoped round.

---

## 6. Provenance artifact schema + trust root (req 5; finding 2)

Additive, versioned, **metadata-only** (no raw review text — `serialize_seat_outcome` posture). The
*artifact* digest reuses `_canonical_artifact_digest`'s JSON canonicalization; the `patch_digest`s it
carries are the §3 binary digests. The artifact's own `artifact_digest` is the single self-excluded
field (like #243's `log_sha256`).

### 6.1 Trust root (finding 2) — the load-bearing authenticity statement

- **Harness-only-written.** The provenance artifact is written by the harness to the **run store**
  (the same durable location as `SeatOutcomeRecord` / `verification.json`), never accepted from a PR
  branch, a client, or repo-tracked files as the **sole** gate input. The gate reads provenance
  **from the run store**, keyed by run id.
- **Integrity** (recomputable digest) proves *the artifact was not edited after write*; **authenticity**
  (this section) proves *the harness actually produced it and the seats actually ran*.

### 6.2 Hash chain (finding 2)

`chain_digest` (§5.1) chains each round to `{policy, review_scope, material digests, findings, prior
chain_digest}`, so a downstream consumer cannot splice a fabricated clean round or reorder rounds
without breaking the chain. The final `chain_digest` is what the gate binds its PASS to.

### 6.3 Seat cross-check against `SeatOutcomeRecord` (finding 2, codex)

The gate cross-checks every provenance seat against the durable `SeatOutcomeRecord`
(`panel_invoker.py:288`) for the same run/epoch, requiring agreement on the fields that record
**carries** (which v1 omitted): `required`, terminal `status`, `artifact_digest`, `evidence_digest`,
`seat_key`, `vendor_leg`, `epoch`. A provenance seat with **no matching durable record**, or a
`required` seat whose durable `status` is not a usable terminal, → **INVALIDATE** (T13). This is what
prevents a hand-written provenance from vouching for seats that never ran.

### 6.4 Immutable review material (finding 2, grok)

`context_refs` (#114) point at **mutable** files. At review time FAB **snapshots** the referenced
bytes into the run store and records `material_digests` (SHA-256 per ref, streamed 1 MiB like #114).
At gate time it **re-hashes the snapshot** and requires equality with `review_scope.reviewed_material_digest`.
A post-review edit of the underlying file cannot change what the seats provably saw (T14).

### 6.5 Schema (`fab.review-provenance.v2`)

```jsonc
{
  "schema": "fab.review-provenance.v2",
  "repo": "github.com/Consiliency/agent-harness",
  "base": {"ref_identity": "github.com/Consiliency/agent-harness#refs/heads/main", "base_sha": "<merge-base>"},
  "boundary_manifest": {"path": ".advisor-board/boundaries.toml", "source_rev": "<base_sha>", "digest": "<sha256 @ base_sha>"},
  "candidate": {"head_sha": "<reviewed head>", "patch_digest": "<§3 binary digest>",
                "review_scope": {"mode": "whole-patch", "reviewed_material_digest": "<...>", "covers_patch_digest": "<...>"}},
  "seats": [{"seat_key": "codex:gpt-5.6-sol:high", "vendor_family": "codex", "required": true,
             "verdict": "AGREE", "status": "ok", "epoch": 3,
             "artifact_digest": "<matches SeatOutcomeRecord>", "evidence_digest": "<matches>",
             "finding_ids": ["f1","f2"]}],
  "findings": [{"id": "f1", "severity": "block", "status": "clean",
                "path_scope": ["phase-loop-runtime/src/.../x.py"], "body_ref": "<content_ref digest, never inline>"}],
  "verification_evidence": [{"kind": "runner_verification_json", "artifact_seal": "<#243 digest>", "path_ref": ".phase-loop/runs/<id>/verification.json"}],
  "material_digests": [{"ref": "<path>", "sha256": "<snapshot hash>"}],
  "delta_chain": [ /* §5.1 DeltaReviewRecord, each with delta_head_sha, status, escalation, chain_digest */ ],
  "chain_digest": "<final §6.2 chain digest>",
  "equivalence": {"expected_head_digest": "<P_n>", "observed_head_digest": "<recomputed>", "result": "EQUIVALENT|INVALIDATED",
                  "reason": "<code|null>", "live_base_sha": "<recomputed>", "final_pr_head_sha": "<live head>"},
  "artifact_digest": "<SHA-256, self-excluded like #243 log_sha256>"
}
```

Back-compat: `candidate.head_sha` **is** the #88 `reviewed_sha`; §8 preserves it and never overwrites
it. Exact-head behavior (empty `delta_chain`, candidate head == live head) is the degenerate supported
case (acceptance criterion 6).

---

## 7. Threat model — fail-OPEN ways a false equivalence could pass, each closed fail-closed

| # | Fail-OPEN attack | Closure |
|---|---|---|
| **T1** | Digest computed against a **different base/repo** compares equal | §3.4 header binds `(repo_slug, base_sha)` INTO the digest; §4.3 base-ref identity + fresh fetch |
| **T1'** | *(corrected from v1)* "base advance injects unreviewed L bytes" | **Not a hole under delta identity** (§4.2); the real residual is merge-outside-head → §4.4 promotion re-assertion + require-up-to-date |
| **T2** | Path set matches, content drifts | per-path SHA-256 of blob bytes (§3.3), not name-only |
| **T3** | git sha1 blob collision (swap a colliding blob; `--raw` OIDs match) | content hash is **our own** SHA-256 of actual bytes; git OID never trusted |
| **T4** | Delta claims a finding resolved that wasn't reviewed | §5.3 requires a corroborating delta-seat verdict on exactly that finding |
| **T5** | Whole-patch escalation satisfied by a delta-scoped round | §5.5 `review_scope.mode == whole-patch` + `covers_patch_digest == candidate.patch_digest` required |
| **T6** | Normalization collision (EOL/whitespace/case) | §3.5 no normalization at all |
| **T7** | *(dual, not attack)* topology churn, identical net content, base current | correctly PASS (I10); safety rests on §4.2 delta identity |
| **T8** | Mode/type/gitlink swap, identical bytes | §3.3 hashes `new_mode` + `status`/type; gitlink rejected/escalated |
| **T9** | Artifact edited after write | `artifact_digest` (#243-style self-excluded) recomputed; **but integrity ≠ authenticity → T13/T14** |
| **T10** | Replay against a look-alike repo | §3.4 header + precondition 1 (host-qualified slug) |
| **T11** | Deletion hidden by dropping a record | explicit `status=D` record; absence changes the sorted stream → mismatch; `-z` D rows; git failure → fail-closed |
| **T12** | Client supplies `observed_head_digest` | gate recomputes every digest + merge-base live; provenance `expected/observed` are audit echoes only |
| **T13** | **Fabricated provenance vouches for seats that never ran** | §6.1 harness-only-written + §6.3 cross-check vs durable `SeatOutcomeRecord` (`required`/`status`/`artifact_digest`/`evidence_digest`) |
| **T14** | **Review material mutated after the manifest hash** | §6.4 snapshot bytes at review, re-verify snapshot at gate |
| **T15** | **Manifest downgraded by the delta**, then judged under weakened rules | §5.4 manifest pinned+hashed at reviewed base; a delta touching the manifest path → forced escalation |
| **T16** | **`reviewed_sha` falsified** to the final head so #88 vouches for unreviewed code | §8 preserves original `reviewed_sha`; equivalence is a separate proof, never a synthesized SHA |
| **T17** | **Hostile git**: `refs/replace/*` substitutes a tree/blob at read; bare `rc==1` accepted as success | §3.2/§4.5 `--no-replace-objects` + `GIT_NO_REPLACE_OBJECTS=1`; only `rc==0` accepted; `cat-file` missing → invalidate |

---

## 8. Gate output contract (req 5) — preserve #88's exact SHA, add a SEPARATE proof (finding 5)

The gate emits ONE record and composes with #88 by **preserving** `verdict_binds_to`, not by
overwriting `reviewed_sha`.

```jsonc
// fab.gate-status.v2
{
  "schema": "fab.gate-status.v2",
  "reviewed_sha": "<candidate.head_sha — the REAL reviewed SHA, unchanged from #88>",
  "prior_review_digest": "<candidate.patch_digest>",
  "chain_digest": "<final §6.2>",
  "deltas": [{"delta_head_sha": "<...>", "delta_digest": "<resulting_head_digest>", "status": "reviewed-clean"}],
  "final_pr_head_sha": "<live head>",
  "equivalence_verified": {                       // the SEPARATE, independently-verified proof
     "result": "EQUIVALENT|INVALIDATED",
     "candidate_head_sha": "<...>",
     "delta_head_shas": ["<...>"],
     "expected_head_digest": "<P_n>",
     "observed_head_digest": "<recomputed live>",
     "base_sha": "<verified merge-base>",
     "reason": "<code|null>"                      // e.g. "base_ref_retargeted", "content_drift:<path>"
  },
  "carried_forward_findings": ["f1"],
  "re_reviewed_findings": ["f2"],
  "escalation": {"required": false, "trigger": null},
  "waiver": null,                                 // operator waiver echo, audited, never silent
  "status": "pass|review_gate_block"              // the ONE status the GitHub gate consumes
}
```

Composition with #88 (finding 5):
- FAB **never** sets `reviewed_sha = final_pr_head_sha`. `reviewed_sha` stays the SHA the seats
  actually reviewed; `verdict_binds_to(finding, reviewed_sha)` keeps its true meaning ("verdict
  computed against THIS exact SHA").
- Equivalence between the reviewed SHA and the live head is a **distinct** claim carried in
  `equivalence_verified`, with its own recomputed evidence. New helper
  `verdict_binds_to_equivalent(finding, gate_status)` := `verdict_binds_to(finding, gate_status.reviewed_sha)`
  (the verdict is genuinely bound to a reviewed SHA) **AND** `gate_status.equivalence_verified.result
  == EQUIVALENT` (that reviewed content is proven equivalent to the live head). Two independent facts,
  ANDed — never one SHA masquerading as the other.
- `status == "pass"` iff `equivalence_verified.result == EQUIVALENT` AND every required seat has a
  non-DISAGREE verdict corroborated by its `SeatOutcomeRecord` (§6.3) AND no unresolved `block`
  finding remains AND the §4.4 promotion re-assertion (run at merge) still holds. Otherwise a
  **non-human, agent-recoverable** `review_gate_block` (`human_required = False`, autonomy-first),
  surfaced with `equivalence_verified.reason`.

---

## 9. Decomposed phase / lane breakdown (mini-roadmap)

`A → B → C → D`. Finding 2 (authenticity/trust-root) expands **Lane A** into the heaviest lane and
adds explicit verification tasks to **Lane D**.

### Lane A — provenance-schema + hash-chain + trust-root  *(no deps; expanded per finding 2)*
- Freeze `fab.review-provenance.v2`, `fab.delta-review` record, `fab.gate-status.v2` as frozen dataclasses + JSON (de)serializers; artifact digest via `_canonical_artifact_digest` (self-excluded field) + fail-closed load (oversize/malformed/`json.dumps`-failure/surrogate).
- **Hash chain** (§6.2) construction/verification.
- **Trust root** (§6.1): harness-only write to the run store; the read API that keys provenance by run id and refuses client-supplied provenance as sole input.
- **Immutable material** (§6.4): snapshot `context_refs` bytes at review, record `material_digests`.
- **IF-0-FAB-A-1**: schemas + chain-digest + artifact-digest canonicalization frozen.
- **Acceptance**: criterion 5 (auditable, metadata-only).

### Lane B — canonical-bytes + equivalence + hostile-git  *(dep A)*
- §3 `patch_digest` (ONE binary encoding; #250 bytes/`os.fsdecode`; `--no-replace-objects`; `rc==0`-only; `cat-file` missing→invalidate; gitlink reject/escalate).
- §4 base-**ref-identity** bind + fresh fetch + merge-base check; `equivalent(...)` with I1–I11.
- **IF-0-FAB-B-1**: `patch_digest` binary format + **hash-only-new-side / base_sha-load-bearing** invariant + result codes frozen.
- **Acceptance**: criteria 2 (unrelated byte → fail closed), 3 (rebase/conflict invalidates).

### Lane C — delta-binding + carry-forward + escalation  *(dep A, B)*
- §5 delta chain (contiguity via `parent_digest` + `parent_chain_digest`), §5.3 carry-forward disjointness (reuse broker path test), §5.4 manifest **pinned at base rev** + frozen glob semantics + manifest-path→escalate + typed manual escalation + no-manifest-escalate, §5.5 `review_scope`/`covers_patch_digest`, T4 resolved-claim corroboration, typed delta `status`.
- **IF-0-FAB-C-1**: boundary-manifest format + glob semantics + carry-forward/escalation decision rule frozen.
- **Acceptance**: criteria 1 (large patch + small delta passes), 4 (contract-surface forces escalation).

### Lane D — gate-output + agent-review-gate + authenticity-verify + promotion re-gate  *(dep A, B, C)*
- Compose `fab.gate-status.v2`; add `verdict_binds_to_equivalent` (**preserve** `reviewed_sha`, §8); wire the single `status` into `closeout_validators` / `governed_premerge` as non-human `review_gate_block`; thread the delta patch via #114 `context_refs`.
- **Authenticity verify** (finding 2): §6.3 `SeatOutcomeRecord` cross-check; §6.4 snapshot re-verify.
- **Promotion re-assertion** (§4.4): re-run §4 at merge in the broker/promotion path; recommend the branch-protection/merge-queue operator control in docs.
- **Acceptance**: criterion 6 (exact-head still supported — degenerate empty-chain case).

Dependency DAG: `A → B → C → D` (B, C consume A; C consumes B; D consumes all). Each lane ends with an
**unmarked** pytest module (dotfiles-integration exclusion lesson) asserting the frozen interface + its
acceptance rows.

### Acceptance-criteria → lane map (no orphans)
| #191 acceptance criterion | Lane |
|---|---|
| Large reviewed patch + small approved delta satisfies gate w/o 2nd whole-patch review | C (+ D wiring) |
| Adding an unrelated byte fails closed | B |
| Rebase / conflict invalidates until reviewed | B (§4.2/§4.3) + D (§4.4 merge-outside-head) |
| Contract-surface delta forces whole-patch escalation | C (§5.4) |
| Provenance auditable + metadata-only + **authentic** | A (+ D verify) |
| Existing exact-head behavior still supported | D (degenerate case) |

---

## 10. Reconcile note (FAB lane-a) — honest truth, no clean-reconcile claim

The advisor-board committed work is **preserved on origin**:
`feat/advisor-board-abdreg` @ `4c603c3` (+8), `phase/abdresolve` @ `582037e` (+4),
`phase/abdfreeze` @ `87dfe8c`, `feat/advisor-board-roadmap-v5` @ `6fea715`,
`fix/advisor-board-degate-v0.4.0` @ `c7dda7b`, plus origin-only branches
(`feat/abdobs-observability-forwarding`, `feat/abdverify-phase7`,
`feat/advisor-board-artifact-by-reference`, `feat/advisor-board-purpose-mode-derivation`,
`phase/abdhome`).

During this session's worktree cleanup, the **uncommitted working trees** of two removed worktrees
were discarded:
- `agent-harness-abdreg` (5 files): surviving sibling copies were inspected and found to **REVERT
  committed safety fixes** — an abandoned experiment. Safe to treat as lost with no value forgone.
- `agent-harness-abdresolve` (25 files, on `phase/abdresolve`): **discarded UN-INSPECTED.** Whether it
  was re-appearing already-committed work or genuine un-committed progress is now **UNKNOWABLE**.
  **This design does NOT claim a clean reconcile or "no silent loss" for abdresolve — the 25-file
  possible-loss is stated plainly and honestly.**

Recommendation: **FAB should start from the committed tips** `phase/abdresolve` /
`feat/advisor-board-abdreg`, which contain the frozen advisor-board schema (`advisor_board/`) this
design codes against.

---

## 11. Open questions for the orchestrator (non-blocking)
- Default boundary manifest when a repo ships none: escalate-all (as designed) vs. a conservative
  built-in default glob set. Designed = escalate-all (fail-closed); a built-in default is a convenience
  layer addable in C without changing the fail-closed contract.
- Gitlink policy default: reject/escalate (as designed) vs. opt-in recursive submodule
  canonicalization. Designed default = reject/escalate.
- sha256-object-format repos: widen `--abbrev=40` to `64`; the design already hashes blob **bytes**
  independently, so only the audit-only git OID width changes.
- Promotion re-assertion host: the broker (`credsep.py`/`train_runner`) already re-reads the remote at
  publish — §4.4 re-gate is a natural extension there; confirm placement vs. `governed_premerge`.
