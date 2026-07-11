---
from: codex-execute-detailed
timestamp: 2026-07-11T18:51:18Z
repo: ViperJuice/agent-harness
repo_root: /home/viperjuice/code/agent-harness-task-message-clientid
branch: codex/task-message-clientid
branch_slug: codex-task-message-clientid
commit: f1b311a7e7967d1d1269071a856adba6ead372ca
run_id: 20260711T185118Z-task-message-clientid
artifact: plans/detailed-trusted-task-message-resolver-20260711.md
verification_artifact_path: .dev-skills/handoffs/codex-execute-detailed/verification-20260711T185118Z-task-message-clientid.md
---

Implemented the bounded Agent Harness compatibility repair for `ViperJuice/agent-harness#165`.

The resolver now uses Codex app-server's persisted `userMessage.clientId`, requires a separate deterministic `-approval` message with one text input, binds both stored item identities and turn timestamps, and rejects the app-server-concatenated one-item shape. A local `--control-socket` transport uses Codex 0.144.1's WebSocket-over-Unix owner socket with compression disabled.

Verification passed: 24 focused tests; 2,244 standalone runtime tests with 35 skipped; 0.7.0 sdist and wheel built; live owner-socket initialize passed on ai and claw. Grok, Gemini, and Fabel agree; Sol's code findings were fixed, with the real round trip retained as the terminal gate. Public CLI/operator documentation was updated (`doc_delta_decision=docs_updated`).

Plan-manifest lifecycle recording was not possible because no manifest entry exists for `plans/detailed-trusted-task-message-resolver-20260711.md`; no manual manifest rewrite was attempted.

The transport blocker is cleared without a network listener. Keep the PR draft until a real two-message source/body resolution round trip passes on claw, the runtime is deployed to ai/claw, and ai-stack's reader invokes the claw-local resolver over authenticated SSH.
