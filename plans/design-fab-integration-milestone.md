# FAB integration milestone — activating the dormant gate into the live pipeline (agent-harness#191)

**Status:** plan v2 — **cross-vendor panel-reviewed** (codex+grok+gemini all initially DISAGREE'd v1; this
version folds in every blocking finding). Higher bar than the A–D lanes: this changes how real PRs get
reviewed, committed, and merged, so the gate is **operator approval of this plan + cross-vendor CR on the
code**. Recommended execution: a **fresh context** against this plan.

## Context

FAB lanes A–D are built + merged (dormant). Activation **piece 1** (a merge-time promotion re-assertion,
byte-neutral behind `PHASE_LOOP_FAB`, merged #282) reads `run_id` from `completed_nodes[nid]["fab_run_id"]`,
a key nothing populates yet. This milestone wires the **producer** (piece 2) and the **deliberate/opt-in
consumer** (piece 3). Everything stays behind `PHASE_LOOP_FAB` (default off = byte-for-byte unchanged).

The panel proved v1's core assumption false, so read the **honesty model** first — it reframes everything.

## The honesty model (the crux the panel corrected)

FAB re-reviews **the unit the board actually reviewed**, and equivalence is checked on that same unit over
`base_sha .. head`, where **FAB equivalence forces `base_sha == the live PR merge-base`**. The board reviews
one phase-closeout's **staged diff** (`_governed_premerge_review`, after `git add`, before commit). Therefore
provenance is honest **only** when the reviewed closeout diff covers the **entire** `merge-base .. PR-head`
net content — otherwise `covers_patch_digest = patch_digest(merge_base, head)` includes bytes the seats never
saw (v1's fatal "by construction" overclaim; all three panelists blocked on it).

**Enforced honesty gate (fail-closed, not "by construction"):** FAB provenance is written for a node's PR
**only if ALL hold**, else NO provenance is written and the PR falls back to normal whole-patch review:
1. **Single reviewed commit covers the PR (milestone scope):** the reviewed closeout commit's parent equals
   the PR merge-base — i.e. `precommit HEAD == merge-base(origin/<base>, head)`. Multi-commit PRs (accumulated
   phase closeouts, continuation commits, executor mid-branch commits) are **out of scope for this milestone**
   — see "Deferred: multi-commit composition" — and MUST be detected and excluded (no provenance → full
   review), never silently attached.
2. **Post-hook tree/parent verification:** after the closeout commit is created (post any pre-commit hooks),
   verify `commit^ == reviewed_base_sha` AND `commit^{tree} == reviewed_tree` (the tree the panel reviewed). A
   hook that mutates the tree, or concurrent HEAD movement, invalidates the review → no provenance.
3. **Non-empty:** an empty / no-op closeout produces no reviewable diff → no provenance, no head index entry.
4. `candidate.head_sha = committed_head`; `base = BaseBinding(ref_identity, base_sha = merge_base)`;
   `covers_patch_digest = candidate.patch_digest = fab_canonical.patch_digest(repo, merge_base, committed_head)`
   — now honestly equal to what the seats reviewed **because 1–3 hold**.

## The three pieces (build in order; each cross-vendor CR'd)

### Piece 1 — promotion re-assertion (MERGED, #282)
`train_runner._live_merge_pr` re-runs Lane B `equivalent()` vs the live PR before `gh pr merge`, behind
`PHASE_LOOP_FAB`, fail-closed on drift/unresolvable/missing-or-unreadable provenance. run_id from
`completed_nodes[nid]["fab_run_id"]`. **Gap to close in piece 2/3:** promotion currently rechecks only byte
equivalence — it MUST also re-verify seat authenticity (§6.3) + no-unresolved-block-finding at merge, OR that
re-verification must run in the dedicated hard gate that piece 2 wires (see below).

### Piece 2 — producer (honest recording + the HARD gate + byte-neutrality)
1. **Authentic seats — capture at invocation, never synthesize (codex, anti-tautology):** `PanelLegResult` /
   `LoopResult` do NOT carry `required`/`attempt_id`/`epoch`/`artifact_digest`/`evidence_digest`, so the
   producer CANNOT build authentic `SeatOutcomeRecord`s from the review return value — synthesizing both the
   `ProvenanceSeat`s and the `SeatOutcomeRecord`s makes the §6.3 cross-check tautological. Instead: the
   governed panel invocation itself (`governed_premerge_for_run`/`invoke_panel`) must **persist the trusted
   per-seat `SeatOutcomeRecord`s to the run store at review time** (real epoch/attempt/digests), and the
   producer builds `ProvenanceSeat`s to MATCH those durable records. May require threading seat outcomes out
   of the panel layer.
2. **One atomic, flag-gated producer transaction** (codex): under `PHASE_LOOP_FAB`, at phase closeout —
   capture the passing panel + its trusted seat outcomes at invocation → commit → run the honesty gate
   (`precommit HEAD == merge-base`, post-hook `commit^`/tree verify, non-empty) → build the
   `ReviewProvenanceArtifact` (delta_chain=(), exact-head; findings→`Finding` with `body_ref` content-digest;
   `material_digests` = snapshot of the reviewed bundle bytes + `reviewed_material_digest`, §6.4, at REVIEW
   time; `chain_digest=compute_c0`) → `write_provenance(repo, run_id, artifact)` → run a **dedicated HARD FAB
   gate**. **Any step failing BLOCKS before publication.**
3. **Dedicated hard gate OUTSIDE the warn-downgradable registry** (codex): FAB's decision must NOT go through
   the generic closeout-validator registry (whose default `PHASE_LOOP_REVIEW=warn` downgrades every block and
   whose exceptions are skipped) — a warn-downgraded FAB block would let unauthenticated provenance pass. Run
   `compose_gate_status` in a dedicated path that hard-blocks on non-pass regardless of `PHASE_LOOP_REVIEW`.
4. **Byte-neutral default (grok):** when `PHASE_LOOP_FAB` is off, NOTHING changes — no panel-seat persistence,
   no post-commit producer, no `fab_gate_inputs`, no return-shape change in `_governed_premerge_review`, no
   head index. Threading `fab_gate_inputs`/run_id alone (which activates the F3 fail-closed gate) is a
   byte-neutrality violation — gate ALL of it. Stash-proof off-path test.

### Piece 3 — consumer (deliberate/opt-in delta-review shortcut) + the durable bridge
1. **Coordinator-owned FAB admission record (codex+grok — replaces v1's ephemeral head→run_id index):**
   persist a durable record (train ledger / coordinator store, NOT ephemeral `completed_nodes`) containing:
   repo+node identity, trusted `run_id`, explicit operator opt-in flag, reviewed head, delta-approved head,
   and the **broker-admitted head**. Bind `fab_run_id` at **publish/admission time** (same place as
   `admitted_head_sha`), not a late opportunistic head lookup.
2. **Atomic re-admission on delta approval (codex):** an advanced PR head is rejected against the OLD
   broker-admitted SHA *before* FAB's promotion check runs (`train_runner:748`). So a successful delta review
   must trigger a NEW broker admission and an **atomic** update of the admitted head, and the consumer must
   **write a NEW provenance record for the new head** (gemini — else piece 1 checks the old provenance,
   mismatches, and blocks, defeating the shortcut).
3. **Fail-closed everywhere (all 3):** missing / ambiguous (two runs sharing a head → fail-closed, not
   last-write-wins) / stale / mismatched admission state → force whole-patch review or halt; a bridge miss →
   whole-patch (test as acceptance, don't assume). The opt-in signal must be **trusted** (coordinator/
   operator-set), never attacker/PR-settable.
4. Consumer reviews only the DELTA (Lane C carry-forward + escalation) and carries the prior approval forward
   only on clean+equivalent (Lane B) + authentic seats (§6.3); default governed-review behavior is UNCHANGED.

## Enforced merge-queue prohibition (codex+gemini — NOT just documented)
Activating the producer/consumer makes the merge-queue TOCTOU reachable: a GitHub merge queue creates the
final commit AFTER `_live_merge_pr`'s piece-1 recheck, bypassing equivalence. Deferring to #265 is only safe
with a **runtime prohibition**: when `PHASE_LOOP_FAB` is on AND the target repo/PR has a merge queue enabled,
**REFUSE/halt** (non-human) rather than proceed. Add the check; remove it when #265 lands the queue-bound
re-assertion.

## Deferred: multi-commit composition (follow-up, not this milestone)
Honestly covering a multi-commit PR (`base..head` across several phase closeouts) needs either a Lane C-style
chain of per-phase provenance covering every commit, or a "full `base..head` covered by the union of reviewed
diffs" predicate. Out of scope here — this milestone supports only single-reviewed-commit PRs (honesty gate
condition 1) and MUST fail-closed (full review) on everything else.

## Test matrix (acceptance — write these)
- Byte-neutrality: `PHASE_LOOP_FAB` off → the review/commit/merge path is byte-for-byte unchanged (stash-proof).
- Honesty: pre-commit hook mutates the tree → no provenance (post-hook verify fails); multi-commit PR
  (parent != merge-base) → no provenance, full review; empty/no-op closeout → no provenance; single-commit
  clean closeout → provenance whose `covers_patch_digest == patch_digest(merge_base, head)`.
- Authenticity NON-tautological: seat outcomes persisted at invocation; a fabricated ProvenanceSeat with no
  matching durable SeatOutcomeRecord → gate BLOCKS.
- Hard gate: a non-pass FAB status BLOCKS even under `PHASE_LOOP_REVIEW=warn`.
- Bridge/admission: fab_run_id bound at admission; resume preserves it (durable, not ephemeral); ambiguous
  head → fail-closed; miss → whole-patch; advanced head → re-admission + new provenance, else block.
- Merge-queue: FAB on + queue enabled → refuse/halt.
- Promotion re-assertion re-verifies seat authenticity + unresolved findings (not just bytes), or the hard
  gate does, before merge.

---
## v1 producer findings (retained for reference; superseded by the honesty model above)

The reviewed unit is the phase-closeout diff (hook `runner._governed_premerge_review`). The v1 claim that
`covers_patch_digest == reviewed diff` "by construction" was WRONG (see honesty model). Timing: provenance is
written POST-commit (the committed head doesn't exist at review time; the code already reads `rev-parse HEAD`
as `closeout_commit` around runner.py:8913). `_governed_premerge_review` returns `None` on pass, discarding
`result.panel` — that must be captured. Thread `run_id`+`repo_root` into `build_phase_loop_closeout`
(runner.py:7361) — but NOTE codex's finding that `build_phase_loop_closeout` runs while reducing the executor
terminal summary, BEFORE the governed closeout commit, so the gate wiring must be re-checked so it validates
the FINAL committed artifact, not an early/nonexistent one.

---
## v2 panel residuals → v3 REQUIREMENTS (codex; grok+gemini AGREE'd v2, codex found these deeper)

These are load-bearing design requirements the fresh execution MUST resolve — two are architectural, not
implementation nits. The plan is NOT "done" until these are designed:

1. **Complete review representation (deeper honesty).** `staged_index_diff` (governed_bundle.py:23) is
   text-mode `git diff`: it ignores nonzero rc, returns sentinels on decode/timeout, and can render only
   "Binary files differ" / attribute-suppressed / invalid-UTF-8 content. So `commit^{tree} == reviewed_tree`
   does NOT prove the seats actually SAW every changed byte. FAB must FAIL CLOSED (no provenance) unless every
   changed path in the reviewed diff has a COMPLETE review representation (no binary-elided / suppressed /
   sentinel content). Acceptance tests for each elision case.
2. **Non-forgeable seat authenticity (ARCHITECTURAL).** `SeatOutcomeRecord` (panel_invoker.py:287) carries
   NEITHER verdict NOR finding-ids, and `cross_check_seat_authenticity` (fab_gate.py:378) compares only
   metadata AND tolerates omitted durable outcomes. So a provenance seat can reuse a real outcome's digests
   while flipping its verdict to AGREE, or a required DISAGREE seat can be omitted, and the gate trusts the
   provenance verdict. REQUIRED: persist verdict + finding bindings (durably, at invocation, tamper-evident)
   and require COMPLETENESS for ALL required seats in the review epoch (no tolerated omissions). This likely
   means EXTENDING SeatOutcomeRecord (or an adjacent durable record) — a Lane-D/panel-layer change. Test
   wrong-verdict AND omitted-required-seat.
3. **Crash-safe producer transaction.** v2 writes provenance BEFORE the hard gate — a crash between leaves an
   unapproved artifact, and the clean-resume path can finalize as `noop_already_committed` (runner.py:9070)
   while promotion does only equivalence (train_runner.py:527). REQUIRED: a durable GATE-PASSED commit marker
   that admission atomically CONSUMES, OR rerun full `compose_gate_status` (authenticity + findings, not just
   bytes) at promotion. Test every commit/write/gate/admission crash boundary.
4. **Wire the whole-patch fallback (ARCHITECTURAL).** "no provenance → full review" is UNWIRED: the harness
   reviews only the per-phase staged diff; there is NO existing path that constructs + board-reviews
   `merge-base..head` for a PR, and the train bundle only lists PR URLs + short SHAs. REQUIRED: the fallback
   must EXPLICITLY invoke a digest-bound whole-patch review of `merge-base..head` (or HALT non-human) — "no
   provenance" alone does NOT satisfy acceptance criterion 1. This interacts with the deferred multi-commit
   composition and may be the real prerequisite: FAB's "carry a whole-patch approval forward" premise needs a
   whole-patch review to exist in the first place.

**Assessment:** two panel rounds hardened this plan substantially, but codex keeps surfacing deeper
*architectural* gaps (#2 seat-verdict persistence, #4 the missing whole-patch review path) — signals that FAB
activation is a foundational design effort, not a wiring job, and wants a focused design pass (its own spike)
before implementation. Piece 1 (safety net) is merged and safe; nothing here is urgent.

---
## v4 — RESOLVED architectural design (design spike; resolves v2/v3 residuals #2, #3, #4)

A focused spike investigated the two architectural gaps against `main`. Both resolve using MOSTLY EXISTING
machinery; the harness already fail-closes an unreviewed advanced head.

### Resolves #2 — non-forgeable seat authenticity (spike A)
- `SeatOutcomeRecord` (panel_invoker.py:287) is confirmed harness-only-written to the run-store ledger
  (`fab_gate.append_seat_outcome`, `O_CREAT|O_APPEND`, mode 0600, fsync, strict-parse) — but carries no
  verdict/finding-ids and NOTHING persists it in a live path yet. **Design:** ADD `verdict: str|None` +
  `finding_ids: tuple[str,...]` (additive, keyword-defaulted; whitelist the two keys in the strict
  `_seat_outcome_from_dict` parser; verdict reuses `panel_invoker.terminal_verdict`'s vocabulary — no new
  enum). Persist from the FAB-scoped panel-invocation wrapper (piece 2, new): after each `PanelLegResult`,
  `verdict = terminal_verdict(leg.text)` → `append_seat_outcome(repo, run_id, SeatOutcomeRecord(..., verdict,
  finding_ids))`. Keeps `panel_invoker` FAB-agnostic (it already takes an injectable sink).
- **`cross_check_seat_authenticity` (fab_gate.py:378) — two new rules, both keyed off the DURABLE side:**
  (1) VERDICT-BINDING: a provenance seat's `verdict` must equal its durable match's verdict (strict,
  no-normalization). (2) COMPLETENESS DRIVEN FROM DURABLE: iterate the DURABLE records (not provenance) —
  every `required=True` durable record with a usable-terminal status MUST have a matching provenance seat at
  the same key with the same verdict, else BLOCK. This closes BOTH the verdict-flip and the omission holes by
  construction (attacker doesn't control what's iterated).
- **Epoch scoping (caveat to honor):** the cross-check runs over `artifact.seats` AND per delta round vs the
  SAME full durable set, so completeness must be EPOCH-SCOPED — thread an explicit `epoch` param into each
  call site (candidate round's epoch for the artifact call; that round's epoch per delta round). Add the
  invariant "all seats in one round share one epoch" when implementing (`DeltaReviewRecord` has no epoch field
  today). Additive; no live consumer breaks (only one positional test fixture to update).

### Resolves #4 — the fallback is EXISTING machinery, no whole-patch subsystem (spike B)
- **Current head-advance behavior (confirmed):** `_live_merge_pr` (train_runner.py:577-836) re-reads the live
  PR head, compares to the broker-ADMITTED `head_sha` (`completed_nodes[nid]["admitted_head_sha"]`, set at
  publish/Step-3), and on divergence `raise RuntimeError("pr-head-advanced")` → node ledgered blocked, train
  `merge_halted`. `--match-head-commit` + post-merge re-validation close TOCTOU (the #250 N7 machinery,
  UNCONDITIONAL, not FAB-gated). **So an advanced/unadmitted head is already unconditionally rejected — never
  re-reviewed-away, never stale-merged.**
- **No whole-`merge-base..head` review exists** (confirmed) — and is NOT needed: every phase-closeout commit
  is ALWAYS reviewed by `_governed_premerge_review` regardless of FAB. The plan's honesty model already
  reframes FAB to carry forward the PER-PHASE-CLOSEOUT approval (not a whole-patch one) — no further reframe.
- **Wired fallback (zero-to-one small primitive):**
  - Piece-2 fallback (multi-commit / honesty-gate-fail / empty closeout): producer writes NO provenance →
    `fab_run_id` absent → promotion gate inert → merge proceeds via the pre-existing non-FAB path, which is
    ALREADY the full per-commit review. **Zero new code.**
  - Piece-3 fallback (bridge-miss / ambiguous / stale-admission, SINGLE new commit since admission): run the
    EXISTING `governed_premerge_for_run`/`_governed_premerge_review` against `git diff old_admitted..new_head`
    — a thin COMMITTED-RANGE variant of `staged_index_diff` (**the only new review primitive**), only when
    exactly one commit separates old-admitted from new head → on approval, the SAME atomic re-admission
    piece 3 already needs (new provenance + atomic `admitted_head_sha` update). Until re-admission, the
    existing `pr-head-advanced` guard rejects the merge — no new rejection code.
  - Multi-commit advance since admission → OUT of scope (deferred multi-commit composition) → same fail-closed
    result (admission never advances → existing guard rejects → `merge_halted`).
  - **Nothing merges unreviewed in any branch:** either the per-commit closeout review covered it, or a
    single-commit fresh review + fresh admission covers it, or the #250 guard blocks it.

### Resolves #3 — crash safety via merge-time full re-gate + admission-as-authority
Admission currently happens at PUBLISH (pre-gate), so a crash between provenance-write and the closeout gate
must not let a non-gate-passed head merge. **Design:** the piece-1 promotion re-assertion at
`_live_merge_pr` must run the FULL hard `compose_gate_status` at merge (authenticity §6.3 + unresolved-block
findings + equivalence — NOT just byte-equivalence, closing the gap piece 1 already flags). So a
provenance-written-but-not-gate-passed (crash), tampered, or incomplete artifact is caught AT MERGE and the
head is rejected (`review_gate_block`, flowing through the existing `merge_halted` path). The merge-time full
gate is the authoritative decision; the closeout gate is advisory/early. This makes the producer transaction
crash-safe without a separate marker: no gate-pass at merge ⇒ no merge.

### Still to implement (v3 requirement #1 — not dissolved, must be built)
COMPLETE REVIEW REPRESENTATION: `staged_index_diff` (governed_bundle.py:23) is text-mode `git diff` (ignores
nonzero rc, sentinels on decode/timeout, "Binary files differ"). Tree-equality does NOT prove the seats SAW
every changed byte. FAB must FAIL CLOSED (no provenance) unless every changed path in the reviewed diff has a
complete review representation (no binary-elided / attribute-suppressed / sentinel content). This is a real
implementation requirement (a completeness predicate on the reviewed diff), not dissolved by the spike.

### Net design shape for execution
Producer (piece 2) = the FAB-scoped panel wrapper (persist authentic seat outcomes w/ verdict+findings) +
post-commit honesty gate (precommit-HEAD==merge-base, post-hook tree/parent verify, non-empty, complete
review representation) + write provenance. Consumer (piece 3) = durable admission record + committed-range
single-commit re-review + atomic re-admission + new provenance. Gate authority = the full `compose_gate_status`
re-run at merge (piece 1 extended). Everything behind `PHASE_LOOP_FAB` (byte-neutral off). Merge-queue refused
until #265. Multi-commit composition deferred.

---
## v5 — closes v4 panel fail-opens (codex + gemini; grok AGREE'd v4)

Four specific forge-path fixes on top of v4's resolved design:

1. **Expected-seat manifest anchors completeness (codex — closes omission of non-usable / never-recorded
   required seats).** v4's "iterate durable required+usable records" has a hole: a required seat that TIMED
   OUT / DEGRADED (non-usable) or NEVER produced a durable record isn't in that set, so completeness doesn't
   demand it → the blocking seat is invisible (bypasses fab_gate.py:396's "required non-usable can't approve").
   Neither `PanelLegResult` nor the board `Seat` carries an authoritative `required` flag
   (panel_invoker.py:220, advisor_board/schema.py:141). **FIX:** persist an EPOCH-SCOPED EXPECTED-SEAT MANIFEST
   (the set of required seat_keys) BEFORE panel invocation, harness-only-written to the run store. The gate
   requires, for every epoch: every EXPECTED-required seat has (a) a durable outcome with a USABLE-terminal
   status (a non-usable/timeout/degraded required outcome → BLOCK), AND (b) a matching provenance seat with
   matching verdict AND finding_ids. Anchor completeness on the EXPECTED set (known pre-invocation), never the
   produced set.
2. **Bind finding_ids, not just verdict (gemini — closes the drop-the-blocking-finding forge).** v4's
   cross-check binds verdict but not findings, so an attacker keeps the matching verdict and EMPTIES the
   provenance `findings`, dropping a blocking finding a seat logged. **FIX:** `cross_check_seat_authenticity`
   must require `set(provenance_seat.finding_ids) == set(durable.finding_ids)` per seat. Then the gate's
   unresolved-block-finding check sees the true findings.
3. **Explicit `epoch` on `DeltaReviewRecord` (gemini — closes the empty-seats epoch bypass).** Deriving a
   delta round's epoch from its seats lets a forged `DeltaReviewRecord` with an EMPTY `seats` list evade
   completeness (can't derive epoch → can't fetch durable records → 0 expected, 0 provided, trivially passes).
   **FIX:** add an explicit `epoch: int` field to `DeltaReviewRecord`; completeness fetches the epoch's
   EXPECTED-seat manifest + durable records BY THAT EPOCH — an empty-seats delta round then fails completeness
   (the epoch's expected required seats are uncovered). This upgrades spike-A's "one-line invariant" to a
   SECURITY requirement.
4. **Honest fallback: "no provenance" = the pre-FAB STATUS QUO, NOT "full review" (codex — corrects a false
   plan claim).** codex is right that "every closeout commit is reviewed" is FALSE (`noop_already_committed`
   completes before review, runner.py:9070; executor mid-branch commits are outside the staged review; the
   admitted-head guard proves "unchanged since publish," not "reviewed"). So the honest statement: when FAB
   writes no provenance, the merge takes the EXISTING non-FAB admission-based path — with the SAME coverage
   properties it has TODAY (pre-FAB). FAB neither weakens nor strengthens that path; it does NOT claim to
   prove `merge-base..admitted_head` is fully reviewed. GUARANTEEING full-range review coverage (closing the
   pre-existing admission≠review gap) is EXPLICITLY DEFERRED to the multi-commit-composition follow-up + an
   OPTIONAL future strict mode (FAB-on ⇒ halt unless the whole range is provably reviewed) — NOT this
   milestone. Do not claim otherwise in the code or docs. (This keeps the milestone's blast radius minimal:
   FAB adds the shortcut + its guarantees for FAB-provenance PRs; non-FAB PRs merge exactly as today.)

### Net (v1→v5)
The plan now: honest per-phase-closeout reviewed unit + enforced honesty gate; non-forgeable authenticity via
an expected-seat manifest + verdict-AND-finding binding + durable-driven epoch-scoped completeness (explicit
DeltaReviewRecord.epoch); hard gate outside the warn registry; full byte-neutral gating; durable admission
record + atomic re-admission + single-commit committed-range re-review; crash safety via full merge-time
re-gate; enforced merge-queue prohibition (#265); complete-review-representation build requirement; and an
HONEST fallback (status quo, full-range coverage deferred). Multi-commit composition + optional strict mode
are the named follow-ups.

---
## v6 — final forge-resistance requirements (v5 panel: codex+grok) + STOP-iteration note

Three deeper forge-resistance requirements. These + the "hard core" note below CLOSE the plan iteration.

1. **Seat-INSTANCE identity, not `seat_key` (codex — seat_key is non-unique).** `seat_key` is explicitly
   non-unique; identical seats are distinguished POSITIONALLY (advisor_board/schema.py:180). An expected-seat
   manifest keyed on `set(seat_key)` collapses duplicate required seats → one outcome satisfies several.
   **REQUIRE:** the manifest + durable outcomes + provenance seats use a UNIQUE per-invocation seat-INSTANCE
   id; freeze the exact RESOLVED invocation set (the concrete seats actually dispatched) as the completeness
   denominator.
2. **Bind finding CONTENT, not just finding_ids (codex+grok — the gate reads severity/status).** The gate
   evaluates top-level `Finding.severity/status` (fab_gate.py:706), while durable outcomes hold only IDs. So
   provenance can keep matching IDs but OMIT the `Finding` records or REWRITE them non-blocking/clean.
   **REQUIRE:** each finding id binds to a HARNESS-AUTHENTICATED canonical finding record (severity+status+
   body_ref digest) persisted at review time; the cross-check requires an EXACT id→content mapping (a
   provenance Finding must match the durable canonical finding's severity/status/digest). Matching the id set
   alone is insufficient.
3. **Harness-issued ROUND IDENTITY, not a self-asserted epoch (codex+grok — replay/vacuity).** A self-asserted
   `DeltaReviewRecord.epoch` lets an attacker REPLAY an earlier clean epoch's manifest+outcomes for a different
   delta, or point at an empty-expected epoch (vacuity). **REQUIRE:** a HARNESS-ISSUED round identity bound to
   that round's immutable review MATERIAL + reviewed HEAD; the gate verifies the round's manifest/outcome
   artifact digests against THAT exact round's material+head; an UNKNOWN, EMPTY-expected, or REUSED round
   identity → BLOCK (no vacuous pass; no cross-round replay).

### STOP-iteration note (honest engineering assessment)
The plan has been panel-hardened across FIVE rounds (v1→v6), closing ~15 distinct real fail-opens. The core
that keeps yielding layers is **making a client-supplied `ReviewProvenanceArtifact` non-forgeable against a
harness-only-written trust root** — a genuine cryptographic-integrity design surface (seat-instance identity →
verdict binding → completeness → finding-content binding → replay-resistant round identity → …). Abstract
plan-doc iteration will keep peeling it. The remaining forge-resistance work is best resolved DURING
IMPLEMENTATION with the actual data structures + a cross-vendor CR in front of you — not in further doc rounds.

**Guiding invariant for implementation (the north star that dissolves this class):** the ONLY thing the gate
may trust is what the HARNESS wrote to the run store at review time (expected-seat manifest, per-seat outcomes
w/ verdict + canonical finding content, round identity bound to material+head). The client-supplied provenance
is verified AGAINST that, field-by-field, fail-closed on any absence/mismatch/vacuity. Anything the gate reads
from the provenance that is NOT cross-checked against a harness-written durable record is a forge path. Build
to that invariant + adversarially CR each field.

This plan is a SOUND, deeply-hardened problem statement + design. It is NOT "all fail-opens are eliminated on
paper" — it is "the architecture is settled, all MAJOR fail-opens are closed, and the residual forge-resistance
details have a clear guiding invariant to implement + adversarially CR." Execute from a fresh context.
