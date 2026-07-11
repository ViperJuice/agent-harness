# Trusted task-message resolver

`phase-loop task-message-resolve` is the read-only cross-host source resolver for governed approvals. It reads one exact Codex user-message item from an authenticated app-server; it does not read copied rollout JSONL, accept caller-supplied digests, search for a latest message, or mutate the source task.

## Source envelope

The source item must be created with a predeclared `clientUserMessageId` and exactly two text inputs in this order:

1. the exact human/source approval message;
2. the exact JSON approval record.

The JSON record must be an authorized approval containing `contract_version`, `source_thread_id`, `source_message_id`, and `source_message_sha256`. The thread and message claims must match the requested app-server identities, and `source_message_sha256` must be the SHA-256 of the first text input's exact UTF-8 bytes. This fixed envelope separates the source bytes from the canonical approval body without a self-referential hash.

The resolver returns the raw byte fields as base64 only after every identity, freshness, and digest check passes. It also returns SHA-256 of the source bytes, SHA-256 of the raw approval-body bytes, and SHA-256 of the RFC 8785-canonical approval object. The successful resolve payload is sensitive approval data and must be consumed directly, not copied into logs or ledger events.

## Authenticated source

Run the source Codex app-server on a tailnet-only listener with either capability-token or signed-bearer-token authentication. `--authority` must be exactly `codex-app-server://<endpoint-hostname>`; this binds the proof identity to the authenticated route instead of accepting a caller-selected label. Use the source host's tailnet DNS name or tailnet address in `--endpoint`; do not expose the listener to a public interface.

Keep the bearer value in a secret-backed environment variable. Pass only its variable name to the CLI:

```sh
phase-loop task-message-probe \
  --endpoint ws://claw.example.ts.net:8765 \
  --authority codex-app-server://claw.example.ts.net \
  --token-env CODEX_TASK_MESSAGE_TOKEN
```

The probe performs only the authenticated app-server initialization handshake and emits authority/status metadata. It does not read a task or message.

Resolve one exact source after the probe is ready:

```sh
phase-loop task-message-resolve \
  --endpoint ws://claw.example.ts.net:8765 \
  --authority codex-app-server://claw.example.ts.net \
  --token-env CODEX_TASK_MESSAGE_TOKEN \
  --thread-id 019f4454-2012-7061-847d-1a9ab0e9ef00 \
  --message-id provdeploy-approval-001 \
  --max-source-age-seconds 900
```

## Fail-closed results

Failures contain only authority and requested identities plus one code:

- `source_task_unavailable`
- `source_message_unavailable`
- `source_identity_mismatch`
- `source_bytes_unavailable`
- `approval_body_unavailable`
- `attestation_invalid`
- `source_stale`

Authentication failures, malformed app-server responses, wrong identities, multiple matching items, non-text inputs, digest-only objects, and stale records never produce a successful proof.

## ai-stack boundary

ai-stack should decode the two successful base64 fields into its `TrustedSourceMessage(message_bytes, approval_body_bytes)` interface and independently repeat schema, identity, source-message SHA-256, and RFC 8785 canonical approval checks before constructing an adapter or actuator. A ready resolver proves only the source boundary; it does not authorize a service restart or any other mutation.
