---
name: claude-phase-loop
description: "Harness Code bridge for the repo-local phase-loop runner. Use when the user wants phase-loop status, resume, bounded run, dry-run, or monitor flows from Harness."
---

# Harness Phase Loop

Thin bridge for the repo-local phase-loop runner. This skill points Harness
operators at the shared loop state and command surface; it does not reimplement
phase selection, reconciliation, event writing, or execution logic.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Use the repo-local phase-loop CLI or wrapper documented by the repo; do not invent a second runner.
- Preserve manual Harness use of `<harness>-phase-roadmap-builder`, `<harness>-plan-phase`, and `<harness>-execute-phase`.
- Inspect `shared/phase-loop/protocol.md` and `<harness>-config/shared/runtime-state.md` before describing artifact or blocker semantics.
- Before reporting a human-required access blocker, inspect repo-local docs/config and safe metadata from available CLIs. Never print secret values.

## Inputs

- `handoff`: print the canonical TUI recovery handoff.
- `status`: report reconciled or current loop state.
- `monitor`: observe state/heartbeat/terminal summaries without launching phases.
- `resume`: reconcile durable state and write the current snapshot.
- `run`: execute a bounded loop run. Prefer `--max-phases 1` unless the user names another bound.
- `dry-run`: show the commands the runner would launch without executing them.
- `sync-skills`: audit or repair the installed `<harness>-phase-loop` bridge skill used for local manual reentry.

Keep `run` bounded. If the user asks for an unbounded TUI run, clamp it to
`--max-phases 1` and say that the bridge keeps interactive runs bounded.

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
phase-loop child launches. Installed workflow skills under `resolve_skill_bundle_root("claude")/`
are the recommended manual reentry surface, but they are not required for the
runner to launch or continue work. If the advisory bridge record reports
`missing_root`, `missing_skill`, or `drifted`, repair the local bridge with
`codex-phase-loop sync-skills --apply` before trusting manual Harness reentry.
The injected Harness bundle carries the full workflow pack
(`<harness>-phase-roadmap-builder`, `<harness>-plan-phase`,
`<harness>-execute-phase`, and `<harness>-phase-loop`) from the repo-owned source
tree under `.phase-loop/runs/<run-id>/<harness>-bundle/`.

Use the shared loop state to reason about:

- current `automation` status and `verification_status`
- whether a blocker is `dirty_worktree_conflict`, `branch_sync_conflict`, or another frozen blocker literal
- whether the selected harness skill pack matches injected metadata or shows installed-skill drift

If the local TUI may be stale because another harness or shell session drove
the loop, start from `handoff` or `monitor --once --json` before resuming.
Manual imports must append shared-protocol events instead of inventing
harness-private state.

If a downstream roadmap amendment lands, route the next step back through the
planner and treat older downstream plans as stale.

## Branch-governance preflight

Issue #83: in pipeline-mode with branchgov on (the default), a dispatch switches
to `consiliency/pipeline/<v>` cut from `origin/main`. If the roadmap is committed
only on the operator's own branch (unpushed / ahead of `origin/main`), the
runtime guard refuses cleanly with a `branch_sync_conflict` blocker rather than
crashing — push the roadmap to the base, or pass `--allow-branchgov` to switch
anyway (it then still fails cleanly if the roadmap isn't on the base). The
runtime guard is the authoritative fail-safe; see
`docs/phase-loop/branchgov-preflight.md`.


## Runner Evidence Contract

Use artifact-backed re-verdicting for blocked gates: a gate changes verdict only by rerunning the originally specified runner check and reading the runner-owned artifact. proxy evidence requires a roadmap amendment before the bridge reports the gate as passed. When both canonical `.phase-loop/` state and legacy `.codex/phase-loop/` compatibility files exist, canonical `.phase-loop/` state takes precedence for status, monitor, resume, reconcile, and repair decisions.


## Spec Delta Closeout

Phase-loop handoffs and terminal closeouts must preserve one `spec_delta_closeout.v1` decision from the executor: `no_spec_delta`, `roadmap_amendment`, `canonical_spec_update`, `governed_pipeline_refresh`, `mirror_cutover_required`, `dotfiles_skill_source_update`, or `human_source_judgment_required`. Treat missing or malformed spec-closeout evidence as a repairable automation blocker with `blocker_class=contract_bug`, unless the decision is `human_source_judgment_required`. The record is metadata-only with `redaction_posture=metadata_only`: keep target surfaces, evidence paths, decision literals, IF gates, and artifact names, but never raw specs, raw diffs, credentials, provider payloads, local env values, or ignored/private evidence-source contents.

## Command Mapping

- `<harness>-phase-loop handoff`: use `phase-loop handoff` or `codex-phase-loop handoff`.
- `<harness>-phase-loop status`: use `phase-loop status` or `codex-phase-loop status`.
- `<harness>-phase-loop state`: use `phase-loop state --json` when exact machine state is needed.
- `<harness>-phase-loop monitor`: use `phase-loop monitor --once --json`.
- `<harness>-phase-loop run`: use `phase-loop run --max-phases <N> --closeout-mode manual` unless the operator explicitly chooses another closeout mode.
- `<harness>-phase-loop dry-run`: use `phase-loop dry-run --max-phases <N>`.
- `<harness>-phase-loop sync-skills`: use `phase-loop sync-skills --check`, and use `--apply` only when the operator explicitly wants bridge repair.

Harness Code uses the shared runner contract, but autonomous live dispatch is
proof-blocked until the authenticated non-interactive planning smoke completes
inside the runner timeout. Manual TUI reentry and manual-import closeout remain
supported through `.phase-loop/` state. ThawedCode stays grouped with
Harness only for docs and manual imports; do not claim a separate live
ThawedCode automation contract unless a later roadmap proves it.
