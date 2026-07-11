# Changelog

All notable changes to `agent-harness` (the `phase-loop-runtime` package + the
`phase-loop-skills` bundle) are documented here. This project adheres to semantic
versioning; the release tag, the package `version`, and this file are kept in lockstep.

## Unreleased

- **Add the authenticated local task-message source broker.** A loopback-only
  user service wraps the real Codex owner socket, authenticates capability
  tokens by pinned SHA-256 before socket access, and streams exact metadata-only
  heartbeats plus one release-SHA-bound resolver result. New `--broker-url`
  probe/resolve mode consumes this channel without a total wall-clock timeout
  while heartbeats remain fresh. Tailscale Serve HTTPS is the only documented
  tailnet exposure; SSH workarounds, Funnel, second app-servers, and arbitrary
  proxy methods remain forbidden.

- **Fix task-message persistence compatibility (`ViperJuice/agent-harness#165`).**
  Resolve governed approval sources by Codex app-server's persisted
  `userMessage.clientId`, not its separately assigned item `id`, and require a
  distinct `<source-client-id>-approval` user message for the exact JSON body.
  Both messages must have one text item, unique client and stored identities,
  source-before-approval ordering, fresh turn timestamps, and matching body
  claims. A new local `--control-socket` transport performs the supported
  WebSocket-over-Unix handshake with compression disabled, so the resolver can
  run on the source host and return its proof over an independently
  authenticated channel without exposing a network listener.
  App-server-concatenated single-item envelopes remain rejected.

## [0.7.0] — 2026-07-11

CLEANSHIP — a single-writer backlog closeout of confirmed phase-loop **runner**,
**executor-governance**, **status**, **skill**, and **advisor-board** correctness
bugs plus **roadmap-discovery** hygiene, landed on top of the post-`0.6.2`
EXECDISPATCH (default-executor AUTOSEL) and adoptability platform work below.
Behavioral changes in the executor-governance AUTO gate
(`ViperJuice/agent-harness#153`) and advisor-board availability
(`ViperJuice/agent-harness#151`) motivate the minor bump.

### Added

- **The 4-vendor advisor board is now the runnable agent-facing default.** A new
  `phase-loop advisor-board <artifact>` CLI subcommand runs a real
  availability-aware review by composing seats through
  `advisor_board.composition.compose_review_board` (auth-aware: a vendor is seated
  only when it is BOTH on PATH and authenticated, REVIEWGOV IF-0-REVIEWGOV-1) and
  dispatching them via `panel_invoker.invoke_board`. The 8 harness advisor skills'
  RUNNABLE code blocks now call this board path instead of
  `invoke_panel(available_panel_legs())`. The load-bearing legacy `invoke_panel`
  (kept by the governed review/premerge gates) is **untouched** — its body and its
  byte-identical golden are unchanged.

- **Opt-in streaming verdict delivery on the shared `_run_legs_ordered`
  (IF-0-REVIEWGOV-2).** `invoke_panel` / `invoke_board` gain optional
  `on_leg_complete` (a per-leg callback) and `stream_dir` (incremental per-leg
  verdict files) parameters. When set, each leg's verdict is delivered the moment
  it lands — no head-of-line blocking on the slowest leg — while the consolidated
  return is still re-sorted to submission order. Both default to the exact
  historical behavior, so the load-bearing `invoke_panel` path and its
  byte-identical advisor-board golden are untouched. The streaming side-channel is
  fail-open (a raising callback or an unwritable `stream_dir` never breaks the pool
  or fails a leg).

- **`default_board_auth_ok`** — the reusable, fail-closed board auth probe, exported
  from `advisor_board`. It is the default gate `load_boards` applies (so the live
  `code-review` board is auth-aware by default); a caller can also inject its own
  `auth_ok` to override or, in a test, isolate the availability dimension.

### Fixed

- **Governed dry-run no longer performs closeout side effects
  (`ViperJuice/agent-harness#78`).** A `phase-loop run … --dry-run
  --closeout-mode commit` against a phase already in `awaiting_phase_closeout`
  entered `_perform_phase_closeout` — launching the governed premerge panel and
  staging the worktree — instead of remaining side-effect-free. The two
  `_perform_phase_closeout` call sites inside the dispatch closure (the
  awaiting-closeout dispatch and the repair-recovery re-closeout) now preview the
  pending closeout and break under `--dry-run`, emitting a `dry_run` terminal
  with no panel launch, no `git add`, and no commit. `dry_run` is not threaded
  into `_perform_phase_closeout` itself — the guard lives at the call site so the
  closeout body stays side-effect-free by construction.

- **`--closeout-allow-unowned` breaks through a sticky closeout scope violation on
  rerun (`ViperJuice/agent-harness#71`).** After a partial closeout committed the
  phase-owned subset and blocked human-required with
  `blocker_class=closeout_scope_violation` over a live unowned remainder, an
  operator rerun with a non-empty `--closeout-allow-unowned <reason>` recorded the
  attestation event but never recovered — the dispatch closure short-circuited at
  the human-required guard before closeout could consume the reason. The guard now
  routes a break-glassable `closeout_scope_violation` into `_perform_phase_closeout`
  with the reason (this is the "SL-1 rerun" the BREAKGLASS protocol promises); all
  other human-required blockers still short-circuit. The closeout fallback
  re-derives the unowned remainder from live git when the reconciled blocked
  snapshot carries no dirty summary, **scoped to the remainder the prior closeout
  recorded** (the paths the operator's reason attests to) intersected with what is
  still dirty — so an unrelated live edit can never be force-committed under a reason
  that named only the phase's remainder. Secrets remain non-break-glassable and keep
  the phase blocked. The closeout now **isolates the index** before staging, review,
  and commit — it resets the index to `HEAD`, stages only the accepted
  `closeout_dirty_paths`, and commits the reviewed staged index (pathspec-less);
  previously a pre-staged unrelated file — including a `.env`/secret the fallback
  deliberately excluded — was swept into the commit, silently defeating the
  secrets-never-break-glassable contract. A **secret-only** break-glass remainder now
  also keeps the sticky `closeout_scope_violation` (`human_required`) gate instead of
  downgrading to a non-human `dirty_worktree_conflict`.

- **A valid planned repair closeout clears the stale blocker instead of looping
  repair (`ViperJuice/agent-harness#59`).** When a bounded repair child reshaped
  the plan and emitted a valid closeout (`terminal_status=planned`,
  `verification_status=not_run`, `dirty_paths=[]`, no blocker, `human_required=null`)
  leaving the tree clean, the parent runner kept the stale non-human blocked state
  and relaunched the same repair path. `repair_precondition_for_snapshot` now clears
  the planned-repair-closeout case (beyond `dirty_worktree_conflict`) so the phase
  re-executes from the repaired plan — conditioned on the repair child's own
  planned/not_run/clean evidence (every field required present) and a clean tree,
  not on `blocker_class` alone. The evidence predicate is **fail-closed**: a
  truncated/partial child payload cannot clear a blocker.

- **Claude `subagent`/`agent_team` authoring actions auto-degrade to solo instead
  of an opaque TEAMGOV block (`ViperJuice/agent-harness#153`).** A claude run in
  `subagent` or `agent_team` mode whose sub-step is an authoring action
  (`plan`/`roadmap`/`maintain-skills` — the modes' `disallowed_actions`) previously
  terminated with a bare policy sentence even though team semantics are meaningless
  for a single authoring action. `build_claude_launch_spec` now AUTO-DEGRADES that
  case to solo and dispatches (solo tool policy, recorded
  `claude_execution_mode=solo`); the authoring set is read from the mode's own
  `ClaudeTeamPolicy.disallowed_actions`, never re-hardcoded. Additionally,
  `default_executor_resolver._gate_candidate` now consults claude's
  `claude_execution_policies`: on the AUTO path an authoring action under
  `subagent`/`agent_team` skips claude rather than seeding a pick the launcher would
  then block. The seed-gate and the launch-time auto-degrade are LAYERED: the gate
  removes claude from the AUTO seed; the auto-degrade is the backstop that dispatches
  claude-solo in the residual session-degraded case. A residual (non-authoring) team
  block now carries actionable remediation in the runner terminal.

- **`phase-loop status` no longer dirties `plans/manifest.json`
  (`ViperJuice/agent-harness#62`).** A `phase-loop status` (or `handoff`) that
  reconciled the plan manifest could append a synthetic auto-import row or flip a
  missing-file entry to `orphaned`, silently mutating a tracked file on a pure read
  path. `reconcile()` now takes a keyword-only `read_only` flag (default `False`,
  so every write-intent caller is byte-for-byte unchanged) threaded into
  `_reconcile_plan_manifest`, where it skips the `append_entry` and
  `update_lifecycle` writers by construction while still surfacing the same ledger
  warnings. `status_snapshot()` defaults to `read_only=True`, and the `status` and
  `handoff` CLI commands pass it explicitly — so a read invocation leaves the
  worktree byte-clean. The duplicate-ACCEPT drift is confirmed already resolved by
  the `#46` `_manifest_file_phase_key` dedup (verified load-bearing end-to-end); no
  new dedup logic was required.

- **Advisor board no longer seats an unauthenticated vendor
  (`ViperJuice/agent-harness#151`, IF-0-REVIEWGOV-1).** `compose_review_board` now
  composes on `is_available ∧ auth_ok`: a PATH-present-but-unauthenticated vendor
  (e.g. a `grok` binary on PATH with no logged-in session) is treated as **down** —
  dropped and backfilled onto an authenticated vendor with a distinct lens, exactly
  like a PATH-absent vendor. The auth gate reuses each executor's own cached,
  timeout-bounded, fail-closed `auth_ok` (`executor_availability.auth_ok_for`), so
  the board's verdict is single-sourced with the dispatch path's and never
  re-implements probing. The live convening path is auth-aware **by default**.

- **Roadmap discovery no longer auto-selects a stale/completed roadmap on a bare
  run.** `discovery.manifest_backed_roadmap` now also skips manifest entries with
  `status == "completed"` (previously only `"orphaned"`), so an all-completed
  manifest falls through to the glob branch instead of silently resuming finished
  work. Default ON, with a one-release env escape hatch
  `PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED=1` that restores the pre-change behavior.
  Genuine resumption is unaffected — the state-file ladder (`active_state_roadmap`)
  precedes the manifest branch.

- **The ambiguous-glob roadmap selection is now a recoverable blocker, not a
  crash.** With multiple `specs/phase-plans-v*.md` present (agent-harness itself
  ships `v1`–`v9`) and no state/manifest/handoff to disambiguate, `select_roadmap`
  raised a bare `RuntimeError` that surfaced as an uncaught traceback. It now
  raises a typed `AmbiguousRoadmapError`, which the CLI converts to a
  `blocker_class="ambiguous_roadmap_selection"` snapshot with an actionable
  "pass `--roadmap`" summary (exit 2). This ships as a UNIT with the
  `completed`-skip above.

- **`plans/manifest.json` repaired.** The tracked manifest was frozen at `v4` and
  never recorded `v5`–`v9`, so a bare run resolved a single completed roadmap. Its
  stale entries are marked to no longer resolve a completed roadmap on a bare run.

- **Regression guard: read/write status parity on an orphaned-entry + renamed-plan
  repo (`ViperJuice/agent-harness#162`).** The `#162` follow-up (grok CR of READONLY
  `#62`) hypothesized that a read-only `phase-loop status`/`handoff` over a manifest
  entry whose plan file was renamed would diverge from a write-intent `reconcile()`.
  Verified at primary source that this does NOT reproduce on this base (the
  `manifest_plan_file_missing` branch is unreachable — a missing-file entry fails
  `validate_manifest` and `_phase_manifest_entries` hides all entries symmetrically
  in read and write mode). A parity test pins read-mode `phases` and
  `ledger_warnings` equal to the write path across `committed`/`executing`/`completed`
  statuses, guarding against a future change to the validate gate or orphan logic.

### Changed

- **Explicit `--phase` consistency on the concurrent coordinator-waves selector.**
  `_select_parallel_dispatch_phase` did not accept a `phase` argument, so it could
  only pick by wave order. It now accepts and honors an explicit phase (bounded to
  the wave structure), mirroring the serial `_select_ready_phase`. This is a
  **defensive consistency** change, not a currently reachable bug fix: through
  `run_loop`, `coordinator_waves` is populated only when no explicit `--phase` is set,
  so an explicit phase is already served by the serial selector today; the guarantee
  matters only if that invariant changes.

- **`claude-plan-detailed` is usable outside Plan Mode and defaults to
  `.consiliency/plans/` (`ViperJuice/agent-harness#87`).** Non-Plan-Mode invocation
  is now a first-class path: the skill writes the plan artifact + handoff without
  calling `ExitPlanMode` or gating on a plan-approval flow. When Plan Mode is active
  it still calls `ExitPlanMode`. Detailed plans now default to
  `.consiliency/plans/detailed-<slug>-<YYYYMMDD-HHMM>.md` (dir auto-created) instead
  of `plans/` at repo root; `--output` still overrides, and the `plans/manifest.json`
  registry location is unchanged (the entry `file` field records the new path).
  Claude-only skill source edit; gemini/opencode/codex sources untouched; regenerated
  `phase-loop-skills/` + packaged `skills_bundle/` copy, `test_skills_canon_parity.py`
  green.

- **Advisor `-panel`/`-board` skill twins collapsed to one canonical skill per
  harness plus an alias.** The canonical source is `<harness>-advisor-board`;
  `<harness>-advisor-panel` is installed as an alias of it (`SKILL_ALIASES`), so a
  historical `/<harness>-advisor-panel` invocation resolves to today's board skill
  and the two can never drift.

### Security

- **grok `execute` runs with a `--disallowed-tools` deny-list that removes privileged
  non-coding built-ins (`ViperJuice/agent-harness#154`).** The grok `execute` leg now
  subtracts the scheduler (`scheduler_create`/`scheduler_delete`/`scheduler_list`/
  `monitor`) and image/video (`image_gen`/`image_edit`/`image_to_video`/
  `reference_to_video`) built-in families while keeping grok's coding tools
  (read/search + write/edit + terminal), so a headless execute leg cannot schedule
  work or generate media outside the phase-loop's governance. Scoped to `execute`;
  `review` keeps its stricter read-only `--tools` allow-list. Live-proven against grok
  0.2.93 (an argv-verified deny-list; a behavioral tripwire test documents the
  still-open subagent gap — see Known open below). The originally-specified `--tools`
  ALLOW-LIST is unusable for a write leg (grok force-adds `run_terminal_command`, whose
  default config aborts the session), so a deny-list is used instead.

### Known open / deferred

- **`ViperJuice/agent-harness#154` (residual) — grok `spawn_subagent` cannot be
  disabled from the CLI.** NEITHER `--disallowed-tools spawn_subagent` NOR the
  dedicated `--no-subagents` flag stops a headless grok leg from spawning (both
  verified BEHAVIORALLY — a forced spawn still succeeds with a live `subagent_id`).
  Both levers are passed anyway as forward-compat, and a behavioral tripwire test
  (`test_grok_spawn_subagent_denial_tripwire`) trips the moment a future grok blocks
  it, so the gap is documented, not silently over-claimed. #154 stays open on this
  residual.

- **`ViperJuice/agent-harness#164` — advisor-board manifest fragility (open).** The
  advisor-board manifest ingestion remains fragile to malformed/partial manifests;
  tracked separately from the READONLY `#62` read-only fix and the `#162` parity
  guard above. Not addressed in this release.

- **`ViperJuice/agent-harness#84` — explicit-`--phase` serial selection
  (investigation).** The reported serial-path symptom (`--phase ROOM` repairs a
  blocked `SEAL` instead of dispatching the explicit ready phase) does **not**
  reproduce on current `main`: the serial selector already honors an explicit
  `--phase` and AUTOSEL/#152 touches zero phase-selection code (confirmed). The
  adjacent concurrent coordinator-waves selector was hardened for consistency (see
  Changed above); a regression guard pins `(ROOM, execute)` on the serial path. See
  `plans/decision-issue-84-explicit-phase-20260711.md`. #84 kept open pending a
  reproducible case.

- **REVIEWGOV W3/W4 deferred to the next roadmap (non-goals).** The review-policy
  layer — `ViperJuice/agent-harness#88` (SHA-bound review gate),
  `ViperJuice/agent-harness#145`/`#146` (release-dispatch approval + concurrency),
  and `governed-pipeline#74` (governed merge-policy consumer) — is out of scope for
  CLEANSHIP and recorded here as an explicit non-goal.

### Platform work landed since 0.6.2 (EXECDISPATCH + adoptability)

- **Authenticated cross-host task-message proof resolver (`ViperJuice/agent-harness#155`).**
  Added neutral `task-message-probe` and `task-message-resolve` commands backed by
  the Codex app-server's authenticated WebSocket `thread/read` protocol. The
  resolver accepts only an exact, pre-identified two-message envelope (source
  message plus JSON approval body), binds the body's source identity and
  SHA-256 claim to the exact source-message UTF-8 bytes,
  computes the RFC 8785 canonical approval digest, enforces freshness, and fails
  closed with a frozen typed error set. Probe/failure output is metadata-only;
  raw bytes are returned as base64 only on successful exact resolution. No
  caller-authored digest-only record or copied session JSONL can satisfy the
  interface.

- **Behavior change: phase-loop now resolves the DEFAULT executor by AUTOSEL
  (EXECDISPATCH Phase 2) instead of always falling back to `codex`.** When no
  operator/CLI/plan/roadmap hint names an executor, the default is resolved in
  four ordered layers: (1) explicit override (unchanged) → (2) the harness
  phase-loop is *run from* (env-signature detection) → (3) a single-available
  registry scan → (4) the `codex` legacy default. The two new AUTO layers
  hard-gate every candidate on `is_available ∧ auth_ok ∧ launch_complete ∧
  headless_launchable ∧ live_available` and fall through on failure, so an AUTO
  layer never picks an executor dispatch would then reject (e.g. the tty-only
  `claude` leg is never auto-picked headlessly; a proof-gated-but-live executor
  like `grok` still is). Selection provenance (which layer chose, which
  candidates were rejected and why) is logged on every auto-pick. **Escape
  hatch:** set `EXECDISPATCH_DISABLE_AUTOSEL=1` to force the pre-AUTOSEL codex
  default (explicit-override + codex-legacy only). Wiring is a seed override
  threaded into `resolve_dispatch_decision` (the historical codex auto-seed
  site); explicit hints, the work-unit rotation path, and delegation/child
  defaults are unchanged. Availability/auth probes are now bounded by a strict
  per-probe subprocess timeout AND the single-available scan carries a wall-clock
  budget and short-circuits once a second executor passes, so a wedged CLI can no
  longer stall the dispatch hot path (it degrades to the codex default); probes
  fail CLOSED on a timeout or any error. Every phase-loop-owned child executor spawn now scrubs
  Claude Code's self-markers and stamps `PHASE_LOOP_CHILD=1` so a child never
  mis-reads the host harness as its own run-from context. New record field
  `ExecutorCapabilityRecord.headless_launchable`.

- **Security: hard read-only `--tools` allow-list on the advisor-panel / CR grok
  leg.** The GROKEXEC finding (agent-harness#147) established that headless
  `grok -p` AUTO-APPROVES file writes regardless of `--permission-mode` and
  `--sandbox` — the only lever that holds grok to read-only is an explicit
  `--tools` allow-list of its read/search built-ins. The launcher's grok
  *review* path already applied this; the advisor-panel / cross-vendor-CR grok
  leg in `panel_invoker._exec_leg` did NOT, so panel/CR grok reviewer legs (which
  are read-only by contract) ran with write access. The panel grok argv now
  appends `--tools GROK_REVIEW_READONLY_TOOLS` (`read_file,grep,list_dir,search_tool`),
  the same constant `launcher.build_grok_command` uses — a single shared
  source of truth for the read-only reviewer tool set. With the write built-ins
  (`write`, `search_replace`, `run_terminal_command`) and every other tool
  (scheduler / spawn_subagent / memory / image) excluded from the allow-list, the
  panel/CR grok leg can no longer mutate the workspace. A regression guard asserts
  the panel grok argv carries the exact allow-list and that no write/privileged
  tool appears in it. No behavior change for the grok EXECUTOR (execute-path)
  argv, which stays proof-gated and out of scope.

- **Registry-driven launch dispatch (EXECDISPATCH EXECREG, IF-0-EXECREG-1).**
  `ExecutorCapabilityRecord` (`models.py`) now carries optional
  `build_command` / `is_available` / `auth_ok` / `provider_backing` /
  `get_session_transcript` fields, and `build_launch_spec` delegates to the
  record's `build_command` instead of a hardcoded `if request.executor == …`
  if-branch chain. Runnable-command construction for every executor (codex,
  claude, gemini, opencode, pi, command, manual) is now a per-record build
  function; adding an executor is a capability-record addition, not a dispatch
  edit. Behavior is spec-identical — a normalized `LaunchSpec` golden (every
  field except the two pass-through preamble objects, `prompt_bundle` /
  `injection_metadata`, which a single shared helper computes identically for all
  executors) is asserted byte-identical for every executor across codex, gemini,
  opencode, pi, command, manual and the claude agent_view / channel / print route
  paths; the golden was regenerated on the pre-refactor base and proven to match
  the refactored output byte-for-byte. Availability (PATH probe) and auth (cached,
  bounded probe gate) live
  in the new `executor_availability` module and are dormant until AUTOSEL. No
  default-executor or runtime behavior change.

- **SPIKE-DISSECT research artifacts (EXECDISPATCH Phase 3, gate IF-0-DISSECT-1).**
  Added a research-only spike under `spikes/execdispatch-dissect/` — a versioned
  tool-usage-profile schema (`schema.v0.draft.json` → frozen `schema.v1.json`), a
  metadata-only extractor (`extract_profile.py`), a stdlib-only validator with an
  active semantic redaction gate (`validate_profile.py`), claude-code + codex
  datasets, and a per-harness feasibility verdict. No production `src/` change; the
  CHANGELOG entry accompanies the committed `schema*.json` public-surface files so
  the docs-freshness audit stays green. Answers north-star B1 (profiles extractable
  for ≥2 harnesses: claude-code + codex).

- **EXECDISPATCH roadmap + pi-native north star (docs).** Adds
  `specs/phase-plans-v8.md` (the panel-ratified EXECDISPATCH phase plan —
  EXECREG → GROKEXEC → AUTOSEL serial spine plus the SPIKE-DISSECT parallel
  root) and `specs/north-star-pi-native.md` (the vision + gated backlog the
  roadmap executes against). Docs-only; no runtime change.

- **`consiliency-harness` publish uses the `pypi` environment.** The
  trusted-publishing workflow now gates on the same `pypi` GitHub environment as the
  runtime's `publish-pypi.yml` (instead of a bespoke `pypi-consiliency-harness`
  environment), so the maintainer registers one set of PyPI trusted-publisher
  coordinates (owner=ViperJuice, repo=agent-harness,
  workflow=publish-consiliency-harness.yml, environment=pypi) for a clean tag-and-go
  first publish. Workflow-only; no runtime change.

## [0.6.2] — 2026-07-10

Front-door adoptability for the primitive (roadmap AHADOPT; freezes
IF-0-AHADOPT-1 + IF-0-AHADOPT-2).

- **`consiliency-harness` install-friendly front door (new PyPI meta-package).**
  A pure dependency shim whose sole install-requires is
  `phase-loop-runtime>=0.6.1` and which ships ZERO `[project.scripts]` — it adds no
  console script and cannot shadow the runtime's `phase-loop` / `codex-phase-loop`.
  The obvious name `agent-harness` is an unrelated third party; `pip install
  consiliency-harness` now pulls the real engine. Staged for release via a
  dedicated tokenless-OIDC trusted-publishing workflow
  (`publish-consiliency-harness.yml`, `consiliency-harness-v*` tag namespace) —
  the maintainer cuts the tag; no agent credential.
- **`phase-loop doctor` (new top-level command).** A strict superset of
  `repo-validate doctor` that reports which tools/CLIs are installed + authed and
  what each unlocks, across BOTH install surfaces (wheel-bundled `phase-loop run`
  vs interactive `~/.claude/skills`), plus a multi-registry **BOM** comparing
  named consumer pins to npm + PyPI registry latest (PyPI `consiliency-contract`;
  npm `@consiliency/contract`, `@consiliency/canon-core`; the install-script ref;
  the mac-skills ref) with `stale|current|unknown` verdicts. Emits the checked-in
  `phase-loop-doctor.v1` schema (+ golden fixture); metadata-only. Degrades to
  `unknown` (never fails) when a registry is unreachable. `--fail-on-stale` exits
  non-zero on a stale GATING (repo-owned) target and is WIRED as a CI drift gate.
  The doctor import graph pulls no dotfiles-domain module (DECOUPLE SL-1).
- **Install-pin auto-track.** `install-agent-harness.sh` no longer hardcodes a
  stale release ref; it resolves the ref from a new checked-in `RELEASE_PIN`
  (sibling when cloned, fetched from the default branch on curl-pipe), held ==
  the package version by the `release-consistency` gate. A test asserts the pin
  is never behind the PyPI-published version.

## [0.6.1] — 2026-07-10

- **Stall-aware leg-liveness monitor (kill on heartbeat extinction, not a blind
  wall-clock).** The codex/gemini/grok print-mode review legs no longer hard-kill at
  the input-scaled `timeout_s` (a slow-but-STREAMING frontier review was being killed
  mid-review at the 600s floor, silently degrading the panel to fewer legs). Each leg
  now runs through `panel_invoker._run_leg_with_liveness`, which terminates the whole
  process group only on HEARTBEAT EXTINCTION — no new stdout/stderr byte AND no
  process-group CPU advance for `_LEG_STALL_THRESHOLD_S` (180s). Both fds are watched
  (codex streams its transcript to STDERR; grok/agy to STDOUT), and advancing
  process-group CPU is a secondary, NON-killing reset (it can only extend a leg's
  life, never false-kill). The wall-clock deadline is retained purely as a raised,
  rarely-hit backstop, so the existing `except subprocess.TimeoutExpired → 124` path
  is preserved; the input-scaled `timeout_s` now only feeds the #114 retry-fraction
  heuristic, not the kill.
  - **An EXPLICIT per-leg timeout override is honored as the hard deadline** (frozen
    contract): `timeouts_by_leg` / `timeout_seconds_by_leg` still bound a leg exactly
    (`{"gemini": 300}` kills at 300s). Only the input-scaled DEFAULT is raised to the
    `_MAX_LEG_TIMEOUT_S` backstop — `_default_spawn` alone knows whether the override
    was explicit and threads the resolved `deadline_s`/`backstop_s` down. (An earlier
    revision nullified overrides via `max(timeout_s, 1800)`; caught in cross-vendor CR.)
  - **Reclaim regression fixed:** if a leg's LEADER exits while a descendant still holds
    the stdout/stderr pipe open, the group is now reclaimed after a short idle grace
    (`_LEG_POST_EXIT_GRACE_S`) via a `force_group` `killpg` — instead of burning the full
    backstop (the old code hit neither the clean-exit nor the stall branch). Also caught
    in CR.
- **Fix: the gemini (agy) review leg silently ran an EMPTY prompt.** The leg passed
  `agy … -p -` and fed the composed prompt on stdin (`input=prompt`), but `agy -p -`
  IGNORES stdin and runs an empty prompt — agy printed its "How can I help you
  today?" greeting (~26 bytes), which the panel classified as a non-review and
  degraded on every run. The gemini leg was effectively non-functional (the
  root cause of the observed "dying agy sessions"). The prompt is now passed inline
  as the `-p` argv value (`stdin` closed via `DEVNULL`), exactly like the grok leg;
  the prompt is the small staged-bundle pointer so argv length stays bounded.

## [0.6.0] — 2026-07-10

- **4-vendor panel reports cross-vendor independence.** `advisor_board.board_independence(board)` returns `independent`/`degraded`/`none` + `distinct_vendors` so a governed consumer (gp's `degraded_independence` gate) can distinguish a backfilled same-vendor board from a true cross-vendor one — the availability-aware fallback previously surfaced no such signal (unanimous 4-vendor CR finding).
- **4-vendor default review board + availability-aware lens-diversity fallback.**
  The `code-review` board is now the 4-vendor cross-vendor panel — one seat per
  frontier vendor (grok `grok-4.5`, claude `claude-fable-5`, codex `gpt-5.6-sol`,
  gemini `Gemini 3.1 Pro`), each at its MAX thinking (gemini's ceiling is `high`)
  with a DISTINCT review lens. Grok joins as a 4th homebrew review lane
  (`grok -p <prompt> --output-format plain --cwd -m grok-4.5 --reasoning-effort`;
  registered subscription-only; full 600/1800s slow-leg timeout bounds). The new
  `advisor_board.composition.compose_review_board` composes the board
  AVAILABILITY-AWARE: it targets 4 independent reviewers (hard floor 3) and never
  collapses to 1–2 when vendors are down — each available vendor gets one
  lens-distinct seat, then the remaining seats are backfilled onto available
  vendors with different lenses (2 up → still 4 seats; 1 up → 4 distinct-lens
  seats). It reuses the registry PATH probe
  (`DEFAULT_HARNESS_REGISTRY.is_available`) so detection is registration-driven.
  The bare `advisor-board`/`default` premerge board is unchanged (byte-frozen
  3-leg panel; the golden + backcompat proofs stay green). The model-id-source
  guard now also recognizes `grok-*` ids.
- **Fix fleet-sweep ORPHAN false-positive (path-contaminated stat grep).** `sweep_fleet_worktrees.sh`'s ENOENT-vs-EACCES discriminator grepped the raw `stat` stderr, which interpolates the path — so a gitdir path literally containing "not found"/"no such file" (or a CRLF `.git` file) misclassified an inaccessible-but-present worktree as ORPHAN, deleting recoverable work under opt-in `--prune`. Strip the path from the stderr before matching + tolerate CRLF. Found by a Grok 4.5 adversarial review that the codex+claude PCR missed.
- **Live prune-on-merge trigger + fleet-wide detection backstop.** Two hardening
  items that shorten the window merged worktrees linger and add a cross-repo
  safety net, both reusing the ironclad safety of `prune_merged_worktrees.sh`
  (skip every owning repo's primary + the current tree; base-confined,
  permission-only `sudo -n rm -rf`; MERGED+CLEAN criterion).
  - *Live trigger (P3):* the release-train coordinator (`train_runner.run_train`)
    now fires a best-effort `post_merge_hook` the moment each node's PR merges —
    the genuine merge-observe point — instead of waiting for the next
    `execute-phase` closeout sweep. The live default delegates to the guarded
    `prune_merged_worktrees.sh` with `cwd` = the node's workspace, so `git worktree
    list` there enumerates that repo's siblings and the just-merged branch's
    worktree is swept by the proven criterion. The hook never prunes the node's
    own per-repo checkout (that is not a disposable worktree), and — because merges
    are forward-only — any hook error is logged and swallowed so a prune failure
    can never fail the train. The `run-train` SKILLs are intentionally unchanged:
    they are thin bridges with no per-node merge event to hook, and the merge
    happens inside the coordinator.
  - *Fleet detection (P2):* new `phase-loop-runtime/scripts/sweep_fleet_worktrees.sh`
    periodically sweeps ALL worktrees under `${PHASE_LOOP_WORKTREES_BASE:-/mnt/workspace/worktrees}`
    across every owning repo (not just the one the current run is in). It prunes
    only MERGED+CLEAN worktrees or genuinely ORPHANED dirs (owning gitdir absent —
    not merely unreachable), resolves and excludes each candidate's owning-repo
    primary, defaults to `--dry-run` (opt-in `--prune`), and offers
    `--alert-threshold N` (exit 2 when N+ prunable dirs accumulate) for scheduled
    alerting. No cron is installed by this change; the script header, this entry,
    and the PR document a recommended daily dry-run cron/systemd-timer line for the
    fleet owner to schedule. Self-tests (`test_sweep_fleet_worktrees_script.py`,
    `TestPostMergeHook`) prove the fleet sweep never selects a primary checkout of
    any owning repo, never removes outside the approved base, alerts at threshold,
    and that the live hook fires per merged node and never fails the train.
- **Multi-repo issue/PR reference convention.** Adds a root `AGENTS.md`
  (imported by a root `CLAUDE.md`) documenting that issue/PR numbers must be
  qualified with their repo (`agent-harness#130` or
  `ViperJuice/agent-harness#130`), never a bare `#130`, since the fleet is
  multi-repo. The same convention is folded into the shipped phase-loop execute
  skills (`*-execute-phase` and `*-execute-detailed` across all four harnesses)
  at their report/handoff/PR-body points, and the neutral bundle
  (`phase-loop-skills/` + packaged `skills_bundle/`) was regenerated to match.
- **Closeout standing rule: prune lane worktree after merge (permission-aware).**
  The `claude`/`codex`/`gemini`/`opencode` `execute-phase` and `execute-detailed`
  skill closeouts now codify a standing worktree-lifecycle rule: prune a lane's git
  worktree and delete its now-dead branch as soon as its PR merges — never leave
  merged or abandoned worktrees behind. The rule is a MERGED+CLEAN *sweep* (MERGED =
  ancestor of `origin/main` OR `gh` reports the PR merged; CLEAN =
  `git status --porcelain` empty), so the current run's own unmerged worktree is
  KEPT and unmerged/dirty peer worktrees are left intact. Documents the
  permission-locked gotcha — worktrees whose `node_modules`/build output was
  installed under a different uid (CI-offload / rootless-docker) reject
  `git worktree remove --force` and `rm -rf`, requiring a `sudo rm -rf` (or same-uid)
  fallback. Adds an idempotent `execute-phase/scripts/prune_merged_worktrees.sh`
  helper that encodes the criterion and the fallback. The helper hardens the
  `sudo rm -rf` fallback with three independent guards so a linked-worktree
  invocation can never destroy the primary checkout: (a) the PRIMARY worktree
  (first `git worktree list --porcelain` record) and the current worktree are
  always excluded; (b) the `sudo` fallback is confined to paths strictly under the
  approved worktrees base (`$PHASE_LOOP_WORKTREES_BASE`, else the parent of the
  current worktree, with a trailing-slash prefix boundary); (c) it escalates to
  `sudo -n rm -rf` ONLY on a genuine permission-denied git error — a "main working
  tree"/locked/other failure skips with a warning. A script self-test proves the
  primary is never selected when the sweep runs from a linked worktree. Closes the
  gap that let sibling worktrees accumulate without bound under the shared
  workspace volume.
- **CI guard against hardcoded model IDs (`model-id-source` convention).** Adds
  `scripts/check_model_id_sources.py`, wired into CI, enforcing "reference the
  central constant, don't inline" for concrete model IDs in
  `src/phase_loop_runtime/**`. A quoted model-ID literal fails the build unless
  the file is a sanctioned *registry* file (the advisor-board matrix/presets/
  fixtures + `profiles.py`) or the line carries a `# model-id-source: <reason>`
  marker. Catches the scatter/duplication class that let a stale `"gpt-5.5"`
  fallback linger in `panel_invoker.py`; a self-test proves the guard flags a
  planted violation, not just that it passes.

- **CERT / SCHEMA tier in the shared conformance library (#153).** Adds
  `phase_loop_runtime.conformance.validate_certificate(cert) -> dict` — the rung
  above `hash-checked`. It structurally validates a *declared* parity
  certificate against the contract-distributed `certificate` schema
  (`consiliency_contract.load_schema('certificate')`, from contract 0.6.4+):
  required fields present, and the `result_state` `$ref` closure enforced
  (`overall_result_state` / `dimension_results[].result_state` must be real
  contract result-state enum members). Loaded via the SAME `consiliency_contract`
  loader the SHAPE gates use, so it is versioned with the contract and
  byte-identical between the actor-side self-check (a mock, never authoritative)
  and the fence (the real mount). It returns the SHAPE-gate verdict shape
  (`{"status", "findings", ...}`) and is additive — it does not change any
  existing shape-tier gate behavior. Contract-absent or a contract too old to
  distribute the cert schema degrades to a neutral `skipped` verdict
  (`consent: false`, mirroring the `gate_posture.available()` pattern), never a
  crash; a companion `certificate_schema_available()` predicate exposes the
  degrade signal. STRUCTURAL only — it does NOT verify the certificate digest
  byte-value, any signature, or canon/provenance; those authority / provenance /
  signing rungs stay downstream in gp.

## [0.5.0] — 2026-07-09

- **Outside-agent conformance runtime (OARELEASE).** Adds the metadata-only outside-agent
  release handoff with `phase-loop-runtime` package identity, validator version,
  `consiliency-spec` contract pin, vector manifest hash, focused release-check
  evidence, temp wheel/sdist inventory, governed-pipeline pinning instructions,
  advisory preflight usage, and maintainer-owned publish/tag/workflow-dispatch
  boundaries. The runtime can be published as the `0.5.0` validator release, but
  downstream production merge enforcement remains governed-pipeline-owned.

## [0.4.0] — 2026-07-09

- **PSCAT-PL — `protected_source_category` sourced from the contract SoT (#155).**
  phase-loop no longer hardcodes the accepted protected-source vocabulary. The
  coarse category enum is now read from the distributed contract registry
  `protected_source_categories` (`consiliency-contract` pin moves
  `>=0.6.3,<0.7` → `>=0.6.5,<0.7`) via the same `consiliency_contract.load_registry`
  loader the git-discipline / consiliency gates use — so the runtime stays in
  lockstep with the SoT. Registry-present is authoritative (the seven coarse
  buckets: the six pre-existing + the new `governance_contracts`); when an older
  contract lacks the registry the runtime degrades to the legacy six-tuple
  (`protected_source_category_registry_available()` False) rather than hard-crashing
  at import, mirroring the `gate_posture.available()` pattern. `PipelineProtectedSource`
  now accepts an optional free-form producer `subtype` (never enum-gated — the
  contract deliberately keeps the fine vocabulary un-coupled across repos; the
  registry's `fine_subtypes` is a soft reference set only). `malformed_source_bundle`
  / `malformed_closeout` no longer fire for a category that is valid per the
  contract coarse enum, including `governance_contracts`.
- **Advisor panel `context_refs` maturity (#114–#118).** The `context_refs`
  path now keeps referenced artifacts out-of-line, carries stable metadata and
  hashes, preserves bounded per-leg timeouts, retries the Gemini leg correctly,
  and has release-gate proof for focused regressions, standalone tests, skill
  parity, clean-room install, and worktree hygiene.
- **Claude Code native-agent review leg (#120, fixes #92).** The advisor-panel
  Claude leg now defers to a native Agent under Claude Code instead of spawning
  a headless TUI, preserving subscription-auth posture and avoiding the prior
  stuck/headless execution mode.
- **Shared conformance library (#121).** Adds the named public
  `phase_loop_runtime.conformance` API so runtime and companion checks share one
  conformance implementation instead of duplicating role-specific helpers.

## [0.3.0] — 2026-07-07

The **spec-integration** release: the harness now consumes the published
`consiliency-contract` and enforces its shared governance, retractably.

- **`git_discipline` guardrail ACTIVATED.** The contract pin moves
  `>=0.3.0,<0.5.0` → `>=0.6.3,<0.7` (contract published to PyPI + npm at 0.6.3).
  This supplies the `pipeline_ref_classes` registry + `git_discipline_protocol`
  schema, so the git-discipline guardrail — previously a latent no-op under the
  0.3.0-resolving ceiling — is now live (warn-default; `human_required` never set).
- **HGATE — a 6th `spec_conformance` governed gate.** Warns when a declared
  spec-projection (`proj-S`/`proj-code`) sits below the conformance bar
  (`hash-checked`+, set by an advisory panel). Structural (no canon dependency),
  warn-default, no-op when no spec-projections are declared, and contract-sanctioned
  L0 stubs are exempt. Forward-infrastructure: dormant on schema-valid manifests
  until the per-archetype conformance ratchet + the manifest maturity enum expand.
- **Retractable-teeth posture dial.** Gate enforcement is now a per-finding-class
  posture (`observe` < `warn` < `enforce`) resolved from the merge-gated
  `gate_posture_registry` in the contract, with **non-retractable floors**: a repo
  raises any class freely via `.consiliency/manifest.json` `gate_posture_overrides`
  and lowers only to each class's floor. `never_delete_human_refs` is enforce-floored
  (forward-declared — no emitter yet), `write_footprint_violation`/`hash_drift` are
  warn-floored; advisories retract to `observe`. Any downward move emits the
  non-retractable `posture_retracted` note (de-fanging is fleet-visible). The
  operator's `PHASE_LOOP_CONSILIENCY_GATES=hard` remains the master block switch;
  `human_required` is never set. Backward-compatible: on a contract `< 0.6.2` every
  gate falls back to its prior behavior. The three previously-hardcoded per-gate
  postures (`version_skew` cap, git-discipline notes, HGATE loud/info) are migrated
  into the registry.

## [0.2.1] — 2026-07-06

- **Advisor Board purpose-derived default mode + advisory prompt hygiene.** A
  board's `purpose` now selects its default panel **mode** automatically, so a
  domain board — especially the legal boards (`legal-review`,
  `legal-strategy-review`, `legal-brainstorm`) — runs `advisory` **analysis**
  instead of being hard code-review-gated (which rejected a legal artifact for
  lacking an AGREE/DISAGREE verdict — correct behavior for the wrong mode).
  `invoke_board(mode=…)` now defaults to `None` and derives
  `_mode_for_purpose(board.purpose)`: code-review-class purposes
  (`code-review` / `premerge-review`) → `review` (strict pre-merge gate,
  verdict required); the known domain purposes → `advisory`; an **unknown**
  purpose → `review` (back-compat safe default). A caller-passed `mode` still
  overrides, and `invoke_panel` keeps its legacy `mode="review"` default. The
  advisory leg prompt also drops the code-review-gate framing (no "authoritative",
  no "untrusted material under review", no accept/reject) while keeping the
  injection-safe instructions/material separation. `DEFAULT_BOARD.purpose` is
  `premerge-review` → derives `review`, so the review path — mode derivation AND
  prompt — is **byte-for-byte unchanged** and the golden byte-identity proof still
  holds.
- **Advisor Board ingestion modes (`artifact`, `artifact_ref` / `brief_ref`,
  `context_refs`).** `invoke_panel` / `invoke_board` / `invoke_panel_request` now
  distinguish three caller choices. `artifact` is small inline text. `artifact_ref`
  (a path or ordered list of paths) and `brief_ref` (a path) are read-file-and-stage
  conveniences: the runtime reads local files and stages their bytes into
  `review-bundle.md` / `review-instructions.md`, so a caller no longer has to inline
  a 20k+ token bundle into its own context to invoke a board. `context_refs` is the
  true by-reference path/metadata mode: the runtime stages pathnames, hashes, size,
  MIME/extension hints, and optional PDF page-count metadata instead of file bytes,
  then instructs each leg to intentionally inspect the referenced local file with its
  own tools. Pathnames and hashes can disclose sensitive metadata, and a leg may
  disclose file contents after inspection unless an output policy forbids disclosure;
  remote providers, sandboxed backings, or service-backed harnesses may not share the
  caller host's local file access. A missing `artifact_ref` / `brief_ref` path fails
  **closed** (`ValueError` naming the path, never a silent-empty review); hard
  `context_refs` also fail closed unless `context_refs_soft_warn=True` records a
  manifest warning. An oversized **inline** artifact (> 16 KB) logs a one-line
  steering warning pointing to `artifact_ref` — **warn, never refuse, never mutate**.
  `artifact: str` back-compat is untouched (no ref ⇒ identical staged bytes ⇒
  identical per-leg argv / env / timeout — the golden byte-identity proof still
  holds). Adds a best-effort, age-gated GC of crash-residual `pl-panel-*` scratch
  dirs that can never affect a run, and rewrites the `advisor-board` skill to lead
  with the three ingestion modes.

## [0.2.0] — 2026-07-06

_First public PyPI release of `phase-loop-runtime`. Headline: the complete
model-first **Advisor Board** — customizable, multi-harness review boards that run
**parallel by default** with frontier models (Fable, not the implementer, on the
review path) — plus everything merged on `main` since v0.1.13. The package installs
standalone from public PyPI (all dependencies, incl. `consiliency-contract`, are
public); the Consiliency governance layer is opt-in._

- **Advisor Board default board library — Fable review path, parallel-by-default
  panels, legal + general/solo boards (#103).** The review-class presets (`default`,
  `code-review`) now seat **Fable** (`claude-fable-5`), never the mid-tier
  implementer, and `panel_invoker.DEFAULT_LEG_MODELS["claude"]` is the single source
  of truth so the **live governed gates** (`governed_review` / `governed_premerge`)
  also review on Fable — `CLAUDE_IMPLEMENTER_MODEL` is left untouched (the implementer
  stays Sonnet; the review model is decoupled from it). `invoke_board` / `invoke_panel`
  now run their legs **CONCURRENTLY by default** through a bounded `ThreadPoolExecutor`
  (wall-clock ≈ slowest leg, not the sum); `max_concurrency` is the knob — `None`
  (default) = parallel, `1` = sequential opt-in, `N` = cap under a hard 8-worker
  ceiling — threaded through the governed gates so a governed caller can request
  sequential. Result order is preserved regardless of finish order, and the golden
  proof still holds (default board byte-identical to `invoke_panel`, now on the Fable
  panel). Adds three **legal** boards (`legal-review`, `legal-strategy-review`,
  `legal-brainstorm`) and two domain-agnostic **catch-alls** (`general` — a top-tier
  cross-vendor panel for any task; `solo` — one top-end member), for **9 built-in
  presets** total. `brainstorm` / `doc-edit` deliberately keep Sonnet, where a diverse
  / low-stakes voice is the right tool.
- **Advisor Board end-to-end verification + capabilities card (Phase 7 ABDVERIFY).**
  The back-compat GOLDEN proof (the `default` board reproduces today's `invoke_panel`
  byte-for-byte — the release keystone protecting every existing caller), an
  end-to-end integration matrix (registries × resolver × backing; the fail-closed
  matrix; presets smoke), the per-harness **capabilities card**
  (`docs/advisor-board-capabilities-card.md`, model×harness matrix + presets + how to
  add a board), and an install-time verification that the `/<harness>-advisor-panel`
  alias resolves to the renamed `advisor-board` skill (and prunes a stale drifted
  install). See `specs/phase-plans-v5.md` (Phase 7 ABDVERIFY).
- **Advisor Board observability forwarding (Phase 6 ABDOBS).** A natively-launched
  board can, opt-in, **emit** its runtime events as the frozen `AdvisorBoardEvent`
  envelope (`advisor_board/events.py`) to a `sink` — **async / best-effort**, so a
  forwarding failure can never delay or fail a leg (the emit is a separate pass over
  results, off the spawn path). The envelope maps to omniagent-plus's own
  append-only **state-ledger** via a `LedgerWriter` seam (a clean cross-language
  integration point, since the ledger is TS and the emit is Python); no ledger
  internals are reimplemented. Per-workload boundary documented + enforced: the board
  is native + optional forward, and the native host leg is **observed, never
  relaunched** through a gateway. `sink=None` (default) builds no envelope, so the
  default board stays byte-neutral. See `specs/phase-plans-v5.md` (Phase 6 ABDOBS).
- **Advisor Board Omnigent backing (Phase 5 ABDOMNI).** Adds the `omnigent`
  provider transport so opt-in breadth seats route through omniagent-plus →
  Omnigent **v0.4.0** over the frozen HTTP surface, opt-in and fail-closed — the
  sibling of ABDHOME's `homebrew` backing, never a hand-written per-harness adapter.
  Implemented **on the shared provider seam**: `agent_runtime_provider.py` gains
  `OmnigentAgentRuntimeProvider` — an `AgentRuntimeProvider` sibling of
  `HomebrewAgentRuntimeProvider` (filling the `["omnigent"]` runtime the module
  reserves), ported method-for-method from omniagent-plus `http-provider.ts` over the
  frozen v0.4.0 surface. `advisor_board/backing_omnigent.py` supplies the transport
  (`OmnigentHttpClient`, stdlib-only) and the `OmnigentBacking` adapter
  (`from_env`/`from_config` factories) whose `run_seat` drives the seam provider
  (`create_session`→`send_turn`→`get_session_info`→`read_history`→`close_session`),
  exactly as the homebrew path drives its provider. `panel_invoker.invoke_board` gains an
  additive `omnigent=None` param and a tri-state `gateway_available` (`None` probes
  the wired backing): an `omnigent` seat routes iff a backing is wired AND the live
  `GET /v1/harnesses` catalog reports its harness; with no backing it stays the
  ABDHOME no-provider skip ("not served by homebrew (ABDOMNI)"), so the default
  board + built-3 stay byte-neutral. Opt-in opencode/pi seats route through
  Omnigent as the primary lane; cursor/amp route ONLY when the live catalog exposes
  them (a dynamic per-seat gate, no code gate). Two DISTINCT fail-closed skips —
  gateway-unreachable (via `select_backing`) and harness-not-in-catalog — each with
  its own reason; the native-host-leg invariant is preserved (a host-leg `omnigent`
  seat is still a hard raise even with a backing wired). Auth reuses the frozen
  no-silent-key contract: the SAME `resolve_seat_env` scrub that governs the
  homebrew subprocess env governs the wire, so a subscription seat transmits ZERO
  vendor-key material and an api-key opt-in seat transmits EXACTLY the seat vendor's
  key; the gateway DERIVES and REPORTS the resolved auth lane
  (`session.metadata.auth_lane`), read back by the backing, so no-silent-key is
  TESTABLE on the recorded request — not asserted. HTTP rejections map through the
  omniagent-plus `failure-mapper` categories (429→rate_limit, 403 auth/billing/
  policy) to a DEGRADED seat that never blocks the board. "We did not fork the
  transport" is a checked invariant: the frozen v0.4.0 `http-surface.json` +
  `source-metadata.json` are vendored (`tests/fixtures/omnigent_contract/`, from
  omniagent-plus `744420d`) and a conformance test asserts every endpoint the
  client issues appears in that surface and the freeze target is `0.4.0`. See
  `specs/phase-plans-v5.md` (Phase 5 ABDOMNI).
- **Advisor Board registries + matrix + presets (Phase 2 ABDREG).** Populates the
  real data behind the frozen ABDFREEZE interfaces (no frozen type/stub changed —
  the stubs still raise; `panel_invoker` untouched). Adds: the six-harness
  `DefaultHarnessRegistry` (claude/codex/gemini built-3 `homebrew`;
  opencode/pi/cursor breadth `omnigent`, cursor's availability gated on the
  `cursor-agent` binary) and the `DefaultModelRegistry` (model → default
  subscription lane pinned to a built-3 leg, `runnable_by` derived from the frozen
  `schema.vendor_family` projection, effort ceiling) in `registries.py`; the
  populated `(model × harness)` compatibility + per-lane auth-availability matrix
  behind the frozen `is_valid(model, harness) -> (bool, AuthAvailability)` /
  `default_lane(model)` API, plus config-time `validate_seat` / `validate_board`
  that reject an invalid pairing (e.g. `claude:gpt-5.5`) or an over-ceiling effort
  with an actionable message (`matrix.py`); the four board presets `default`
  (== the shared `DEFAULT_BOARD` fixture, so today's 3 seats reconstruct by
  construction), `code-review`, `brainstorm`, `doc-edit` (`presets.py`); and the
  user-editable config loader for `~/.config/agent-harness/advisor-boards.toml`
  (`config.py`) — `allow_api_key_fallback` defaults false, unknown top-level/board/
  seat keys are a hard error (never a silent drop), and every board (presets
  included) is matrix-validated at load. New dep: `tomli` on Python 3.10 (stdlib
  `tomllib` on 3.11+). See `specs/phase-plans-v5.md` (Phase 2 ABDREG).
- **Advisor Board resolution + rename (Phase 3 ABDRESOLVE).** Board resolution,
  seat validation, and the `advisor-panel` → `advisor-board` skill rename, coded
  against the frozen ABDFREEZE interfaces + shared canonical fixtures.
  `advisor_board/resolver.py`: `BoardResolver` turns a board name into seats,
  honors a settable default board, and parses ad-hoc `--seats model:effort[:harness]`;
  `advisor-panel` resolves as a board-name alias of the default board.
  `advisor_board/validation.py`: `validate_seat`/`validate_board` fail fast against
  the frozen `CompatibilityMatrix.is_valid` with actionable "did-you-mean"
  diagnostics. `advisor_board/standin.py`: fixture-backed stand-in registries/matrix
  (until ABDREG's real data). The `advisor-panel` skill is renamed `advisor-board`
  across all harness prefixes (`skills-src/`, `phase-loop-skills/`, packaged
  `skills_bundle/`, `REQUIRED_SKILLS`, `CANONICAL_WORKFLOW_SKILLS`, the SKILLPACK
  matrix); `advisor-panel` stays a working alias (`SKILL_ALIASES` +
  `canonical_skill_name`), and the stray prefixed `<harness>-advisor-panel` install
  is no longer produced. Result identity is re-keyed leg→seat: `PanelLegResult`
  gains an additive `seat_key` (defaults to `leg`, so the default board is
  byte-equivalent) so a board with two same-vendor seats is expressible, and the
  skill-documented `PanelRequest` is reconciled as a real entry point via
  `invoke_panel_request` (`invoke_panel`'s signature is unchanged — the ABDFREEZE-4
  back-compat anchor). See `specs/phase-plans-v5.md`.
- **Advisor Board homebrew backing through the seam (Phase 4 ABDHOME).** Board
  seats now run through the provider seam with per-seat `homebrew` backing:
  `panel_invoker.invoke_board(board, artifact, …)` routes each seat via
  `select_backing`, renders `seat.effort` to each built-3 CLI through the frozen
  `render_seat_invocation` mapping (incl. the agy leg, where effort is baked into
  the model-name string — previously hard-coded), and launches with an ACTIVELY
  scrubbed subprocess env via `resolve_seat_env` (a subscription/default seat
  scrubs every vendor API-key var; an api-key seat injects ONLY its own vendor's
  key, and only behind the board opt-in — never silent). The built-3 (claude
  native-host / Agent-View TUI off-host, codex, gemini) are behavior-neutral
  behind the seam: the `default` board renders each leg to today's exact argv +
  env. `enforce_native_host_leg` encodes the native-host-leg invariant — the host
  leg is never routed through a gateway (a host-leg `omnigent` seat is a hard
  raise, distinct from the ordinary omnigent/breadth skip-with-warning). Breadth
  harnesses (opencode/pi/cursor) get NO hand-written homebrew adapters — they are
  Omnigent-or-skip (ABDOMNI); an unavailable lane degrades skip-with-warning
  without blocking the board. The governed reviewer≠author disjointness is rewired
  onto the ONE frozen `advisor_board.schema` vendor projection (not a copy):
  `governed_review.select_reviewer_pool` now projects each available leg through
  `vendor_of_harness` before exclusion, so a same-vendor breadth lane (e.g. an
  `opencode` reviewer over a `codex`-authored artifact) is correctly excluded for
  custom/model-first boards while the built-3 panel stays byte-neutral. Effort/env
  default to today's behavior on the legacy `invoke_panel` path (its frozen
  signature is unchanged). See `specs/phase-plans-v5.md` (ABDHOME).
- **Advisor Board contract freeze (Phase 1 ABDFREEZE).** New additive,
  behavior-neutral `phase_loop_runtime.advisor_board` package freezing the
  model-first Advisor Board interfaces the parallel fan-out (ABDREG / ABDRESOLVE
  / ABDHOME) builds against — no change to the running `panel_invoker` path (the
  `default` board reproduces today's 3-leg behavior byte-for-byte). Freezes:
  `Seat{model, effort, harness?, lens?, auth?, backing?, host_leg?}` + `Board`
  schema and the `~/.config/agent-harness/advisor-boards.toml` config format
  (`schema.py`, `fixtures/advisor-boards.example.toml`); the per-harness
  model/effort mapping incl. the agy leg's effort-in-model-name special case
  (`harness_mapping.py`); the seat→vendor-family projection that keeps the
  governed reviewer≠author disjointness intact under model-first (byte-consistent
  with `governed_review`); host-leg identity; registry + `(model × harness)`
  compatibility/auth-availability matrix interfaces with importable stubs
  (`registries.py`); the provider-backing selector + active-env-scrubbing
  no-silent-key auth contract (`backing.py`); the internal advisor-board event
  envelope + best-effort forwarding (`events.py`); and shared canonical fixtures
  (`fixtures.py`). Contracts documented in `advisor_board/CONTRACTS.md`.
  Produces IF-0-ABDFREEZE-1..5. See `specs/phase-plans-v5.md`.
- **CI: publish `phase-loop-runtime` to PyPI on tag.** New
  `.github/workflows/publish-pypi.yml`, tag- and `workflow_dispatch`-triggered,
  builds sdist+wheel and publishes via PyPI Trusted Publishing (OIDC) — no
  token stored in the repo. Verifies the pushed tag matches
  `phase-loop-runtime`'s `pyproject.toml` version before building. Also flags
  in the README and `pyproject.toml` that the PyPI dist name is
  `phase-loop-runtime`, not `agent-harness` (an unrelated third-party PyPI
  project). One-time PyPI setup required before the first tag publish: claim
  project `phase-loop-runtime` and add a Trusted Publisher (owner
  `ViperJuice`, repo `agent-harness`, workflow `publish-pypi.yml`, environment
  `pypi`).

- **Self-ingest: agent-harness adopts its own `.consiliency/`.** First governed repo — archetype `tooling-meta` + `public` modifier, adopted scope `[layout, gates]`, vendoring `@consiliency/contract` 0.3.0. Additive (`.phase-loop/` untouched); all L0 gates pass (presence/local-integrity/layout/version-skew). Docs are L0 presence-stubs to be filled at L1.

- Bumped the vendored `consiliency-contract` pin from `>=0.2.1,<0.3.0` to
  `>=0.3.0,<0.4.0`. 0.3.0 rebalances the required-documents registry: the
  baseline shrinks from ~13 governed docs down to 6 universal ones (`readme`,
  `doc-contract-index`, `contract-version-status`, `glossary`,
  `interface-declaration`, `codeowners`); `service-catalog-ownership`, `sbom`,
  `adr-index`, `dev-setup`, and `changelog` move onto the code archetypes
  (`product`/`service`/`library`/`infra`/`tooling-meta`) that actually need
  them; and `contributing`/`license` move onto the (opt-in) `public` modifier.
  No `phase_loop_runtime` code changes — the scaffolder/gates/ingest modules
  already compose the required-document set purely from the vendored
  registry at runtime — but the consiliency test suite's fixtures/assertions
  are updated to the new baseline: scaffolding a bare archetype (no `public`
  modifier) no longer has a LICENSE gap to exercise, so
  `test_consiliency_gates.py`'s `_scaffolded_repo` and
  `test_consiliency_scaffold.py`'s fabrication test now request the `public`
  modifier explicitly to keep covering a real `l0_stub_allowed: false` gap,
  and `test_consiliency_ingest.py`'s second-verify-pass test now asserts a
  genuinely clean (`passed`) gate scan instead of the LICENSE-driven `warn`
  it exercised under the old, larger baseline.

## v0.1.13

- **CS-0.10a — `phase-loop worktree-index` freshness pointer.** New read-only,
  purely git-derived command that answers "where is the freshest working copy
  of a path, and who's touching it": enumerates active worktrees (`git
  worktree list --porcelain`), diffs each worktree's branch against
  `origin/<default-branch>` (falling back to `origin/main`), and reports the
  holders (worktree path, branch, last commit) for a queried path — or every
  touched path when none is given — plus whether `origin/main` is behind on
  it. No new persistent state; a repo with no divergent worktrees answers
  `origin/main`. `phase_loop_runtime.worktree_index` is the module; never
  writes repo state.
- **CS-0.7 — realized-edge / fleet-map v0 extractor.** Between the core
  Consiliency-standardization repos there are ~zero package-level deps, so a
  package-lockfile scan renders the real cross-repo interface graph
  invisible; the actual edges are git+ref pins, copied-literal (vendored)
  contract/schema drift, and hard-coded host-path refs in source. New
  `phase-loop fleet-map --repo <path> [--repo <path> ...] [--json]` (module
  `phase_loop_runtime.fleet_map`) statically extracts those three edge kinds
  across a set of repo paths and emits an interface-graph artifact — each
  edge `{from_repo, to_repo, kind, evidence, maturity_label}` — alongside a
  package-lockfile-only baseline for comparison (typically empty even over
  repos with ordinary, unrelated third-party manifest deps). v0: no network
  calls or git remote resolution, static file inspection only.
- **CS-0.10c — local-file `LeaseStore` + soft leases.** New
  `phase_loop_runtime.lease_store`: a local-file backend for the CS-0.10b
  `LeaseStore` contract (`consiliency_contract`'s `lease.schema.json` /
  `lease-event.schema.json` / `lease-store-protocol.schema.json`) so parallel
  local agents can claim path-sets without stepping on each other. SOFT MODE
  ONLY — this backend never declares an atomic backend, so a requested hard
  lease always degrades to soft (no cross-machine atomic acquire locally;
  that needs the off-device backend, CS-0.10d). TTL + heartbeat +
  auto-expiry: an unrenewed lease past `heartbeat_at + ttl_seconds`
  (exclusive boundary) is free, so a dead holder can never freeze a path.
  Give-way policy is REROUTE, not block: an `acquire()` colliding with an
  active lease (same `lease_id`, or an overlapping path-set scope held by
  someone else) returns the blocking lease instead of raising or waiting.
  The current-lease view (`query()`) is a pure projection of the append-only
  `.consiliency/leases/events.jsonl` event log ONLY — the module has no
  parameter a coordination-channel message could occupy, so the sole-truth
  guardrail (the inbox is never authoritative for lease state) is structural,
  not a runtime check. New `phase-loop consiliency-lease
  acquire|renew|release|query --repo <path> ...` CLI. Vector-tested against
  every `lease-*`/`coordination-*` conformance vector the vendored
  `consiliency_contract` (>=0.2.0) ships.
- **CS-0.8 — agent-runtime provider seam.** New `phase_loop_runtime.agent_runtime_provider`:
  an `AgentRuntimeProvider` Protocol (matching omniagent-plus core-contracts) +
  `HomebrewAgentRuntimeProvider` degraded profile — a one-shot CLI spawn presented as a
  single-turn, buffered-replay session, `cancel_turn` = process kill, unsupported
  capabilities declared via `health()`. The panel spawn path routes through the seam
  (behavior unchanged; the existing panel tests are the regression guard). Lets a future
  Omnigent-backed provider drop in without a caller change.
- **CS-0.11 — brownfield ingestion (shape-to-conform, then verify).** New
  `phase-loop consiliency-ingest --repo <path> [--adopt]`: first pass (no manifest) shapes a
  compliant `.consiliency/` via the CS-0.5 scaffolder + a CS-0.12 adoption profile + a
  conservative governed-set proposal (consent-gated on `--adopt`; unflagged repos untouched;
  scratch/other-harness namespaces never claimed); every subsequent pass verifies via the
  CS-0.6 L0 gates + `evaluate_governance_scope` (governed / foreign / present-nonconforming
  labels), never rewriting. Detection = manifest presence. The repo-library on-ramp.

- **CS-0.5 — `.consiliency/` scaffolder (first-writer).** New
  `phase-loop consiliency-scaffold --repo <path> --archetype <name>` (repeatable
  `--archetype`/`--modifier`, or `--baseline-only`) writes a schema-valid
  `.consiliency/` layout: `manifest.json` (declaring archetype(s)/modifier(s) +
  the composed governed-doc allowlist), `status.json` (contract-version-status)
  and `interfaces.json` (interface declaration) as real minimal artifacts, and
  L0 presence-stub docs — each an honest "unauthored, tracked" marker with an
  explicit authored zone, never a fabricated projection — for every doc the
  vendored `consiliency_contract` required-documents registry demands.
  Additive/first-writer: never touches `.phase-loop/`/`.pipeline/`, never
  overwrites a file that already exists (re-running is a safe no-op), and
  docs marked `l0_stub_allowed: false` (`readme`, `license`, `document-index`)
  are never fabricated — referenced if present, otherwise just declared for
  the presence gate to flag. `phase_loop_runtime.consiliency_scaffold` /
  `phase_loop_runtime.consiliency_layout` are the modules.
- **CS-0.6 — `.consiliency/` L0 gates.** Four gates — `presence`,
  `local-integrity` (git-scoped hash snapshot; a forward-compatible no-op
  today since Phase 0 floors every doc at `presence-only`), `layout-validity`
  (manifest/status/interfaces validate against the vendored schemas), and
  `version-skew` (repo `contract_version` vs the installed
  `consiliency-contract` package) — wired at top-of-loop (a non-blocking
  advisory notice, mirroring the existing governed-mode notice) and closeout
  (threaded into `build_phase_loop_closeout` alongside `docs_freshness`, new
  `consiliency_gates`/`consiliency_gates_detail` closeout fields). SOFT/warn
  by default; `PHASE_LOOP_CONSILIENCY_GATES=hard` opts into blocking (new
  `consiliency_gate_blocked` frozen blocker literal), never sets
  `human_required`, and the version-skew gate never blocks even under `hard`
  (Phase 0 severity is normatively `warn`). CONSENT-GATED: a repo without a
  `.consiliency/manifest` is a pure no-op. `phase_loop_runtime.consiliency_gates`
  is the module.
- Added `consiliency-contract` (the published shared Consiliency contract
  package) and `jsonschema` as runtime dependencies; all `.consiliency/`
  schemas/registries are read from the vendored package, never copied.

## v0.1.12

- **CS-0.4 release floor.** Bumped `phase-loop-runtime` to `0.1.12` for the
  Consiliency standardization release floor. No bridge-contract or behavior
  changes; `phaseLoopBridgeContract.v1` remains unchanged.
- Updated the public `install-agent-harness.sh` default ref to `v0.1.12` so
  off-tailnet installs resolve the same release by default.

## v0.1.11

- **Harness-neutral repo-validation contract.** `phase-loop repo-validate
  <target>` resolves and runs a repo's *explicit* local-first validation contract
  (`fast`/`gate`/`full`/`fix`/`affected`/`doctor`) so coding agents run the same
  checks locally, in worktrees, and in CI before opening PRs — GitHub stays the
  authoritative merge gate. Discovery is `just agent::<t>` (via `mod agent` +
  `agent.just`) then `package.json` `agent:<t>`; unmigrated repos **fail closed**
  (exit 20) rather than guessing `npm test`/`pytest`/`make test`. Frozen exit
  codes: 0 ok · 2 usage · 10 not-a-worktree · 20 no-contract · 21 runner-missing ·
  30 command-failed. The resolver (`phase_loop_runtime.repo_validation`) is
  stdlib-only with a pure `resolve()` split from `run_plan()`; Dagger is an
  optional, repo-owned posture (open-source Engine, no Dagger Cloud). Contract
  spec: `docs/repo-validation-contract.md`. Scope is the contract + neutral
  resolver + tests; per-repo checks and Dagger modules belong to consuming repos.
- **#66 — advisor-panel per-leg model override.** Each leg's model was hardcoded (`CLAUDE_IMPLEMENTER_MODEL`, `gpt-5.5`, `Gemini 3.1 Pro (High)`), so running e.g. the Claude leg on
  `claude-fable-5` required monkeypatching a module constant. `invoke_panel(..., models={"claude":
  "claude-fable-5"})` now overrides any subset per leg; unset legs use `DEFAULT_LEG_MODELS`.

- **#63 — advisor-panel advisory mode.** `panel_invoker` was hardcoded to a pre-merge
  code-review framing, so the three-model panel couldn't be used for general adversarial/advisory
  analysis (architecture, product, red-teaming a plan) — 2/3 legs replied "nothing to review".
  `invoke_panel(artifact, legs, mode="advisory")` now reuses all the leg-spawn machinery but swaps
  the framing and drops the AGREE/DISAGREE requirement (substantial prose is a real leg);
  `mode="review"` stays the default (back-compat, byte-identical behavior).

- **#64 — advisor-panel leg auth preflight + soft-empty-turn retry.** A logged-out CLI made the
  codex leg fail obliquely (an `rc=0` empty-turn, then rate-limit errors), so the panel silently
  degraded and the failure was misdiagnosed. `_exec_leg` now runs a cheap auth preflight
  (`codex login status`) before the expensive leg — a de-authed leg fails fast and classifies
  `DEGRADED` (never a silent empty leg) — and retries a transient soft empty-turn (`rc=0` + empty
  output) once, while never retrying a hard failure (`rc!=0`).

- **#48 — Advisor-panel Claude TUI leg no longer hangs on child exit.** The TUI read loop now
  treats PTY EOF (`os.read` → empty) as terminal: when the child CLI and its descendants close
  the pty, the leg returns a structured result (verdict if one landed, else an `ERROR`-classified
  `claude_tui_pty_eof_no_output`) instead of busy-spinning to the input-scaled (up to 30-min)
  deadline. Previously a lingering wrapper parent kept `proc.poll()` from firing while the EOF fd
  stayed "readable", hanging the panel indefinitely. On EOF the canonical review **file** is the
  only OK path; a transcript-scraped verdict is salvage evidence only (non-zero rc, fail-closed) —
  never promoted to OK, matching the sibling exit paths.
- **#52 — Actionable lane-IR closeout refusals.** When closeout/status fails closed on an
  unresolved Lane IR diagnostic, the `blocker_summary` now names the concrete diagnostic
  (`kind@lane` + message) and the phase-plan file location, instead of the opaque "Lane IR
  diagnostics failed closed for the current phase plan". The operator can repair the exact
  lane/contract (e.g. `missing_producer_dependency@SL-4: SL-4 consumes … without depending on
  it`) without guessing. The fail-closed contract is unchanged — diagnostics still block; they
  are just legible now.
- **#49 — Codex effort `max` → CLI `xhigh`.** The internal `max` effort tier (codex's top
  tier, used by the max-effort planner of record) is now translated to `xhigh` at the codex CLI
  boundary (`build_codex_command`). Previously `model_reasoning_effort="max"` was emitted verbatim
  and rejected by the codex CLI ("Invalid value: 'max'"), which misclassified the phase as
  `account_or_billing_setup`. Codex remains max-*eligible* in the policy/tier layer; only the CLI
  value is clamped to codex's real ceiling.
- **#47 — order-only cross-repo train dependencies.** `**Channel:** order-only` declares a
  merge-order (freeze) dependency with no channel injection — see the authoring guide.
- **#45 — `phase-loop train-status` command.** Non-mutating inspection of the cross-repo
  train ledger (`train-status --train <file> [--ledger-dir DIR] [--json]`) — the command the
  v0.1.11 run-train docs/skills already referenced but which did not exist. Resolves the same
  default ledger path as `run-train` and prints per-node status/branch/PR/merge-order/merged-SHA
  in topo order (pending nodes surfaced). Also corrects the stale `run-train --help` (the P4
  governed review + sequential merge path is implemented behind `--governed`).
- **#36 / dotfiles #135 — Advisor panel ownership and staged review packets.**
  `agent-harness` now owns the advisor-panel runtime primitive and harness-prefixed
  skill source. Codex and Gemini receive compact prompts that point to staged
  `review-instructions.md` / `review-bundle.md` files instead of embedded artifact
  bodies; Claude uses a Claude Code TUI session with `claude-sonnet-5`, max effort,
  subscription-safe env stripping, no `claude -p`, and a canonical scratch
  `panel-claude.txt` output file. Dotfiles can now reduce its
  unprefixed `advisor-panel` skill to compatibility guidance over this packaged source.
- **#33 / #39 — Runner hardening.** Background subprocess monitoring now preserves
  salvage evidence and bounds stale `quiet_unknown` children after a grace period when
  CPU sampling is unavailable, while still avoiding cleanup for CPU-active quiet work.
  Cross-repo live reverify honors hard-mode verification enforcement.
- **#29 — Cross-repo release-train coordinator.** `phase-loop run-train --train
  <roadmap>` orchestrates multi-repo changes in a single atomic train: draft PRs
  open across all nodes in topo order (P3), a train-level governed review gates
  the full diff, then nodes merge sequentially with each downstream re-verified
  against the upstream **MERGED SHA** before its own merge (P4). Safety invariants
  are enforced structurally and asserted in the CI suite:
  - **Zero PRs on preflight failure** (train-schema validation T-A/B/C/D runs before
    any `publish_from_worktree` call).
  - **No merge before train approval** — review panel rejection is a non-human
    terminal (`terminal_blocker.human_required=False`, zero `merge_pr` calls).
  - **False-green killer** — `set_upstream_ref` is called with the upstream MERGED
    SHA (not the draft SHA) ordered before `reverify_fn`; asserted by call-log
    capture in `tests/test_train_invariants.py::TestInvariant2FalseGreenKiller`.
  - **Forward-only guard** — downstream re-verify failure halts the train at that
    node; upstream merges stay merged.
  - **Train state off `.phase-loop/`** — `append_record` raises `ValueError` on
    violation; the ledger is caller-supplied and coordinator-side only.
  - **Autonomous boundary** — `run_mode="autonomous"` stops at `drafts_open`;
    `--governed` is required to proceed to review + merge.
  - **Crash-resumable** — the JSONL ledger records `merged`/`pr_open`/`blocked`
    per node; a second run skips already-merged nodes and retries blocked ones.
  - Documented limitation: the merged downstream PR carries the draft-time upstream
    pin (not the merge-commit SHA); safe under expand/contract upstream contracts.
  - New `run-train` workflow skill (`claude-run-train`, `codex-run-train`,
    `gemini-run-train`, `opencode-run-train`) added to `REQUIRED_SKILLS` and the
    harness-skill-matrix; regenerated bundle + synced `skills_bundle/`; parity/drift
    gates green.
  - Protocol doc (`_contract_docs/phase-loop/protocol.md`) extended with: train
    ledger shape, merge-SHA-pinned cross-repo gate, six numbered invariants, the
    expand/contract limitation, and the train roadmap authoring format.
  - `README.md` extended with a "Cross-repo release train" section.

## v0.1.10

- **#28 — execute skills default to a published review surface.** `execute-detailed`
  and `execute-phase` no longer leave a verified implementation as dirty changes in the
  primary checkout with no PR. A human-invoked run now publishes a branch + PR by default
  after verification, under a hard safety invariant: never commit to `main`/a protected
  branch or from a dirty primary checkout; if not already on a clean dedicated branch,
  work in a worktree off a fresh base ref. `execute-detailed` gains a publication flow
  (runner/manifest carve-out, pre-edit preflight, scoped staged-diff audit, push-rejection
  stop, skipped-verification→draft); `execute-phase` gains a 3-state publication mode that
  **stops rather than merging lanes onto `main`/a protected branch** and defers to the
  runner (incl. governed mode) when the runner owns closeout. Merge/force-push/reset stay
  gated by explicit instruction. Source-edited in the `skills-src/` canon (harness-neutral;
  collapses to the build base) + regenerated bundle; parity/drift gates green.
  Decision-panel-reconciled. Cross-repo publication is a separate follow-up (#29).
- **Skills-cutover cleanup (#26 → #30).** Repointed the dead `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` reference to the package-relative `phase_loop_runtime/baml_src/...` (resolvable wherever the runtime installs); preserved the `Claude Opus 4.8` co-author trailer through the harness brand-substitution (added to `PRESERVE_LITERALS`); added a backstop lint flagging any unguarded `claude-*` literal in `skills-src/`; bumped the installer pin. Item 2 (install-time body skill-name re-expansion) remains a deferred follow-up.

## v0.1.9

- **Canonical in-repo skill sources + hard parity gate (#25 → #27).** `skills-src/<harness>/` is now the authored canonical source; `build_bundle.DEFAULT_SOURCES` points at it, so the bundle builds entirely from the agent-harness tree with **no dotfiles checkout**. Added `scripts/regenerate_skills_bundle.py` (the one-command regenerate) and a hard parity gate (`tests/test_skills_canon_parity.py` + `.github/workflows/skills-parity.yml`) asserting the committed `phase-loop-skills/` equals `build_bundle(skills-src/)`. The fleet `bootstrap.sh --source <dotfiles-root>` path is unchanged.

## v0.1.8

- **Fix (#18 follow-up) — F5: evidence-backed docs-freshness decision.** A
  `docs_freshness: passed` claim is now *provable* from the scan evidence rather than a
  self-attested literal: `scan_docs_freshness` no longer emits `passed` when the scan ran
  but enumerated **no** public-doc surfaces (a bare/empty detail reports `skipped` — "could
  not verify" — instead of reading as a pass), and every result carries an `evidence_backed`
  flag (a new `docs_freshness_evidence_backed()` helper: `passed` AND surfaces enumerated AND
  no blocking hit). The pre-existing `doc_delta` gate's self-attested `no_doc_delta` is now
  **corroborated** against that scan on release phases — the freshness result is threaded into
  the closeout-validator context (validators stay pure; no repo IO) and an un-corroborated
  `no_doc_delta` is downgraded to a recorded **warn** (`doc_delta_uncorroborated`), never a
  block. Ordinary phases, and any phase with no scan threaded in, are unaffected.
- **Fix (#18 follow-up) — F4: required post-dispatch evidence-reducer lane.** A
  release-dispatch phase writes evidence docs referencing a commit SHA / workflow result that
  is unknowable before the tag is cut, so a pre-dispatch reducer necessarily leaves a
  placeholder (F1's scan is the backstop that blocks the closeout if it survives). F4 makes the
  back-fill an explicit, required planning step: `validate_plan_doc.py` now **errors** on an
  explicit `phase_loop_mutation: release_dispatch` plan that omits a post-dispatch
  evidence-reducer lane (and **warns** on a non-dispatch release shape that omits it), mirroring
  the F2 explicit-release posture. Added to the plan-phase SKILL guidance and propagated to the
  four `skills_bundle/*-plan-phase` copies via `sync_skills_bundle.py`. This is a
  plan-validation rule only — no new runtime back-fill engine (the placeholder scan remains the
  enforcement).

## v0.1.7

- **Fix (#18 follow-up) — pipeline-independent `docs-audit` backstop.** v0.1.6's
  `docs_freshness` closeout gate is *path-keyed* (it fires in the closeout pipeline and
  flags stale *tokens present* in a doc) but structurally cannot catch the **silent-absence**
  case: a release surface changed (e.g. `pyproject.toml`/`VERSION` bumped) while the
  CHANGELOG simply was not updated — no token to find — or any path that bypasses closeout
  (direct-`Agent()`, absent runtime helper). This adds a **diff-driven, pipeline-independent**
  `phase-loop docs-audit --base <ref>` CLI (a new `docs_audit` module over a standalone
  `docs_surfaces` taxonomy) wired into CI (`docs-audit.yml`) on `pull_request` (blocks the
  merge), `push:main` (red-marks post-hoc — the autonomous loop pushes directly to main, so
  the **whole pushed batch** is diffed via `github.event.before..HEAD`, not just the tip), and
  `push:tags`. It enforces a per-surface, **relevance-bound** decision contract — a release
  surface must change its *required* doc (a token or an unrelated README edit does not satisfy
  it); every general public surface needs at least a recorded decision — and **fails closed**
  on any un-evaluable input (unresolved base / git-diff error) rather than passing silently.
  The shipped v0.1.6 closeout gate and `release_guard` are untouched (this is purely additive;
  a single unified taxonomy is a later, separately-tested change). Decision-panel-reconciled.

## v0.1.6

Docs-freshness closeout gate (#18) + the model-routing & governed-review work and the
#12/#14 packaging fixes that shipped under this tag (previously left under "Unreleased").

- **Fix (#18):** A phase-loop release recovery could close **green** (clean tree, pushed
  `main`, release workflow passed) while its public docs stayed stale or absent — the
  existing doc-delta gate is diff-keyed, so files that *should* have changed but didn't
  were invisible, and under the default `PHASE_LOOP_REVIEW=warn` no finding ever blocks.
  Three load-bearing fixes:
  - **F1 — docs-freshness closeout gate** (`docs_freshness.py`): a *path-keyed*
    pre-scan (runner-side; validators stay pure) enumerates public-doc surfaces from the
    filesystem and `.claude/docs-catalog.json` (**not** from `changed_paths`) and scans
    their contents for stale placeholders (`recovery commit pending`, `TBD`, …). For
    **release/package phases only** it blocks `complete` as a hard gate — modeled on the
    verification-evidence gate, governed by its own `PHASE_LOOP_DOCS_FRESHNESS`
    (`hard` default | `warn` | `off`), independent of `PHASE_LOOP_REVIEW`. The hard
    block is **opt-in via explicit release frontmatter** (`phase_loop_mutation:
    release_dispatch` or a release `phase_type`): only an explicitly-declared release
    phase can be `blocked`. A heuristic-only release shape (the artifact-glob match on
    e.g. `CHANGELOG.md`/`**/pyproject.toml` with no release frontmatter) still scans and
    records evidence, but block-severity hits are **downgraded to warn** and can never
    halt the run — so an ordinary changelog/dep bump on a feature phase is never
    fleet-halted. Ordinary phases with no artifact match are unaffected (status
    `skipped`). The closeout now always carries `docs_freshness: passed|skipped|blocked`
    + a `docs_freshness_detail` evidence record (including an `explicit_release` flag), so
    a clean worktree alone cannot imply docs are current. Fuzzy signals (stale
    package-count claims, "skeleton") are warn-tier; an inline `<!-- freshness-ok -->`
    marker suppresses a false positive.
  - **F2 — release docs-lane ownership** (`validate_plan_doc.py`): release/package phases
    must have a docs lane that **owns** `README`/`CHANGELOG`/release-notes (or records an
    explicit no-doc-change decision), and the docs reducer must **depend on every producer
    lane**. ERROR only for **explicitly-declared** release phases (frontmatter); a
    heuristic-only release shape and ordinary phases are WARN (autonomy-first preserved).
  - **F3 — widened `PUBLIC_SURFACE_GLOBS`** to cover package-level `**/README.md`,
    `CHANGELOG*`, and release-notes surfaces.
  - Deferred as follow-ups: F4 (post-dispatch evidence reducer that back-fills the
    commit SHA/workflow result not knowable before tag creation) and F5 (evidence-backed
    freshness decision literal).
- **model-routing-v2 — governed mode goes live (serial path).** The v1 governed-review
  machinery (a tested island where `run_mode` reached `run_loop` but was never used) is now
  wired into the live runner: `--governed` / `PHASE_LOOP_RUN_MODE=governed` surfaces the mode;
  a plan-stage gate reviews first-attempt plans; a pre-merge gate runs before the closeout
  commit on implementation closeouts and runs a bounded review→fix→re-review loop; the panel
  spawns the **codex + gemini** subscription CLI legs fail-closed (claude leg `unavailable`
  pending a native-Agent path); every governed terminal is a non-human `review_gate_block`
  surfaced in the run-end summary. The **autonomous default is byte-identical** — an outer
  `run_mode=="governed"` guard means it renders no bundle and spawns zero panel legs (asserted
  at the run level). The panel reviews a *review bundle* (staged diff + acceptance criteria +
  verification results + summary) staged to a file. Remaining threads (documented, not
  overclaimed): the `model_class` escalation decision is recorded on dispatch metadata but not
  yet re-routed into live model selection; concurrent-wave dispatch is not governed; the real
  CLI spawn boundary (`_exec_leg`) can't run frontier models in CI so it's stubbed in tests.
- **Governed gate hardened (advisor-panel reconciliation).** A 3-model panel (Claude Opus +
  Codex GPT-5.5 + Gemini 3.1 Pro, each verifying against the code) found the gate failing in
  both directions and prescribed a structural fix, now landed:
  - **Relocated** — the pre-merge gate now runs INSIDE `_perform_phase_closeout`, after
    `git add` and before the commit, and reviews the EXACT staged index
    (`git diff --cached`). "What the panel reviews" == "what gets committed" *by construction*,
    which dissolves the prior bundle-vs-commit divergence, the untracked-new-files omission,
    the fail-open `_is_untracked` probe, and the N+1 git subprocesses in one move (the parallel
    `governed_bundle` path discovery is deleted; the renderer no longer writes into the repo).
  - **Reviewer≠author derived correctly** — from the UNION of the dispatch events'
    `selected_executor` (the `action='run'` event shape, not a filtered `execute`/`repair`),
    excluding EVERY vendor that authored the phase (rotation/repair can have several); an
    unknown author set fails closed.
  - **Fail-closed verdicts** — a strict terminal-line contract (last line begins with
    `AGREE`/`PARTIALLY AGREE`/`DISAGREE`, tolerant of markdown bullet/blockquote/numbered
    formatting); a *substantive* review with no conforming verdict is treated as a BLOCK (not
    a non-gating warn), and no usable disjoint reviewer HOLDS the merge (`review_gate_block`)
    instead of silently passing — the prior advisory-pass-on-degraded fail-open is gone. On a
    block the staged index is reset, so a stray `git commit` can't land the rejected change.
    The autonomous default stays byte-identical (the gate is a no-op off the governed path).
  - **Governed mode is EXPERIMENTAL — known limitations (documented, fail-safe).** It may
    over-block, but never silently passes unreviewed or self-reviewed code. With only the
    **codex + gemini** legs live (the claude leg is deferred), a **multi-vendor** phase —
    authored by codex *and* repaired by gemini — has no disjoint reviewer, so it is HELD with
    an explicit reason (it cannot be independently reviewed until the claude leg lands), rather
    than promoted. The `model_class` escalation decision is still recorded-not-yet-routed, and
    the executor-driven `apply_fix` re-dispatch remains a thread; a governed block halts the
    bounded run for the operator.
- **Fix (#14):** `phase-loop sync-skills --apply` silently no-oped — when a bridge skill's
  source did not resolve it skipped the record, producing output identical to `--check`
  with exit 0. It now reports the unrepaired skills and **exits non-zero** with actionable
  remediation, so it can never falsely imply success. The repair source resolves from the
  in-wheel `skills_bundle/` (via #12), so on a pinned install `--apply` actually repairs.
  Also: dropped stale post-cutover `vendor/phase-loop-{skills,runtime}` paths in the
  `build-bundle --source` default and the BAML-closeout prompt label, and the
  `SkillBundleResolutionError` now names the `PHASE_LOOP_RUNNER_REPO_ROOT` anchor (setting
  `PHASE_LOOP_SKILL_SOURCE_PLUGINS` alone is insufficient when its roots are relative).
- **Fix (#12):** `phase-loop run`/`dry-run` failed with `SkillBundleResolutionError`
  in a pinned/pip install (no dotfiles checkout) — the wheel shipped no skills and the
  built-in source roots were dotfiles-repo-relative. The assembled neutral workflow
  skill bundle now ships **inside** the package (`phase_loop_runtime/skills_bundle/`,
  generated by `scripts/sync_skills_bundle.py`) and `resolve_source_skill_dir` falls
  back to it by absolute path (`importlib.resources`), tried last so a dotfiles overlay
  still wins for dev checkouts. The Gate A clean-room probe now asserts a pinned install
  resolves the core skills under site-packages. The resolution error message no longer
  falsely claims the entry-point is unregistered and notes that custom
  `PHASE_LOOP_SKILL_SOURCE_PLUGINS` providers must return absolute roots.

### model routing & governed review (model-routing-v1/v2)

Tiered model selection + an opt-in governed review mode. **Two orthogonal axes,
kept separate** — and the autonomous default is unchanged:

- **`model_policy`** (*what model*): a vendor-agnostic `model_class` role layer
  (`planner`/`implementer`/`worker`) resolved to a concrete model per executor
  (claude → opus/sonnet/haiku; codex → gpt-5.5/5.4/5.4-mini; gemini → `pro` for
  planning and its built-in `auto` routing alias for implementer/worker —
  gemini exposes no vetted distinct cheap model). This repo ships a default
  policy — planning at `max` effort, implementation at the implementer class. A
  checkout with **no** `model_policy` resolves model + effort byte-for-byte as
  before (the empty-policy back-compat path).
- **Behavior change for upgraders (deliberate):** with this repo's shipped
  policy, default autonomous `execute`/`repair` now route to the *implementer*
  model_class (e.g. claude → `claude-sonnet-4-6` at `medium`) rather than the
  prior executor-default heavy model at `high`. This is the intended
  implementation-by-implementer-class design; pin `--model`/`--effort` or a plan
  `## Execution Policy` to override per run/phase. The empty-policy path is
  unchanged.
- **`run_mode`** (*how governed*): `autonomous` (default) vs `governed`
  (opt-in). Autonomous invokes **no** panel and adds no `human_required`;
  governed adds a 3-harness advisor-panel gate at planning + pre-merge with a
  bounded review loop and a non-human escalation terminal.

Details:
- **Effort clamp**: requesting `max` for a sub-max provider (gemini ceilings at
  `high`) *raises* unless the policy opts into the provider `effort_map`
  fallback — the shipped policy does, so `(plan, gemini)@max → high`.
- **Selection guard**: gemini/pi are never the max-effort *planner of record*
  (they can't run at `max`); enforced at dispatch selection, not only the clamp.
- **Governed gate** reuses the rigor-v1 `ReviewFinding` severity vocabulary but
  runs on a separate plan-stage seam (not the closeout registry) and
  short-circuits before any panel spawns in autonomous mode.
- **Route logging**: each dispatch records `model_class`/`concrete_model`/
  `effort`/`route_reason` (metadata-only) to the ledger; governed panel verdicts
  surface in the run-end summary.
- *Note*: the governed pre-merge loop's logic, ladder, and rendering are wired
  and unit-tested; full live threading into the executor fix-apply cycle is
  follow-up work. The autonomous default path is a proven no-op.

## v0.1.5

Closeout convergence fixes — both resolve infinite re-dispatch loops at the source.

- **#5:** build-regenerated **gitignored** artifacts are no longer classified as un-owned
  spillover. In `_classify_dirty_paths`, a path matching a gitignore pattern
  (`git check-ignore --no-index`, which matches even *tracked* paths) is excluded from the
  `unowned` set, so it can't trigger `dirty_worktree_conflict` -> an endless repair loop. It is
  NOT dropped from the dirty set: a gitignored path the plan OWNS still classifies as
  phase-owned and commits normally (no data loss); a genuinely-unowned non-ignored path still blocks.
- **#6:** a phase whose verified work is **already on the base branch** (nothing staged to
  commit) finalizes as a no-op (`closeout_action=noop_already_committed`, `closeout_commit=HEAD`)
  and advances, instead of `git commit` exiting non-zero, being mistaken for a commit failure and
  re-dispatching forever. Gated strictly on `terminal_status == "complete"` (== verification
  passed) so a blocked/failed/non-verified phase is never finalized; checked before the
  default-branch commit guard (a no-op commits nothing).

(The deterministic-blocker loop-breaker and a `reconcile --to-status complete` escape hatch
from the fix plans remain optional follow-ups — the above resolves both loops directly.)

## v0.1.4 — planning & execution rigor (rigor-v1)

Adds autonomy-first review gates and planner guidance. **Default behavior is
unchanged**: every new gate runs at `warn` severity (records a finding to the
closeout and the loop continues); gates block only when an operator opts in via
`PHASE_LOOP_REVIEW=block`, and **no new gate ever sets `human_required`**. Human
review cadence is meant to come from bounded runs (`--max-phases`), not in-loop
stalls.

- **Pluggable closeout-validator hook** (`closeout_validators.py`): the single
  seam review gates register through, with a `warn`/`block` severity model and
  the `PHASE_LOOP_REVIEW` control (default `warn`). `closeout.py` stays
  single-writer.
- **Doc-delta gate**: a public-surface change (CLI/schema/contract docs/README/
  CHANGELOG) with no recorded `doc_delta_decision` raises a finding.
- **Verification-evidence gate**: closes the generic-phase hole — a phase
  reporting `passed` with no evidence artifact (and no typed opt-out reason)
  raises a finding. The legacy `RG`/`--verification-log` hard gate is unchanged.
- **Visual-evidence gate**: a UI/visual change (`*.tsx/jsx/vue/svelte/css`,
  `components/**`) with no `visual_evidence_path` raises a finding.
- **New blocker class** `review_gate_block` (non-human, agent-recoverable).
- **Planner guidance**: model/effort tiering (right-size each lane, escalate
  with a reason via the `## Execution Policy` section); `validate_plan_doc.py`
  gains WARN checks for a terminal docs lane, non-testable acceptance criteria,
  and missing browser/screenshot steps on UI plans.
- **Mode-aware handoff**: the `/clear` recommendation is interactive-TUI-only;
  autonomous runs rely on the written handoff + fresh runner process, or a
  dispatched subagent.
- **Run-end findings summary**: at the end of a (bounded) run the runner emits an
  aggregated, de-duplicated summary of the review findings to stderr, so a human
  reviewing between `--max-phases` batches sees them without the loop ever
  stalling.
- **Hygiene**: untracked committed `__pycache__/*.pyc` bytecode caches.

## v0.1.3

- **Fix:** break the cross-phase dirty start-gate dead-end (#1) — the start-gate's
  recommended `reconcile` recovery no longer points at a command that only accepts
  `blocked` phases, so a repo with accumulated `.phase-loop/` state can always recover.
- **Hygiene:** removed a committed `build/` directory + `egg-info` (a stale build
  artifact carrying `__version__ = "0.1.0"` that setuptools intermittently reused,
  making installs report the wrong version) and added a `.gitignore` for build artifacts.

## v0.1.2

Packaging and documentation polish — no runtime behavior change.

- `phase-loop-runtime` package `version` now tracks the release tag (was reporting
  `0.1.0` on the `v0.1.1` tag).
- Public package metadata: harness-neutral `description` (no longer "vendored for
  dotfiles"), `[project.urls]`, license classifier, Python-version classifiers, author.
- Rewrote `phase-loop-runtime/README.md` for the public install flow (the prior copy
  referenced private `vendor/...` paths and "not published").
- Added this CHANGELOG, a `phase-loop-skills/` bundle README, and a
  `docs/TEAM-ONBOARDING.md` quickstart.
- Installer default ref bumped to `v0.1.2`.

## v0.1.1

- Synced the runtime to the post-TESTDECOUPLE state: bundled `_contract_docs/` and
  `_test_fixtures/` package data so the runtime-core test suite resolves them via
  `importlib.resources` and passes standalone in the extracted layout.
- Re-ran the public-release scrub.

Supersedes v0.1.0 (which predated TESTDECOUPLE and was missing the bundled
contract-docs package data).

## v0.1.0

- Initial public extraction of the harness-neutral phase-loop runtime + the
  cross-harness workflow-skills bundle from a private fleet repo, under Apache-2.0.
- **Superseded by v0.1.1** — do not pin v0.1.0.
