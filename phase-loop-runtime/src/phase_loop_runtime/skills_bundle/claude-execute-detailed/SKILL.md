---
name: claude-execute-detailed
description: "Harness Code bounded-plan executor for one detailed implementation plan with verification, acceptance reduction, and mandatory reflection closeout."
---

# Harness Execute Detailed

Executes one detailed plan artifact produced by `<harness>-plan-detailed`.
Use this for bounded implementation work where a single Harness Code thread can
carry the change end to end without phase lanes, lane ownership, or worker
worktrees.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots,
handoff roots, helper roots, and reflection roots.

- Read the detailed plan artifact before editing.
- Optionally read `.dev-skills/handoffs/<harness>-plan-detailed/latest.md`
  when no explicit plan path is supplied, and only trust it if `from:` is
  `<harness>-plan-detailed`, the repo fields match the current repo, and the
  artifact path resolves under the current repo root.
- Preserve user work. Never revert changes you did not make.
- Use `apply_patch` for manual edits.
- Keep implementation bounded to the detailed plan's `## Changes` section.
- Do not add unrelated refactors, speculative validation, or broad cleanup.
- Treat ignored, private, raw-data, credential, and evidence-source files as
  read-protected unless the detailed plan explicitly allowlists the exact path
  or glob for read access.
- Git safety invariant: never commit to `main` or a protected branch, never commit from
  a dirty primary checkout, and never commit/push onto a branch this run does not own (a
  shared or pre-existing colleague branch). When executing a plan, publishing a PR from a
  dedicated branch is the default outcome (see the Workflow + "## Publication") unless a
  runner owns closeout, or the user asked for local-only, planning-only, or no-publication
  work, or verification failed. Never merge, force-push, reset, delete a publication
  branch/worktree holding unmerged work, or run other destructive git commands without
  explicit instruction.

## Inputs

- Plan path: explicit detailed plan artifact, or the latest valid
  `.dev-skills/handoffs/<harness>-plan-detailed/latest.md` artifact.
- Optional handoff: repo-local handoff from `<harness>-plan-detailed`.

## Workflow

1. Resolve the repo root and detailed plan artifact.
2. Run `git status --short --untracked-files=all` and preserve the starting
   dirty-path list for closeout comparison.
3. Read the plan artifact in full.
4. Validate that it contains `## Changes`, `## Verification`, and
   `## Acceptance criteria`. If any section is absent or empty, stop with
   `acceptance criterion unmet` and explain the missing section.
5. Best-effort mark the detailed plan manifest entry as `executing` through
   `phase_loop_runtime.plan_manifest.update_lifecycle` (`plan-manifest append`)
   after plan validation and before source edits. Use `by=<harness>-execute-detailed`
   and metadata that identifies the run and plan artifact. Manifest lifecycle
   failures emit ledger/reflection warnings only and do not abort execution
   during the dual-mode window.
6. **Establish the safe working tree BEFORE editing** (unless a runner owns closeout — see step 2 / the Git safety invariant): resolve a fresh base ref (`origin/<default-branch>` after fetch, or an explicit merge target; never a stale local `main`) and select the working tree — reuse the current branch only if it is clean, non-protected, AND owned by this run; otherwise (`main`/a protected branch, a shared/foreign branch, or a dirty primary checkout) create a worktree+branch off the base: `git worktree add <path> -b <branch> <base>` under `/mnt/workspace/worktrees/<project>-<branch-slug>` if `/mnt/workspace` exists, else a repo sibling `../<project>-<branch-slug>`. Do ALL editing, the `git status` snapshots, the dirty-path classification, and verification in that tree — never on `main`/protected/dirty/shared. Then apply the `## Changes` section in declared order. For manual edits, use
   `apply_patch`. If a requested edit requires a file not listed in
   `## Changes`, stop and report `dirty worktree from non-plan output` rather
   than expanding scope silently.
7. Run every command in `## Verification` unless the command is unsafe,
   unavailable, or clearly requires credentials not provided by the plan. Report
   each command with pass/fail status.
8. Compare the final repository state against every item in
   `## Acceptance criteria`. Mark each item satisfied or unmet.
9. Run `git status --short --untracked-files=all` again. Classify every dirty
   path as plan-owned, pre-existing unrelated, or non-plan output.

## Failure Diagnostics

- `verification failed`: a command from `## Verification` failed after one local
  diagnosis and repair attempt, or the failure is outside the detailed plan's
  scope.
- `acceptance criterion unmet`: a required `## Acceptance criteria` item is not
  satisfied, or the detailed plan lacks `## Changes`, `## Verification`, or
  `## Acceptance criteria`.
- `dirty worktree from non-plan output`: closeout finds a dirty path that was
  not pre-existing and is not produced by the detailed plan's declared changes.
- `publication blocked`: a publication precheck failed (no remote / no push auth / no `gh` /
  branch-name collision / push rejected / unresolved base ref), or publishing would otherwise
  require committing to `main`/a protected branch or a branch this run does not own. Verified
  work is preserved locally on a safe branch/worktree; report what blocked the PR.


## Runner Verification Evidence

Before reporting a successful closeout, require the runner-owned verification artifact. The closeout must name `verification_artifact_path`, quote the artifact summary line, and must not report `verification_status=passed` unless that artifact exists and supports the executed work. Treat dependency-manifest install refresh and the full suite before closeout as runner-enforced expectations, not optional narrative checks. A blocked gate may be re-verdicted only by rerunning the originally specified runner check; proxy evidence requires a roadmap or plan amendment before the verdict changes.

## Closeout

### Manifest lifecycle

During closeout, best-effort call `phase_loop_runtime.plan_manifest.update_lifecycle` with `completed` when verification and acceptance passed, or `failed` when verification failed, acceptance was unmet, or closeout found non-plan output. Include verification metadata, reflection metadata, dirty-path classification, and the final diagnostic in the lifecycle event. Manifest lifecycle failures are non-fatal during the dual-mode window: emit a ledger warning, record the failure in the mandatory reflection, and preserve the existing detailed executor closeout.

Before final response, write a reflection for every non-trivial run. Write it to
`resolve_skill_bundle_root("codex")/<harness>-execute-detailed/reflections/<repo_hash>/<branch_slug>/<run_id>.md`.
The reflection must include `## Run context` with skill name, ISO timestamp,
repo, branch, commit, and artifact path, followed by `## What worked`,
`## What didn't`, and `## Improvements to SKILL.md`. Skip only when no artifact
was executed, no edit was made, and no decision was made.

Final response must include:

- detailed plan artifact path;
- files changed;
- verification commands with pass/fail status;
- acceptance criteria with satisfied/unmet status;
- final dirty-path classification;
- next action or blocker using the stable failure diagnostics above.


## Publication

After a verified implementation, publishing a PR from the working tree established in
Workflow step 6 is the default outcome — under the Git safety invariant in Core Rules.
Defer entirely if a runner / pipeline owns closeout (a phase-loop-runner-driven or adapter
run, or an explicit runner-owned-closeout instruction): the runner owns commit and closeout.
Do NOT infer "runner owns closeout" from `verification_artifact_path` — every closeout
records it; it does not signal a runner. The following applies for human-invoked plan
execution.

Call `phase_loop_runtime.publishing.publish_from_worktree(repo, owned_paths, draft=<intent>)`
(IF-0-P1-1 — the runtime publication primitive), supplying the worktree root, the plan's
owned paths, and the draft/ready intent:
- `draft=True` when dependencies remain or verification was partial/skipped;
- `draft=False` when verification is complete (skipped or partial verification never opens
  a ready PR).

The primitive performs the complete #28 flow in code, not prose:

1. **Preflight.** Confirms the branch is non-protected, non-main, and not in a
   dirty/unowned state; confirms a remote, push auth, and the `gh` CLI. A preflight
   failure returns `{status: "publication_blocked", reason: ...}` — keep the committed
   work on the safe branch/worktree and STOP at publish with `publication blocked`;
   never on `main`/a protected branch, and never imply a PR exists.
2. **Stage + audit.** Stages only owned paths (`git add -- <paths>`, never `git add -A`),
   audits the staged set (rejects any ignored, private, raw-data, credential, or `.env`
   path; runs `git diff --cached --check`), and fails closed on any violation — path-scoping
   alone does not catch a secret inside an owned file; the read-protection rules and this
   audit are the backstop.
3. **Commit + push.** Commits the audited diff and pushes without force. A rejected push
   (divergent / non-fast-forward / branch protection) returns `publication_blocked` — never
   force-push or merge to resolve it.
4. **Open the PR.** `gh pr create --draft` or ready (`--fill`) per the intent flag.

Returns `{status: "published", branch, head_sha, pr_url}` on success (IF-0-P1-1; report
`pr_url` in the final response and include `head_sha` in the handoff for downstream
consumers). Returns `{status: "publication_blocked", reason: <slug>}` on any violation —
report `publication blocked` with the reason and stop.
