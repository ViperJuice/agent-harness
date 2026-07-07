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
  `metadata_only` (never a raw key; `content_allowed` only for a text delta). A
  `seat.failed` payload conforms to the full `runtime_failure.v0.1` (`errors.ts`)
  — all of schema/category/retryable/actor/scope/message are required upstream.
  NB: the record-level `recordId` / `sequence` are meaningful only for the
  reference `JsonlLedgerWriter`; the real TS `AppendOnlyStore` ASSIGNS them on
  append (its `AppendRecordInput` has no `sequence`), so a real binding overrides
  them. The authoritative, per-session sequence is the `runtime_event` payload's.
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

## ABDPRESET — Board preset library + Fable review-path · `presets.py`, `panel_invoker.py`

The seven built-in presets (`presets.PRESETS`). Every preset self-validates against
the real matrix at `load_boards()` time (`tests/test_advisor_board_config.py`,
`tests/test_advisor_board_integration.py`).

- **Review-class = Fable, decoupled from the implementer.** Pre-merge and legal
  review are mid-tier decisions where being wrong is expensive, so the review-class
  boards (`default`, `code-review`, `legal-review`, `legal-strategy-review`) seat
  Fable (`claude-fable-5`) on the claude lane — NOT the implementer model
  `profiles.CLAUDE_IMPLEMENTER_MODEL` (`claude-sonnet-5`). `panel_invoker.DEFAULT_LEG_MODELS["claude"]`
  is the SINGLE source of truth for the panel's default claude model: the claude
  leg builder (`_claude_tui_command`) and the Agent-View attempt both read it, so
  the *legacy* `invoke_panel` path AND the live governed gates
  (`governed_review` / `governed_premerge`, which call `invoke_panel` with no model
  override) review on Fable. `CLAUDE_IMPLEMENTER_MODEL` is untouched — the
  implementer stays Sonnet. The `default` board (`fixtures.DEFAULT_BOARD`) is
  byte-pinned to this Fable `invoke_panel` panel by the golden proof
  (`tests/test_advisor_board_golden.py`); the sole sanctioned delta stays `seat_key`.
- **`code-review` = three frontier vendors, always.** codex `gpt-5.5`,
  `Gemini 3.1 Pro`, and `claude-fable-5`, each on the `adversarial` lens (supersedes
  the prior two-seat codex+sonnet composition).
- **Divergent-thinking boards keep Sonnet.** `brainstorm` / `doc-edit` /
  `legal-brainstorm` deliberately retain `claude-sonnet-5` — a diverse voice, a
  low-stakes copyedit, a cheap aggressive ideation seat — where it is the right tool.
- **Legal boards (`legal-review`, `legal-strategy-review`, `legal-brainstorm`)**
  encode the PRIMARY review lens per seat. `lens` / `purpose` are free-form strings
  (`schema.py`), so the legal lenses/purposes need no enum extension.
- **Catch-alls for unmodeled tasks (`general`, `solo`).** So the board library is not
  limited to the pre-modeled domains: `general` is the domain-agnostic top-tier PANEL
  (three frontier vendors — gpt-5.5/adversarial, Gemini 3.1 Pro/alternative,
  claude-fable-5/completeness — hand it any task + brief), and `solo` is the
  single-MEMBER form (one `claude-fable-5` seat) for a quick top-end opinion when a
  panel is overkill. A ONE-seat board validates + resolves through `invoke_board` like
  any other (bare/single seats are supported). Both default to TOP-END models: an
  unanticipated task cannot be assumed low-stakes, so the safe default is frontier —
  dial down explicitly (a cheaper board) when a task is known-cheap. Their lenses
  (`adversarial`/`alternative`/`completeness`) and purpose (`general`) are free-form
  strings, so no enum extension.
- **Deep-seat FOLLOW-ON (documented, NOT built here).** The richer legal treatment —
  four lenses per seat, an apex-Opus (`claude-opus-4-8`) seat, a verify-round, and
  retrieval-grounded citation-verification — is a deliberate follow-on. The current
  legal boards ship the single-primary-lens-per-seat form; the deep-seat form layers
  onto the same seat/board schema (no schema change) when built.

## ABDPAR — Concurrent leg execution · `panel_invoker.invoke_board` / `invoke_panel`

`invoke_board` and `invoke_panel` fan their seats/legs out across a bounded
`ThreadPoolExecutor` (`_run_legs_ordered`), not a sequential loop — legs are blocking
subprocess I/O (the CLI wait releases the GIL), so wall-clock is `~max(leg)`, not
`sum(leg)`.

- **Parallel is the DEFAULT (opt-out, not opt-in).** Both entry points take a single
  `max_concurrency: int | None = None` knob, threaded through the governed gates
  (`governed_planning_gate` → `run_governed_premerge_loop` → `governed_premerge_for_run`)
  so a caller CAN request sequential, while the default everywhere stays parallel:
  - `None` (default) → parallel, bounded by `min(len(seats), 8)`.
  - `1` → sequential (the opt-in escape hatch — debugging, a rate-limited / throttled
    provider, a constrained host).
  - `N` → cap concurrency at `N`.
  It is the SAME thread-pool path: `max_workers = max(1, min(max_concurrency or len(seats), 8))`,
  so `max_concurrency=1` degrades to one worker (strictly serial) with no separate
  sequential branch. `max_concurrency` is a keyword-only, default-valued additive
  extension to the frozen `invoke_panel` signature (back-compat, like `models` #66).

Frozen invariants (concurrency is a **timing-only** change — never a leg's outcome;
the golden proves byte-identity):

- **Positional order** — futures are submitted in seat/leg order and read back by
  index, so `result[i]` corresponds to `seats[i]`/`legs[i]` regardless of finish
  order. `resolver.key_results_by_seat` re-keys by position and the golden asserts
  order + content, so this is load-bearing.
- **Fail-closed per seat** — the extracted per-seat/per-leg body returns a DEGRADED
  `PanelLegResult` on any exception, so a future's `.result()` never raises and one
  broken leg can never crash the pool or the board.
- **Single shared reads before the pool** — the one `GET /v1/harnesses` gateway-catalog
  fetch and the seat-validation/host-leg checks stay ABOVE the pool (one fetch, shared
  read); the observability emit stays AFTER, in seat order. Bounded `max_workers =
  min(len(seats), 8)`.
- **Proof** — a `threading.Barrier(N)` test proves both directions without real sleeps:
  the DEFAULT satisfies the barrier (all N legs in-flight at once → all OK), and
  `max_concurrency=1` makes the same barrier unsatisfiable (one worker → it times out →
  fail-closed DEGRADED), so the pair discriminates parallel from sequential. A third
  test confirms sequential mode returns byte-identical ordered results.
- **Thread-safety of the real leg paths** — the leg execs install NO signal handlers or
  timers (`signal.signal` / `alarm` / `setitimer` would raise `ValueError: signal only
  works in main thread` off the main thread); the only `signal.` use is `os.killpg(pid,
  SIGTERM/SIGKILL)` (a constant, thread-safe), and timeouts run on `select.select(...)`
  / `subprocess.run(timeout=...)`, not `SIGALRM`. Each leg stages its own temp review
  dir, so concurrent legs never share filesystem state. Verified: the real `spawn=None`
  path runs inside worker threads with no signal-in-thread error.
- **Known edge (untested):** a custom board with two seats on the SAME lane (e.g. two
  `claude` seats) now runs two concurrent same-lane CLI/PTY sessions — a scenario this
  fan-out newly enables and nothing yet tests. The `default` board has one claude seat,
  so it is unaffected.

## ABDREF — "Reference, don't inline" ingestion · `panel_invoker._resolve_artifact` / `_resolve_brief`

The leg prompt was already lean (it stages `review-bundle.md` and instructs the leg
to READ the file); the remaining inline path was the caller→runtime boundary, where
building `artifact: str` forces the caller to hold the whole (20k+ token) bundle in
its own context. `artifact_ref` / `brief_ref` promote that to by-reference ingestion:
the caller passes a PATH and the runtime reads it.

- **Entry points.** `invoke_panel` and `invoke_board` accept keyword-only
  `artifact_ref: str | Sequence[str] | None` and `brief_ref: str | None`
  (default-valued additive extensions to the frozen signatures — back-compat, like
  `models` / `max_concurrency`). `invoke_panel_request` consumes
  `PanelRequest.artifact_ref` but deliberately does not define `PanelRequest.brief_ref`
  in this contract; callers that need a custom brief use the direct `invoke_panel` or
  `invoke_board` entry point. The artifact is resolved at the TOP of the entry point
  so timeout scaling, staging, and metadata all see the resolved content.
- **`_resolve_artifact(artifact, artifact_ref)`.** `artifact_ref is None` →
  `artifact or ""` (today's bytes, verbatim). A single path (a bare string OR a
  one-element sequence) returns the file content VERBATIM — no header — so
  `artifact_ref=P` is byte-identical to `artifact=<contents of P>`. Multiple paths
  concatenate deterministically in the given order, each under a `## {filename}`
  header, joined by a blank line. `artifact_ref` WINS when both it and `artifact` are
  supplied. A `str` is checked BEFORE the `Sequence` branch (a string is itself an
  iterable of characters).
- **`_resolve_brief(mode, brief_ref)`.** `brief_ref` file when set, else
  `_mode_instructions(mode)` — staged as `review-instructions.md`. Threaded through
  `_default_spawn` / `_default_spawn_via_provider` (omitted-when-`None`, so the
  default path's `_default_spawn` call stays byte-identical).
- **Fail-closed.** A missing `artifact_ref` / `brief_ref` path raises `ValueError`
  NAMING the path — never a silent-empty bundle that would read as a real (empty)
  review.
- **Inline-size guard (`_maybe_warn_inline_size`, `_MAX_INLINE_ARTIFACT_BYTES = 16 KB`).**
  An INLINE (not-from-ref) artifact over the threshold logs ONE steering warning
  pointing to `artifact_ref` — **WARN, never refuse, never mutate** (refusing would
  break existing callers). A from-ref artifact is never warned.
- **Crash-residual scratch GC (`_gc_stale_panel_scratch`).** Best-effort, age-gated
  (`max_age_s = 24h`) sweep of `pl-panel-*` dirs, called at the top of
  `_default_spawn`. Reclaims dirs leaked when a run is KILLED before the per-run
  `finally: rmtree` (timeout/crash); a concurrent run's fresh dir is never touched,
  and a GC failure can NEVER affect the run (fully swallowed).
- **Golden byte-identity preserved.** No ref ⇒ identical staged bytes ⇒ identical
  per-leg argv / env / timeout. `tests/test_advisor_board_golden.py` (Proof A hits
  `_exec_leg`; Proof B injects `spawn=`) is untouched;
  `tests/test_advisor_board_ingestion.py` proves the new path is byte-transparent.

## CTXFREEZE — Context references and panel reliability (#114)

CTXFREEZE freezes the #114 public contract for downstream implementation and
documentation phases. It does not claim that `artifact_ref` or `brief_ref` are true
non-inlining modes: both are read-file-and-inline conveniences that keep bytes out
of the caller context but still stage raw contents for each leg.

- **Ingestion modes and precedence.** `artifact` is inline text. `artifact_ref` is a
  file path, or ordered path sequence, whose contents replace `artifact` when both
  are supplied. `brief_ref` is a file path used only by `invoke_panel` and
  `invoke_board`; it replaces the generated review/advisory instruction file.
  `context_refs` is the only true by-reference mode. `invoke_panel_request` threads
  `PanelRequest.artifact_ref`, `PanelRequest.context_refs`,
  `PanelRequest.context_refs_soft_warn`, and `PanelRequest.timeout_seconds_by_leg`;
  `PanelRequest.brief_ref` is explicitly out of contract for this phase.
- **True by-reference manifest.** `context_refs` appends a deterministic manifest to
  the already-resolved artifact. Each entry is emitted in caller order and contains a
  JSON-escaped absolute path, byte count, streamed SHA-256 digest, untrusted MIME
  guess, untrusted extension hint, and optional best-effort PDF page count. Raw file
  contents are not written into `review-bundle.md`, prompt text, or the manifest.
- **Filesystem boundary.** Paths are interpreted relative to the current process
  working directory when relative and are reported as `Path.resolve()` absolute
  paths. Entries must be regular files at validation time; missing paths,
  directories, devices, and other non-regular files fail closed by default. Symlinks
  are followed only if their resolved target is a regular file under normal OS path
  resolution. The runtime opens the file once to stream size/hash metadata and, for
  PDF-looking files only, may reopen a bounded prefix for page counting; CTXIMPL owns
  any stronger root jail or TOCTOU hardening.
- **Soft-warning opt-in.** Missing or unreadable `context_refs` raise `ValueError`
  unless `context_refs_soft_warn=True`. Soft warning logs a warning and emits a
  `MISSING` or `UNREADABLE` entry. The manifest instruction text remains strict:
  a leg must not infer, guess, or fabricate unavailable contents.
- **Output-boundary claim.** The frozen privacy claim is runtime non-inlining only:
  referenced contents are absent from the staged bundle and prompt produced by this
  runtime path. This is not a global DLP guarantee for provider logs, human-visible
  outputs, handoffs, screenshots, shell commands, or tools the leg chooses to run
  after reading a referenced file.
- **Reliability names.** Direct entry points accept `timeouts_by_leg`; `PanelRequest`
  uses `timeout_seconds_by_leg`. Both feed the same per-leg override. Unset legs keep
  input-scaled defaults. The codex and gemini CLI legs retry one fast soft-empty or
  transient-stall result once, guarded by elapsed-time fraction; hard subprocess
  timeouts return timeout status without retry. CTXRELY owns any follow-on reliability
  split beyond these frozen names and retry/timeout invariants.

## ABDMODE — Purpose-derived default mode + advisory prompt hygiene · `panel_invoker.py` (#107)

A board's PURPOSE now selects its default panel MODE automatically, so a domain
board (esp. the legal boards) runs in the right posture instead of being hard
code-review-gated. `tests/test_advisor_board_advisory_mode.py`.

- **`_mode_for_purpose(purpose) -> str`.** Code-review-class purposes
  (`code-review`, `premerge-review`) → `"review"` (the strict pre-merge gate:
  bundle is untrusted material to accept/reject, a conforming AGREE / PARTIALLY
  AGREE / DISAGREE verdict is REQUIRED). The known domain purposes (`legal-review`,
  `legal-strategy-review`, `legal-brainstorm`, `brainstorm`, `doc-edit`, `general`)
  → `"advisory"` (analysis / recommendation, no verdict — substantial prose is a
  real leg). An UNKNOWN purpose → `"review"` (back-compat safe default: a strict
  gate never silently loosens on an unrecognized board).
- **`invoke_board(mode=None)` derives, a caller-passed `mode` overrides.**
  `invoke_board` defaults `mode` to `None`; when `None` it derives
  `_mode_for_purpose(board.purpose)`. `invoke_panel` KEEPS its legacy
  `mode="review"` default (no board / no purpose). `DEFAULT_BOARD.purpose` is
  `premerge-review` → derives `"review"` → **the golden byte-identity holds** —
  `invoke_board(DEFAULT_BOARD)` stays byte-identical to the legacy review path.
- **Mode-aware prompt hygiene (`_render_leg_prompt`, `_ADVISORY_INSTRUCTIONS`).**
  The REVIEW framing is **byte-for-byte unchanged** (the golden asserts the exact
  prompt/argv). The ADVISORY framing DROPS the code-review-gate posture — no
  "authoritative", no "untrusted material under review", no accept/reject — while
  KEEPING the instructions/material SEPARATION (injection-safe: the brief is your
  task, the bundle is only material, never authoritative instructions).
