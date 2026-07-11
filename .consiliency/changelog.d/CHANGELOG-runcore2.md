<!-- POST070FIX Phase RUNCORE2 — runner/reconcile single-writer correctness batch.
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

- **Roadmap amendments no longer make a completed phase look "genuinely unplanned"
  (agent-harness#85).** When a roadmap is amended in-flight and the edit churns a
  COMPLETED phase's own section, that phase's `phase_sha256` drifts and its stored
  completion is (correctly, by the completion-invalidation invariant) no longer
  trusted, so it reclassifies to `unplanned`. Reconcile now stamps the resulting
  provenance-mismatch warning with a repairable `gold_record_amendment` marker —
  carrying the drifted vs current `phase_sha256` and a repair hint — so `status`
  can distinguish "an amendment changed this completed phase's hashes" (repair by
  restoring the section wording or re-attesting) from a phase that was genuinely
  never planned (which gets no marker). The invalidation itself is unchanged; only
  its observability is fixed. Follow-ups (not in this change): #85's runner
  active-run closeout phase-alias preservation on in-flight amendment, and
  worktree/repo path-portability replay.

- **Standalone closeout prompt no longer drops the active plan's owned files
  (agent-harness#58).** The non-governed closeout prompt built by
  `injection.build_prompt_bundle` hardcoded an empty `plan_owned_files`, so the
  executor saw a blank "Active plan owned files" section, reported empty
  `phase_owned_dirty_paths`, and the runner refused closeout with
  `missing_phase_owned_dirty_paths` even for a plan with explicit lane ownership.
  The prompt now sources the plan's declared owned patterns via
  `parse_plan_ownership`, mirroring the governed `build_lane_prompt_bundle` path.

- **Unobserved (`--no-observe`) executor children no longer hang silently
  (agent-harness#61, agent-harness#86).** An unobserved planner/execute child fell
  to `launcher.launch`'s bare `subprocess.run` branch (`log_path=None`), which has
  no heartbeat, no quiet-child / CPU-idle stall detection, and no timeout — so an
  idle child wedged the parent inside `subprocess.run` with a stale monitor and no
  fresh artifact (the avatar-client ARTIFACTS/SCENARIO wedge). `launch` now takes an
  opt-in `ephemeral_monitor` flag (set by `launch_with_spec` whenever the child is
  unobserved) that routes the child through the SAME streaming + quiet-child
  detector used for observed runs, against a throwaway log dir that is discarded
  afterward (nothing persisted, honoring `--no-observe`). `result.stalled` /
  `result.timed_out` now fire, so the runner's existing `_launch_contract_blocker`
  emits a structured `stalled_child_observation` blocker instead of hanging. The
  wall-clock timeout stays opt-in (the "no short timeout on CLI legs" rule); the
  quiet/CPU-idle detector is what catches the wedge. Cross-repo train node children
  inherit this coverage automatically (they run through `run_loop` →
  `launch_with_spec`). Not closed here: agent-harness#90 (rehydrating a completed
  roadmap from committed closeout artifacts without a runner-owned
  `verification.json` — a reconcile rehydration-contract change that must not weaken
  the `verification.json` tamper-evidence gate) and the roadmap-format-handling half
  of agent-harness#60 — both left open.

- **Compact operator stop summary in closeout/handoff (agent-harness#119).** New
  harness-agnostic `operator_stop_summary.v1` surface (`handoff.operator_stop_summary`)
  derived from closeout/handoff state: 1-10 short plain-English bullets shaped as
  What happened / Verified / Current state / Next, suitable for direct display in a
  Codex/Claude/Gemini final response instead of relying on each model to remember to
  summarize. Token-efficient by construction — no raw logs, secret values, or
  dirty-path dumps (dirty paths are summarized as a count). Rendered as a
  top-of-file "Operator Stop Summary" section in `.phase-loop/tui-handoff.md`. The
  bridge-skill wiring that injects it into each harness's final response is homed in
  the skill sources (SKILLREF), not the runtime.
