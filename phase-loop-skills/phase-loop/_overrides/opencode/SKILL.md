---
name: phase-loop
description: "OpenCode bridge for the repo-local phase-loop runner. Use when the user wants phase-loop status, resume, bounded run, dry-run, or monitor flows from OpenCode."
---

# OpenCode Phase Loop

Thin bridge for the repo-local phase-loop runner. This skill points OpenCode
operators at the shared loop state and command surface; it does not reimplement
phase selection, reconciliation, event writing, or execution logic.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Use the repo-local phase-loop CLI or wrapper documented by the repo; do not invent a second runner.
- Preserve manual OpenCode use of `<harness>-phase-roadmap-builder`, `<harness>-plan-phase`, and `<harness>-execute-phase`.
- Inspect `shared/phase-loop/protocol.md` and `<harness>-config/shared/runtime-state.md` before describing artifact or blocker semantics.
- Before reporting a human-required access blocker, inspect repo-local docs/config and safe metadata from available CLIs. Never print secret values.

## Inputs

- `handoff`
- `status`
- `monitor`
- `resume`
- `run`
- `dry-run`
- `sync-skills`

Keep `run` bounded. Prefer `--max-phases 1` unless the user names another
bound.

## Shared State Surface

- `.phase-loop/state.json`
- `.phase-loop/events.jsonl`
- `.phase-loop/tui-handoff.md`
- `.phase-loop/runs/<run-id>/heartbeat.json`
- `.phase-loop/runs/<run-id>/terminal-summary.json`

Use CLI output such as `status --json` and `monitor --once --json` when exact
machine state is needed.

## What This Bridge Must Cover

- bounded `run`, `dry-run`, `resume`, `status`, and `monitor` flows
- manual event import expectations from `shared/phase-loop/protocol.md`
- injected-skill metadata and installed-skill drift guidance from the shared loop state
- downstream roadmap amendments that invalidate older downstream phase plans or handoffs

## Operator Notes

Repo-sourced injected workflow bundles remain authoritative for autonomous
phase-loop child launches. Installed workflow skills under
`resolve_skill_bundle_root("opencode")/` are the recommended manual reentry surface, but
they are not required for the runner to launch or continue work. If the
advisory bridge record reports `missing_root`, `missing_skill`, or `drifted`,
repair the local bridge with `codex-phase-loop sync-skills --apply` before
trusting manual OpenCode reentry.

Use the shared loop state to reason about:

- current `automation` status and `verification_status`
- whether a blocker is `branch_sync_conflict`, `dirty_worktree_conflict`, or another frozen blocker literal
- whether the selected harness skill pack matches injected metadata or shows installed-skill drift
- whether the latest OpenCode launch used explicit `<harness> run` metadata for
  `--agent`, provider-qualified `--model`, optional `--variant`, and recorded
  permission posture instead of silently accepting permissive defaults

If the local TUI may be stale because another harness or shell session drove
the loop, start from `handoff` or `monitor --once --json` before resuming.
Manual imports must append shared-protocol events instead of inventing
harness-private state.

If a downstream roadmap amendment lands, route the next step back through the
planner and treat older downstream plans as stale.


## Runner Evidence Contract

Use artifact-backed re-verdicting for blocked gates: a gate changes verdict only by rerunning the originally specified runner check and reading the runner-owned artifact. proxy evidence requires a roadmap amendment before the bridge reports the gate as passed. When both canonical `.phase-loop/` state and legacy `.codex/phase-loop/` compatibility files exist, canonical `.phase-loop/` state takes precedence for status, monitor, resume, reconcile, and repair decisions.


## Spec Delta Closeout

Phase-loop handoffs and terminal closeouts must preserve one `spec_delta_closeout.v1` decision from the executor: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Treat missing or malformed spec-closeout evidence as a repairable automation blocker with `blocker_class=contract_bug`, unless the decision is `human_source_judgment_required`. The record is metadata-only with `redaction_posture=metadata_only`: keep target surfaces, evidence paths, decision literals, IF gates, and artifact names, but never raw specification bodies, raw patch bodies, credentials, provider-supplied payloads, local environment values, or ignored/private evidence-source contents.

## Command Mapping

- `<harness>-phase-loop handoff`: use `phase-loop handoff` or `codex-phase-loop handoff`.
- `<harness>-phase-loop status`: use `phase-loop status` or `codex-phase-loop status`.
- `<harness>-phase-loop state`: use `phase-loop state --json` when exact machine state is needed.
- `<harness>-phase-loop monitor`: use `phase-loop monitor --once --json`.
- `<harness>-phase-loop run`: use `phase-loop run --max-phases <N> --closeout-mode manual` unless the operator explicitly chooses another closeout mode.
- `<harness>-phase-loop dry-run`: use `phase-loop dry-run --max-phases <N>`.
- `<harness>-phase-loop sync-skills`: use `phase-loop sync-skills --check`, and use `--apply` only when the operator explicitly wants bridge repair.

OpenCode is live-supported through the shared runner contract. Keep explicit
permission posture, selected agent, provider-qualified model, and optional
reasoning variant visible in launch metadata rather than assuming permissive
defaults are acceptable.
