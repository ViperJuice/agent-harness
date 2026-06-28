---
name: opencode-execute-phase
description: "OpenCode-optimized executor for a `<harness>-plan-phase` lane plan. Use when the user wants OpenCode to implement a planned phase. Executes lanes with clean git preflight, owned-file boundaries, verification, and optional explicit worker-subagent fanout for disjoint lanes."
---

# OpenCode Execute Phase

Executes a phase plan produced by `<harness>-plan-phase`. The default executor is the main OpenCode thread. Worker subagents are optional and only used when the user explicitly asks for subagents, delegation, or parallel execution.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Read the full phase plan before editing.
- Preserve user work. Never revert changes you did not make.
- Use the active session's file-editing tool for manual edits.
- When implementation depends on current external documentation, use PMCP first. Context7 is the preferred path for library docs; Bright Data or other search/scrape tools may be used only when `gateway_catalog_search` shows they are available.
- Keep lane ownership boundaries. If implementation requires touching another lane's files, stop and revise the plan or ask the user.
- Do not run destructive git commands such as `git reset --hard` or `git checkout -- <path>` unless the user explicitly requested that operation.
- Do not commit, push, or merge unless the user asked for those git actions.

## Runner-Owned Lane Work Units

Injected HARNESSLANE runs may include a `HarnessLaneAssignment` from
`shared/phase-loop/protocol.md`. Treat that assignment as the work-unit
contract: execute only the selected `lane_id`, write only the listed
`owned_files`, treat `consumed_interfaces` as read-only, and emit one shared
`automation:` closeout for that work unit. Installed-skill drift is
warning-only when the repo-injected context is present.

OpenCode lane launches must preserve explicit delivery and permission
metadata: `<harness> run`, `--dir`, `--agent`, provider-qualified `--model`,
optional `--variant`, `--format json`, and the shared context file. Review,
reducer, verify, and closeout prompts are distinct from implementation prompts.
If the selected policy is unsupported by the active harness, stop with a typed
non-human blocker instead of silently downgrading.

## Inputs

- Plan path: default latest `plans/phase-plan-*.md`.
- `--dry-run`: parse and print the lane schedule without editing.
- `--parallel`: allowed only when the user explicitly requests parallel worker execution.

If no plan path is explicit, first check the current repo and branch handoff from `<harness>-plan-phase` using `<harness>-config/shared/runtime-state.md`: read the repo-local handoff resolver target `.dev-skills/handoffs/<harness>-plan-phase/latest.md`, validate `from`, `repo`, `repo_root`, `branch`, `branch_slug`, `commit`, and `artifact`, then use the artifact only if it exists under the current repo root. Ignore missing or mismatched handoffs unless the user explicitly asks to reuse cross-branch state.

## Preflight

1. Resolve repo root and plan path.
2. Run `git status --short`.
3. Run `git status --short -- <plan_path>` and warn if the plan is untracked.
4. If the tree has unrelated dirty files, leave them alone and scope edits around them.
5. Parse:
   - interface gates;
   - lane DAG;
   - owned files;
   - task lists;
   - verification commands;
   - optional `Dispatch Hints` from the roadmap or plan using the frozen
     vocabulary from `shared/phase-loop/protocol.md`.
6. Validate producer dependencies:
   - any lane that consumes another lane's findings, interfaces, or artifacts must list that producer lane in `Depends on`;
   - any lane that writes a synthesized artifact must be downstream of every producer lane it summarizes;
   - if dependencies are missing, stop and require a plan correction before execution.
7. For `--dry-run`, report the topological lane order and stop.

## Execution Workflow

1. Execute lanes in topological order.
2. For each lane:
   - when the selected executor is OpenCode live, keep the launch contract
     explicit around `<harness> run`, `--dir`, `--agent`, provider-qualified
     `--model`, optional `--variant`, `--format json`, and the shared
     `context_file` artifact;
   - record OpenCode permission posture explicitly and refuse permissive
     defaults unless the runner intentionally opts in;
   - treat installed-skill drift as warning-only metadata when injected repo
     context is present and the child otherwise succeeds;
   - read the owned files and related tests;
   - write or update tests first when practical;
   - implement only lane-scoped changes;
   - run lane verification (normalize any phase-plan pytest `-k` selector whose prose terms contain spaces — e.g. `remote connect` becomes `'remote and connect'` — or quote it; a bare spaced `-k` term is an executor-side normalization, not a plan blocker);
   - run any phase-level checks that cover touched files.
3. After each lane:
   - inspect `git diff -- <owned files>`;
   - confirm no peer-owned files were modified;
   - record completed gates.
4. After all lanes:
   - run the full phase verification commands;
   - if execution discoveries change downstream work, amend the nearest
     downstream roadmap phase that is not already executing;
   - treat any older downstream phase plan or handoff as stale after a roadmap
     amendment and route the next step back through `<harness>-plan-phase`;
    - describe the change explicitly as a roadmap amendment in the final
      closeout;
   - summarize changed files, tests run, and any residual risks.

## Optional Worker Fanout

Use worker subagents only when the user explicitly authorizes parallel agent work and the DAG-ready lanes are disjoint.

For every worker brief:

- State that they are not alone in the codebase.
- Assign exact owned files or globs.
- List files they may read but not edit.
- Tell them not to revert unrelated changes.
- Require a final response with changed paths, tests run, failures, and blockers.
- Do not give two workers overlapping write ownership.

The main thread remains responsible for integrating results, reviewing diffs, running final verification, and resolving conflicts.

## Failure Policy

- Test failure in a lane: diagnose once, fix within the lane if the cause is local, otherwise stop and report.
- Ownership violation: stop and revise the plan before continuing.
- Missing dependency or unclear interface: stop and route back through planning.
- Verification command unavailable: report the missing tool and run the closest available static check only if it is meaningful.
- Use only the frozen blocker taxonomy from `shared/phase-loop/protocol.md`,
  including `missing_secret`, `dirty_worktree_conflict`,
  `branch_sync_conflict`, and `repeated_verification_failure`.


## Runner Verification Evidence

Before reporting a successful closeout, require the runner-owned verification artifact. The closeout must name `verification_artifact_path`, quote the artifact summary line, and must not report `verification_status=passed` unless that artifact exists and supports the executed work. Treat dependency-manifest install refresh and the full suite before closeout as runner-enforced expectations, not optional narrative checks. A blocked gate may be re-verdicted only by rerunning the originally specified runner check; proxy evidence requires a roadmap or plan amendment before the verdict changes. A prior run's terminal-summary is authoritative for reconcile-or-skip only when that closeout was accepted; a rejected or blocked prior closeout — regardless of any self-reported `complete`/`passed` — must not be reconciled against. On a re-run, re-do and re-verify the phase work from scratch rather than skipping on a stale summary.


## Spec Delta Closeout

Before final closeout, choose exactly one `spec_delta_closeout.v1` decision: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Cite metadata-only evidence paths such as the active plan, lane closeouts, targeted pytest output, and `git diff --check` output. Preserve the phase plan's target surfaces and `redaction_posture=metadata_only`; do not include raw specification bodies, raw patch bodies, credentials, provider-supplied payloads, local environment values, or evidence-source contents. Missing or malformed spec-closeout evidence is a repairable automation blocker with `blocker_class=contract_bug` unless the decision is `human_source_judgment_required`.

## Closeout

### Manifest lifecycle

After plan validation and before lane execution, perform a best-effort `plan-manifest append` lifecycle update through `phase_loop_runtime.plan_manifest.update_lifecycle` to mark the matching `type=phase` entry `executing` with run metadata. During closeout, update the same entry to `completed` or `failed` with verification metadata, reflection metadata, produced-gate metadata, `if_gates_produced`, and dirty-worktree summary fields as available. `if_gates_produced` must list only the IF gates the active phase produces per its own plan; never carry a prior phase's gate forward into this phase's closeout. Manifest lifecycle failures are non-fatal during the dual-mode window: emit a ledger warning, mention the warning in the mandatory reflection, and preserve the existing bounded phase execution, closeout expectations, and dirty-worktree behavior.

Before final closeout, run `git status --short -- <plan_path> <roadmap_path>` for every consumed or updated planning artifact. If any planning artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <path>` for each artifact. Rerun status and report `Artifact state: staged|tracked|modified|unstaged|blocked` for each artifact. Do not commit unless requested. Repo-local handoff files are operational state: do not `git add` an ignored handoff alongside the plan artifact unless the plan's owned-files/allowlist explicitly includes the handoff directory; leave ignored handoffs ignored and exclude them from artifact-state reporting.

Determine the next step before final response and handoff:

- If the current phase is incomplete or verification failed, report `Next phase: <current alias> - blocked: <blocker>` and `Next command: none - <blocker>`.
- If another generated phase plan is ready, report `Next phase: <next alias> - execution ready` and `Next command: <harness>-execute-phase <next_plan_path>`.
- If the roadmap has an unplanned ready phase, report `Next phase: <next alias> - planning ready` and `Next command: <harness>-plan-phase <roadmap_path> <next_alias>`.
- If the roadmap needs extension, report `Next phase: none - roadmap extension needed` and `Next command: <harness>-phase-roadmap-builder <roadmap_path>`.
- If all phases are complete, report `Next phase: none - roadmap complete` and `Next command: none - roadmap complete`.

Add a machine-readable `automation:` handoff with `verification_status` that agrees with the human-readable next step fields, and use the phrase `manual event` when standalone compatibility event import applies. Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.

Report:

- lanes completed;
- files changed;
- planning artifact tracking state;
- next phase and next command;
- verification commands and results;
- commands not run and why;
- follow-up risks or manual checks.

Resolve closeout writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use OpenCode paths only:

- Reflection: `resolve_skill_bundle_root("codex")/<harness>-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-execute-phase/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-execute-phase/latest.md`

Handoff frontmatter must include `from: <harness>-execute-phase`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, `artifact:`, `artifact_state:`, `next_skill:`, `next_command:`, and `next_phase:`. Put open follow-up items in the body, and update `latest.md` with the same handoff content.
