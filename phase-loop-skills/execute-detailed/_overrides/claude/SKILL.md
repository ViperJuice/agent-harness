---
name: execute-detailed
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
- Do not commit, push, merge, or run destructive git commands unless the user
  explicitly requested that operation.

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
6. Apply the `## Changes` section in declared order. For manual edits, use
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
