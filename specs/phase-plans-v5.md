# agent-harness - Advisor Board (customizable, model-first, multi-harness review board) - Phase Plan v5

> How to use this document: run `phase-loop validate-roadmap specs/phase-plans-v5.md`, then plan each phase with the phase-loop command for the phase alias.

---

## Context

This roadmap evolves the fixed 3-vendor `advisor-panel` (v4: *Cross-Vendor Advisor Panel Ownership And Routing*) into the **Advisor Board** — a customizable, **model-first** review board with named purpose presets, a swappable provider backing, a six-harness compatibility matrix, and observability decoupled from launching.

Design source: `DESIGN-advisor-board.md`. The panel is already a single runtime-owned primitive (`phase_loop_runtime.panel_invoker`); the standalone `advisor-panel` skill and the embedded governed gates in `execute-phase`/`plan-phase` both call it, so this refactor has one implementation surface, not two. The runtime already supports overridable legs + per-leg model + per-leg effort, and the CS-0.8 provider seam (`agent_runtime_provider.py`) already mirrors omniagent-plus's `core-contracts/src/provider.ts` — so the provider-backing work activates an existing seam rather than inventing one.

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

1. **IF-0-ABDFREEZE-1** — Seat + Board schema (model-first): the `{model, effort, harness?, lens?, auth?}` seat shape, the board shape (name, purpose, open-ended seats, `allow_api_key_fallback`), and the config format + location (`~/.config/agent-harness/advisor-boards.toml`).
2. **IF-0-ABDFREEZE-2** — Registry interfaces: harness registry, model registry, and the `(model × harness)` compatibility + per-lane auth-availability matrix API (`is_valid(model, harness) -> (bool, auth_availability)`, `default_lane(model)`).
3. **IF-0-ABDFREEZE-3** — Provider-backing selector contract: per-seat `homebrew | omnigent`, fail-closed fallback semantics, and auth resolution (subscription-default, api-key by override/opt-in, never silent).
4. **IF-0-ABDFREEZE-4** — Back-compat contract: `default` board reproduces the current 3-leg panel behavior; `advisor-panel` remains a working alias of `advisor-board`.
5. **IF-0-ABDFREEZE-5** — Observability contract: forwarded-event shape for an observed-not-relaunched native leg, and the launcher-not-equal-observability-plane boundary.

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
- [ ] An automated proof shows the `default` board reproduces today's 3-leg behavior.
- [ ] `advisor-board --board code-review <artifact>` and bare `advisor-board` (default) both resolve.

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
Route board seats through the provider seam with the `homebrew` backing, preserve the built-3 and the native host leg, and provide homebrew one-shot legs as the fail-closed fallback for breadth harnesses.

**Exit criteria**
- [ ] Board seats run through the provider seam with per-seat `homebrew` backing selection.
- [ ] The built-3 (claude native host leg / Agent-View TUI off-host, codex, gemini) behave unchanged behind the seam.
- [ ] The native-host-leg-stays-native invariant is enforced and tested (the host leg is never routed through the gateway).
- [ ] An opencode/pi/cursor seat runs as a homebrew one-shot leg where a local subscription CLI exists.
- [ ] An unavailable lane degrades the seat gracefully (skip/fallback) without blocking the board.

**Scope notes**
Decompose into three lanes: seam-wiring lane, built-3-preservation lane (incl. the native-host-leg invariant), and homebrew-breadth+fallback lane. Buildable now with no gateway dependency.

**Non-goals**
No Omnigent backing, no observability forwarding.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/backing.py
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/claude_agent_view.py
- phase-loop-runtime/tests/test_advisor_board_backing_homebrew.py

**Depends on**
- ABDFREEZE

**Produces**
- (none)

### Phase 5 — Omnigent Backing (ABDOMNI)

**Objective**
Add the `omnigent` provider backing so breadth harnesses (opencode/pi/cursor/amp) route through omniagent-plus to Omnigent v0.4.0, opt-in and fail-closed, coordinating with the in-flight v0.4.0 adaptation rather than forking the transport. Gated on Omnigent v0.4.0.

**Exit criteria**
- [ ] The `omnigent` backing is implemented against the shared seam, targeting Omnigent v0.4.0 via omniagent-plus's provider.
- [ ] Opt-in opencode/pi/cursor/amp seats route through Omnigent as the primary lane; homebrew (ABDHOME) is the fallback.
- [ ] Gateway auth resolution maps subscription-default/api-key-opt-in/never-silent to the gateway-bearer lane.
- [ ] A gateway-down condition degrades an omnigent seat to homebrew or skip; native and built-3 seats are unaffected.

**Scope notes**
Decompose into three lanes: omnigent-provider-adapter lane, breadth-routing lane, and gateway-auth-resolution lane. Gated on the Omnigent v0.4.0 adaptation landing; coordinate, do not fork.

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
Forward a natively-launched leg's runtime events into Omnigent's observability plane (observed, not relaunched), and document/enforce the per-workload boundary. Gated on Omnigent v0.4.0.

**Exit criteria**
- [ ] A natively-launched leg emits its runtime events into the observability plane per the frozen forwarded-event shape (IF-0-ABDFREEZE-5).
- [ ] A natively-launched Claude host leg appears in the observability plane without being routed through the gateway.
- [ ] The per-workload boundary is documented and enforced: Board = native + optional forward; phase-execution = Omnigent-as-launcher.

**Scope notes**
Decompose into two lanes: event-forwarding lane and per-workload-boundary lane. Gated on the Omnigent v0.4.0 observability plane being available.

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
ABDFREEZE  (serial freeze)
   |
   +-----------------+-----------------+-----------------+---------------------+
   |                 |                 |                 |                     |
   v                 v                 v                 v                     v
ABDREG           ABDRESOLVE         ABDHOME           ABDOMNI*              ABDOBS*
(registries)     (resolve+rename)   (homebrew)        (gated v0.4.0)        (gated v0.4.0)
   |                 |                 |                 |                     |
   +-----------------+--------+--------+-----------------+---------------------+
                              |
                              v
                        ABDVERIFY  (serial integrate+verify)

* ABDOMNI, ABDOBS ride the in-flight Omnigent v0.4.0 adaptation; they join ABDVERIFY when ungated.
  ABDREG, ABDRESOLVE, ABDHOME are buildable now (pure agent-harness, back-compat) and run fully in parallel.
```

## Parallelism summary

- **1 front serial gate** (ABDFREEZE) unblocks a **5-way parallel fan-out**.
- **3 of the 5 fan-out phases (ABDREG, ABDRESOLVE, ABDHOME) are buildable now and independent** — run concurrently, each with 2-4 internal lanes (about ten lanes live at once).
- **2 fan-out phases (ABDOMNI, ABDOBS) are gated** on Omnigent v0.4.0 and run in parallel with each other when unblocked.
- **1 back serial gate** (ABDVERIFY) integrates.
- Critical path with v0.4.0 ready: ABDFREEZE → (max of the 5) → ABDVERIFY = **3 phases deep**. Buildable-now critical path: ABDFREEZE → (max of ABDREG/ABDRESOLVE/ABDHOME) → ABDVERIFY.
