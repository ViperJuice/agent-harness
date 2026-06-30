# Phase-Loop Harness Capability Matrix

PROMOTE finalizes the operator-facing maturity story for Codex, Claude Code /
ThawedCode, Gemini CLI, OpenCode, Pi Agent, and the generic command adapter as of
2026-04-27. This matrix now serves as the frozen operator contract that
follows the v6 live-proof ledger: preserve the earlier probe-backed notes,
compare each harness against the same lifecycle fields, and keep registry
promotion claims separate from operator-facing maturity labels so public docs
do not imply broader autonomous support than the disposable live proof actually
established. Authenticated disposable live proof remains opt-in through
`scripts/smoke-phase-loop-live-adapters`, while cheap checks and offline smoke
stay the default verification path. The v6 proof ledger lives at
`docs/phase-loop/claude-code-v6-live-proof.md`.

## Maturity Labels

| Surface | Maturity | Operator note |
| --- | --- | --- |
| Codex | Live-supported | Full disposable live proof is supported, but active Codex-thread runs may intentionally skip the nested Codex live slice; run that proof from a normal shell session. |
| Claude Code | Proof-blocked | `PROMOTE` keeps autonomous Claude closeout conservative even after `LIVEPROOF` and `CTXBUNDLE`; manual reentry and manual-import closeout remain supported. |
| Gemini CLI | Live-supported | Shared state and closeout contracts are verified; trust posture remains visible in launch metadata. |
| OpenCode | Live-supported | Shared state and closeout contracts are verified; permission posture remains explicit in launch metadata. |
| Pi Agent | Live-supported | Preferred simple-lane child runner using repo-local `phase-loop-pi/**` and `pi-config/**`; no global scheduler, ledger, worktree allocation, or merge-reducer authority. |
| Generic `command` adapter | Experimental | Opt-in wrapper only, context-file delivery only, and not promoted as a first-class live harness. |
| ThawedCode-specific automation | Manual-only | Grouped with Claude for docs and manual imports, but not independently proven as a separate autonomous adapter. |

### Claude Support Slices

| Claude slice | Maturity | Operator note |
| --- | --- | --- |
| `claude_solo` | `proof-blocked` | Default non-interactive Claude launch path. Auth is healthy, but disposable autonomous closeout still lacks a fully green proof run. |
| `claude_delegated_worker` | `proof-blocked` | Runner-brokered Claude child work stays proof-blocked until the same closeout contract is proven under child execution. |
| `claude_subagent` | `experimental` | Native subagents are governed and bounded, but remain an internal Claude collaboration mode rather than a promoted cross-harness worker path. |
| `claude_agent_team` | `experimental` | Native teams stay opt-in, env-gated, and evidence-gated. `PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST` only enables disposable proof, not general promotion. |

## Parity Vocabulary

PROFILEDOC adds the v8 default profile story without changing maturity labels:
roadmap and phase planning are high or xhigh-capable, lane execution and repair
are medium, and reducer/review/verify work is high. Gemini CLI follows the
GEMPROBE decision `gemini_cli_config_unverified_but_usable`: normal
phase-loop use targets built-in Gemini CLI routing aliases (`pro` for
planning/review, `auto` for execution/repair), while explicit proof runs may
use run-local user-scope `modelConfigs.customAliases` that carry
`thinkingConfig.thinkingLevel`. Model provenance and phase-loop metrics stay
recorded in launch metadata and `.phase-loop/metrics.jsonl`.

PARITY freezes two different vocabularies and they must not be conflated:

| Contract surface | Frozen labels | Meaning |
| --- | --- | --- |
| Operator maturity | `live-supported`, `proof-blocked`, `experimental`, `manual-only` | What the docs may claim operators can trust today. |
| Registry promotion | `live`, `proof_gated`, `manual_only` | What the shared code may advertise for autonomous selection and promotion logic. |
| Failure reduction | `adapter_failure`, `phase_failure` | Whether the executor path failed to satisfy the contract or the requested phase work itself failed inside the contract. |
| Blocker posture | `human_required`, `repairable_non_human` | Whether a real operator action is required or the runner should route back through repair/planning. |

For this closeout, Claude stays operator-`proof-blocked` and registry
`promotion_status=proof_gated`. Within Claude, `claude_solo` and
`claude_delegated_worker` remain `proof-blocked`, while `claude_subagent` and
`claude_agent_team` remain operator `experimental` and registry
`promotion_status=proof_gated`. Gemini CLI and OpenCode remain operator
`live-supported` with registry `promotion_status=live`. The generic `command`
adapter stays operator `experimental` and registry `manual_only`.

## Frozen Lifecycle Matrix

Every harness comparison below is reduced against the same operator fields:

| Lifecycle field | What the row must answer |
| --- | --- |
| Launch entrypoint | Non-interactive command shape and required invocation posture |
| Auth preflight | Metadata-only login or subscription probe and blocker reduction |
| Prompt / context delivery | Whether workflow context arrives as prompt-only, inline, context-file, or manual delivery |
| Process lifecycle | Child supervision, timeout posture, and cleanup expectations |
| Output capture | Durable logs or structured response surfaces available to the runner |
| Heartbeat / observability | Evidence available while the child is still running |
| Terminal summary / closeout | Whether the executor reduces back into the shared `automation:` and `terminal-summary.json` contract |
| Manual reentry | Artifact-led recovery expectations after blocked or incomplete autonomous runs |

## Shared Matrix Fields

Each harness section still records the probe-backed details that explain the
frozen lifecycle claims:

| Field | Meaning |
| --- | --- |
| Prompt input | How the harness accepts a non-interactive prompt or instruction body |
| CWD selection | How the harness chooses the working directory or workspace root |
| Model flags | CLI flags or config surfaces for model selection |
| Reasoning / effort flags | Explicit reasoning or effort controls when documented or probed |
| Permission / approval flags | Approval, sandbox, or tool-permission controls |
| Streaming / log behavior | Structured output, stream output, or log-oriented surfaces |
| Exit-code semantics | Whether the surfaced contract documents or exposes process return status |
| Context / skill / instruction injection | Native ways to inject instructions, skills, rules, policies, or prompt files |
| Installed-skill discovery / install paths | Current documented or repo-local skill discovery and install paths |

`CTXDELIV` freezes shared delivery vocabulary for all adapters as
`prompt_only`, `inline`, `stdin`, `context_file`, and `manual`. When a harness
uses `context_file`, the runner writes `.codex/phase-loop/runs/<run-id>/context.md`
beside `launch.json` and records only redacted metadata such as
`context_path`, `context_sha256`, `expected_skill_pack`,
`skill_bundle_sha256`, and `fallback_mode`.

DFHARNESSPOLICY freezes harness selection defaults: simple bounded
scheduler-assigned lane execution defaults to Pi Agent, Claude/Anthropic model
lanes default to Claude Code CLI unless policy explicitly selects a Pi-wrapped
Claude route with an override reason, and Codex/Gemini fallback routes remain
CLI-based and reason-coded. `manual` and generic `command` routes stay
non-default unless selected by operator, roadmap, or phase-plan policy.

## Safe Metadata-Only Auth and Subscription Probes

`LIVEBASE` freezes auth preflight as metadata-only probing. Live child launch
must not depend on API keys for Claude Code, Gemini CLI, or OpenCode; the proof
bar is an authenticated local CLI or subscription session that can be checked
without mutating repo state.

| Harness | Safe probe contract | Blocker reduction if missing |
| --- | --- | --- |
| Claude Code / ThawedCode | `claude --version`, `claude --help`, and metadata-only `claude auth status` to confirm the CLI exists, exposes the documented non-interactive contract, and is logged into a subscription-backed local session. | `account_or_billing_setup` |
| Gemini CLI | `gemini --version` plus `gemini --help` or another help-only surface to confirm the installed CLI and current approval/output flags. | `account_or_billing_setup` |
| OpenCode | `opencode --version`, `opencode run --help`, and `opencode agent list` to confirm the installed CLI, scripted surface, and current agent inventory without mutating repo state. | `account_or_billing_setup` |
| Pi Agent | `pi --version` and `pi --help` to confirm the installed CLI and help surface without reading provider credentials or mutating repo state. | `account_or_billing_setup` |

These probes are intentionally metadata-only. Launch metadata may record command
availability, auth/help-surface presence, selected maturity, blocker class,
profile source, override reason, fallback applied, and fallback reason. It must
not record raw command stdout, raw command stderr, secrets, local environment
values, OAuth material, raw provider payloads, or credential payloads. Later
live-adapter phases may add stronger disposable proof, but they must not switch
to API-key execution or quietly broaden auth preflight into a mutating login
flow.

## Pipeline Bridge Boundary

The Pipeline bridge proof is fake and local by default. Governed Pipeline owns
roadmap-wide orchestration, bundle refresh, closeout ingest, Portal lifecycle
state, and `pipeline.definition.json`; dotfiles handles one delegated phase and
preserves the harness lifecycle contract for that phase. Pipeline-aware runs
may carry `phase-source-bundle.v1` metadata into planning and export
`phase_loop_closeout.v1` with redacted evidence refs. DFFAKESMOKE records the
local substrate receipt at `docs/phase-loop/dffakesmoke-substrate-receipt.md`
and uses `vendor/phase-loop-runtime/tests/fixtures/phase_loop_fake_smoke/matrix.json` to name the fake
success, failure, default-dispatch, smoke-wrapper, and receipt proof matrix.
`phase_loop_closeout.v1` after execution, but standalone phase-loop operation
stays supported without Pipeline metadata or `.pipeline/**` state.

Migration triage follows the same boundary: stale protected-source evidence
routes to Pipeline bundle refresh, while local closeout, dispatch, smoke, or
runner-contract failures route to dotfiles repair. Authenticated live harness
smokes continue to require explicit environment gates; the Pipeline bridge
proof does not make live Pipeline execution a default prerequisite.

Skill examples must keep Portal contracts, Portal projection, Greenfield
authority files, and Greenfield metadata-only authority refs mediated through
governed-pipeline closeout ingest. They are not direct dotfiles write targets,
and standalone dotfiles use remains valid without Pipeline metadata. No harness
may infer writes to `.pipeline/**`, governed-pipeline specs, Portal contracts,
Greenfield authority files, private evidence, raw data, raw evidence,
credentials, provider payloads, or legacy `.codex/phase-loop/` state without
an active plan and source bundle that explicitly own the exact path or glob.

DFTRUTHSOAK keeps the same boundary for the final truth soak: standalone
dotfiles closeout proof, pipeline-required closeout proof, stale-input
blockers, source-truth advisory hints, bridge fixtures, and downstream mirror
refs are valid evidence. Portal-facing proof is projection metadata owned by
governed-pipeline, and Greenfield-facing proof is metadata-only authority refs
owned by governed-pipeline. Neither surface promotes Portal or Greenfield
writes from dotfiles.

## Codex

Maturity: `live-supported`

| Field | Current finding |
| --- | --- |
| Prompt input | `codex exec [PROMPT]` accepts a prompt argument; if the prompt is omitted or `-` is used, stdin is read and appended as a `<stdin>` block when both are present. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`. |
| CWD selection | `codex exec -C <DIR>` selects the working root, and `--add-dir <DIR>` adds writable directories beside the primary workspace. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`. |
| Model flags | `codex exec --model <MODEL>` and `--profile <CONFIG_PROFILE>` are available. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`. |
| Reasoning / effort flags | The current local CLI surface does not expose a dedicated `--effort` flag on `codex exec`; the current runner sets reasoning via `-c model_reasoning_effort="..."` against config. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`; repo-local source inspection: `sed -n '1,240p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. |
| Permission / approval flags | `codex exec --sandbox <read-only|workspace-write|danger-full-access>`, `--full-auto`, and `--dangerously-bypass-approvals-and-sandbox` are available. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`. Official doc: the Codex CLI docs describe approval modes and scripting with `exec`, but the exact adapter-safe flags in this repo should still be taken from the probed CLI help because the current runner uses them directly. Source: <https://developers.openai.com/codex/cli>. |
| Streaming / log behavior | `codex exec --json` emits JSONL events and `--output-last-message <FILE>` writes the final message to a file. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`. |
| Exit-code semantics | The CLI help does not document detailed exit-code semantics beyond standard process completion; the runner currently trusts the child process return code surfaced by Python subprocess calls. Local probe: `codex exec --help 2>&1 | sed -n '1,220p'`; repo-local source inspection: `sed -n '1,240p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. |
| Context / skill / instruction injection | The current repo injects workflow intent by building prompt strings such as `codex-plan-phase ...` and `codex-execute-phase ...`; there is no separate adapter abstraction yet. Repo-local source inspection: `sed -n '1,260p' vendor/phase-loop-runtime/src/phase_loop_runtime/prompts.py` and `sed -n '1,260p' vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py`. |
| Installed-skill discovery / install paths | Repo-local docs and bootstrap install Codex workflow skills under `~/.codex/skills/`, with shared skills mirrored from `shared/skills/` and Codex-private workflow skills from `codex-config/skills/`. Repo-local source inspection: `sed -n '1,260p' README.md`, `sed -n '1,240p' claude-config/AGENTS.md`, and `sed -n '1240,1395p' bootstrap.sh`. Official doc: the Codex CLI setup page documents install and sign-in but not repo-specific skill deployment. Source: <https://developers.openai.com/codex/cli>. |

## Claude Code / ThawedCode

Maturity: Claude Code is `proof-blocked` for autonomous live dispatch until the
authenticated non-interactive planning smoke completes; ThawedCode-specific
automation remains `manual-only` until independently proven.

`BASELINE` adds the 2026-06-18 route policy freeze without changing runtime
behavior. The validated facts are: normal `claude -p` worked under Claude Code
subscription auth; `--bare -p` failed without API-key or helper auth; fakechat
Channel ingress and reply worked without spawning `claude -p`; and remote
permission relay remains unproven. `IF-0-BASELINE-1` therefore freezes the
Claude route policy as Channel-first for managed local sessions, Agent View for
async dispatch, and `claude -p` only as a compatibility route when the task
explicitly accepts one-shot and billing-sensitive execution.

`CHANNEL` adds the repo-local local-only proof surface for that replacement
direction. `IF-0-CHANNEL-1` freezes the harness event envelope as
`{event_id, session_id, sender, content, attachments, created_at, ack_policy}`:
the loopback sidecar generates `event_id` once, preserves it through delivery
and acknowledgement state, keeps `sender` non-secret, restricts attachments to
metadata refs, and uses `ack_policy=tool_ack_required`. `IF-0-CHANNEL-2`
freezes the Channel tool payload as
`{event_id, status, text, artifacts, error, final}` with `status` limited to
`received`, `working`, `blocked`, `done`, or `error`; `final=true` acknowledges
delivery only after the sidecar records a matching `reply` or `status` tool
call. The proof remains loopback-only, does not use `claude -p`, and leaves
permission relay explicitly deferred to `PERMISSION`. On this host, the offline
contract checks, dry-run proof, and live acknowledgement proof pass under
Claude Code 2.1.181 with normal Claude Max subscription auth. The proved launch
shape is
`claude --dangerously-load-development-channels server:phase-loop-channel --allowedTools mcp__phase-loop-channel__reply,mcp__phase-loop-channel__status --model sonnet`.
The Claude-facing channel process uses the MCP SDK over stdio, polls the Python
loopback sidecar, emits `notifications/claude/channel`, and records delivery
only after Claude calls the hyphenated MCP tool identifier
`mcp__phase-loop-channel__status` or `mcp__phase-loop-channel__reply`.

`DFCHCONTRACT` freezes the metadata-only Claude route result that governed
adapters mirror before route behavior changes. `IF-0-DFCHCONTRACT-1` is:
`{route, session_id, event_id, status, text, artifacts, auth_posture, billing_posture, trust_state, permission_state, warnings, evidence_refs}`.
Route is limited to `claude_channel`, `claude_agent_view`, or `claude_print`.
`auth_posture` records `subscription_local`, `api_key`, or `unknown`;
`billing_posture` records `subscription_included`, `api_key_billed`,
`usage_credit`, or `unknown` and is never inferred from command success alone.
The local governed default remains `claude_channel`; `claude_agent_view` is the
async route; `claude_print` is billing-sensitive compatibility and must not be
used as a silent fallback from a failed Channel preflight. `IF-0-DFCHCONTRACT-2`
keeps `phase_loop_closeout.v1` governed-compatible through nested
`verification.status` and `source_bundle.pipeline_mode`. The fixture
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfchcontract_claude_route_evidence.json`
is the dotfiles-owned mirror target for governed-pipeline v25.

`PERMISSION` adds the local-only security envelope for that Channel route.
`IF-0-PERMISSION-1` freezes permission requests as
`{request_id, session_id, tool_name, description, input_preview, risk_class, requested_at}`
and permission verdicts as
`{request_id, verdict, actor, reason, decided_at}` with `verdict` limited to
`allow` or `deny`. The sidecar accepts only non-secret `input_preview` metadata
and rejects raw tool input payload fields. When a local bearer token is
configured, harness message writes and permission verdict writes require
`Authorization: Bearer <token>`; missing auth fails with 401 and wrong tokens
fail with 403. Sender allowlists fail closed before a message can be enqueued.
Verdict audit entries preserve `{request_id, session_id, verdict, actor, reason, decided_at}`
and never serialize raw tool inputs or bearer tokens. The Channel plugin
declares permission relay support while preserving `experimental.claude/channel`
and the hyphenated `mcp__phase-loop-channel__reply` /
`mcp__phase-loop-channel__status` tools. Permission notifications contain only
`request_id`, `tool_name`, `description`, `input_preview`, `risk_class`, and
`requested_at`, and instruct operators to route allow/deny decisions through
the authenticated local harness endpoint instead of raw terminal control.
Remote non-loopback transport and broader workspace trust changes remain
explicit non-goals for this phase.

`SESSION` adds the managed local session layer without changing the route
policy. `IF-0-SESSION-1` freezes `GET /sessions` as
`{"sessions": [...]}` where each session record contains
`{session_id, adapter, cwd, state, auth_posture, trust_state, channel_health, last_event_id, last_reply_at, permission_state}`.
The `claude_channel` adapter records non-secret launch metadata before
dispatch, keeps `state` to `disconnected`, `starting`, `ready`, `blocked`,
`stopped`, or `stale`, and keeps `channel_health` to `disconnected`,
`starting`, `ready`, `blocked`, or `stopped`. Channel replies update
`last_event_id`, `last_reply_at`, and ready or blocked health; permission audit
events update `permission_state` without exposing raw tool input, bearer
tokens, or local environment values.

The managed launch wrapper preserves the proved Channel command shape
(`server:phase-loop-channel` with the hyphenated
`mcp__phase-loop-channel__reply` and `mcp__phase-loop-channel__status` tool
identifiers) and preflights workspace trust, repo-local `.mcp.json` trust
posture including `pmcp` pending approval, Claude auth posture, and hidden
Channel support before dispatch. Notification, Stop, and UserPromptSubmit hooks
fan out metadata-only hook events to the sidecar when
`PHASE_LOOP_CHANNEL_SIDECAR_URL` and a session id are present, while preserving
the existing Zellij notification and clear behavior when no sidecar is active.
Cleanup now recognizes stale managed Channel session processes while retaining
the protected harness process allowlist.

`HARDEN` packages that Channel path as a repeatable local install contract.
`IF-0-HARDEN-1` freezes
`claude-config/plugins/phase-loop-channel/.claude-plugin/plugin.json`,
`package.json`, `bun.lock`, the plugin README, root README, ONBOARDING, and
`scripts/launch-claude-channel-session --dry-run --json` as the clean-machine
setup surface. The dry-run reports
`{session_id, adapter, cwd, state, auth_posture, trust_state, channel_health, command, plugin_dir}`
without secrets and preserves `server:phase-loop-channel` plus the hyphenated
`mcp__phase-loop-channel__reply` and
`mcp__phase-loop-channel__status` tool identifiers. Generated dependency trees
such as `node_modules` remain install output, not package contract files.

`IF-0-HARDEN-2` keeps the sidecar local-only by default. Remote client posture
stays blocked unless explicit bearer-token or equivalent harness auth plus
sender allowlist policy exists before message, reply/status, or
permission-verdict writes. Permission audit entries remain
`{request_id, session_id, verdict, actor, reason, decided_at}`; redaction tests
cover bearer tokens, local env values, raw tool input, OAuth/keychain payloads,
terminal transcripts, and provider payload fields.

`IF-0-HARDEN-3` freezes the operator smoke story: offline Channel dry-run,
opt-in live Channel ingress/reply proof, opt-in permission-relay proof, Agent
View blocked/completed lifecycle checks, and `claude -p` print compatibility.
Each smoke reports skipped, blocked, or passed with a non-secret summary.
Fakechat remains validation tooling only and is not promoted to shared default
Claude settings.

`BG` adds Agent View as a secondary async Claude Code path and freezes
`IF-0-BG-1`. The adapter lists background sessions with
`claude agents --json --all` and reduces each record to non-secret
`AgentViewSession` metadata:
`{id, session_id, cwd, kind, state, status, name, started_at, pid}`. State
reduction is limited to `running`, `done`, `blocked`, `stopped`, `failed`, and `unknown`;
unknown CLI states are retained as unknown metadata instead of being treated as
success. Agent View launch preparation builds `claude --bg` commands only after
workspace and repo-local `.mcp.json` trust preflights pass, including
`pmcp_pending_approval`; trust or unsupported launch posture is reported as a
structured blocker and never silently falls back to `claude -p`.

Agent View treats `claude logs <id>` as human-readable operator text, not a
stable machine-state API. The adapter exposes command builders for
`claude attach <id>`, `claude stop <id>`, and `claude rm <id>` without parsing
terminal output as a contract. Stopped and done sessions are eligible for
documented `claude rm <id>` cleanup, while blocked records are not force-removed
by list or status operations; if `claude rm` refuses a blocked record, the
adapter reports an operator-visible blocker summary and does not mark the
session deleted. BG does not promote Agent View to the default Claude route,
does not parse `~/.claude/jobs` as the primary API, and does not change the
Channel-first policy for managed local sessions.

`DFSMOKE` closes the migration baseline for operator routing. The primary local
route is `PHASE_LOOP_CLAUDE_ROUTE=channel` with
`PHASE_LOOP_CLAUDE_CHANNEL_URL` and `PHASE_LOOP_CHANNEL_SESSION_ID` or
`PHASE_LOOP_CLAUDE_CHANNEL_SESSION_ID`; this preserves
subscription-authenticated local Claude Code sessions and blocks on failed
Channel preflight. The async route is `PHASE_LOOP_CLAUDE_ROUTE=agent_view` and
uses Agent View / `claude --bg` lifecycle metadata. The CI or one-shot route is
`PHASE_LOOP_CLAUDE_ROUTE=print`, is billing-sensitive `claude -p`
compatibility, and must be selected explicitly. Emergency PTY/tmux control is a
manual fallback for legacy TUI sessions only, not the first-class control plane.

`MIGRATE` may consume `IF-0-SESSION-1` and `IF-0-BG-1` only after BG
verification passes and dirty paths are limited to the BG owned files. SESSION
does not migrate phase-loop Claude dispatch, does not remove the existing local
notification path, does not broaden remote transport, and does not write
secrets.

`CLIRESEARCH` on 2026-04-27 froze
`docs/phase-loop/claude-code-v6-research.md` as the current v6 Claude baseline.
That artifact and `docs/phase-loop/claude-code-v6-live-proof.md` are the
source of truth for the local CLI probe ledger, official doc coverage, PMCP /
Context7 replay notes, teammate-mode reconciliation, the disposable live-proof
results, and the risk table consumed by `CLAEXEC`, `PLUGINPACK`, `TEAMGOV`,
`TASKLEDGER`, `DELEGATE`, and `LIVEPROOF`. `PROMOTE` reduces those inputs into
four support slices and keeps only the currently defensible claims published.

`TEAMGOV` now freezes a second-layer posture inside the Claude adapter:
`solo`, `subagent`, and `agent_team` execution modes are tracked separately in
launch metadata. `PROMOTE` adds a fourth operator-facing slice,
`claude_delegated_worker`, for runner-brokered Claude child work. `solo`
remains the launch default; `subagent` and `agent_team` stay operator
`experimental`, require a team-safe phase-plan ownership assessment, and keep
task-list or teammate controls denied by default until `TASKLEDGER` and
`LIVEPROOF` add stronger observability and proof. Runner-brokered Claude child
work stays proof-blocked until the same autonomous closeout contract is proven
under child execution.

| Field | Current finding |
| --- | --- |
| Prompt input | Claude Code defaults to interactive mode and uses `-p, --print` for non-interactive output. The runner keeps explicit `PHASE_LOOP_CLAUDE_ROUTE=print` compatibility with `claude -p --verbose --output-format stream-json`, sends Channel route prompts through the local sidecar, and builds Agent View async prompts with `claude --bg`. Print is billing-sensitive compatibility and never a silent fallback from Channel or Agent View failure. Local probe: `claude --help 2>&1 | grep -nE -- '--print|--output-format|--permission-mode|--plugin-dir|--worktree|--add-dir|--effort|--model|--system-prompt|--append-system-prompt'`. Official doc: Anthropic documents `--print` as the non-interactive surface. Source: <https://code.claude.com/docs/en/cli-reference>. |
| CWD selection | The CLI exposes `--add-dir <directories...>` for additional tool access and `-w, --worktree [name]` for a new git worktree session. Local probe: `claude --help 2>&1 | grep -nE -- '--print|--output-format|--permission-mode|--plugin-dir|--worktree|--add-dir|--effort|--model|--system-prompt|--append-system-prompt'`. Official doc: Anthropic documents both `--add-dir` and `--worktree`. Source: <https://code.claude.com/docs/en/cli-reference>. |
| Model flags | `claude --model <model>` is available. Local probe: `claude --help 2>&1 | grep -nE -- '--print|--output-format|--permission-mode|--plugin-dir|--worktree|--add-dir|--effort|--model|--system-prompt|--append-system-prompt'`. Official doc: Anthropic documents `--model`. Source: <https://code.claude.com/docs/en/cli-reference>. |
| Reasoning / effort flags | `claude --effort <low|medium|high|xhigh|max>` is available. Local probe: `claude --help 2>&1 | grep -nE -- '--print|--output-format|--permission-mode|--plugin-dir|--worktree|--add-dir|--effort|--model|--system-prompt|--append-system-prompt'`. Official doc: Anthropic documents the flag in current CLI reference. Source: <https://code.claude.com/docs/en/cli-reference>. |
| Permission / approval flags | `--permission-mode` supports `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, and `plan`; `--dangerously-skip-permissions` also exists in current local help. Local probe: `claude --help 2>&1 | sed -n '1,120p'`. Official doc: Anthropic documents `--permission-mode`; current repo execution should still probe the live CLI before locking exact values. Source: <https://code.claude.com/docs/en/cli-reference>. |
| Streaming / log behavior | `--output-format <text|json|stream-json>` and `--input-format <text|stream-json>` are documented for print mode. The current runner uses `--output-format stream-json`; downstream reduction consumes the streamed events and closeout text rather than relying on Channel ingress for this compatibility route. Local probe: `claude --help 2>&1 | sed -n '1,120p'`. Official doc: Anthropic documents both surfaces. Source: <https://code.claude.com/docs/en/cli-reference>. |
| Exit-code semantics | The current official CLI reference enumerates print-mode output formats but does not publish a detailed exit-code contract for automation in the surfaced page reviewed here. Mark as unknown pending a stronger vendor guarantee. |
| Context / skill / instruction injection | Claude Code supports `--system-prompt`, `--append-system-prompt`, `--plugin-dir`, `--agents`, `--settings`, and `--mcp-config`; `--bare` explicitly says skills still resolve via `/skill-name` when present, but the 2026-06-18 validation keeps bare mode outside the subscription-auth route. The current runner materializes a run-local bundle, passes `--plugin-dir`, `--settings`, inline `--agents` JSON, `--mcp-config`, `--tools`, `--disallowedTools`, `--add-dir`, `--model`, `--effort`, and optional `--json-schema`, and records only redacted bundle-path and hash metadata. Local probe: `claude --help 2>&1 | sed -n '1,120p'`; repo-local source inspection: `build_claude_command(...)`, `_resolve_command_context(...)`, `_claude_bundle_paths(...)`, and `_claude_context_prompt(...)` in `vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. Official docs: Anthropic documents plugin-directory, dynamic `--agents` JSON, and plugin component layout. Sources: <https://code.claude.com/docs/en/cli-usage>, <https://code.claude.com/docs/en/plugins-reference>. |
| Installed-skill discovery / install paths | Repo-local docs install Claude workflow skills at `~/.claude/skills/`, shared skills from `shared/skills/`, and Claude-private workflow skills from `claude-config/claude-skills/`. Repo-local source inspection: `sed -n '1,260p' README.md`, `sed -n '1,240p' claude-config/AGENTS.md`, and `sed -n '1240,1395p' bootstrap.sh`. ThawedCode status: no separate official ThawedCode CLI documentation was found during this phase, so downstream work should treat ThawedCode as an unverified Claude-compatible variant rather than a separately cleared adapter contract. |

Current permission mapping remains a compatibility-launch inventory item:
`_claude_permission_mode(...)` maps review to `plan`, execute and repair to
`bypassPermissions`, other actions to `acceptEdits`, and explicit
`bypass_approvals` to `bypassPermissions` plus
`--dangerously-skip-permissions`. BASELINE records this behavior for later
route-policy work and does not endorse broadening it.

DOCSCLOSE authenticated proof on 2026-04-26 confirmed `claude auth status`
with a subscription-backed local session, then observed the disposable
`claude-plan-phase` child produce no closeout before the runner timeout. This
is a live-dispatch proof blocker, not an auth blocker; keep Claude manual TUI
reentry and manual imports available through the shared state ledger.

### Frozen Claude Failure Inventory

The proof-blocking Claude cases are explicit parity failures, not vague
warnings:

| Failure case | Frozen reduction |
| --- | --- |
| Non-interactive timeout before closeout | `adapter_failure` plus operator `proof-blocked`; route to repair instead of describing the phase as complete. |
| Empty or unusable output capture | `adapter_failure`; the runner must not treat an empty log or malformed JSON body as success. |
| Missing `automation:` block | `adapter_failure` and repairable non-human blocker because the child did not emit the shared closeout contract. |
| Missing terminal summary | `adapter_failure` and repairable non-human blocker because closeout and monitor surfaces are incomplete. |
| Stale handoff or state after repair | `adapter_failure` until a repair flow writes fresh shared artifacts that supersede the stale ones. |

## Gemini CLI

Maturity: `live-supported`

| Field | Current finding |
| --- | --- |
| Prompt input | Gemini CLI defaults to interactive mode and uses `-p, --prompt` for non-interactive headless mode; positional query arguments remain interactive by default. Local probe: `gemini --help 2>&1 | sed -n '1,220p'`. Official doc: Gemini CLI configuration docs describe non-interactive output and currently mark `--prompt` as deprecated in favor of positional arguments, which means adapter work must pin to the live CLI it launches. Source: <https://geminicli.com/docs/reference/configuration/>. |
| CWD selection | `-w, --worktree` creates a new git worktree session, and `--include-directories` expands the workspace. Headless disposable runs also need `--skip-trust` or equivalent workspace trust configuration; otherwise Gemini overrides approval mode and exits before the run starts. Local probe: `gemini --help 2>&1 | sed -n '1,220p'` plus a disposable `gemini -p ... --approval-mode plan --output-format json` smoke. Official doc: current configuration docs describe extension and output surfaces; the local help is the authoritative probe for the present launcher-ready flags. Source: <https://geminicli.com/docs/reference/configuration/>. |
| Model flags | `gemini --model <model>` is available. Local probe: `gemini --help 2>&1 | sed -n '1,220p'`. Official doc: Gemini CLI configuration documents `--model`. Source: <https://geminicli.com/docs/reference/configuration/>. |
| Reasoning / effort flags | No dedicated reasoning-effort flag was documented or probed in the current CLI surface reviewed here. Plan/read-only behavior exists via `--approval-mode plan` and planning tools, but that is not equivalent to a model reasoning knob. Local probe: `gemini --help 2>&1 | sed -n '1,220p'`. Official doc: Gemini planning tools document read-only plan mode. Source: <https://geminicli.com/docs/tools/planning/>. |
| Permission / approval flags | `--approval-mode <default|auto_edit|yolo|plan>`, `--sandbox`, and deprecated `--allowed-tools` exist; the policy engine ties rules to approval modes. Local probe: `gemini --help 2>&1 | sed -n '1,220p'`. Official doc: Gemini policy-engine docs describe approval modes, including `plan`. Source: <https://geminicli.com/docs/reference/policy-engine/>. |
| Streaming / log behavior | `--output-format <text|json|stream-json>` is available. Local disposable smoke on 2026-04-26 showed `json` returning a top-level `response` string after warning prelude lines, and `stream-json` emitting assistant `message` deltas before a final `result` record. Official doc: Gemini configuration documents `text`, `json`, and `stream-json`. Source: <https://geminicli.com/docs/reference/configuration/>. |
| Exit-code semantics | The reviewed Gemini docs and help output do not publish an adapter-safe exit-code contract beyond normal process completion. Mark as unknown. |
| Context / skill / instruction injection | Gemini supports policy injection via `--policy` and `--admin-policy`, plus extension selection via `--extensions`. Skills are a native surface and load on demand once activated. Local probe: `gemini --help 2>&1 | sed -n '1,220p'; gemini skills --help 2>&1 | sed -n '1,220p'; gemini extensions --help 2>&1 | sed -n '1,220p'`. Official doc: Gemini skills docs describe user, workspace, and extension skill tiers, and extension docs describe extension-based packaging. Sources: <https://geminicli.com/docs/cli/skills/>, <https://geminicli.com/extensions/about/>. |
| Installed-skill discovery / install paths | Official doc: Gemini discovers workspace skills from `.gemini/skills/` or `.agents/skills/`, user skills from `~/.gemini/skills/` or `~/.agents/skills/`, and extension skills from installed extensions. Source: <https://geminicli.com/docs/cli/skills/>. Repo-local docs and bootstrap deploy Gemini workflow skills to `~/.gemini/skills/` from `gemini-config/skills/`, and shared skills to both `~/.gemini/skills/` and `~/.agents/skills/`. Repo-local source inspection: `sed -n '1,260p' README.md`, `sed -n '1,240p' claude-config/AGENTS.md`, and `sed -n '1240,1395p' bootstrap.sh`. |

## OpenCode

Maturity: `live-supported`

| Field | Current finding |
| --- | --- |
| Prompt input | `opencode run [message..]` is the non-interactive command surface in the currently installed CLI. Local probe: `opencode run --help 2>&1 | sed -n '1,220p'`. Official doc: the OpenCode CLI docs describe `opencode run` as the command surface for scripted use. Source: <https://opencode.ai/docs/cli/>. |
| CWD selection | `opencode run --dir <path>` selects the directory, and `--attach` can target a running server. Local probe: `opencode run --help 2>&1 | sed -n '1,220p'`. Official doc: OpenCode CLI docs document `--attach`; the local help exposes `--dir` in the currently installed build. Source: <https://opencode.ai/docs/cli/>. |
| Model flags | `opencode run --model <provider/model>` is available. Local probe: `opencode run --help 2>&1 | sed -n '1,220p'`. Official doc: OpenCode CLI docs and model docs use the `provider/model` form. Sources: <https://opencode.ai/docs/cli/>, <https://opencode.ai/docs/models>. |
| Reasoning / effort flags | `opencode run --variant <provider-specific reasoning effort, e.g. high, max, minimal>` exists in the installed CLI. Local probe: `opencode run --help 2>&1 | sed -n '1,220p'`. Official doc: the reviewed docs show model selection but do not yet surface a stable generic reasoning-variant contract for automation; treat `--variant` as probe-backed, not doc-frozen. |
| Permission / approval flags | `--dangerously-skip-permissions` exists in the current CLI; OpenCode config and agent docs define permission states such as `allow`, `ask`, and `deny`, and current defaults are permissive unless config narrows them. The phase-loop live adapter therefore records permission posture explicitly and fails closed unless the runner intentionally opts into the permissive path. Local probes: `opencode run --help 2>&1 | sed -n '1,220p'` and `opencode agent list`. Official docs: OpenCode config and permissions docs define the permission model and default allow posture. Sources: <https://opencode.ai/docs/config/>, <https://opencode.ai/docs/permissions/>. |
| Streaming / log behavior | `opencode run --format <default|json>` exposes formatted vs raw JSON event output; `--print-logs` sends logs to stderr. Local probe: `opencode run --help 2>&1 | sed -n '1,220p'`. Official doc: OpenCode CLI docs document JSON formatting for scripted output. Source: <https://opencode.ai/docs/cli/>. |
| Exit-code semantics | The reviewed docs do not publish a detailed non-interactive exit-code contract. Mark as unknown. |
| Context / skill / instruction injection | OpenCode config supports `instructions` files, agent markdown/config files, and prompt files such as `prompt: {file:...}` for agents. The live phase-loop adapter keeps the prompt body minimal and points the child at run-local `.codex/phase-loop/runs/<run-id>/context.md`, treating repo-sourced injected context as authoritative and installed-skill drift as warning-only metadata. Local probes: `opencode agent --help 2>&1 | sed -n '1,220p'` and repo-local source inspection of `vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. Official docs: config and agents docs cover instruction files, prompt files, and markdown agents. Sources: <https://opencode.ai/docs/config/>, <https://opencode.ai/docs/agents/>. |
| Installed-skill discovery / install paths | Official doc: OpenCode discovers skills from `.opencode/skills/`, `~/.config/opencode/skills/`, `.claude/skills/`, `~/.claude/skills/`, `.agents/skills/`, and `~/.agents/skills/`, walking upward to the git worktree for project-local paths. Source: <https://opencode.ai/docs/skills>. Repo-local docs and bootstrap deploy OpenCode workflow skills to `~/.config/opencode/skills/` from `opencode-config/skills/`, while shared skills are mirrored into both `~/.config/opencode/skills/` and `~/.agents/skills/`. Repo-local source inspection: `sed -n '1,260p' README.md`, `sed -n '1,240p' claude-config/AGENTS.md`, and `sed -n '1240,1395p' bootstrap.sh`. |

## Generic Command Adapter

Maturity: `experimental`

| Field | Current finding |
| --- | --- |
| Prompt input | `CMDENTRY` freezes the public generic surface as an explicit `--executor command` path plus `--command-name` and `--command-template`. The current implementation supports only `context_file` delivery: the template must include `{context_file}` so the runner can deliver the repo-sourced workflow bundle without inlining raw context into argv. Repo-local source inspection: `sed -n '1,260p' vendor/phase-loop-runtime/src/phase_loop_runtime/cli.py` and `sed -n '260,520p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. |
| CWD selection | The runner keeps cwd runner-owned and records it as `wrapped_cwd` in `launch.json`; templates may reference `{cwd}` or `{repo}`, but the adapter still launches from the selected repo root instead of inventing a separate workspace model. Repo-local source inspection: `sed -n '260,520p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py` and `sed -n '40,110p' vendor/phase-loop-runtime/src/phase_loop_runtime/observability.py`. |
| Model flags | The adapter does not define a universal model flag, but the template may opt into `{model}` when the wrapped command understands one. Unsupported placeholders fail closed. Repo-local source inspection: `sed -n '260,520p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. |
| Reasoning / effort flags | The template may opt into `{effort}` when the wrapped command exposes a reasoning control. No cross-command effort contract is implied beyond explicit placeholder substitution. Repo-local source inspection: `sed -n '260,520p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. |
| Permission / approval flags | The generic adapter does not normalize wrapped-command approval semantics. Permission posture remains `manual`, keeping the runner's safety policy explicit and preventing the adapter from pretending arbitrary subprocesses are first-class live harnesses. Repo-local source inspection: `sed -n '1,220p' vendor/phase-loop-runtime/src/phase_loop_runtime/capability_registry.py`. |
| Streaming / log behavior | Current local source uses `subprocess.run(..., capture_output=True, text=True, check=False)` for no-log execution and `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True)` for log streaming. Repo-local source inspection: `sed -n '1,240p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. Official doc: Python subprocess docs define `run`, `Popen`, and `returncode` behavior. Source: <https://docs.python.org/3.11/library/subprocess.html>. |
| Exit-code semantics | Current local source trusts child `returncode`; Python docs state that `Popen.returncode` is initially `None` and is set once termination is detected. Repo-local source inspection: `sed -n '1,240p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`. Official doc: Python subprocess docs define `returncode`. Source: <https://docs.python.org/3.11/library/subprocess.html>. |
| Context / skill / instruction injection | The frozen generic contract is repo-sourced context-file delivery only. Launch metadata records `command_adapter_name`, `command_template`, `wrapped_cwd`, `context_path`, and `context_sha256`; unsupported command shapes such as missing `{context_file}` or unsupported placeholders block before launch. Repo-local source inspection: `sed -n '1,260p' shared/phase-loop/protocol.md`, `sed -n '260,520p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`, and `sed -n '340,760p' vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py`. |
| Installed-skill discovery / install paths | Not applicable generically. The command adapter receives the repo-sourced workflow bundle directly and does not claim any installed-skill contract for the wrapped command. |

## Local Probe Inventory

All local claims above come from metadata-only help, version, path, or source-inspection commands:

| Probe | Purpose |
| --- | --- |
| `git status --short -- plans/phase-plan-v5-PROMOTE.md` | Confirmed the `PROMOTE` plan artifact was staged before execution. |
| `test ! -e docs/phase-loop/harness-capability-matrix.md && rg -n "build_codex_command|codex-plan-phase|codex-execute-phase|codex-phase-roadmap-builder|codex-phase-loop|subprocess\\.run|Popen" vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py vendor/phase-loop-runtime/src/phase_loop_runtime/prompts.py vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py README.md bootstrap.sh` | Confirmed the current runner is still Codex-specific and the target artifact did not yet exist. |
| `rg -n "~/.claude/skills|~/.codex/skills|~/.config/opencode/skills|~/.gemini/skills|shared/skills|claude-config/claude-skills|codex-config/skills|opencode-config/skills|gemini-config/skills" README.md bootstrap.sh claude-config/AGENTS.md codex-config/shared/runtime-state.md claude-config/shared/runtime-state.md opencode-config/shared/runtime-state.md gemini-config/shared/runtime-state.md` | Reduced repo-local skill install and runtime-state paths. |
| `codex --version`, `claude --version`, `gemini --version`, `opencode --version` | Captured installed CLI versions. |
| `codex exec --help 2>&1 | sed -n '1,220p'` | Captured the current non-interactive Codex adapter surface. |
| `claude --help 2>&1 | sed -n '1,120p'` and `claude --help 2>&1 | grep -nE -- '--print|--output-format|--permission-mode|--plugin-dir|--worktree|--add-dir|--effort|--model|--system-prompt|--append-system-prompt'` | Captured the current Claude Code non-interactive, permission, and prompt-injection surfaces. |
| `gemini --help 2>&1 | sed -n '1,220p'`, `gemini skills --help 2>&1 | sed -n '1,220p'`, and `gemini extensions --help 2>&1 | sed -n '1,220p'` | Captured Gemini CLI headless, policy, skills, and extension surfaces. |
| `opencode run --help 2>&1 | sed -n '1,220p'` and `opencode agent --help 2>&1 | sed -n '1,220p'` | Captured OpenCode run, formatting, permission-skip, directory, and agent-management surfaces. |
| `sed -n '1,260p' README.md`, `sed -n '1,240p' claude-config/AGENTS.md`, `sed -n '1,220p' codex-config/shared/runtime-state.md`, `sed -n '1,220p' claude-config/shared/runtime-state.md`, `sed -n '1,220p' opencode-config/shared/runtime-state.md`, `sed -n '1,220p' gemini-config/shared/runtime-state.md`, and `sed -n '1240,1395p' bootstrap.sh` | Reduced repo-local skill deployment, discovery, and runtime-state paths. |
| `sed -n '1,240p' vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py`, `sed -n '1,260p' vendor/phase-loop-runtime/src/phase_loop_runtime/prompts.py`, and `sed -n '1,260p' vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py` | Confirmed the current runner still hardcodes Codex command construction and prompt wording. |

## Adapter Constraints and Known Limits

1. `vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py` and `vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py` are still Codex-only because launch construction flows through `build_codex_command(...)`; ADAPTER must introduce a neutral executor interface before any non-Codex launch is real.
2. `vendor/phase-loop-runtime/src/phase_loop_runtime/prompts.py` is also Codex-only because the built prompt bodies literally emit `codex-phase-roadmap-builder`, `codex-plan-phase`, `codex-execute-phase`, and `codex-phase-loop` command strings. PROTOCOL can standardize outputs first, but ADAPTER and INJECT must stop assuming the child understands Codex skill names.
3. `README.md` and `bootstrap.sh` already distinguish shared private skills from harness-private workflow skills. INJECT should preserve that split instead of flattening all harnesses into one install root.
4. Gemini and OpenCode both have native skill ecosystems, but they are not contract-compatible with Codex workflow skill names. INJECT should model these as separate delivery modes: native skill discovery where supported, inline or file-based instruction fallback otherwise.
5. Claude Code currently has a documented non-interactive `--print` path plus prompt and plugin injection surfaces, but the DOCSCLOSE disposable planning smoke timed out before closeout. Operator docs should keep `claude_solo` and `claude_delegated_worker` proof-blocked until that verifier passes, while treating `claude_subagent` and `claude_agent_team` as governed experimental slices and keeping ThawedCode-specific automation manual-only unless a later phase verifies it directly.
6. IF-0-BASELINE-1 freezes Claude routing policy for downstream work: Channel-first for managed local sessions, Agent View for async dispatch, and `claude -p` only as the explicit compatibility route when one-shot and billing-sensitive execution is acceptable.
7. OpenCode currently defaults to permissive permissions unless config narrows them, while Codex, Claude Code, and Gemini expose more explicit command-line approval toggles. CAPREG should treat permission posture as part of capability selection instead of assuming one safe default across harnesses.
8. The generic command adapter is not ready for promotion beyond an experimental wrapper until a later roadmap proves how prompt text, cwd, model selection, approval posture, and observability remain constrained across arbitrary commands.
## Shared Compatibility Fixtures

As of `XREPOFIXTURE`, a set of canonical JSON fixtures is available for validating any phase-loop harness or adapter against the `phase_loop_closeout.v1` schema.

### Usage in Validation

All harnesses (Codex, Claude, Gemini, OpenCode, and Generic) can be tested against these scenarios:
- **Location**: `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/`
- **Scenarios**: `complete`, `blocked`, `stale_input`, `failed_verification`, `human_required`.

These fixtures serve as the shared truth for cross-repo compatibility and should be the first point of validation for any new adapter or protocol change.

## DFPROMPTSYNC Readiness

Prompt and skill surfaces consume the DFPROMPTSYNC receipts at
`docs/phase-loop/dfpromptsync-contract-map.md` and
`docs/phase-loop/dfpromptsync-readiness.md`. Harness examples may cite schema
names, assignment fields, fixture paths, artifact refs, and digests, but must
not include raw secrets, raw transcripts, raw diffs, raw provider payloads,
credential file contents, local env values, or prompt-only containment claims.

## DFPARSOAK Integrated Soak

The integrated soak consumes `docs/phase-loop/dfparsoak-receipt.md` and
`docs/phase-loop/dfparsoak-runbook.md`. The route matrix covers Pi Agent
default, Claude Code CLI exception, Codex CLI fallback, and Gemini CLI
fallback with model, effort, policy source, fallback reason, lane, wave,
worktree, and redacted evidence metadata.
