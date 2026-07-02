---
phase_loop_plan_version: 1
phase: PNLREDACT
roadmap: specs/phase-plans-v4.md
roadmap_sha256: 906d5d558f4b713abeda01b9c1e443ab09bf9d7203cee49777d7a92fde2f4261
---

# PNLREDACT: Dotfiles Redaction And Fleet Cutover

## Context

Phase `PNLREDACT` follows the completed `PNLSKILL` package work. `agent-harness` now owns the runtime primitive and canonical advisor-panel skill source. Dotfiles should no longer carry an independent advisor-panel implementation; it may retain only redacted compatibility/install glue that points at the agent-harness source of truth.

## Interface Freeze Gates
- [ ] IF-0-PNLREDACT-5 — Dotfiles cutover contract: dotfiles has no standalone advisor-panel scripts/reference implementation, bootstrap exposes advisor-panel through the pinned agent-harness skill source or a thin compatibility shim, and cutover evidence is metadata-only.

## Lane Index & Dependencies

SL-0 — Dotfiles advisor-panel redaction
  Depends on: (none)
  Blocks: SL-1
  Parallel-safe: no
SL-1 — Cutover docs and smoke reducer
  Depends on: SL-0
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Dotfiles Advisor-Panel Redaction
- **Scope**: Remove dotfiles' standalone advisor-panel implementation while preserving the unprefixed compatibility skill as a shim.
- **Owned files**: `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/SKILL.md`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/scripts/run_cli_panels.sh`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/scripts/run_claude_leg.sh`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/references/capability-matrix.md`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/bootstrap.sh`
- **Interfaces provided**: `IF-0-PNLREDACT-5`
- **Interfaces consumed**: `IF-0-PNLSKILL-4`, `(pre-existing)` dotfiles bootstrap agent-harness skill install block
- **Parallel-safe**: no
- **Tasks**:
  - impl: Replace `shared/skills/advisor-panel/SKILL.md` with a thin compatibility shim that points to `agent-harness` `phase_loop_runtime.panel_invoker` and harness-prefixed advisor-panel skills.
  - impl: Delete dotfiles-owned advisor-panel scripts and reference implementation files.
  - impl: Update bootstrap comments or skip logic only as needed to make clear the shared advisor-panel path is compatibility glue, not source-of-truth implementation.
  - verify: `bash -n bootstrap.sh`
  - verify: `test ! -e shared/skills/advisor-panel/scripts/run_cli_panels.sh && test ! -e shared/skills/advisor-panel/scripts/run_claude_leg.sh`

### SL-1 — Cutover Docs And Smoke Reducer
- **Scope**: Record the cutover contract and metadata-only smoke evidence.
- **Owned files**: `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/README.md`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/docs/phase-loop/harness-skill-matrix.md`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/docs/phase-loop/harness-substrate-manifest.md`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/docs/phase-loop/advisor-panel-cutover.md`
- **Interfaces provided**: phase evidence for `IF-0-PNLREDACT-5`
- **Interfaces consumed**: `IF-0-PNLREDACT-5`, `(pre-existing)` `IF-0-PNLFEED-3`, `(pre-existing)` `IF-0-PNLSKILL-4`
- **Parallel-safe**: no
- **Tasks**:
  - impl: Update dotfiles documentation to say advisor-panel source lives in agent-harness and dotfiles retains only compatibility/install glue.
  - impl: Add metadata-only cutover evidence citing agent-harness staged-artifact prompt tests and dotfiles redaction checks.
  - verify: `rg -n "run_cli_panels|run_claude_leg" shared/skills/advisor-panel` returns no matches.
  - verify: `PYTHONPATH=/mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime/src python -m pytest /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime/tests/test_panel_invoker_spawn.py -q`

## Dispatch Hints
- preferred executors: `codex`
- allowed executors: `codex`
- fallback executors: `codex`
- required capabilities: `structured_output`

## Execution Policy
- work-unit defaults: work-unit=`lane_execute`, effort=`high`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`cross-repo redaction touches install-facing skill surfaces`
- SL-1: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_reducer`, reason=`docs and smoke reducer`

## Execution Notes

- Dotfiles work must happen in `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630` on branch `codex/advisor-panel-redact-20260630`, not dirty dotfiles `main`.
- Do not delete or rewrite unrelated shared runner skills.
- Do not add secrets, auth payloads, or local environment values to evidence.

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `mirror_cutover_required`
- target surfaces: `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/**`, `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/docs/phase-loop/advisor-panel-cutover.md`
- evidence paths: `plans/phase-plan-v4-PNLREDACT.md`, `specs/phase-plans-v4.md`, dotfiles redaction command output
- redaction posture: `metadata_only`
- downstream handling: `mirror cutover`

## Verification

```bash
cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && bash -n bootstrap.sh
cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && test ! -e shared/skills/advisor-panel/scripts/run_cli_panels.sh && test ! -e shared/skills/advisor-panel/scripts/run_claude_leg.sh
cd /mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630 && if rg -n "run_cli_panels|run_claude_leg" shared/skills/advisor-panel; then exit 1; else echo advisor-panel-shim-has-no-standalone-script-refs; fi
PYTHONPATH=phase-loop-runtime/src python -m pytest phase-loop-runtime/tests/test_panel_invoker_spawn.py -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
```

## Acceptance Criteria
- [ ] Dotfiles no longer owns a standalone advisor-panel implementation under `shared/skills/advisor-panel/scripts` or `shared/skills/advisor-panel/references`.
- [ ] Dotfiles bootstrap/install docs expose advisor-panel from the `agent-harness` source of truth or a thin compatibility shim.
- [ ] `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/shared/skills/advisor-panel/SKILL.md` is compatibility guidance only and names the harness-prefixed agent-harness skill source.
- [ ] `/mnt/workspace/worktrees/dotfiles-advisor-panel-redact-20260630/docs/phase-loop/advisor-panel-cutover.md` records metadata-only smoke evidence proving Gemini and Codex receive compact prompts that point to staged review files.
- [ ] `git diff --check` and the redaction evidence doc contain no secrets or local auth payloads.
