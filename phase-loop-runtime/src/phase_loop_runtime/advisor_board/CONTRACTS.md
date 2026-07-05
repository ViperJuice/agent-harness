# Advisor Board — Frozen Contracts (Phase 1 ABDFREEZE)

Interface-freeze for the model-first, multi-harness Advisor Board
(`specs/phase-plans-v5.md`). Everything here is **additive and behavior-neutral**:
no running path changes, `panel_invoker` is untouched, and the `default` board
reproduces today's 3-leg panel byte-for-byte. Downstream phases (ABDREG,
ABDRESOLVE, ABDHOME) code against these interfaces so the fan-out integrates
without a big-bang.

Where a contract must reproduce today's behavior, the anchor line in
`phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` is cited and the
equivalence is proven by a test (not asserted in prose).

## IF-0-ABDFREEZE-1 — Seat + Board schema (model-first) · `schema.py`, `harness_mapping.py`

- **`Seat{model, effort, harness?, lens?, auth?, backing?, host_leg?}`** — a seat
  is a cognition; the harness is a defaulted-but-overridable execution *lane*, not
  the primary key. `effort` is a canonical level in `EFFORT_LEVELS =
  (low, medium, high, max)`. Frozen dataclass with fail-closed validation.
- **`Board{name, purpose, seats:[Seat], allow_api_key_fallback=false}`** —
  named, purpose-tagged, open-ended seat list; rejects api-key seats unless the
  board opts in.
- **Config format + location** — `$XDG_CONFIG_HOME/agent-harness/advisor-boards.toml`
  (`board_config_path()`); shape frozen by `fixtures/advisor-boards.example.toml`.
  The loader is ABDREG.
- **Per-harness model/effort mapping** — `render_seat_invocation(harness, model,
  effort) -> SeatInvocation`. Freezes how `seat.effort` reaches each CLI, incl.
  the **agy/gemini leg where effort is embedded in the model-name string**
  (`render_gemini_model`, panel_invoker.py:1016). Built-3 lanes are concrete;
  breadth lanes raise `EffortMappingError` until ABDREG/ABDHOME/ABDOMNI.
  Round-trip (proven): claude→`--effort max` (:324), codex→
  `-c model_reasoning_effort=xhigh` (:992), gemini→`"Gemini 3.1 Pro (High)"` (:1016).
- **Seat identity for result re-keying** — `Seat.seat_key` is a stable LABEL over
  every distinguishing field (lane, model, effort, lens), so lens-only-different
  seats get distinct keys. It is not a guaranteed-unique id — a board may hold two
  byte-identical seats — so ABDRESOLVE keys results by **seat position** and uses
  `seat_key` only as the label.
- **Host-leg identity** — `Seat.host_leg` marker + `identify_host_leg(board,
  HostContext)`. A seat is the native in-process host leg only when the board runs
  *inside* that harness (`HostContext.host_harness`). The standalone runner
  (`host_harness=None`) has no host leg — every leg is a subprocess, exactly as
  today.
- **Seat → vendor-family projection** — `vendor_family(model, harness)` /
  `seat_vendor_family(seat)`, model-first with a harness-lane fallback.
  Byte-consistent with `governed_review.author_vendor_for_model` (:60-75) and
  `_EXECUTOR_VENDOR` (:47-53). Two same-vendor seats on different harnesses
  (`gpt-5.5` on `codex` and on `opencode`) project to the same family, so the
  governed reviewer≠author disjointness survives model-first. ABDHOME rewires the
  governed gates onto *this* canonical function (not a copy).

## IF-0-ABDFREEZE-2 — Registry interfaces + shared fixtures · `registries.py`, `fixtures.py`

- **Interfaces (Protocols):** `HarnessRegistry`, `ModelRegistry`,
  `CompatibilityMatrix` with `is_valid(model, harness) -> (bool,
  AuthAvailability)` and `default_lane(model) -> str`.
- **Frozen return types:** `HarnessSpec`, `ModelSpec`, `AuthAvailability`
  (concrete, so no-silent-key is testable), `MatrixVerdict` alias.
- **Stubs:** `Stub{Harness,Model}Registry`, `StubCompatibilityMatrix` — raise
  `NotImplementedError`; no six-harness data (that is ABDREG).
- **Shared canonical fixtures** (the anti-divergence keystone ABDREG populates
  *from* and ABDRESOLVE/ABDHOME test *against*): `DEFAULT_BOARD`, `DEFAULT_SEATS`,
  `CANONICAL_LEG_ORDER`, `CANONICAL_VALID_PAIRS`, `CANONICAL_INVALID_PAIRS`,
  `TWO_SAME_VENDOR_BOARD`.

## IF-0-ABDFREEZE-3 — Provider-backing selector + auth enforcement · `backing.py`

- **Backing selector** — `select_backing(seat, gateway_available) ->
  BackingDecision`. Per-seat `homebrew | omnigent`; an `omnigent` seat with no
  gateway degrades **skip-with-warning** (fail-closed — never a silent homebrew
  breadth fallback).
- **Auth = active env scrubbing** — `resolve_seat_env(seat, base_env,
  allow_api_key_fallback)`, freezing the `_subscription_env` pattern
  (panel_invoker.py:226-230,348-353): a subscription seat scrubs **every** vendor
  API-key var; an api-key seat (only behind the board opt-in) scrubs everything
  then injects **only the seat vendor's** key(s). Never silent — an api-key seat
  without the opt-in raises.
- **`VENDOR_API_KEY_VARS`** — the flat `_API_KEY_VARS` tuple re-keyed by vendor
  family; its union equals today's tuple (proven), so scrubbing stays
  byte-equivalent.

## IF-0-ABDFREEZE-4 — Back-compat contract · `fixtures.py` + `tests/test_advisor_board_backcompat.py`

- The `default` board (`DEFAULT_BOARD`) resolves to today's three seats in
  `PANEL_LEGS` order, and each seat renders to today's exact model string + effort
  args (cross-checked against `panel_invoker.DEFAULT_LEG_MODELS` and the
  codex/claude/agy arg forms). Full assertions (launch order, payloads, env/auth,
  timeout/retry, result keys, output formatting, failure semantics, `invoke_panel`
  API) land in ABDVERIFY; this ships the **scaffold + the default-board fixture**.
- `advisor-panel` stays a working alias of `advisor-board` — the rename + alias is
  ABDRESOLVE; this contract only *states* the invariant.
- **Behavior-neutrality proof:** `git diff` on `panel_invoker.py` is empty and the
  full existing suite is green (this package is purely additive).

## IF-0-ABDFREEZE-5 — Observability contract · `events.py`

- **Internal envelope** — `AdvisorBoardEvent` (our shape, `EVENT_SCHEMA_VERSION =
  advisor_board.event.v1`, kinds in `EVENT_KINDS`). NOT a guessed Omnigent schema.
- **launcher ≠ observability-plane** — a natively-launched leg *emits* into a
  forwarded stream; it is never relaunched through the gateway for observability.
- **Forwarding is async/best-effort and can never delay or fail the native leg** —
  `best_effort_forward(sink, event)` swallows every sink error and never raises;
  `NullSink` keeps the default board a no-op. The mapping to a concrete sink
  (Omnigent v0.4.0 endpoint, or omniagent-plus ui-read-model/state-ledger) is
  deferred to ABDOBS — do not freeze against a guessed upstream schema.

## ABDOBS — Observability forwarding (Phase 6) · `observability.py`, `panel_invoker.invoke_board`

Builds the mapping ABDFREEZE-5 deferred. **Confirmed sink:** omniagent-plus's own
`state-ledger` / `ui-read-model` (we control it) — NOT an Omnigent HTTP ingestion
endpoint. v0.4.0's HTTP surface is launcher-centric and exposes **no** ingestion
endpoint for an externally-launched (native) session, so a native leg is
*observed*, never relaunched.

- **Envelope → sink mapping** — `map_event_to_runtime_event(event, session_id)`
  and `map_event_to_ledger_record(event, session_id)` project our
  `AdvisorBoardEvent` onto omniagent-plus's *own frozen* wire shapes:
  `runtime_event.v0.1` (`core-contracts/src/events.ts`) inside a
  `state_ledger_record.v0.1`, kind `runtime_event` (`core-contracts/src/state-ledger.ts`)
  — exactly what `AuditLedger.appendRuntimeEvent` (`state-ledger/src/audit-ledger.ts`)
  writes. **A board run projects to a session; each seat projects to a turn.** A
  per-run `sessionId` is minted (`new_session_id()` — never the board name);
  `turnId` derives from the seat's frozen `seat_key` label. `redaction` is
  `metadata_only` (never a raw key; `content_allowed` only for a text delta).
- **Cross-language transport seam** — the ledger is TypeScript, the emit is
  Python, so the boundary is `LedgerWriter` (a Protocol a real omniagent-plus
  binding implements over IPC/HTTP/a shared file) + `JsonlLedgerWriter`, a
  reference transport appending the exact `state_ledger_record.v0.1` records the TS
  `AppendOnlyStore` ingests. We do **not** reimplement ledger internals
  (retention / replay / compaction stay TS-side). This is the integration seam.
- **Async / best-effort (never delays or fails the native leg)** —
  `AsyncForwardingSink.emit` does a NON-BLOCKING put and returns; a background
  daemon thread does the real (slow / failing) write via `best_effort_forward`.
  The never-**raise** guarantee is frozen in `events.py`; the never-**delay**
  guarantee is here (unbounded queue; a bounded queue drops on full). `BoardObserver`
  wraps construct+map+enqueue in a swallow-all, so even a bad kind / full queue
  never touches the leg. `flush()` / `close()` drain deterministically (tests /
  graceful shutdown only — never on the leg's critical path).
- **launcher ≠ observability-plane, in code** — every sink here is structurally
  emit-only (no `create_session` / `send_turn`), so the observability path
  *cannot* launch a leg. Combined with the frozen `enforce_native_host_leg`
  (which hard-raises on a gatewayed host leg), the native host leg is observed,
  never relaunched.
- **Per-workload boundary (documented + enforced)** — `WORKLOAD_BOARD` =
  native launch **+ optional forward** (this path); `WORKLOAD_PHASE_EXECUTION` =
  Omnigent-as-launcher (CS-2.2, out of scope here). `invoke_board(sink=...)` is
  the ONLY opt-in; `sink=None` builds no envelope (default board byte-neutral).
  `invoke_panel` is untouched, so the live default panel is byte-neutral by
  construction.
- **Key-file note (reviewers):** the phase plan lists
  `agent_runtime_provider.py`; the emit lands in `panel_invoker.invoke_board`
  instead, because the envelope's vocabulary is `board.*` / `seat.*` (with
  `seat_key` / `vendor_family` / `harness`) which only the board seam knows — the
  provider only knows sessions/turns and cannot populate it. `observability.py`
  maps our envelope onto the provider-mirrored `runtime_event.v0.1` shape, so the
  provider layer is still the wire target, just not the emit site.
