# Advisor-panel reconciliation — governed pre-merge gate fix approach

Three independent frontier legs (native Claude Opus @ max+ultrathink with repo access,
Codex/GPT-5.5 @ xhigh, Gemini 3.1 Pro @ high) reviewed the governed pre-merge gate, both
rounds of code-review findings, and the proposed "fail-safe on uncertainty" reframe.

## Unanimous verdict: PARTIALLY AGREE

Fail-safe-on-uncertainty is the **right answer for verdict parsing only** (you genuinely
cannot force a CLI to emit a parseable token). It is a **band-aid — and per the Claude leg,
actively harmful — as the primary fix for path-set and author identity**, both of which are
eliminable *by construction*. The Claude leg's decisive point (code-verified): my proposed
"block when bundle path-set ≠ commit path-set" is **uncomputable where the gate currently
fires** — the gate runs at `runner.py:1899`, but the committed set isn't produced until
`_perform_phase_closeout` (`runner.py:7376-7440`, staged at `7538`). Adopting fail-safe
without relocating would flip every codex-empty-classification phase from silent-pass to
**permanent block** → governed mode unusable. So: do NOT attempt a third in-place patch (it
reintroduces the bug class).

## The fix — structural relocation, not another patch (all 3 legs agree)

### 1. Review the actual staged index (kills 4 bugs by construction)
Relocate the pre-merge gate **into `_perform_phase_closeout`, after `git add` stages the files
(`runner.py:7538`) and before the commit is finalized**; render the bundle from
`git diff --cached -- <closeout_dirty_paths>`. `_perform_phase_closeout` already returns
`(status, LoopEvent)`, so it can return the `review_gate_block` terminal directly — a **bounded
relocation, not a rewrite**. Skip when `_closeout_nothing_staged` (`runner.py:7546` — the
legitimate issue-#6 out-of-band finalize, correctly safe-to-pass). **Delete** the parallel
`governed_bundle._owned_dirty_paths` / `_staged_diff` / `_is_untracked` path-discovery.
Dissolves: bundle-vs-commit divergence, R1 #3 (untracked files), R2 `_is_untracked` fail-open,
R2 N+1 subprocesses — and Codex's catch that writing a bundle into `repo/.phase-loop/governed/`
*before* closeout can itself dirty the worktree (review the index instead; no pre-closeout repo
file). Codex frames it as a `CloseoutCandidate` with content identity (staged tree/blob hashes)
that the finalizer commits **unchanged** — same idea, stronger invariant. Fallback if
relocation is too invasive for one PR: compute `changed_paths` ONCE and pass the identical
list to both the bundle and closeout.

### 2. Thread the actual executor; exclude the UNION of authors (don't reverse-engineer)
Stop deriving author identity from event logs. The runner holds the real value at the call
site (`work_unit_selected_executor` / `dispatch_decision.selected_executor`). Drop the
`action in (execute/repair/plan)` filter (events log `action='run'`; the verb lives in
`metadata.dispatch_decision.launch_action`) and the `author_vendor_for_model(selection.model)`
fallback. **Under rotation/repair, multiple vendors author one phase** (Claude + Codex) — so
exclude the **union** of `selected_executor` across all the phase's dispatch events, not just
the latest. If the author set is unknown/ambiguous → **block**. Gemini: delete
`_phase_author_vendor` entirely and pass the executor directly.

### 3. Strict, fail-closed verdict contract (where fail-safe belongs) + require a real reviewer
Replace the negation-aware substring/regex guessing in `panel_invoker._classify_leg`
(`115-127`) and `governed_review._leg_blocks` (`40-56`) with a strict terminal contract: the
last non-empty line ≡ exactly one of {AGREE, PARTIALLY AGREE, DISAGREE} (or a `VERDICT: …`
machine token). Non-conforming / empty / degraded / timeout → degraded/**block**, never a
silent pass. **Require ≥1 usable disjoint reviewer**: today if every selected leg is unusable
but the pool is non-empty, `has_block` is false and the artifact promotes (Codex — fail-open) →
must block. Fix the related double-fail-open: `governed_premerge.py:160-166` returns
`mergeable=True` when degraded *before* any block is seen (a codex-empty phase whose only
disjoint reviewer is offline both renders empty AND advisory-passes). If advisory-best-effort
is still wanted, make it a **separate mode**, not silent behavior inside `governed`.

## Recommendation
This is a bounded structural relocation with a precise, code-verified blueprint — the proper
fix, not a third patch. Either land it now on the v2 branch (re-review, then merge), or merge
v2 with governed marked **experimental + fail-closed** and do the relocation as a clean
follow-up. The panel explicitly warns against another in-place derivation patch.
