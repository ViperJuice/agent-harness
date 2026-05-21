---
name: plan-detailed
description: "Harness detailed planner for one bounded change. Use when the user wants an immediately implementable plan for a bug fix, small feature, or targeted refactor. Researches the repo with local reads by default, optionally uses explicit explorer subagents, and returns a concise plan with changes, docs impact, verification, and acceptance criteria."
---

# Harness Plan Detailed

Plans one bounded implementation. Use this instead of the phase roadmap pipeline when a single developer or one Harness thread should do the work end to end.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- In Plan Mode, inspect only and return a `<proposed_plan>`.
- In Default mode, write a plan artifact only if the user asked for one.
- In planning-only runs, do not execute tests, builds, formatters, generators, or other verification commands. List the commands in the plan instead. Run verification only when the user explicitly asks for validation or execution.
- Research first. Do not propose changes to files you have not located.
- Use local tools by default: `rg`, `sed`, `find`, `git status`, and targeted file reads.
- Use PMCP for current external docs only when the answer is not in the repo. Prefer Context7 for library/product documentation and only use Bright Data if PMCP exposes it.
- Do not spawn subagents unless the user explicitly asks for agents, delegation, or parallel research.
- Keep the plan bounded. No opportunistic refactors, speculative features, or broad cleanup.

## Inputs

- Task description: explicit user text or prior conversation context.
- Output path: default `plans/detailed-<slug>-<YYYYMMDD-HHMM>.md`.

## Workflow

1. Extract the task. If still unclear after reading local context, ask one concise question.
2. Gather context:
   - `git status --short`;
   - `git log --oneline -5`;
   - repo root;
   - `AGENTS.md` and `CLAUDE.md`, if present;
   - files found by targeted `rg`.
3. Inspect likely implementation and test files.
4. Decide the smallest coherent implementation.
5. Enumerate every change with:
   - file path;
   - entity;
   - action: add, modify, or delete;
   - reason.
6. Document impact:
   - list docs or API/schema files that need updates;
   - if none, state why.
7. Add dependencies, ordering, verification commands, and acceptance criteria.

Do not run the verification commands while planning unless the user explicitly asked for a validation run. Many test tools write caches or require writable temp directories, so executing them can dirty a smoke-test repo even when source files stay unchanged.

## Plan Template

```markdown
# Detailed plan: <one-line task summary>

## Task
<task statement>

## Research summary
<2-5 sentences with important files and patterns>

## Changes

### `<file-path>` (<create|modify|delete>)
- `<entity>` — <add|modify|delete> — <reason>

## Documentation impact
<docs to update, or `None — <reason>.`>

## Dependencies & order
<blocking order and dependencies>

## Verification
<concrete shell/test commands and edge cases>

## Acceptance criteria
- [ ] <testable assertion>
```

## Quality Bar

- Every changed behavior has a verification path.
- Acceptance criteria are testable, not aspirational.
- Plan references concrete files and entities.
- Documentation impact is explicit.
- The plan leaves no implementation decisions open.

## Closeout

If writing an artifact, use `apply_patch`, report the path, and do not commit unless requested.

If writing self-improvement state, resolve handoff writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use Harness paths only:

- Reflection: `resolve_skill_bundle_root("codex")/<harness>-plan-detailed/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-plan-detailed/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-plan-detailed/latest.md`

Handoff frontmatter must include `from: <harness>-plan-detailed`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, and `artifact:`. Update `latest.md` with the same handoff content.
