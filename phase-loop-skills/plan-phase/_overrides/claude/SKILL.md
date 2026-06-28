---
name: plan-phase
description: "Harness Code phase planner for a versioned roadmap phase. Produces an interface-freeze + swim-lane document for parallel execution. Use in plan mode. Supports --consensus for multi-agent architectural consensus across named Plan teammates."
---

# <harness>-plan-phase

## Runtime State

For reflections, handoffs, and latest handoff pointers, follow `<harness>-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

`shared/phase-loop/protocol.md` is the canonical shared contract for
execution-ready plan metadata, including `phase_loop_plan_version: 1`,
`roadmap_sha256`, optional `Dispatch Hints`, and downstream roadmap-amendment
rules.

When this skill runs through the live phase-loop adapter, the child launch is a
non-interactive `<harness> -p` session and the final response must still include a
shared `automation:` closeout with `verification_status`. The repo-injected
workflow bundle remains authoritative whether the adapter delivers it inline or
through the run-local context-file fallback.
That repo-owned bundle includes the full Harness workflow pack
(`<harness>-phase-roadmap-builder`, `<harness>-plan-phase`,
`<harness>-execute-phase`, and `<harness>-phase-loop`) and does not depend on
installed bridge skills under `resolve_skill_bundle_root("claude")/`.

## Phase-Loop Adapter Mode

When the prompt or run-local context file starts with a concrete
`<harness>-plan-phase <roadmap> <phase>` command from `.codex/phase-loop/runs/`,
this adapter mode overrides the delegation-first interactive workflow below.

- Do not read installed handoffs under `resolve_skill_bundle_root("claude")/**`; the run-local
  phase-loop context is the only predecessor context.
- Do not call `ToolSearch`, `TaskCreate`, `TeamCreate`, `TeamDelete`, `Agent`,
  `SendMessage`, `ExitPlanMode`, `advisor()`, or `AskUserQuestion`.
- Resolve the roadmap and phase from the first command line, read only the
  repo-local roadmap and relevant files, and write the repo-local
  `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` artifact directly.
- The plan artifact frontmatter is part of the trust contract. `roadmap:` MUST
  be the exact repo-relative path from repo root to the roadmap file, not a
  shortened basename or suffix, and `roadmap_sha256:` MUST be the SHA-256 of
  that exact roadmap file.
- Do not read or write `~/.claude/**` reflection or handoff files, and do not
  edit `.codex/phase-loop/` runner artifacts. Adapter-mode closeout happens
  only through stdout plus repo-local artifacts.
- If ambiguity, access, dirty-state, or missing-input handling would normally
  ask a user, stop and emit a shared `automation:` closeout with
  `status: blocked`, `human_required` set accurately, the frozen
  `blocker_class`, concrete `blocker_summary`, and `verification_status:
  blocked`.
- End stdout with one shared `automation:` closeout. Do not wait for
  interactive approval after the artifact is written.

## Dispatch Hints

When non-default executor policy is needed, add an optional `## Dispatch Hints`
section using the frozen vocabulary from `shared/phase-loop/protocol.md`
instead of inventing Harness-only plan metadata.

When model, effort, work-unit defaults, lane-specific policy, fallback, policy
source, or override reason must also be frozen, add optional `## Execution
Policy` using the protocol syntax. Precedence is CLI/operator override,
phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults;
silent downgrade is forbidden unless explicit fallback or default inheritance
is recorded.

## Model & Effort Tiering (right-size per lane, don't default to the ceiling)

The runtime resolves one heavy model per executor (`codex` → `gpt-5.5`,
`claude` → `claude-opus-4-8`), so **reasoning effort is the primary cost dial.**
The normalized effort ladder, cheapest first, is:

`minimal` < `low` < `medium` < `high` < `xhigh` < `max`

Registry action defaults today are blunt — `plan`/`roadmap`/`review` = `high`,
`execute`/`repair` = `medium` — applied uniformly regardless of how hard the
lane actually is. As the planner you have the complexity signal the runtime
lacks; use it:

- **Default each lane to the cheapest effort you believe will succeed**, not the
  action default. A mechanical edit, a config bump, a docs-sweep lane, or a
  rename rarely needs more than `low`/`minimal`.
- **Escalate only with a stated reason.** Subtle concurrency, cross-module
  refactors, security-sensitive logic, or ambiguous specs justify `high`/`xhigh`;
  record *why* in the rule's `reason=`.
- Express the choice in an `## Execution Policy` section the runtime parses
  (selector = `default`, an action, or a lane alias):

```
## Execution Policy
- default: effort=low, reason=most lanes are mechanical this phase
- execute: effort=medium
- SL-3: effort=high, reason=constant-time comparison is subtly wrong-prone
- SL-7: effort=minimal, reason=docs sweep only
```

Two hard syntax rules the parser enforces (a malformed line fails the whole
section, not just that line):

- **Lane selectors are the numeric lane id** (`SL-3`, `P2A` → use `lane <name>:`
  for non-numeric names). `SL-DOCS`-style names are rejected; write `SL-7` or
  `lane SL-DOCS: …`.
- **No commas inside a `reason=` value** — the comma separates assignments, so
  `reason=docs sweep, no logic` breaks parsing. Keep reasons comma-free.

Operator `--model`/`--effort` and CLI overrides still win, so this never blocks
a human from forcing a tier. The goal is to stop paying `high` for a one-line
change by default.

**Model class (vendor-agnostic role).** Alongside effort, a rule may set a
`model_class` — `planner` / `implementer` / `worker` — which resolves to a
concrete model per executor. The shipped `model_policy` already routes planning
to the planner class at `max` and implementation to the implementer class, so
you rarely set this by hand; reach for it when a specific lane wants a cheaper
worker-class model for a bounded, schema-checked subtask. The `worker` class
must never author a final patch, and an executor that can't run at `max` (e.g.
gemini) is never the max-effort planner of record.

**Run mode is separate.** `model_policy` (what model) is independent of
`run_mode` (autonomous default vs opt-in governed review). Planning a phase does
not enable the governed panel; that is an operator opt-in.

## Planner Literal Validation

Before writing a plan document to the project path, validate the complete draft
with `phase_loop_runtime.planner_validation.validate_plan_dispatch_hints`. In
Plan Mode, run this after the scratch draft is fully emitted and before
`ExitPlanMode`; in adapter/default flows, run it before writing
`plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md`. If findings are returned, do not
write the plan artifact. Stop with a validation_failed closeout:
`terminal_status=blocked`, `verification_status=blocked`,
`blocker_class=contract_bug`, `human_required=false`, and a non-secret
`blocker_summary` listing each finding's `field_path`, `literal`,
`allowed_values`, and `suggested_fix`.

The validator imports its defaults from `phase_loop_runtime.models`:
`DISPATCH_CAPABILITIES`, `EXECUTORS`, and `PRODUCT_LOOP_ACTIONS`. Keep these
allowed values inline in the planner prompt so invented literals are visibly
out of contract:

- `dispatch_hints.required_capabilities`: `live_launch`, `dry_run`, `skill_bundle_injection`, `inline_instructions`, `context_file_instructions`, `manual_handoff`, `subagents`, `explicit_approval_controls`, `structured_output`, `browser_automation`
- `dispatch_hints.executors[]`: `codex`, `claude`, `gemini`, `opencode`, `pi`, `command`, `manual`
- `## Execution Policy` selectors: `work-unit defaults`, `roadmap`, `plan`, `execute`, `repair`, `review`, `maintain-skills`, and lane selectors such as `SL-2`
- `terminal_status`: `unplanned`, `planned`, `executing`, `executed`, `awaiting_phase_closeout`, `complete`, `blocked`, `unknown`
- `verification_status`: `not_run`, `passed`, `failed`, `blocked`
- `blocker_class`: `missing_secret`, `account_or_billing_setup`, `admin_approval`, `destructive_operation`, `ambiguous_roadmap_selection`, `product_decision_missing`, `dirty_worktree_conflict`, `branch_sync_conflict`, `stalled_child_observation`, `repeated_verification_failure`, `sandbox_command_restriction`, `upstream_phase_unmet`, `contract_bug`, `gold_record_amendment`, `unretryable_external_outage`, `stuck_loop`

Architecture-first planner for a single phase of a multi-phase specification. Produces a plan document containing interface freezes, swim lanes with disjoint file ownership, a lane DAG, per-lane task lists (test → impl → verify), and testable acceptance criteria. Designed to be run in **plan mode** and handed off to `<harness>-execute-phase` for parallel execution.

## When to use

- The input is a multi-phase spec (e.g., `specs/phase-plans-v1.md`) and the user wants to plan a specific phase.
- The work touches more than one area of the codebase and would benefit from parallel lane execution.
- You need interface contracts frozen before lanes diverge.

## When NOT to use

- Single-file, single-concern change → use `/<harness>-plan-detailed` instead.
- Pure research / "how does X work" → use `Agent(subagent_type: "Explore")` directly, no plan doc needed.
- No phase structure in the spec → use `/<harness>-plan-detailed` or ad-hoc planning.

## Inputs

| Arg | Required | Meaning |
|---|---|---|
| `<spec-path>` | no | Path to the spec file (relative to repo root). Default: auto-detected `specs/phase-plans-v*.md` at the highest version. |
| `<phase-name-or-id>` | yes | A phase heading, short alias (`P1`–`P7`), or any fuzzy match. Ambiguous → stop and ask via `AskUserQuestion`. |
| `--output <path>` | no | Override the default output path. Default: `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md`. `<PHASE_ALIAS>` MUST be uppercase exactly as declared in the roadmap (e.g., `FOUND`, `DESIGNFOUND`); lowercase or alternate filename variants force an extra plan-iteration roundtrip because the runner uses the uppercase alias to locate the plan artifact. |
| `--consensus` | no | Enable multi-agent architectural consensus (2–3 Plan teammates with different framings). |
| `--review-external` | no | After writing the plan doc, run Gemini + Codex CLIs in parallel to review it. Requires `gemini` and `codex` installed and authenticated. Produces a `_reviews.md` sibling file. |

Repos may supply a phase alias table (JSON file) via `$PLAN_PHASE_ALIASES` or fall back to the built-in `P1`–`P7` table. If the alias isn't recognized and no custom table is set, stop and ask via `AskUserQuestion` with the actual spec headings.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PLAN_SPEC` | Auto-detected highest `specs/phase-plans-v*.md` | Path to the spec file. |
| `PLAN_VERSION` | Extracted `v\d+` from spec filename | Version string embedded in output filename. |
| `PLAN_PHASE_ALIASES` | Built-in alias table | Path to a JSON file mapping alias → phase heading. |

Example `.env`:

```sh
PLAN_SPEC=specs/phase-plans-v1.md
```

Invocation examples:

```
/<harness>-plan-phase P1
/<harness>-plan-phase P3 --consensus
/<harness>-plan-phase P3 --review-external
/<harness>-plan-phase specs/roadmap.md "Phase 3: Billing" --consensus --review-external
```

## Expected helpers

The skill references these `_shared/` helpers. Each degrades gracefully if absent:

- `_shared/next_reflection_path.py` — legacy helper only; current closeout writes reflections to `resolve_skill_bundle_root("codex")/<harness>-plan-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`.
- `_shared/review_with_cli.py` — required only for `--review-external`. No fallback; if absent, surface an `AskUserQuestion` with `[skip review this run, abort]`.
- `_shared/scaffold_docs_catalog.py` — used by `SL-docs.1`. If absent, the docs lane records "docs-catalog rescan helper unavailable; manual catalog audit" in its commit message and proceeds.

## Deferred tool preloading

Load tools used later in a single query so mid-workflow calls don't pay a round-trip:

```
ToolSearch(query: "select:TaskCreate,AskUserQuestion,ExitPlanMode")
```

## Workflow (delegation-first)

The main thread is an orchestrator only: brief specialists, synthesize output, enforce consensus, write the final doc, emit tasks. See `## Teamwork & delegation posture` for the posture rules.

### Step 0 — Read predecessor handoff (if present)

Handoffs are keyed on the current repo so each workspace has its own slot. Resolve both candidate paths first:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  REPO_KEY="_no-git-$(pwd | sha1sum | cut -c1-12)"
else
  REPO_KEY="$(basename "$REPO_ROOT")-$(printf '%s' "$REPO_ROOT" | sha1sum | cut -c1-12)"
fi
ROADMAP_HANDOFF="$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-phase-roadmap-builder"))
PYH
)"
EXECUTE_HANDOFF="$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-execute-phase"))
PYH
)"
```

The predecessor skill may be either:

- `<harness>-phase-roadmap-builder` (first time planning a phase against a new roadmap) → `$ROADMAP_HANDOFF`
- `<harness>-execute-phase` (planning the next phase after a prior one finished executing) → `$EXECUTE_HANDOFF`

Check both paths. If both exist, pick the one with the newer `timestamp:` in its metadata header. If only one exists, use it. If neither, proceed standalone.

Defense-in-depth checks (the repo-key scheme prevents the common cross-project case, but these still catch symlink-shared workspaces, manual file copies, and stale handoffs):

- `from:` must match the expected predecessor.
- Timestamp must be recent (<7 days).
- Every `artifact:` path must resolve under `$(git rev-parse --show-toplevel)`.

On any failure, flag via `AskUserQuestion` with `[use anyway, ignore, abort]`.

Fold the handoff's "Open items" and "Repo-specific gotchas" into the brief given to Step 2's Explore teammates so they know what to watch for.

### Step 1 — Resolve spec path, phase, and PHASE_ID

**Spec path resolution (in order):**
1. `$PLAN_SPEC` env var → use verbatim.
2. `<spec-path>` arg → use verbatim.
3. Glob `specs/phase-plans-v*.md`; pick the highest version.
4. Else any `specs/*.md` if exactly one exists → use it and note the assumption.
5. Else stop and ask via `AskUserQuestion`.

**Version string** (for output filename): `$PLAN_VERSION` → pattern `v\d+` in filename → `v1` default.

**Phase alias table**: `$PLAN_PHASE_ALIASES` (JSON file) → built-in table.

**Phase name**: short alias → fuzzy match → 0 matches: stop + ask → multiple matches: stop + disambiguate.

**PHASE_ALIAS**: the resolved short alias in lowercase (e.g., `p1`). If none exists, use `phase-<N>`.

**Output path**: `--output` override, else `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md`.

### Step 2 — Parallel reconnaissance via Explore teammates

Preflight: call `TeamDelete` defensively to flush any inherited team context from a predecessor skill run, then `TeamCreate` a fresh team named for this run before dispatching any `Agent` calls. Recognize these error signatures as benign on the `TeamDelete`: `Team X does not exist` or `already leading team X`.

Launch up to 3 `Agent(subagent_type: "Explore")` calls in a single message. One per major area the phase touches. Each Agent call MUST set `name:` so it can be re-addressed later via `SendMessage`.

Teammate-naming template: `explore-<area>` (e.g., `explore-schema`, `explore-workers`).

Each brief must include:

- The phase's Objective + Exit criteria copied verbatim from the spec.
- A scoped question: "Map existing code in `<paths>` relevant to this phase. Surface: (a) existing utilities/patterns to reuse, (b) current type/schema/interface shapes that constrain the design, (c) places that will need to change, (d) hidden coupling that would break worktree isolation."
- A 1–2 sentence architecture context: how these paths fit the larger system.
- Related files the teammate should know about but not rewrite (type defs, tests, shared config).
- A preload instruction: "Load `SendMessage` via `ToolSearch(query: \"select:SendMessage\")` as your first action so you can reply without a round-trip."
- An explicit deliverable statement: "Your deliverable is a `SendMessage` to team-lead with your findings; do not rely on text output or idle without reporting."
- A length guideline: "Be as short as possible while citing every load-bearing file:line. No fixed word cap."

Apply the `/<harness>-task-contextualizer` checklist to every brief.

Block until all return. Their findings populate `## Context`.

### Step 2.5 — Explore teammate recovery

If a teammate idles without a substantive reply, send one targeted `SendMessage` nudge naming the missing deliverable. On a second non-response, proceed with main-thread read-only reconnaissance rather than respawning the teammate.

### Step 3 — Architectural decisions

**With `--consensus`**: Launch 2–3 `Agent(subagent_type: "Plan")` calls in a single message, each with a distinct framing:

| Name | Framing |
|---|---|
| `arch-minimal` | Minimal change. Preserve current module boundaries. Add, don't refactor. |
| `arch-clean` | Clean architecture. Willing to refactor to make the design right. |
| `arch-parallel` | Maximize parallelism. Prefer more, smaller lanes over fewer, fatter lanes, even if it adds interface surface. |

Each teammate's brief includes: the spec phase section, all Explore teammate findings, and its framing. Apply the `/<harness>-task-contextualizer` checklist — architecture context and related-files list carry over from the Explore briefs. Each must return: (1) proposed interface freezes, (2) proposed lane decomposition with file ownership, (3) rationale, (4) known risks.

Synthesize per the Consensus mechanism below. If round 1 doesn't converge, re-address the same named teammates via `SendMessage` (not new `Agent` calls) with the specific disagreement surfaced. Max 2 rounds.

**Without `--consensus`**: Launch 1 `Agent(subagent_type: "Plan", name: "arch-baseline")` for baseline architecture decisions.

## Preamble lane archetype

The "preamble lane" concentrates single-writer files AND freezes interface shapes on Day 1, letting all downstream lanes run in parallel without contending for the same index/config/init file.

**When to use**: ≥2 parallel lanes would otherwise need to touch the same index/config/init file, OR ≥2 lanes consume the same frozen type.

Template sketch:

```markdown
### SL-0 — Preamble (interface + single-writer setup)
- **Scope**: Freeze shared types and stub the single-writer files every downstream lane will import from.
- **Owned files**: `src/index.ts`, `src/config.ts`, `src/__init__.py` (list every shared index/config/init file)
- **Interfaces provided**: `FooContract`, `BarRegistry`, `WorkerRouter` (frozen type names)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal in preamble position — no downstream lane modifies SL-0's files)
- **Tasks**: one test task pinning the frozen type shapes; one impl task adding the stubs; one verify task
```

Downstream lanes depend on `SL-0` and consume its frozen interfaces; they must not list any SL-0 owned file in their own `Owned files`.

### Step 4 — Lane decomposition (main thread)

Synthesize Explore + Plan output into swim lanes. For each lane, determine:

- **Scope** — one sentence.
- **Owned files** — glob list. Must be disjoint from every other lane's globs. Before emitting the plan, scan every lane's owned files for overlap with every other lane in the same wave. If overlap is found, either (a) move the shared file into a preamble lane that all dependents consume, or (b) collapse the overlapping lanes into a single lane. Overlap surviving into the final plan triggers `overlapping_write_ownership` lane-IR diagnostic at runtime and refuses execution (Pattern B from 2026-05-25 runner failure analysis). Owned files MUST enumerate the COMPLETE set the executor will touch — not just headline source files. For every primary file added or modified, include matching test file(s), snapshot file(s), generated artifact(s), `.env.example` / `.env.local.example` if env shape changes, `package.json` + `pnpm-lock.yaml` (or equivalent) if dependencies change, and migration files matching the timestamp pattern. Under-enumeration causes the closeout's `phase_owned_dirty` check to fail closed (Pattern A from 2026-05-25 — hit ~70% of phases in that drive).
- **Interfaces provided** — symbols, types, endpoints, migrations this lane publishes.
- **Interfaces consumed** — symbols this lane depends on from other lanes.
- **Parallel-safe** — `yes` / `no` / `mixed` (with explanation if not `yes`).

Run the Lane validation checklist (below) before proceeding. If it fails, return to Step 3 with the failure noted.

### Step 5 — Task authoring (main thread)

For each lane, author an ordered task list:

- One **test** task (write failing tests for the lane's contracts).
- One or more **impl** tasks (each depends on the preceding test task).
- One **verify** task (runs the full test suite for the lane, plus any integration checks).

Tasks are identified `<SL-ID>.<N>`.

**Every phase must include a terminal `SL-docs` lane** after the impl/verify lanes. See `## Docs-sweep lane template` below. No opt-out — force a conscious doc decision every phase, even if the lane ends up recording "no cross-cutting changes needed."

### Step 6 — Emit per-lane tasks via TaskCreate

Plan-mode note: `TaskCreate` writes outside the scratch file and is blocked in plan mode. Author the task bodies in-thread during Step 5 so they are ready, but defer the actual `TaskCreate` invocations until AFTER `ExitPlanMode` approval (Step 8).

For each lane, emit one `TaskCreate`:

- **Title**: `<SL-ID> — <lane name>`
- **Body**: `Depends on: <upstream SL-IDs>`, `Blocks: <downstream SL-IDs>`, `Parallel-safe: <flag>`, and the ordered child task list (`test / impl / verify`).

This makes the lane DAG visible in the user's task pane and becomes the hand-off surface for `<harness>-execute-phase`.

### Step 7 — Write plan doc

Draft the plan in the plan-mode scratch file only. The scratch-file path is given in the plan-mode system reminder — do not guess it. Do NOT write to `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` yet; plan mode forbids writes outside the scratch file. The project-path copy + staging happens in the Close-out "Stage artifact" section after `ExitPlanMode` approval.

Then validate the scratch draft with
`validate_plan_dispatch_hints`. If it returns findings, stop without writing
the project-path artifact and emit the validation_failed closeout described in
`## Planner Literal Validation`. If `scripts/validate_plan_doc.py` exists
(shell test `[ -f scripts/validate_plan_doc.py ]`), run it against the scratch
file and fix any errors before `ExitPlanMode`:

```
python scripts/validate_plan_doc.py <scratch-file-path>
```

Otherwise walk the Lane validation checklist by hand and note manual verification in the post-approval closeout or Execution Notes. The validator checks required headings, disjoint file ownership, DAG acyclicity, grep-assertion-paired-with-tests, and eager-reexport risks.

### Step 7.75 — Advisor review

After the plan doc is drafted in the scratch file and before `ExitPlanMode`, call `advisor()`. Expect 1–4 contract-tightening suggestions per run, typically covering: under-specified freezes, asserted-but-unverified file paths, test-outline mechanism gaps, and spec-vs-contract conflicts. Apply the findings to the scratch draft before calling `ExitPlanMode`.

### Step 7.5 — External CLI review (only if `--review-external`)

Run the shared review script:

```bash
python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/review_with_cli.py" \
  --artifact plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md \
  --prompt-file "$(git rev-parse --show-toplevel)/.claude/skills/<harness>-plan-phase/assets/review_prompt.md" \
  --out plans/phase-plan-<VERSION>-<PHASE_ALIAS>_reviews.md
```

If the script reports the frontier-model cache is empty, it prints a discovery prompt to stderr. Surface to the user via `AskUserQuestion` with options `[run discovery now, skip review this run, abort]`.

Tell the user: "Review written to `plans/phase-plan-<VERSION>-<PHASE_ALIAS>_reviews.md`. When Gemini and Codex flag the same concern, treat it as real; divergent comments are context, not verdicts."

### Step 8 — ExitPlanMode

Call `ExitPlanMode`. The plan doc is the approval surface. After approval, execute the deferred actions in this order: Close-out "Stage artifact" (writes the project-path `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` and stages it unless forbidden), then Step 6 `TaskCreate` invocations, then Close-out "Reflection + Handoff".

## Plan document template

Use this frontmatter and these headings verbatim — the phase-loop runner trusts
the frontmatter before it trusts the plan body:

```markdown
---
phase_loop_plan_version: 1
phase: <PHASE_ALIAS>
roadmap: <repo-relative roadmap path, e.g. specs/phase-plans-v1.md>
roadmap_sha256: <sha256 of that roadmap file>
---

# <PHASE_ID>: <Phase Name>

## Context
<Synthesized from Explore teammates. What exists, what constrains the design, what will change.>

## Interface Freeze Gates
- [ ] IF-0-<PHASE>-<N> — <one-line description of the frozen interface>
- [ ] IF-0-<PHASE>-<N+1> — …

## Cross-Repo Gates
<Omit entirely if the phase only touches this repo.>
- [ ] IF-XR-<N> — <interface that must be frozen across repo boundaries>

## Lane Index & Dependencies

SL-1 — <lane name>
  Depends on: (none)
  Blocks: SL-3, SL-4
  Parallel-safe: yes

SL-2 — <lane name>
  Depends on: (none)
  Blocks: SL-4
  Parallel-safe: yes

## Lanes

### SL-1 — <lane name>
- **Scope**: <one sentence>
- **Owned files**: `path/one/**`, `path/two/*.ts` (MUST be a single-line inline bullet, comma-separated backticked globs; do NOT use nested sub-bullets — the downstream file-touch auditor expects inline form)
- **Interfaces provided**: `FooContract`, `POST /api/bar`
- **Interfaces consumed**: (none)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `path/one/__tests__/foo.test.ts` | `FooContract` shape | `pnpm test path/one/__tests__/foo.test.ts` |
| SL-1.2 | impl | SL-1.1 | `path/one/foo.ts` | — | — |
| SL-1.3 | verify | SL-1.2 | `path/one/**` | all SL-1 tests | `pnpm test path/one` |

### SL-2 — <lane name>
…

### SL-docs — Documentation & spec reconciliation

(See `## Docs-sweep lane template` earlier in this skill for the full lane spec. Copy it verbatim and set `Depends on:` to list every other SL-N in this phase.)

## Execution Notes
- <Parallelism caveats, sequencing gotchas, lanes that can't be worktree-isolated (shared migrations, shared generated files), etc.>
- **Single-writer files**: <files multiple lanes might want to touch but only one is allowed to modify — e.g., barrel index files, generated types, nav config, worker router. List the owner lane for each. If a single-writer file is also touched by a later phase, name this phase's owner lane and have them author-at-plan-time any additions the later phase's consumer lanes will need. Re-opening the file from the later phase's lane adds a cross-phase serialization edge that shouldn't exist.>
- **Known destructive changes**: <any deletions a lane legitimately performs, named by file path. If empty, write "none — every lane is purely additive." This is the whitelist <harness>-execute-phase's pre-merge check uses to distinguish legitimate deletions from stale-base accidents.>
- **Expected add/add conflicts**: <if SL-0 preamble stubs a file that a later lane replaces the body of, list the file path here. The orchestrator pre-authorizes `git checkout --theirs <path>` resolution at merge time.>
- **SL-0 re-exports**: <if the preamble adds symbols to an `__init__.py`, specify the `__getattr__` lazy pattern (not top-level imports). Eager re-exports break package load when a later lane drops or renames the symbol.>
- **Worktree naming**: <harness>-execute-phase allocates unique worktree names via `scripts/allocate_worktree_name.sh`. Plan doc does not need to spell out lane worktree paths.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated worktrees do not see sibling-lane merges automatically. If a lane finds its worktree base is pre-<first upstream dependency's merge>, it MUST stop and report rather than committing — the orchestrator will re-spawn or rebase. Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy peer-lane work on `--no-ff` merge.
- (If `--consensus` was used) **Architectural choices**: <consensus summary, or unresolved disagreement with dissent recorded>

## Acceptance Criteria
- [ ] <Testable assertion 1 drawn from the spec phase's Exit criteria>
- [ ] <Testable assertion 2>

## Verification
<Concrete end-to-end commands to run after all lanes merge. pnpm, supabase, curl, playwright, etc.>
```

## ID conventions

| ID | Format | Example |
|---|---|---|
| `PHASE_ID` | Spec identifier, else `PHASE-<kebab>` | `PHASE-1-shared-semantics` |
| Lane ID | `SL-<N>` | `SL-3` |
| Task ID | `<LANE_ID>.<N>` | `SL-3.2` |
| Interface freeze | `IF-0-<PHASE>-<N>` | `IF-0-P1-1` |
| Cross-repo freeze | `IF-XR-<N>` | `IF-XR-2` |

Defaults only — if the spec already uses its own identifiers (e.g., `P1-SL-AUTH-01`), adopt those verbatim.

Any non-numeric lane alias (e.g., `SL-docs`) must still appear in the machine-readable `## Lane Index & Dependencies` block as `SL-<N>` for compatibility with downstream audit/validator tooling that expects `SL-\d+`. Its `Depends on:` line must be on its own line (not inlined with prose that could regex-match as a dependency). The alias (e.g., `SL-docs`) remains valid as the author-facing lane heading in `## Lanes`.

## Task types & dependency rules

| Type | Purpose | Rules |
|---|---|---|
| `test` | Write failing tests that pin down the lane's contracts. | Must precede any `impl` task in the same lane. |
| `impl` | Write the code that makes the preceding tests pass. | Must depend on exactly one `test` task in the same lane. |
| `verify` | Run the full lane test suite + any integration checks. | Last task in the lane. Depends on the last `impl` task. |
| `docs` | Update cross-cutting documentation and the docs catalog. | Lives in the terminal `SL-docs` lane. Depends on every other lane's final `verify` task. |

## Docs-sweep lane template

Every phase plan must include this as the final lane. Copy verbatim into the `## Lanes` section, adjust `Depends on:` to list every other `SL-N` in the phase, and edit the `Scope notes` if the phase has atypical docs impact.

```markdown
### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation touched or invalidated by this phase's impl lanes, and append any post-execution amendments to phase specs whose interface freezes turned out wrong.
- **Owned files** (read `.claude/docs-catalog.json` for the authoritative list; a minimum set is below, but the catalog is canonical):
  - Root: `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `MIGRATION.md`, `ARCHITECTURE.md`, `DESIGN.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`
  - Agent indexes: `llm.txt`, `llms.txt`, `llms-full.txt`
  - Service manifests: `services.json`, `openapi.yaml`/`.yml`/`.json`
  - `docs/**`, `rfcs/**`, `adrs/**`
  - `.claude/docs-catalog.json` (this lane maintains it)
  - The current phase's section of `specs/phase-plans-v<N>.md` (append-only amendments)
  - Any prior `plans/phase-plan-v<N>-<alias>.md` or prior spec phase sections whose contracts this phase invalidated (prior-phase amendments allowed)
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: every other `SL-N` in this phase

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan: `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan`. Picks up any new doc files created by impl lanes; preserves `touched_by_phases` history. |
| SL-docs.2 | docs | SL-docs.1 | per catalog | For each file in the catalog, decide: does this phase's work change it? If yes, update the file and append the current phase alias to its `touched_by_phases`. If no, leave it. Record in commit message any files intentionally skipped. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v<N>.md`, prior plans | Append `### Post-execution amendments` subsections to any phase section (current or prior) whose interface freeze was empirically wrong this run. Named freeze IDs + dated correction. |
| SL-docs.4 | verify | SL-docs.3 | — | Run any repo doc linters (`markdownlint`, `vale`, `prettier --check`, Mermaid/PlantUML render check). If none configured, no-op. |
```

No opt-out. A phase with nothing to change still runs `SL-docs` and records that explicitly in its commit message — the audit trail.

## Consensus mechanism (synthesis rule)

Applied by the main thread after `--consensus` Step 3a:

1. **Unanimous** across all teammates → accept directly.
2. **Majority (2 of 3)** → accept the majority view; record the dissenting view under `## Execution Notes > Architectural choices > Dissent`.
3. **No majority** → re-address the same named teammates via `SendMessage` with the specific conflict surfaced. Max 1 additional round.
4. **Still no convergence** → main thread picks (biased toward `arch-parallel` for this skill's purpose) and records the full disagreement under `## Execution Notes > Unresolved architectural disagreements`.

## Lane validation checklist

Before writing the plan doc, verify:

- [ ] **Disjoint file ownership** — no two lanes' `Owned files` globs intersect. For generated files, call out shared-generated status in Execution Notes.
- [ ] **Owned files is inline, one line** — no nested bullets; comma-separated backticked concrete paths/globs only. Do not use prose entries such as "callsite wiring", "new migration file", "or equivalent", or "additions to"; resolve those to actual files before writing the plan.
- [ ] **DAG has no cycles** — a topological sort of `Depends on:` succeeds.
- [ ] **Every `impl` task has a preceding `test` task** in the same lane.
- [ ] **Every acceptance criterion is a testable assertion**, not prose. "Users can log in" is not testable; "`POST /api/auth` returns 200 with a valid session cookie for a registered user" is. `validate_plan_doc.py` WARNs (check K) on a criterion that names no command/path/assertion.
- [ ] **Each acceptance criterion names the command that proves it.** The definition of done (canonical term: `acceptance_criteria`) and the `## Verification` commands are one contract, not two parallel lists — cite the proving command (or test file) in or beside each `- [ ]` item so done is mechanically checkable.
- [ ] **Grep assertions are paired with tests.** Any acceptance criterion using `rg` or `grep` as its sole check must also cite a test file — grep alone is defeated by renaming a symbol to pass the regex.
- [ ] **UI changes get a visual check.** When any lane owns UI/visual files (`*.tsx`/`*.jsx`/`*.vue`/`*.svelte`, `*.css`/`*.scss`, `components/**`), `## Verification` must include a browser/screenshot step (Playwright-via-PMCP or claude-in-chrome) and at least one acceptance criterion phrased as a visually observable outcome. `validate_plan_doc.py` WARNs (check L) when UI files change but Verification names no browser step.
- [ ] **Interface freeze gates are concrete** — name the symbol/endpoint/migration, not a vibe.
- [ ] **Stale-base resilience** — for each lane that isn't a DAG root, list every upstream symbol, migration number, or file path it reads under `Interfaces consumed`. This gives `<harness>-execute-phase` evidence to verify the base wasn't stale and narrows the blast radius of a mis-based commit. Execution Notes must call out "if lane teammate finds its worktree base is pre-<upstream-SL>, stop and report — do not rebase silently."
- [ ] **Synthesis lanes are explicit reducers** — any lane that writes a docs summary, truth table, readiness matrix, release summary, or other synthesized artifact lists every producer lane under `Depends on` and every consumed finding under `Interfaces consumed`. Mark these lanes `Parallel-safe: no`.
- [ ] **No completion-order assumptions** — the plan never relies on lane numbering, prose ordering, or "last lane" wording to sequence final artifact writes; the DAG is the only sequencing mechanism.
- [ ] **Cross-lane file deletions called out** — if any lane legitimately deletes a file that another lane produces (rare but real: a lane replacing a stub), record it under Execution Notes' "Known destructive changes" block.
- [ ] **Expected add/add conflicts declared** — if SL-0 preamble stubs a file that a lane replaces, add it under Execution Notes' "Expected add/add conflicts" block.
- [ ] **SL-0 re-exports use `__getattr__` lazy form** — declared under Execution Notes' "SL-0 re-exports" block.
- [ ] **Plan doc passes `validate_plan_doc.py`** — run the validator and confirm zero errors before calling `ExitPlanMode`. The validator catches structural issues (missing headings, duplicate lane IDs, malformed task tables) that manual review misses.
- [ ] **Terminal `SL-docs` lane present** — every phase plan must include the docs-sweep lane from `## Docs-sweep lane template`. `Depends on:` lists every other lane in the phase. No opt-out; a phase with no doc changes still runs the lane and records that.

## Teamwork & delegation posture

- **Main thread = orchestrator only.** Brief, synthesize, write, emit. Do not `Grep`/`Read` the codebase directly during Steps 2–5. If you find yourself doing so, the teammate's brief was incomplete — re-brief via `SendMessage`.
- **Parallel-by-default.** Step 2 (Explore) and Step 3a (consensus Plan) MUST be issued as a single message with multiple `Agent` tool calls.
- **Name every teammate.** Set `name:` on every `Agent` call so you can re-address via `SendMessage` without losing context or paying to restart.
- **Task list as source of truth for the lane DAG.** Step 6's per-lane `TaskCreate` is how the plan becomes actionable; each lane task is addressable by ID for `<harness>-execute-phase`.
- **Hand-off to `<harness>-execute-phase`.** After `ExitPlanMode` approval, invoke `/<harness>-execute-phase <plan-doc-path>`. See that skill for the full execution contract (team creation, worktree isolation, merge policy). Do NOT pass `isolation: "worktree"` alongside `team_name` — the harness drops `isolation` in that combination.
- **Manual hand-off (when `<harness>-execute-phase` is unavailable).** Run `python scripts/validate_plan_doc.py <plan-doc-path>` first. Then execute each lane in one of two ways:
  - (a) **Standalone** — `Agent(isolation: "worktree", name: "<SL-ID>", subagent_type: "general-purpose")` without `team_name`. The `isolation` kwarg is honored in this form; loses team coordination.
  - (b) **Teamed** — `TeamCreate` + `Agent(team_name=…, name="<SL-ID>", subagent_type="general-purpose")`, and the teammate's first tool call is `EnterWorktree` (load via `ToolSearch(query="select:EnterWorktree")`). Worktree via tool, team coordination preserved.

## Output contract

After `ExitPlanMode` approval, three artifacts exist:

1. `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` — committable, valid markdown, all headings present.
2. The plan-mode scratch file — identical contents.
3. One `TaskCreate`'d top-level task per lane, each with `test / impl / verify` children, containing `Depends on:` / `Blocks:` / `Parallel-safe:` metadata in the body.

Those three are the full hand-off surface — everything downstream (manual lane execution or `<harness>-execute-phase`) reads from them.

## Close-out — Stage artifact (preservation guarantee)

After `ExitPlanMode` is approved, before exiting:

1. Verify frontmatter before staging:
   - `phase_loop_plan_version: 1`
   - `phase: <PHASE_ALIAS>`
   - `roadmap: <repo-relative roadmap path from repo root>`
   - `roadmap_sha256: <sha256 of that roadmap file>`
2. Run `validate_plan_dispatch_hints` before copying or staging the project-path
   artifact; stop with the validation_failed closeout if it returns findings.
3. Run `python scripts/validate_plan_doc.py plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` when the script is present; fix any frontmatter/path/hash failures before continuing.
4. Run `git status --short -- plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` and include the `_reviews.md` sibling if `--review-external` produced one.
5. If the plan or review artifact is untracked or modified and the user did not explicitly forbid staging, run `git add plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` plus the review sibling if present.
6. Rerun `git status --short -- plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` and report `Artifact state: staged|tracked|modified|unstaged|blocked`.
7. Do not commit unless the user explicitly asked for a commit.

When the generated plan is ready to execute, set `Next phase: <PHASE_ALIAS> - execution ready` and `Next command: /<harness>-execute-phase <PHASE_ALIAS>`. If execution should not start yet, set `Next phase: <PHASE_ALIAS> - blocked: <reason>` and `Next command: none - <reason>`.

## Close-out — Reflection + Handoff

After artifacts are staged or confirmed tracked, resolve paths. Treat `_shared/next_reflection_path.py` as optional-if-present: check existence, use it when available, otherwise fall back to an inline date-based filename.

```bash
REFLECTION_PATH=resolve_skill_bundle_root("codex")/<harness>-plan-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
REPO_LOCAL_HANDOFF=$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-plan-phase"))
PYH
)
mkdir -p "$(dirname "$REPO_LOCAL_HANDOFF")"
SKILL_MD=resolve_skill_bundle_root("codex")/<harness>-plan-phase/SKILL.md
```

Primary path: the orchestrator writes BOTH files directly with the Write tool. Before writing either file, ensure the plan doc has been staged in the preceding Close-out "Stage artifact" section unless the user explicitly forbade staging.

FILE 1 — REPO-AGNOSTIC reflection → `<REFLECTION_PATH>`:

```markdown
# <harness>-plan-phase reflection — <ISO timestamp>

## Run context
- Skill: <harness>-plan-phase
- Timestamp: <ISO timestamp>
- Repo: <repo>
- Branch: <branch>
- Commit: <commit>
- Artifact: <artifact path if any, or none>

## What worked
- <bullet, about the SKILL's instructions>

## What didn't
- <bullet, about friction or gaps in the SKILL's instructions>

## Improvements to SKILL.md
- <specific, actionable change to the instructions>
```

Do NOT reference this project, codebase, filenames, or domain in FILE 1. Feedback is about how the skill's instructions performed, for a future meta-skill that digests reflections across runs.

FILE 2 — REPO-SPECIFIC handoff → `<REPO_LOCAL_HANDOFF>` (per-repo slot; overwrites any prior handoff from this skill in the same repo):

```markdown
<!--
  Consumer validation — before acting on this handoff:
  1. Verify `from:` matches the expected predecessor skill.
  2. Verify `timestamp:` is within the last 7 days.
  3. Verify every `artifact:` path resolves under your current
     `$(git rev-parse --show-toplevel)`. If any path points to a
     different repo, stop and surface it to the user — the handoff
     was written against a different workspace.
-->
---
from: <harness>-plan-phase
timestamp: <ISO>
artifact: <absolute path to plan doc + reviews if any>
artifact_state: <staged|tracked|modified|unstaged|blocked>
next_skill: <<harness>-execute-phase|none>
next_command: </<harness>-execute-phase PHASE_ALIAS|none - reason>
next_phase: <PHASE_ALIAS - execution ready|PHASE_ALIAS - blocked: reason>
---

# Handoff for <harness>-execute-phase

## Summary
<2-3 sentences: phase planned, lanes count, plan doc path.>

## Key decisions made this run
- <numbered, one line each — lane boundaries, IF-freeze signatures, consensus outcomes if --consensus was used>

## Open items for <harness>-execute-phase
- <concrete — e.g., "SL-2 depends on SL-1's StoreRegistry.get signature; ensure lane ordering in dispatch">

## Repo-specific gotchas surfaced
- <quirks of THIS codebase discovered during planning>

## Planning artifacts staged this run
- <path> @ <artifact_state>

## Execute-phase's likely scope
- <file globs from Owned files across lanes>
```

Optional alternative: when fresh-context independent review is desired, the orchestrator MAY instead spawn ONE close-out agent using the `frontier` tier with the prompt below. Use this only when the orchestrator wants a clean-context review of the transcript before writing.

```
Agent(
  subagent_type: "general-purpose",
  model: "<frontier-model-id>",
  name: "<harness>-plan-phase-closeout",
  prompt: """
    Review the skill at <SKILL_MD> and the current execution transcript.
    Produce the two files above (same schemas) via the Write tool to
    <REFLECTION_PATH> and <REPO_LOCAL_HANDOFF>.
  """
)
```

After the files are written, print to the user:

> Plan written to `<plan-doc-path>`.
> Reflection saved to `<REFLECTION_PATH>`.
> Handoff written to `<REPO_LOCAL_HANDOFF>`.
>
> Recommended next step: run `/clear` to reset your context window, then invoke `/<harness>-execute-phase <alias>`. The next skill reads the handoff automatically.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.



## Spec Closeout Plan

Generated phase plans must include a machine-readable section:

```markdown
## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `<one of no_spec_delta, roadmap_amendment, canonical_spec_update, governed_pipeline_refresh, mirror_cutover_required, dotfiles_skill_source_update, human_source_judgment_required>`
- target surfaces: `<repo-relative paths or globs>`
- evidence paths: `<repo-relative metadata-only artifacts>`
- redaction posture: `metadata_only`
- downstream handling: `<none, roadmap amendment, Governed Pipeline refresh, mirror cutover, or human source judgment>`
```

Validate that the decision literal is in vocabulary, `target surfaces` and `evidence paths` are present, and `redaction posture` is `metadata_only`. Missing, malformed, or out-of-vocabulary spec-closeout sections are repairable `contract_bug` blockers unless the plan explicitly requires `human_source_judgment_required`. This section must preserve the allowed `## Execution Policy` selector vocabulary and must not invent new Dispatch Hints selectors. Reducer and verification lanes keep the existing `work-unit=phase_reducer` and `work-unit=phase_verify` policy literals.

## Verification Contract

Generated plans must contain machine-checkable verification commands and an effective `automation.suite_command`; phase plans must validate those commands through IF-0-VC-2 before they are handoff-ready. If an acceptance item depends on operational evidence that cannot be machine-checked directly, the plan must name the operational evidence artifact and the runner-stamped amendment mechanism that records it. proxy evidence requires a roadmap amendment before downstream plans rely on it.

## Closeout

### Manifest write

After the plan artifact and repo-local handoff path are known, perform a best-effort `plan-manifest append` through `phase_loop_runtime.plan_manifest.append_entry`. Append a `type=phase` entry with `status=committed`, `slug`, `file`, `created_at`, `owner_skill=<harness>-plan-phase`, `handoff_ref`, `roadmap_ref`, `phase_alias`, `if_gates_produced`, and `lanes`. Resolve paths with `phase_loop_runtime.skill_paths` helpers and keep the manifest write best-effort during the dual-mode window: failures are non-fatal, emit a ledger warning, and are mentioned in the mandatory reflection without changing the existing plan closeout result. A legacy `plans/manifest.json` using the `schema`/`entries` layout raises `unsupported manifest schema_version: 0`; treat that exact error as the expected non-fatal case (warn, do not block or migrate in place). `phase_loop_runtime` may resolve from the installed dotfiles runtime on `PYTHONPATH` even when the target repo neither vendors nor installs it — use the importable package; a missing repo-local checkout is not a blocker.

Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-plan-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.
