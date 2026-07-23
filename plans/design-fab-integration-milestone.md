# FAB integration milestone — activating the dormant gate into the live pipeline (agent-harness#191)

**Status:** plan for operator approval. Higher bar than the A–D lanes: this changes how real PRs get
reviewed and merged, so the gate is **operator approval of this plan + cross-vendor CR**, not CR alone.
Recommended execution: a **fresh context** against this approved plan (not the tail of the build session).

## Context

FAB lanes A–D are built. A/B/C are merged (provenance/schema/hash-chain, canonical-bytes/equivalence,
delta-chain/carry-forward/escalation). Lane D (the gate module `fab_gate.py`) is complete, logic-correct,
fail-closed-by-construction, and **dormant** — traced empirically: no live path writes FAB provenance
(producer) or takes the delta-review shortcut (consumer), and `fab_gate` is imported only by its own
closeout self-registration. Absence of provenance ⇒ every PR gets normal full review. This milestone
**activates** FAB end-to-end.

## The three pieces, decomposed by blast radius (build in this order)

### 1. Promotion re-assertion — bounded, load-bearing safety (do first)
- **Where:** `train_runner._live_merge_pr` (:453) / the `run_governed_premerge_loop` merge path.
- **What:** immediately before `gh pr merge`, for a FAB-gated PR, re-run Lane B `equivalent()` against the
  LIVE PR using the bound tuple `(repo_slug, base_ref_identity, base_sha, head_sha, expected_head_digest)`
  from the gate status; refuse merge (non-human `review_gate_block`) on ANY change (retarget / content drift
  / merge-outside-head). `FabPromotionCheck`/`fab_promotion_check` already exist in `governed_premerge`
  (default `None`); wire the production caller to construct + forward it for FAB PRs.
- **Why first:** it's the §4.4 fail-closed backstop and the smallest, most-defined change; landing it first
  means the safety net exists before the producer/consumer can take any shortcut.

### 2. Producer — write provenance + seat outcomes on a board pass (moderate, additive-ish)
- **Where:** `governed_review.py` (the advisor-board review gate) — on a PASS, and the closeout path.
- **What:** when the advisor board approves a PR, write the `ReviewProvenanceArtifact` (Lane A
  `write_provenance`) + the per-seat `SeatOutcomeRecord`s (Lane D `append_seat_outcome`) to the trusted run
  store, keyed by the trusted `run_id`; thread that trusted `run_id` + `repo_root` into the closeout so
  `fab_gate_inputs` reaches the gate (which is fail-closed-by-construction once provenance exists).
- **Note:** additive — it records what a full review already produced. No behavior change to *how* review
  happens; it just makes the review auditable + carry-forward-eligible.

### 3. Consumer — the delta-review shortcut (unbounded, BEHAVIOR-CHANGING — needs the decision below)
- **Where:** `governed_review` / `governed_premerge` when a previously-approved PR's head advances.
- **What:** instead of re-reviewing the whole patch, review only the DELTA (Lane C carry-forward +
  escalation) and, on a clean delta with equivalence proven (Lane B), carry the prior approval forward.
  This is the actual value of FAB — but it changes how PRs get reviewed.

## Operator decision — RESOLVED (2026-07-23): DELIBERATE / OPT-IN

The pipeline does **not** take the delta-review shortcut automatically. The machinery is wired live
(producer writes provenance on a board pass; gate + promotion re-assertion verify it), but **taking the
delta-review shortcut is an explicit operator/flag action** — FAB is a capability you invoke, not a
governed-review default. Consequences for piece 3 (consumer):

- The delta-review path is gated behind an explicit opt-in signal (an operator flag / a per-run mode),
  NOT auto-detected from "approved at X, head advanced to Y".
- Default governed-review behavior is UNCHANGED (full review), so the standing blast radius on the live
  merge path is limited to piece 1 (the promotion re-assertion, which runs for FAB-gated PRs only) and the
  additive producer (piece 2).
- Turning on auto-shortcut is a later, separate decision once the opt-in path is proven in the field.

## Verification bar

- Each piece: cross-vendor CR (codex load-bearing) + green CI.
- The promotion re-assertion (piece 1) must have an end-to-end test proving a live head/base change after a
  gate pass is REFUSED at the real merge path.
- The consumer (piece 3) must prove: a clean small delta carries forward WITHOUT whole-patch re-review
  (acceptance crit 1) AND a contract-surface / non-equivalent / unauthenticated delta does NOT.
- Operator approval of this plan precedes any merge; the dormant gate module (Lane D) merges first.

## Producer implementation findings (from grounding — for clean execution)

**Operator decision: reviewed unit = the PHASE-CLOSEOUT DIFF.** Hook `runner._governed_premerge_review`
(runs inside `_perform_phase_closeout_impl`, AFTER `git add`, BEFORE the commit; reviews
`staged_index_diff(repo, closeout_dirty_paths)` = exactly what gets committed).

**Key intricacies (why this is commit-path surgery, not simple wiring):**
1. **Timing — provenance is written POST-commit.** The review is pre-commit, so `candidate.head_sha` (the
   committed sha) does not exist at review time. Write the `ReviewProvenanceArtifact` AFTER the closeout
   commit is created (the code already reads `rev-parse HEAD` as `closeout_commit` around runner.py:8913).
   `covers_patch_digest = candidate.patch_digest = fab_canonical.patch_digest(repo, base_sha,
   committed_head)` — this equals the reviewed staged diff BY CONSTRUCTION (the commit == the staged
   content), which is the honesty invariant the reviewed-unit choice buys.
2. **Panel propagation.** `_governed_premerge_review` returns `None` on PASS (discarding `result.panel` /
   `result.findings`). The producer needs the PASSING review's PanelResult (seats/verdicts/findings). So
   `_governed_premerge_review` (or `_perform_phase_closeout_impl`) must be modified to CAPTURE + propagate
   the pass-time panel to the post-commit producer.
3. **Seat field-parity.** Build `ProvenanceSeat`s that field-for-field match the `SeatOutcomeRecord`s
   persisted via `fab_gate.append_seat_outcome` / `seat_outcomes_path_for_run` (§6.3 cross-check joins on
   seat_key/vendor_leg/epoch + required/status/artifact_digest/evidence_digest). Findings → `Finding` with
   `body_ref` content-digest (never inline text). `delta_chain=()`, `chain_digest=compute_c0()`.
4. **Closeout-gate activation.** Thread `run_id` + `repo_root` into `build_phase_loop_closeout(fab_gate_inputs
   =...)` (runner.py:7361) so the Lane D closeout gate fires (clean full review → exact-head PASS).
5. **run_id bridge to the train merge.** Persist a head_sha→run_id index (or make provenance findable by
   `candidate.head_sha`) so the train P4 loop sets `completed_nodes[nid]["fab_run_id"]` for the node whose PR
   head matches → piece-1's `_live_merge_pr` re-assertion engages.
6. **Fail-closed + byte-neutral default** (`fab_promotion_enabled()` gate; stash-proof off-path test).

**Execution note:** this is core commit-path surgery. Recommend a fresh context (or a fresh subagent handed
THIS section) — the pre-commit→post-commit propagation is the crux; get it wrong and either provenance
misrepresents what was reviewed (honesty bug) or the closeout commit path regresses. Piece 1 (safety net) is
already merged, so the pipeline is safe with FAB off regardless.
