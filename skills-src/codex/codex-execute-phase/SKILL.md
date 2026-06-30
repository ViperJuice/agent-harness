---
name: codex-execute-phase
description: "Codex-optimized executor for a `codex-plan-phase` lane plan. Use when the user wants Codex to implement a planned phase. Executes lanes with clean git preflight, owned-file boundaries, verification, and optional explicit worker-subagent fanout for disjoint lanes."
---

# Codex Execute Phase

Executes a phase plan produced by `codex-plan-phase`. The default executor is the main Codex thread. Worker subagents are optional and only used when the user explicitly asks for subagents, delegation, or parallel execution. Write-capable parallel execution also requires machine-verified disjoint lanes and scheduler-owned worktree assignments; prose lane labels are not sufficient.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Read the full phase plan before editing.
- Preserve user work. Never revert changes you did not make.
- Use `apply_patch` for manual edits.
- When implementation depends on current external documentation, use PMCP first. Context7 is the preferred path for library docs; Bright Data or other search/scrape tools may be used only when `gateway_catalog_search` shows they are available.
- Before asking the user for credentials, account setup, infrastructure state, admin action, or another access blocker, inspect repo-local docs/config and safe read-only or metadata-only state from available CLIs: `op`, `gh`, `vercel`/`npx vercel`, `supabase`/`npx supabase`, `gcloud`, `wrangler`, `cloudflared`, and `gam`. Never record secret values.
- Keep lane ownership boundaries. If implementation requires touching another lane's files, stop and revise the plan or ask the user.
- Treat ignored, private, raw-data, credential, and evidence-source files as read-protected unless the phase plan or source bundle explicitly allowlists the exact path or glob for read access. Do not infer permission from nearby owned output paths, old memory, or prior phases.
- Before final closeout, compare `git status --short` dirty paths against the active plan's owned files and control artifacts. If any generated path is unowned, ignored without explicit allowlist/staging policy, or derived from unauthorized raw/private inputs, stop with `dirty_worktree_conflict` instead of calling the phase complete.
- For Pipeline-aware plans, recompute `source_bundle_sha256` from source bundle bytes before implementation or child work; treat `freshness.source_bundle_hash` as Pipeline-owned metadata unless it is a sha256 digest. For pipeline_required execution, validate protected-source hashes and stop before child launch on missing, stale, malformed, or mismatched source bundles.
- Refuse protected Pipeline writes, including `.pipeline/**`, `pipeline.definition.json`, governed-pipeline specs, Portal contracts, Greenfield authority files, private evidence, raw data, raw evidence, credentials, provider-supplied payloads, or legacy `.codex/phase-loop/` state unless the source-bundle write policy and phase-plan owned files both explicitly authorize the output path. The active plan and source bundle explicitly own the exact path or glob before these inputs or outputs may be read or written.
- Preserve standalone execution behavior for local phase plans without Pipeline metadata; governed-pipeline, Portal, Greenfield, `.pipeline/**`, and source bundles are not mandatory for manual dotfiles use.
- Use `## Execution Policy` only for model, effort, work-unit defaults, lane-specific policy, fallback, policy source, or override reason. `Dispatch Hints` remain executor fallback metadata. Supported selectors are `work-unit defaults`, `roadmap`, `plan`, `execute`, `repair`, `review`, `maintain-skills`, and lane selectors such as `SL-2`; reducer and verification work use lane selectors with `work-unit=phase_reducer` or `work-unit=phase_verify`. Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; policy precedence must not allow silent downgrade without explicit fallback or default inheritance.
- Do not run destructive git commands such as `git reset --hard` or `git checkout -- <path>` unless the user explicitly requested that operation.
- Do not commit, push, or merge unless the user asked for those git actions.
- Claude Code CLI exception wording means local Claude Code CLI execution through the phase-loop launcher, not Anthropic API-key execution or PI provider fallback. Codex and Gemini fallback wording must stay CLI-based and reason-coded.

## Runner-Owned Lane Work Units

Injected HARNESSLANE runs may include a `HarnessLaneAssignment` from
`shared/phase-loop/protocol.md`. Treat that assignment as the work-unit
contract: execute only the selected `lane_id`, write only the listed
`owned_files`, treat `consumed_interfaces` as read-only, and emit one shared
`automation:` closeout for that work unit. Installed-skill drift is
warning-only when the repo-injected context is present.

Review, reducer, verify, and closeout prompts are distinct from implementation
prompts. If the selected policy is unsupported by the active harness, stop with
a typed non-human blocker instead of silently downgrading. Do not spawn peer
harnesses directly; cross-harness execute, repair, or review work must be
externalized as a typed `DelegationRequest`.

## Inputs

- Plan path: default latest `plans/phase-plan-*.md`.
- `--dry-run`: parse and print the lane schedule without editing.
- `--parallel`: allowed only when the user explicitly requests parallel worker execution.

If no plan path is explicit, first check the current repo and branch handoff from `codex-plan-phase` using `codex-config/shared/runtime-state.md`: read the repo-local handoff resolver target `.dev-skills/handoffs/codex-plan-phase/latest.md`, validate `from`, `repo`, `repo_root`, `branch`, `branch_slug`, `commit`, and `artifact`, then use the artifact only if it exists under the current repo root. Ignore missing or mismatched handoffs unless the user explicitly asks to reuse cross-branch state.

## Preflight

1. Resolve repo root and plan path.
2. Run `git status --short`.
3. Run `git status --short -- <plan_path>` and classify the plan as tracked, modified, or untracked. If untracked, warn before editing or executing: `This execution input is untracked and can be deleted by git clean -fd.`
4. If the tree has unrelated dirty files, leave them alone and scope edits around them.
5. Parse:
   - interface gates;
   - lane DAG;
   - owned files;
   - any explicit read allowlists for ignored, private, raw-data, credential, or evidence-source paths;
   - task lists;
   - verification commands.
   - optional dispatch hints from the roadmap or plan `## Dispatch Hints` section, using only:
     `preferred executors`, `allowed executors`, `fallback executors`, `disabled executors`, and `required capabilities`
     with optional action selectors `roadmap`, `plan`, `execute`, `repair`, `review`, and `maintain-skills`.
   Treat lane `**Owned files**` entries as the execution ownership contract:
   repo-relative literals and globs inside backticks are owned paths, `none`
   owns nothing, and the active roadmap plus active plan artifact are always
   control paths. Treat `Interfaces consumed` as read-only contract inputs
   unless the same path is also listed under `Owned files`. If a test or docs
   update pressures execution to rewrite a consumed upstream artifact, first
   adjust the current phase's owned tests/docs to consume the existing
   contract; if that is impossible, stop and route back to `codex-plan-phase`
   for an ownership or downstream-roadmap amendment.
   Owned files authorize writes, not unrestricted reads of ignored/private/raw
   inputs. Read ignored data, private evidence, raw corpora, local-only
   exports, credentials, or user data only when the plan/source bundle names
   the path or glob as an allowed read input; otherwise use metadata-only
   fixtures, committed docs, or stop with a non-human plan/contract blocker.
6. Validate producer dependencies:
   - any lane that consumes another lane's findings, interfaces, or artifacts must list that producer lane in `Depends on`;
   - any lane that writes a synthesized artifact must be downstream of every producer lane it summarizes;
   - if dependencies are missing, stop and require a plan correction before execution.
7. For Pipeline-aware plans, run `pipeline_execution_plan_diagnostic`: parse `phase-source-bundle.v1`, recompute `source_bundle_sha256` from source bundle bytes, re-check protected sources, and compare `freshness.source_bundle_hash` only when it is sha256-shaped. Stale source bundles, changed protected sources, missing Pipeline phase artifacts, and unknown Pipeline phase ids are repairable non-human blockers routed to planning or repair with `human_required=false` and `blocker_class=contract_bug`.
8. For `--dry-run`, report the topological lane order and stop.

## Execution Workflow

1. Execute lanes in topological order.
2. For each lane:
   - resolve executor policy in this order: CLI/operator override, then phase-plan hints, then roadmap hints, then registry defaults;
   - reject disabled or capability-mismatched preferred executors with a typed non-human blocker instead of silently falling through;
   - read the owned files and related tests;
   - write or update tests first when practical;
   - implement only lane-scoped changes;
   - for Pipeline-aware lanes, keep writes inside both the phase-plan owned files and source-bundle-authorized Pipeline outputs;
   - do not edit consumed-interface artifacts unless they are also owned by
     the active lane;
   - run lane verification (normalize any phase-plan pytest `-k` selector whose prose terms contain spaces — e.g. `remote connect` becomes `'remote and connect'` — or quote it; a bare spaced `-k` term is an executor-side normalization, not a plan blocker);
   - run any phase-level checks that cover touched files.
3. After each lane:
   - inspect `git diff -- <owned files>`;
   - confirm no peer-owned files were modified;
   - run `git status --short` and confirm every new or modified path is either lane-owned or a planning/control artifact;
   - for any phase-owned generated path under an ignored parent, confirm the plan/source bundle explicitly allows it to be preserved or force-staged;
   - run `pipeline_write_boundary_diagnostic` when the plan has Pipeline metadata, and fail closed on unauthorized `.pipeline/**`, protected Pipeline, `pipeline.definition.json`, or portal contract writes;
   - record completed gates.
4. After all lanes:
   - run the full phase verification commands;
  - if execution discoveries change downstream work, amend the phase roadmap at the nearest downstream phase in the amended roadmap that is not already executing;
  - treat any older downstream phase plan or handoff as stale after a roadmap amendment and route the next step back through `codex-plan-phase` for the newly selected downstream phase;
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
- Missing dependency, malformed lane dependency, or unclear interface: stop and route back to `codex-plan-phase` with `human_required=false` unless a true product or human decision is missing.
- Verification command unavailable: report the missing tool and run the closest available static check only if it is meaningful.
- True human-required blockers must use the frozen blocker taxonomy from `codex-config/shared/runtime-state.md`: `missing_secret`, `account_or_billing_setup`, `admin_approval`, `destructive_operation`, `ambiguous_roadmap_selection`, `product_decision_missing`, `dirty_worktree_conflict`, `branch_sync_conflict`, `stalled_child_observation`, `repeated_verification_failure`, or `unretryable_external_outage`.
- If the same verification remains failing after one local diagnosis and repair attempt, set `blocker_class=repeated_verification_failure`. If unrelated user work overlaps required lane files, set `blocker_class=dirty_worktree_conflict`.
- If a release plan with `phase_loop_mutation: release_dispatch` discovers that release-affecting files must be edited before dispatch, do not call the external release command. Stop with `automation.status=blocked`, `human_required=true`, `blocker_class=dirty_worktree_conflict`, and a summary telling the operator to complete/commit the prepare work first.
- If a phase is ready to report `complete`, verify that `git status --short` is clean for the target repo. If the repo is dirty only because of phase-owned output and required verification passed, do not report it as a human blocker; report `blocked`, `human_required=false`, `blocker_class=dirty_worktree_conflict`, and state that a loop repair turn can commit, isolate, or convert the state to a true human blocker. Use `human_required=true` only when pre-existing unrelated dirty paths overlap the required work, the repo is on the wrong branch/ref, or a true human/product/access decision is needed.


## Runner Verification Evidence

Before reporting a successful closeout, require the runner-owned verification artifact. The closeout must name `verification_artifact_path`, quote the artifact summary line, and must not report `verification_status=passed` unless that artifact exists and supports the executed work. Treat dependency-manifest install refresh and the full suite before closeout as runner-enforced expectations, not optional narrative checks. A blocked gate may be re-verdicted only by rerunning the originally specified runner check; proxy evidence requires a roadmap or plan amendment before the verdict changes. A prior run's terminal-summary is authoritative for reconcile-or-skip only when that closeout was accepted; a rejected or blocked prior closeout — regardless of any self-reported `complete`/`passed` — must not be reconciled against. On a re-run, re-do and re-verify the phase work from scratch rather than skipping on a stale summary.


## Spec Delta Closeout

When reporting `verification_status=passed` for a generic phase, attach the runner verification artifact (the closeout's `artifact_paths.verification`, or a top-level `verification_artifact_path`). If the phase legitimately has no executable verification (docs-only, config-only), record a `verification_evidence_opt_out` reason instead — one of `no_executable_verification`, `verification_deferred_to_later_phase`, or `operator_attested_manual`. This is the input the autonomy-first verification-evidence review gate checks (warn by default); recording the artifact or a typed opt-out keeps the closeout clean with no human.

Before final closeout, when the diff touches a user-visible public surface (CLI flags, exported symbols, config/openapi schema, contract docs, `README.md`, or `CHANGELOG.md`), record a `doc_delta_decision`: `docs_updated` (you updated the matching doc surface in this phase), `docs_follow_up_filed` (tracked for a later phase), or `no_doc_delta` (the change needs no doc update — state why). This is the input the autonomy-first doc-delta review gate checks; the gate defaults to `PHASE_LOOP_REVIEW=warn` (records the finding and the loop continues), so recording the decision keeps the closeout clean and is satisfiable by the agent with no human. Prefer updating the doc surface within the phase's docs lane over deferring.

Before final closeout, when the diff changes a UI/visual surface (`*.tsx`, `*.jsx`, `*.vue`, `*.svelte`, `*.css`/`*.scss`, or `components/**`), capture a screenshot of the rendered result (claude-in-chrome or Playwright-via-PMCP) and record its path as `visual_evidence_path` (with a brief `visual_evidence_observed` note). This is the input the autonomy-first visual-evidence review gate checks; it defaults to warn (recorded, non-blocking) and is satisfiable by the agent capturing the screenshot — no human eyeball needed to pass. The screenshot is the artifact a human spot-checks after a bounded run.

Before final closeout, choose exactly one `spec_delta_closeout.v1` decision: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Cite metadata-only evidence paths such as the active plan, lane closeouts, targeted pytest output, and `git diff --check` output. Preserve the phase plan's target surfaces and `redaction_posture=metadata_only`; do not include raw specification bodies, raw patch bodies, credentials, provider-supplied payloads, local environment values, or evidence-source contents. Missing or malformed spec-closeout evidence is a repairable automation blocker with `blocker_class=contract_bug` unless the decision is `human_source_judgment_required`.

## Closeout

### Manifest lifecycle

After plan validation and before lane execution, perform a best-effort `plan-manifest append` lifecycle update through `phase_loop_runtime.plan_manifest.update_lifecycle` to mark the matching `type=phase` entry `executing` with run metadata. During closeout, update the same entry to `completed` or `failed` with verification metadata, reflection metadata, produced-gate metadata, `if_gates_produced`, and dirty-worktree summary fields as available. `if_gates_produced` must list only the IF gates the active phase produces per its own plan; never carry a prior phase's gate forward into this phase's closeout. Manifest lifecycle failures are non-fatal during the dual-mode window: emit a ledger warning, mention the warning in the mandatory reflection, and preserve the existing phase closeout JSON, verification, and dirty-worktree behavior.

Before final closeout, run `git status --short -- <plan_path> <roadmap_path>` for every consumed or updated planning artifact. If any planning artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <path>` for each artifact. Rerun status and report `Artifact state: staged|tracked|modified|unstaged|blocked` for each artifact. Do not commit unless requested. Repo-local handoff files are operational state: do not `git add` an ignored handoff alongside the plan artifact unless the plan's owned-files/allowlist explicitly includes the handoff directory; leave ignored handoffs ignored and exclude them from artifact-state reporting.

Also run a whole-tree `git status --short` closeout audit. Classify every dirty path as phase-owned, planning/control, pre-existing unrelated, or unowned. For ignored phase-owned outputs that must be preserved, verify an explicit plan/source-bundle allowlist or staging policy before using `git add -f`; otherwise report a repairable `dirty_worktree_conflict`. Precedence for a verified phase: when required verification passed and the ONLY uncommitted paths are phase-owned outputs this run was not authorized to commit, report `terminal_status=awaiting_phase_closeout` and let the runner's graduated closeout gate commit them — do NOT report `dirty_worktree_conflict`; reserve that conflict for unowned, unauthorized-ignored, or overlapping-unrelated dirty paths. Never report `complete` while unowned generated files, unauthorized ignored outputs, or outputs derived from unauthorized raw/private reads remain in the worktree.

Determine the next step before final response and handoff:

- If the current phase is incomplete or verification failed, report `Next phase: <current alias> - blocked: <blocker>` and `Next command: none - <blocker>`.
- If another generated phase plan is ready, report `Next phase: <next alias> - execution ready` and `Next command: codex-execute-phase <next_plan_path>`.
- If the roadmap has an unplanned ready phase, report `Next phase: <next alias> - planning ready` and `Next command: codex-plan-phase <roadmap_path> <next_alias>`.
- If the roadmap needs extension, report `Next phase: none - roadmap extension needed` and `Next command: codex-phase-roadmap-builder <roadmap_path>`.
- If all phases are complete, report `Next phase: none - roadmap complete` and `Next command: none - roadmap complete`.

Add a machine-readable automation handoff that agrees with the human-readable next step fields. Closeout payload shape is defined by `EmitPhaseCloseout` in `phase_loop_runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/codex-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.

Set `automation.status=complete`, `verification_status=passed`, and `human_required=false` only when acceptance criteria and required verification are satisfied. Set `automation.status=executed` when implementation ran but acceptance reduction, artifact tracking, or a required verification remains unresolved. Use the consumed phase plan as the handoff `artifact`; the autonomous phase loop trusts phase status handoffs only when that plan's phase-loop frontmatter still matches the selected roadmap. For repairable plan gaps such as malformed dependencies or missing lane interfaces, set `automation.status=blocked`, `next_skill=codex-plan-phase`, `next_command=codex-plan-phase <roadmap_path> <current_alias>`, `next_model_hint=plan`, `next_effort_hint=high`, and `human_required=false`. For true human blockers, set `human_required=true`, a frozen `blocker_class`, a redacted `blocker_summary`, non-secret `required_human_inputs`, and redacted `access_attempts` when access was involved. Never include secret values.

Manual TUI runs remain valid without the outer phase loop. When `.phase-loop/` exists, treat it as the authoritative runner state; legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede canonical `.phase-loop/` state. When canonical `.phase-loop/state.json` or `tui-handoff.md` lags behind the newer `.phase-loop/events.jsonl` plus live git topology, reconcile current phase state from the ledger and `git status --short`/HEAD and cite that reconciliation; stale handoff text is not blocking. Only append a legacy `manual` source event to `.codex/phase-loop/events.jsonl` for standalone manual compatibility when no canonical `.phase-loop/` runtime exists, using the same `automation.status`, `next_skill`, `next_command`, `next_model_hint`, `next_effort_hint`, `human_required`, `blocker_class`, `blocker_summary`, `required_human_inputs`, `verification_status`, `artifact`, and `artifact_state` values. This manual event import must preserve the same machine-readable closeout contract. If execution amended the roadmap downstream, compute `roadmap_sha256` and `phase_sha256` from the amended roadmap for the completed current phase before appending the event; do not reuse stale plan frontmatter, next-phase hashes, or pre-amendment roadmap hashes.

Report:

- lanes completed;
- files changed;
- planning artifact tracking state;
- next phase and next command;
- verification commands and results;
- commands not run and why;
- follow-up risks or manual checks.

Resolve closeout writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `codex-config/shared/runtime-state.md` and use Codex paths only:

- Reflection: `resolve_skill_bundle_root("codex")/codex-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/codex-execute-phase/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/codex-execute-phase/latest.md`

Handoff frontmatter must include `from: codex-execute-phase`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, `artifact:`, `artifact_state:`, `next_skill:`, `next_command:`, and `next_phase:`. Put open follow-up items in the body, and update `latest.md` with the same handoff content.

If execution is blocked by credentials, account setup, infrastructure state, admin action, or other access prerequisites, write a handoff with `human_required=true` and redacted `access_attempts` entries before asking the user to act. Each `access_attempts` entry must include `source`, `probe`, `result`, `details`, and `timestamp`, and `details` may report only metadata such as command availability, account or project identity, vault/item/field names, environment variable names, presence, and validation status.


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
