---
name: phase-roadmap-builder
description: "Harness-optimized roadmap planner. Use when the user wants to turn a conversation, architecture discussion, or markdown spec into a multi-phase roadmap for later Harness planning and execution. Creates or extends versioned phase roadmap specs in execution mode, but in Plan Mode returns a proposed plan only. Do not use for single bounded changes; use <harness>-plan-detailed instead."
---

# Harness Phase Roadmap Builder

Builds a multi-phase roadmap that downstream `<harness>-plan-phase` can turn into implementation lanes. This is the Harness port of the Claude-oriented roadmap builder; do not edit or depend on the original skill at runtime.

## Shared Protocol

Follow `shared/phase-loop/protocol.md` for the shared closeout contract.
Roadmap-builder closeout must emit `automation:` metadata that agrees with the
human-readable `Next phase`, `next_skill`, `next_command`, and
`verification_status` fields.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Preserve Plan Mode boundaries. In Plan Mode, inspect files and produce a `<proposed_plan>` roadmap only; do not create or edit repo-tracked files.
- In Default mode, write the roadmap artifact only after enough context is available.
- During planning-only roadmap creation, do not execute test suites, builds, formatters, generators, or migrations. Capture end-to-end verification commands in the roadmap without running them.
- Use local truth first: read named specs, `AGENTS.md`, and repo docs the user explicitly points at. Do not invent phases from vague context.
- Use PMCP for external capability research when current docs or third-party tooling facts affect the roadmap. Prefer `gateway_catalog_search`, then `gateway_describe`, then `gateway_invoke`; use Context7 for library/product docs. Use Bright Data only if PMCP exposes it in the current environment.
- Use `request_user_input` only when available; in Default mode ask one concise plain-text question only if a missing decision would make the roadmap wrong.
- Prefer fewer serial phases and more parallel work inside each phase. A phase boundary exists only when a contract must freeze before downstream work starts.
- Do not spawn subagents unless the user explicitly asks for agents, subagents, delegation, or parallel agent work.

## Inputs

- Optional spec path: markdown file to fold into the roadmap.
- Optional output path: default `specs/phase-plans-v<N>.md`, choosing the highest existing version or `v1` if none exists.
- Append mode: if the output roadmap exists, add phases without rewriting prior phases unless the user explicitly requests a replacement.
- Multi-roadmap disambiguation: when several `specs/phase-plans-v*.md` exist, default to the highest version but confirm the intended target before writing. Each roadmap file is its own alias namespace — create mode starts a fresh namespace for a new version, and append mode must reuse the existing file's aliases without renumbering or colliding with them. Never fold a new initiative into an unrelated newer roadmap just because it is the latest version; a new initiative is a new roadmap (create mode), not an append.

## Workflow

1. Resolve repo root with `git rev-parse --show-toplevel`, then inspect:
   - the named spec, if any;
   - `AGENTS.md` and `CLAUDE.md`, if present;
   - existing `specs/phase-plans-v*.md`;
   - markdown files the user named.
2. Choose create or append mode:
   - Create mode writes a full roadmap.
   - Append mode reads existing aliases, phase numbers, dependencies, and interface gates, then appends only new phases.
3. Write top-level context:
   - `Context`
   - `Architecture North Star` when structural
   - `Assumptions`
   - `Non-Goals`
   - `Cross-Cutting Principles`
4. Decompose into phases:
   - serial phases only at interface-freeze boundaries;
   - sibling phases for independent subtrees;
   - at least two likely lanes per implementation phase unless it is a preamble or interface-only phase;
   - explicit `Depends on` and `Produces` entries.
5. Add:
   - `Top Interface-Freeze Gates`;
   - `Phase Dependency DAG`;
   - `Execution Notes` that name which phases can be planned or executed concurrently;
   - end-to-end `Verification` commands.
6. Validate mechanically, then by eye:
   - run `phase-loop validate-roadmap <output-path>` (or `python3 -m phase_loop_runtime.roadmap_lint <output-path>`) — always available wherever the phase-loop runtime is installed; it mechanically checks stable headings, unique aliases, acyclic DAG, IF-gate reconciliation, and the lane-count hint. Fix every reported issue, then confirm by eye:
   - stable headings are present;
   - aliases are unique;
   - dependency graph is acyclic;
   - every produced gate has a producing phase;
   - append mode did not silently rewrite old phases.

## Validator Format Contract

`phase-loop validate-roadmap` (the `phase_loop_runtime.roadmap_lint` module) parses by regex on stable headings, not a full Markdown parser, so these formatting rules are load-bearing — a violation drops the phase, mis-parses a field, or trips a structural check rather than warning politely:

- Phase heading shape is `### Phase N — <Name> (<ALIAS>)`. The alias must match `[A-Za-z0-9]+` — letters and digits only, no spaces, hyphens, underscores, or punctuation — and nothing may follow the closing `)` on that line. Trailing decoration after `(ALIAS)` makes the heading malformed. Use UPPERCASE aliases (`SKILLREF`, `P2A`), the convention every roadmap follows: a lowercase alias parses in the heading but its `Depends on` references will not resolve, because the dependency parser uppercases the tokens it reads while the phase's own alias is compared as written.
- A malformed heading cascades — fix the heading first. A heading that fails the phase regex is not parsed as a phase at all, so its fields, alias, `Depends on`, and produced gates disappear and downstream checks (unknown-alias, IF-gate reconciliation, DAG acyclicity) light up with secondary errors. When a heading error appears, correct it and re-run before chasing the rest.
- Each `**Field**` label sits on its own line, with the field body on the following lines. `**Objective** text on the same line` is not recognized and reads as a missing field.
- Lists are bulleted: `Key files` uses `- ` bullets and `Exit criteria` uses `- [ ]` / `- [x]` checkboxes. Prose in place of bullets reads as empty.
- Each exit-criterion leads with a stable goal ID `EC-<ALIAS>-<N>` (alias = the phase alias): `- [ ] EC-<ALIAS>-1 — <assertion>`. A downstream plan REFERENCES the ID instead of restating (and drifting from) the goal. Rules: **all-or-none per phase** (every criterion carries an ID or none do — a mixed phase is a `phase-loop validate-roadmap` (H) error); **unique + alias-scoped**; **gaps allowed — never reuse or renumber** a deleted ID (a plan references it by ID). Runtime `goal-coverage` checks each ID is referenced by ≥1 plan acceptance item.
- Every implementation phase declares a lane hint in `Scope notes` (or a `**Lanes**` block): a literal such as `decompose into N lanes`, `Single lane` (with justification), or partition words (`disjoint`, `owns`, `partition`, `lane A`/`lane B`), unless the phase is marked preamble/interface-only.
- Required top-level headings, unique aliases, non-decreasing phase numbers, `IF-0-<ALIAS>-<n>` gate IDs reconciled with `Produces`, `(none)` roots, and an acyclic DAG are enforced too. Run the validator and fix every reported issue before hand review.

## Artifact Contract

Use this shape so `<harness>-plan-phase` can parse it:

```markdown
# Phase roadmap v<N>

## Context

## Architecture North Star

## Assumptions

## Non-Goals

## Cross-Cutting Principles

## Top Interface-Freeze Gates
- IF-0-<ALIAS>-<N> — <frozen contract>

## Phases

### Phase N — <Name> (<ALIAS>)
**Objective**

**Exit criteria**
- [ ] EC-<ALIAS>-1 — <testable criterion>

**Scope notes**

**Non-goals**

**Key files**

**Depends on**
- (none)

**Produces**
- IF-0-<ALIAS>-<N> — <contract>

## Phase Dependency DAG

## Execution Notes

## Verification
```



## Spec Delta Policy

Every roadmap phase must declare its expected `spec_delta_closeout.v1` policy. Add a phase-local `**Spec closeout policy**` block that names `schema: spec_delta_closeout.v1`, the expected decision (`no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`), target surfaces, evidence paths, `redaction_posture: metadata_only`, and any non-human `blocker_class=contract_bug` routing for missing or malformed evidence. Use `no_spec_delta` for phases that do not change reusable specs, `dotfiles_skill_source_update` for dotfiles-owned skill/source updates, and downstream routing decisions such as `governed_pipeline_refresh` or `mirror_cutover_required` only as metadata-only deferrals, not as write authorization.

## Verification Contract

Roadmap phases must set the expectation that downstream plans include machine-checkable verification commands and an effective `automation.suite_command`. If a phase depends on operational evidence that cannot be machine-checked directly, name the operational evidence artifact and the runner-stamped amendment mechanism that records it. proxy evidence requires a roadmap amendment before any downstream plan treats it as a gate verdict.

## Closeout

In Default mode, write the roadmap with the active session's file-editing tool, then run `git status --short -- <artifact>`. If the artifact is untracked or modified and the user did not explicitly forbid staging, run `git add <artifact>` and include the `_reviews.md` sibling if one was produced. Rerun `git status --short -- <artifact>` and report `Artifact state: staged|tracked|modified|unstaged|blocked`. Do not commit unless the user asked for a commit.

Before final response and handoff, choose the next phase to plan from the roadmap DAG. If at least one phase is ready, report `Next phase: <alias> - <phase name>` and `Next command: <harness>-plan-phase <artifact> <alias>`. If no phase should be planned next, report `Next phase: none - <reason>` and `Next command: none - <reason>`.

Add a machine-readable automation handoff that agrees with the human-readable next step fields. Closeout payload shape is defined by `EmitPhaseCloseout` in `phase_loop_runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

If a roadmap edit changes downstream scope, amend the nearest downstream phase
that is not already executing and treat any older downstream phase plan or
handoff as stale until it is regenerated.

Before final response, write a reflection for every non-trivial run. Write it to `resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/reflections/<repo_hash>/<branch_slug>/<run_id>.md`. The reflection must include `## Run context` with skill name, ISO timestamp, repo, branch, commit, and artifact path if any, followed by `## What worked`, `## What didn't`, and `## Improvements to SKILL.md`. skip only when no artifact was produced AND no decision was made AND the run was pure inspection.

Resolve closeout writes through the `phase_loop_runtime.skill_paths` resolver as the primary source — `resolve_handoff_root(repo)` for the handoff root and `resolve_reflection_root(skill_name)` for reflection roots; fall back to the repo-local `shared/phase-loop/handoff_path.py` resolver only when `phase_loop_runtime` is not importable. Legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use Harness paths only:

- Reflection: `resolve_skill_bundle_root("codex")/<harness>-phase-roadmap-builder/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-phase-roadmap-builder/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-phase-roadmap-builder/latest.md`

Handoff frontmatter must include `from: <harness>-phase-roadmap-builder`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, `artifact:`, `artifact_state:`, `next_skill:`, `next_command:`, and `next_phase:`. Update `latest.md` with the same handoff content.
