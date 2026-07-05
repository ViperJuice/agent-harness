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
