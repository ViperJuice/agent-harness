---
phase_loop_plan_version: 1
phase: SOURCEBROKER
roadmap: specs/phase-plans-v1-task-message-sourcebroker.md
roadmap_sha256: f10d6ac70edf68627bd6b2476a418e88ff05901ab5dfa69149485f94005f3b3c
---

# SOURCEBROKER: Authenticated Task-Message Source Broker

## Context

Implement the upstream Agent Harness node of the governed SOURCEBROKER train.

## Interface Freeze Gates

- [ ] IF-0-SOURCEBROKER-1 — exact authenticated NDJSON server contract.
- [ ] IF-0-SOURCEBROKER-2 — strict broker client and CLI transport selection.
- [ ] IF-0-SOURCEBROKER-3 — loopback service artifact and tailnet-only deployment procedure.

## Lane Index & Dependencies

SL-0 — Broker server core
  Depends on: (none)
  Blocks: SL-1
  Parallel-safe: no

SL-1 — Broker client, CLI, and service
  Depends on: SL-0
  Blocks: SL-2
  Parallel-safe: no

SL-2 — Documentation and verification reducer
  Depends on: SL-0, SL-1
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Broker server core
- **Scope**: Add the loopback-only authenticated broker and exact heartbeat/result wire protocol.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/task_message_broker.py`, `phase-loop-runtime/tests/test_task_message_broker.py`
- **Interfaces provided**: `TaskMessageBroker`, exact `/v1/task-message/probe` and `/v1/task-message/resolve` request schemas, exact NDJSON frames
- **Interfaces consumed**: `CodexAppServerTaskMessageResolver` (pre-existing), `TaskMessageResolverError` (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: cover auth-before-socket, exact request/frame keys, SHA identity, sequence monotonicity, size bounds, heartbeat cadence, single-flight, blocked results, disconnect behavior, and loopback bind rejection;
  - impl: hash bearer bytes and compare to the configured SHA-256 before resolver construction; accept only fixed POST paths and exact JSON keys; stream metadata-only heartbeats every 5 seconds and one terminal result wrapper; suppress request logs;
  - verify: `cd phase-loop-runtime && uv run --with pytest python -m pytest tests/test_task_message_broker.py -q`.

### SL-1 — Broker client, CLI, and service
- **Scope**: Add broker transport to existing probe/resolve commands and a credential-free user-service artifact.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/task_message_broker_client.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_task_message_broker_cli.py`, `phase-loop-runtime/tests/test_task_message_resolver.py`, `deploy/phase-loop-task-message-broker.service`
- **Interfaces provided**: `--broker-url`, strict heartbeat-inactivity client, `task-message-broker-serve`, loopback service unit
- **Interfaces consumed**: `TaskMessageBroker`, exact `/v1/task-message/probe` and `/v1/task-message/resolve` request schemas, exact NDJSON frames; existing endpoint/control-socket transports (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: cover mutually exclusive transports, token lookup only for authenticated remote modes, exact/no-extra-key parsing, 40-hex release SHA, no total request deadline while heartbeats advance, 15-second inactivity failure, and sanitized CLI output;
  - impl: stream NDJSON with a bounded connect timeout and heartbeat-reset inactivity bound; preserve existing resolver payload while adding `agent_harness_sha`; expose server command with fixed loopback default and validated digest/SHA config;
  - impl: add a hardened user service that stores no raw token, opens no tailnet/public listener, and never starts or restarts Codex app-server;
  - verify: `cd phase-loop-runtime && uv run --with pytest python -m pytest tests/test_task_message_resolver.py tests/test_task_message_broker_cli.py -q`.

### SL-2 — Documentation and verification reducer
- **Scope**: Record the canonical broker/operator contract only after both implementation lanes pass.
- **Owned files**: `docs/task-message-resolver.md`, `CHANGELOG.md`, `.dev-skills/handoffs/codex-execute-phase/verification-SOURCEBROKER.md`
- **Interfaces provided**: operator deployment procedure and phase evidence
- **Interfaces consumed**: `TaskMessageBroker`, exact NDJSON frames, `--broker-url`, strict heartbeat-inactivity client, `task-message-broker-serve`, loopback service unit
- **Parallel-safe**: no
- **Tasks**:
  - docs: document 1Password token ownership, digest-only claw config, loopback unit, scoped `svc:phase-loop-task-message-broker` Tailscale Serve HTTPS, Funnel prohibition, probe/resolve commands, and teardown;
  - verify: run focused tests, standalone suite, build, roadmap validation, and `git diff --check`; record exact counts without secrets.

## Execution Notes

- Execute lanes serially; shared CLI behavior in SL-1 consumes the frozen SL-0 wire contract.
- This source plan does not dispatch a release, tag, workflow, or live deployment.
- Claw deployment and the downstream ai-stack pin happen only after this node merges through the governed train.

## Verification

```bash
phase-loop validate-roadmap specs/phase-plans-v1-task-message-sourcebroker.md
cd phase-loop-runtime
uv run --with pytest python -m pytest tests/test_task_message_resolver.py tests/test_task_message_broker.py tests/test_task_message_broker_cli.py -q
uv run --with pytest python -m pytest -m 'not dotfiles_integration' -q
uv run --with build python -m build
cd ..
git diff --check
```

`automation.suite_command`: `cd phase-loop-runtime && uv run --with pytest python -m pytest -m 'not dotfiles_integration' -q`

## Acceptance Criteria

- [ ] `tests/test_task_message_broker.py` proves authentication fails before owner-socket access and failure JSON contains no token/proof bytes.
- [ ] `tests/test_task_message_broker.py` and `tests/test_task_message_broker_cli.py` prove exact NDJSON frames and 40-hex release SHA end to end.
- [ ] `tests/test_task_message_broker_cli.py` proves fresh heartbeats outlive any total duration while 15 seconds of inactivity fails closed.
- [ ] `deploy/phase-loop-task-message-broker.service` static assertions prove loopback bind, and `docs/task-message-resolver.md` names only Tailscale Serve, never Funnel.
- [ ] The commands in `## Verification` record passing full-suite, build, roadmap, and diff-check results in `verification-SOURCEBROKER.md`.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/task_message_broker*.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `deploy/phase-loop-task-message-broker.service`, `docs/task-message-resolver.md`
- evidence paths: focused tests, full suite, package build, `.dev-skills/handoffs/codex-execute-phase/verification-SOURCEBROKER.md`
- redaction posture: `metadata_only`
- downstream handling: ai-stack train node pins the merged Agent Harness SHA
