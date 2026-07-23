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
