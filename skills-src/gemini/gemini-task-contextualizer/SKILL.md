---
name: gemini-task-contextualizer
description: "Gemini subagent briefing guide. Use before spawning Gemini explorer or worker subagents, especially for parallel research or implementation. Produces concise prompts with file paths, architecture context, ownership boundaries, expected output, and non-reversion rules."
---

# Gemini Task Contextualizer

Use this before any Gemini subagent delegation call. Subagents need concrete context and boundaries; broad prompts waste time and increase the chance of conflicting edits.

## When to Use

- The user explicitly asked for agents, subagents, delegation, or parallel work.
- You are about to delegate to a read-only or implementation agent.
- You need several independent investigations or disjoint implementation lanes.

Do not spawn a subagent only because a task is large. If the next critical-path step depends on the answer, do it locally.

## Mandatory Brief Contents

Every subagent prompt must include:

- **Goal**: one sentence.
- **Starting files**: exact paths or narrow globs.
- **Architecture context**: one or two sentences explaining how the files fit.
- **Scope boundary**: what not to touch.
- **Ownership**: files the agent may edit, or "read-only" for explorers.
- **Related files**: tests, configs, types, fixtures.
- **Expected output**: answer format or changed-file report.
- **Coordination rule**: "You are not alone in the codebase; do not revert edits made by others."

## Explorer Brief

Use the available read-only agent role for reconnaissance questions. Ask a specific question and require file:line evidence.

```text
Goal: Map how <feature/module> works so the main thread can plan safely.

Starting files:
- <path>

Architecture context:
<1-2 sentences>

Scope:
Read-only. Do not edit files. Do not run mutating commands.

Questions:
- <specific question>
- <specific constraint/risk to check>

Expected output:
Return concise findings with file:line references, reusable patterns, files likely to change, and hidden coupling.
```

## Worker Brief

Use the available implementation agent role only when write ownership is disjoint.

```text
Goal: Implement <bounded task>.

Owned files:
- <paths/globs the worker may edit>

Read-only related files:
- <paths/globs the worker may inspect but not modify>

Architecture context:
<1-2 sentences>

Scope:
Only edit owned files. Do not revert unrelated changes. You are not alone in the codebase; adjust to peer changes instead of overwriting them.

Tasks:
- <test task>
- <implementation task>
- <verification task>

Expected output:
List changed paths, tests run, failures, blockers, and any assumptions.
```

## Reference

For copy-ready variants, read `references/subagent-briefs.md`.

## Runtime State

This skill normally writes no reflection or handoff. If a future workflow adds self-improvement state, follow `gemini-config/shared/runtime-state.md` and use Gemini paths only:

- Reflection: `resolve_skill_bundle_root("gemini")/gemini-task-contextualizer/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `resolve_handoff_root(repo)/gemini-task-contextualizer/<run_id>.md`
- Latest handoff pointer: `resolve_handoff_root(repo)/gemini-task-contextualizer/latest.md`

Handoff frontmatter must include `from: gemini-task-contextualizer`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, and `artifact:`. Update `latest.md` with the same handoff content.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.
