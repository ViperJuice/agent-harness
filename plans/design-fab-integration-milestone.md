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
