# PI Loop Control

The PI package in `phase-loop-pi/` is a minimal operator harness for the neutral phase-loop runner. PI does not own the state machine. The Python `phase-loop` CLI remains authoritative for roadmap selection, phase reconciliation, closeout policy, blockers, handoffs, and monitor events.

## Roles

- `phase-loop`: neutral runner command for CLI, TUI, PI, and other harnesses.
- `codex-phase-loop`: Codex bridge alias for existing Codex skills and scripts.
- `.phase-loop/`: canonical runtime artifact root for state, events, runs, stop files, handoffs, active-loop locks, and archives.
- `.codex/phase-loop/`: legacy compatibility root that remains readable and excluded from git during migration.
- PI loop control: observes state, runs bounded loop steps, triages non-human blockers, and performs commit-only closeout when explicitly selected.
- Child harnesses: Codex, Claude Code, Gemini, OpenCode, Pi Agent, or command adapters implement individual roadmap/plan/execute turns.

Pi Agent as `executor=pi` is the preferred simple bounded lane runner. It is
still a child executor, not the global scheduler, runtime ledger owner,
worktree allocator, or merge reducer.

Granular execution policy lives at
`docs/phase-loop/granular-execution-policy.md`. PI should preserve the runner's high -> medium -> high posture: high planning, medium lane execution and repair, and high reducer/review/verify work.

## Install Or Load

Local install:

```bash
pi install <HOME-REDACTED>/code/dotfiles/phase-loop-pi -l
```

Direct load when supported by the PI CLI:

```bash
pi -e <HOME-REDACTED>/code/dotfiles/phase-loop-pi/extensions/phase-loop-tools.ts \
  -e <HOME-REDACTED>/code/dotfiles/phase-loop-pi/extensions/phase-loop-guardrails.ts \
  --skill <HOME-REDACTED>/code/dotfiles/phase-loop-pi/skills/phase-loop-supervisor
```

## Run

Inspect before launch:

```bash
phase-loop state --repo /path/to/repo --roadmap specs/phase-plans-v1.md --json
phase-loop monitor --repo /path/to/repo --roadmap specs/phase-plans-v1.md --once --json
```

Bounded supervised run:

```bash
phase-loop run --repo /path/to/repo --roadmap specs/phase-plans-v1.md --max-phases 1 --observe
```

PI wrapper run for an existing roadmap:

```bash
pi-agent-watch \
  --repo /path/to/repo \
  --roadmap specs/phase-plans-v1.md \
  --phase-executor codex \
  --max-phases 10 \
  --closeout-mode commit \
  --timeout-seconds 14400 \
  --json
```

Bounded provider rotation keeps PI's provider model separate from the nested
child executor. Examples:

```bash
pi-agent-watch --repo /path/to/repo --roadmap specs/phase-plans-v1.md --phase-executor gemini --closeout-mode manual --json
pi-agent-watch --repo /path/to/repo --roadmap specs/phase-plans-v1.md --phase-executor opencode --closeout-mode commit --json
pi-agent-watch --repo /path/to/repo --roadmap specs/phase-plans-v1.md --phase-executor pi --closeout-mode manual --json
pi-agent-watch --repo /path/to/repo --roadmap specs/phase-plans-v1.md --phase-executor command --closeout-mode manual --json
```

Automatic push is not a published default. Use manual closeout for inspection
and commit-only closeout when verified phase-owned output should be preserved
locally.

`pi-agent-watch` is the appropriate PI entrypoint when a roadmap already
exists. Roadmap-builder commands are only for creating or extending roadmap
artifacts, not for consuming an execution-ready roadmap.

PI supervisor prompt:

```text
Use the phase-loop-supervisor skill. Inspect phase-loop state for /path/to/repo and specs/phase-plans-v1.md. If no active run or human blocker exists, run the existing roadmap through phase_loop_run and report the monitor outcome.
```

## Watch And Callbacks

Use `phase_loop_monitor` for a one-shot state check. Use `phase_loop_watch` when
the operator wants PI to wait by exception, a check-by-exception pattern, instead of repeatedly polling by
hand. Watch mode calls the neutral runner monitor without `--once`, exits on a
terminal or exception event, and can invoke a local notification command.

Equivalent CLI:

```bash
phase-loop monitor \
  --repo /path/to/repo \
  --roadmap specs/phase-plans-v1.md \
  --poll-seconds 60 \
  --timeout-seconds 3600 \
  --notify-on blocked \
  --notify-on stale \
  --notify-on complete \
  --notify-command 'python3 /path/to/write-phase-loop-event.py' \
  --json
```

`notifyCommand` receives the runner notification payload on stdin. Keep
notification commands local and metadata-only: write an event file, send a
desktop notification, or call a harness-specific adapter. Watch mode does not guarantee that every TUI can receive an async message unless that TUI provides a
callback adapter that consumes the notification.

## PI Agent Observability

`phase_loop_watch` lets PI watch the phase loop. `pi-agent-watch` lets an
outside supervisor watch PI itself. It launches PI with this package loaded and
writes neutral controller-process artifacts under the target repo:

- `.phase-loop/pi-agent.json`
- `.phase-loop/pi-agent.log`
- `.phase-loop/pi-agent-terminal.json`

Example:

```bash
pi-agent-watch \
  --repo /path/to/repo \
  --roadmap specs/phase-plans-v1.md \
  --provider anthropic \
  --model claude-opus-4-8 \
  --thinking high \
  --max-phases 1 \
  --closeout-mode commit \
  --poll-seconds 30 \
  --timeout-seconds 3600 \
  --json
```

PI's provider is independent from the nested phase executor. To supervise with
PI on an available provider while running phase work through Claude Code CLI,
set `--phase-executor claude`:

```bash
pi-agent-watch \
  --repo /path/to/repo \
  --roadmap specs/phase-plans-v1.md \
  --provider openai-codex \
  --model gpt-5.5 \
  --thinking high \
  --phase-executor claude \
  --claude-execution-mode agent_team \
  --timeout-seconds 3600 \
  --json
```

When PI is launched with `--provider anthropic` and no explicit model, the
wrapper defaults to `claude-opus-4-8` with `--thinking high` so Anthropic-backed
supervision uses the heavy model tier by default.

The PI provider model and the nested phase executor model are separate knobs.
Use `--model` for the model backing PI's loop-control reasoning. Use
`--phase-model` when the child phase-loop executor must receive an explicit
model. For Gemini child execution, prefer `--phase-executor gemini` with no
`--phase-model` so phase-loop can use Gemini CLI routing defaults: `pro` for
planning/review and `auto` for execution/repair. Use `--phase-model` only for
explicit proof runs that intentionally pin a custom phase-loop alias or concrete
model.

Read `.phase-loop/pi-agent.json` before tailing PI logs. It reports PI process
PID, liveness, elapsed time, seconds since PI output, quiet level, terminal
status, and the log/terminal artifact paths. `.phase-loop/pi-agent.log` captures
PI stdout/stderr with secret-shaped values redacted when surfaced through the
wrapper. These artifacts describe PI controller-process health only; the Python
`phase-loop` state remains authoritative for roadmap state.

The wrapper launches PI with an explicit `phase_loop_*` tool allowlist, session
persistence disabled, and ambient context-file loading disabled. That keeps the
controller focused on the injected phase-loop tools instead of shell-searching
for runner binaries or carrying state between repos. It also passes
`PHASE_LOOP_DEFAULT_REPO` and
`PHASE_LOOP_DEFAULT_ROADMAP` into the extension environment so the tools can
fall back to the wrapper target if PI emits an empty or nested argument object.
When `--phase-executor` is provided, it also passes
`PHASE_LOOP_DEFAULT_EXECUTOR` so `phase_loop_run` deterministically launches the
requested child harness. When `--claude-execution-mode` is provided, it passes
`PHASE_LOOP_DEFAULT_CLAUDE_EXECUTION_MODE` so Claude Code can run in `solo`,
`subagent`, or `agent_team` mode through the phase-loop launcher. Native
subagents, task lists, teammates, and team delegation remain gated by the
runner's TEAMGOV eligibility checks; the wrapper only requests the mode.
When `--max-phases` or `--closeout-mode` is provided, it also passes
`PHASE_LOOP_DEFAULT_MAX_PHASES` and `PHASE_LOOP_DEFAULT_CLOSEOUT_MODE` so the
supervisor can call `phase_loop_run` with deterministic loop depth and closeout
policy instead of relying on prompt prose.

For `executor=pi` lane launches, the phase-loop launcher writes a context-file
prompt from repo-local `phase-loop-pi/**` and `pi-config/**` sources. That
prompt must carry the explicit system prompt, tool policy, allowed writes,
read-only refs, forbidden refs, output roots, verification intent, Greenfield
authority citations when present, governed-pipeline assignment fields
(`lane_id`, `wave_id`, `worktree_path`, `base_sha`, `isolation_mode`,
`owned_files`, `read_only_refs`, `harness_route`, `model`, `effort`, and
`fallback_reason`), and the shared `automation:` / `phase_loop_closeout.v1`
closeout requirements. DFPROMPTSYNC records the prompt-safe contract map at
`docs/phase-loop/dfpromptsync-contract-map.md`.

If `pi --provider anthropic` fails because PI lacks Anthropic API or OAuth
environment credentials, that does not imply Claude Code CLI is unavailable.
Use another PI provider for supervision and `--phase-executor claude` when the
desired child work should use the local `claude` CLI subscription session.
Validate that child path with metadata-only `claude auth status`.

## Blocker Handling

Use `phase_loop_state` first, then `phase_loop_handoff`, `phase_loop_monitor`, and bounded `tail_run_log` only when the state points to a specific log. PI must not infer completion from narrative logs. `human_required=true` is never bypassed.

For `human_required=false` blockers, PI can propose or perform mechanical repair using the `phase-loop-repair` skill, then call `phase_loop_resume` and re-check machine state.

## Closeout

Default policy is manual. Commit-only closeout is the middle ground for preserving verified phase-owned output without triggering remote CI/CD. Push remains explicit operator policy.

Before closeout, confirm:

- `verification_status=passed`
- `human_required=false`
- dirty paths are phase-owned
- `git diff --check` passes

## Decoupling

The PI package calls `phase-loop`, not `codex-phase-loop`. It does not require Codex skills, Codex TUI state, or `.codex/phase-loop/` as the canonical artifact root. Repos with legacy `.codex/phase-loop/` state remain readable so existing loops are not stranded during migration.

For MIGRATELOOP lane scheduling, PI must read canonical `.phase-loop/` state
and pass `--lane-scheduler serialized` only when the operator intentionally
opts into runner-owned lane work units. Plain `phase-loop run --max-phases 1`
remains the coarse compatibility path.

## DFPARSOAK Integrated Soak

DFPARSOAK consumes the Pi default route through
`docs/phase-loop/dfparsoak-receipt.md` and
`docs/phase-loop/dfparsoak-runbook.md`. Pi remains a bounded child runner for
scheduler-assigned lanes; it does not own scheduling, merge reduction, or
destructive worktree cleanup.
