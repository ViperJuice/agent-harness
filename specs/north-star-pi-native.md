# North Star — pi as the Native Execution Substrate

> Status: vision + gated backlog. This document sets direction and binds the
> cross-cutting principles that several roadmaps share. It authorizes **nothing**
> on its own: every backlog item promotes to a real roadmap only when its named
> evidence gate is met. The near-term roadmap that begins de-cornering the
> execute path toward this vision is `specs/phase-plans-v8.md` (EXECDISPATCH).

---

## 1. Vision

Today the phase-loop execute path hardcodes a fixed set of executors
(`codex`, `claude`, `gemini`/`agy`, `opencode`, `pi`, `command`) as if-branches
in `phase-loop-runtime/src/phase_loop_runtime/launcher.py`. Each executor is a
CLI we shell out to. `pi` is one branch among several — a peer, not a substrate.

The north star inverts that relationship: **pi becomes the native execution
substrate** that every other harness can call into, rather than one more sibling
CLI. Four properties define the target state.

**1.1 Callable from any harness.** A run initiated from claude-code, codex,
Antigravity, opencode, or a bare shell resolves to the same pi execution core.
The harness you happen to launch from is an entry point, not a lock-in. This
requires dispatch to resolve against a *registry* (see Principle 2.1), never a
hardcoded branch list — which is exactly what the EXECDISPATCH roadmap's EXECREG
phase delivers.

**1.2 Per-vendor subscription backings, swappable without touching agents.**
Each model vendor exposes its subscription through a different door:

- **CLI where offered** — grok, codex, and Antigravity (`agy`) ship headless
  CLIs we already drive. These back their vendor directly.
- **TUI adapter where the CLI is not the subscription door** — Anthropic's
  subscription is reachable through the Claude Code TUI. The existing claude TUI
  leg in `panel_invoker.py` (heartbeat-watched, version-floored at
  `_CLAUDE_CODE_MIN_VERSION = (2, 1, 197)`) is the **proof-of-concept** that a
  TUI can be driven as a non-interactive backing.
- **Official APIs later** — a backing is an implementation of the
  `AgentRuntimeProvider` seam (published as `@consiliency/runtime-provider`;
  Python port `phase_loop_runtime/agent_runtime_provider.py` with Homebrew and
  Omnigent providers, conformance golden `conformance.v0.1.json`). Swapping a
  vendor's backing from `TUI → official API` is a provider swap behind the seam;
  **no agent definition changes**.

**1.3 Distilled per-MODEL expert agents.** The endgame is not one generic pi
agent but a family of distilled experts, one per model. Each expert's toolset
reuses the tool **names and semantics that its underlying model was trained on**
— a claude-distilled agent uses claude-code's tool names/shapes; a
grok-distilled agent uses grok's. These profiles are derived **empirically from
session data** (the EXECDISPATCH SPIKE-DISSECT phase is the first evidence
probe), not hand-guessed.

**1.4 Narrowed per task, to cut context bloat.** A distilled expert does not
carry its model's entire trained tool surface into every task. The profile is
narrowed to the tools a given task class actually uses, so context spent on tool
definitions stays proportional to the work.

---

## 2. Cross-Cutting Principles (bind current + future roadmaps)

These principles are the connective tissue between the EXECDISPATCH roadmap and
every roadmap that follows it toward this north star. A roadmap that violates one
of these is drifting off the north star and should be flagged.

1. **Registry-not-hardcoded dispatch.** Executor selection resolves against a
   registry of entries, never against a hardcoded name list or a chain of
   `if executor == "..."` branches. This is the single structural change that
   un-corners pi-as-substrate: once dispatch reads a registry, adding pi
   (or any vendor) as the native core is a registry entry, not a dispatch-code
   edit. (EXECDISPATCH EXECREG delivers this for the execute path.)

2. **Tool naming is per-MODEL, never per-harness.** A distilled agent's tools
   are named and shaped after the **model** it wraps, using the vocabulary that
   model was trained on. Never borrow claude-code's tool names for a grok-backed
   agent (or vice versa) for convenience — a model performs best against the tool
   semantics in its own training distribution. Harness-shaped naming is a smell.

3. **Provider backings are swappable (TUI → API) without agent changes.** The
   agent definition binds to the `AgentRuntimeProvider` seam, not to a transport.
   Whether a vendor is reached by CLI, by a driven TUI, or by an official API is a
   provider-implementation detail. Changing it must not touch any agent or tool
   definition.

4. **Session-data capture is a first-class executor output.** An executor does
   not merely produce a closeout verdict; it produces (or makes available) the
   session transcript that records how the model actually worked — which tools,
   which argument shapes, at what frequency. This capture is the raw material for
   distillation (Principle 1.3) and must be treated as an intended output, not a
   debugging byproduct. **Made testable** by the EXECDISPATCH EXECREG phase's
   `get_session_transcript` hook on `ExecutorCapabilityRecord` (IF-0-EXECREG-1):
   an executor that cannot surface its transcript through that hook fails the
   principle. GROKEXEC ships the first non-claude implementation with a
   session-record preservation test.

---

## 3. Gated Backlog

The rule for this table is absolute: **nothing promotes to a roadmap until its
evidence gate is met.** A gate is a concrete, observable fact — not a judgment
call. Until the gate reads true, the item stays a vision line here.

| # | Backlog item | Evidence gate that promotes it to a roadmap |
|---|---|---|
| B1 | **pi tool-parity build** — build pi's per-model toolsets reusing each model's native tool names/semantics. | SPIKE-DISSECT (EXECDISPATCH DISSECT phase) produces an IF-0-DISSECT-1 v1 dataset that **validates against its committed schema for ≥2 harnesses** — claude-code plus ≥1 of codex/agy proven by an **actual extraction run** (not a format read-through), recorded in the feasibility verdict as an explicit yes + named harness. |
| B2 | **pi + grok distilled agent** — the first distilled per-model expert. | GROKEXEC live as a real execute-path executor (its zero-dispatch-edit + session-preservation tests green) **and** grok session data extracted into a schema-valid IF-0-DISSECT-1 profile by a real run (B1 evidence covering grok specifically). |
| B3 | **Anthropic TUI adapter for pi** — drive the Claude Code TUI as pi's Anthropic subscription backing. | pi toolset exists (B1 delivered) **and** a recorded driven-TUI stability metric from the existing `panel_invoker` claude leg clears an agreed threshold (e.g. unattended-run success rate over a stated sample) for execute-path use — a recorded number, not a subjective judgment. |
| B4 | **Fleet naming migration** — resolve the three-way `runtime` overload (`phase-loop-runtime` PyPI orchestrator vs `@consiliency/pipeline-runtime` GP lib vs `@consiliency/runtime-provider` seam). | The naming-convention decision (surfaced as an Assumption in the EXECDISPATCH roadmap) is **made and recorded** by the maintainer. No renames happen before the decision. |
| B5 | **Omnigent-transport publish** — externalize the runtime-provider transport so backings are shared fleet-wide. | The state-ledger externalization question is **decided** (whether provider state lives in-process or in an external ledger). |
| B6 | **Portal / message-board one-command deploy** — single-command stand-up of the portal + message-board control plane. | Container-hardening capacity exists to finish the drafted work (draft PRs `portal#201` / `message-board#14` already exist and are the starting point). |

Promotion mechanics: when a gate reads true, the item becomes a new roadmap
(`specs/phase-plans-v<N>.md`) or a phase appended to an existing one via
`claude-phase-roadmap-builder`. The gate's evidence artifact (dataset, live
executor, recorded decision) is cited in that roadmap's `## Assumptions` so the
promotion is auditable.
