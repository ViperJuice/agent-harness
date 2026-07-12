# Trusted task-message resolver

`phase-loop task-message-resolve` is the read-only cross-host source resolver for governed approvals. It reads one exact pair of Codex user-message items from an authenticated app-server; it does not read copied rollout JSONL, accept caller-supplied digests, search for a latest message, or mutate the source task.

## Source envelope

Codex app-server 0.144.1 identifies a caller-supplied `clientUserMessageId` as the persisted `userMessage.clientId`; the stored `userMessage.id` is separately assigned by app-server. Adjacent text inputs in one `turn/start` request are normalized into one persisted text item, so they cannot carry this contract.

Create two separate user messages in the same task, in this order:

1. one text input containing the exact human/source approval message, with `clientUserMessageId=<source-message-id>`;
2. one text input containing only the exact JSON approval record, with `clientUserMessageId=<source-message-id>-approval`.

The JSON record must be an authorized approval containing `contract_version`, `source_thread_id`, `source_message_id`, and `source_message_sha256`. The thread claim and source `clientId` claim must match the requested app-server identities, and `source_message_sha256` must be the SHA-256 of the first message's exact UTF-8 bytes. The resolver requires unique source and approval client identities, unique app-server-assigned item IDs, source-before-approval ordering, one text item in each message, and fresh timestamps for both turns. This fixed pair separates the source bytes from the canonical approval body without a self-referential hash or concatenated parsing.

The resolver returns the raw byte fields as base64 only after every identity, freshness, and digest check passes. It also returns SHA-256 of the source bytes, SHA-256 of the raw approval-body bytes, and SHA-256 of the RFC 8785-canonical approval object. The successful resolve payload is sensitive approval data and must be consumed directly, not copied into logs or ledger events.

## Authenticated source

Codex app-server 0.144.1's owner-only Unix control socket is itself a WebSocket
transport. For a managed source task, run the resolver on the source host
against that socket. When SSH is unavailable, use the task-message broker below;
do not start another app-server or copy task state.

```sh
phase-loop task-message-resolve \
  --control-socket /home/operator/.codex/app-server-control/app-server-control.sock \
  --authority codex-app-server://claw.example.ts.net \
  --thread-id 019f4454-2012-7061-847d-1a9ab0e9ef00 \
  --message-id provdeploy-approval-001 \
  --max-source-age-seconds 900
```

`--control-socket` is local-side only. It performs the required WebSocket HTTP
Upgrade directly over the Unix socket with compression disabled, matching
Codex 0.144.1's supported transport. It does not expose the socket or add a
network listener. The caller is responsible for authenticating the outer
channel and pinning `--authority` to that source host. Direct network WebSocket mode
continues to require `--token-env`; control-socket mode never accepts or reads a
bearer. A missing socket, failed handshake, or unavailable task fails closed
with `source_task_unavailable`.

### Tailnet broker

The broker is a separate read-only wrapper around the local resolver, not an
app-server proxy. Install the exact merged Agent Harness commit with an immutable
VCS revision, then install `deploy/phase-loop-task-message-broker.service` as a
root-managed system unit on the source host. It runs as `User=viperjuice` and
`Group=viperjuice`; it never runs the broker as root. The unit also ships inside
the runtime wheel as
`phase_loop_runtime/deploy/phase-loop-task-message-broker.service`. Its
environment file contains only the SHA-256 of the capability token and the
merged 40-hex Agent Harness commit; it never contains the raw token. Source the
raw token from 1Password only into the caller environment.

The broker reads the installed distribution's PEP 610 `direct_url.json` at
startup and requires both `requested_revision` and `commit_id` to exactly match
`AGENT_HARNESS_SHA`. An installation without Git VCS provenance, one installed
from a moving branch/tag, or any supplied SHA mismatch fails before the broker
binds a listener.

Install the exact merged revision into the broker's root-owned dedicated venv;
do not use the fleet-wide `~/.local/bin/phase-loop` installation or a venv under
the hidden user home:

```sh
sudo /home/viperjuice/.local/bin/uv venv --python /usr/bin/python3 /opt/phase-loop-task-message-broker
sudo /home/viperjuice/.local/bin/uv pip install --python /opt/phase-loop-task-message-broker/bin/python \
  "git+https://github.com/ViperJuice/agent-harness@${AGENT_HARNESS_SHA}#subdirectory=phase-loop-runtime"
```

The service binds `127.0.0.1:18765`. Expose that loopback endpoint only through
Tailscale Serve HTTPS:

```sh
tailscale serve --service=svc:phase-loop-task-message-broker --bg --https=8765 http://127.0.0.1:18765
```

Never use Tailscale Funnel. Probe from the authenticated caller:

The root system manager gives the unit a private mount namespace, hides the
user's home, and binds back only the exact `app-server-control.sock` inode
read-only. The immutable broker venv stays outside the home under `/opt` and the
system tree is read-only. Private devices, kernel-module protection, an
address-family allowlist, and systemd's deny-all/allow-localhost IP policy are
all active. `MemoryDenyWriteExecute` is intentionally absent: claw's Python
3.13/glibc thread path requests an executable thread stack and fails with
`EPERM` when that directive is active. The broker command independently rejects
every non-loopback bind, and Tailscale Serve is the only tailnet exposure. The
unit does not expose adjacent Codex logs/sockets or unrelated home content. Do
not weaken those restrictions to make deployment succeed. The
client rejects redirects rather than forwarding its bearer to another origin.

The system-service environment file is `/etc/phase-loop/task-message-broker.env`
and contains exactly `TASK_MESSAGE_TOKEN_SHA256=<64-hex>` plus
`AGENT_HARNESS_SHA=<merged-40-hex>`. Install it root-owned at mode `0600`.

```sh
phase-loop task-message-probe \
  --broker-url https://<tailnet-service-url>:8765 \
  --authority codex-app-server://claw.example.ts.net \
  --token-env CODEX_TASK_MESSAGE_TOKEN
```

The broker authenticates before opening the owner socket. Successful responses
are NDJSON: strictly increasing metadata-only heartbeat frames every five
seconds, followed by one exact result frame containing the existing resolver
payload and the merged Agent Harness SHA. The client has no total wall-clock
request timeout while heartbeats remain fresh; three missed heartbeats fail
closed. The heartbeat-less local app-server hop retains a ten-second inactivity
bound.

To remove the broker boundary, clear the Tailscale Serve route first and then
stop the broker unit:

```sh
tailscale serve clear svc:phase-loop-task-message-broker
sudo systemctl disable --now phase-loop-task-message-broker.service
```

Resolve one exact source after the probe is ready:

```sh
phase-loop task-message-resolve \
  --broker-url https://<tailnet-service-url>:8765 \
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

Authentication failures, malformed app-server responses, wrong or duplicate client identities, missing app-server item identities, reversed pairs, concatenated single-item envelopes, non-text inputs, digest-only objects, and stale records never produce a successful proof.

## ai-stack boundary

ai-stack should invoke broker mode, require the broker's merged Agent Harness SHA
to match its reviewed pin, decode the two successful base64 fields into its
`TrustedSourceMessage(message_bytes, approval_body_bytes)` interface, and
independently repeat schema, identity, source-message SHA-256, and RFC 8785
canonical approval checks before constructing an adapter or actuator. A ready
resolver proves only the source boundary; it does not authorize a service
restart or any other mutation.
