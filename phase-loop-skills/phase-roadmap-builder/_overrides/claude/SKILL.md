---
name: phase-roadmap-builder
description: "Harness Code roadmap planner. Produces or extends a multi-phase roadmap spec that <harness>-plan-phase can ingest, with phase dependencies and interface-freeze gates."
---

# <harness>-phase-roadmap-builder

## Runtime State

For reflections, handoffs, and latest handoff pointers, follow `<harness>-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

`shared/phase-loop/protocol.md` is the canonical shared contract for
`automation:`, `next_skill`, `next_command`, `verification_status`, and
downstream roadmap-amendment semantics. Harness-specific closeout text must
agree with that shared protocol.

When this skill runs through the phase-loop Harness adapter, the repo-owned
injected plugin bundle is authoritative. That bundle carries the full Harness
workflow pack (`<harness>-phase-roadmap-builder`, `<harness>-plan-phase`,
`<harness>-execute-phase`, and `<harness>-phase-loop`) even if installed bridge
skills under `resolve_skill_bundle_root("claude")/` are missing or drifted.

Produces the `specs/phase-plans-v<N>.md` roadmap that `/<harness>-plan-phase` consumes. Digests the prior conversation and any markdown files the user points at, then writes a phased roadmap whose structure maximizes parallel execution. Creates a new roadmap if none exists; appends phases (never edits existing phases) if one does.

## When to use

- User wants to formalize a conversation's plan into a phased roadmap.
- User points at a markdown spec and asks to "phase it."
- User wants to add phases to an existing `specs/phase-plans-v*.md`.
- User wants the output to be ingestible by `/<harness>-plan-phase`.

## When NOT to use

- Single-phase or single-concern change → use `/<harness>-plan-detailed`.
- Lane-level planning for one phase → the user wants `/<harness>-plan-phase`, not this skill.
- Pure research / "how does X work" → use `Agent(subagent_type: "Explore")` directly.

## Inputs

| Arg | Required | Meaning |
|---|---|---|
| `<spec-path>` | no | Path to an input markdown spec to fold into the roadmap. Optional; conversation context is the primary source. |
| `--output <path>` | no | Override the default output path. Default: `specs/phase-plans-v<N>.md` at the repo root. |
| `--append` | no | Force append mode even if the resolved output path is new (rare). |
| `--review-external` | no | After writing the roadmap, run Gemini + Codex CLIs in parallel to review it. Requires `gemini` and `codex` installed and authenticated. Produces a `_reviews.md` sibling file. |

## Prerequisites

Expected helpers under `.claude/skills/_shared/` (check existence before invocation; fall back inline when absent):

- `_shared/scaffold_docs_catalog.py` — scaffolds the docs catalog. On absence, create `.claude/docs-catalog.json` manually as an empty-array JSON file (`[]`).
- `_shared/next_reflection_path.py` — legacy helper only; current closeout writes reflections to `resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/reflections/<repo_hash>/<branch_slug>/<run_id>.md`.
- `_shared/review_with_cli.py` — used only with `--review-external`; required for that flag.

## Mode detection

Resolve the output path in this order:
1. `--output` override.
2. Glob `specs/phase-plans-v*.md`; pick the highest version.
3. If none found, default to `specs/phase-plans-v1.md` (create mode).

If the resolved path exists → **append mode**. Otherwise → **create mode**.

## Deferred tool preloading

Load tools used later in a single query:

```
ToolSearch(query: "select:AskUserQuestion,ExitPlanMode")
```

## Workflow — create mode

### Step 0 — Read predecessor handoff (if present)

Handoffs are keyed on the current repo so each workspace has its own slot. Resolve the predecessor path first:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  REPO_KEY="_no-git-$(pwd | sha1sum | cut -c1-12)"
else
  REPO_KEY="$(basename "$REPO_ROOT")-$(printf '%s' "$REPO_ROOT" | sha1sum | cut -c1-12)"
fi
PREDECESSOR_HANDOFF="$(python3 - <<'PYH'
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

If `$PREDECESSOR_HANDOFF` exists, `Read` it in full — a prior `/<harness>-execute-phase` run may have left context useful for the next roadmap (completed phases, decisions made, gotchas discovered mid-execution). Treat its contents as additional input to Step 1.

Defense-in-depth checks (the repo-key scheme prevents the common cross-project case, but these still catch symlink-shared workspaces, manual file copies, and stale handoffs):

- If the handoff's `from:` field is not `<harness>-execute-phase` or its timestamp is older than 7 days, flag via `AskUserQuestion` with `[use anyway, ignore, abort]`.
- Parse the handoff's `artifact:` field and verify at least one referenced path resolves under `$(git rev-parse --show-toplevel)`. On mismatch, surface `AskUserQuestion` with `[use anyway, ignore, abort]`.

If absent, proceed — this run is standalone.

### Step 1 — Gather inputs

Sources (permissive but bounded):

1. The current conversation's user turns, assistant plans, and any committed architectural decisions.
2. The spec path argument, if supplied — `Read` it in full.
3. `CLAUDE.md` and `AGENTS.md` at the repo root, if present.
4. Markdown files the user has explicitly named anywhere in the conversation.

Do not grep the repo for additional material. If context is insufficient, stop and ask the user via `AskUserQuestion` — a thin roadmap is worse than one more question.

### Step 1a — Ensure docs catalog exists

Check for the helper before invoking:

```bash
HELPER="$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py"
if [ -f "$HELPER" ]; then
  python3 "$HELPER"
else
  CATALOG="$(git rev-parse --show-toplevel)/.claude/docs-catalog.json"
  [ -f "$CATALOG" ] || echo '[]' > "$CATALOG"
fi
```

Scaffolds `.claude/docs-catalog.json` if absent; no-op if already present. On helper absence, seed the catalog manually as an empty-array JSON file. The catalog drives the `SL-docs` lane that `<harness>-plan-phase` constructs for every phase. Existence matters more than content at this stage — the catalog will be refreshed (`--rescan`) by each `SL-docs` lane as the roadmap executes.

### Step 2 — Synthesize top-of-document sections

Fill these sections before touching phase decomposition:

- **Context** — one-page synthesis: the problem, current state, the thesis of the refactor/build.
- **Architecture North Star** — ASCII diagram of the target architecture if the work is structural. Omit if not load-bearing.
- **Assumptions (fail-loud if wrong)** — numbered list of preconditions; each is something that, if false, invalidates the plan.
- **Non-Goals** — explicit deferrals. Use to cut scope; silence here causes scope creep later.
- **Cross-Cutting Principles** — rules that apply across every phase (e.g., "single-writer-per-SQLite-file", "no mocks in integration tests").

### Step 3 — Decompose into phases (parallelization-maximizing)

Apply the heuristics in `references/parallelization-heuristics.md` in order:

1. **Phases are serial checkpoints; lanes within a phase are parallel.** Minimize total wall time by minimizing phase count and maximizing lanes per phase.
2. **A phase boundary exists only at a necessary interface freeze.** If two bodies of work share no freeze dependency and have disjoint files, they are sibling lanes inside one phase, not sequential phases.
3. **Prefer narrow, early freezes.** Each phase's `Produces:` lists the tightest contract that unblocks the next phase.
4. **Split fat phases into parallel sibling phases** when they contain independent subtrees (the `P6A || P6B` pattern).
5. **Every phase decomposes into ≥2 lanes** unless it is a preamble / interface-freeze-only phase (Phase 1 / P0 are the common exceptions). Single-lane phases are a code smell — merge or split.
6. **Each phase's `Scope notes` lists explicit parallelism hints**: suggested lane count, candidate disjoint-file partitions, and any single-writer files that would serialize lanes if ignored.
7. **Identify cross-phase parallelism.** Phases with no shared ancestor in the DAG can run concurrently. Call these out in the DAG.
8. **Day-1 intra-phase freeze publication.** When one lane's output shape can be published before its implementation lands, declare the shape as an intra-phase freeze so sibling lanes start against the contract rather than waiting.
9. **Soft upper bound on lanes per phase.** When a phase would contain more than 6–8 lanes AND the lanes partition into independent subtrees sharing no intra-phase freeze, split into sibling phases. When the lanes share intra-phase freezes, keep them in one phase regardless of count.

### Step 4 — Draft Top Interface-Freeze Gates

For each inter-phase freeze, write one `IF-0-<ALIAS>-<N>` entry naming the concrete symbol, schema, or endpoint being frozen. Narrow is better — a gate that names "a dataclass with fields `{a, b, c}`" lets downstream lanes start; "the data model" does not.

### Step 5 — Write each phase section

Use the skeleton from `references/roadmap-template.md`. Each phase MUST contain:

- `**Objective**` — one or two sentences.
- `**Exit criteria**` — checkbox list (`- [ ] …`), each item testable.
- `**Scope notes**` — lane decomposition hints, parallelism advice, edge cases.
- `**Non-goals**` — explicitly deferred.
- `**Key files**` — paths the phase touches.
- `**Depends on**` — upstream phase aliases, or `(none)` for roots. Root phases use the exact form `- (none)` with no trailing prose; put explanatory notes on a following bullet or in `Scope notes`.
- `**Produces**` — `IF-0-<ALIAS>-<N>` entries for freezes this phase publishes.

Phase heading format: `### Phase N — <Name> (<ALIAS>)`. Alias is short, mnemonic, alphanumeric (`P1`, `P2A`, `P2B`, `P3`, …). It becomes the filename token in `phase-plan-v<N>-<alias>.md`.

Runner compatibility hard requirement: every `### Phase ...` heading MUST include `(<ALIAS>)`, even when `--output` writes an alternate path such as `planning/**/phase-roadmap.md` instead of `specs/phase-plans-v<N>.md`. Never write prose-only headings like `### Phase 1 — Foundation`; `phase-loop` and `<harness>-plan-phase` use the alias as the stable phase identifier.

Before writing the artifact, perform an explicit alias-assignment pass:

1. List every runnable phase heading you are about to emit.
2. Assign one unique uppercase alias per phase.
3. Rewrite each heading into the exact `### Phase N — <Name> (<ALIAS>)` form.
4. Ensure the `## Phase Dependency DAG`, `**Depends on**`, `**Produces**`, and `## Execution Notes` sections refer to phases by those aliases, not by prose names or workstream IDs.

Do not substitute `## Phase Decomposition`, `## Workstreams`, or prose "Done when" sections for the canonical `## Phases` structure. Narrative context may exist above the phase list, but runnable roadmap content must be under `## Phases` with aliased phase headings.

### Step 6 — Build Phase Dependency DAG

Render as ASCII. Show serial arrows (`P1 → P2A → P2B`) and parallel branches (`P6A  parallel after P1`). This is the source of truth for the wall-clock schedule.

### Step 7 — Write Execution Notes and Verification

- **Execution Notes**: how to invoke `/<harness>-plan-phase <ALIAS>` for each phase, and `/<harness>-execute-phase <alias>` for each. Call out which phases can be planned and executed concurrently.
- **Verification**: end-to-end shell commands that prove the whole roadmap delivered its goals. These are the integration tests the user will run after the last phase merges.

### Step 8 — Validate

Run:

```bash
VALIDATOR=""
for base in \
  "$(git rev-parse --show-toplevel)/.claude/skills" \
  "$HOME/.claude/skills" \
  "${PHASE_LOOP_SKILL_BUNDLE:-}" \
  "${CLAUDE_SKILL_BUNDLE:-}"
do
  [ -n "$base" ] || continue
  CANDIDATE="$base/<harness>-phase-roadmap-builder/scripts/validate_roadmap.py"
  if [ -f "$CANDIDATE" ]; then
    VALIDATOR="$CANDIDATE"
    break
  fi
done
if [ -z "$VALIDATOR" ]; then
  echo "blocker: cannot locate <harness>-phase-roadmap-builder/scripts/validate_roadmap.py" >&2
  exit 1
fi
python3 "$VALIDATOR" <output-path>
```

Run the validator against the exact artifact path written by this invocation, including custom paths such as `planning/**/phase-roadmap.md`; do not validate a default `specs/phase-plans-v<N>.md` path unless that is the actual output. Fix every reported error before handing off. The validator enforces: required headings, phase-heading aliases, alias uniqueness, DAG acyclicity, IF-gate ID format, Depends-on correctness, and the ≥2-lanes-per-phase rule (with a preamble-phase escape hatch).

If validation cannot be run, stop and report that as a blocker. Do not hand off a roadmap whose phase aliases were checked only by eye.

### Step 8.5 — External CLI review (only if `--review-external`)

Requires `_shared/review_with_cli.py` (see Prerequisites). After the roadmap is written and validated, run:

```bash
python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/review_with_cli.py" \
  --artifact <output-path> \
  --prompt-file "$(git rev-parse --show-toplevel)/.claude/skills/<harness>-phase-roadmap-builder/assets/review_prompt.md" \
  --out <output-path>_reviews.md
```

If the frontier-model cache is empty, the script prints the discovery prompt to stderr. Surface to the user via `AskUserQuestion` with options `[run discovery now, skip review this run, abort]`.

Tell the user: "Review written to `<output-path>_reviews.md`. Agreements between Gemini and Codex are real signal; divergences are context."

### Step 9 — Write and exit

Write the roadmap to the resolved output path. Call `ExitPlanMode` — the roadmap is the approval surface. After approval, the hand-off message is: "Run `/<harness>-plan-phase <ALIAS>` for each phase. Phases with no shared DAG ancestor (e.g., `<list them>`) can be planned concurrently."

## Workflow — append mode

### Step A1 — Read existing roadmap

`Read` the existing `specs/phase-plans-v*.md`. Extract:

- The highest `## Phase N` number.
- The full existing alias set.
- The highest `IF-0-<ALIAS>-<N>` sequence number per phase.
- The existing Phase Dependency DAG edges.

### Step A2 — Identify new phases from context

Apply Step 1 above to gather new context. Then apply Step 3's heuristics to decide which new phases are needed. Do not modify existing phases — additions only. If the new context implies that an existing phase is wrong, stop and tell the user; do not silently edit.

### Step A3 — Assign numbers and aliases

- Phase numbers continue strictly increasing (highest existing + 1, +2, …).
- New aliases must not collide with existing ones. Extend the convention (`P8`, `P9`, or `P4B` if it branches from an existing `P4`).

### Step A4 — Wire Depends on

For each new phase, list every existing phase it depends on by alias. A new phase that depends on nothing existing is either a new root (rare — possible when introducing a parallel initiative) or misidentified.

### Step A5 — Splice into the document

Insert new phase sections under `## Phases`, after the last existing phase, before `## Execution Notes`. Update two other sections:

- `## Phase Dependency DAG` — add the new nodes and edges.
- `## Top Interface-Freeze Gates` — append any new `IF-0-<ALIAS>-<N>` entries.

Leave `## Context`, `## Architecture North Star`, `## Assumptions`, `## Non-Goals`, `## Cross-Cutting Principles`, `## Execution Notes`, and `## Verification` alone unless the new phases invalidate a stated assumption — in which case surface the conflict to the user and stop.

### Step A6 — Validate + exit

Same as Step 8, Step 8.5 (if `--review-external`), and Step 9. Validator must pass.

## Output contract

The output file must match `references/roadmap-template.md` structurally. `<harness>-plan-phase` parses stable headings (`## Context`, `### Phase N — <Name> (<ALIAS>)`, `**Objective**`, `**Exit criteria**`, etc.) by regex; non-standard formatting breaks downstream.

## Hand-off

After approval, invoke `/<harness>-plan-phase <ALIAS>` per phase. Phases with no shared ancestor in the DAG can be planned and executed in parallel — note which ones in the hand-off message. The wall-clock critical path is the longest DAG path through the roadmap.

## Close-out — Stage artifact (preservation guarantee)

After `ExitPlanMode` is approved, before exiting:

1. Run `git status --short -- <output-path>` and include the `_reviews.md` sibling if `--review-external` produced one.
2. If the roadmap or review artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <output-path>` plus the review sibling if present.
3. Rerun `git status --short -- <output-path>` and report `Artifact state: staged|tracked|modified|unstaged|blocked`.
4. Do not commit unless the user explicitly asked for a commit.

Before final response and handoff, choose the next phase to plan from the roadmap DAG. If at least one phase is ready, set `Next phase: <ALIAS> - <phase name>` and `Next command: /<harness>-plan-phase <ALIAS>`. If no phase should be planned next, set `Next phase: none - <reason>` and `Next command: none - <reason>`.

## Close-out — Reflection + Handoff

Stage the roadmap/plan artifacts before writing either close-out file so the deliverable is preserved in the index unless the user explicitly forbade staging.

After artifacts are staged or confirmed tracked, resolve paths:

```bash
REFLECTION_PATH=resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/reflections/<repo_hash>/<branch_slug>/<run_id>.md
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
REPO_LOCAL_HANDOFF=$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-phase-roadmap-builder"))
PYH
)
mkdir -p "$(dirname "$REPO_LOCAL_HANDOFF")"
SKILL_MD=resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/SKILL.md
```

Write BOTH files directly via the Write tool — this is the primary path.

FILE 1 — REPO-AGNOSTIC reflection → write to `<REFLECTION_PATH>`

```
# <harness>-phase-roadmap-builder reflection — <ISO timestamp>

## Run context
- Skill: <harness>-phase-roadmap-builder
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

FILE 2 — REPO-SPECIFIC handoff → write to `<REPO_LOCAL_HANDOFF>` (per-repo slot; overwrites any prior handoff from this skill in the same repo)

```
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
from: <harness>-phase-roadmap-builder
timestamp: <ISO>
artifact: <absolute path(s) to roadmap spec + any reviews written>
artifact_state: <staged|tracked|modified|unstaged|blocked>
next_skill: <<harness>-plan-phase|none>
next_command: </<harness>-plan-phase ALIAS|none - reason>
next_phase: <ALIAS - phase name|none - reason>
---

# Handoff for the next skill

## Summary
<2-3 sentences: what was produced, where to find it.>

## Key decisions made this run
- <numbered, one line each>

## Open items for the next skill
- <concrete, actionable — e.g., "Lane SL-3 expects the
  plugins_for(repo_id) signature from IF-0-P3-1; verify it
  before planning P4">

## Repo-specific gotchas surfaced
- <things that surprised this run; quirks of THIS codebase>

## Planning artifacts staged this run
- <path> @ <artifact_state>

## Next skill's likely scope
- <best-effort forecast of which files/paths the next skill will touch>
```

Optionally delegate to a spawned frontier-tier Agent instead when independent review with a fresh context window is desired (e.g., after complex runs). Resolve the model via the `<harness>-execute-phase` Model tiers table:

```
Agent(
  subagent_type: "general-purpose",
  model: "<frontier-model-id>",
  name: "<harness>-phase-roadmap-builder-closeout",
  prompt: """
    Review the skill at <SKILL_MD> and the current execution transcript.
    Write FILE 1 to <REFLECTION_PATH> and FILE 2 to <REPO_LOCAL_HANDOFF> using
    the schemas defined in the SKILL's Close-out section.
  """
)
```

After the files are written, print to the user:

> Roadmap written to `<spec-path>`.
> Reflection saved to `<REFLECTION_PATH>`.
> Handoff written to `<REPO_LOCAL_HANDOFF>`.
>
> Recommended next step: run `/clear` to reset your context window, then invoke `/<harness>-plan-phase <ALIAS>`. The next skill reads the handoff automatically.

## Reference files

- `references/roadmap-template.md` — exact structural skeleton to emit.
- `references/parallelization-heuristics.md` — extended rules for edge cases in phase/lane decomposition.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.


## Verification Contract

Roadmap phases must set the expectation that downstream plans include machine-checkable verification commands and an effective `automation.suite_command`. If a phase depends on operational evidence that cannot be machine-checked directly, name the operational evidence artifact and the runner-stamped amendment mechanism that records it. proxy evidence requires a roadmap amendment before any downstream plan treats it as a gate verdict.

## Closeout

Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml`; keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.
