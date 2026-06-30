---
name: gemini-execute-phase
description: "Gemini-optimized executor for a `gemini-plan-phase` lane plan. Use when the user wants Gemini to implement a planned phase. Executes lanes with clean git preflight, owned-file boundaries, verification, and optional explicit worker-subagent fanout for disjoint lanes."
---

# Gemini Execute Phase

Executes a phase plan produced by `gemini-plan-phase`. The default executor is the main Gemini thread. Worker subagents are optional and only used when the user explicitly asks for subagents, delegation, or parallel execution.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Read the full phase plan before editing.
- Preserve user work. Never revert changes you did not make.
- Use the active session's file-editing tool for manual edits.
- When implementation depends on current external documentation, use PMCP first. Context7 is the preferred path for library docs; Bright Data or other search/scrape tools may be used only when `gateway_catalog_search` shows they are available.
- Follow `shared/phase-loop/protocol.md` for shared closeout, manual event, installed-skill drift, and roadmap amendment behavior.
- In injected phase-loop repair launches, do not edit `.phase-loop/` or claim `manual_repair` ledger writes unless the launch explicitly permits and confirms them; emit one valid shared `automation:` closeout so the parent runner can reconcile state.
- Keep lane ownership boundaries. If implementation requires touching another lane's files, stop and revise the plan or ask the user.
- Treat ignored, private, raw-data, credential, and evidence-source files as read-protected unless the phase plan or source bundle explicitly allowlists the exact path or glob for read access. Do not infer permission from nearby owned output paths, old memory, or prior phases.
- Before final closeout, compare `git status --short` dirty paths against the active plan's owned files and control artifacts. If any generated path is unowned, ignored without explicit allowlist/staging policy, or derived from unauthorized raw/private inputs, stop with `dirty_worktree_conflict` instead of calling the phase complete.
- Do not run destructive git commands such as `git reset --hard` or `git checkout -- <path>` unless the user explicitly requested that operation.
- Do not commit, push, or merge unless the user asked for those git actions.

## Runner-Owned Lane Work Units

Injected HARNESSLANE runs may include a `HarnessLaneAssignment` from
`shared/phase-loop/protocol.md`. Treat that assignment as the work-unit
contract: execute only the selected `lane_id`, write only the listed
`owned_files`, treat `consumed_interfaces` as read-only, and emit one shared
`automation:` closeout for that work unit. Installed-skill drift is
warning-only when the repo-injected context is present.

Gemini thinking control must use runner-supported aliases such as
`phase-loop-execute-medium` or `phase-loop-review-high`. Review, reducer,
verify, and closeout prompts are distinct from implementation prompts. If the
selected policy is unsupported by the active harness, stop with a typed
non-human blocker instead of silently downgrading.

## Inputs

- Plan path: default latest `plans/phase-plan-*.md`.
- `--dry-run`: parse and print the lane schedule without editing.
- `--parallel`: allowed only when the user explicitly requests parallel worker execution.

If no plan path is explicit, first check the current repo and branch handoff from `gemini-plan-phase` using `gemini-config/shared/runtime-state.md`: read the repo-local handoff resolver target `.dev-skills/handoffs/gemini-plan-phase/latest.md`, validate `from`, `repo`, `repo_root`, `branch`, `branch_slug`, `commit`, and `artifact`, then use the artifact only if it exists under the current repo root. Ignore missing or mismatched handoffs unless the user explicitly asks to reuse cross-branch state.

## Preflight

1. Resolve repo root and plan path.
2. Run `git status --short`.
3. Run `git status --short -- <plan_path>` and warn if the plan is untracked.
4. If the tree has unrelated dirty files, leave them alone and scope edits around them.
5. Parse:
   - interface gates;
   - lane DAG;
   - owned files;
   - any explicit read allowlists for ignored, private, raw-data, credential, or evidence-source paths;
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
   - read the owned files and related tests;
   - write or update tests first when practical;
   - implement only lane-scoped changes;
   - run lane verification (normalize any phase-plan pytest `-k` selector whose prose terms contain spaces — e.g. `remote connect` becomes `'remote and connect'` — or quote it; a bare spaced `-k` term is an executor-side normalization, not a plan blocker);
   - run any phase-level checks that cover touched files.
3. After each lane:
   - inspect `git diff -- <owned files>`;
   - confirm no peer-owned files were modified;
   - run `git status --short` and confirm every new or modified path is either lane-owned or a planning/control artifact;
   - for any phase-owned generated path under an ignored parent, confirm the plan/source bundle explicitly allows it to be preserved or force-staged;
   - record completed gates.
4. After all lanes:
   - run the full phase verification commands;
   - if execution discoveries change downstream work, amend the nearest
     downstream roadmap phase that is not already executing;
   - treat any older downstream phase plan or handoff as stale after a roadmap
     amendment and route the next step back through `gemini-plan-phase`;
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
- Installed-skill conflicts are warnings when the injected repo bundle was
  delivered successfully; they do not block an otherwise valid shared closeout.


## Runner Verification Evidence

Before reporting a successful closeout, require the runner-owned verification artifact. The closeout must name `verification_artifact_path`, quote the artifact summary line, and must not report `verification_status=passed` unless that artifact exists and supports the executed work. Treat dependency-manifest install refresh and the full suite before closeout as runner-enforced expectations, not optional narrative checks. A blocked gate may be re-verdicted only by rerunning the originally specified runner check; proxy evidence requires a roadmap or plan amendment before the verdict changes. A prior run's terminal-summary is authoritative for reconcile-or-skip only when that closeout was accepted; a rejected or blocked prior closeout — regardless of any self-reported `complete`/`passed` — must not be reconciled against. On a re-run, re-do and re-verify the phase work from scratch rather than skipping on a stale summary.


## Spec Delta Closeout

Before final closeout, choose exactly one `spec_delta_closeout.v1` decision: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Cite metadata-only evidence paths such as the active plan, lane closeouts, targeted pytest output, and `git diff --check` output. Preserve the phase plan's target surfaces and `redaction_posture=metadata_only`; do not include raw specification bodies, raw patch bodies, credentials, provider-supplied payloads, local environment values, or evidence-source contents. Missing or malformed spec-closeout evidence is a repairable automation blocker with `blocker_class=contract_bug` unless the decision is `human_source_judgment_required`.

## Closeout

### Manifest lifecycle

After plan validation and before lane execution, perform a best-effort `plan-manifest append` lifecycle update through `phase_loop_runtime.plan_manifest.update_lifecycle` to mark the matching `type=phase` entry `executing` with run metadata. During closeout, update the same entry to `completed` or `failed` with verification metadata, reflection metadata, produced-gate metadata, `if_gates_produced`, and dirty-worktree summary fields as available. `if_gates_produced` must list only the IF gates the active phase produces per its own plan; never carry a prior phase's gate forward into this phase's closeout. Manifest lifecycle failures are non-fatal during the dual-mode window: emit a ledger warning, mention the warning in the mandatory reflection, and preserve the existing phase closeout JSON, verification, active-session file-editing tool language, and dirty-worktree behavior.

Before final closeout, run `git status --short -- <plan_path> <roadmap_path>` for every consumed or updated planning artifact. If any planning artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <path>` for each artifact. Rerun status and report `Artifact state: staged|tracked|modified|unstaged|blocked` for each artifact. Do not commit unless requested. Repo-local handoff files are operational state: do not `git add` an ignored handoff alongside the plan artifact unless the plan's owned-files/allowlist explicitly includes the handoff directory; leave ignored handoffs ignored and exclude them from artifact-state reporting.

Also run a whole-tree `git status --short` closeout audit. Classify every dirty path as phase-owned, planning/control, pre-existing unrelated, or unowned. For ignored phase-owned outputs that must be preserved, verify an explicit plan/source-bundle allowlist or staging policy before using `git add -f`; otherwise report a repairable `dirty_worktree_conflict`. Precedence for a verified phase: when required verification passed and the ONLY uncommitted paths are phase-owned outputs this run was not authorized to commit, report `terminal_status=awaiting_phase_closeout` and let the runner's graduated closeout gate commit them — do NOT report `dirty_worktree_conflict`; reserve that conflict for unowned, unauthorized-ignored, or overlapping-unrelated dirty paths. Never report `complete` while unowned generated files, unauthorized ignored outputs, or outputs derived from unauthorized raw/private reads remain in the worktree.

Determine the next step before final response and handoff:

- If the current phase is incomplete or verification failed, report `Next phase: <current alias> - blocked: <blocker>` and `Next command: none - <blocker>`.
- If another generated phase plan is ready, report `Next phase: <next alias> - execution ready` and `Next command: gemini-execute-phase <next_plan_path>`.
- If the roadmap has an unplanned ready phase, report `Next phase: <next alias> - planning ready` and `Next command: gemini-plan-phase <roadmap_path> <next_alias>`.
- If the roadmap needs extension, report `Next phase: none - roadmap extension needed` and `Next command: gemini-phase-roadmap-builder <roadmap_path>`.
- If all phases are complete, report `Next phase: none - roadmap complete` and `Next command: none - roadmap complete`.

Add a machine-readable `automation:` handoff with `verification_status` that agrees with the human-readable next step fields. Closeout payload shape is defined by `EmitPhaseCloseout` in `phase_loop_runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/gemini-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.

Report:

- lanes completed;
- files changed;
- planning artifact tracking state;
- next phase and next command;
- verification commands and results;
- commands not run and why;
- follow-up risks or manual checks.

Resolve closeout writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `gemini-config/shared/runtime-state.md` and use Gemini paths only:

- Reflection: `resolve_skill_bundle_root("codex")/gemini-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/gemini-execute-phase/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/gemini-execute-phase/latest.md`

Handoff frontmatter must include `from: gemini-execute-phase`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, `artifact:`, `artifact_state:`, `next_skill:`, `next_command:`, and `next_phase:`. Put open follow-up items in the body, and update `latest.md` with the same handoff content.


## Publication mode

After verified phase work, select exactly one of three modes. **Default to (a) unless a
clear interactive signal is present** — a run missing BOTH the adapter prompt prefix AND
`PHASE_LOOP_RUN_MODE` is AMBIGUOUS and MUST be treated as (a) (fail safe toward the runner).

- (a) Runner-managed closeout (incl. governed mode), OR ambiguous mode. If this is the
  phase-loop adapter (the prompt begins with `<harness>-execute-phase <plan>` from a
  pipeline run dir), or `PHASE_LOOP_RUN_MODE` is set (autonomous/governed), OR neither
  interactive signal is clearly present, the RUNNER owns closeout and commit. Do NOT
  independently publish — defer entirely to runner closeout (`awaiting_phase_closeout` /
  runner commit). Publishing here would bypass the governed pre-merge review panel.
- (b) Interactive orchestrator on a clean, non-protected feature branch (a clear
  interactive signal, and the merge target passed the merge-target safety gate). After the
  Step-9 clean-tree state, push the merge-target branch and open a PR (`gh pr create`,
  `--draft` if dependencies remain or verification was partial/skipped, else ready) instead
  of leaving the lane merge only local.
- (c) Merge target is `main` or a protected branch. Already STOPPED at the merge-target
  safety gate before any lane merge — never merge lanes onto `main`/protected. Re-target a
  feature branch or take explicit instruction.

This applies only to interactive (non-runner) completion; it never overrides
`awaiting_phase_closeout`, the runner's deliberate non-complete terminal. Allowed runner
hygiene (forced lane-worktree removal, `branch -D`, `sweep_stale_worktrees.sh`) is
unchanged — the destructive-op ban targets publication branches/worktrees holding
unmerged work.
