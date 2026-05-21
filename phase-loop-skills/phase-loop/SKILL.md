---
name: phase-loop
description: "Harness TUI bridge for the repo-local <harness>-phase-loop runner. Use when the user wants phase-loop status, resume, bounded run, dry-run, or skill-maintenance from inside the selected harness."
---

# Harness Phase Loop

Thin TUI bridge for the `<harness>-phase-loop` alias of the neutral `phase-loop`
runner. This skill delegates to the stable `phase-loop` runtime boundary and
follows the public contract defined in `docs/phase-loop/runtime-boundary.md`.
This skill does not reimplement phase selection, reconciliation, handoff
parsing, model policy, event writing, or execution logic.

## Core Rules

Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

- Use the installed repo command: `<harness>-config/bin/<harness>-phase-loop`. Non-the selected harness
  control planes should use the neutral `<harness>-config/bin/phase-loop` command.
- Prefer the outside-TUI runner for full-roadmap execution; this TUI bridge keeps interactive `run` calls bounded and operator-driven.
- Preserve manual TUI use of `<harness>-phase-roadmap-builder`, `<harness>-plan-phase`, and `<harness>-execute-phase`; this bridge is optional operator convenience.
- Do not edit OpenCode skills, broaden operator docs, create cloud resources, retrieve secrets, commit, push, merge, or discard unrelated work.
- Before reporting a human-required access blocker, inspect repo-local docs/config and safe metadata from available CLIs named in `<harness>-config/shared/runtime-state.md`. Never print secret values.
- If the CLI is missing or its commands differ from this skill, stop and return to the RUNNER phase instead of guessing behavior.

## Inputs

- `handoff`: print the canonical TUI recovery handoff.
- `status`: report reconciled or current loop state.
- `monitor`: observe state/heartbeat/terminal summaries without launching phases.
- `resume`: reconcile durable state and write the current snapshot.
- `run`: execute a bounded loop run. Always pass `--max-phases <N>`; default to `1` when the user does not specify a bound.
- `dry-run`: show the commands the runner would launch without executing them.
- `maintain-skills`: delegate skill-maintenance workflow selection to the runner. Planner-only is the default.
- `sync-skills`: audit or repair the installed `*-phase-loop` bridge skills used for manual TUI reentry.

Optional flags may be passed through when requested: `--repo`, `--roadmap`, `--phase`, `--model-profile`, `--model`, `--effort`, `--json`, `--observe`, `--no-observe`, `--stream-output`, `--heartbeat-interval-seconds`, `--quiet-warning-seconds`, `--quiet-blocker-seconds`, `--no-heartbeat`, `--closeout-mode`, `--bypass-approvals`, `--poll-seconds`, `--timeout-seconds`, `--notify-command`, `--notify-on`, `--once`, `--min-reflections`, `--apply-skill-edits`, `--allow-skill`, and `--improvement-plan`.

## Preflight

1. Resolve the repo root and run `git status --short`.
2. Confirm `<harness>-config/bin/<harness>-phase-loop --help` lists `status`, `resume`, `run`, `dry-run`, and `maintain-skills`.
3. For commands that need a roadmap, let the CLI discover it unless the user supplied `--roadmap`; do not reimplement roadmap or phase selection in the skill text.
4. If unrelated dirty work overlaps files the runner would modify, report `human_required=true` with `blocker_class=dirty_worktree_conflict` instead of continuing.

## Command Mapping

- `<harness>-phase-loop handoff`: run `<harness>-config/bin/<harness>-phase-loop handoff`.
- `<harness>-phase-loop status`: run `<harness>-config/bin/<harness>-phase-loop status`.
- `<harness>-phase-loop resume`: run `<harness>-config/bin/<harness>-phase-loop resume`.
- `<harness>-phase-loop state`: run `<harness>-config/bin/<harness>-phase-loop state --json` when exact machine state is needed.
- `<harness>-phase-loop monitor`: run `<harness>-config/bin/<harness>-phase-loop monitor --once --json` for a single machine-readable observation.
- `<harness>-phase-loop monitor --notify-command <cmd>`: run monitor with callback-by-exception; the command receives redacted JSON on stdin.
- `<harness>-phase-loop run`: run `<harness>-config/bin/<harness>-phase-loop run --max-phases <N>`.
- `<harness>-phase-loop dry-run`: run `<harness>-config/bin/<harness>-phase-loop dry-run --max-phases <N>`.
- `<harness>-phase-loop run --observe`: run `<harness>-config/bin/<harness>-phase-loop run --max-phases <N> --observe`; launch artifacts are written by default, so this flag is compatibility-only.
- `<harness>-phase-loop dry-run --observe`: run `<harness>-config/bin/<harness>-phase-loop dry-run --max-phases <N> --observe`; launch artifacts are written by default, so this flag is compatibility-only.
- `<harness>-phase-loop maintain-skills`: run `<harness>-config/bin/<harness>-phase-loop maintain-skills`.
- `<harness>-phase-loop maintain-skills --observe`: run `<harness>-config/bin/<harness>-phase-loop maintain-skills --observe`; launch artifacts are written by default, so this flag is compatibility-only.
- `<harness>-phase-loop maintain-skills --apply-skill-edits --improvement-plan <path> --allow-skill <<harness>-* skill>`: run apply-enabled maintenance only when the user explicitly requests skill edits and names the allowed Harness skill targets.
- `<harness>-phase-loop sync-skills`: run `<harness>-config/bin/<harness>-phase-loop sync-skills --check` unless the user explicitly asks to repair drift.
- `<harness>-phase-loop sync-skills --apply`: run apply mode only when the user explicitly wants local bridge-skill repair for manual reentry.

Keep `run` bounded. If the user requests an unbounded run, choose `--max-phases 1` and say that the bridge keeps TUI-initiated runs bounded.

Use the shared loop state to inspect injected-skill metadata and
installed-skill drift before blaming a missing capability on the runner
contract. Repo-sourced injected workflow bundles remain authoritative for
autonomous child launches; installed workflow skills under `resolve_skill_bundle_root("codex")/`
are the recommended manual reentry surface but are not a runtime prerequisite.
If the advisory bridge record reports `missing_root`, `missing_skill`, or
`drifted`, repair the local `<harness>-phase-loop` bridge with
`<harness>-config/bin/<harness>-phase-loop sync-skills --apply` before relying on
manual reentry.

Harness is live-supported, but nested Harness-on-Harness disposable proof remains a
known operator caveat. If the requested action is a full Harness live smoke, run
it from a normal shell session rather than an active Harness thread. Active
Harness-thread runs may still observe or exercise the non-Harness live matrix.

When supervising a loop from a TUI, launch artifacts are written by default. If
the TUI session has been idle while another control plane ran the loop, first run
`<harness>-config/bin/<harness>-phase-loop handoff` or read
`.phase-loop/tui-handoff.md`. That file is the canonical human-readable
entrypoint and links to `.phase-loop/state.json`,
`.phase-loop/events.jsonl`, `.phase-loop/runs/`, and the latest run
log when known. Use `<harness>-config/bin/<harness>-phase-loop state --json` when exact
machine state is needed. Creating `.phase-loop/stop` pauses the loop
before the next phase launch; remove it before continuing.

Use `<harness>-config/bin/<harness>-phase-loop run --max-phases <N> --observe
--closeout-mode <manual|commit|push>` for execution. Use
`<harness>-config/bin/<harness>-phase-loop monitor` for observation only. Monitor mode
can be polled by a human, TUI, or external supervisor and can notify on
`blocked`, `stale`, `complete`, `awaiting_phase_closeout`, `operator_halt`, or
`terminal_exit` transitions through `--notify-command`. The notification
payload is redacted JSON and points back to the same canonical state, ledger,
handoff, heartbeat, terminal summary, and log files.

Observed runs write `.phase-loop/runs/<run-id>/heartbeat.json` and
`.phase-loop/runs/<run-id>/terminal-summary.json`. The handoff,
`state --json`, and `monitor --once --json` surface elapsed time, child process
liveness, seconds since the log changed, quiet level, heartbeat status,
terminal status, recommended action, and a paste-ready nudge prompt. Use the
nudge prompt only as an operator action in the owning TUI/session; the runner
does not automatically kill, retry, duplicate, or inject prompts into child
harnesses.

Pipeline-aware dotfiles runs may also emit adjacent Pipeline closeout export
metadata as `phase_loop_closeout.v1` in terminal summaries and event metadata.
This is not a second shared closeout: preserve exactly one shared
`automation:` block, treat `.phase-loop/` as authoritative state, and keep
Governed Pipeline ingest outside dotfiles. Human-required Pipeline closeout
metadata must include only redacted access attempts: source, probe, result,
details, and timestamp, never secret values.

Manual standalone bridge use remains valid without governed-pipeline, Portal,
Greenfield, `.pipeline/**`, or a source bundle. In pipeline_required runs,
Portal lifecycle state, Greenfield reduction, and Greenfield metadata-only authority refs are mediated through governed-pipeline closeout ingest and
projection; protected-source freshness stays governed-pipeline-owned, and they are not dotfiles write targets. Do not infer reads or writes
to `.pipeline/**`, governed-pipeline specs, Portal contracts, Greenfield
authority files, raw evidence, provider payloads, credentials, or legacy
`.codex/phase-loop/` state unless the active plan and source bundle explicitly
own the exact path or glob. Legacy `.codex/phase-loop/` compatibility-only
artifacts must not supersede canonical `.phase-loop/` state.
The active plan and source bundle explicitly own the exact path or glob before
protected bridge inputs or outputs may be used.
Greenfield authority files remain outside dotfiles write ownership.
Legacy `.codex/phase-loop/` state is compatibility-only and never authority.

If execution discovers steering that changes downstream work, amend the nearest
downstream roadmap phase that is not already executing. After that amendment,
older downstream phase plans or handoffs whose roadmap metadata no longer
matches are stale and must route back through `<harness>-plan-phase` before any
further execution.

When a phase is `awaiting_phase_closeout`, `manual` remains the default policy.
Operators may opt into `--closeout-mode commit` to preserve trusted
phase-owned dirty output locally, or `--closeout-mode push` to attempt the same
local preservation and then push only when topology is clean and the push
target is explicit or unambiguous. The handoff and `state --json` should be the
source of truth for the latest closeout mode, commit SHA, push target, refusal
reason, and verification status.

When a phase is `blocked` with `human_required=false`, the runner may launch one
bounded repair turn for that phase instead of treating the blocker as an
operator stop. Repair launch is fail-closed: it requires trusted machine
context for the current phase, including the current terminal summary and the
active phase plan artifact. The repair turn must inspect
`.phase-loop/state.json`, `.phase-loop/events.jsonl`,
`.phase-loop/tui-handoff.md` or `<harness>-phase-loop handoff`, and verify
`<harness>-phase-loop status --json` before and after edits. It should fix local
roadmap/plan drift, apply closeout only when the recorded policy allows it,
append a `manual_repair` event only when it clears the blocker, or convert the
blocker to a frozen human-required taxonomy entry if investigation shows real
operator input is needed.

If an execute child returns without completion evidence and leaves the target
repo dirty, including cases where the plan artifact still classifies the phase
as `planned`, the runner treats that as a non-human repair blocker unless the
dirty paths were already present outside the selected roadmap/plan artifacts.
Rerunning the loop should launch a bounded repair turn only when that trusted
context exists; otherwise it should fail closed and point the operator back to
`.phase-loop/tui-handoff.md`, `<harness>-phase-loop handoff`, and
`<harness>-phase-loop status --json`.

If a planning child exits successfully but does not create the expected current
phase-plan artifact, the runner must classify the phase as `blocked` with
`human_required=false` and `blocker_class=repeated_verification_failure`. This
usually means the child returned a proposed plan in prose instead of writing the
artifact. The TUI handoff and terminal summary are the source of truth for the
recovery command.

Blocker meanings:

- `awaiting_phase_closeout`: verified phase-owned dirty output needs explicit `manual`, `commit`, or `push` closeout.
- `dirty_worktree_conflict`: dirty paths are unowned, pre-existing, or otherwise unsafe for automatic closeout.
- `repeated_verification_failure`: a deterministic artifact or verification contract failed after a bounded attempt.
- `admin_approval`: the loop found a real admin action, branch protection, or policy prerequisite.
- `operator_halt`: `.phase-loop/stop` paused the loop before the next launch.

The runner also records `git_topology` in `state.json`, `events.jsonl`, and the
TUI handoff. Use it to detect whether the loop is operating from a PR branch,
an alternate push ref, or a local branch ahead of its base. If the surrounding
pipeline knows the intended target, it may set `PHASE_LOOP_BASE_REF`,
`PHASE_LOOP_TARGET_PUSH_REF`, `PHASE_LOOP_PR_HEAD_REF`,
`PHASE_LOOP_PR_BASE_REF`, or `PHASE_LOOP_PR_URL` before invoking the CLI.

Release-dispatch phases are guarded by the CLI before child launch. If a plan is
marked `phase_loop_mutation: release_dispatch`, dirty release-affecting paths or
a branch/base-ref mismatch produce a blocked handoff instead of dispatching the
external workflow. A roadmap-level complete handoff means all phases are
complete and the target repo worktree is clean; it should not include a resume
command.

Child Harness launches default to `--sandbox danger-full-access`. Pass `--bypass-approvals` only when the user explicitly wants fully non-interactive child execution in an externally trusted sandbox.

Write-capable parallel execution requires machine-verified disjoint lanes and
scheduler-owned worktree assignments before fanout. Do not treat a prompt,
plan note, or native harness team capability as enough to bypass runner-owned
lane safety. Claude Code CLI exception wording means local Claude Code CLI
execution through the phase-loop launcher, not Anthropic API-key execution or
PI provider fallback. Harness and Gemini fallback wording remains CLI-based and
reason-coded.

## Human-Required Blockers

When the CLI reports or implies `human_required=true`, preserve its `blocker_class`, `blocker_summary`, `required_human_inputs`, `access_attempts`, and `automation.status` in the final response. If the blocker is access-related and the CLI did not already record safe probes, perform metadata-only probes from `<harness>-config/shared/runtime-state.md` before asking the user to act.

If `.phase-loop/tui-handoff.md` exists, use its plain-language blocker
summary and required action as the user-facing explanation, then verify exact
state from `state.json` or `<harness>-phase-loop state --json` before resuming.

Use only the frozen blocker taxonomy: `missing_secret`, `account_or_billing_setup`, `admin_approval`, `destructive_operation`, `ambiguous_roadmap_selection`, `product_decision_missing`, `dirty_worktree_conflict`, `branch_sync_conflict`, `stalled_child_observation`, `repeated_verification_failure`, or `unretryable_external_outage`.

## Manual Fallback

If the runner cannot be used but a manual phase command is clear from durable state or CLI output, report that command instead of reconstructing runner behavior. Valid manual fallbacks are:

- `<harness>-phase-roadmap-builder <roadmap_or_spec>`
- `<harness>-plan-phase <roadmap_path> <phase_alias>`
- `<harness>-execute-phase <plan_path>`
- `<harness>-skill-improvement-planner`
- `<harness>-skill-editor --improvement-plan <path> --allow-skill <<harness>-* skill>`

## Verification

Use the narrowest command that proves the requested bridge action:

- `<harness>-config/bin/<harness>-phase-loop --help`
- `<harness>-config/bin/<harness>-phase-loop handoff`
- `<harness>-config/bin/<harness>-phase-loop status`
- `<harness>-config/bin/<harness>-phase-loop dry-run --max-phases 1 --observe`

For roadmap-level closeout, also inspect `.phase-loop/state.json`,
`.phase-loop/events.jsonl`, relevant handoffs, and path-scoped `git status`
instead of trusting a running transcript alone.
