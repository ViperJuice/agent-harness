# Phase-Loop Protocol

This document is the canonical shared output contract for harness-neutral
phase-loop artifacts. It freezes the artifact shapes, failure vocabulary, and
promotion semantics that every executor, adapter, and manual/operator flow must
produce or consume during the current phase-loop roadmap family.
Harness-specific launch mechanics, runtime paths, and private skill install
details stay in harness-local runtime docs.
Harness-specific path ownership for the dotfiles-hosted substrate is recorded
in `docs/phase-loop/harness-substrate-manifest.md`; this protocol remains the
schema and artifact contract.
Harness workflow skill naming is recorded in
`docs/phase-loop/harness-skill-matrix.md`; that matrix freezes the
`<harness>-<workflow>` contract, Pi Agent role-style exceptions, direct route
compatibility, and the governed-pipeline `.pipeline/skills/**` namespace guard.

## Plan Frontmatter

Execution-ready phase plans remain compatible with the existing
`phase_loop_plan_version: 1` contract:

```yaml
---
phase_loop_plan_version: 1
phase: <PHASE_ALIAS>
roadmap: <repo-relative roadmap path>
roadmap_sha256: <sha256 of roadmap file bytes at planning time>
phase_loop_mutation: <optional release_dispatch>
release_base_ref: <optional base ref for release dispatch, default origin/main>
source_bundle: <optional repo-relative Pipeline source bundle path>
source_bundle_sha256: <optional sha256 of Pipeline source bundle bytes>
pipeline_phase_id: <optional Pipeline phase id>
pipeline_mode: <optional standalone|pipeline_optional|pipeline_required>
---
```

Required fields:

- `phase_loop_plan_version`
- `phase`
- `roadmap`
- `roadmap_sha256`

Optional fields:

- `phase_loop_mutation`
- `release_base_ref`
- `source_bundle`
- `source_bundle_sha256`
- `pipeline_phase_id`
- `pipeline_mode`

`PIPELINE_PLAN_FRONTMATTER_CONTRACT` is additive. Pipeline-aware metadata is additive
metadata, not a replacement for `phase`, `roadmap`, or `roadmap_sha256`.
Existing standalone plans with only `phase_loop_plan_version: 1`,
`phase`, `roadmap`, and `roadmap_sha256` remain valid when no Pipeline mode
requires bundle metadata.

The supported `pipeline_mode` literals are:

- `standalone`
- `pipeline_optional`
- `pipeline_required`

Pipeline metadata is required only when `pipeline_mode` is `pipeline_required`.
A pipeline-required plan missing `source_bundle` or `source_bundle_sha256`, or
whose `source_bundle_sha256` no longer matches the source bundle bytes, is a
typed repairable non-human diagnostic rather than partial execution readiness.
Unknown `pipeline_mode` values fail closed as typed metadata diagnostics.

Plans without this metadata, or with metadata that no longer matches the
selected roadmap, are stale for autonomous execution and must route back
through planning instead of execution.

`PIPELINE_PROTECTED_SOURCE_CATEGORIES` freezes the protected source vocabulary
for Pipeline bridge work:

- `specs` - Pipeline specs.
- `diagrams` - Pipeline diagrams.
- `adapter_config` - Pipeline adapter config.
- `definition_files` - Pipeline definition files, including
  `pipeline.definition.json`.
- `portal_contracts` - Portal contracts.
- `phase_artifacts` - Pipeline phase artifacts.

Protected-source categories remain high-level. Canonical spec adoption semantics
are expressed through optional protected-source role metadata, not by adding
new categories or making dotfiles the adoption authority.
`PIPELINE_PROTECTED_SOURCE_ROLES` freezes the adoption-sensitive role
vocabulary that source bundles may attach to protected-source entries:

- `seed_spec`
- `predecessor_spec`
- `active_canonical_spec`
- `archived_spec`
- `managed_mirror_file`
- `unmanaged_spec_input`
- `legacy_specs_bundle`
- `root_specs_intake`
- `pipeline_specs_canonical`
- `adapter_configured_intake_root`
- `mirror_manifest`
- `archive_manifest`

The upstream canonical adoption config keys are metadata-only bridge terms:
`canonical_sources.adoption_mode`,
`canonical_sources.active_canonical_root`, `canonical_sources.mirror_root`,
`canonical_sources.archive_manifest_path`,
`canonical_sources.spec_intake_roots`, and
`canonical_sources.legacy_seed_roots`. The adoption mode literals are
`track_existing`, `greenfield_single_spec`, `greenfield_spec_bundle`, and
`brownfield_existing_specs`.

Standalone `phase_loop_plan_version: 1` plans remain valid without Pipeline or
canonical adoption metadata. A pipeline-required source bundle that declares
role metadata as required must fail closed with a repairable non-human
`contract_bug` diagnostic when an adoption-sensitive role is missing, stale,
malformed, unknown, or cannot be represented in closeout metadata.

Phase-loop planners and executors may read protected sources when the selected
source bundle authorizes that context, but they must not mutate Pipeline-owned
protected sources by inference. Any Pipeline-owned write must be explicit in the
bundle policy and in the phase-plan owned-file contract.

## Handoff Storage

Workflow skill handoffs are stored in repo-local generated state under
`.dev-skills/handoffs/<skill-name>/`. The canonical latest pointer for a skill
is resolved by `shared/phase-loop/handoff_path.py` through
`resolve_handoff_path(repo, skill_name)` and points to
`.dev-skills/handoffs/<skill-name>/latest.md`. The resolver returns a `Path`
only; callers create directories and write content.

`phase-loop migrate-handoffs [--dry-run | --apply] [--json]` is the hard-cut
migration command. It scans legacy Claude, Codex, Gemini, and OpenCode skill
handoff roots, selects only handoffs whose frontmatter matches the current
repo, and emits metadata-only records with `skill_name`, `source`, `target`,
`action`, and `status`. Dry-run mode reports planned moves without mutation.
Apply mode moves matching `latest.md` files and timestamped siblings into the
repo-local directory and is idempotent when the target already has matching
content.

Apply mode is guarded by `_quiesced(repo)`. The guard succeeds only when
`.phase-loop/state.json` has no in-flight phase states, `.phase-loop/events.jsonl`
has no recent action except `closeout` or `manual_repair`, and no
`.phase-loop/*.lock` file exists. A failed quiescence check reports the first
blocker condition and performs no moves.

Legacy harness handoff roots are read only for migration after this cutover.
Reflections remain in harness-specific `reflections/` trees and are not moved
by HANDOFFS. The root `.gitignore` excludes `/.dev-skills/` so migrated or newly
written handoffs do not become source artifacts.

## Skills Bundle

The harness-neutral workflow skill source bundle lives at
`vendor/phase-loop-skills/`. Base source directories are unprefixed:
`execute-phase`, `plan-phase`, `plan-detailed`, `phase-roadmap-builder`,
`phase-loop`, `skill-editor`, `skill-improvement-planner`, and
`task-contextualizer`. Installed names retain the harness prefix, for example
`codex-execute-phase` and `claude-plan-phase`.

Harness-specific differences are escape hatches, not a second source tree.
Per-harness overlays live under
`vendor/phase-loop-skills/<skill-name>/_overrides/<harness>/` and are layered
over the unprefixed source during installation. Operators author those overlay
files explicitly; the installer does not auto-generate operator-authored
exceptions.

`phase_loop_runtime.skill_paths` exposes the resolver API used by workflow
skills and installers:

- `current_harness(harness: str | None = None) -> str`
- `resolve_skill_bundle_root(harness: str | None = None) -> Path`
- `resolve_skill_helper_root(harness: str | None = None) -> Path`
- `resolve_handoff_root(repo: Path) -> Path`
- `resolve_reflection_root(skill_name: str, harness: str | None = None) -> Path`

Resolver precedence is explicit function argument, `PHASE_LOOP_HARNESS`,
`PHASE_LOOP_SKILL_BUNDLE`, then the documented harness defaults:
`~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`, and
`~/.config/opencode/skills`. Handoffs resolve to repo-local
`.dev-skills/handoffs/`; reflections remain under the selected harness skill
root.

`phase-loop install --harness {claude,codex,gemini,opencode}
[--source <bundle-path>] [--destination <install-root>] [--symlink|--copy]
[--dry-run|--apply]` is the metadata-only install surface. Dry-run mode reports
planned source, destination, harness, skill name, install mode, and action
without mutating destinations. Apply mode is idempotent and installs
harness-prefixed workflow skills from `vendor/phase-loop-skills/`.

`phase-loop install --status --json` is a read-only runtime visibility surface.
It never installs, replaces, copies, symlinks, deletes, or rewrites skill files.
The JSON reports per-harness skill parity, console-script availability, BAML
closeout schema availability, and `.dev-skills/` ignore readiness using
symbolic metadata only.

## Operating Modes

The phase-loop runner supports three operating modes:

- `standalone`: local dotfiles execution. A `phase_loop_plan_version: 1` plan
  with only the required `phase`, `roadmap`, and `roadmap_sha256` fields
  remains valid without Pipeline metadata. Standalone runs do not require
  governed-pipeline, `.pipeline/**`, Portal, Greenfield, or a source bundle.
- `pipeline_optional`: dotfiles may consume supplied Pipeline metadata and
  source-bundle context when present, but missing Pipeline metadata does not
  invalidate the standalone plan contract.
- `pipeline_required`: execution must fail closed before child launch when
  validated Pipeline context is missing, stale, malformed, or mismatched.

Pipeline-required execution requires a validated `source_bundle`,
`source_bundle_sha256`, `pipeline_phase_id`, bundle freshness, protected-source
entries, and protected-source hash checks before execution. When
`freshness.source_bundle_hash` is a SHA-256 digest, it must match the source
bundle bytes. Protected-source entries must cite one of
`PIPELINE_PROTECTED_SOURCE_CATEGORIES`, and protected-source files must exist
with matching SHA-256 hashes.

Governed-pipeline owns canonical source-truth refresh, source-bundle emission,
protected-source freshness, scheduling, closeout ingest, Greenfield reduction,
and Portal projection. Dotfiles consumes those inputs and emits redacted
metadata; it does not infer authority over governed-pipeline, Portal,
Greenfield, `.pipeline/**`, private evidence, raw data, credentials, or
provider payloads.
Governed-pipeline also owns canonical spec adoption, archive creation, managed
mirror refresh, source-truth reconciliation, canonical refresh, replan, and
preflight block decisions. Dotfiles may echo validated adoption role metadata
and advisory hints in closeout, but it must not make archive, mirror,
canonical refresh, replan, or block decisions.

## Dotfiles Source And Visibility Contracts

Dotfiles source authority for governed-pipeline adoption is frozen in
`docs/dotfiles-source-authority-contract.md`. That document classifies every
top-level path with `path_glob`, `classification`, `owner`,
`ingestion_policy`, and rationale. Governed-pipeline may pull paths classified
as `authority`; it must reject `derived`, `runtime_state`, `private`, and
`out_of_scope` paths as adoption source material.

Dotfiles cross-repo visibility is frozen in
`docs/dotfiles-visibility-contract.md`. That contract exposes only adoption
inputs, redacted runtime metadata, and the operating-mode declaration. The
surface is pull-only: dotfiles does not push into governed-pipeline or Portal,
governed-pipeline must not write into dotfiles, consumers must not depend on
runtime state paths, and BAML schema contracts must be imported rather than
paraphrased.

## Dotfiles Schema Pack

The dotfiles BAML schema pack lives under
`vendor/phase-loop-runtime/baml_src/` and freezes the direct import contracts
for downstream consumers. The authority class names are
`DotfilesAdoptionManifest`, `DotfilesRuntimeProjection`,
`DotfilesC4Document`, and `DotfilesTaskCatalog`.

Consumers import the `.baml` contracts directly or use
`phase_loop_runtime.baml_modular.export_function_schema(<class name>)` for a
dialect-clean JSON Schema projection. Protocol prose may describe the pack and
its consumers, but it must not paraphrase full field definitions as an
alternate schema source. `DotfilesAdoptionManifest` is the governed-pipeline
pull manifest shape, `DotfilesRuntimeProjection` is the redacted runtime
visibility shape, `DotfilesC4Document` carries Markdown/Mermaid source for
deterministic rendering, and `DotfilesTaskCatalog` carries audience-tagged task
entries.

In standalone mode, root `specs/**` is the default human-visible future-spec
discovery root when no phase plan, source bundle, or repo-local config
overrides it. Legacy or project-specific seed roots such as `Specs/**` are
explicit input roots only; accepted future specs should normalize toward root
`specs/**` for later governed-pipeline intake.

## Dispatch Hints

Roadmaps and phase plans may include an optional markdown section named
`Dispatch Hints` without changing the required frontmatter contract. The parser
must treat this section as optional and fail closed when it is absent.

Supported keys:

- `preferred executors`
- `allowed executors`
- `fallback executors`
- `disabled executors`
- `required capabilities`

Supported action selectors:

- `roadmap`
- `plan`
- `execute`
- `repair`
- `review`
- `maintain-skills`

Accepted forms:

```markdown
## Dispatch Hints
- preferred executors: `codex`
- allowed executors: `codex`, `claude`
- fallback executors: `codex`
- disabled executors: `manual`
- required capabilities: `live_launch`, `structured_output`
- execute preferred executors: `codex`
- review allowed executors: `codex`, `claude`
```

or action-grouped subsections such as:

```markdown
## Dispatch Hints
### Default
- preferred executors: `codex`

### Review
- allowed executors: `codex`, `claude`
```

Dispatch hint precedence is:

1. operator / CLI override
2. phase-plan hints
3. roadmap hints
4. registry defaults

Disabled executors and required capabilities remain conservative filters: they
must not be silently ignored when a preferred executor conflicts with them.

## Execution Policy

Roadmaps and phase plans may include an optional `## Execution Policy` section
when executor selection also needs model, effort, work-unit defaults, fallback,
or policy source provenance. Existing artifacts do not need this section.
`Dispatch Hints` remain valid and are the executor-only fallback surface when no
execution policy is present.

The accepted syntax is line-oriented markdown:

```markdown
## Execution Policy
- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`pi`, model=`auto`, effort=`medium`, work-unit=`lane_execute`, reason=`simple bounded lane default`
- repair: executor=`claude`, model=`claude-opus-4-7`, effort=`high`, work-unit=`repair`
- SL-2: executor=`gemini`, model=`phase-loop-execute-medium`, effort=`medium`, work-unit=`lane_execute`, unsupported=`fallback`, fallback=`phase-loop-execute-medium`
```

Supported selectors are `work-unit defaults`, `roadmap`, `plan`, `execute`,
`repair`, `review`, `maintain-skills`, and lane-specific selectors such as
`SL-2`. Lane-specific policy only resolves metadata for that lane; POLICYDSL
does not schedule lane work units.
Reducer and verification lanes use lane selectors with
`work-unit=phase_reducer` or `work-unit=phase_verify`; invented action
selectors such as `reduce` and `verify` are invalid.

Execution policy precedence is frozen as:

1. CLI/operator override
2. phase-plan policy
3. roadmap policy
4. `Dispatch Hints`
5. registry defaults

The resolver must record policy source and override reason for model, effort,
executor, and fallback decisions. Invalid model, effort, work-unit, fallback, or
silent downgrade cases fail closed unless explicit fallback or default
inheritance is recorded.

A plan or roadmap whose `## Execution Policy` block fails to parse is recorded
as a `contract_bug` blocker carrying `path:line_number` and the offending raw
line, surfaced through `phase-loop status` and `phase-loop handoff`. The runner
stays alive and exits with code `5` (blocked), distinct from `1` (child launch
failure) and `0` (success), so operator rotation wrappers can branch on the
exit code instead of grepping stdout for `Terminal status: blocked`.

`phase-loop status --json` exposes `pipeline_mode` at the top level and the
resolved per-phase execution policy under an `execution_policy` block keyed by
phase alias, with sub-keys for `plan`, `execute`, `repair`, and `review`. Each
sub-key reports the resolved `executor`, `model`, `effort`, and `source`
(`phase-plan policy` or `roadmap policy`). Rotation wrappers consult this block
to pre-resolve executor pins before dispatching, avoiding the per-phase
`sandbox_command_restriction` failures that arise when an external rotation
pushes an executor that the plan policy pins to a different harness.

`phase-loop status --runtime-projection --json` emits the
`DotfilesRuntimeProjection` shape. The projection maps the selected
`pipeline_mode` to `operating_mode`, validates the emitted JSON through the BAML
schema parser, and must not include absolute host paths, secret references,
environment values, provider tokens, or local account identifiers. The surface
is standalone-first: it works without governed-pipeline, a source bundle,
Portal, Greenfield, or any acknowledged Pipeline contract.

## Reconcile Command

`phase-loop reconcile --phase <ALIAS> [--closeout-commit <SHA>]
[--repair-summary <text>] [--verification-status <not_run|passed|failed>]`
synthesizes a v28-shape `manual_repair` event for the named phase, recording
the current `HEAD` (or the supplied SHA) as the closeout commit and marking
`clears_blocker=true`. It then re-reconciles so `phase-loop status` reflects
the cleared blocker.

The command refuses if the working tree is dirty (override with
`--allow-dirty`) so the synthesized event always references a clean closeout
commit. This compresses the post-`dirty_worktree_conflict` recovery ritual —
manually authoring the v28 P3/P4 event shape and appending to events.jsonl —
to one CLI call. Use only as a recovery tool when the executor's work is
correct but ownership classification or lane-evidence gaps left the runner
blocked; do not use to bypass legitimate verification failures.

## Reopen Command

`phase-loop reopen --phase <ALIAS> --reason <text> [--allow-dirty]` is the
symmetric counterpart to `reconcile`: where `reconcile` advances a `blocked`
phase to `complete`, `reopen` reverts a spurious `complete` phase back to
`planned`. Use when an executor reported a phase as complete +
`verification_status=passed` but the underlying IF gates were not actually
satisfied (e.g., a repair iteration that reported done with zero diff and no
real work).

The command appends a typed `phase_reopen` event with `status: planned` and
metadata `{reason, prior_status, prior_closeout_commit, reopen_commit}`. The
reducer recognizes `action: phase_reopen` and flips the phase back to
`planned` regardless of plan-artifact existence. Subsequent
`phase-loop run` invocations re-execute the phase.

Refuses if the phase is not currently `complete` (cannot reopen what isn't
closed). Refuses if the working tree is dirty (override with `--allow-dirty`)
so `reopen_commit` references a clean state. The `--reason` field is
required and recorded on the event for audit.

This command exists because the runner trusts the executor's reported
terminal status by default; if an executor hallucinates completion, the
runner has no way to independently verify the IF gates were satisfied. A
future "closeout-payload IF-gate cross-check" would prevent the bug at
emission time; `reopen` is the recovery path until that ships.

### Verified Dirty Closeout Auto-Recovery

When the runner has already performed a verified dirty closeout recovery, a
later `phase-loop status` reconciliation may supersede the stale non-human
blocker without requiring an operator-authored `manual_repair` event. The
current phase's latest trusted event must carry
`metadata.completion_dirty_worktree.reason:
verified_dirty_closeout_recovery`, a `metadata.closeout.closeout_commit`,
`metadata.terminal_summary.verification_status: passed`, and no
`metadata.completion_dirty_worktree.unowned_dirty_paths`.

The repository worktree must be clean before this reducer fires. A successful
reduction marks the phase complete, clears blocker and dirty-path fields,
preserves closeout summary metadata, and records the ledger warning reason
`clean_verified_dirty_closeout_recovery_superseded_nonhuman_blocker`. Human
blockers and unrelated blocker classes remain authoritative; this reducer only
repairs stale non-human dirty-closeout blockers.

### Executor Degradation Cache

Session-scoped executor degradation is stored at the canonical
`.phase-loop/executor-degradation.json` path. There is no legacy
`.codex/phase-loop/**` fallback for this sidecar. The JSON object is keyed by
executor name, and each record has these fields: `since`, `ttl_seconds`,
`demoted_to`, `reason`, `source_phase`, and `blocker_summary`. `demoted_to`
is limited to `proof_gated` or `manual_only`.

`state_degradation.load_degradation(repo)` returns valid records and tolerates
missing or corrupted files by returning `{}`. `record_degradation(repo,
executor, reason, source_phase, blocker_summary, ttl_seconds,
demoted_to="proof_gated")` validates `demoted_to` and writes with a temporary
file plus `os.replace`. `active_degraded_executors(repo, *, now=None)` returns
the TTL-filtered active executor set, and `clear(repo)` removes the sidecar
idempotently. `phase-loop archive-state` does not move this cache, so session
demotion can survive runtime ledger archival.

FOUND publishes this cache contract only. DISPATCH wires launcher emissions,
dispatch filtering, and any future `--reset-capability` control.

### Blocker Classification Heuristics

Metadata-only launcher preflight must report only redacted probe metadata:
command availability, return code, byte counts, and boolean surface presence.
It must not persist stdout or stderr excerpts. Missing login, token, or
subscription signals classify as `account_or_billing_setup` with
`suggested_ttl_seconds: 300` and `demoted_to: proof_gated`.

Capacity-like provider signals classify as `unretryable_external_outage` with
`suggested_ttl_seconds: 1800` and `demoted_to: manual_only`. The frozen
capacity patterns are `capacity`, `exhausted`, `rate.limit`, `503`, and
`temporarily.unavailable`; `claude auth status` quota-like JSON is reduced
through the same capacity path without storing credential or provider payload
values.

### Session Capability Degradation During Dispatch

`resolve_dispatch_decision(..., repo=...)` consults
`active_degraded_executors(repo)` after live availability has been confirmed
for a candidate and before selecting it. A session-degraded executor is skipped
silently so a live fallback can run. If every otherwise viable live candidate
is session-degraded, dispatch returns `blocked_reason:
all_candidates_session_degraded` with a summary naming the action and no
credential or provider payload values.

`phase-loop run --reset-capability`, `phase-loop resume --reset-capability`,
and `phase-loop dry-run --reset-capability` clear only
`.phase-loop/executor-degradation.json` before dispatch setup. They do not
archive, rewrite, or reconcile `.phase-loop/state.json`,
`.phase-loop/events.jsonl`, or legacy `.codex/phase-loop/**`.

### Rotation

`phase-loop run`, `phase-loop resume`, and `phase-loop dry-run` accept
`--rotate-executors <csv>`, `--rotation-mode <phase|work_unit>`, and
`--rotation-on-policy-pin <skip|fallback-next>`. The rotation list uses executor
names from the frozen executor vocabulary, trims whitespace, deduplicates in
order, and fails closed with a non-human `contract_bug` blocker when the list is
empty or contains an invalid executor.

Rotation injects the current cursor executor as an operator-layer preferred executor
before `resolve_dispatch_decision(..., repo=...)`. It rotates only
executors; model and effort still come from the profile and Execution Policy
chain. Plan and roadmap Execution Policy pins remain higher precedence than
rotation. A policy pin launches with its pinned executor regardless of cursor
position.

`--rotation-mode phase` consumes rotation at phase launch boundaries.
`--rotation-mode work_unit` consumes rotation at work-unit launch starts. A
running phase or work unit keeps the executor selected at launch; rotation never
switches an in-flight unit. `--rotation-on-policy-pin=skip` is the default and
counts a policy pin as a consumed rotation turn. `fallback-next` preserves the
cursor until the next non-pinned phase or work unit consumes it.

The accepted mode literals are `phase` and `work_unit`. The accepted
policy-pin literals are `skip` and `fallback-next`.

Executor degradation remains the DISPATCH authority: candidates listed in
`.phase-loop/executor-degradation.json` are excluded by the existing
`active_degraded_executors(repo)` filter during final dispatch. Rotation does
not read provider payloads or reimplement degradation. New launch events with a
resolved dispatch decision stamp top-level `selected_executor`; old events
without that field reduce identically.

Default harness policy is explicit. Simple bounded scheduler-assigned lane
execution defaults to `executor=pi`; Claude or Anthropic model lanes default to
Claude Code CLI unless a policy explicitly selects a Pi-wrapped Claude route
and records the override reason. Codex and Gemini fallback routes are
CLI-based, reason-coded, and must not silently switch to API-key command
adapters.

## Skill Namespace Contract

Harness-local workflow skills follow the `<harness>-<workflow>` pattern frozen
in `docs/phase-loop/harness-skill-matrix.md`. The active harness families are
Codex, Claude Code, Gemini CLI, and OpenCode. Direct Codex, direct Gemini, and
direct OpenCode launcher routes remain compatibility-supported during this
roadmap, while Claude Code continues to use the `claude -p` path.

Pi Agent role-style skills are explicit exceptions: `phase-loop-supervisor`,
`phase-loop-repair`, and `phase-loop-closeout`. They are adapter roles, not a
fifth unnormalized harness workflow family.

Governed-pipeline `.pipeline/skills/**` is a downstream product/runtime
namespace outside dotfiles skill normalization. Dotfiles artifacts may mention
canonical workflow skill names as bridge vocabulary, but dotfiles must not
rewrite, rename, install, or validate governed-pipeline `.pipeline/skills/**`
as `<harness>-<workflow>` skills.

## Work-Unit Policy

WORKPOLICY freezes provider-neutral policy metadata for future model and effort
selection. It is a contract surface only; v8 does not schedule individual lanes
as runner-owned work units.

The work-unit kind vocabulary is:

- `roadmap_build`
- `phase_plan`
- `lane_execute`
- `lane_review`
- `phase_reducer`
- `phase_verify`
- `repair`
- `closeout`

The normalized effort vocabulary is:

- `minimal`
- `low`
- `medium`
- `high`
- `xhigh`
- `max`

Unsupported provider policy must resolve to exactly one explicit behavior:

- `block`: fail closed when the selected provider cannot honor the requested
  work-unit, model, effort, or thinking policy.
- `fallback`: use a named fallback policy, executor, model alias, or effort
  mapping and record that fallback in launch metadata.
- `inherit_default`: use the provider or profile default only when that default
  inheritance is explicitly recorded.

Silent downgrade is forbidden. A requested effort such as `xhigh` must not
become `high` unless `fallback` or `inherit_default` was selected and recorded.

Provider capability normalization covers:

- Codex/OpenAI: accepts the normalized work-unit and effort metadata directly
  for `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`.
- Claude Code: maps normalized policy onto Claude effort controls; unsupported
  high-end distinctions such as `xhigh` require explicit fallback to `max`.
- Gemini CLI: defaults to built-in routing aliases, using `pro` for
  planning/review and `auto` for execution/repair so Gemini CLI can apply its
  own fallback behavior. Explicit phase-loop proof runs may still use
  run-local user-scope `modelConfigs.customAliases` for
  `phase-loop-plan-high`, `phase-loop-execute-medium`, and
  `phase-loop-review-high`; those aliases carry
  `thinkingConfig.thinkingLevel` and non-secret auth selector metadata. A
  project-local `.gemini/settings.json` beside the prompt workspace is not
  sufficient evidence for custom policy injection.
- Gemini API/OpenAI-compatible: remains metadata-only unless a future adapter
  phase explicitly owns and verifies an API path.
- OpenCode: records normalized work-unit and effort metadata for adapter
  selection without changing current dispatch behavior.
- Pi Agent: consumes repo-local `phase-loop-pi/**` prompts, skills,
  extensions, and `pi-config/**` installation metadata through a context-file
  launch. `executor=pi` is bounded to simple scheduler-assigned lane work and
  never owns global scheduling, runtime ledger mutation, worktree allocation,
  or merge reduction.
- Manual handoff: non-default and selected only by operator, roadmap, or
  phase-plan policy.
- Generic command adapters: require explicit adapter inputs and fail closed
  when policy cannot be mapped.

## Automation Handoffs

This protocol is harness-neutral, not a blanket support claim. Operator-facing
docs must pair it with the current maturity matrix in
`docs/phase-loop/harness-capability-matrix.md` so shared artifact shapes are
not mistaken for proof that every executor is live-supported.

## Phase-Loop Closeout Schema (v1)

The canonical closeout schema (`phase_loop_closeout.v1`) is a nested object that describes the terminal state of a phase execution. It is emitted as a JSON object with these top-level fields:

- `schema`: Always `phase_loop_closeout.v1`.
- `phase`: The phase alias (e.g., `RUNNER`).
- `terminal_status`: The high-level phase outcome (`complete`, `blocked`, `failed_verification`, `human_required`, `stale_input`).
- `automation`: An object describing the next machine steps.
- `artifacts`: An object listing produced artifacts and plan metadata.
- `verification`: An object describing verification status and evidence.
- `blocker`: An object describing why a phase is blocked, if applicable.
- `source_bundle`: An object describing the pipeline source context. Standalone
  closeout keeps this object present with `pipeline_mode: standalone` and may
  omit Pipeline-only identity fields.
- `source_truth_impact`: An advisory object describing metadata-only
  source-truth impact hints for changed paths.
- `lane`: Optional scheduler-owned lane closeout metadata for work-unit
  launches, containing lane identity, wave identity, worktree identity,
  verification status, changed paths, and redacted evidence refs.

Native v1 JSON closeout must not include deprecated root-level v5 automation aliases
such as `status`, `next_skill`, `next_command`,
`verification_status`, `artifact`, or `artifact_state`. Those values belong
inside the nested `automation` object.
Terminal-summary extraction remains a legacy compatibility path for rendered
`automation:` blocks; it is not the native JSON fixture contract.

## Native Output Schema Enforcement

NATIVE added `CLOSEOUT_SCHEMA` in `phase_loop_runtime.models` as the temporary
structured-output contract before BAML became the single source of truth. The
schema requires
`terminal_status`, `verification_status`, `dirty_paths`, and
`produced_if_gates`. A closeout that claims `terminal_status=complete` must
report at least one produced IF gate at the schema layer.

Codex live launches that require a closeout write `CLOSEOUT_SCHEMA` to a
temporary JSON file, append `--output-schema <path>`, record the path on
`LaunchSpec.cleanup_paths`, and remove it after subprocess completion. Claude
live launches that require a closeout append `--json-schema <compact-json>`
with the same schema. Gemini, OpenCode, PI, command adapters, and manual paths
do not receive native CLI schema flags during NATIVE.

The NATIVE runner still accepts legacy rendered `automation:` blocks during the
compatibility window. Native JSON closeouts are normalized back into the shared
automation fields before reducer logic runs.

## BAML Closeout Schema

BAMLBASE moves the closeout-emission boundary to the declarative
`EmitPhaseCloseout` function in
`vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml`. The BAML source
defines `PhaseLoopCloseoutV1` with the same root fields as the NATIVE closeout
schema: `terminal_status`, `verification_status`, `dirty_paths`,
`produced_if_gates`, `next_action`, blocker metadata, and
`required_human_inputs`.

`phase_loop_runtime.baml_modular.build_baml_request("EmitPhaseCloseout",
payload)` loads the vendored BAML source and returns the model-facing prompt
plus request metadata. Harness prompt injection consumes that rendered prompt
instead of duplicating the closeout field ceremony in each skill.

`phase_loop_runtime.baml_modular.parse_baml_response("EmitPhaseCloseout",
raw_text)` parses child closeout output through the BAML runtime and then
normalizes the typed value back into the runner's shared automation fields.
BAML validation errors are reported as repairable non-human `contract_bug`
blockers with non-secret summaries.

## Schema-Flow Architecture

`vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` is the canonical
closeout contract. `phase_loop_runtime.baml_modular.export_function_schema(
"EmitPhaseCloseout")` reads that BAML function, exports the `PhaseLoopCloseoutV1`
object shape, applies documented Codex/Claude JSON Schema dialect
normalization, and fails with `BamlValidationError` when the BAML function or
return class cannot be exported. Missing BAML schema export is a repairable
non-human `contract_bug`; the runtime must not silently downgrade to a duplicate
hand-written schema.
The canonical helper call is `export_function_schema("EmitPhaseCloseout")`.

`phase_loop_runtime.models.CLOSEOUT_SCHEMA` is a compatibility import path for
existing callers and is computed from
`export_function_schema("EmitPhaseCloseout")` at module import time. Codex
launches that require closeout write that schema to `--output-schema <path>`;
Claude launches pass the same canonical schema through
`--json-schema <compact-json>`. Gemini, OpenCode, and PI do not expose matching
native flags, so their closeout prompts embed deterministic schema-description
text from `inject_schema_description(prompt, schema)` with the canonical schema
hash and ordered fields.

All executor closeouts still pass through `parse_baml_response(
"EmitPhaseCloseout", raw_text)` and then through IF-Gate Tier 1 validation.
The runner compares `produced_if_gates` with the active plan's declared
`Produces` / `Interfaces provided` gates, so native flags, prompt embedding,
BAML parse, and IF-gate cross-check all consume the same BAML-authored schema
flow.

### Strict Mode Transition

BAMLBASE ends the NATIVE compatibility window for native JSON closeouts.
Completed closeouts that omit `produced_if_gates`, report an empty gate list,
or otherwise violate `PhaseLoopCloseoutV1` are rejected before runner state can
advance. Legacy rendered `automation:` blocks remain a compatibility path only
for actions that do not emit native JSON closeouts. IF-Gate Tier 1 validation
continues to compare the typed `produced_if_gates` list with the active phase
plan's declared gates.

## IF-Gate Tier 1 Validation

NATIVE adds `validate_produced_gates(plan_path, closeout_payload)` in
`phase_loop_runtime.closeout_validation`. It extracts the active phase plan's
declared IF gates from `Produces` and lane `Interfaces provided` declarations,
then compares them with closeout `produced_if_gates`.

During the NATIVE compatibility window, a completed legacy closeout with no
`produced_if_gates` field records a warning and remains compatible. A completed
closeout with `produced_if_gates: []`, missing expected gates, or unexpected
gates is blocked as repairable non-human `contract_bug`. This is the Tier 1
scope check only; filesystem evidence verification remains out of scope.

#### Automation Object

Phase-loop aware skills and manual TUI runs must emit a machine-readable `automation` object with this exact field set:

```yaml
automation:
  status: <phase status literal>
  next_skill: <skill name or none>
  next_command: <command string or none>
  next_model_hint: <model profile key or none>
  next_effort_hint: <reasoning effort hint or none>
  human_required: <true|false>
  blocker_class: <frozen blocker class or none>
  blocker_summary: <short actionable summary or none>
  required_human_inputs: []
  verification_status: <not_run|passed|failed|blocked>
  artifact: <absolute artifact path or none>
  artifact_state: <staged|tracked|modified|unstaged|untracked|blocked|none>
```

#### Artifacts Object

- `plan_path`: Path to the phase plan file.
- `plan_sha256`: SHA-256 of the phase plan file.
- `artifact_paths`: A map of logical names to absolute paths for produced artifacts.
- `changed_paths`: A list of repository paths modified during the phase.
- `evidence_refs`: Metadata-only evidence references. Entries may include paths,
  labels, and hashes, but not raw transcripts, provider payloads, local
  environment values, credentials, or private evidence bytes.

#### Source Truth Impact Object

`source_truth_impact` is advisory metadata only. Impact hints are advisory:
governed-pipeline owns canonical refresh, replan, and block decisions.
Dotfiles must not update governed-pipeline canonical docs/specs, `.pipeline/**`,
Portal contracts, Greenfield authority files, raw evidence, or legacy
`.codex/phase-loop/` state in response to these hints.

- `changed_path_boundaries`: A list of objects containing `path` and `category`.
  Category is one of `code`, `tests`, `docs`, `specs`,
  `active_canonical_spec`, `managed_root_mirror_spec`, `mirror_manifest`,
  `archive_manifest`, `archived_spec`, `unmanaged_spec`,
  `pipeline_sources`, `portal_contract_refs`, `greenfield_authority_refs`,
  or `unknown`. The broad `specs` category remains a compatibility literal,
  but standalone root `specs/**` paths classify as `unmanaged_spec` unless a
  validated source bundle marks them as managed mirror or archived material.
- `canonical_refresh_recommended`: Boolean advisory signal for source-truth
  sensitive changes.
- `canonical_refresh_reason_codes`: A list using `docs_source_truth_touched`,
  `specs_source_truth_touched`, `active_specs_touched`,
  `managed_mirror_specs_touched`, `mirror_manifests_touched`,
  `archive_manifests_touched`, `archived_specs_touched`,
  `unmanaged_specs_touched`, `adoption_contracts_touched`,
  `contract_refs_touched`, `pipeline_sources_touched`,
  `portal_contract_refs_touched`, or `greenfield_authority_refs_touched`.
- `redaction_posture`: One of `metadata_only` or
  `rejected_forbidden_metadata`.

Impact and evidence metadata exclude raw diffs, raw transcripts, secret-like values,
absolute private paths, provider payloads, credential payloads, local environment values,
and private evidence bytes. Redaction violations make the closeout
malformed instead of preserving the forbidden content.

#### Verification Object

- `status`: One of the local verification status literals such as `passed`,
  `failed`, `blocked`, `not_run`, or `unknown`.
- `commands`: Optional command strings used as metadata-only proof of what was
  checked.

#### DFPARSOAK Receipt Boundary

DFPARSOAK is the dotfiles substrate soak for the parallel bridge. A valid
DFPARSOAK receipt cites Greenfield `GFPARSOAK` and governed-pipeline
`GPPARSOAK` as metadata-only upstream receipts with phase alias, repo-relative
path, sha256 digest, produced interfaces, verification status, and redacted
evidence refs.

The local wave proof uses scheduler-owned git-worktree assignment metadata and
records Pi Agent default coverage, Claude Code CLI exception coverage, and
Codex/Gemini fallback coverage. Fallback or default inheritance must be
explicitly recorded; silent downgrade is invalid.

DFPARSOAK closeout evidence must remain redacted evidence handles or hashes. It
must preserve no sibling-repo mutation and must not contain raw logs, raw
transcripts, raw prompts, provider payloads, credentials, local env values, raw
diffs, ignored private paths, or host-only evidence paths.

#### Source Bundle Object

- `pipeline_mode`: One of `standalone`, `pipeline_optional`, or
  `pipeline_required`.
- `path`: Path to the source bundle JSON. Required for `pipeline_required`;
  optional or absent for standalone closeout.
- `sha256`: SHA-256 of the source bundle. Required for `pipeline_required`;
  optional or absent for standalone closeout.
- `phase_id`: The canonical pipeline phase ID. Required for
  `pipeline_required`; optional or absent for standalone closeout.
- `protected_sources`: Optional metadata-only protected-source echo. Entries
  may include `path`, `category`, `sha256`, and adoption-sensitive `role`, but
  must not include raw spec bodies, raw diffs, provider payloads, credentials,
  local environment values, private evidence, or absolute private paths.

Pipeline-required execution must fail closed before child launch when the plan
or deterministic bridge output cannot supply matching `source_bundle.path`,
`source_bundle.sha256`, `source_bundle.phase_id`, `source_bundle.pipeline_mode`,
`phase`, `phase_alias`, `plan_path`, and `plan_sha256`. The resulting direct
closeout is still a typed `phase_loop_closeout.v1` blocker with metadata-only
diagnostics. Dotfiles reports the consumed identity; governed-pipeline remains
the canonical source authority.
- `pipeline_mode`: One of `standalone`, `pipeline_optional`, `pipeline_required`.

#### Lane Object

The optional `lane` object is present only when a closeout summarizes a
runner-assigned work unit. It records scheduler-owned metadata without making
dotfiles the scheduler, runtime ledger, worktree allocator, merge reducer, or
authority-digest owner:

- `lane_id`: Lane identity selected by the phase plan.
- `wave_id`: Wave identity assigned by the scheduler.
- `worktree_path`: Worktree identity assigned by the scheduler.
- `verification_status`: Work-unit verification result.
- `changed_paths`: Repository paths changed by the work unit.
- `evidence_refs`: Redacted evidence references such as artifact paths and
  digests; raw transcripts, credentials, private file contents, and provider
  tokens are not closeout fields.

When human-readable top-level handoff fields such as `artifact`,
`artifact_state`, `next_skill`, or `next_command` are also present, they must
agree with `automation.*` or the handoff is malformed.

The phase status vocabulary used by `automation.status`, ledger events, and
reconciled state is frozen to:

- `unplanned`
- `planned`
- `executing`
- `executed`
- `awaiting_phase_closeout`
- `complete`
- `blocked`
- `unknown`

## Event Ledger Records

Durable loop events are append-only records stored in the active runtime ledger.
Shared event semantics include:

- `schema_version: 2`
- `roadmap_sha256`
- `phase_sha256`
- phase status literals from the frozen vocabulary in this document
- blocker metadata when a blocker exists
- optional top-level `selected_executor` when dispatch or work-unit launch
  selected an executor
- model provenance and executor metadata when available
- dispatch metadata when selection occurs: `selected_executor`, `source`,
  `selected_via`, `considered_executors`, `fallback_applied`,
  `blocked_reason`, and `blocked_summary`
- optional delegation metadata when a run proposes or launches nested work:
  request contract, approval or denial decision, budget metadata, and
  parent-child lineage pointing to child artifacts without copying prompt bodies
  or collapsing native Claude team activity into runner-visible child work

Manual/operator events must use the same contract surface as autonomous runs.
Legacy hashless records may remain visible for audit but must not drive future
autonomous execution.

## Live Adapter Contract

The live-adapter readiness contract is frozen around these shared code surfaces:

- `LaunchRequest` carries adapter input selection: repo, roadmap, phase, plan,
  model, permission policy, injection metadata, dispatch policy, and optional
  delegation lineage. Mixed-run delegation keeps the resolved
  `DispatchDecision`, typed `DelegationRequest`, and `ParentChildRunMetadata`
  on the shared request object instead of reconstructing them later. TEAMGOV
  extends this request contract with `claude_execution_mode` literals
  `solo`, `subagent`, and `agent_team`, plus optional typed
  `claude_team_policy` and `phase_team_eligibility` metadata for governed
  Claude-native collaboration.
- `LaunchSpec` carries the reduced launch contract written to `launch.json`:
  the redacted command, availability, proof gate, promotion status, auth
  preflight mode, timeout posture, output-capture format, delivery literals
  `prompt_only`, `inline`, `stdin`, `context_file`, and `manual`, plus the
  `terminal-summary.json` artifact path contract. When an adapter needs
  executor-specific launch state such as permission posture, selected agent,
  provider-qualified model, or reasoning variant, that metadata must also stay
  in `LaunchSpec` and `launch.json` instead of being inferred later from raw
  output. The generic `command` adapter is frozen as an explicit
  `command_template` contract: it records `command_adapter_name`,
  `command_template`, `wrapped_cwd`, delivery mode, `context_path`, and
  `context_sha256`; it must fail closed when required placeholders or adapter
  inputs are missing. Governed Claude launch specs must also preserve the
  resolved `claude_execution_mode`, the typed `claude_team_policy`, and the
  evaluated `phase_team_eligibility` so unsafe native-team requests are
  rejected before launch and safe governed launches record their policy even
  when public task-list CLI flags remain unavailable.
- `LaunchResult` carries the observed child result and the same reduced
  availability metadata back into events and monitors.
- `ExecutorCapabilityRecord` is the frozen registry surface for per-executor
  live readiness, proof-gate status, promotion requirements, permission
  posture, auth-preflight probe mode, timeout policy, output capture, and
  terminal-summary reduction expectations. The registry vocabulary is frozen to
  `promotion_status` literals `live`, `proof_gated`, and `manual_only`; shared
  failure literals `adapter_failure` and `phase_failure`; and blocker posture
  literals `human_required` and `repairable_non_human`.

Later adapter phases may fill in executor-specific command vectors and
disposable proof artifacts, but they must not move these shared fields into ad
hoc dicts or harness-specific markdown.

Shared failure reduction rules are also frozen here:

- `adapter_failure` covers launch, output-capture, automation-closeout,
  terminal-summary, stale-handoff, and other executor-path failures where the
  selected harness could not satisfy the shared contract for the requested
  phase.
- `phase_failure` covers requested work that ran inside the shared contract but
  still failed because the product change, test result, or verification outcome
  was genuinely unsuccessful.
- `human_required` blockers require a true operator action such as account,
  billing, secret, admin, destructive-operation, or product-decision input.
- `repairable_non_human` blockers are runner or adapter failures that should
  route back through repair or planning instead of being described as success.

## Launch Artifacts

Observed launches reduce into these shared artifacts:

- `launch.json` records the launch request/spec metadata, redacted command,
  availability, proof gate, promotion requirements, auth-preflight posture,
  output capture, delivery metadata, `context_path`, `context_sha256`,
  `expected_skill_pack`, `skill_bundle_sha256`, `fallback_mode`, and artifact
  paths without persisting raw prompt or skill-body content. Mixed-run launch
  records must also preserve `dispatch_decision`, `delegation_request`,
  `delegation_decision`, and `parent_child` lineage metadata when present.
  Governed Claude launch metadata must additionally preserve
  `claude_execution_mode`, `claude_team_policy`, and
  `phase_team_eligibility`.
- `phase-loop` is the neutral operator alias for the same runner implementation
  as `codex-phase-loop`; documentation and bootstrap may expose both names, but
  they must point to the same runtime contract.
- Live Claude launches must also reduce the non-interactive `claude -p`
  response back into the shared `automation:` contract. Missing or malformed
  closeout is a repairable non-human blocker, not silent success. TEAMGOV keeps
  team creation, task creation, task list, and direct teammate messaging
  denied by default outside governed native-team mode, and native-team policy
  metadata must stay distinct from runner-brokered delegation lineage.
- Live Gemini launches may use a minimal `--prompt` that points at the
  run-local `context.md` artifact, must set `--skip-trust` for disposable
  headless repos, and must reduce `text`, `json`, or `stream-json` output back
  into the shared `automation:` contract. Installed-skill conflicts stay
  warning-only when repo-sourced injected context succeeded.
- Live OpenCode launches must use `opencode run` with explicit `--dir`,
  `--agent`, `--model`, `--format json`, and the shared `context.md` artifact.
  If the selected OpenCode agent posture would otherwise remain permissive, the
  runner must record that posture explicitly and refuse the launch unless an
  operator or runner policy intentionally opts in. OpenCode `json` output and
  closeout-bearing default output must both reduce back into the shared
  `automation:` contract, and installed-skill conflicts remain warning-only
  when repo-sourced injected context succeeded.
- `.phase-loop/runs/<run-id>/context.md` is the frozen run-local context
  artifact path when the selected delivery mode requires a file. It must live
  beside `launch.json` and contain the same repo-sourced workflow bundle the
  adapter receives: workflow command, action-specific instructions, injected
  skill bodies, delegation guidance when present, and closeout requirements.
- `terminal-summary.json` records the shared child-exit reduction used by
  closeout, repair, and monitor flows.

Adapters may add transport-specific metadata under additional keys, but the
artifact filenames and their shared contract roles stay frozen.

## Operator Maturity Boundaries

The shared protocol freezes artifact shapes, reentry semantics, delegation
metadata, and closeout reduction across supported harnesses. It does not by
itself promote an executor to live-supported status. Live support, experimental
status, or manual-only posture must come from the current roadmap closeout and
capability matrix, while this document stays limited to the common artifact and
monitoring contract.

Operator-facing maturity labels remain distinct from registry promotion
statuses:

- `live-supported` means the current roadmap closeout and disposable proof
  support autonomous use of that executor.
- `proof-blocked` means the executor can still expose launch metadata or manual
  reentry surfaces, but the latest shared disposable proof did not satisfy the
  autonomous contract.
- `experimental` means the executor contract is intentionally narrower than the
  first-class live adapters.
- `manual-only` means manual import or operator reentry is supported without a
  current autonomous live-support claim.

## Terminal Summary

`build_terminal_summary(...)` must emit exactly these fields:

- `terminal_status`
- `terminal_blocker`
- `verification_status`
- `next_action`
- `dirty_paths`
- `phase_owned_dirty`
- `phase_owned_dirty_paths`
- `unowned_dirty_paths`
- `pre_existing_dirty_paths`
- `artifact_paths`

These fields are the shared child-exit reduction contract. Adapters may add
transport-specific metadata elsewhere, but they must not rename or omit these
summary fields.

## Monitor Payloads

`build_notification_payload(...)` must emit exactly these top-level fields:

- `timestamp`
- `repo`
- `roadmap`
- `event_kind`
- `monitor_status`
- `current_phase`
- `current_status`
- `human_required`
- `blocker_class`
- `blocker_summary`
- `required_human_inputs`
- `latest_heartbeat`
- `terminal_summary`
- `state_path`
- `event_path`
- `tui_handoff_path`
- `run_log_path`
- `recommended_action`

This payload is the normalized machine callback surface for monitors,
notification commands, and external supervisors.

## Human-Required Blockers

The blocker taxonomy is frozen to these literals:

- `missing_secret`
- `account_or_billing_setup`
- `admin_approval`
- `destructive_operation`
- `ambiguous_roadmap_selection`
- `product_decision_missing`
- `dirty_worktree_conflict`
- `branch_sync_conflict`
- `sandbox_command_restriction`
- `upstream_phase_unmet`
- `contract_bug`
- `gold_record_amendment`
- `stalled_child_observation`
- `repeated_verification_failure`
- `unretryable_external_outage`

Before setting `human_required=true`, the runner or skill must record safe,
redacted access or environment probes when access is part of the blocker.

When live launch remains blocked behind metadata-only auth or subscription
checks, the default blocker reduction is `account_or_billing_setup` unless a
different frozen blocker literal is explicitly justified by the observed probe
result.

## Lane IR Plan Parser Contract

Execution-ready phase plans may be reduced into `PhasePlanIR` without launching
lane work. The IR is parser-only and records:

- `PhasePlanIR` metadata, attached `ExecutionPolicyDocument`, dispatch hints,
  lane records, dependency edges, and typed diagnostics
- `PhasePlanLane` identity, display name, owned files, read-only status,
  `depends_on`, `blocks`, provided interfaces, consumed interfaces, task
  buckets, verification commands, `parallel_safe`, reducer kind, and
  lane-specific execution policy
- `LaneDependency` edges from producer or prerequisite lanes to dependent lanes
- `LaneTaskSet` buckets for `test`, `impl`, `verify`, and `other`
- `LaneIRDiagnostic` with `kind`, `message`, optional `lane_id`,
  `human_required=false`, and `blocker_class=contract_bug`

The frozen diagnostic kinds are `cycle`, `overlapping_write_ownership`,
`unsafe_concurrent_lane`, `missing_producer_dependency`, `missing_owned_files`,
`malformed_owned_files`, `malformed_dependencies`,
`unsupported_lane_policy`, and `missing_lane_sections`.

The frozen reducer kinds are `none`, `acceptance_reducer`,
`compatibility_reducer`, `verification_reducer`, and `summary_reducer`.

Malformed lane IR is a repairable non-human blocker. The parser fails closed
for dependency cycles, overlapping write ownership, missing producer
dependencies, malformed or missing owned-file lines, malformed dependencies,
and unsupported lane policy literals. Existing `phase_loop_plan_version: 1`
frontmatter remains unchanged. The LANEIR contract does not add scheduler mode,
worktree fanout, runner-owned lane launches, reducer launches, or any change to
coarse phase execution defaults.

## Work-Unit Lifecycle

UNITRUN adds runner-owned work-unit records behind explicit work-unit mode while
leaving coarse phase execution intact. A work-unit identity is frozen as
`<phase>.<kind>.<lane-or-reducer-id>.<attempt>` and is represented by typed
fields on `WorkUnitIdentity`: `phase`, `kind`, `lane_id`, `attempt`, and
`work_unit_id`.

The frozen work-unit statuses are `pending`, `running`, `complete`, `blocked`,
`skipped`, `superseded`, and `awaiting-closeout`. These are intentionally
separate from phase statuses such as `planned`, `executing`, and `complete`.

The state snapshot may include `work_units` and `latest_work_unit`. Each
`WorkUnitState` records identity, status, parent phase event, policy, artifact
paths, `heartbeat_path`, `terminal_summary_path`, retry lineage, blocker
metadata, and timestamps. Malformed work-unit state records are ignored by the
work-unit loader rather than breaking legacy phase-level state reads.

The event ledger may include `event_kind: work_unit` entries. A
`WorkUnitEventMetadata` record stores launch metadata, heartbeat path, terminal
summary path, closeout summary, retry lineage, blocker metadata, and the current
work-unit status. These records coexist with phase-level `LoopEvent` entries and
must not cause older phase-level reconciliation to treat work-unit statuses as
phase statuses.

The shared `automation:` closeout contract remains valid at phase level.
Work-unit closeout adds optional work-unit fields through `WorkUnitCloseout` and
optional `terminal-summary.work_unit` data without changing the required frozen
terminal summary fields. A lane work-unit closeout may record `lane_id`,
`wave_id`, `worktree_path`, `verification_status`, `changed_paths`, and
redacted `evidence_refs`; it must not store raw transcripts, credential
payloads, private source bytes, or provider tokens. Work-unit metrics may
include `work_unit_metric.v1.work_unit_id`, `work_unit_metric.v1.lane_id`, and
`work_unit_metric.v1.wave_id`.

Resume semantics are work-unit granular: completed units are skipped,
non-human blocked or stale running units may be retried with a new attempt,
stale attempts are marked `superseded`, and human-required blocked units remain
blocked until operator action resolves them. UNITRUN does not schedule
dependency waves or isolated worktrees; WAVESCHED owns that later behavior.

## Lane Scheduler

WAVESCHED adds runner-owned lane scheduling behind an explicit scheduler gate.
Lane scheduler modes are `off`, `serialized`, and `concurrent`. `off` is the
default and preserves coarse phase execution. `serialized` may select one ready
lane on the main worktree. `concurrent` may select multiple ready lanes only
when writer ownership is disjoint and each writer has a `git_worktree`
assignment.

`LaneWave` records `wave_id`, ordered `lane_ids`, scheduler `mode`, and
optional `LaneWorktreeAssignment` records. `LaneWaveDecision` records status
`ready`, `blocked`, or `empty`, plus pending, completed, blocked lane IDs, and
typed diagnostics. `LaneWorktreeAssignment` records `lane_id`,
`worktree_path`, `isolation_mode`, optional branch, and optional `base_sha`.
Worktree isolation modes are `main_worktree` and `git_worktree`.

The scheduler-owned assignment field list accepted by dotfiles is: lane id
(`lane_id`), wave id (`wave_id`), worktree path (`worktree_path`), base SHA
(`base_sha`), isolation mode (`isolation_mode`), owned files (`owned_files`),
read-only refs (`read_only_refs`), harness route (`harness_route`), model
(`model`), effort (`effort`), and fallback reason (`fallback_reason`).
Governed Pipeline owns scheduling, runtime ledger, worktree assignment, and
merge reduction. Greenfield owns deterministic authority contracts, authority
refs, and authority digests. Dotfiles consumes governed-pipeline assignments
and Greenfield authority refs as inputs and must not promote those inputs into
dotfiles-owned runtime authority.

Worktree placement uses `<WORKTREE-PATH-REDACTED>
when `/mnt/workspace` exists; otherwise it uses default sibling placement next
to the repo. The runner must halt between lane launches when a stop file is
present and must not rewrite in-flight heartbeat, terminal-summary, metric, or
closeout artifacts.

Concurrent scheduling is available only for scheduler-approved
`parallel_safe` non-reducer writer waves with disjoint writable ownership and
isolated `git_worktree` assignments. Serialized scheduling and coarse phase
execution remain compatibility paths and do not require scheduler-owned
worktree assignments. Unsafe concurrent selection must fail closed with typed
diagnostics for overlapping owned files, missing worktree assignment, stale
base SHA, active work unit, human-required blocked work unit,
reducer-in-wave, stop-file interruption, and malformed closeout.

Dirty-path reduction uses `DirtyPathClassification` with classifications
`pre_existing`, `lane_owned`, `peer_owned`, `reducer_owned`, and `unowned`.

Ignored, private, raw-data, credential, and evidence-source files are
read-protected by default. Child executors may read those paths only when the
run-local context, phase plan, or Pipeline source bundle explicitly allowlists
the exact path or glob as a read input. Owned output paths do not imply
permission to read ignored or private source inputs, and old memory or prior
phase behavior is not an allowlist.

Before any phase or work-unit closeout claims success, the child executor must
audit `git status --short` against the active owned-file contract. Dirty paths
must be classified as phase/lane-owned, planning/control, pre-existing
unrelated, or unowned. Ignored phase-owned outputs may be preserved only when
the plan/source bundle includes an explicit allowlist or staging policy;
otherwise the executor must report a repairable `dirty_worktree_conflict`.

File-rename pairs in the dirty tree — both the `R src -> dst` form that git
emits when both sides are staged, and the unpaired `D src` + `?? dst` form
that arises when an executor performs a filesystem-only `mv` — are detected by
exact blob-hash equality between the deleted HEAD blob and the untracked
working-tree file. When the destination is phase-owned, the source path is
promoted to `phase_owned_dirty` automatically so a "sensible refactor
instinct" move does not fire as `unowned_dirty_paths`. Detection is exact,
not similarity-based: a move that also rewrites content is not paired, and
must be declared in the plan's owned-file contract explicitly.

## Harness Lane Workflows

HARNESSLANE adds a runner-owned lane assignment payload for harness-specific
work units while preserving the coarse phase execution path. The frozen payload
is `HarnessLaneAssignment` with `schema=harness_lane_assignment.v1` semantics:

- `phase`
- `lane_id`
- `work_unit_kind`
- `prompt_kind`
- `wave_id`
- `owned_files`
- `read_only_refs`
- `consumed_interfaces`
- `depends_on`
- `execution_policy`
- `worktree_assignment`
- `harness_route`
- `model`
- `effort`
- `fallback_reason`
- `closeout_schema_required`
- `reducer_kind`
- `metadata`

The frozen `HarnessWorkUnitPromptKind` literals are `implementation`, `review`,
`reducer`, `verify`, and `closeout`. Implementation prompts can edit only the
assigned `owned_files`. Review prompts must not make production edits. Reducer
prompts may summarize producer results and closeout state, but must not claim
producer write ownership.

`build_lane_prompt_bundle`, `render_harness_lane_context`,
`render_harness_review_context`, and `render_harness_reducer_context` render a
single selected work unit for Codex, Claude, Gemini, OpenCode, or command
adapters. Each bundle carries the selected lane id, owned files, consumed
interfaces, work-unit kind, execution-policy summary, worktree assignment
metadata, and the required shared `automation:` closeout fields. These bundles
do not grant whole-phase implementation authority.

`LaunchSpec.harness_lane_assignment` and `launch.json.harness_lane_assignment`
freeze lane metadata for live launches. `launch.json.lane_id` and
`launch.json.work_unit_kind` are convenience copies for metrics and terminal
summaries. Claude `subagent` and `agent_team` launches remain gated by
team-safe lane policy, runner work-unit state, delegation broker depth/fanout
limits, and owned-file boundaries. Gemini lane launches use built-in routing
aliases such as `pro` and `auto` by default; explicit run-local aliases such as
`phase-loop-execute-medium` and `phase-loop-review-high` remain opt-in proof
surfaces and fail closed through the unsupported-policy diagnostic path when
misspelled. OpenCode lane launches record explicit `opencode run`,
`--dir`, `--agent`, provider-qualified `--model`, optional `--variant`,
`--format json`, and permission posture metadata. Command adapter lane launches
require an explicit template that includes `{context_file}`.

## Closeout Modes

Closeout policy is frozen to:

- `manual`
- `commit`
- `push`

`manual` preserves verified phase-owned dirty output without mutating git
state. `commit` may preserve only trusted phase-owned dirty paths in a local
commit. `push` may build on that local commit only when branch/base topology is
safe and explicit.

## Roadmap Amendments

Execution discoveries that change downstream scope must amend the nearest
downstream roadmap phase that is not already executing. After the amendment:

- reconcile against the amended roadmap before selecting the next phase
- treat older downstream phase plans or handoffs as stale when their roadmap
  metadata no longer matches
- regenerate any affected downstream phase plan through the planner before
  execution resumes
- preserve prior ledger records for audit, but let later trusted events on the
  amended roadmap suppress stale mismatch noise

## Delegation Broker

Cross-harness delegation remains runner-owned. Child runs may recommend nested
work only through a typed delegation request contract. The frozen request shape
includes:

- `request_id`
- `product_action`
- `target_executor`
- `reason`
- `owned_files`
- `expected_output`
- `priority`
- optional `review_context`
- optional `repair_context`
- optional metadata-only `budget`

Delegation approval is validated in this order:

1. active-loop mode
2. ownership boundaries
3. depth limit
4. fanout limit
5. budget metadata
6. dispatch policy

Denied requests must produce a typed non-human blocked outcome with a clear
reason code and summary. Approved requests must reuse the normal runner launch
path so launch artifacts record request metadata, resolved executor, observed
launch path, and parent-child linkage without changing the frozen top-level
monitor payload fields.

Claude-native subagents and agent teams do not implicitly create runner child
runs. They become runner-visible child work only after a typed delegation
request is emitted and approved through this broker contract.

Delegated cross-harness child work is frozen to `execute`, `repair`, and
`review`, and approved child executors are limited to `codex` and `claude`.
Native Claude team tasks stay internal unless they externalize through the same
typed `DelegationRequest` broker path.
Top-level executor switching remains an operator or runner decision, not a
native-team side effect: choose the top-level harness through normal dispatch
selection or `--executor`, then require typed broker approval before any child
work crosses harness boundaries.

The shared mixed-run lineage surface includes:

- `DispatchDecision.selected_via`
- `DispatchDecision.considered_executors`
- typed delegation denial `reason_code`
- `ParentChildRunMetadata.parent_executor`
- `ParentChildRunMetadata.child_executor`
- `ParentChildRunMetadata.child_artifact_root`
- `ParentChildRunMetadata.child_worktree_root`
- `ParentChildRunMetadata.child_closeout_result`

## Manual Advancement and Import

Manual TUI runs remain valid when they emit the same shared artifact contract
as automated runs. Shared manual rules:

- append a `manual` source event when phase-loop state already exists
- keep `automation.*` aligned with the human-readable handoff
- use `manual_repair` with `clears_blocker=true` only when a repair actually
  clears the recorded blocker
- autonomous child executors should not edit `.phase-loop/` or claim durable
  ledger mutation unless the injected launch explicitly permits and confirms
  it; the parent runner owns blocker clearing and closeout reconciliation from
  valid shared automation closeouts
- when a manual run amended the roadmap downstream, compute
  `roadmap_sha256` and `phase_sha256` from the amended roadmap for the current
  phase being closed instead of reusing stale plan hashes
- installed bridge skills are recommended for manual takeover and TUI reentry,
  but repo-injected workflow bundles remain the autonomous source of truth for
  child launches

## Runtime Path

For this roadmap family, `.phase-loop/` is the canonical durable runtime path
for state, ledger, handoff, and observed-run artifacts. Existing
`.codex/phase-loop/` artifacts remain a legacy read fallback so active repos are
not stranded during migration. New writes use `.phase-loop/`.

`phase-loop init [--repo <path>] [--dry-run]` initializes the repo-local
handoff storage expected by workflow skills. It idempotently adds
`/.dev-skills/` to the target repo `.gitignore` without duplication and creates
`.dev-skills/handoffs/` unless `--dry-run` is set.

## Runtime Boundary

The phase-loop runtime is isolated as a coherent internal package within the
dotfiles repository. It provides a stable CLI and Python API boundary for
external tools and repositories.
The authoritative boundary document is `docs/phase-loop/runtime-boundary.md`.
The dotfiles-specific substrate path inventory is
`docs/phase-loop/harness-substrate-manifest.md`; it is intentionally separate
from this schema contract.

Key boundary properties:
- The primary CLI entrypoint is `phase-loop` (aliased as `codex-phase-loop`).
- The CLI supports a `version` command and `--version` flag for contract reporting.
- Public Python modules are exposed under the `phase_loop_runtime.*` namespace.
- Internal modules not explicitly listed in `__all__` or the boundary document
  are considered private and subject to change.
- Durable state and events are stored under `.phase-loop/` (legacy fallback
  `.codex/phase-loop/`).
- New writes use `.phase-loop/`.

## Extraction Readiness

The protocol and runtime boundary are considered "Extraction Ready" when:
1. All public CLI commands are documented and stable.
2. All machine-readable artifacts follow frozen schemas.
3. Cross-repo compatibility fixtures exist for downstream validation.
4. The migration path does not break existing `dotfiles` installation.

This roadmap family confirms that phase-loop has met these criteria.

## Cross-Repo Compatibility Fixtures

To ensure stable integration with downstream repositories such as `governed-pipeline`, this repository provides a set of canonical fixtures that demonstrate various phase-loop outcomes.

### Fixture Location

Stable fixtures are located under:
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/`

These fixtures are dotfiles-owned substrate. The governed-pipeline mirror
location is `packages/pipeline-runtime/test/fixtures/phase-loop-bridge/`, and
mirror updates, closeout ingest, canonical refresh, replan, and preflight block
decisions are governed-pipeline-owned work. Dotfiles must not edit the
governed-pipeline mirror path from this repository.

### Available Scenarios

- `complete.json`: A standard successful phase execution.
- `blocked.json`: A phase blocked by a non-human restriction (e.g., sandbox).
- `stale_input.json`: A phase with mismatched plan or roadmap hashes.
- `failed_verification.json`: A phase where verification failed.
- `human_required.json`: A phase requiring operator intervention (e.g., billing).
- `dfbundlecloseout_standalone.json`: A standalone closeout without
  Pipeline-only source bundle path or hash identity.
- `dfdriftsignal_canonical_refresh_recommended.json`: Advisory changed-path
  metadata recommending governed-pipeline canonical refresh.
- `dfbundlecloseout_malformed_*.json`: Negative fixtures for malformed
  closeout, missing bundle hash, deprecated root aliases, invalid nested
  objects, invalid terminal status, and forbidden metadata.
- `dfadoptbridge_adoption_complete.json`: Adoption bridge fixture with active
  canonical spec, managed root mirror, mirror manifest, archive manifest,
  source-bundle identity, protected-source roles, and SHA-256 evidence refs.
- `dfadoptbridge_stale_mirror_manifest.json`: Adoption bridge fixture for
  stale mirror manifest metadata and governed-pipeline-owned refresh handling.
- `dfadoptbridge_unmanaged_spec_input.json`: Adoption bridge fixture for
  unmanaged root `specs/**` intake as metadata-only evidence.

Downstream consumers should use these fixtures to verify their ingestion logic against the `phase_loop_closeout.v1` schema.

DFADOPTBRIDGE fixtures extend the adoption bridge matrix for governed-pipeline
v11. They cover adoption complete, blocked adoption metadata, stale source
bundle, stale mirror manifest, unmanaged spec input, archive manifest touched,
standalone non-adoption, deprecated root aliases, and redaction rejection.
Pipeline-required fixtures use `source_bundle.path`, `source_bundle.sha256`,
`source_bundle.phase_id`, protected-source `path`, protected-source `sha256`,
protected-source `role`, plan `sha256`, changed-path categories, advisory
reason codes, and evidence-ref `sha256` metadata. They do not grant dotfiles
permission to write governed-pipeline mirror paths, `.pipeline/**`, Portal
contracts, Greenfield authority files, raw evidence, raw data, credentials, or
legacy `.codex/phase-loop/**` state.

DFADOPTSOAK is the dotfiles adoption integration release gate. It proves root
`specs/**` standalone advisory hints, explicit non-default spec-root intake
metadata, unconfigured non-default root non-advisory behavior,
pipeline-required source-bundle echo, stale-input blockers, v14 bridge fixture
retention, DFADOPTBRIDGE fixture parity, and unittest-first verification. It
consumes governed-pipeline `CADOPTSOAK` only as read-only metadata evidence;
governed-pipeline retains ownership of mirror copies, adoption decisions,
archive creation, managed mirror refresh, source-truth reconciliation,
source-bundle emission, canonical refresh, replan, and preflight block
decisions.

DFFAKESMOKE adds a metadata-only local substrate receipt at
`docs/phase-loop/dffakesmoke-substrate-receipt.md` and a fake-smoke matrix at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_fake_smoke/matrix.json`. The receipt fields are
`phase`, `roadmap_sha256`, `plan_path`, `fake_fixture_matrix`,
`smoke_commands`, `work_unit_evidence_refs`, `verification_status`,
`changed_path_boundaries`, and `redaction_posture`. These fields are
downstream evidence refs only; governed-pipeline remains responsible for
scheduling, runtime ledger policy, worktree assignment, closeout ingest, and
merge reduction.

DFPROMPTSYNC adds a prompt-safe contract map at
`docs/phase-loop/dfpromptsync-contract-map.md`, a readiness receipt at
`docs/phase-loop/dfpromptsync-readiness.md`, and a fixture matrix at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_prompt_sync/matrix.json`. Prompt bundles and harness
lane workflows may cite schema names, field names, fixture paths, artifact
refs, and digests from those receipts, but must not copy raw secrets, raw
transcripts, raw diffs, raw provider payloads, credential file contents, local
env values, or prompt-only containment claims.

## Direct Invocation Policy

INVOKEOPT adds support for optional direct invocation by external systems (such as Governed Pipeline) while preserving the shared artifact contract as the primary interoperability surface.

### Supported CLI Shape

When direct invocation is enabled, the `phase-loop` CLI exposes a deterministic shape:

```bash
phase-loop execute <phase> --bundle <path> --output <path> --mode <execute|repair|review>
```

- `<phase>`: The phase alias to execute.
- `--bundle <path>`: Path to a `phase-source-bundle.v1` artifact.
- `--output <path>`: Path where exactly one closeout JSON file must be written.
- `--mode`: One of `execute`, `repair`, or `review`.
  Invalid mode literals fail at the direct CLI boundary before child launch.

### Rejection Policy

Unsupported direct invocation must fail closed with a typed diagnostic. The following scenarios result in a `sandbox_command_restriction` or `contract_bug` blocker:

- Missing or invalid `<phase>`.
- Missing `--bundle` when `pipeline_required` mode or the phase plan requires it.
- Missing `--output` when direct invocation is attempted.
- Invalid or unsupported `--mode`.
- Stale `freshness.source_bundle_hash` when it is a SHA-256 digest.
- Unknown phase IDs or aliases in the source bundle.
- Missing or stale protected source entries.
- Malformed `phase_loop_closeout.v1` payloads.
- Missing or incompatible runner for the requested phase.

### Output Semantics

Output semantics are deterministic: exactly one closeout JSON file is written
to the path specified by `--output` for supported `execute`, `repair`, and
`review` invocations, including typed bridge blockers detected before child
launch. Standard output may contain human-readable logs, but machine
integration must depend only on the output file.

## DFPARSOAK Integrated Soak

DFPARSOAK adds the integrated parallel substrate soak source map at
`docs/phase-loop/dfparsoak-source-map.md`, the governed-pipeline-consumable
receipt at `docs/phase-loop/dfparsoak-receipt.md`, the operator runbook at
`docs/phase-loop/dfparsoak-runbook.md`, and the fixture matrix at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_dfparsoak/matrix.json`.

The soak closeout records `lane_id`, `wave_id`, `worktree_path`,
`isolation_mode`, `base_sha`, `harness_route`, `work_unit_kind`, `model`,
`effort`, `policy_source`, `fallback_reason`, verification status, changed
paths, and redacted evidence refs. Governed Pipeline still owns scheduling and
closeout ingest; Greenfield still owns authority schemas and contract-pack
expectations.
