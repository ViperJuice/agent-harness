# Detailed Plan: Issue #33 Background Subprocess Liveness And Salvage

## Task

Harden background subprocess management so long-running `codex exec` or related CLI children can be identified as hung when stdin/output are stalled, while preserving evidence before cleanup and avoiding termination of live work.

This is a targeted runtime plan, not a roadmap. Current launcher behavior already closes stdin for background commands; the remaining work is liveness classification and salvage.

## Code Research

Primary files:

- `phase-loop-runtime/src/phase_loop_runtime/launcher.py`
- `phase-loop-runtime/src/phase_loop_runtime/observability.py`
- `phase-loop-runtime/tests/test_phase_loop_launcher.py`
- `phase-loop-runtime/tests/test_observability.py`

Findings:

- `launcher.launch(..., log_path=None)` already passes `stdin=subprocess.DEVNULL` when there is no stdin payload.
- The `Popen` log path already passes `stdin=subprocess.DEVNULL` when there is no stdin payload and uses `start_new_session=True`.
- Existing tests cover both stdin-close paths.
- The launcher already cleans up process groups on timeout and stale heartbeat conditions.
- `observability.run_heartbeat_summary` currently classifies quiet/stale output using log mtime and size, but does not inspect CPU usage.
- Current stale cleanup can treat output silence as enough evidence. #33 asks for a stronger distinction between "quiet but alive" and "likely hung".

## Implementation Steps

### 1. Preserve the existing stdin behavior and add explicit regression coverage

Do not rework command launching. Keep the current `stdin=subprocess.DEVNULL` behavior for all no-payload background paths.

In `phase-loop-runtime/tests/test_phase_loop_launcher.py`, keep or extend tests that assert:

- no-log `subprocess.run` receives `stdin=subprocess.DEVNULL` when no payload is supplied
- log-backed `subprocess.Popen` receives `stdin=subprocess.DEVNULL` when no payload is supplied
- stdin payload mode still uses a pipe or input text as appropriate

### 2. Add best-effort CPU liveness sampling

Modify `phase-loop-runtime/src/phase_loop_runtime/observability.py`.

Add a small, best-effort process CPU sampler for POSIX hosts:

- Input: process id
- Output: `float | None`
- Implementation can use `ps -o %cpu= -p <pid>` with a short timeout
- On unsupported platforms, missing `ps`, parse failures, or exited processes, return `None`

Do not make CPU sampling a hard dependency and do not fail heartbeat generation when it is unavailable.

Add heartbeat fields:

- `cpu_percent`: float or null
- `liveness_class`: one of `active_output`, `cpu_active_quiet`, `suspect_stalled`, `quiet_unknown`, `exited`
- `stalled_suspect`: boolean

Suggested classification:

- output recently changed: `active_output`
- process exited: `exited`
- output is stale and CPU is above a small threshold such as `1.0`: `cpu_active_quiet`
- output is stale and CPU is at or below the threshold: `suspect_stalled`
- output is stale and CPU is unavailable: `quiet_unknown`

### 3. Gate stale cleanup on liveness class

Modify `phase-loop-runtime/src/phase_loop_runtime/launcher.py`.

When launch monitoring reaches the stale heartbeat path:

- cleanup may proceed for `suspect_stalled`
- timeout cleanup may still proceed for explicit launch timeout
- CPU-active quiet processes must not be killed only because output is quiet
- CPU-unknown quiet processes should be reported as `quiet_unknown`; keep existing timeout behavior as the final hard boundary

This preserves safety for live but quiet work while still handling genuinely idle hung children.

### 4. Capture salvage evidence before cleanup

Before terminating a suspected hung process group, record a redacted salvage snapshot in `cleanup_evidence`.

Include only metadata and artifact references:

- process pid and process group id
- command display string or redacted argv already used by launcher evidence
- working directory
- log path, log size, log mtime, and a short tail excerpt if existing launcher evidence already permits log excerpts
- heartbeat summary path or inline heartbeat fields
- timeout/stall reason
- cleanup signal sequence
- any transcript/session/artifact path already produced by the launcher

Do not include secrets, raw environment values, bearer tokens, or private stdin payloads.

### 5. Extend tests around liveness and salvage

Modify `phase-loop-runtime/tests/test_phase_loop_launcher.py`:

- CPU-active stale output should set stalled metadata but not invoke process-group cleanup solely for staleness.
- CPU-idle stale output should invoke cleanup and record salvage evidence.
- Timeout cleanup still works regardless of CPU class.
- Existing stale cleanup tests should be updated to assert `suspect_stalled` rather than relying on output mtime alone.

Modify `phase-loop-runtime/tests/test_observability.py`:

- heartbeat summary includes `cpu_percent` when the sampler succeeds.
- stale + CPU idle maps to `suspect_stalled`.
- stale + CPU active maps to `cpu_active_quiet`.
- CPU unavailable maps to `quiet_unknown` without crashing.

## Verification

Run focused tests first:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime
PYTHONPATH=src python -m pytest tests/test_phase_loop_launcher.py tests/test_observability.py -q
```

Then run the runtime suite:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime
PYTHONPATH=src python -m pytest -q
```

If available, finish with:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630
just agent:fast
```

## Acceptance Criteria

- All background no-payload subprocess paths continue to close stdin.
- Heartbeat summaries distinguish stale CPU-idle, stale CPU-active, and stale CPU-unknown children.
- Stale CPU-active children are not killed solely because output stopped changing.
- Stale CPU-idle children are cleaned up with process-group cleanup.
- Cleanup evidence includes a redacted salvage snapshot before termination.
- Targeted launcher/observability tests and the runtime pytest suite pass.

## Out Of Scope

- Rewriting the launcher execution model
- Adding a non-stdlib process supervisor dependency
- Changing model selection, advisor-panel behavior, or CLI prompt feeding
- Capturing secret-bearing environment or stdin payloads in salvage artifacts

