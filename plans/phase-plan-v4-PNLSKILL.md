---
phase_loop_plan_version: 1
phase: PNLSKILL
roadmap: specs/phase-plans-v4.md
roadmap_sha256: 906d5d558f4b713abeda01b9c1e443ab09bf9d7203cee49777d7a92fde2f4261
---

# PNLSKILL: Source-First Advisor Panel Skill Bundle

## Context

Phase `PNLSKILL` follows the completed `PNLCLAUDE` runtime primitive. The advisor-panel implementation now lives in `phase_loop_runtime.panel_invoker`; this phase makes `agent-harness` the canonical source for the user-facing advisor-panel skill and packages it through the existing `skills-src/` -> `phase-loop-skills/` -> runtime `skills_bundle/` pipeline.

## Interface Freeze Gates
- [ ] IF-0-PNLSKILL-4 — Canonical advisor-panel skill source layout: every active harness has `<harness>-advisor-panel` source under `skills-src/`, `advisor-panel` is a required installed skill, and regenerated committed/package bundles contain the advisor-panel skill without dotfiles-owned scripts.

## Lane Index & Dependencies

SL-0 — Advisor-panel skill source and generated bundle
  Depends on: (none)
  Blocks: SL-1
  Parallel-safe: no
SL-1 — Skill ownership docs and verification reducer
  Depends on: SL-0
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Advisor-Panel Skill Source And Generated Bundle
- **Scope**: Add advisor-panel to the canonical skill source set and regenerate committed and packaged bundles.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/skill_install.py`, `skills-src/claude/claude-advisor-panel/SKILL.md`, `skills-src/codex/codex-advisor-panel/SKILL.md`, `skills-src/gemini/gemini-advisor-panel/SKILL.md`, `skills-src/opencode/opencode-advisor-panel/SKILL.md`, `phase-loop-skills/advisor-panel/**`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/claude-advisor-panel/**`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/codex-advisor-panel/**`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/gemini-advisor-panel/**`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/opencode-advisor-panel/**`, `phase-loop-runtime/tests/test_skills_canon_parity.py`, `phase-loop-runtime/tests/test_skills_bundle_drift.py`
- **Interfaces provided**: `IF-0-PNLSKILL-4`, `REQUIRED_SKILLS` includes `advisor-panel`
- **Interfaces consumed**: `(pre-existing)` `build_bundle`, `(pre-existing)` `install_skills`, `(pre-existing)` `panel_invoker.invoke_panel`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add parity coverage proving `advisor-panel` is a required skill with source, committed bundle, and packaged bundle entries.
  - impl: Add harness-specific advisor-panel canonical source skill files that direct agents to the runtime primitive/governed phase-loop path and forbid dotfiles script ownership.
  - impl: Add `advisor-panel` to `REQUIRED_SKILLS`.
  - impl: Run `python phase-loop-runtime/scripts/regenerate_skills_bundle.py` and `python phase-loop-runtime/scripts/sync_skills_bundle.py`.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q`

### SL-1 — Skill Ownership Docs And Verification Reducer
- **Scope**: Update canonical skill-source documentation and run the phase verification slice.
- **Owned files**: `docs/phase-loop/skills-canonical-source.md`, `phase-loop-skills/README.md`
- **Interfaces provided**: phase evidence for `IF-0-PNLSKILL-4`
- **Interfaces consumed**: `IF-0-PNLSKILL-4`
- **Parallel-safe**: no
- **Tasks**:
  - impl: Name advisor-panel as part of the canonical source and installed skill set.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py tests/test_phase_loop_runtime_boundary.py -q`
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md`

## Dispatch Hints
- preferred executors: `codex`
- allowed executors: `codex`
- fallback executors: `codex`
- required capabilities: `structured_output`

## Execution Policy
- work-unit defaults: work-unit=`lane_execute`, effort=`high`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`skill packaging touches source and generated bundle surfaces`
- SL-1: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_reducer`, reason=`docs and verification reducer`

## Execution Notes

- Do not copy dotfiles advisor-panel scripts into `agent-harness`; the skill must be thin over the runtime primitive and phase-loop governed flow.
- Treat `phase-loop-skills/advisor-panel/**` and runtime `skills_bundle/*advisor-panel/**` as generated outputs.

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `dotfiles_skill_source_update`
- target surfaces: `skills-src/*/*advisor-panel/SKILL.md`, `phase-loop-skills/advisor-panel/**`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor-panel/**`, `docs/phase-loop/skills-canonical-source.md`
- evidence paths: `plans/phase-plan-v4-PNLSKILL.md`, `specs/phase-plans-v4.md`, focused pytest output
- redaction posture: `metadata_only`
- downstream handling: `mirror cutover`

## Verification

```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py tests/test_phase_loop_runtime_boundary.py -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
```

## Acceptance Criteria
- [ ] `agent-harness` contains canonical advisor-panel skill source under all active `skills-src/<harness>/` roots.
- [ ] `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor-panel/SKILL.md` tells agents to use the runtime primitive or governed phase-loop flow and avoids duplicating dotfiles scripts.
- [ ] `phase-loop-runtime/tests/test_skills_canon_parity.py` and `phase-loop-runtime/tests/test_skills_bundle_drift.py` pass after regeneration.
- [ ] `phase-loop-runtime/tests/test_skills_canon_parity.py` covers advisor-panel source and packaged output.
- [ ] `docs/phase-loop/skills-canonical-source.md` names advisor-panel ownership explicitly.
