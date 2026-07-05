# agent-harness - Advisor Board (customizable, model-first, multi-harness review board) - Phase Plan v5

> How to use this document: run `phase-loop validate-roadmap specs/phase-plans-v5.md`, then plan each phase with the phase-loop command for the phase alias.

---

## Context

This roadmap evolves the fixed 3-vendor `advisor-panel` (v4: *Cross-Vendor Advisor Panel Ownership And Routing*) into the **Advisor Board** — a customizable, **model-first** review board with named purpose presets, a swappable provider backing, a six-harness compatibility matrix, and observability decoupled from launching.

Design source: `DESIGN-advisor-board.md`. The panel is already a single runtime-owned primitive (`phase_loop_runtime.panel_invoker`); the standalone `advisor-panel` skill and the embedded governed gates in `execute-phase`/`plan-phase` both call it, so this refactor has one implementation surface, not two. The runtime already supports overridable legs + per-leg model (`invoke_panel(models={…})`, panel_invoker.py:49-53,1114-1121), and the CS-0.8 provider seam (`agent_runtime_provider.py`) already mirrors omniagent-plus's `core-contracts/src/provider.ts` and is LIVE (the default path routes every leg through `HomebrewAgentRuntimeProvider`) — so the provider-backing work activates an existing seam rather than inventing one.

**v0.4.0 landed (2026-07-05, verified on omniagent-plus origin/main `744420d`):** the Omnigent v0.4.0 adaptation is MERGED (PR #1 `omnigent-v0-4-adaptation`) — freeze target is `v0.4.0`, capability probe asserts `0.4.0`, `GET /v1/harnesses` (dynamic harness catalog) exists. So **ABDOMNI and ABDOBS are NO LONGER externally gated** — the earlier "gated on v0.4.0" annotation is removed. They still depend on ABDHOME (a real code dependency: the seam), so the DAG order is unchanged, but nothing waits on external work. One confirmed nuance: v0.4.0's HTTP surface is launcher-centric (no ingestion endpoint for externally-launched sessions), so ABDOBS observability of a NATIVE leg retargets at omniagent-plus's own `state-ledger`/`ui-read-model` (which we control) — this is now the confirmed sink, not a "verify an ingestion surface that may not exist."

**Panel-verified corrections (v5, three-agent panel incl. Fable code-verification):** (a) per-leg **effort is NOT supported today** — it is hard-coded per leg (claude `--effort max` :324, codex `xhigh` :992) and for the agy/gemini leg **effort is baked into the model-name string** (`"Gemini 3.1 Pro (High)"` :51/:1016), so the model-first `{model, effort}` split needs a per-harness model/effort mapping that is NEW work scheduled here. (b) The governed gates key reviewer≠author disjointness on **vendor-leg identity** (`governed_review.py:45-52,88-103`; `governed_premerge.py`); model-first breaks the `leg==vendor` assumption, so a seat→vendor-family projection is frozen and those call sites are updated, or custom boards silently corrupt reviewer-disjointness. (c) omniagent-plus integrates **opencode/pi/codex/claude-code/gemini-antigravity only** (`core-contracts/src/types.ts:10-17`) — **cursor and amp are NOT present**; breadth via Omnigent is scoped to opencode/pi, with a named contract-extension work item for cursor/amp.

The work is roadmap-sized (not a single detailed plan) because it spans schema, registries, a compatibility matrix, board resolution, a rename, two provider backings, and an observability plane — with a natural interface-freeze boundary up front that lets almost everything else fan out in parallel, and a gated tail that rides the in-flight omniagent-plus Omnigent v0.4.0 adaptation.

## Architecture North Star

A board **seat is a cognition** (`{model, effort, harness?, lens?, auth?}`), model-first: the harness is a defaulted-but-overridable execution *lane*, not the primary key. A **board** is a named, purpose-tagged, open-ended list of seats. Seats resolve through a **provider seam** whose backing is `homebrew | omnigent` per seat — homebrew keeps the built-3 (claude native/TUI, codex, gemini) and the native host leg; omnigent supplies harness breadth (opencode/pi/cursor/amp) by reusing omniagent-plus → Omnigent, not by hand-writing adapters. **Observability is a forwarded event stream, not a launcher mandate**: native legs stay natively launched and *emit* into Omnigent's observability plane rather than being relaunched through the gateway.

## Assumptions

- The user can subscription-auth to any provider a harness supports; **subscription is the default lane**.
- omniagent-plus is owned/controlled by us and not commercialized; **upstream Omnigent** is the maturity variable, currently moving v0.3.0 → official v0.4.0 (adaptation in flight in omniagent-plus).
- Omnigent already integrates opencode/pi/cursor/amp/claude-code/codex.
- The native host leg (Claude Code → native `Agent`) is the most reliable leg and must not regress onto the gateway.

## Non-Goals

- No changes to the XG-1 authority stack (separate program).
- No silent API-key use, ever — keys only on explicit override or opt-in fallback.
- Do not route the native host leg through Omnigent.
- Do not hand-write harness adapters Omnigent already owns.
- Full phase-execution agent-spawning through Omnigent is out of scope here (that is CS-2.2); this roadmap covers the Advisor Board and its provider seam only.

## Cross-Cutting Principles

- **Back-compat by construction**: `invoke_panel()` with no board == the `default` board == today's exact 3-leg behavior. Every existing caller unchanged.
- **Fail-closed availability**: an unavailable lane (missing CLI, no auth, gateway down) degrades the seat gracefully (skip/fallback) and never blocks the board.
- **Interface-freeze then fan out**: downstream phases code against frozen contracts (registry APIs, seat/board schema, backing selector), integrated at the verify gate — so registry population, resolver logic, rename, and the homebrew backing all proceed in parallel.

## Top Interface-Freeze Gates

1. **IF-0-ABDFREEZE-1** — Seat + Board schema (model-first): the `{model, effort, harness?, lens?, auth?}` seat shape, the board shape (name, purpose, open-ended seats, `allow_api_key_fallback`), the config format + location (`~/.config/agent-harness/advisor-boards.toml`), a **per-harness model/effort mapping** (incl. the agy leg where effort is embedded in the model name), the **host-leg seat identity** (which seat is the native host leg when the board runs inside Claude Code), and a **seat→vendor-family projection** (so the governed reviewer≠author disjointness survives model-first).
2. **IF-0-ABDFREEZE-2** — Registry interfaces + shared canonical fixtures: harness registry, model registry, the `(model × harness)` compatibility + per-lane auth-availability matrix API (`is_valid(model, harness) -> (bool, auth_availability)`, `default_lane(model)`), and a single canonical fixture set that ABDREG populates and ABDRESOLVE/ABDHOME test against (kills mock-vs-real divergence).
3. **IF-0-ABDFREEZE-3** — Provider-backing selector + auth-enforcement contract: per-seat `homebrew | omnigent`, fail-closed fallback semantics, and auth resolution enforced by **active environment scrubbing** (subscription-default scrubs vendor API-key vars from the subprocess env / gateway payload per the existing `_subscription_env` strip pattern; a fallback injects ONLY the seat's vendor key; never silent).
4. **IF-0-ABDFREEZE-4** — Back-compat contract: `default` board reproduces the current 3-leg panel behavior under **golden tests** (launch order, prompt/input payloads, env/auth, timeout/retry, result keys, output formatting, failure semantics, `invoke_panel()` API), not just seat count; `advisor-panel` remains a working alias of `advisor-board`.
5. **IF-0-ABDFREEZE-5** — Observability contract: an **internal advisor-board event envelope** (owned by us, not a guessed Omnigent schema), the launcher-not-equal-observability-plane boundary, and the rule that forwarding is **async/best-effort and can never delay or fail the native leg**. The mapping from the internal envelope to a concrete sink is deferred to ABDOBS.

## Phases

### Phase 1 — Contract Freeze (ABDFREEZE)

**Objective**
Freeze every contract downstream phases depend on — seat/board schema, registry APIs, backing selector, back-compat, observability shape — before any behavior change, so the rest fans out in parallel against stable interfaces.

**Exit criteria**
- [ ] Seat schema `{model, effort, harness?, lens?, auth?}` and board schema (name, purpose, open-ended seats, `allow_api_key_fallback`) are documented as typed structures.
- [ ] Board config format and location (`~/.config/agent-harness/advisor-boards.toml`) are specified with a fixture.
- [ ] Registry interfaces are frozen: harness registry, model registry, and the `(model × harness)` compatibility + auth-availability matrix API.
- [ ] The provider-backing selector contract (`homebrew | omnigent`, fail-closed fallback, subscription-default/api-key-opt-in/never-silent auth resolution) is documented.
- [ ] The back-compat contract states the `default` board reproduces today's 3-leg behavior and `advisor-panel` stays a working alias.
- [ ] The observability contract defines the forwarded-event shape and the launcher-not-equal-observability-plane boundary.
- [ ] A `default` board fixture resolves to today's three seats with no runtime behavior change.

**Scope notes**
Interface-only preamble; decompose into two lanes: a schema lane (seat/board/config/alias — IF-0-ABDFREEZE-1, -4) and an interfaces lane (registry/matrix/backing/observability contracts — IF-0-ABDFREEZE-2, -3, -5). Serial front gate; the only serial phase before the fan-out.

**Non-goals**
No registry population, no resolver logic, no rename, no provider backing, no behavior change.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/agent_runtime_provider.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/schema.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/registries.py
- phase-loop-runtime/tests/test_advisor_board_schema.py

**Depends on**
- (none)

**Produces**
- IF-0-ABDFREEZE-1
- IF-0-ABDFREEZE-2
- IF-0-ABDFREEZE-3
- IF-0-ABDFREEZE-4
- IF-0-ABDFREEZE-5

### Phase 2 — Registries And Matrix (ABDREG)

**Objective**
Populate the real data behind the frozen registry interfaces: the six harnesses, the model registry, the compatibility+auth matrix, and the board presets + config loader.

**Exit criteria**
- [ ] Harness registry lists claude, codex, gemini, opencode, pi, cursor with cli backing, auth lanes, and availability probes (cursor gated on `cursor-agent`).
- [ ] Model registry maps each model to its default subscription lane, `runnable_by`, and effort ceiling.
- [ ] The `(model × harness)` compatibility + auth-availability matrix is populated; an invalid pairing such as `claude:gpt-5.5` is rejected at config time with a clear message.
- [ ] Board presets `default`, `code-review`, `brainstorm`, `doc-edit` load from config; `allow_api_key_fallback` defaults to false.
- [ ] Unknown config keys produce a clear error rather than a silent drop.

**Scope notes**
Decompose into four lanes: harness-registry lane, model-registry lane, matrix+validation lane, and presets+config-loader lane. Disjoint (each owns its own module) and integrate at the verify gate.

**Non-goals**
No board resolution logic, no rename, no provider backing.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/registries.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/matrix.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/presets.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/config.py
- phase-loop-runtime/tests/test_advisor_board_registries.py

**Depends on**
- ABDFREEZE

**Produces**
- (none)

### Phase 3 — Board Resolution And Rename (ABDRESOLVE)

**Objective**
Implement board resolution (name to seats, settable default, ad-hoc seats), fail-fast seat validation, and the `advisor-panel` to `advisor-board` rename with a back-compat alias — coded against the frozen interfaces with fixture registries.

**Exit criteria**
- [ ] Board resolver turns a board name into seats, honors a settable default board, and parses ad-hoc `--seats model:effort[:harness]`.
- [ ] Seat validation runs against the matrix and fails fast with actionable diagnostics on an invalid seat.
- [ ] The skill is renamed `advisor-board` across all harness prefixes; `advisor-panel` resolves as an alias; the stray codex `codex-advisor-panel` duplicate is removed.
- [ ] An automated proof shows the `default` board reproduces today's 3-leg behavior (resolution-level; the full golden proof lives in ABDFREEZE-4 / ABDVERIFY).
- [ ] `advisor-board --board code-review <artifact>` and bare `advisor-board` (default) both resolve.
- [ ] Result identity is re-keyed leg→seat so a board with two same-vendor seats is expressible (`PanelLegResult.leg` today assumes one model per vendor); `PanelRequest` (documented as an entry point in the skill but not accepted by `invoke_panel`) is reconciled or retired.

**Scope notes**
Decompose into four lanes: resolver lane, validation-wiring lane, rename+alias+dedup lane, and back-compat-proof lane. The resolver/validation lanes code against frozen interfaces with fixture registries and integrate with ABDREG at verify.

**Non-goals**
No provider backing change, no observability work.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/resolver.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/validation.py
- phase-loop-skills/advisor-panel/SKILL.md
- phase-loop-runtime/src/phase_loop_runtime/skills_bundle/
- phase-loop-runtime/tests/test_advisor_board_resolver.py

**Depends on**
- ABDFREEZE

**Produces**
- (none)

### Phase 4 — Homebrew Backing (ABDHOME)

**Objective**
Route board seats through the provider seam with the `homebrew` backing; preserve the built-3 and the native host leg (byte-for-byte for the default board); plumb per-seat effort into every spawned CLI; enforce no-silent-key by active env scrubbing; and preserve the governed reviewer≠author disjointness under model-first. Breadth harnesses are NOT hand-written here — they are Omnigent-or-skip (ABDOMNI).

**Exit criteria**
- [ ] Board seats run through the provider seam with per-seat `homebrew` backing selection.
- [ ] The built-3 (claude native host leg / Agent-View TUI off-host, codex, gemini) behave unchanged behind the seam, proven by the IF-0-ABDFREEZE-4 golden tests (not just seat count).
- [ ] The native-host-leg-stays-native invariant is enforced and tested (the host leg is never routed through the gateway).
- [ ] `seat.effort` reaches each spawned CLI via the frozen per-harness model/effort mapping (incl. the agy leg where effort is embedded in the model name).
- [ ] No-silent-key is enforced by ACTIVE env scrubbing: a subscription/default seat scrubs vendor API-key vars from the subprocess env (per `_subscription_env`, panel_invoker.py:227-230,348-353); a fallback injects ONLY the seat's vendor key; negative tests cover every launcher/fallback path.
- [ ] The governed reviewer≠author disjointness holds for custom boards: `governed_review.py`/`governed_premerge.py` consume the frozen seat→vendor-family projection; a two-same-vendor-seat board is tested for correct disjointness.
- [ ] An unavailable lane degrades the seat gracefully (skip-with-warning) without blocking the board.

**Scope notes**
Decompose into three lanes: seam-wiring + effort-plumbing + env-scrubbing lane, built-3-preservation + native-host-leg-invariant lane, and governed-gate-disjointness lane. Buildable now with no gateway dependency. NOTE: breadth harnesses (opencode/pi/cursor) are deliberately NOT given homebrew adapters here — hand-writing them defeats the Omnigent maintenance-offload; unavailable breadth = skip-with-warning (fail-closed), routed by ABDOMNI.

**Non-goals**
No Omnigent backing, no observability forwarding, no hand-written breadth adapters.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/backing.py
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/claude_agent_view.py
- phase-loop-runtime/src/phase_loop_runtime/governed_review.py
- phase-loop-runtime/src/phase_loop_runtime/governed_premerge.py
- phase-loop-runtime/tests/test_advisor_board_backing_homebrew.py

**Depends on**
- ABDFREEZE

**Produces**
- (none)

### Phase 5 — Omnigent Backing (ABDOMNI)

**Objective**
Add the `omnigent` provider backing so breadth harnesses route through omniagent-plus to Omnigent v0.4.0, opt-in and fail-closed, coordinating with the in-flight v0.4.0 adaptation rather than forking the transport. Scoped to the harnesses omniagent-plus integrates today (**opencode/pi**). v0.4.0 has LANDED (freeze target v0.4.0, `GET /v1/harnesses` present), so this is buildable now (depends on ABDHOME, not on external work). cursor/amp availability is checked dynamically via the live `GET /v1/harnesses` catalog; routing them needs only the upstream Omnigent catalog to report them, not a code gate.

**Exit criteria**
- [ ] The `omnigent` backing is implemented against the shared seam, targeting Omnigent v0.4.0 via omniagent-plus's provider.
- [ ] Opt-in **opencode/pi** seats route through Omnigent as the primary lane; unavailable = skip-with-warning (no hand-written homebrew breadth fallback).
- [ ] cursor/amp routing is gated on the live `GET /v1/harnesses` catalog reporting them (the v0.4.0 dynamic catalog exists); registered as harnesses regardless, routed through Omnigent only when the catalog exposes them.
- [ ] Gateway auth resolution maps subscription-default/api-key-opt-in/never-silent to the gateway-bearer lane, and the gateway reports which auth lane a session actually used (so no-silent-key is testable for omnigent seats, not just asserted).
- [ ] A gateway-down condition degrades an omnigent seat to skip-with-warning; native and built-3 seats are unaffected.

**Scope notes**
Decompose into three lanes: omnigent-provider-adapter lane, breadth-routing (opencode/pi) lane, and gateway-auth-resolution lane. v0.4.0 has landed; coordinate with omniagent-plus main, do not fork. cursor/amp route only when the live harness catalog exposes them.

**Non-goals**
No native-host-leg routing through the gateway; no observability work (that is ABDOBS).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/backing_omnigent.py
- phase-loop-runtime/src/phase_loop_runtime/agent_runtime_provider.py
- phase-loop-runtime/tests/test_advisor_board_backing_omnigent.py

**Depends on**
- ABDFREEZE
- ABDHOME

**Produces**
- (none)

### Phase 6 — Observability Forwarding (ABDOBS)

**Objective**
Emit a natively-launched leg's runtime events as the frozen internal advisor-board envelope, then MAP that envelope to omniagent-plus's own `state-ledger`/`ui-read-model` sink (which we control; v0.4.0's HTTP surface has no external-session ingestion endpoint, so this is the confirmed path — observed, not relaunched); document/enforce the per-workload boundary. Buildable now.

**Exit criteria**
- [ ] A natively-launched leg emits its runtime events as the internal advisor-board event envelope (IF-0-ABDFREEZE-5), async/best-effort, never delaying or failing the native leg.
- [ ] The envelope maps to omniagent-plus's own `state-ledger`/`ui-read-model` (append-only-store/audit-ledger/evidence-store) — confirmed sink, since v0.4.0's launcher-centric HTTP surface exposes no ingestion endpoint for externally-launched sessions.
- [ ] A natively-launched Claude host leg appears in the chosen plane without being routed through the gateway.
- [ ] The per-workload boundary is documented and enforced: Board = native + optional forward; phase-execution = Omnigent-as-launcher.

**Scope notes**
Decompose into two lanes: envelope-emit (async/best-effort) lane and envelope→verified-sink mapping lane. Sink is omniagent-plus's own state-ledger/ui-read-model (we control it); no external gate. The internal envelope is frozen in ABDFREEZE-5; do NOT depend on a nonexistent upstream ingestion endpoint.

**Non-goals**
No relaunching of the native host leg for observability; no phase-execution agent-spawning (CS-2.2).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/observability.py
- phase-loop-runtime/src/phase_loop_runtime/agent_runtime_provider.py
- phase-loop-runtime/tests/test_advisor_board_observability.py

**Depends on**
- ABDFREEZE
- ABDHOME

**Produces**
- (none)

### Phase 7 — Integrate, Verify, And Docs (ABDVERIFY)

**Objective**
Integrate the fan-out against real registries and the homebrew backing end-to-end, run the release verification matrix, and publish the docs (capabilities card + migration note). Omnigent/observability legs join when ungated.

**Exit criteria**
- [ ] End-to-end integration runs real registries × resolver × homebrew backing; presets smoke green.
- [ ] The back-compat regression proves the `default` board reproduces today's 3-leg behavior.
- [ ] The fail-closed matrix (missing CLI / no auth / gateway down) degrades gracefully in every case.
- [ ] A per-harness capabilities card is published (prefixed skill names + the `advisor-board`-is-unprefixed exception).
- [ ] A migration note documents the `advisor-panel` alias and the preset boards.

**Scope notes**
Serial integrate gate joining the fan-out; decompose into two lanes: integration+verification-matrix lane and docs+migration lane. ABDOMNI/ABDOBS legs are added to the matrix when ungated.

**Non-goals**
No new feature scope; verification and documentation only.

**Key files**
- phase-loop-runtime/tests/test_advisor_board_integration.py
- phase-loop-skills/advisor-panel/SKILL.md
- docs/advisor-board-capabilities-card.md
- specs/phase-plans-v5.md

**Depends on**
- ABDREG
- ABDRESOLVE
- ABDHOME

**Produces**
- (none)

## Execution Notes

- Run the buildable-now core first: ABDFREEZE → then ABDREG, ABDRESOLVE, ABDHOME concurrently (pure agent-harness, back-compat, no gateway dependency).
- ABDOMNI and ABDOBS are gated on the omniagent-plus Omnigent v0.4.0 adaptation; start them only once v0.4.0 has landed there, and coordinate with that work — do not fork the transport.
- Plan each phase with the phase-loop `plan-phase` command for its alias; execute with `execute-phase`. Use isolated worktrees for parallel lanes.
- The `default` board must stay byte-for-byte equivalent to today's 3-leg panel throughout; treat any drift as a release blocker.
- Panel this roadmap (and the ABDFREEZE contracts) through the current advisor panel before executing, per the design's load-bearing decisions (model-first inversion, Omnigent-for-breadth, observability-decoupling, auth model).

## Verification

- **Back-compat keystone**: an automated proof (ABDRESOLVE lane 3d, re-run in ABDVERIFY) that the `default` board reproduces the current 3-leg panel behavior. This gate protects every existing caller (the governed gates in execute/plan and standalone use).
- **Validation matrix**: invalid `(model × harness)` pairings rejected at config time; unknown config keys error clearly; `allow_api_key_fallback` defaults false; no API key used without an explicit signal.
- **Fail-closed matrix**: missing CLI, no auth, and gateway-down each degrade the affected seat (skip/fallback) and never block the board; the native host leg is proven never routed through the gateway.
- **Release smoke**: `advisor-board`, `advisor-board --board <preset>`, and the `advisor-panel` alias all resolve; presets load; the capabilities card matches the installed per-harness skill names.
- **Runtime gate**: `phase-loop validate-roadmap specs/phase-plans-v5.md` passes; per-phase exit criteria checked at closeout.

## Phase Dependency DAG

```
ABDFREEZE  (serial freeze — all contracts + shared canonical fixtures)
   |
   +-----------------+-----------------+        <-- buildable-now 3-way fan-out
   |                 |                 |
   v                 v                 v
ABDREG           ABDRESOLVE         ABDHOME
(registries)     (resolve+rename)   (homebrew)
   |                 |                 |
   |                 |                 +-----------+----------------+   <-- gated 2-way second wave
   |                 |                 |           |                |
   |                 |                 v           v                |
   |                 |             ABDOMNI*     ABDOBS*             |
   |                 |             (gated)      (gated)             |
   +-----------------+--------+------+-----------+----------------+-+
                              |
                              v
                        ABDVERIFY  (serial integrate+verify)

* ABDOMNI, ABDOBS DEPEND ON ABDHOME (the seam) — a code dependency, NOT an external gate.
  Omnigent v0.4.0 has landed; ABDOBS's sink is our own state-ledger. Nothing waits on external work.
  ABDREG/ABDRESOLVE/ABDHOME are buildable now and run in parallel; ABDFREEZE ships the
  shared canonical fixtures so ABDRESOLVE/ABDHOME don't diverge from ABDREG (no big-bang).
```

## Parallelism summary

- **1 front serial gate** (ABDFREEZE) unblocks a **3-way buildable-now fan-out** (ABDREG, ABDRESOLVE, ABDHOME) — independent file footprints, each with 2-3 internal lanes (~8 lanes live at once), all coding against ABDFREEZE's shared canonical fixtures.
- **A 2-way second wave** (ABDOMNI, ABDOBS) that **depends on ABDHOME** (the seam, a code dependency — NOT an external gate; v0.4.0 has landed); the two run in parallel with each other once ABDHOME is wired.
- **1 back serial gate** (ABDVERIFY) integrates.
- **Buildable-now critical path**: ABDFREEZE → max(ABDREG, ABDRESOLVE, ABDHOME) → ABDVERIFY = **3 phases deep**.
- **Full critical path** (all buildable now, v0.4.0 landed): ABDFREEZE → ABDHOME → max(ABDOMNI, ABDOBS) → ABDVERIFY = **4 phases deep** (the second wave is a code dependency on ABDHOME, not a flat fan-out).
