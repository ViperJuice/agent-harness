---
phase_loop_plan_version: 1
phase: PNLFEED
roadmap: specs/phase-plans-v4.md
roadmap_sha256: 906d5d558f4b713abeda01b9c1e443ab09bf9d7203cee49777d7a92fde2f4261
---

# PNLFEED: Staged Artifact Prompting And CLI Leg Execution

## Context

Phase `PNLFEED` fixes the advisor-panel failure mode where CLI legs received oversized prompt material instead of a compact pointer to staged review files. `PNLFOUND` already froze the panel result/status and timeout-policy vocabulary, so this phase should only change prompt feeding and the focused tests/docs for those behaviors.

## Interface Freeze Gates

- [ ] IF-0-PNLFEED-3 — CLI prompt feeding contract: Codex and Gemini receive compact prompts that reference staged `review-instructions.md` and `review-bundle.md`; no leg depends on `--add-dir` as the only way to see review material, and no prompt embeds the artifact body.

## Lane Index & Dependencies

SL-0 — Staged CLI artifact prompt integrator
  Depends on: (none)
  Blocks: SL-1
  Parallel-safe: no
SL-1 — Contract note and phase verification reducer
  Depends on: SL-0
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Staged CLI Artifact Prompt Integrator

- **Scope**: Feed Codex and Gemini compact prompts that point to staged review files and preserve PNLFOUND status/timeout behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- **Interfaces provided**: `IF-0-PNLFEED-3`, `_render_leg_prompt`
- **Interfaces consumed**: `(pre-existing)` `PanelRequest`, `(pre-existing)` `panel_leg_timeout_seconds`, `(pre-existing)` `_classify_leg`
- **Parallel-safe**: no; all four roadmap concerns share the same `_exec_leg` subprocess boundary.
- **Tasks**:
  - test: Add tests proving Codex and Gemini command prompts reference staged review files, Gemini does not use `--add-dir`, timeout log text includes configured timeout metadata, and large artifacts are represented by deterministic metadata rather than body text.
  - impl: Add a prompt renderer that includes review instructions, staged file names, digest metadata, and size metadata without embedding the artifact body.
  - impl: Use the rendered prompt for Codex and Gemini subprocess calls and remove Gemini's `--add-dir` dependency.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker_spawn.py -q`

### SL-1 — Contract Note And Phase Verification Reducer

- **Scope**: Update the existing model-routing research note and run focused verification for the phase.
- **Owned files**: `docs/research/model-routing-v2-integration.md`
- **Interfaces provided**: phase evidence for `IF-0-PNLFEED-3`
- **Interfaces consumed**: SL-0 feeding contract results
- **Parallel-safe**: no; reducer depends on SL-0.
- **Tasks**:
  - impl: Replace the old `--add-dir`-dependent Gemini note with the staged-file prompt contract and record the remaining Claude-native work as deferred to `PNLCLAUDE`.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-1: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_reducer`, reason=`contract note and verification reducer`

## Execution Notes

- Execute serially. SL-0 is a single-writer integrator lane because the Codex, Gemini, prompt, status, and large-input concerns all touch the same subprocess seam.
- Do not implement the native Claude panel leg in this phase.
- Do not move advisor-panel skill source in this phase.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`, `docs/research/model-routing-v2-integration.md`
- evidence paths: `plans/phase-plan-v4-PNLFEED.md`, `specs/phase-plans-v4.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker_spawn.py -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker_spawn.py -q

## Acceptance Criteria

- [ ] `tests/test_panel_invoker_spawn.py` proves Codex command input references the staged review files without embedding the artifact body.
- [ ] `tests/test_panel_invoker_spawn.py` proves Gemini command input references the staged review files without embedding the artifact body.
- [ ] `tests/test_panel_invoker_spawn.py` proves Gemini panel execution no longer passes `--add-dir`.
- [ ] `tests/test_panel_invoker_spawn.py` proves timeout log text includes the configured timeout without including artifact content.
- [ ] `tests/test_panel_invoker_spawn.py` proves large artifacts use deterministic threshold metadata instead of unbounded prompt growth.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md` passes.
