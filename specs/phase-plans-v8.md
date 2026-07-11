# EXECDISPATCH — Phase Plan v8

> How to use this document: save to `specs/phase-plans-v8.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v8-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

The phase-loop execute path already has a **capability registry**:
`phase-loop-runtime/src/phase_loop_runtime/capability_registry.py:511`
`capability_registry() -> dict[str, ExecutorCapabilityRecord]` returns a data
record per executor (`ExecutorCapabilityRecord` at `models.py:1738`, carrying
`promotion_status`, `output_capture_format`, `auth_preflight_probes`,
`live_proof_gate`, `permission_posture`, effort/model maps, …). It is consumed
when building a launch (`launcher.py:764` `capability = capability_registry()[request.executor]`
inside `build_launch_spec(request) -> LaunchSpec`) and by dispatch
(`capability_registry.py:627` `resolve_dispatch_decision`, imported at
`broker.py:5`).

What is **not** registry-driven is **argv construction**: inside
`build_launch_spec` the executor's runnable command is still selected by
hardcoded if-branches (`if request.executor == "codex"` :768, `"claude"` :807 —
which returns a route/policy-rich `LaunchSpec`, not plain argv; `"gemini"` :1031,
already driving Antigravity's `agy`; `"opencode"` :1061; `"pi"` :1098;
`"command"` :1131 → `_build_command_launch_spec`; and a `_stub_command` tail for
the rest). Dispatch's default is `runner.py:2588`
(`resolved_executor = dispatch_decision.selected_executor or "codex"`). There is
**no run-from-harness detection on the execute path** and **no `auth_ok`
boolean** — auth today is the `auth_preflight_probes` command tuples (e.g. codex
`("codex --version", "codex --help", "codex login status")`) run at launch. The
only run-from-claude-code read that exists is on the panel path
(`panel_invoker.py:1400` `_under_claude_code`, which forces `_tui_capable=False`
at :1414 — a Claude TUI cannot be driven from inside claude-code). Delegation
(`broker.py:13`, `DELEGATION_CHILD_EXECUTORS = ("codex", "claude")`) is those two.
**grok is panel-only today**: fully proven in `panel_invoker.py` (argv
`grok -p <prompt> --output-format plain --cwd <dir> -m grok-4.5 --reasoning-effort <token>`;
`DEFAULT_LEG_MODELS["grok"] = "grok-4.5"`; `_LEG_TIMEOUT_BOUNDS["grok"] = (600, 1800)`;
plain-text stdout, no `--output-last-message` file) but **absent** from
`capability_registry.py`, `launcher.py`, `broker.py`, `profiles.py`, `models.py`.

This roadmap does four things:
it (1) **extends the existing `ExecutorCapabilityRecord`** so argv/LaunchSpec
construction, availability, auth, provider backing, and session capture are all
registry-driven — eliminating the hardcoded if-branch chain (EXECREG); then
(2) lands **grok as a pure capability-record addition** with zero edits to the
(now-removed) dispatch/launcher if-branch chain — which *is* the proof that the
registry refactor worked (GROKEXEC); then (3) layers **automatic
default-executor resolution** (run-from-harness + single-available, each hard
gated on availability∧auth) on top of the registry (AUTOSEL); and — on an
independent parallel track — (4) runs a time-boxed **spike to dissect
claude-code session data** into a versioned tool-usage profile, the evidence
gate for the north star's pi arc (DISSECT).

The thesis: the registry already exists; making *dispatch + launch* read it end
to end (not just capability metadata) is the structural change that un-corners
pi-as-native-substrate later (see the north star). Everything is additive or
gated so no existing run changes behavior silently — except AUTOSEL, which
deliberately changes the *default* and therefore carries an extra review gate.

Raw material to reuse rather than rewrite: the existing
`ExecutorCapabilityRecord` + `capability_registry()` + `resolve_dispatch_decision`
(`capability_registry.py`); `build_launch_spec` / `LaunchSpec`
(`launcher.py:81`, :763); the panel's proven grok invocation and the `agy`
plain-text closeout pattern (`panel_invoker.py`); the advisor-board PATH-probe
availability pattern (`advisor_board/registries.py:314` `DEFAULT_HARNESS_REGISTRY`
+ `.is_available`); seat rendering (`advisor_board/harness_mapping.py:113`
`render_seat_invocation`); the run-from-claude-code detector
(`panel_invoker.py:1400` `_under_claude_code`); the model/profile default path
(`capability_registry.py:591` `default_executor_for_action`, :763
`default_model_profile_for_executor`); and the `AgentRuntimeProvider` seam
(`agent_runtime_provider.py:192`).

**North-star alignment:** this roadmap is the near-term execution of
[`specs/north-star-pi-native.md`](./north-star-pi-native.md) — specifically its
Principle 1 (registry-not-hardcoded dispatch), Principle 4 (session-data capture
as a first-class executor output), and evidence gates B1/B2. Read that document
for the vision and the gated backlog this roadmap feeds.

---

## Architecture North Star

```
  CURRENT (execute path)                    TARGET (this roadmap)

  runner.dispatch                           runner.dispatch
    resolved_executor or "codex"              resolve_default_executor(ctx,reg,env)
      │                                          │  1 explicit (selected_executor)
      ▼                                          │  2 run-from harness  ─┐ each layer
  build_launch_spec(request)                     │  3 single-available  ─┤ gated on
    capability = registry()[executor]            │  4 codex (legacy)     │ is_available
      │  (metadata only)                         └───────────┬───────────┘ ∧ auth_ok
      ├─ if == "codex":   build argv             ┌───────────▼───────────────────────┐
      ├─ if == "claude":  route/policy LaunchSpec│  EXTENDED ExecutorCapabilityRecord │
      ├─ if == "gemini":  argv (agy)             │  (models.py:1738) now also carries:│
      ├─ if == "opencode":argv                   │   build_command → LaunchSpec        │
      ├─ if == "pi":      argv                    │   is_available()                   │
      ├─ if == "command": _build_command_spec    │   auth_ok()  (from                 │
      └─ (stub tail)                             │      auth_preflight_probes, cached) │
      (grok: absent)                             │   provider_backing (seam hook)      │
                                                 │   get_session_transcript hook       │
                                                 │  + existing promotion_status,       │
                                                 │    output_capture_format, probes…   │
                                                 │  codex claude gemini opencode pi    │
                                                 │  command  + grok (record-only add)  │
                                                 └───────────┬────────────────────────┘
                                                             ▼
                                          build_launch_spec delegates to the record's
                                          build_command → LaunchSpec (normalized-spec
                                          golden holds it byte-stable per executor)

  DISSECT (parallel root, evidence only, no code):
     ~/.claude/projects/**/*.jsonl ──► versioned tool-usage profile + per-harness verdict
```

---

## Assumptions (fail-loud if wrong)

1. `ExecutorCapabilityRecord` (`models.py:1738`) can be extended with callable
   fields (`build_command`, `is_available`, `auth_ok`, `provider_backing`,
   `get_session_transcript`) without breaking its existing constructors/consumers
   — i.e. the record is not frozen against added optional fields. If it is, the
   extension surfaces as a loud construction error, not silent divergence.
2. Every current executor's `build_launch_spec` branch is a deterministic pure
   function of `(request, capability)` — so porting each into the record's
   `build_command` can be proven equivalent by a **normalized full-`LaunchSpec`
   golden** (not argv-only, because claude/command return route/policy/stub
   metadata, not plain argv). If a branch has hidden nondeterminism, EXECREG's
   spec-identity gate fails loudly.
3. The grok CLI accepts the panel-proven argv shape for a single-turn headless
   execute run and emits a parseable plain-text final message on stdout (no
   `--output-last-message` file, unlike codex). grok can therefore be modeled as
   `output_capture_format="terminal_summary"` like `agy`.
4. Each harness that spawns a child sets a **distinguishable** env signature, and
   run-from detection can tell **self vs child** apart (claude-code's
   `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT` leak into child and cron env). AUTOSEL
   must verify markers for **≥2 harnesses** concretely — "researched-in-lane,
   degrade to unknown" for all of them would ship a vacuous layer and is a
   fail-loud condition, not an acceptable outcome. claude-code markers are
   verified live this session; ≥1 more (codex or agy) must be verified in-lane.
5. claude-code session transcripts under `~/.claude/projects/**/*.jsonl` record
   tool calls with enough structure (tool name + argument object) to reconstruct
   a per-task tool-usage profile. The dataset **schema is drafted (v0) then frozen
   (v1) only after lane (a)'s first real jsonl inspection** — not day-1.
6. **Naming-convention decision point (non-blocking).** Three distinct artifacts
   are all named "runtime": `phase-loop-runtime` (PyPI orchestrator),
   `@consiliency/pipeline-runtime` (GP lib, npm), `@consiliency/runtime-provider`
   (the seam, npm). This roadmap **renames nothing**. The decision should be
   *made and recorded* before or during EXECREG (it informs how the record's
   `provider_backing` hook is named), but blocks no phase. Resolve via the
   maintainer; see north-star backlog B4.

---

## Non-Goals

- **No pi toolset build.** Distilled per-model pi agents and their tool profiles
  are north-star backlog (B1/B2), gated on DISSECT evidence. This roadmap
  produces the evidence, not the agents.
- **No renames.** The three-way "runtime" naming overload is surfaced as an
  Assumption/decision point only. No package, module, or symbol is renamed here.
- **No rival registry struct.** EXECREG extends the existing
  `ExecutorCapabilityRecord`; it must not introduce a parallel
  `ExecutorRegistryEntry` or a second source of truth for executor capability.
- **No default-behavior change outside AUTOSEL.** EXECREG and GROKEXEC are
  strictly additive / spec-identical. Only AUTOSEL changes the resolved default,
  and it ships an env-var escape hatch plus an extra review gate.
- **No new transport layer.** Backings continue to use existing subprocess /
  TUI-drive mechanisms; `provider_backing` is a seam reference, not a new
  transport implementation.
- **No changes to the panel path.** The 3-leg panel is byte-frozen; grok stays
  where it is in the panel. This roadmap only adds grok to the *execute* path.

---

## Cross-Cutting Principles

1. **Dispatch and launch resolve against the capability record, never a hardcoded
   list.** After EXECREG, no execute-path code may branch on a literal executor
   name to build its runnable command. Adding an executor is a capability-record
   addition; `build_launch_spec` reads the record. This is the load-bearing
   principle — it is what un-corners pi-as-native-executor later.
2. **Extend, don't rival.** All executor capability lives in the one
   `ExecutorCapabilityRecord`. New behavior (build/availability/auth/backing/
   session) is added as fields on that record, keeping a single source of truth.
3. **Additive/spec-identical until AUTOSEL.** EXECREG and GROKEXEC must not change
   the resolved executor or its `LaunchSpec` for any existing run. A normalized
   full-`LaunchSpec` golden is the acceptance bar; grok is reachable only by
   explicit selection until AUTOSEL layers it into default resolution.
4. **Every resolution layer is gated on availability ∧ auth.** No layer may
   hard-pick an executor that fails `is_available() ∧ auth_ok()`; it must fall
   through to the next layer. (Concretely: under claude-code the claude executor
   is TUI-incapable and must fall through — never be selected.)
5. **Reuse the proven patterns, don't reinvent.** grok argv + `agy` plain-text
   parse from `panel_invoker.py`; availability from `advisor_board/registries.py`;
   seat rendering from `render_seat_invocation`; run-from detection from
   `panel_invoker.py:1400` `_under_claude_code`; auth from the existing
   `auth_preflight_probes`. New code mirrors these.
6. **Session-data capture is a first-class executor output** (north-star
   Principle 4). The record's `get_session_transcript` hook makes the transcript
   an intended output; GROKEXEC's closeout must preserve, not discard, the grok
   session record.
7. **Every changed default ships an escape hatch.** Any layer that changes
   resolution behavior (AUTOSEL) must be disableable by a single documented env
   var that restores the legacy `… or "codex"` behavior.

---

## Phase Dependency DAG

```
  EXECREG   extend capability record, kill argv if-branch chain   (root)
     │
     ▼
  GROKEXEC  grok as a pure capability-record addition (zero-branch proof)
     │
     ▼
  AUTOSEL   layered default resolution (each layer gated is_available ∧ auth_ok)

  DISSECT   session-data spike   (independent parallel root; no deps, no dependents)
```

- Serial spine: `EXECREG → GROKEXEC → AUTOSEL` (unanimous 4-vendor panel: land the
  registry refactor first, then grok is a clean record-only add and *is* the
  zero-dispatch-edit proof, then AUTOSEL builds on both).
- `AUTOSEL` needs GROKEXEC (grok must be an available, auth-gated executor to be
  auto-selectable) and EXECREG (auto-selection scans the record for
  `is_available`/`auth_ok`); the serial spine already orders both before it.
- `DISSECT` shares no ancestor with any phase; it runs concurrently start to
  finish and gates only north-star backlog, not any phase here.

Critical path: `EXECREG → GROKEXEC → AUTOSEL`. `DISSECT` is off the critical path.

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream phases.
`/claude-plan-phase` concretizes each (exact signature/schema) when it plans the
owning phase.

1. **IF-0-EXECREG-1** — the **extended `ExecutorCapabilityRecord`** (`models.py:1738`).
   New fields added to the existing record (all optional/nullable so existing
   constructors keep working):
   `build_command: Callable[[LaunchRequest, ExecutorCapabilityRecord], LaunchSpec]`
   (the unified runnable-entry contract — returns a full `LaunchSpec`, not argv,
   so claude's route/policy and command's stub logic are expressible);
   `is_available: Callable[[], bool]`;
   `auth_ok: Callable[[], bool]` (built from the existing
   `auth_preflight_probes`, cached/bounded — see EXECREG lane (c));
   `provider_backing: <AgentRuntimeProvider seam hook | None>`;
   `get_session_transcript: Callable[[LaunchRequest], Transcript | None]`.
   `build_launch_spec` consumes `record.build_command`; adding an executor is a
   record addition with no if-branch edit. This gate replaces (does not
   supplement) any rival struct.
2. **IF-0-GROKEXEC-1** — grok's capability record: a `capability_registry` entry
   for `"grok"` with `build_command` producing the panel-proven argv inside a
   `LaunchSpec` (`output_capture_format="terminal_summary"`, plain-text closeout
   parse mirroring `agy`), `auth_preflight_probes` for grok, model/effort defaults
   (`grok-4.5` via `render_seat_invocation("grok", …)` + the
   `default_model_profile_for_executor` path), and the `get_session_transcript`
   hook implemented. This is the contract AUTOSEL depends on to treat grok as an
   available, auth-gated, selectable executor.
3. **IF-0-AUTOSEL-1** — the env-signature map: `HARNESS_ENV_SIGNATURES:
   dict[str, EnvSignature]` where `EnvSignature` names the env var(s), a match
   predicate, **and a self-vs-child disambiguator** (so claude-code markers
   leaking into a child/cron env do not produce a false run-from match).
   claude-code entry verified live; ≥1 more harness (codex or agy) verified
   in-lane (non-vacuous — see AUTOSEL exit).
4. **IF-0-AUTOSEL-2** — the resolution-order contract:
   `resolve_default_executor(ctx, registry, env) -> DefaultSelection` applying, in
   strict order, (1) explicit override = `dispatch_decision.selected_executor` →
   (2) run-from harness (via IF-0-AUTOSEL-1) → (3) single-available registry scan
   → (4) `"codex"` legacy fallback; **each of layers 2–3 selects only if the
   candidate passes `is_available() ∧ auth_ok()`, else falls through**. The
   returned `DefaultSelection` carries **selection provenance** (which layer
   picked, which candidates were rejected and why). Plus the
   `EXECDISPATCH_DISABLE_AUTOSEL`-style env escape hatch collapsing to layers
   (1)+(4) only.
5. **IF-0-DISSECT-1** — the dissection dataset schema, **versioned**: `v0` draft
   emitted before inspection, then **`v1` frozen after lane (a)'s first real jsonl
   inspection**. Shape: per-`(task_type, tool_name)` rows with
   `{call_count, arg_keys, arg_shape_sample, frequency}` (metadata only — no raw
   arg values or prompt bodies) plus a top-level per-harness feasibility `verdict`
   record. A committed JSON-schema file + a runnable validator accompany v1. This
   is what a future north-star pi-parity roadmap consumes.

---

## Phases

### Phase 0 — EXECREG (EXECREG)

**Objective**
Extend the existing `ExecutorCapabilityRecord` (`models.py:1738`) so that
runnable-command construction, availability, auth, provider backing, and session
capture are all registry-driven, and rewrite `build_launch_spec` to delegate to
`record.build_command`, eliminating the hardcoded executor if-branch chain.
Behavior is **spec-identical** (normalized full-`LaunchSpec`) for every existing
executor. Does **not** add a rival struct.

**Exit criteria**
- [ ] `ExecutorCapabilityRecord` carries the IF-0-EXECREG-1 fields
      (`build_command`, `is_available`, `auth_ok`, `provider_backing`,
      `get_session_transcript`), all optional; every existing record still
      constructs (`pytest -k capability_record` green).
- [ ] `build_launch_spec` delegates to `record.build_command` and contains **no**
      `if request.executor == "<literal>"` branch for runnable-command selection;
      asserted by a lint/grep test that fails if such a branch reappears. Any
      deliberately exempt literal (e.g. the `command`/`manual` stub tail, if kept)
      is enumerated in an explicit allowlist the lint reads — not silently skipped.
- [ ] A **normalized full-`LaunchSpec` golden** captures the LaunchSpec for
      **every** executor (codex, claude, gemini, opencode, pi, command) before and
      after the refactor and asserts they are identical (`pytest -k launchspec_golden`
      green). This covers route/policy/stub metadata, not just argv.
- [ ] `auth_ok()` is built from each record's existing `auth_preflight_probes`
      (cached + bounded so it is not re-run every dispatch); a test asserts
      `auth_ok` reflects probe success/failure and honors the cache bound.
- [ ] `is_available()` reuses the PATH-probe pattern
      (`advisor_board/registries.py`); a test asserts unavailable → `False`
      (not a crash) via injected PATH/env.
- [ ] `provider_backing` hook field is present and wired to the
      `AgentRuntimeProvider` seam reference (nullable; no behavior change when
      None); `conformance.v0.1.json` byte-identity guard stays green.

**Scope notes**
- Decompose into **4 lanes**, disjoint files: (a) extend `ExecutorCapabilityRecord`
  in `models.py` + publish IF-0-EXECREG-1 as an intra-phase freeze day 1;
  (b) port each `build_launch_spec` if-branch into that executor's `build_command`
  (returning the full `LaunchSpec`), preserving spec exactly, and rewrite
  `build_launch_spec` to delegate; (c) build `auth_ok` (from
  `auth_preflight_probes`, cached/bounded) + `is_available` (PATH-probe reuse);
  (d) normalized-LaunchSpec golden + no-hardcoded-branch lint (with the explicit
  exempt-literal allowlist) + conformance guard.
- **Single-writer:** `models.py` record owned by lane (a); `launcher.py`
  (`build_launch_spec` + per-executor `build_command`) owned by lane (b);
  auth/availability module owned by lane (c). Lanes (a) and (b) must not both
  write `models.py`/`launcher.py` across that boundary.
- Capture the pre-refactor normalized LaunchSpec on the base commit first, then
  hold it invariant through the port — it is the acceptance spine.

**Non-goals**
- No new executors (grok comes from GROKEXEC). No change to *which* executor is
  selected (that is AUTOSEL). No provider-backing *implementation* beyond the
  nullable hook field. No zero-dispatch-edit "toy executor" fixture — GROKEXEC is
  that proof, on a real executor.

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/models.py` (`ExecutorCapabilityRecord` :1738)
- `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py` (registry :511, dispatch :627)
- `phase-loop-runtime/src/phase_loop_runtime/launcher.py` (`build_launch_spec` :763, branches :768–:1131)
- `phase-loop-runtime/src/phase_loop_runtime/agent_runtime_provider.py` (seam hook reference :192)
- `phase-loop-runtime/src/phase_loop_runtime/advisor_board/registries.py` (PATH-probe reuse)
- `phase-loop-runtime/tests/` (LaunchSpec golden, auth_ok, is_available, no-hardcoded-branch lint)

**Depends on**
- (none)

**Produces**
- IF-0-EXECREG-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/models.py`, `capability_registry.py`, `launcher.py`
- evidence paths: `phase-loop-runtime/tests/` (LaunchSpec-golden spec-identity + auth/availability tests), `phase-loop-runtime/tests/data/conformance_golden/conformance.v0.1.json` (byte-identity guard, metadata-only)
- redaction posture: `metadata_only`
- blocker routing: LaunchSpec-golden divergence or conformance guard break → `blocker_class=contract_bug` (non-human)

---

### Phase 1 — GROKEXEC (GROKEXEC)

**Objective**
Add grok as a full execute-path executor by adding **one capability record** —
zero edits to the (now-removed by EXECREG) dispatch/launcher if-branch chain.
grok landing as a pure record-only addition **is** the proof that EXECREG's
refactor worked. **Dual purpose:** delivers a working grok executor now *and*
establishes grok as the session-data source for the future pi+grok distillation
(north-star backlog B2), via the record's `get_session_transcript` hook.

**Exit criteria**
- [ ] A `capability_registry` entry for `"grok"` exists with grok
      `auth_preflight_probes`, `output_capture_format="terminal_summary"`, and
      model/effort defaults registered through `render_seat_invocation("grok", …)`
      (`DEFAULT_LEG_MODELS["grok"]="grok-4.5"`) and the
      `default_model_profile_for_executor` path; asserted by a record test.
- [ ] grok's `build_command` produces argv byte-identical in shape to the panel
      invocation inside a `LaunchSpec`; plain-text (no `--output-last-message`
      file) closeout parse yields a valid closeout from captured sample stdout;
      both unit-tested (`pytest -k grok`).
- [ ] **Zero-dispatch-edit proof:** grok is dispatchable and launchable with **no
      edits** to `build_launch_spec`'s delegation, the dispatch resolver, or any
      if-branch (there are none after EXECREG); asserted by a test that registering
      the grok record alone drives an end-to-end launch.
- [ ] grok's `get_session_transcript` hook is implemented **and** a
      session-record **preservation** test passes: a live grok execution captures
      and preserves the session record (not discarded at closeout).
- [ ] Availability + auth probe returns a clean "unavailable"/"unauthed" (not a
      crash) when the grok binary is absent or unauthed; unit-tested via injected
      env.
- [ ] No existing executor's resolved `LaunchSpec` changes (grok is additive); the
      EXECREG LaunchSpec-golden stays green.

**Scope notes**
- Decompose into **4 lanes**, disjoint files: (a) grok `capability_registry`
  record + `auth_preflight_probes` + model/effort/profile defaults (publishes
  IF-0-GROKEXEC-1); (b) grok `build_command` (LaunchSpec, plain-text closeout
  parse mirroring the `agy` leg) — a `build_grok_command` helper in `launcher.py`,
  *referenced from* the record, not an if-branch; (c) grok `get_session_transcript`
  hook + is_available/auth wiring; (d) tests: grok argv/parse, zero-dispatch-edit
  proof, session preservation, availability/auth degrade + live-grok capture in
  verification.
- **Single-writer:** the grok record owned by lane (a); `build_grok_command` in
  `launcher.py` owned by lane (b); session hook owned by lane (c). Because EXECREG
  removed the if-branch chain, lane (b) adds a helper function, never an if-branch.
- Mirror the `agy` leg for closeout-schema-in-prompt + plain-text parse. Reuse
  `_LEG_TIMEOUT_BOUNDS["grok"] = (600, 1800)` semantics — never wrap grok in a
  short bash `timeout`.

**Non-goals**
- No auto-selection of grok (that is AUTOSEL). No panel-path changes. No
  delegation support for grok (`broker.py` stays `("codex","claude")`).

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py` (grok record)
- `phase-loop-runtime/src/phase_loop_runtime/launcher.py` (`build_grok_command` helper)
- `phase-loop-runtime/src/phase_loop_runtime/models.py` (grok model/effort default)
- `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` (reference only — grok argv + agy parse pattern)
- `phase-loop-runtime/src/phase_loop_runtime/advisor_board/harness_mapping.py` (`render_seat_invocation`)
- `phase-loop-runtime/tests/` (grok record/argv/parse, zero-edit proof, session-preservation tests)

**Depends on**
- EXECREG

**Produces**
- IF-0-GROKEXEC-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py`, `launcher.py`, `models.py`
- evidence paths: `phase-loop-runtime/tests/` (grok record + argv + parse + zero-edit + session-preservation, metadata-only)
- redaction posture: `metadata_only`
- blocker routing: missing/malformed test evidence or session-preservation failure → `blocker_class=contract_bug` (non-human)

---

### Phase 2 — AUTOSEL (AUTOSEL)

**Objective**
Layer automatic default-executor resolution on top of the capability record:
explicit override (exists, unchanged) → run-from harness detection (NEW) →
single-available registry scan (NEW) → `codex` legacy fallback (unchanged). Each
of the two new layers hard-gates on `is_available() ∧ auth_ok()` and falls
through on failure. This is the only phase that changes **default** behavior, so
it ships an escape hatch and takes an extra panel review before build.

**Exit criteria**
- [ ] `HARNESS_ENV_SIGNATURES` map (IF-0-AUTOSEL-1) exists with **self-vs-child
      disambiguation** and markers **verified for ≥2 harnesses** (claude-code
      live + ≥1 of codex/agy verified in-lane). A map where every harness is
      "unknown/degrade" fails this criterion (non-vacuous).
- [ ] `resolve_default_executor(ctx, registry, env) -> DefaultSelection`
      (IF-0-AUTOSEL-2) applies the four layers in strict order; **one behavior
      test per layer** plus the returned **selection provenance** (which layer
      picked, which candidates rejected + why) asserted.
- [ ] **FM1 test** — run-from detected but candidate fails availability/auth →
      falls through, does not hard-pick. Concretely: **running under claude-code**
      (`_under_claude_code` true → claude executor TUI-incapable) the resolver
      must **not** select claude; it falls through to the next layer. Unit-tested.
- [ ] **FM2 test** — single-available scan skips an unavailable-or-unauthed
      candidate and only selects one passing `is_available() ∧ auth_ok()`;
      unit-tested with a constructed registry.
- [ ] Escape-hatch env var (e.g. `EXECDISPATCH_DISABLE_AUTOSEL=1`) collapses
      resolution to explicit-override + codex-legacy only; asserted by a test.
- [ ] Layer 1 (explicit) is defined concretely as
      `dispatch_decision.selected_executor`; when none is detected and multiple/zero
      executors are available, the default remains `codex` (legacy regression test).

**Scope notes**
- Decompose into **4 lanes**, disjoint files: (a) `HARNESS_ENV_SIGNATURES` +
  run-from detector with self-vs-child disambiguation (reuse
  `panel_invoker.py:1400` `_under_claude_code`; verify codex/agy markers in-lane —
  publishes IF-0-AUTOSEL-1); (b) `resolve_default_executor` layered resolver with
  per-layer `is_available ∧ auth_ok` gating + `DefaultSelection` provenance +
  escape hatch (publishes IF-0-AUTOSEL-2); (c) wire the resolver into `runner.py`
  dispatch (replacing bare `… or "codex"` at :2588); (d) per-layer behavior tests
  + FM1/FM2 + escape-hatch + legacy regression.
- **Single-writer:** `runner.py` dispatch site owned by lane (c); resolver module
  by lane (b); signature map by lane (a).
- Research task (lane a): capture the env markers codex / agy set when they spawn
  a child, and the self-vs-child discriminator for each (claude-code verified live:
  `CLAUDECODE=1`, `AI_AGENT=claude-code_*`, `CLAUDE_CODE_ENTRYPOINT=cli` —
  but these leak to children, so the discriminator matters). Record ≥2 verified.

**Non-goals**
- No change to the explicit-override or codex-legacy layers (preserved verbatim).
  No PATH auto-discovery beyond the record `is_available` scan. No delegation
  changes.

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/runner.py` (dispatch site :2588)
- `phase-loop-runtime/src/phase_loop_runtime/` (new resolver + env-signature map modules)
- `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` (reference — `_under_claude_code` :1400)
- `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py` (`is_available`/`auth_ok` from EXECREG)
- `phase-loop-runtime/tests/` (per-layer behavior + FM1/FM2 + escape-hatch + regression)

**Depends on**
- EXECREG
- GROKEXEC

**Produces**
- IF-0-AUTOSEL-1
- IF-0-AUTOSEL-2

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/runner.py`, new resolver + env-signature map modules
- evidence paths: `phase-loop-runtime/tests/` (per-layer + FM1/FM2 + escape-hatch + legacy regression, metadata-only)
- redaction posture: `metadata_only`
- blocker routing: run-from layer hard-picks an unavailable/unauthed executor, or default-behavior regression → `blocker_class=contract_bug` (non-human)

---

### Phase 3 — SPIKE-DISSECT (DISSECT)

**Objective**
Time-boxed spike (independent parallel root): dissect claude-code session data
(`~/.claude/projects/**/*.jsonl` transcripts — the richest source) to deliver a
**versioned tool-usage profile** report + dataset (which tools, argument shapes,
frequencies, per task type) and a **per-harness feasibility verdict** grounded in
an **actual second-harness extraction attempt**. Deliverable is a **report +
dataset + validator, not production code**. This is the evidence gate for the
north star's pi arc (backlog B1/B2).

**Exit criteria**
- [ ] A `v0` draft schema is emitted, then **`v1` frozen after lane (a)'s first
      real jsonl inspection** (IF-0-DISSECT-1); a committed JSON-schema file
      accompanies v1.
- [ ] A dataset conforming to IF-0-DISSECT-1 v1 is produced from a sampled set of
      real claude-code transcripts: per-`(task_type, tool_name)` rows with
      `{call_count, arg_keys, arg_shape_sample, frequency}` (metadata only).
- [ ] A written report summarizes the claude-code tool-usage profile (top tools,
      argument-shape patterns, per-task-type variation).
- [ ] The per-harness feasibility **verdict** is grounded in an **actual
      extraction attempt against ≥1 second harness** (codex or agy session data),
      not a format read-through; it states, per harness, whether an equivalent
      profile is extractable, with the concrete blockers found.
- [ ] The verdict answers the north-star **B1 threshold** explicitly: are profiles
      extractable for **≥2 harnesses** (claude-code + ≥1 verified by real
      extraction)? yes/no + which two.
- [ ] Dataset + report + schema + validator committed under a `spikes/`- or
      `research/`-style path (not `src/`); no production code path modified.

**Scope notes**
- Decompose into **3 lanes**: (a) claude-code transcript parser + profile
  extractor → dataset; owns the extraction script, the v0→v1 schema, and the
  validator; (b) the report write-up from the dataset; (c) second-harness
  extraction attempt (codex or agy) + the feasibility verdict — this lane owns the
  real "≥2 harness" B1 answer via an actual extraction run, not an inspection.
- **Single-writer:** dataset+schema+validator owned by lane (a); report by lane
  (b); verdict by lane (c). Disjoint output files.
- Time-box: prefer a representative transcript sample over exhaustive coverage.
  Verdict quality (backed by a real second extraction) matters more than dataset
  size.
- Redaction: the dataset captures tool names / arg *keys* / shape samples /
  frequencies, **not** raw arg values or prompt bodies. The validator checks
  redaction (fails if a row carries a raw value/body).

**Non-goals**
- No pi toolset build, no distilled-agent build (north-star B1/B2, gated on this
  spike's own output). No production code changes. No changes to any executor.

**Key files**
- `spikes/execdispatch-dissect/` (new — extraction script, v1 schema, validator, dataset, report, verdict)
- `~/.claude/projects/**/*.jsonl` (input, read-only, sampled)

**Depends on**
- (none)

**Produces**
- IF-0-DISSECT-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `spikes/execdispatch-dissect/` (research artifacts only)
- evidence paths: `spikes/execdispatch-dissect/` (dataset + v1 schema + validator + report + verdict, metadata-only — tool names/arg-keys/shapes/frequencies, no raw values)
- redaction posture: `metadata_only`
- blocker routing: dataset fails IF-0-DISSECT-1 v1 schema or redaction validation → `blocker_class=contract_bug` (non-human)

---

## Execution Notes

- **Planning**: `/claude-plan-phase EXECREG`, then `/claude-plan-phase GROKEXEC`,
  then `/claude-plan-phase AUTOSEL` (serial spine — each depends on the prior
  freeze). `/claude-plan-phase DISSECT` shares no DAG ancestor and can be planned
  concurrently with all of them.
- **Execution**: `/claude-execute-phase execreg` → `/claude-execute-phase grokexec`
  → `/claude-execute-phase autosel` in order. `/claude-execute-phase dissect` runs
  concurrently with everything.
- **AUTOSEL extra review gate:** AUTOSEL is the only phase that changes the
  resolved **default** executor. Before its build, run an extra advisor-panel
  review of the resolution-order contract (IF-0-AUTOSEL-2), the availability∧auth
  gating (FM1/FM2), and the escape hatch — do not execute AUTOSEL straight from
  plan approval the way EXECREG/GROKEXEC can. The escape-hatch env var must land in
  the same change as the new layers.
- **Critical path**: `EXECREG → GROKEXEC → AUTOSEL` — wall-clock minimum.
  `DISSECT` is off the critical path (parallel root, gates only north-star backlog).
- **Single-writer files across phases**: the serial spine removes the earlier
  parallel-root collision on `launcher.py` — EXECREG owns the `build_launch_spec`
  refactor first; GROKEXEC then adds only a `build_grok_command` helper referenced
  from grok's capability record (no if-branch, because EXECREG removed the chain).
  `runner.py` is written by EXECREG (dispatch reads the record) and AUTOSEL
  (resolver wire-in), but AUTOSEL depends on EXECREG so the order is forced —
  no extra coordination. `models.py` `ExecutorCapabilityRecord` is written by
  EXECREG (fields) and GROKEXEC (grok's model default) in that serial order.

---

## Acceptance Criteria

- [ ] `ExecutorCapabilityRecord` is the single source of truth: it carries
      `build_command`/`is_available`/`auth_ok`/`provider_backing`/
      `get_session_transcript`, `build_launch_spec` delegates to it, and no
      runnable-command if-branch on a literal executor name remains (exempt
      literals enumerated in an allowlist). Normalized full-`LaunchSpec` golden is
      green for every pre-existing executor; conformance guard green.
- [ ] grok runs as an execute-path executor added as a **pure capability-record
      addition** (zero edits to dispatch/launcher if-branch chain), with argv
      byte-identical in shape to the panel invocation, a working
      `get_session_transcript` hook (session-preservation test green, live grok
      capture in verification), and clean availability/auth degrade.
- [ ] Default-executor resolution applies override → run-from-harness →
      single-available → codex, with **each new layer gated on
      `is_available() ∧ auth_ok()`** (FM1: under claude-code, claude falls through;
      FM2: unavailable/unauthed candidates skipped), a working single-env-var
      escape hatch, selection provenance, and a per-layer behavior test suite;
      legacy default (codex) preserved when nothing is detected.
- [ ] A committed **versioned** tool-usage dataset (IF-0-DISSECT-1 v1) + JSON
      schema + runnable validator + report + per-harness feasibility verdict
      (backed by a real second-harness extraction) exist, answering the "≥2
      harness extractable" north-star B1 threshold.

---

## Verification

```bash
cd phase-loop-runtime

# Whole-roadmap test suite (each phase adds machine-checkable tests)
python3 -m pytest tests/ -q

# EXECREG — extended record + normalized LaunchSpec golden + auth_ok/is_available + no-hardcoded-branch lint
python3 -m pytest tests/ -k "capability_record or launchspec_golden or auth_ok or is_available or no_hardcoded_branch" -q
python3 -m pytest tests/ -k conformance -q   # byte-identity guard still green

# GROKEXEC — grok record/argv/parse + zero-dispatch-edit proof + session preservation (incl. live grok capture)
python3 -m pytest tests/ -k "grok or zero_dispatch_edit or session_preservation" -q

# AUTOSEL — per-layer resolution + FM1 (under-claude-code falls through) + FM2 + escape hatch + legacy regression
python3 -m pytest tests/ -k "resolve_default or autosel or fm1 or fm2 or escape_hatch" -q

# DISSECT — dataset validates against the committed IF-0-DISSECT-1 v1 schema + redaction check (real validator, not test -f)
python3 spikes/execdispatch-dissect/validate_profile.py spikes/execdispatch-dissect/tool_usage_profile.json spikes/execdispatch-dissect/schema.v1.json
test -f spikes/execdispatch-dissect/feasibility_verdict.md && echo "verdict present"

# Roadmap structure self-check
phase-loop validate-roadmap specs/phase-plans-v8.md
```
