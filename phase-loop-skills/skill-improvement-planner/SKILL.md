---
name: skill-improvement-planner
description: "Harness skill feedback aggregator. Use when the user wants to review Harness skill reflections, aggregate recurring feedback, or plan improvements to `<harness>-*` skills. Produces an improvement plan for <harness>-skill-editor and does not edit skills itself."
---

# Harness Skill Improvement Planner

Aggregates reflection files for Harness skills and produces a structured improvement plan. It does not edit skills; `<harness>-skill-editor` applies the plan.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Planning only. Do not modify `SKILL.md` files.
- Prefer Harness skill state under `resolve_skill_bundle_root("codex")/<skill>/reflections/**`.
- Also inspect source-controlled reflections under `<harness>-config/skills/<harness>-*/reflections/**` when present.
- Exclude any path with an `archive/` component.
- Follow the recursive reflection rules in `<harness>-config/shared/runtime-state.md`.
- Act only on recurring evidence unless the user explicitly asks to apply one-off feedback.
- Do not spawn subagents unless the user explicitly asks for delegated analysis.

## Inputs

- `--target <skill-name>`: plan for one skill.
- `--min-reflections <N>`: default `2`.
- `--output <path>`: default `resolve_skill_bundle_root("codex")/<harness>-skill-improvement-planner/plans/plan-v<N>-<ISO>.md`.

When invoked by `<harness>-phase-loop maintain-skills`, planner output is the default result. Do not edit skills or call `<harness>-skill-editor` from this planner turn.

## Workflow

1. Enumerate unarchived reflections for:
   - `<harness>-phase-roadmap-builder`
   - `<harness>-plan-phase`
   - `<harness>-execute-phase`
   - `<harness>-task-contextualizer`
   - `<harness>-skill-improvement-planner`
   - `<harness>-skill-editor`
   - `<harness>-plan-detailed`
2. Parse each reflection:
   - skill name;
   - version or timestamp;
   - `What worked`;
   - `Improvements to SKILL.md`;
   - raw body when headings are missing.
3. Gate on minimum evidence:
   - zero reflections: report that there is nothing to aggregate;
   - fewer than `--min-reflections` for a skill: record as skipped.
4. Aggregate recommendations:
   - group recurring themes by skill;
   - separate actionable changes from speculative notes;
   - flag contradictions instead of resolving them silently;
   - reject repo-specific recommendations unless the target skill is intentionally repo-specific.
5. Produce a plan with:
   - frontmatter listing consumed reflection paths;
   - recommendations by skill;
   - cross-cutting recommendations;
   - speculative notes;
   - contradictions;
   - archival directive for `<harness>-skill-editor`.
   - runner handoff fields showing the approved next command, or `none` when no edits are approved.

## Plan Format

```markdown
---
from: <harness>-skill-improvement-planner
timestamp: <ISO>
min_reflections: <N>
reflections_consumed:
  - <absolute path>
---

# Harness skill improvement plan — <ISO>

## Summary

## Recommendations by skill

### <skill-name>
- **Change**: <directive>
  - **Rationale**: <evidence>
  - **Supporting reflections**: <ids>

## Cross-cutting recommendations

## Speculative / low-confidence notes

## Contradictions surfaced

## Archival directive for <harness>-skill-editor
```

## Closeout

In Default mode, write the plan only if the user asked for an artifact. Otherwise summarize the recommendations. Do not archive reflections; that is the editor's job.

For `maintain-skills` planner-only runs, report `<harness>-skill-editor --improvement-plan <path> --allow-skill <<harness>-* skill>` only as the explicit follow-on command. Do not imply that editor execution is automatic.

If writing self-improvement state, resolve handoff writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use Harness paths only:

- Reflection: `resolve_skill_bundle_root("codex")/<harness>-skill-improvement-planner/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-skill-improvement-planner/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-skill-improvement-planner/latest.md`

Handoff frontmatter must include `from: <harness>-skill-improvement-planner`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, and `artifact:`. Update `latest.md` with the same handoff content.
