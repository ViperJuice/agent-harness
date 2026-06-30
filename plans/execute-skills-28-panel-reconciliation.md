# Advisor-panel reconciliation — #28 execute-skills publication-default plan

3 legs (native Claude Opus + repo access; Codex GPT-5.5; Gemini 3.1 Pro). **Not ready as-is** — 1 DISAGREE (Claude), 2 PARTIALLY AGREE. Heavy convergence; all repo-grounded where possible.

## Corrections to r1 (things the plan got wrong)
- **§A is a WORKFLOW REWRITE, not a bullet swap** (Claude, Codex): execute-detailed is fully in-place (step-2/9 dirty snapshots, plan-owned dirty-path classification, "preserve user work") with no worktree/commit/push/PR concept. Dropping "use a worktree" as one bullet is self-contradictory; cross-tree plan-artifact reading + manifest lifecycle must be rewired.
- **The ban is execute-detailed ONLY** (Claude, verified): the 4 execute-phase files have no ban line → execute-phase edits are ADDITIVE, not "ban removal" (r1's "8 files" framing wrong).
- **HEADLINE SAFETY HOLE** (Claude): execute-phase's interactive orchestrator auto-merges lanes into `EXECUTE_MERGE_TARGET` (default current branch), and `main` is a permitted target → it can merge straight onto local `main`, violating the plan's own "never commit to main" invariant. §B must STOP when the merge target is main/protected.
- **Primitive is invariant-first, worktree-as-fallback** (Claude, all on brittleness): "always create a worktree" fragments an already-clean dedicated branch, splits plan from impl, orphans worktrees. Invariant = "no commit to main/protected or from a dirty checkout"; worktree only when not already on a clean dedicated branch.
- **Harness-NEUTRAL wording** (Claude, Gemini): build_bundle factors to a shared base + per-harness `_overrides`; per-harness "voice" wording bloats the bundle + parity diff. Neutral text collapses to the base.

## Missing safety mechanics (added in r2)
- **Publication preflight** before editing (Codex): fresh base ref, protected-branch check, remote/push-auth/PR-tool/branch-collision — else verified work then can't PR.
- **Staged-diff audit before commit** (all three, Codex strongest): scoped path-staging is insufficient (dir/glob can stage secrets/.env/artifacts); require `git diff --cached` audit == owned set, no ignored/private/credential paths, `--check`, fail-closed on unexpected delta; tie "plan-owned" to the existing classification.
- **Push-rejection → STOP** (all): divergent/non-FF/branch-protection → fail closed, never force-push/merge.
- **Skipped/partial verification ≠ passed** (Claude): step-7 skips unsafe/unavailable cmds → draft-only or stop, never ready-PR.
- **Runner/manifest carve-out for execute-detailed** (Claude): it's manifest-supervised → defer when an outer runner owns closeout.
- **Base-ref staleness** (all): specify origin/<default> (fetched) or the merge target, never stale local main.
- **execute-phase runner detection**: Claude verified the adapter-mode prefix is deterministic; Gemini warns a prompt-prefix is fragile (a copied command bypasses the governed panel) → use it PLUS a runner env signal (`PHASE_LOOP_RUN_MODE`), and **defer when ambiguous** (fail safe toward the runner; governed is a sub-case of runner-owned).
- **Worktree location/cleanup conflict** (Codex): execute-phase uses repo-local `.worktrees/`, not `/mnt/workspace`; reconcile, and distinguish allowed runner cleanup (forced removal + `branch -D`) from forbidden publication-branch deletion.
- **Verification grep brittle** (Codex): `grep -c # expect 0` exits non-zero on no match → use explicit negative (`! git grep`) + positive checks.

## Descoped (panel)
- **Cross-repo** (all): execute-detailed has no multi-repo plan schema; partial-success ordering + per-worktree verification need a real design → file as a separate follow-up; ship single-repo MVP.

All folded into `plans/detailed-fix-execute-skills-worktree-pr.md` (r2).
