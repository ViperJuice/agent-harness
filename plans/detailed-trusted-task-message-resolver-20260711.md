# Detailed plan: Add an authenticated cross-host task-message proof resolver

## Task

Resolve `Consiliency/agent-harness#155` by adding a read-only resolver that queries an authenticated Codex app-server for one exact thread/message item, returns the exact UTF-8 message and approval-body bytes, and fails closed with the governed error vocabulary required by ai-stack PROVDEPLOY.

## Research summary

The installed Codex app-server already supplies the required source-of-truth boundary: authenticated WebSocket listeners and `thread/read` responses containing stable thread, turn, and message-item IDs. Agent Harness currently has no client for that surface. The smallest implementation is a neutral `phase_loop_runtime` module plus a narrow CLI. A governed source item carries exactly two text inputs: the exact source message and the exact JSON approval body; the body binds the first input's digest and the predeclared message-item ID, so the resolver never guesses, extracts from prose, or creates an impossible self-referential digest.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/task_message_resolver.py` (create)
- `TaskMessageResolverError`, `TaskMessageProof`, and `CodexAppServerTaskMessageResolver` — add — implement authenticated JSON-RPC/WebSocket preflight and exact `thread/read` resolution of the fixed two-text-input envelope, raw-byte proof output, identity/digest checks, freshness checks, and typed fail-closed errors without logging raw content.

### `phase-loop-runtime/src/phase_loop_runtime/cli.py` (modify)
- `task-message-probe` and `task-message-resolve` — add — expose metadata-only availability probing and exact resolution as neutral CLI commands; read bearer material only from a named environment variable and emit either proof data or a typed status.

### `phase-loop-runtime/pyproject.toml` (modify)
- runtime dependencies — modify — add bounded `websockets` and `rfc8785` dependencies for authenticated app-server transport and contract-identical canonical approval hashing.

### `phase-loop-runtime/tests/test_task_message_resolver.py` (create)
- resolver unit and loopback integration coverage — add — prove exact hashes, one-byte drift, wrong identities, unavailable authority, stale/malformed records, digest-only rejection, metadata-safe errors, and a real client/server round trip between separate test authority and client contexts.

### `docs/task-message-resolver.md` (create)
- operator contract — add — document the authenticated app-server configuration, JSON-only source-message requirement, typed failures, secret handling, metadata-only probe, and ai-stack integration boundary.

### `CHANGELOG.md` (modify)
- unreleased entry — modify — record the new governed task-message resolver surface.

## Documentation impact

Add a dedicated operator document because this introduces a public CLI and an authentication boundary. Update the changelog because the packaged runtime gains a new dependency and commands.

## Dependencies & order

1. Implement the resolver contract and transport.
2. Wire the two neutral CLI commands and dependency metadata.
3. Add focused unit and loopback integration tests.
4. Document deployment and security posture, then update the changelog.

## Verification

```sh
cd phase-loop-runtime && python3 -m pytest tests/test_task_message_resolver.py -q
cd phase-loop-runtime && python3 -m pytest -m "not dotfiles_integration" -q
cd phase-loop-runtime && python3 -m build
```

## Acceptance criteria

- [x] Exact task/message bytes produce stable SHA-256 values and the separately parsed approval JSON produces the expected canonical body input bytes.
- [x] A one-byte message or body change changes the proof and cannot match the original claims.
- [x] Wrong thread ID, wrong message ID, unavailable authority, stale source, malformed body, and identity mismatch return only the frozen typed failure codes.
- [x] A loopback WebSocket integration test resolves a source message from a separate authenticated authority context.
- [x] A caller-authored digest-only record or a body not bound to the exact first-input bytes and predeclared message-item identity cannot satisfy the resolver.
- [x] Probe and failure output contain identities, digests, and status only; raw message/approval content is returned only in the successful resolve payload and is never logged.
- [x] The complete standalone Agent Harness runtime test suite passes and the wheel builds.
