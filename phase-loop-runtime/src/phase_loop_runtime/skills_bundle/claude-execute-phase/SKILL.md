---
name: claude-execute-phase
description: "Harness Code executor for a <harness>-plan-phase lane plan. Uses TeamCreate, worktree-isolated teammates, lane DAG ordering, verification gates, and retry-once failure handling."
---

# <harness>-execute-phase

## Runtime State

For reflections, handoffs, and latest handoff pointers, follow `<harness>-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

`shared/phase-loop/protocol.md` is the canonical shared contract for the
`automation:` object, `verification_status`, frozen blocker taxonomy,
manual event import, and downstream roadmap-amendment handling.

When launched by the live phase-loop adapter, this skill runs through
non-interactive `<harness> -p`; its final reply must still emit the shared
`automation:` closeout or the parent runner will reduce the turn to
`repeated_verification_failure` rather than silent success. The repo-injected
workflow bundle remains authoritative whether the adapter delivers it inline or
through the run-local context-file fallback.
That repo-owned bundle includes the full Harness workflow pack
(`<harness>-phase-roadmap-builder`, `<harness>-plan-phase`,
`<harness>-execute-phase`, and `<harness>-phase-loop`) and does not depend on
installed bridge skills under `resolve_skill_bundle_root("claude")/`.

Harness closeout and blocker reporting must use the shared literals directly,
including `dirty_worktree_conflict`, and must treat older downstream plans as
stale after a roadmap amendment.

## Runner-Owned Lane Work Units

Injected HARNESSLANE runs may include a `HarnessLaneAssignment` from
`shared/phase-loop/protocol.md`. Treat that assignment as the work-unit
contract: execute only the selected `lane_id`, write only the listed
`owned_files`, treat `consumed_interfaces` as read-only, and emit one shared
`automation:` closeout for that work unit. Installed-skill drift is
warning-only when the repo-injected context is present.

Native Harness subagent and `agent_team` activity stays internal unless it emits
a typed `DelegationRequest` and the runner approves it. Review, reducer,
verify, and closeout prompts are distinct from implementation prompts. If the
selected policy is unsupported by the active harness, stop with a typed
non-human blocker instead of silently downgrading.

## Phase-Loop Adapter Mode

When the prompt or run-local context file starts with a concrete
`<harness>-execute-phase <plan>` command from `.codex/phase-loop/runs/`, this
adapter mode overrides the orchestrator-only interactive workflow below.

- Do not read installed handoffs under `resolve_skill_bundle_root("claude")/**`; the run-local
  context and the plan artifact are the only predecessor context.
- Do not call `ToolSearch`, `TaskCreate`, `TaskUpdate`, `TaskList`,
  `TeamCreate`, `TeamDelete`, `Agent`, `SendMessage`, `EnterWorktree`,
  `ExitWorktree`, or `AskUserQuestion`.
- Do not create `.claude/worktrees/` or lane branches. Execute the plan
  directly in the provided workspace, respecting the plan's owned-file
  boundaries and running the narrowest applicable verification commands.
- Treat ignored, private, raw-data, credential, and evidence-source files as
  read-protected unless the run-local context, phase plan, or source bundle
  explicitly allowlists the exact path or glob for read access. Do not infer
  permission from nearby owned output paths, old memory, or prior phases.
- Do not read or write `~/.claude/**` reflection or handoff files, and do not
  edit `.codex/phase-loop/` runner artifacts. Adapter-mode closeout happens
  only through stdout plus repo-local artifacts.
- If a missing script or unavailable helper is nonessential, replace it with a
  direct repo-local check and record the substitution in the closeout. If the
  missing input is essential, stop with a shared blocked `automation:`
  closeout.
- If ambiguity, access, dirty-state, or merge safety handling would normally
  ask a user, stop and emit a shared `automation:` closeout with
  `status: blocked`, `human_required` set accurately, the frozen
  `blocker_class`, concrete `blocker_summary`, and `verification_status:
  blocked`.
- End stdout with one shared `automation:` closeout. Do not wait for
  interactive approval after verification is complete.
- Do not emit a `/clear` recommendation or any human-only context-reset
  instruction. Continuity to the next phase is the run-local handoff plus the
  runner re-invoking the next phase in a fresh executor process — that fresh
  process is the context reset. There is no human in this loop to reset it.
- Before that closeout, run `git status --short` and classify dirty paths
  against the active plan's owned files and control artifacts. If a generated
  path is unowned, ignored without explicit allowlist/staging policy, or
  derived from unauthorized raw/private inputs, stop with
  `dirty_worktree_conflict` instead of reporting completion. But when required
  verification passed and the ONLY uncommitted paths are phase-owned outputs this
  run was not authorized to commit, report `awaiting_phase_closeout` and let the
  runner's graduated closeout gate commit them — reserve `dirty_worktree_conflict`
  for unowned, unauthorized-ignored, or overlapping-unrelated paths.

Follow-on executor for `/<harness>-plan-phase`. Consumes the plan doc + TaskCreate'd lane tasks and drives them to completion: root lanes first, parallel lanes in parallel, auto-merge on green, retry-once on failure, halt-all on second failure.

## When to use

- `/<harness>-plan-phase` has produced `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` and a `TaskCreate`'d task per lane.
- You want the phase executed **full-auto** end-to-end, with lanes merging to the current branch as their gates go green.
- The working tree is clean and you're on the branch lanes should merge into.

## When NOT to use

- Plan doc has never been produced → run `/<harness>-plan-phase` first.
- You want human checkpoints between lanes → execute lanes manually (`Agent(isolation: "worktree", name: "<SL-ID>")` with the lane's section pasted in).
- The plan doc's DAG has cycles or a lane's owned files overlap another lane's → fix the plan first.

## Model tiers (edit on Anthropic releases)

Model IDs appear only in this table. All model-routing logic in the workflow references the tier name.

| Tier      | Model              | Use for                                                       |
|-----------|--------------------|---------------------------------------------------------------|
| frontier  | <harness>-opus-4-8    | retry escalation ceiling, highest-stakes lanes                |
| strong    | <harness>-sonnet-4-6  | contract-authoring (IF-freeze), schema/migration, algorithmic |
| fast      | <harness>-haiku-4-5   | mechanical wiring, small components against frozen types      |

These map to the runtime `model_class` axis: planner = frontier, implementer =
strong, worker = fast. The shipped `model_policy` dispatches **implementation at
the implementer class**; on repeated failure the escalation ladder steps the
class up (implementer → planner) before, in **governed** `run_mode` only,
invoking the advisor panel. The default `run_mode` is `autonomous` — no panel,
no `human_required`; every governed escalation terminal is a non-human
`review_gate_block` surfaced in the run-end summary, never a synchronous human
wait.

Governed mode is **live on the serial path** (`--governed` / `PHASE_LOOP_RUN_MODE=governed`,
model-routing-v2): a pre-merge panel gate (codex + gemini) reviews the
implementation diff *before* the closeout commit and runs a bounded
review→fix→re-review loop; a `block` finding holds the merge until fixed or the
bounded loop terminates non-human. Autonomous runs spawn no panel. Concurrent-wave
dispatch is not governed yet.

## Inputs

| Arg | Required | Meaning |
|---|---|---|
| `<plan-path>` | no | Path to plan doc. Default: auto-detected via env vars + filename convention. |
| `--dry-run` | no | Parse + validate, print the dispatch schedule with per-lane model assignments, do not spawn teammates. |
| `--resume` | no | Continue a partially executed plan: skip lanes already merged (their merge commit is on the target branch and their produced gates are closed). |

## Environment variables

Plan-location variables are inherited from `<harness>-plan-phase` so the two skills resolve the same defaults:

| Variable | Default | Meaning |
|---|---|---|
| `PLAN_SPEC` | Auto-detected highest `specs/phase-plans-v*.md` | Path to spec file (used only for version extraction here). |
| `PLAN_VERSION` | Extracted `v\d+` from spec filename | Version token embedded in plan-doc filename. |
| `PLAN_PHASE_ALIASES` | Built-in alias table | Path to alias-map JSON. |
| `PLAN_DOC` | `plans/phase-plan-<VERSION>-<PHASE_ALIAS>.md` | Override the resolved plan-doc path directly. |
| `EXECUTE_MERGE_TARGET` | Current branch at invocation | Branch lanes merge into. |
| `EXECUTE_WORKTREE_ROOT` | `.worktrees/` (inside repo; auto-gitignored) | Root dir for per-lane worktrees. |
| `EXECUTE_MAX_PARALLEL_LANES` | `4` | Max concurrent lane dispatches per wave; parallelism-maximizing default. Lower to `2` when stale-base churn becomes problematic on phases with many interdependent lanes. |

## Expected helpers

Helpers under `resolve_skill_bundle_root("claude")/_shared/` may be absent on some hosts. Each helper degrades as listed:

- `_shared/next_reflection_path.py` — legacy helper only; current closeout writes reflections to `resolve_skill_bundle_root("codex")/<harness>-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`.
- `_shared/review_with_cli.py` — used only with `--review-external`; no fallback (required for that flag).

## Deferred tool preloading

Several tools this skill uses are deferred and must be registered via `ToolSearch` before first call. Load them in a single query at the top of Step 1 so mid-workflow calls (especially `TaskUpdate` on every dispatch and `AskUserQuestion` on preflight failures) don't pay a round-trip:

```
ToolSearch(query: "select:TaskCreate,TaskUpdate,TaskList,TeamCreate,AskUserQuestion")
```

## Workflow (orchestrator-only main thread)

The main thread reads exactly two things: the plan doc and the TaskList. It does not Grep/Read repo source — lane teammates own their files. If the main thread is reaching for Grep or Read on source files, the teammate brief was incomplete; re-brief via `SendMessage`.

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
print(mod.resolve_handoff_path(repo, "<harness>-plan-phase"))
PYH
)"
```

If `$PREDECESSOR_HANDOFF` exists, `Read` it in full — `<harness>-plan-phase` just produced the lane plan and may have flagged gotchas the dispatch loop needs to know about.

Defense-in-depth checks (the repo-key scheme prevents the common cross-project case, but these still catch symlink-shared workspaces, manual file copies, and stale handoffs):

- `from:` must be `<harness>-plan-phase`.
- Timestamp must be <7 days.
- Every `artifact:` path must resolve under `$(git rev-parse --show-toplevel)`.

On any failure, flag via `AskUserQuestion` with `[use anyway, ignore, abort]`.

Fold the handoff's "Open items" and "Repo-specific gotchas" into each lane teammate's brief (Step 5's Teammate brief contents) so they carry the same context without the user having to reinject it.

If absent, proceed standalone — the plan doc is authoritative either way.

### Step 1 — Resolve + parse plan doc

Resolve in order: `$PLAN_DOC` → `<plan-path>` arg → default path built from `$PLAN_VERSION` + phase alias argument.

`Read` the plan doc. Parse these sections (headings are stable IDs from `<harness>-plan-phase`):

- `## Interface Freeze Gates` — list of `IF-0-<PHASE>-<N>` items with descriptions.
- `## Cross-Repo Gates` (optional) — `IF-XR-<N>`.
- `## Lane Index & Dependencies` — machine-readable DAG block (`SL-N`, `Depends on:`, `Blocks:`, `Parallel-safe:`).
- `## Lanes` — per-lane: `Scope`, `Owned files`, `Interfaces provided`, `Interfaces consumed`, task table.
- `## Verification` — final end-to-end commands.

Build the lane graph. Topologically sort it; reject on cycle with a clear error.

### Step 2 — Preflight

- **Run `scripts/verify_harness.sh <merge-target>`.** Enforces the hard gates: git + git-worktree available, inside a work tree, merge-target branch exists locally, working tree clean (allowlist: `.claude/worktrees/`, `.claude/<harness>-execute-phase-state.json`), `.gitignore` covers worktree paths. **Non-zero exit blocks dispatch.** On dirty-tree failure, invoke `AskUserQuestion` with `[commit the changes as a chore, stash for the duration of the phase, abort /<harness>-execute-phase]` and take the user's answer. No override.
- Record merge target = current branch (or `$EXECUTE_MERGE_TARGET`).
- Sanity check: every symbol appearing in any lane's `Interfaces consumed` must either be produced by an upstream lane's `Interfaces provided` OR be pre-existing (skip unknown symbols with a warning, don't hard-fail).
- Producer-dependency check: if a lane consumes another lane's findings, interfaces, or artifacts, that producer lane must appear in `Depends on`. If a lane writes a synthesized artifact, it must be downstream of every producer lane it summarizes. Missing dependencies block execution until the plan is corrected.
- If `--dry-run`: print the topological schedule with per-lane model/thinking assignments (see Step 3) and stop here.

### Step 2.5 — Baseline-capture

Run the plan's full regression command once at phase start against the pre-phase tip. Cache the failing-test list to `.claude/<harness>-execute-phase-state.json#baseline_fails`. Step 9 diffs against this cached list rather than reconstructing it.

Before hypothesizing test pollution on a post-merge failure, run the failing test in isolation (fresh process) to distinguish a direct regression from fixture pollution.

### Step 2a — Worktree hygiene preflight

Prune stale worktrees in `.claude/worktrees/` at phase start. Skip on `--resume`.

```bash
bash "$(git rev-parse --show-toplevel)/.claude/skills/<harness>-execute-phase/scripts/sweep_stale_worktrees.sh"
# Pass --dry-run to preview decisions without removing anything.
```

For each worktree marked `PRUNE`:

1. `git worktree unlock <path>` (succeeds silently if unlocked).
2. `git worktree remove -f -f <path>` — double-force bypasses both the lock and the unmerged-changes check (safe since the ancestor check already confirmed incorporation).
3. `git branch -D <branch>` **only** if the branch name matches an auto-named pattern (`worktree-*`, `phase/*/sl-*`). Leave human-named branches (`skills/*`, `feature/*`, `fix/*`) intact even when their work is on the merge target — the user may still want the reference.

Worktrees marked `KEEP` are left alone; log a one-line warning per worktree.

### Step 3 — Assign model + thinking per lane

Each lane gets a model and thinking level matched to its complexity. The assignment is encoded on the `Agent` call via `model:` and communicated as guidance in the brief.

Apply in order, first match wins:

1. Lane has `Execution hint: <tier>/<thinking>` inside its `### SL-N` section → use it verbatim.
2. Lane publishes any `IF-0-*` gate (contract-authoring) → `strong` / high. Downstream lanes depend on the symbols being correct.
3. Lane scope mentions migrations, schema, or SQL → `strong` / high. Bad migrations are expensive to unwind.
4. Lane scope mentions algorithmic/computed logic AND has tests as first task → `strong` / medium.
5. Lane owns >10 files AND publishes no interfaces (wiring / mechanical refactor) → `fast` / low.
6. Lane implements small components against already-frozen types → `fast` / low.
7. Default fallback → `strong` / medium.

Resolve tier → model from the Model tiers table at the top of this skill and pass the concrete model ID to `Agent(model: …)`.

**Retry escalation** (after a lane's first failure):

- `fast` → `strong`
- `strong` → `frontier`
- `frontier` → stay at `frontier`
- Thinking level bumps up one tier (`low → medium → high`).

`Agent` does not expose a direct thinking-level parameter — convey it in the brief's framing (e.g., "Think carefully before editing shared type definitions" vs "These are mechanical edits; move quickly").

### Step 4 — TeamCreate

**Preflight (before any `Agent` or `TeamCreate` call).** Flush inherited team context from a predecessor skill run via `TeamList` (or a defensive `TeamDelete`). On the error signature `"already leading team X"`, `TeamDelete` the stale team and retry with a repo-scoped suffix — e.g., the short hash of `$(git rev-parse --show-toplevel)` — to prevent cross-project team-name collisions. Same-aliased phases from different repos collide on the global team namespace without a suffix. Error signature `"Team X does not exist"` on `TeamDelete` is a no-op; proceed.

Create one team for the phase:

- **Team name**: `phase-<PHASE_ALIAS>` (e.g., `phase-p1`), with repo-scoped suffix appended when collision is detected.
- **Teammates**: one per lane, `subagent_type: "general-purpose"`. Teammate names use the allocator — see Step 5.

TeamCreate registration enables `SendMessage` retry rounds without fresh Agent spawns.

### Step 5 — Dispatch loop

State per lane: `pending | running | verify-ok | merged | failed`. State per gate: `open | closed`.

Repeat until all lanes are `merged` or halt is triggered:

1. **Find ready lanes** — `pending` lanes whose upstream lanes are all `merged` AND all consumed `IF-0-*` gates are `closed`. Synthesized artifact writer lanes are ready only after every producer lane named in `Interfaces consumed` is merged. Cap the dispatch batch at `EXECUTE_MAX_PARALLEL_LANES` (default 4). Slice the eligible set to the first N (lower SL-ID first) and queue the rest.

2. **Allocate worktree names**. For each ready lane, run `scripts/allocate_worktree_name.sh <sl-id>`. Substitute the emitted name (e.g., `lane-sl-1-20260418T144536-nxz5`) into both `Agent(name=…)` and the briefed `EnterWorktree(name=…)`. Bare `lane-<sl-id>` names collide under rapid dispatch.

3. **Dispatch in parallel** — single message with one `Agent(team_name: "phase-<alias>", name: "<allocated-name>", subagent_type: "general-purpose", model: "<assigned>")` call per ready lane. Sequential dispatch within a wave is a bug. Do NOT pass `isolation: "worktree"` when `team_name` is set — the harness drops `isolation` in that combination. Use `team_name` for coordination + teammate-called `EnterWorktree` for filesystem isolation.

4. **Teammate brief** contains:
   - **Worktree isolation (mandatory, first tool call)**: Load `EnterWorktree` via `ToolSearch(query="select:EnterWorktree,ExitWorktree")` if not already in the tool registry. Call `ExitWorktree(action="keep")` FIRST (no-op if no session active; clears any stale session-state flag inherited from a prior teammate). Then call `EnterWorktree(name: "<allocated-name>")` as the very next tool call, BEFORE any file operation. Every subsequent edit/commit goes into that worktree. At the end, call `ExitWorktree(action: "keep")` so the worktree and branch remain available for the orchestrator to merge.
   - The full `### SL-N` section copied verbatim from the plan doc.
   - **Architecture context** (1–2 sentences): how this lane fits the phase; how this phase fits the roadmap.
   - **Related files** the lane reads but does not own: type defs, test fixtures, shared config. Distinct from `Owned files`.
   - Concrete upstream artifact paths (populated from now-merged upstream lanes) for every entry in `Interfaces consumed`.
   - The merge target branch and the orchestrator's current tip SHA, injected as `<TIP_SHA>` (used by the stale-base check below).
   - The lane's test → impl → verify task list. The lane's verify command must include regression files listed in the plan's `## Verification` section that reference this lane's owned files, not only the lane's new tests. Each lane must enumerate tests that mock or stub any existing public function the lane's scope adds a call to, and include those tests in verify.
   - Thinking-level guidance matching the lane's profile.
   - **Stale-base discipline**: AFTER `EnterWorktree` returns and BEFORE any code change, run `git rebase main` (or `git merge main --no-ff -m "merge: incorporate prior-lane foundation"`). Verify with `git merge-base --is-ancestor <TIP_SHA> HEAD && echo OK || echo STALE`. Repeat the rebase immediately before your final commit. On conflicts, STOP and report — do not resolve silently. Never `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree; this destroys peer-lane work on `--no-ff` merge.
   - **Staging discipline**: Forbid `git add -A` and `git add .`. Stage with explicit pathspecs drawn from the lane's `Owned files`.
   - **No self-merge**: DO NOT run `git merge` or `git push` against the merge-target branch yourself; the orchestrator owns merges.
   - **Structured-reply instruction**: "When done, reply with JSON `{lane, verify_exit_code, failed_tasks, pre_existing_failures, tsc_error_delta, notes, commit_sha, branch, worktree_path}`. `failed_tasks` lists failures attributable to this lane; `pre_existing_failures` lists failures reproducible on the base SHA (capture the base-SHA reproduction command as evidence). When the lane's verify chain runs whole-repo type-checking, populate `tsc_error_delta` (errors-after minus errors-before); accept `tsc_error_delta <= 0` as a pass even if raw exit code is non-zero. `branch` and `worktree_path` come from `EnterWorktree`'s return value."

   Apply the `/<harness>-task-contextualizer` checklist to every brief.

5. **Await completions**. For each:
   - `verify_exit_code == 0` → run gate verification (Step 6). On green → auto-merge (Step 7). Mark lane `merged`, flip produced gates to `closed`, update the lane's `TaskCreate`'d task to `completed`.
   - Non-zero or gate failure → retry-once (Step 8).
   - **Idle without JSON reply** → handle per the decision table below. After one `SendMessage` retry with no response in a reasonable interval, escalate directly to SHA-discovery rather than continuing to ping.

     | Case | Condition | Action |
     |---|---|---|
     | (a) | Idle + no commits | `SendMessage` one nudge requesting commit + envelope. |
     | (b) | Idle + commit on identifiable lane branch | Treat the commit as the result; run Step 6.5 / Step 7 audits; proceed on `CLEAN`. |
     | (c) | Idle + multiple candidate commits | One more `SendMessage`; if no response, halt for user. |
     | (d) | Idle + commits landed on merge-target directly | Route to `PARENT_ON_MERGE_TARGET` verdict (Step 6.5). |

6. Persist lane + gate state to `.claude/<harness>-execute-phase-state.json` after every transition (enables `--resume`).

### Step 6 — Gate verification

For each gate the lane provides:

1. If the plan doc's `## Verification` section has a command that names this gate's artifact (e.g., `psql` for schema gates, `grep` for symbol gates), run it against the lane's worktree.
2. Else fall back:
   - Files listed under `Owned files` exist.
   - Symbols listed under `Interfaces provided` are grep-visible at expected paths.
   - Lane's own verify command exited 0.

Any failure here counts as a lane failure and triggers retry-once.

### Step 6.5 — Parent-tree leakage check

Detects two isolation failure modes before merge: (a) teammate writes landing in the parent checkout, (b) `EnterWorktree` falling back to in-place branch creation on the parent.

Run:
```bash
bash scripts/parent_tree_leakage_check.sh <lane-id> <owned-globs-file> <merge-target>
```

Verdicts (emitted on stdout; details on stderr):

- **`CLEAN`** — proceed to Step 7.
- **`LEAKAGE_DETECTED`** (dirty files overlap the lane's owned globs):
  1. Verify byte-equality: for each leaked path, `git diff <lane-sha> -- <path>` must be zero lines (confirms the leak is a redundant copy of committed lane work). If non-zero, treat as independent uncommitted work and STOP.
  2. Clean the parent: `git checkout -- <modified>` for tracked changes; `rm <untracked>` for new files. Do NOT `git stash`.
  3. Record in `.claude/<harness>-execute-phase-state.json` under the lane's `notes`.
  4. Proceed to Step 7.
- **`UNRELATED_DIRTY_TREE`** (dirty files outside the lane's globs) → STOP. Ask via `AskUserQuestion` whether to stash, commit, or abort.
- **`PARENT_ON_WRONG_BRANCH`** (tree clean but parent on a non-merge-target branch — `EnterWorktree` fell back to in-place branch creation):
  1. Merge by SHA per Step 7.
  2. Run `git checkout <merge-target>` in the parent BEFORE Step 7's worktree cleanup — otherwise `git worktree remove` refuses.
  3. Record parent-fallback note in state.
- **`PARENT_ON_MERGE_TARGET`** (alias `LANE_BYPASSED_ISOLATION`) — resolve the reply envelope's `commit_sha`; if `git merge-base --is-ancestor <sha> <merge-target>` is already true before any merge step, the commit landed directly on the target via an `EnterWorktree` fallback:
  1. Skip the merge (already merged).
  2. Clean up the worktree and branch per Step 7's post-merge cleanup.
  3. Record the fallback in `.claude/<harness>-execute-phase-state.json` and note it in the close-out.

### Step 7 — Auto-merge

**Branch discovery.** Resolve the lane's branch/worktree from the reply envelope's `commit_sha` via `git log --oneline -1 <commit_sha>` + `git worktree list`. Never trust the briefed branch name.

**Cross-lane file-touch audit.** Before the destructiveness check, run:

```bash
python scripts/audit_lane_file_touches.py <lane-sha> <plan-doc-path> <this-lane-id>
```

**Parser contract.** After matching the `**Owned files**:` heading, the parser walks subsequent deeper-indented bullets until the next sibling bullet or blank line and accumulates backticked tokens. Both inline form (`- **Owned files**: \`path/one\`, \`path/two\``) and nested-bullet form (heading followed by `  - \`path/one\``) are supported. A unit test covering both formats ships alongside the script.

Verdicts:

- **`CLEAN`** → every touched file is within this lane's `Owned files` globs. Proceed.
- **`PEER_INTRUSION`** → the lane touched files owned by another lane. Stderr lists peer + files. Pause on `AskUserQuestion`: merge anyway, bounce the lane, or ask the teammate to revert the peer edits.
- **`ORPHAN_FILES`** → the lane touched files outside every lane's globs. Surface to user — likely plan or impl bug.

**Pre-merge destructiveness check** — lanes that committed against a stale base can wipe peer-lane work on `--no-ff` merge. Use the three-outcome script:

```bash
bash scripts/pre_merge_destructiveness_check.sh <lane-sha> <merge-target> <whitelist-path>
```

Where `<whitelist-path>` is a file (one path per line) of deletions the lane legitimately performs per its plan section; `/dev/null` if the lane is purely additive.

The script (or a sibling) also runs an ADDITION-side check mirroring the deletion check: flag commits that ADD more than N files (default ~50) or more than M lines outside the lane's owned globs. Out-of-scope adds surface as a verdict alongside deletions to catch lanes that wandered.

Verdicts:

- **`SAFE`** — deletion list empty or all whitelisted. Merge with `git merge --no-ff <lane-sha>`.
- **`STALE_BASE_DETECTED`** — lane branched from a pre-peer-merge main but did not actively delete. Preview, then finalize with the 3-way merge:
  ```bash
  git merge --no-ff --no-commit <lane-sha>
  ls <peer-lane-owned-paths>   # must exist
  git commit --no-edit
  ```
  Do NOT salvage — salvage loses commit lineage.
- **`CONFLICT`** — lane actively removed files. DO NOT `--no-ff` merge. Salvage:
  ```bash
  git checkout <lane-sha> -- <lane's owned paths from the plan doc>
  git commit -m "feat(<phase>,<sl-id>): <subject>

  Salvage of <SL-ID> lane work: lane commit was based on stale main and
  would have deleted <peer lanes' files>. Cherry-picked in-scope additions only."
  ```
  Count this as a successful completion — no retry.

**Merge conflict** (non-destructive case) → treat as lane failure: `git merge --abort`, surface the conflict to the teammate via `SendMessage` asking it to rebase inside its worktree, then retry the merge.

**Post-merge import smoke** — run immediately after `git merge --no-ff` and before dispatching the next wave:

```bash
packages="$(git diff --name-only HEAD~..HEAD \
    | grep '__init__\.py$' \
    | sed 's|/__init__\.py$||; s|/|.|g' \
    | sort -u)"
if [[ -n "$packages" ]]; then
    bash scripts/post_merge_import_smoke.sh $packages
fi
```

On non-zero exit, the merge broke package load. Unwind with `git reset --hard HEAD~`, then retry per Step 8. Second failure halts the phase.

**Post-merge cleanup.** First, `cd "$(git rev-parse --show-toplevel)"` so subsequent Bash calls survive. Then `git worktree remove --force` any worktrees the lane used. `git branch -D` (not `-d`) every lane branch — salvaged work leaves branches unreachable from the merge target. If Step 6.5 returned `PARENT_ON_WRONG_BRANCH`, `git checkout <merge-target>` in the parent BEFORE removing worktrees.

### Step 8 — Retry-once, then halt

On first failure for a lane, decide between SendMessage-resume and kill-and-respawn based on whether the lane committed anything:

- **`commit_sha` empty AND `verify_exit_code != 0`** (teammate STOPped during preflight, no work done): kill via `TaskStop` and re-`Agent`-spawn fresh. Never resume a STOPped teammate with no commits — the worktree may be reaped and the resumed instance writes into the parent.
- **`commit_sha` populated AND verify failed** (teammate did work but it broke): re-address the same named teammate via `SendMessage` to preserve branch-state context.

In either case, the brief includes: the failure log, the escalated model+thinking hint, and the instruction to fix the failing task and re-run verify.

On second failure:

- Cancel all still-running lane Agents via their task IDs.
- Emit a diagnostic report naming the failing lane, the failing task, the last ~30 log lines, and which lanes were merged vs pending.
- Persist state to `.claude/<harness>-execute-phase-state.json` and exit cleanly. User can fix and re-run with `--resume`.

### Step 9 — Final verification, cleanup, and summary

After all lanes merged:

1. Run every command under the plan doc's `## Verification` section against the merged tree.
2. Run every assertion under `## Acceptance Criteria` that can be mechanically checked.
3. **Team teardown**. Try `TeamDelete` first. If it blocks (in-process teammates ignore `shutdown_request`), fall back to filesystem cleanup: `bash scripts/team_teardown.sh phase-<alias>` (rm on `the Harness home directory/teams/<team>/` + `the Harness home directory/tasks/<team>/`).
4. **Kill leftover background processes**. Run `ps aux | grep -E "next dev|node.*dev"` and `kill` any stragglers spawned by lane teammates.
5. **Clean-tree verification**. Run `git status`. After successful completion the tree must be clean (lane merges are the only changes, and they're committed). Allowlist: `.claude/worktrees/`, `.claude/<harness>-execute-phase-state.json`. Anything else → stop and surface.
6. Emit final summary:
   - Lanes merged (with merge-commit SHAs)
   - Gates closed
   - Final verification pass/fail
   - Total wall-clock duration
   - Per-lane breakdown: model used, duration, token spend, retry count
7. Mark the phase's parent TaskCreate (if one exists) as completed.

**Halt exception**: on Step 8 second-failure halt, `.claude/<harness>-execute-phase-state.json` persists for `--resume`. This is the documented dirty-tree exception; no commit required.

### Step 9.5 — Close-out: Reflection + Handoff

Before close-out, run `git status --short -- <plan_path> <roadmap_path>` for every consumed or updated planning artifact. If any planning artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <path>` for each artifact. Rerun status and report `Artifact state: staged|tracked|modified|unstaged|blocked` for each artifact. Do not commit unless the user explicitly asked for a commit. Repo-local handoff files are operational state: do not `git add` an ignored handoff alongside the plan artifact unless the plan's owned-files/allowlist explicitly includes the handoff directory; leave ignored handoffs ignored and exclude them from artifact-state reporting.

Determine the next step before final response and handoff:

- If the current phase is incomplete or verification failed, set `Next phase: <current alias> - blocked: <blocker>` and `Next command: none - <blocker>`.
- If another generated phase plan is ready, set `Next phase: <next alias> - execution ready` and `Next command: /<harness>-execute-phase <next-alias>`.
- If the roadmap has an unplanned ready phase, set `Next phase: <next alias> - planning ready` and `Next command: /<harness>-plan-phase <next-alias>`.
- If the roadmap needs extension, set `Next phase: none - roadmap extension needed` and `Next command: /<harness>-phase-roadmap-builder`.
- If all phases are complete, set `Next phase: none - roadmap complete` and `Next command: none - roadmap complete`.

Resolve paths, with an inline fallback for the reflection helper:

```bash
REFLECTION_PATH=resolve_skill_bundle_root("codex")/<harness>-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
REPO_LOCAL_HANDOFF=$(python3 - <<'PYH'
from importlib import util
from pathlib import Path
repo = Path.cwd().resolve()
spec = util.spec_from_file_location("handoff_path", repo / "shared" / "phase-loop" / "handoff_path.py")
mod = util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.resolve_handoff_path(repo, "<harness>-execute-phase"))
PYH
)
mkdir -p "$(dirname "$REPO_LOCAL_HANDOFF")"
SKILL_MD=resolve_skill_bundle_root("codex")/<harness>-execute-phase/SKILL.md
```

**Primary path — orchestrator writes inline.** The orchestrator writes BOTH files directly via the Write tool using the schemas below. This is the default.

**Optional alternative.** The orchestrator MAY instead spawn a frontier-tier agent for fresh-context independent review. Use this only when independent review is desired.

FILE 1 — REPO-AGNOSTIC reflection → write to `<REFLECTION_PATH>`

```
# <harness>-execute-phase reflection — <ISO timestamp>

## Run context
- Skill: <harness>-execute-phase
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

Do NOT reference this project, codebase, filenames, or domain.

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
from: <harness>-execute-phase
timestamp: <ISO>
artifact: <phase alias that was executed, merge-commit SHAs>
artifact_state: <staged|tracked|modified|unstaged|blocked>
next_skill: <<harness>-execute-phase|<harness>-plan-phase|<harness>-phase-roadmap-builder|none>
next_command: </<harness>-execute-phase next-alias|/<harness>-plan-phase next-alias|/<harness>-phase-roadmap-builder|none - reason>
next_phase: <next alias - status|none - reason>
---

# Handoff for the next skill

The next skill in the chain is usually `/<harness>-plan-phase` for the
subsequent phase, or `/<harness>-phase-roadmap-builder` if the user is
appending new phases to the roadmap.

## Summary
<2-3 sentences: which phase completed, how many lanes merged,
final verification state.>

## Key decisions made this run
- <numbered, one line each — salvaged commits, retry outcomes,
  which lanes needed escalated models>

## Open items for the next skill
- <concrete — e.g., "Phase P2A is next; its Interfaces consumed
  list references IF-0-P1-1 which was frozen with signature X">

## Repo-specific gotchas surfaced
- <surprises this run: slow tests, flaky browser checks,
  unexpected module coupling, quirks worth knowing>

## Files committed this run
- <merge-commit SHAs, with lane ID and brief description>

## Planning artifacts staged this run
- <path> @ <artifact_state>

## Next skill's likely scope
- <forecast of what <harness>-plan-phase / <harness>-phase-roadmap-builder will touch next>
```

After both files are written, **in the interactive TUI path only** (a human is
driving), print to the user:

> Phase `<alias>` complete. `<N>` lanes merged; final verification `<pass|fail>`.
> Reflection saved to `<REFLECTION_PATH>`.
> Handoff written to `<REPO_LOCAL_HANDOFF>`.
> Artifact state: `<staged|tracked|modified|unstaged|blocked>`.
>
> Next phase: `<next alias - status|none - reason>`.
> Next command: `<exact command or none - reason>`.
> Recommended next step (interactive only): either run `/clear` to reset your
> context window and then invoke the next command above, **or** dispatch a fresh
> subagent (Task/Agent) to carry the next phase in clean context when this loop
> is skill/tool-driven. The next skill reads the handoff automatically.

`/clear` is a human-operated reset and applies only to the interactive TUI path
above. **Do not emit it in Phase-Loop Adapter (autonomous) mode** — an
autonomous loop has no human to run it and cannot clear its own context.
In autonomous runs, continuity is the written handoff and the runner re-invokes
the next phase in a fresh executor process; that fresh process *is* the context
reset. See "Phase-Loop Adapter Mode" above.

## Lane state machine

```
pending ──► running ──► verify-ok ──► merged
                 │                        ▲
                 └─fail─► failed ─retry─►─┘ (once)
                                 │
                                 └─fail2─► HALT
```

## Parallelism contract

- All lanes whose dependencies are satisfied at the same time MUST be dispatched in the same message (parallel `Agent` tool calls), up to `EXECUTE_MAX_PARALLEL_LANES`.
- Lanes must not share files. The plan's lane-validation checklist guarantees disjoint ownership; <harness>-execute-phase trusts the plan and does not re-verify file-glob intersections.
- Shared generated files (e.g., barrel index files, generated types) are called out in the plan's `## Execution Notes`; only the lane that lists them under `Owned files` may modify them.

## Model selection quick-reference

| Symptom in the plan doc | Assigned tier | Thinking |
|---|---|---|
| `Scope` mentions schema / migration / SQL | `strong` | high |
| `Interfaces provided` includes any `IF-0-*` gate | `strong` | high |
| `Scope` mentions algorithm / compute / worker logic | `strong` | medium |
| `Owned files` glob expands to >10 files, no interfaces provided | `fast` | low |
| `Scope` is "small components against frozen types" | `fast` | low |
| `Execution hint:` line is present in lane section | Use it verbatim | Use it verbatim |
| Retry | One tier up | One tier up |

Resolve tier names to model IDs via the Model tiers table at the top of this skill.

## Output contract

On successful completion:

1. Every lane's branch merged into the target branch with a `--no-ff` commit, in topological order.
2. All lane worktrees removed; lane branches deleted.
3. All `TaskCreate`'d lane tasks marked `completed`.
4. `.claude/<harness>-execute-phase-state.json` deleted (or the `status` field set to `complete`).
5. Team torn down via `TeamDelete` or `team_teardown.sh`.
6. Final summary message to the user.

On halt:

1. Lanes that completed remain merged; their state is preserved.
2. `.claude/<harness>-execute-phase-state.json` contains the failing lane's name, task, log tail, and which gates/lanes were green vs open.
3. User can `/<harness>-execute-phase --resume` after fixing the blocker.

## Worktree layout

```
<repo>/
├── .claude/
│   ├── worktrees/
│   │   ├── lane-sl-1-<timestamp>-<random>/
│   │   └── …
│   └── <harness>-execute-phase-state.json
```

Both paths are auto-added to `.gitignore` in Step 2.

## Teamwork posture (hard rules)

- **Main thread = dispatcher.** Read plan doc + TaskList + git state. Do not Grep/Read source code during execution, except for the Step 7 destructiveness check and file-touch audit.
- **Parallel-by-default.** Ready lanes dispatch in a single message, every round.
- **Name every teammate via the allocator.** Bare `lane-<sl-id>` names collide in rapid dispatch.
- **TaskCreate tasks are the source of truth for lane status.** Update them as lanes transition.
- **No speculative refactoring.** Lane teammates must stay within their `Owned files` globs; reject their work at gate verification if they wandered. Use the file-touch audit to catch this pre-merge.
- **Never trust a branch name.** Resolve work by `commit_sha` from the reply envelope.
- **Never `--no-ff` merge without the destructiveness check.**
- **Never resume a STOPped teammate with no commits.** Kill + respawn.
- **Never override the dirty-tree preflight.** `verify_harness.sh` is the gate. Only paths forward: (a) commit as `chore:`, (b) `git stash push -u` and restore post-phase, (c) abort.
- **Never rely on implicit shell CWD.** Every orchestrator git/script invocation MUST either wrap as `bash -c 'cd "$(git rev-parse --show-toplevel)" && <cmd>'` or pass `git -C <repo-root>`. `git worktree remove` on the shell's current CWD bricks subsequent Bash calls.

## Browser verification capabilities

When a plan's `## Verification` section includes Playwright/e2e commands or lane tasks ask for browser smoke-testing, run them:

- **Playwright via PMCP** — `pmcp_invoke(tool_id="playwright::browser_navigate", ...)` and related `playwright::*` tools. Default.
- **Chrome DevTools Protocol (CDP)** — low-level debugging (performance traces, CPU profiling).
- **`<harness>-in-chrome`** — extension-context automation only.

The PMCP Playwright server provisions on demand. If the repo lacks a Playwright config, add one in the lane rather than deferring the test.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.


## Runner Verification Evidence

Before reporting a successful closeout, require the runner-owned verification artifact. The closeout must name `verification_artifact_path`, quote the artifact summary line, and must not report `verification_status=passed` unless that artifact exists and supports the executed work. Treat dependency-manifest install refresh and the full suite before closeout as runner-enforced expectations, not optional narrative checks. A blocked gate may be re-verdicted only by rerunning the originally specified runner check; proxy evidence requires a roadmap or plan amendment before the verdict changes. A prior run's terminal-summary is authoritative for reconcile-or-skip only when that closeout was accepted; a rejected or blocked prior closeout — regardless of any self-reported `complete`/`passed` — must not be reconciled against. On a re-run, re-do and re-verify the phase work from scratch rather than skipping on a stale summary.


## Spec Delta Closeout

Before final closeout, choose exactly one `spec_delta_closeout.v1` decision: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Cite metadata-only evidence paths such as the active plan, lane closeouts, targeted pytest output, and `git diff --check` output. Preserve the phase plan's target surfaces and `redaction_posture=metadata_only`; do not include raw specification bodies, raw patch bodies, credentials, provider-supplied payloads, local environment values, or evidence-source contents. Missing or malformed spec-closeout evidence is a repairable automation blocker with `blocker_class=contract_bug` unless the decision is `human_source_judgment_required`.

## Closeout

### Manifest lifecycle

After plan validation and before lane execution, perform a best-effort `plan-manifest append` lifecycle update through `phase_loop_runtime.plan_manifest.update_lifecycle` to mark the matching `type=phase` entry `executing` with run metadata. During closeout, update the same entry to `completed` or `failed` with verification metadata, reflection metadata, produced-gate metadata, `if_gates_produced`, and dirty-worktree summary fields as available. `if_gates_produced` must list only the IF gates the active phase produces per its own plan; never carry a prior phase's gate forward into this phase's closeout. Manifest lifecycle failures are non-fatal during the dual-mode window: emit a ledger warning, mention the warning in the mandatory reflection, and preserve the existing phase closeout JSON, verification, and dirty-worktree behavior.

Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-execute-phase/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.
