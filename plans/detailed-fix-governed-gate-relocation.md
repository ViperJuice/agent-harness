# Detailed plan — governed pre-merge gate relocation (panel-blueprinted fix)

Implements the unanimous, code-verified advisor-panel reconciliation
(`plans/governed-gate-panel-reconciliation.md`). This is a **bounded structural relocation**,
not a third in-place patch (the panel explicitly warned the latter reintroduces the bug class).
Branch `feat/model-routing-v2`. The autonomous default path must stay byte-identical.

## Verified anchors (runner.py, feat/model-routing-v2)
- Current premerge gate call site: `runner.py:1884` (`_gate = _governed_premerge_gate(...)`) — fires BEFORE `_perform_phase_closeout`. **This is the wrong site** (the commit set doesn't exist yet → bundle/commit divergence is uncomputable here).
- `_perform_phase_closeout` (`7338`): computes `closeout_dirty_paths` (`7359`), reclassifies the codex-empty case from `dirty_paths` (`7376-7421` → overwrites `closeout_dirty_paths` at `7415`), stages via `git add` (`7538`), nothing-staged guard `_closeout_nothing_staged` (`7546`, `7850`), `git commit` (`~7583`), returns `(status, event)` (`7756`).
- `_phase_author_vendor` (`7940-7952`); `governed_premerge.run_governed_premerge_loop` degraded-before-block advisory pass (`governed_premerge.py:160-166`); verdict classification `panel_invoker._classify_leg` (`115-127`) + `governed_review._leg_blocks` (`40-56`).

## Change 1 — Relocate the gate to review the actual staged index
Move the governed pre-merge gate INTO `_perform_phase_closeout`, **after `git add` (`7538`) and
before `git commit` (`~7583`)**, guarded by `run_mode == "governed"` and gating implementation
closeouts only (skip `terminal_status == "planned"`).
- Render the review bundle from the **staged index**: `git diff --cached -- *closeout_dirty_paths`
  (the exact bytes about to be committed). New files appear natively (no `--no-index` synthesis).
- Run the bounded `run_governed_premerge_loop` here; on a non-mergeable result, **return the
  existing `review_gate_block` LoopEvent directly** from `_perform_phase_closeout` (it already
  returns `(status, event)`) — do NOT commit. On mergeable, proceed to commit unchanged.
- **Skip the gate when `_closeout_nothing_staged(repo)` (`7546`)** — the legitimate issue-#6
  out-of-band finalize (verified work already on base); empty staged set there is safe-to-pass,
  NOT a fail-open.
- **Delete** the now-dead parallel path-discovery: `governed_bundle._owned_dirty_paths`,
  `_staged_diff`, `_is_untracked` (and the `git diff --no-index` untracked synthesis). The bundle
  renderer keeps only the formatting of acceptance-criteria/verification/summary + the cached diff.
- **Remove the old gate call at `runner.py:1884`** and its helper `_governed_premerge_gate` /
  `_make_governed_apply_fix` no-op (or repoint them to the new in-closeout site). Codex's catch:
  do NOT write a bundle file into `repo/.phase-loop/governed/<alias>` before closeout — review the
  index in-memory / stage review artifacts OUTSIDE the repo so the gate never dirties the worktree.
- This dissolves by construction: bundle-vs-commit divergence, R1 #3 (untracked), R2 `_is_untracked`
  fail-open, R2 N+1 subprocesses, and the repo-dirtying risk.

(Author identity: even at the new site, derive it from the dispatch state, per Change 2 — closeout
only has `selection`, not the executor.)

## Change 2 — Thread the actual executor; exclude the UNION of author vendors
Stop reverse-engineering author identity. Thread the real dispatched executor into the gate from
the call site (`work_unit_selected_executor` / `dispatch_decision.selected_executor`).
- Drop the `action in (execute/repair/plan)` filter in `_phase_author_vendor` (events log
  `action='run'`; the verb is `metadata.dispatch_decision.launch_action`) and the
  `author_vendor_for_model(selection.model)` primary fallback.
- Under rotation/repair multiple vendors author one phase → exclude the **UNION** of
  `author_vendor_for_executor(selected_executor)` across ALL the phase's dispatch events (every
  event carrying a non-empty top-level `selected_executor`), not just the latest.
- If the author set is unknown/ambiguous → **block** (non-human `review_gate_block`).
- Prefer the directly-threaded executor; keep the event-union as the source for prior rounds.
  (Gemini: delete `_phase_author_vendor` and pass the executor in; acceptable if the union of
  prior authors is still threaded for the multi-author case.)

## Change 3 — Strict fail-closed verdict contract + require a real disjoint reviewer
- Replace the negation-aware substring/regex guessing in `panel_invoker._classify_leg` (`115-127`)
  and `governed_review._leg_blocks` (`40-56`) with a **strict terminal contract**: the last
  non-empty line ≡ exactly one of {`AGREE`, `PARTIALLY AGREE`, `DISAGREE`} (or a `VERDICT: …`
  machine token). Non-conforming / empty / degraded / timeout → degraded/**block**, never a silent
  pass. (This is the one axis where fail-safe-on-uncertainty genuinely belongs.)
- **Require ≥1 usable disjoint reviewer**: if every selected leg is unusable but the pool is
  non-empty, today `has_block` is false and the artifact promotes (fail-open) → must **block**.
- Fix the double-fail-open at `governed_premerge.py:160-166`: returning `mergeable=True` when
  degraded BEFORE any block is seen lets a codex-empty phase whose only disjoint reviewer is
  offline both render empty AND advisory-pass. In governed mode, no-usable-disjoint-reviewer →
  block. If advisory-best-effort is still wanted, make it a SEPARATE mode, not silent inside
  `governed`.

## Tests (de-mask the prior false-greens)
- **Relocation:** a governed run with a codex-empty-classification phase (only `dirty_paths`
  populated) — the gate reviews the reclassified staged set (NOT empty), and a block holds the
  commit; `_closeout_nothing_staged` → gate skipped, commit is the no-op finalize. Assert the
  reviewed diff == the staged diff.
- **Author identity:** a phase dispatched with `action='run'` and `selected_executor='codex'` →
  the codex leg is excluded (use the REAL event shape, `action='run'`, not a fabricated
  `'execute'`); a rotation/repair phase with two authors → BOTH excluded.
- **Verdict:** bare `DISAGREE` blocks; "I cannot AGREE or DISAGREE…" is non-conforming → does NOT
  pass; all-legs-unusable-but-pool-nonempty → block; degraded-before-block + codex-empty → block.
- **Autonomous invariant:** the run-level zero-panel e2e regression still passes byte-identical.

## Verification
```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "governed or premerge or closeout or panel or routing" -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q          # full suite green
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v3.md
```

## Acceptance criteria
- [ ] The governed pre-merge gate runs INSIDE `_perform_phase_closeout` (after `git add`, before
      commit), reviews `git diff --cached`, and returns `review_gate_block` directly on a block —
      no commit. The pre-closeout gate at `runner.py:1884` is removed; `governed_bundle`'s parallel
      path discovery is deleted.
- [ ] Author vendor is derived from threaded/event `selected_executor` (union of all authors,
      `action`-filter dropped); unknown author → block. A test uses the REAL `action='run'` shape.
- [ ] Verdict classification is strict + fail-closed; ≥1 usable disjoint reviewer required; the
      degraded-before-block advisory-pass hole is closed.
- [ ] Autonomous default path byte-identical (zero-panel e2e regression green); full suite green;
      validate-roadmap OK; #12 drift guard green.
- [ ] Governed mode marked appropriately in docs (CHANGELOG/protocol) — the relocation + the
      remaining honest threads.
