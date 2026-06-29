# Decision-panel consensus — UNANIMOUS (c): minimal Layer A on top of v0.1.6

Three independent legs (native Claude Opus + repo access; Codex GPT-5.5 xhigh; Gemini 3.1 Pro) all chose **(c-with-spec)**. Neither (a) full-salvage nor (b) accept-R.

## The decision
- **R (v0.1.6, released) is NOT enough** — it's Layer-B-only (closeout gate, token-present staleness), structurally blind to the silent-absence case (version bumped, CHANGELOG simply not updated) and to the pipeline-bypass / direct-push / helper-absent evasion paths. Do not close #18 with R alone.
- **O (our branch) is NOT mergeable as-is** — its Layer A is directionally right but has fail-OPEN holes, and its taxonomy unification REGRESSED shipped code; its stale scanner is where the false-positives live (a noisy CI gate gets disabled).
- **Ship a minimal Layer A follow-up on top of R, additively, as v0.1.7.**

## Execution spec (reconciled across all three)
1. **Start from `origin/main` (v0.1.6).** New branch; abandon the conflicted PR #21.
2. **Bring over ONLY the net-new Layer A:** the pipeline-independent `docs_audit.py` (the `phase-loop docs-audit` CLI), a **standalone** `docs_surfaces.py` taxonomy for the audit's own use, `.github/workflows/docs-audit.yml`, the `cli.py` `docs-audit` subcommand dispatch, and the audit tests.
3. **DROP everything that touches shipped code or adds noise:**
   - `docs_stale_scan.py` and its import/call in the audit (P2 stays covered by R's shipped `BLOCK_TOKENS` in-pipeline scan; reintroduce a *reliable* scanner in a later PR).
   - O's overlapping Layer B: the `closeout.py`, `doc_delta_validator.py`, and `validate_plan_doc.py` changes (defer to R's released gate).
   - The taxonomy **re-export** through `models.py`/`release_guard.py` — leave those shipped modules UNTOUCHED (this is what caused regressions #7/#9/#10). Keep `docs_surfaces.py` standalone. Accept the temporary 3-copy taxonomy drift as a conscious deferral (a separate, tested unification PR later) — drift in new code beats regressing shipped controls.
   - The `**/package.json` release-set widening (would stall a real release-dispatch).
4. **Fix the Layer A fail-opens BEFORE merge (the only must-fixes):**
   - **#2 (FIRST, load-bearing):** `push:main` must diff the whole batch — `github.event.before...HEAD` (the push `before` SHA), with an all-zeros first-push guard. `HEAD~1` misses batched pushes (the loop batches), making the push:main leg near-useless.
   - **#1:** fail **CLOSED** on any `git diff` error (returncode ∉ {0,1} / OSError) — currently collapses to empty → `skipped` → exit 0 (violates its own invariant).
   - **#3:** add the `push: tags:['v*']` trigger (claimed in docs, absent in the workflow).
   - **#8:** first-tag / first-push base fallback (don't fail-closed on a project's first release for a non-docs reason).
5. **Tests:** pipeline-independent diff only (no `.phase-loop` state); a **version bump with no CHANGELOG change → `blocked`** (the silent-absence case R can't catch — the whole point); diff-error → fail-closed; batched push detection; tag path; release-surface→required-doc relevance binding; assert **no `release_guard` behavior change**.
6. **Ship additively as v0.1.7** (releasing twice for one issue is fine — a "pipeline-independent docs-audit backstop" follow-up is a better signal than pretending v0.1.6 closed a gap it can't).

## Dropped finding scope
Of the 27: must-fix = #1/#2/#3/#8 (CLI fail-opens/triggers). Avoided-by-not-touching-shipped-code = #7/#9/#10 (the regressions). Dropped-with-the-scanner = #4/#5/#6 (false positives). Net: ~4 contained fixes, not 27.
