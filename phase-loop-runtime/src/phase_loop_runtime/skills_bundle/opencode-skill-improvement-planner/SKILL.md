---
name: opencode-skill-improvement-planner
description: "OpenCode skill feedback aggregator. Use when the user wants to review OpenCode skill reflections, aggregate recurring feedback, or plan improvements to `<harness>-*` skills. Produces an improvement plan for <harness>-skill-editor and does not edit skills itself."
---

# OpenCode Skill Improvement Planner

Aggregates reflection files for OpenCode skills and produces a structured improvement plan. It does not edit skills; `<harness>-skill-editor` applies the plan.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- OpenCode meta-skill source changes move through three tiers: canonical source at `<harness>-config/skills/<harness>-<skill>/SKILL.md`, harness-neutral bundle at `vendor/phase-loop-skills/<bare-skill>/SKILL.md`, and installed runtime roots at `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`, and `~/.opencode/skills/`.
- The harness-neutral bundle is currently bundle-derived-from-codex. Leave `vendor/phase-loop-skills/` and installed runtime roots stale until the end-of-v36 cutover; the cutover regenerates the bundle from the codex-derived source path and runs `./bootstrap.sh`, which installs the bundle with `python3 -m phase_loop_runtime.cli install --source vendor/phase-loop-skills --symlink --apply`.
- Planning only. Do not modify `SKILL.md` files.
- Scan OpenCode skill state under `resolve_skill_bundle_root("opencode")/<skill>/reflections/**`.
- Also scan the codex-derived source-controlled reflection root `resolve_skill_bundle_root("codex")/<skill>/reflections/**`.
- Exclude any path with an `archive/` component.
- Follow the recursive reflection rules in `<harness>-config/shared/runtime-state.md`.
- Act only on recurring evidence unless the user explicitly asks to apply one-off feedback.
- Do not spawn subagents unless the user explicitly asks for delegated analysis.

## Pipeline

The three-tier pipeline is canonical source -> harness-neutral bundle -> installed runtime roots. Edit and plan against `<harness>-config/skills/<harness>-<skill>/SKILL.md`; do not treat bundle or installed copies as source of truth. Bundle regeneration plus `./bootstrap.sh` is the explicit cutover trigger after approved canonical edits.

## Inputs

- `--target <skill-name>`: plan for one skill.
- `--min-reflections <N>`: default `2`.
- `--output <path>`: default `resolve_skill_bundle_root("opencode")/<harness>-skill-improvement-planner/plans/plan-v<N>-<ISO>.md`.

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

## Plan Format

```markdown
---
from: <harness>-skill-improvement-planner
timestamp: <ISO>
min_reflections: <N>
reflections_consumed:
  - <absolute path>
---

# OpenCode skill improvement plan — <ISO>

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

Closeout payload shape is defined by `EmitPhaseCloseout` in `phase_loop_runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

In Default mode, write the plan only if the user asked for an artifact. Otherwise summarize the recommendations. Do not archive reflections; that is the editor's job.

If writing self-improvement state, resolve handoff writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use OpenCode paths only:

- Reflection: `resolve_skill_bundle_root("opencode")/<harness>-skill-improvement-planner/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-skill-improvement-planner/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-skill-improvement-planner/latest.md`

Handoff frontmatter must include `from: <harness>-skill-improvement-planner`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, and `artifact:`. Update `latest.md` with the same handoff content.
