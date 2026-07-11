# Task-message source broker roadmap v1

## Context

The merged local resolver can read exact governed source bytes from Codex's real
owner-only Unix control socket, but ai-stack cannot invoke it through tailnet SSH.
A second app-server is invalid because it does not share materialized task state.

## Architecture North Star

Expose a loopback-only, capability-authenticated resolver broker through
Tailscale Serve HTTPS. The broker offers only probe and exact resolve, streams
metadata-only heartbeats, and returns the existing resolver proof with its
immutable Agent Harness release SHA.

## Assumptions

- Codex app-server 0.144.1 owner socket uses WebSocket-over-Unix with compression disabled.
- Tailscale Serve is the tailnet-only TLS boundary; Funnel is forbidden.
- The raw capability token exists only in 1Password and the ai caller; claw stores only its SHA-256.

## Non-Goals

- A second app-server, arbitrary RPC proxy, SSH workaround, task/message mutation, or copied state.
- Any PE-Core, FM queue, collection, inference, signing-key, or Qwen action.

## Cross-Cutting Principles

- Authenticate before opening the owner socket.
- Exact schemas, safe identifiers, bounded inputs/outputs, and metadata-only failures.
- No total wall-clock timeout while authenticated heartbeats remain fresh.

## Top Interface-Freeze Gates

- IF-0-SOURCEBROKER-1 — exact authenticated NDJSON broker contract over the real owner socket.
- IF-0-SOURCEBROKER-2 — loopback-only user service plus Tailscale Serve deployment artifact.
- IF-0-SOURCEBROKER-3 — broker reports the immutable merged Agent Harness SHA consumed by downstream pinning.

## Phases

### Phase 1 — Authenticated Task-Message Source Broker (SOURCEBROKER)

**Objective**

Implement and verify the narrow local resolver broker and client contract.

**Exit criteria**

- [ ] Exact heartbeat/result frames, token-digest authentication, release SHA, and inactivity rules are tested.
- [ ] Probe/resolve client mode preserves the existing terminal resolver JSON contract.
- [ ] Loopback user-service artifact and Tailscale Serve procedure expose no public listener.
- [ ] Full standalone suite and package build pass.

**Scope notes**

Decompose into three lanes: broker core, CLI/client plus service artifact, then documentation and verification reduction.

**Non-goals**

- Live deployment or creation of a production approval.

**Key files**

- `phase-loop-runtime/src/phase_loop_runtime/task_message_broker.py`
- `phase-loop-runtime/src/phase_loop_runtime/task_message_resolver.py`
- `phase-loop-runtime/src/phase_loop_runtime/cli.py`
- `phase-loop-runtime/tests/test_task_message_broker.py`
- `phase-loop-runtime/tests/test_task_message_broker_cli.py`
- `deploy/phase-loop-task-message-broker.service`
- `docs/task-message-resolver.md`
- `CHANGELOG.md`

**Depends on**

- (none)

**Produces**

- IF-0-SOURCEBROKER-1 — exact authenticated broker wire contract.
- IF-0-SOURCEBROKER-2 — reviewed loopback service and tailnet exposure procedure.
- IF-0-SOURCEBROKER-3 — immutable release-SHA response identity.

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected decision: `canonical_spec_update`
- target surfaces: broker/client/service/operator contract
- evidence paths: focused tests, full suite, package build, live disposable round trip
- redaction_posture: `metadata_only`
- malformed/missing evidence: `blocker_class=contract_bug`

## Phase Dependency DAG

```text
SOURCEBROKER
```

## Execution Notes

- Source implementation is fake/loopback only. Live claw deployment occurs only after merge.
- The downstream ai-stack train node pins the merged SHA before integration.

## Verification

```bash
cd phase-loop-runtime
uv run --with pytest python -m pytest tests/test_task_message_resolver.py tests/test_task_message_broker.py tests/test_task_message_broker_cli.py -q
uv run --with pytest python -m pytest -m 'not dotfiles_integration' -q
uv run --with build python -m build
git diff --check
```
