---
name: execute-detailed
description: "OpenCode bounded-plan executor for one detailed implementation plan with verification, acceptance reduction, and mandatory reflection closeout."
---

# OpenCode Execute Detailed

Executes one detailed plan artifact produced by `<harness>-plan-detailed`. Use
this when one OpenCode thread should implement a bounded plan end to end without
phase lanes or cross-harness dispatch.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots,
handoff roots, helper roots, and reflection roots.

- Read the detailed plan artifact before editing.
- Optionally read `.dev-skills/handoffs/<harness>-plan-detailed/latest.md` when
  no explicit plan path is supplied, and only trust it if `from:` is
  `<harness>-plan-detailed`, the repo fields match the current repo, and the
  artifact path resolves under the current repo root.
- Preserve user work. Never revert changes you did not make.
- Use `apply_patch` for manual edits when available; otherwise use the active
  session's file-editing tool with the same narrow diff discipline.
- Keep implementation bounded to the detailed plan's `## Changes` section.
- Treat ignored, private, raw-data, credential, and evidence-source files as
  read-protected unless the detailed plan explicitly allowlists the exact path
  or glob for read access.
- Git safety invariant: never commit to `main` or a protected branch, and never
  commit from a dirty primary checkout. After a verified implementation, publishing a
  PR is the default outcome (see "## Publication") unless the user asked for local-only,
  planning-only, or no-publication work, or verification failed. Never merge,
  force-push, reset, delete a publication branch/worktree holding unmerged work, or run
  other destructive git commands without explicit instruction.

## Inputs

- Plan path: explicit detailed plan artifact, or the latest valid
  `.dev-skills/handoffs/<harness>-plan-detailed/latest.md` artifact.
- Optional handoff: repo-local handoff from `<harness>-plan-detailed`.

## Workflow

1. Resolve the repo root and detailed plan artifact.
2. Run `git status --short --untracked-files=all` and keep the starting dirty
   paths for closeout comparison.
3. Read the plan artifact in full.
4. Validate that it contains non-empty `## Changes`, `## Verification`, and
   `## Acceptance criteria` sections.
5. Best-effort mark the detailed plan manifest entry as `executing` through
   `phase_loop_runtime.plan_manifest.update_lifecycle` (`plan-manifest append`)
   after plan validation and before source edits. Use `by=<harness>-execute-detailed`
   and metadata that identifies the run and plan artifact. Manifest lifecycle
   failures emit ledger/reflection warnings only and do not abort execution
   during the dual-mode window.
6. Apply the `## Changes` section in declared order. Use `apply_patch` or the
   active file-editing tool for manual edits. If implementation requires a file
   or behavior not named by the plan, stop and report
   `dirty worktree from non-plan output`.
7. Run every command in `## Verification` unless unsafe, unavailable, or blocked
   by missing credentials. Report each command with pass/fail status.
8. Compare the final state against each item in `## Acceptance criteria` and
   report satisfied or unmet status.
9. Run `git status --short --untracked-files=all` and classify every dirty path
   as plan-owned, pre-existing unrelated, or non-plan output.

## Failure Diagnostics

- `verification failed`: a command from `## Verification` failed after one local
  diagnosis and repair attempt, or the failure is outside the detailed plan's
  scope.
- `acceptance criterion unmet`: a required `## Acceptance criteria` item is not
  satisfied, or the detailed plan lacks `## Changes`, `## Verification`, or
  `## Acceptance criteria`.
- `dirty worktree from non-plan output`: closeout finds a dirty path that was
  not pre-existing and is not produced by the detailed plan's declared changes.


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

Final response must include the detailed plan artifact path, changed files,
verification command results, acceptance-criteria status, final dirty-path
classification, and next action or blocker.


## Publication

After a verified implementation the default outcome is a pushed branch + PR, but only
under the git safety invariant in Core Rules. Apply this flow:

1. Defer if a runner owns closeout. If the run is runner/manifest-supervised — a
   runner-provided `verification_artifact_path`, or a phase-loop-runner-driven run — do
   NOT independently publish; the runner owns commit and closeout. The rest of this
   section is for human-invoked runs only.
2. Preflight (before editing). Fetch and resolve a fresh base ref (the remote default
   branch `origin/<default-branch>`, or an explicit merge target) — never a stale local
   `main`. Confirm the current branch is not `main`/protected and the primary checkout is
   clean; confirm a remote, push auth, the `gh` CLI, and that the intended branch name is
   free. If any publication precheck fails, continue local-only and say so explicitly at
   the end — never imply a PR exists when it does not.
3. Choose the workspace (honor the invariant). If already on a clean, non-protected
   branch, work there. Otherwise create a dedicated worktree + branch off the resolved
   base — `git worktree add <path> -b <branch> <base>` — under
   `/mnt/workspace/worktrees/<project>-<branch-slug>` if `/mnt/workspace` exists, else a
   repo-local `.worktrees/<branch-slug>`. Read the plan/handoff artifact from the primary
   checkout by absolute path; perform all editing, the starting `git status` snapshot, the
   closeout dirty-path classification, verification, and manifest-lifecycle writes in the
   chosen working tree.
4. Stage only plan-owned paths, then audit before committing. Stage by explicit path
   (`git add -- <owned paths>`), never `git add -A`. Then audit the staged set:
   `git diff --cached --name-only` must equal the plan-owned set; reject any ignored,
   private, raw-data, credential, or `.env` path; run `git diff --cached --check`. Fail
   closed (stop and report) on any unexpected staged or unstaged delta — path-scoping
   alone does not catch a secret inside an owned file; the read-protection rules and this
   audit are the backstop.
5. Commit, push (no force), open a PR. Commit the audited diff and `git push` the branch
   without force. If the push is rejected (divergent / non-fast-forward / branch
   protection), STOP and report — never force-push or merge to resolve it. Open a PR with
   `gh pr create` — `--draft` if dependencies remain or verification was partial/skipped,
   ready (`--fill`) when verification is complete. Skipped or partial verification is not
   a pass: it caps the PR at draft, or stops.

New stop conditions: `publication blocked` (a precheck failed — no remote / no push auth /
no `gh` / branch-name collision / push rejected / unresolved base ref; verified work is
preserved locally, report what blocked the PR); `protected branch` (the run would
otherwise commit to `main`/a protected branch or from a dirty primary checkout — stop
publication and report).
