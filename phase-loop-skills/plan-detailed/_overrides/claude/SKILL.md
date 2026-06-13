---
name: plan-detailed
description: "Harness Code detailed planner for one bounded change. Researches the repo, writes an immediately implementable plan, documents verification, and records a handoff."
---

# <harness>-plan-detailed

## Runtime State

For reflections, handoffs, and latest handoff pointers, follow `<harness>-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

Standalone planner for one bounded change. Not part of the `<harness>-phase-roadmap-builder` → `<harness>-plan-phase` → `<harness>-execute-phase` loop. Used **by exception, outside the pipeline**, when a change is single-concern and the pipeline's roadmap/phase/lane overhead costs more than the change deserves.

## When to use

- The change is bounded — a bug fix, a small feature, a targeted refactor with obvious blast radius.
- One agent (or one developer) will carry the work end-to-end.
- Producing a full phase roadmap would be disproportionate.

## When NOT to use

- Work spans multiple concerns that would benefit from parallel execution → use `/<harness>-phase-roadmap-builder` (for the roadmap) then `/<harness>-plan-phase` (per phase).
- Pure research / "how does X work" → use `Agent(subagent_type: "Explore")` directly.
- Task is trivial and one-step ("rename this variable") → just do it; a plan doc is noise.

## Inputs

| Arg | Required | Meaning |
|---|---|---|
| `<task>` | no | Free-form task description. Falls back to prior conversation context if omitted. |
| `--output <path>` | no | Override the generated plan path. Default: `plans/detailed-<slug>-<YYYYMMDD-HHMM>.md`. |
| `--review-external` | no | Run Gemini + Codex review after writing the plan. Requires both CLIs installed and the frontier-model cache populated. |

## Deferred tool preloading

```
ToolSearch(query: "select:AskUserQuestion,ExitPlanMode")
```

## Workflow

### Step 1 — Extract the task + gather implicit context

Task source, in order: invocation args → preceding conversation → `AskUserQuestion` if still unclear. A thin plan is worse than one more question.

Implicit context:

```bash
git status --porcelain 2>/dev/null | head -20
git log --oneline -5 2>/dev/null
git rev-parse --show-toplevel 2>/dev/null || pwd
```

Also read `CLAUDE.md` / `AGENTS.md` at repo root if present.

Keep the full starting `git status --short --untracked-files=all` output in the run notes. Before closeout, compare it to the ending status; any pre-existing untracked file must be called out in the handoff as "not an artifact of this plan — do not commit as part of implementation."

### Step 2 — Parallel reconnaissance via Explore teammates

Launch up to 3 `Agent(subagent_type: "Explore")` calls in a single message (1–2 is usual for single-concern work). Each Agent call MUST set `name:` for `SendMessage` addressability.

Teammate-naming template: `explore-<area>` (e.g., `explore-auth`, `explore-schema`).

Apply the `/<harness>-task-contextualizer` checklist to every brief. Each must include:

- The task statement verbatim.
- 1–2 sentences of architecture context: how the relevant module fits the larger system.
- Specific file paths to start from, when known; otherwise a glob to search.
- A scoped question: "Map existing code in `<paths>`. Surface: (a) utilities/patterns to reuse, (b) types/schemas/contracts that constrain the design, (c) places that must change, (d) hidden coupling."
- A length cap: "Report in under 400 words."
- Expected output format.

Block until all return. Findings populate the plan's `## Research summary` section.

### Step 3 — Architect the plan

Synthesize into a concrete change list. Follow these rules rigorously — they're the core of what this skill exists to enforce.

**Research first.** Never propose a change without having located, in this research pass or prior context, the file and pattern it relates to.

**Explicit change enumeration.** Every change names: file path, entity (class/method/function/table/column/config/migration), action (add/modify/delete), reason (one clause).

**Modification over creation.** Prefer editing existing code. Only create new files/functions when separation of concerns demands it or the change has no existing home.

**Scope discipline.** No features beyond what was requested. No refactors of surrounding code unless broken or directly blocking. No speculative error handling, type annotations, or comments on unchanged code.

**Bounded-plan threshold.** If the plan would touch more than ~8 source files or require more than ~3 conceptually distinct changes, recommend splitting it into multiple bounded plans instead of producing one oversized detailed plan.

**line-number-fabrication guard.** do not fabricate line numbers. Cite `module.py:NNN` only when that exact line was read in this session or captured in the Explore transcript; otherwise hedge the citation with "around line NNN" or tell the implementer to search for the relevant symbol.

**Frozen vocabulary / protocol rule.** If the plan touches a file with a frozen vocabulary or protocol contract, quote the relevant protocol line range in the plan and explicitly confirm that no new vocabulary is introduced.

**Documentation impact enumerated inline.** Every cross-cutting doc (`README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`, `llm.txt`, `llms.txt`, `llms-full.txt`, `services.json`, `openapi.*`, `ARCHITECTURE.md`, `DESIGN.md`, `docs/**`, `rfcs/**`, `adrs/**`) that needs updating gets a bullet with file + entity + action + reason like any other change. If none applies, state `Documentation impact: none — internal refactor, no doc footprint.` Force a conscious decision every run.

**Dependencies & order.** Identify which changes must happen first. Name blocking external dependencies (migrations that must run before a column is read, type definitions others consume, etc.).

**Verification.** Concrete shell/test commands (`pnpm test path/to/foo`, `cargo check`, `psql -c '…'`, `curl …`), behaviors to observe, edge cases to check. No "manually verify that it works" items.

**Acceptance criteria.** 2–5 `- [ ]` items. Testable assertions, not prose. "Users can log in" fails. "`POST /api/auth` returns 200 with a valid session cookie for a registered user" passes.

### Step 4 — Write the plan doc

Derive `<slug>` from the task (kebab-case, 3–5 words: `add-refresh-token-endpoint`, `fix-stale-cache-eviction`). Default path: `plans/detailed-<slug>-<YYYYMMDD-HHMM>.md` at repo root. Override via `--output`.

Also write to the plan-mode scratch file (path in the plan-mode system reminder — do not guess).

Use the template in `## Plan document template` below verbatim.

If invoked outside Plan Mode, still produce the plan and handoff artifacts, but do not begin implementation. Include a one-line note to the user that Plan Mode was not active and the output is a planning artifact only.

### Step 5 — External CLI review (only if `--review-external`)

```bash
python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/review_with_cli.py" \
  --artifact <plan-path> \
  --prompt-file "$(git rev-parse --show-toplevel)/.claude/skills/<harness>-plan-detailed/assets/review_prompt.md" \
  --out <plan-path>_reviews.md
```

On stale/missing frontier-model cache, surface via `AskUserQuestion` with `[run discovery now, skip review this run, abort]`.

Tell the user: "Review written to `<path>_reviews.md`. Agreements between Gemini and Codex are real signal; divergences are context."

### Step 6 — ExitPlanMode

Plan doc is the approval surface.

### Step 7 — Close-out: Commit artifact (clean-tree guarantee)

After `ExitPlanMode` approval, before exiting:

```bash
git add plans/detailed-<slug>-<YYYYMMDD-HHMM>.md
# Plus the _reviews.md sibling if --review-external produced one.
git commit -m "chore(plan): detailed plan for <short task summary>"
```

`git status` must be clean. On dirty outside the skill's artifacts, surface via `AskUserQuestion` with `[commit as chore, stash, abort]`.

### Step 8 — Close-out: Reflection + Handoff

Write the plan first, then write the reflection and handoff serially. The handoff should reference the final plan path and may quote final decisions verbatim; do not parallelize handoff writing with the final plan-body write.

Resolve paths:

```bash
REFLECTION_PATH=resolve_skill_bundle_root("codex")/<harness>-plan-detailed/reflections/<repo_hash>/<branch_slug>/<run_id>.md
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
REPO_LOCAL_HANDOFF=$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-plan-detailed"))
PYH
)
mkdir -p "$(dirname "$REPO_LOCAL_HANDOFF")"
SKILL_MD=resolve_skill_bundle_root("codex")/<harness>-plan-detailed/SKILL.md
```

Spawn ONE close-out agent on the `frontier` tier. It writes both files via the Write tool:

```
Agent(
  subagent_type: "general-purpose",
  model: "<frontier-model-id>",
  name: "<harness>-plan-detailed-closeout",
  prompt: """
    Review the skill at <SKILL_MD> and the current execution transcript.
    Produce TWO files via the Write tool.

    FILE 1 — REPO-AGNOSTIC reflection → write to <REFLECTION_PATH>

      # <harness>-plan-detailed reflection — <ISO>

      ## Run context
      - Skill: <harness>-plan-detailed
      - Timestamp: <ISO timestamp>
      - Repo: <repo>
      - Branch: <branch>
      - Commit: <commit>
      - Artifact: <artifact path if any, or none>

      ## What worked
      - <bullet about the SKILL's instructions>

      ## What didn't
      - <bullet about friction or gaps in the SKILL's instructions>

      ## Improvements to SKILL.md
      - <specific, actionable change>

      Do NOT reference this project or the specific plan produced.

    FILE 2 — REPO-SPECIFIC handoff → write to <REPO_LOCAL_HANDOFF> (per-repo slot)

      <!--
        Consumer validation — before acting on this handoff:
        1. Verify `from:` is `<harness>-plan-detailed`.
        2. Verify `timestamp:` is within the last 7 days.
        3. Verify every `artifact:` path resolves under your current
           `$(git rev-parse --show-toplevel)`. If any path points to a
           different repo, stop and surface it to the user — the handoff
           was written against a different workspace.
      -->
      ---
      from: <harness>-plan-detailed
      timestamp: <ISO>
      artifact: <absolute path to plan doc + reviews if any>
      ---

      # Handoff for the implementer

      ## Summary
      <1–2 sentences: task, plan doc path, rough scope.>

      ## Key decisions made this run
      - <numbered, one line each>

      ## Open items for the implementer
      - <concrete, actionable — e.g., "confirm the refresh-token TTL
        matches the session policy in auth-config.ts">

      ## Repo-specific gotchas surfaced
      - <quirks discovered during research — patterns to match, files
         not to touch>

      ## Files the implementer will touch
      - <enumerated from the plan>
  """
)
```

Exit message to user:

> Plan written to `<plan-path>`.
> Reflection saved to `<REFLECTION_PATH>`.
> Handoff written to `<REPO_LOCAL_HANDOFF>`.
>
> Recommended next step: run `/clear` to reset your context window, then implement the plan. The implementing agent should verify the handoff's `from:` field, timestamp (<7 days), and `artifact:` paths against the current repo before acting.

## Consumer contract

`<harness>-plan-detailed` has no paired executor skill — the "implementer" is typically a fresh Harness Code session launched after `/clear`. The handoff is the only channel carrying pre-`/clear` context forward, so validation has to be self-serve. Every handoff this skill writes embeds the consumer-validation preamble (see the FILE 2 template above); a fresh implementer reading the file sees those instructions first and should apply them before acting on any downstream content:

1. **`from:` check** — must be `<harness>-plan-detailed`. A mismatch means the file belongs to a different skill and should not be consumed as a <harness>-plan-detailed handoff.
2. **Timestamp check** — must be within the last 7 days. Older handoffs are likely stale; the plan artifact may already be merged, abandoned, or superseded.
3. **`artifact:` containment check** — every path in the `artifact:` field must resolve under the current `$(git rev-parse --show-toplevel)`. A path pointing elsewhere means the handoff was written against a different workspace (repo-key collision on a symlink-shared volume, manual file copy, or stale entry from a prior invocation in a renamed/moved repo).

On any failure, stop and surface to the user — do not silently proceed. The per-repo handoff path already filters out most cross-project bleed, but these checks catch the residual cases the path scheme can't.

## Plan document template

Emit this structure verbatim.

```markdown
# Detailed plan: <one-line task summary>

## Task
<task statement — from args or conversation synthesis>

## Research summary
<2–5 sentences synthesized from Explore teammates. Cite the files,
utilities, and patterns worth reusing.>

## Changes

### `<file-path>` (<create|modify|delete>)
- `<entity>` — <add|modify|delete> — <reason>
- `<entity>` — <add|modify|delete> — <reason>

### `<file-path>` (<create|modify|delete>)
…

## Documentation impact
- `<doc-path>` — <add|modify> — <reason>
- …

(If no docs need changes: `None — internal refactor, no doc footprint.`)

## Dependencies & order
<Which changes must happen first. Name blocking relationships.>

## Verification
<Concrete shell/test commands, behaviors, edge cases. Runnable.>

## Acceptance criteria
- [ ] <testable assertion>
- [ ] <testable assertion>
```

## Teamwork posture

- **Main thread = orchestrator.** Brief Explore teammates, synthesize, write the plan, commit. Do not `Grep`/`Read` source files directly during Step 2 — that's the Explore teammates' job.
- **Parallel-by-default.** Step 2 launches all Explore teammates in a single message.
- **Name every teammate.** Set `name:` on every `Agent` call.

## Reference files

- `assets/review_prompt.md` — used by `--review-external` to critique the plan via Gemini + Codex.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.


## Verification Contract

Detailed plans must contain machine-checkable verification commands and an effective `automation.suite_command` whenever the runner will execute them. If an acceptance item depends on operational evidence that cannot be machine-checked directly, the plan must name the operational evidence artifact and the runner-stamped amendment mechanism that records it. proxy evidence requires a roadmap or plan amendment before downstream execution relies on it.

## Closeout

Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml`; keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

### Manifest write

After the plan artifact path and repo-local handoff path are known, best-effort append a `type=detailed` entry to `plans/manifest.json` through `phase_loop_runtime.plan_manifest.append_entry` (`plan-manifest append`). Use `phase_loop_runtime.skill_paths` resolver helpers for any reflection or handoff paths needed by the metadata. The manifest entry must record `status=committed`, `slug`, `file`, `created_at`, `updated_at`, `owner_skill=<harness>-plan-detailed`, `task_summary`, `acceptance_criteria_count`, and the handoff path metadata (`handoff_path` / `handoff_ref`). Include a committed lifecycle event with `by=<harness>-plan-detailed` when the helper contract requires lifecycle provenance.

Manifest write failures are non-fatal during the dual-mode window: emit a ledger warning, add the failure to the mandatory reflection, and preserve the existing closeout, handoff, and final response behavior.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-plan-detailed/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.
