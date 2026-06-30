# Detailed plan (r2, panel-reconciled) — #28: execute skills default to a published review surface

> r1 was reviewed by the 3-model advisor panel (native Claude + repo access, Codex GPT-5.5, Gemini 3.1 Pro). Verdict: **not ready as-is** (1 DISAGREE, 2 PARTIALLY AGREE). r2 folds in the reconciliation. Key corrections: §A is a *workflow rewrite*, not a bullet swap; the ban is in **execute-detailed only** (execute-phase edits are additive); `execute-phase` can already merge lanes onto local `main` — the headline safety hole, now closed; the primitive is **invariant-first**, worktree-as-fallback; plus the missing safety mechanics (preflight, staged-diff audit, push-rejection stop, runner carve-outs).

## Task
The execute skills leave a **verified** implementation as dirty changes in the primary checkout with no PR/review surface (the #28 "why no PR?"). Change the default so a verified implementation run lands on **its own branch with a PR**, under a hard safety invariant — without ever committing to `main`/protected or from a dirty checkout.

## Core invariant (the primitive — panel-reconciled framing)
The safety property is **not** "always make a worktree" (too brittle — it fragments an already-clean dedicated branch, splits the plan artifact from the implementation, and orphans worktrees). It is:

> **Never commit to `main`/a protected branch, and never commit from a dirty primary checkout.**
> If already on a **clean, non-protected** branch → commit there. Otherwise → create a dedicated
> **worktree + branch** off the resolved base, work there, and publish from it.

Worktree isolation is the *fallback that satisfies the invariant*, not the goal.

## Research summary (verified against `main` @ v0.1.9 + panel repo-verification)
- **Canonical source** is `skills-src/<harness>/<harness>-<skill>/SKILL.md`; `regenerate_skills_bundle.py` (→ `phase-loop-skills/`) + `sync_skills_bundle.py` (→ packaged `skills_bundle/`); hard parity gate (`test_skills_canon_parity.py`/`skills-parity.yml`) + drift guard. **Source-first then regenerate; never hand-edit generated trees.**
- **`build_bundle` factors the 4 per-harness sources into a shared base + per-harness `_overrides`.** The ban currently lives in the base + 3 overrides (codex is the base). ⇒ **New policy text MUST be harness-NEUTRAL** so it collapses to the shared base; override only where a harness genuinely differs. Per-harness "voice" wording (r1's instruction) was wrong — it bloats the bundle + parity diff.
- **The ban (`Do not commit, push, merge…`) is in `execute-detailed` ONLY.** The 4 `execute-phase` files have no ban line — so execute-phase edits are **additive**, not "ban removal." (r1's "8 files / ban removed" framing was wrong.)
- **execute-detailed today is fully in-place:** Workflow steps snapshot `git status --short` at start, apply `## Changes` in the primary tree, re-`git status` at closeout, and classify every dirty path (plan-owned / pre-existing / non-plan output). It is also **manifest/runner-supervised** (`verification_artifact_path`, `plan_manifest.update_lifecycle`). It has **no worktree/branch/commit/push/PR concept**. ⇒ §A is a **workflow rewrite**, and execute-detailed needs a **runner/manifest carve-out**.
- **execute-phase is runner-aware:** adapter mode triggers when the prompt starts with `<harness>-execute-phase <plan>` from `.codex/phase-loop/runs/` (`injection.py:157`); it has per-lane `EnterWorktree`, **Step 7 auto-merges lanes into `EXECUTE_MERGE_TARGET` (default = current branch)**, Step 9 clean-tree, `sweep_stale_worktrees.sh`, and uses repo-local `.worktrees/`/`.claude/worktrees/`. **"When NOT to use" permits `main` as the merge target** ⇒ the interactive orchestrator path can merge lanes straight onto local `main`. **This is the headline safety hole and §B must stop it.**
- **Governed mode (model-routing-v2)** is a runner-side pre-merge gate that runs only when the runner owns closeout — a strict **sub-case of adapter/runner mode**. So the §B "defer to runner" rule also prevents bypassing/double-reviewing governed. The §B human-invoked PR path must NOT fire in adapter/runner mode (else it bypasses the governed panel).

## Changes

### §A — `execute-detailed`: workflow rewrite (not a bullet swap)
Rewire the skill end-to-end so publication is the default outcome of a verified implementation run, honoring the core invariant:
1. **Runner/manifest carve-out (first):** if an outer runner/manifest owns closeout (manifest-supervised run / `verification_artifact_path` provided by a runner), DEFER — do not independently publish (same posture §B gives execute-phase).
2. **Preflight (before editing):** resolve a **fresh base ref** (default `origin/<default-branch>` after fetch, or an explicit merge target — never a stale local `main`); confirm current branch is **not** `main`/protected and the primary checkout is **clean**; confirm remote + push auth + PR tool (`gh`) availability + no branch-name collision. If any precheck that blocks publication fails → record it and continue local-only, reporting clearly at the end (do verified work, but never silently imply a PR exists).
3. **Workspace selection (invariant-driven):** if already on a clean non-protected branch → use it. Else create a worktree+branch off the resolved base — under `/mnt/workspace/worktrees/<project>-<branch-slug>` if `/mnt/workspace` exists, else a repo-local `.worktrees/` (match execute-phase's convention, not a new location). Read the plan/handoff artifact from the **primary** checkout by absolute path; write closeout/reflection/manifest-lifecycle into the run's worktree.
4. **Implement** the plan's `## Changes` in the chosen tree; rewire the step-2/step-9 dirty snapshots and the **plan-owned dirty-path classification** to operate in that tree.
5. **Verify** per the plan. **Skipped/partial verification ≠ passed** — a skipped-because-unsafe/unavailable command means the publish, if any, is **draft-only** (or stop), never a ready PR.
6. **Scoped staged-diff audit, then commit:** stage only the **plan-owned** paths (by explicit path, derived from the existing classification — never `git add -A`). Then **audit the staged set before committing**: `git diff --cached --name-only` must equal the owned/allowlisted set; no `.gitignore`d / private / credential / `.env` paths; `git diff --cached --check`; **fail closed on any unexpected staged or unstaged delta.** (Path-staging alone can't catch a secret *inside* an owned file — the audit + the existing read-protection rules are the backstop.)
7. **Push + PR:** push the branch (no force). **If push is rejected** (divergent/non-fast-forward/branch-protection) → **stop and report; never force-push or merge.** Open a PR — **draft** if deps remain or verification was partial/skipped, **ready** when verification is complete.
8. **Never** merge, force-push, reset, delete a publication branch/worktree with unmerged work, or commit to a protected branch without explicit instruction.

### §B — `execute-phase`: integrate into the real state machine (additive), close the main-merge hole
Add a publication-mode section reconciled with Step 7 (lane auto-merge) / Step 9 (clean tree). Three explicit states:
- **(a) Adapter / runner-managed closeout (incl. governed mode):** unchanged — defer entirely to the runner's closeout (`awaiting_phase_closeout` / runner commit). The §B human PR path must NOT fire here (else it bypasses the governed pre-merge panel). Detect via the existing adapter-mode signal **plus** a runner env signal (`PHASE_LOOP_RUN_MODE` / pipeline run dir) — and **when detection is ambiguous, DEFER** (fail safe toward the runner).
- **(b) Interactive orchestrator on a clean, non-protected feature branch:** after Step 9's clean-tree, push the merge-target branch + open a PR (draft/ready per verification), instead of leaving it as a local merge.
- **(c) Interactive orchestrator whose merge target is `main`/a protected branch:** **STOP and report — do not merge lanes onto it.** (Closes the headline hole.)
- Reconcile bullet "never report `complete` while verified changes sit dirty with no PR" with `awaiting_phase_closeout` (a deliberate non-complete terminal the runner owns) — it applies to state (b) only.
- **Cleanup distinction:** execute-phase's existing forced lane-worktree removal + `branch -D` (runner hygiene, `sweep_stale_worktrees.sh`) is allowed; the new destructive-op ban targets **publication** branches/worktrees with unmerged work.

### §C — harness-neutral wording + exact commands
Write §A/§B as **harness-neutral** text (collapses to the build base). Provide the **exact git command structure** for worktree-create / scoped-stage / staged-audit / push / `gh pr create` so 4 harnesses + model tiers don't drift (e.g. `git worktree add <path> -b <branch> <base>`; `git add -- <owned paths>`; `git diff --cached --check`; `gh pr create --draft|--fill`).

### §D — propagate + gates
Edit the canonical `skills-src/<harness>/...` files → `regenerate_skills_bundle.py` → `sync_skills_bundle.py`; **parity + drift gates green**; full suite green.

## Out of scope (descoped per panel — file as a follow-up)
**Cross-repo publication** (one branch/PR per repo, dependency links, partial-success ordering, per-worktree verification). execute-detailed is "one bounded plan, single thread" with no multi-repo plan schema; doing it right needs a multi-repo plan section. Descope to a separate issue; note it in #28. The single-repo publish default is the high-leverage MVP.

## Verification (fixed — r1's `grep -c # expect 0` is brittle: no-match exits non-zero)
```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q
# NEGATIVE (ban gone from execute-detailed canon + generated + packaged):
! git grep -nI 'Do not commit, push, merge' -- 'skills-src/**execute-detailed*' 'phase-loop-skills/**execute-detailed*' 'phase-loop-runtime/**skills_bundle/**execute-detailed*'
# POSITIVE (new invariant present in all canonical + regenerated + packaged copies):
for layer in 'skills-src' 'phase-loop-skills' 'phase-loop-runtime/src/phase_loop_runtime/skills_bundle'; do
  git grep -lI 'never commit to .*protected\|worktree' -- "$layer/**execute-detailed*" | wc -l   # expect the full set
done
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q   # full suite green
```

## Acceptance criteria
- [ ] `execute-detailed` is rewired to publish a PR by default after verified implementation, honoring the **invariant** (no commit to main/protected or from a dirty checkout; worktree as fallback), with a runner/manifest carve-out, preflight, staged-diff audit, push-rejection stop, and skipped-verify→draft/stop.
- [ ] `execute-phase` gains the 3-state publication policy; the **merge-target-is-main/protected → STOP** rule closes the local-main-merge hole; adapter/governed defers (panel never bypassed); runner cleanup vs publication-branch deletion distinguished.
- [ ] Policy text is **harness-neutral** (collapses to the build base — no per-harness override bloat) with exact git commands; canonical edits regenerated; **parity + drift + full suite green**.
- [ ] CHANGELOG + skill-matrix docs note the new default; cross-repo descoped to a filed follow-up.
