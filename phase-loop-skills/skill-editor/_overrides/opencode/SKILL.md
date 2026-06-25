---
name: skill-editor
description: "OpenCode skill editor. Use when the user wants to apply an improvement plan produced by <harness>-skill-improvement-planner to OpenCode skill files. Edits only targeted `<harness>-*` skills by default, archives consumed reflections after successful edits, and uses structured file-editing tools for manual changes."
---

# OpenCode Skill Editor

Applies a structured improvement plan to OpenCode skill files. It is deliberately narrower than arbitrary skill editing: it consumes plans from `<harness>-skill-improvement-planner` and updates the named target skills.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- OpenCode meta-skill source changes move through three tiers: canonical source at `<harness>-config/skills/<harness>-<skill>/SKILL.md`, harness-neutral bundle at `vendor/phase-loop-skills/<bare-skill>/SKILL.md`, and installed runtime roots at `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`, and `~/.opencode/skills/`.
- The harness-neutral bundle is currently bundle-derived-from-codex. Leave `vendor/phase-loop-skills/` and installed runtime roots stale until the end-of-v36 cutover; after edits, report bundle regeneration from the codex-derived source path plus `./bootstrap.sh` as the required follow-up. `./bootstrap.sh` installs the bundle with `python3 -m phase_loop_runtime.cli install --source vendor/phase-loop-skills --symlink --apply`.
- Read the improvement plan and target `SKILL.md` before editing.
- Use the active session's file-editing tool for manual edits.
- Edit only skills named by the plan.
- Default target set is `<harness>-*` skills. Do not edit the original Claude-oriented skills unless the plan explicitly names them and the user confirms that scope.
- Preserve skill frontmatter validity.
- Do not push or commit unless the user explicitly requests it.
- Archive consumed reflections only after all recommendations citing them succeeded.

## Pipeline

The three-tier pipeline is canonical source -> harness-neutral bundle -> installed runtime roots. Edit only `<harness>-config/skills/<harness>-<skill>/SKILL.md` during this skill-editor workflow; bundle regeneration plus `./bootstrap.sh` is the explicit cutover trigger after approved canonical edits.

## Inputs

- Plan path: explicit path, or latest `resolve_skill_bundle_root("opencode")/<harness>-skill-improvement-planner/plans/plan-v*.md`.
- `--dry-run`: parse and report intended edits without changing files.

If no plan path is explicit, first check the current repo and branch handoff from `<harness>-skill-improvement-planner` using `<harness>-config/shared/runtime-state.md`: read the repo-local handoff resolver target `.dev-skills/handoffs/<harness>-skill-improvement-planner/latest.md`, validate `from`, `repo`, `repo_root`, `branch`, `branch_slug`, `commit`, and `artifact`, then use the artifact only if it exists under the current repo root. Ignore missing or mismatched handoffs unless the user explicitly asks to reuse cross-branch state.

## Workflow

1. Resolve and read the plan.
2. Parse:
   - `reflections_consumed`;
   - recommendations by skill;
   - cross-cutting recommendations;
   - contradictions.
3. If contradictions exist, stop and ask the user how to resolve them unless the plan already contains a resolution.
4. Validate target skills:
   - source path under `<harness>-config/skills/<harness>-<skill>/SKILL.md` when working in this dotfiles repo;
   - symlink/runtime path under `resolve_skill_bundle_root("opencode")/<skill>/SKILL.md` only when no source path exists.
5. For `--dry-run`, report the target files and recommendation summaries, then stop.
6. Apply recommendations:
   - group changes per target skill to avoid conflicting edits;
   - keep `SKILL.md` concise;
   - move lengthy examples into `references/`;
   - update `agents/openai.yaml` when display metadata becomes stale.
7. Validate:
   - YAML frontmatter parses;
   - `name` matches the skill directory intent;
   - `description` clearly states trigger scope and non-scope;
   - referenced files exist.
8. Archive reflections:
   - move successfully consumed reflection files to an `archive/` directory under the same repo and branch subtree;
   - leave reflections in place for failed recommendations.

## Failure Policy

- Malformed plan: stop and report exact parse failure.
- Missing target skill: mark that recommendation failed; continue only if other independent targets remain.
- Patch conflict: re-read the file, adjust once, then report if still blocked.
- Validation failure: fix if local to the edit; otherwise roll forward with a clear report and do not archive affected reflections.

## Closeout

Closeout payload shape is defined by `EmitPhaseCloseout` in `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.

Report:

- applied recommendations;
- skipped or failed recommendations;
- files changed;
- reflections archived;
- validation commands run.

If writing self-improvement state, resolve handoff writes through `shared/phase-loop/handoff_path.py` and the repo-local handoff resolver; legacy harness handoff roots are read only for migration. Follow `<harness>-config/shared/runtime-state.md` and use OpenCode paths only:

- Reflection: `resolve_skill_bundle_root("opencode")/<harness>-skill-editor/reflections/<repo_hash>/<branch_slug>/<run_id>.md`
- Handoff: `<repo>/.dev-skills/handoffs/<harness>-skill-editor/<run_id>.md`
- Latest handoff pointer: `<repo>/.dev-skills/handoffs/<harness>-skill-editor/latest.md`

Handoff frontmatter must include `from: <harness>-skill-editor`, `timestamp:`, `repo:`, `repo_root:`, `branch:`, `branch_slug:`, `commit:`, `run_id:`, and `artifact:`. Update `latest.md` with the same handoff content.
