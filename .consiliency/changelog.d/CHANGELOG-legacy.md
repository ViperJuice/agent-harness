<!-- CLEANSHIP Phase 7 LEGACY — advisor-surface consolidation + roadmap-discovery hygiene.
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

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
  `completed`-skip above: enabling the skip without the crash-fix would turn
  agent-harness's own bare run into a hard crash.

- **`plans/manifest.json` repaired (`ViperJuice/agent-harness`).** The tracked
  manifest was frozen at `v4` and never recorded `v5`–`v9`, so a bare run resolved
  a single completed roadmap. Its stale entries are marked to no longer resolve a
  completed roadmap on a bare run.

- **Regression guard: read/write status parity on an orphaned-entry + renamed-plan
  repo (`ViperJuice/agent-harness#162`).** The `#162` follow-up (grok CR of READONLY
  `#62`) hypothesized that a read-only `phase-loop status`/`handoff` over a manifest
  entry whose plan file was renamed (a stale live-missing entry plus a regex-reachable
  renamed plan, phase `planned` in state) would diverge from a write-intent
  `reconcile()` — a duplicate `manifest_plan_file_missing` warning plus the
  classifier-default phase. Verified at primary source that this does NOT reproduce on
  this base: the `manifest_plan_file_missing` branch in
  `discovery.manifest_plan_artifact` is unreachable, because a missing-file entry fails
  `validate_manifest` (added in the same commit) and `_phase_manifest_entries` hides
  ALL entries symmetrically in read and write mode, so both fall through to the
  regex-reachable renamed plan. A parity test pins read-mode `phases` and
  `ledger_warnings` equal to the write path across `committed`/`executing`/`completed`
  statuses, guarding against a future change to the validate gate or orphan logic.

- **Advisor `-panel`/`-board` skill twins remain collapsed to one canonical skill
  per harness plus an alias.** The canonical source is `<harness>-advisor-board`;
  `<harness>-advisor-panel` is installed as an alias of it (`SKILL_ALIASES`), so a
  historical `/<harness>-advisor-panel` invocation resolves to today's board skill
  and the two can never drift.
