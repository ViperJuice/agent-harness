---
phase_loop_plan_version: 1
phase: PNLFOUND
roadmap: specs/phase-plans-v4.md
roadmap_sha256: 906d5d558f4b713abeda01b9c1e443ab09bf9d7203cee49777d7a92fde2f4261
---

# PNLFOUND: Panel Contract And Routing Baseline

## Context

Phase `PNLFOUND` freezes the advisor-panel contract and model-routing-v3 baseline before later phases change CLI feeding, Claude native execution, skill source ownership, or dotfiles cutover.

The implementation is intentionally limited to contracts and routing constants:

- canonical panel leg statuses and request/result metadata;
- input-scaled, leg-specific timeout policy;
- Sonnet-family Claude routing to Claude Sonnet 5;
- Gemini implementer/worker eligibility through Gemini 3.5 Flash;
- focused tests and a contract-note update.

## Interface Freeze Gates

- [ ] IF-0-PNLFOUND-1 — Panel request/result contract: `PanelRequest`, `PanelResult`, canonical uppercase `LEG_STATUSES`, per-leg timeout policy, metadata-only redaction posture, and fail-closed degraded/error handling.
- [ ] IF-0-PNLFOUND-2 — Model-routing-v3 policy: Claude implementer routes to `claude-sonnet-5`, Gemini implementer/worker route to `Gemini 3.5 Flash (High)`, and Gemini remains ineligible as max-effort planner of record.

## Lane Index & Dependencies

SL-0 — Panel contract/status surface
  Depends on: (none)
  Blocks: SL-2
  Parallel-safe: mixed
SL-1 — Model-routing-v3 constants/tests
  Depends on: (none)
  Blocks: SL-2
  Parallel-safe: mixed
SL-2 — Contract note and phase verification reducer
  Depends on: SL-0, SL-1
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Panel Contract/Status Surface

- **Scope**: Freeze panel request/result status and timeout metadata without changing the later `PNLFEED` prompt-feeding behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`, `phase-loop-runtime/tests/test_governed_gate_crfixes.py`
- **Interfaces provided**: `IF-0-PNLFOUND-1`, `LEG_STATUSES`, `PanelRequest`, `panel_leg_timeout_seconds`
- **Interfaces consumed**: `(pre-existing)` `PanelLegResult`, `(pre-existing)` `PanelResult`, `(pre-existing)` `terminal_verdict`, `(pre-existing)` `invoke_panel`
- **Parallel-safe**: mixed; safe against SL-1, but single-writer within panel files.
- **Tasks**:
  - test: Update panel invoker tests to assert canonical uppercase statuses, lowercase spawn compatibility, unknown status degradation, and timeout scaling.
  - impl: Add `PanelRequest`, canonical uppercase `LEG_STATUSES`, compatibility normalization for legacy lowercase spawn statuses, explicit `ERROR`, and an input-scaled per-leg timeout helper.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py -q`

### SL-1 — Model-Routing-v3 Constants And Tests

- **Scope**: Freeze current Sonnet/Gemini routing names while preserving the max-effort planner guard.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/profiles.py`, `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py`, `phase-loop-runtime/src/phase_loop_runtime/build_bundle.py`, `skills-src/claude/claude-execute-phase/SKILL.md`, `phase-loop-skills/execute-phase/_overrides/claude/SKILL.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/claude-execute-phase/SKILL.md`, `phase-loop-runtime/tests/test_model_class_policy.py`, `phase-loop-runtime/tests/test_governed_premerge.py`, `phase-loop-runtime/tests/test_route_log.py`, `phase-loop-runtime/tests/test_skills_canon_parity.py`, `phase-loop-runtime/tests/test_phase_loop_launcher.py`
- **Interfaces provided**: `IF-0-PNLFOUND-2`, `CLAUDE_IMPLEMENTER_MODEL`, `GEMINI_FLASH_MODEL`
- **Interfaces consumed**: `(pre-existing)` `resolve_model_class`, `(pre-existing)` `resolve_profile_for_executor`, `(pre-existing)` `_gemini_cli_model`, `(pre-existing)` `max_effort_planner_eligible`
- **Parallel-safe**: mixed; safe against SL-0, but single-writer within model-routing tests.
- **Tasks**:
  - test: Update model-class, governed premerge, route log, skill-neutralization, and Gemini model passthrough expectations.
  - impl: Set Claude implementer model to `claude-sonnet-5`; add Gemini 3.5 Flash implementer/worker routing while keeping planner as `pro` and max-effort planner guard unchanged.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_model_class_policy.py tests/test_governed_premerge.py tests/test_route_log.py tests/test_skills_canon_parity.py tests/test_phase_loop_launcher.py -q`

### SL-2 — Contract Note And Phase Verification Reducer

- **Scope**: Update the existing model-routing research note with the PNLFOUND contract decision and run the focused phase checks.
- **Owned files**: `docs/research/model-routing-v2-integration.md`
- **Interfaces provided**: phase evidence for `IF-0-PNLFOUND-1` and `IF-0-PNLFOUND-2`
- **Interfaces consumed**: SL-0 panel contract results and SL-1 routing results
- **Parallel-safe**: no; reducer lane depends on SL-0 and SL-1.
- **Tasks**:
  - test: Confirm the roadmap still validates.
  - impl: Add a short model-routing-v3 / advisor-panel ownership note describing uppercase panel statuses, Sonnet 5, Gemini 3.5 Flash, and the remaining `PNLFEED` prompt-feed work.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-2: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_reducer`, reason=`contract note and verification reducer`

## Execution Notes

- Execute SL-0 and SL-1 before SL-2. SL-0 and SL-1 are conceptually independent, but this manual run should keep them serial because both update focused tests and the worktree already contains detailed-fix changes.
- Do not change Gemini CLI artifact feeding in this phase; leave `--add-dir` and inline prompt-feed repair to `PNLFEED`.
- Do not implement the native Claude leg in this phase; only freeze the Sonnet 5 routing contract.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/profiles.py`, `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py`, `phase-loop-runtime/src/phase_loop_runtime/build_bundle.py`, `skills-src/claude/claude-execute-phase/SKILL.md`, `phase-loop-skills/execute-phase/_overrides/claude/SKILL.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/claude-execute-phase/SKILL.md`, `docs/research/model-routing-v2-integration.md`
- evidence paths: `plans/phase-plan-v4-PNLFOUND.md`, `specs/phase-plans-v4.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_model_class_policy.py tests/test_governed_premerge.py tests/test_route_log.py tests/test_skills_canon_parity.py tests/test_phase_loop_launcher.py -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py tests/test_model_class_policy.py tests/test_governed_premerge.py tests/test_route_log.py tests/test_skills_canon_parity.py tests/test_phase_loop_launcher.py -q

## Acceptance Criteria

- [ ] `PanelLegResult.status` is canonical uppercase and rejects unknown statuses, while `invoke_panel` still normalizes legacy lowercase spawn output.
- [ ] Panel contract tests cover `OK`, `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE`.
- [ ] Panel timeout policy scales by input size and can produce bounded high-effort timeouts up to approximately 1800 seconds.
- [ ] Claude implementer/model-policy execution resolves to `claude-sonnet-5`.
- [ ] Gemini implementer and worker model classes resolve to `Gemini 3.5 Flash (High)`, while Gemini planner remains `pro`.
- [ ] Gemini remains ineligible as the max-effort planner of record.
- [ ] Focused panel/routing tests and roadmap validation pass.
