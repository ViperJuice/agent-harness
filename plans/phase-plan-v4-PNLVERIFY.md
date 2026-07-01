---
phase_loop_plan_version: 1
phase: PNLVERIFY
roadmap: specs/phase-plans-v4.md
roadmap_sha256: 906d5d558f4b713abeda01b9c1e443ab09bf9d7203cee49777d7a92fde2f4261
---

# PNLVERIFY: Live Default Verification And Release Closure

## Context

Phase `PNLVERIFY` follows the completed runtime, skill-source, and dotfiles-redaction phases. It introduces no new runtime behavior. Its job is to prove the advisor-panel roadmap is release-ready with focused tests, the full runtime suite, dotfiles cutover checks, and a metadata-only evidence document suitable for issue closeout.

## Interface Freeze Gates
- [ ] IF-0-PNLVERIFY-6 — Release verification matrix: focused panel/launcher/routing/skill checks, full runtime suite, staged-artifact smoke evidence, dotfiles redaction evidence, and issue-closeout-ready summary all pass or are explicitly classified.

## Lane Index & Dependencies

SL-0 — Final verification evidence reducer
  Depends on: (none)
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Final Verification Evidence Reducer
- **Scope**: Run final verification across agent-harness and dotfiles and write release-closeout evidence.
- **Owned files**: `docs/research/advisor-panel-roadmap-v4-verification.md`, `plans/manifest.json`, `phase-loop-runtime/src/phase_loop_runtime/skill_inventory.py`, `phase-loop-runtime/src/phase_loop_runtime/_contract_docs/phase-loop/harness-skill-matrix.md`
- **Interfaces provided**: `IF-0-PNLVERIFY-6`
- **Interfaces consumed**: `(pre-existing)` `IF-0-PNLFOUND-1`, `(pre-existing)` `IF-0-PNLFOUND-2`, `(pre-existing)` `IF-0-PNLFEED-3`, `(pre-existing)` `IF-0-PNLCLAUDE-1`, `(pre-existing)` `IF-0-PNLCLAUDE-2`, `(pre-existing)` `IF-0-PNLSKILL-4`, `(pre-existing)` `IF-0-PNLREDACT-5`
- **Parallel-safe**: no
- **Tasks**:
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py tests/test_governed_review.py tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py tests/test_model_class_policy.py tests/test_route_log.py tests/test_phase_loop_launcher.py -q`
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q`
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md`
  - verify: `cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && bash -n bootstrap.sh && git diff --check`
  - verify: `cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && test ! -e shared/skills/advisor-panel/scripts/run_cli_panels.sh && test ! -e shared/skills/advisor-panel/scripts/run_claude_leg.sh`
  - impl: Write metadata-only verification evidence summarizing command results, remaining dirty paths, and issue-closeout-ready notes.

## Dispatch Hints
- preferred executors: `codex`
- allowed executors: `codex`
- fallback executors: `codex`
- required capabilities: `structured_output`

## Execution Policy
- work-unit defaults: work-unit=`phase_verify`, effort=`high`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`phase_verify`, reason=`final release evidence and full-suite verification`

## Execution Notes

- Do not introduce new behavior in this phase.
- If a real live frontier-panel smoke is not run, the evidence document must classify the proof as structured status evidence from tests rather than model-output proof.
- Do not include secrets, local auth values, provider transcripts, or environment payloads.

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `docs/research/advisor-panel-roadmap-v4-verification.md`, `plans/manifest.json`
- evidence paths: `plans/phase-plan-v4-PNLVERIFY.md`, `specs/phase-plans-v4.md`, full runtime pytest output, dotfiles redaction output
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_governed_gate_crfixes.py tests/test_governed_review.py tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py tests/test_model_class_policy.py tests/test_route_log.py tests/test_phase_loop_launcher.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && bash -n bootstrap.sh && git diff --check
```

## Acceptance Criteria
- [ ] The focused pytest command in `## Verification` passes for panel, launcher, routing, and skill parity tests.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q` passes.
- [ ] `docs/research/advisor-panel-roadmap-v4-verification.md` cites `test_panel_invoker_spawn.py` staged-artifact prompt evidence for Codex and Gemini.
- [ ] `docs/research/advisor-panel-roadmap-v4-verification.md` cites dotfiles redaction checks proving no divergent advisor-panel implementation remains under `shared/skills/advisor-panel/scripts`.
- [ ] `docs/research/advisor-panel-roadmap-v4-verification.md` contains issue-closeout-ready notes for issues #36 and #135.
