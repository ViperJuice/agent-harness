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

- **Runner-side operator-approval injection for release-dispatch (agent-harness#145).**
  Completes #145 (UNATTEND landed the typed `OperatorApproval` record + `dispatch_lock`
  fix; this wires it into the runner executor context). A release-dispatch plan that
  opts in with `phase_loop_requires_operator_approval: true` now has the runner
  resolve a metadata-only `.phase-loop/operator-approval.json`, freshness-scope it to
  this exact roadmap + phase (mirroring `_closeout_allow_unowned_attested`), and
  inject `OperatorApproval.to_metadata()` into the launch-metadata file the child
  reads plus the launch event/state — so SL-0 verifies the approval from runner
  context instead of `record_status=absent_from_runner_context` (even under
  `--bypass-approvals`). When a fresh valid record cannot be injected — absent,
  malformed, secret-bearing (rejected by `operator_approval_from`), or STALE (scoped
  to a different roadmap/phase) — the runner fail-closes to a sticky, human-required
  `admin_approval` blocker BEFORE launch, flowing through the existing
  `release_dispatch_blocker` emit path. Target-coverage stays with the child's SL-0
  `OperatorApproval.covers()` — the runner does not re-implement it. The gate is
  plan-declared opt-in, so existing release-dispatch plans launch unchanged.
  Approval injection requires an observed run: an approval-gated release-dispatch
  under `--no-observe` fails closed (`admin_approval`, `record_status=
  requires_observe`) rather than launching without the approval reaching the child.
  The `admin_approval` gate is sticky (like `missing_secret`); its
  `required_human_inputs` name the standard sticky-blocker recovery
  (`phase-loop reconcile --phase <P> --to-status planned --reason … --force`), not a
  bare rerun. Freshness is scoped to roadmap PATH + phase ALIAS (normalized, tolerant
  of absolute/relative/symlink forms); it is NOT content-bound — the frozen
  `OperatorApproval` carries no sha256. Deferred follow-ups (documented, not in this
  change): content-bound freshness (add sha256 to the record + compare provenance) and
  record authenticity (the file is hand-writable, weaker than a runner-emitted ledger
  attestation; a planted file with repo write access is trusted, the same threat
  surface as the rest of phase-loop's file-based ledger).

- **RUNCORE2 cross-vendor CR hardening (codex / grok / agy).** The 3-vendor review
  converged on concrete defects, all fixed here: the lane-(a) amendment marker moved
  off the misleading `blocker_class` key to a warning-only `diagnostic_class` and is
  framed as "provenance drift (amendment-shaped)" rather than a confirmed amendment
  (a hash mismatch could also be a hand-edited ledger); the lane-(c) ephemeral-monitor
  temp dir is now torn down via `try/finally` on the exception path; and the lane-(e)
  tests were strengthened to assert the injected approval reaches the child launch
  metadata + persisted event (not merely "not blocked"), plus wrong-roadmap-stale and
  secret `record_status` coverage and a `build_prompt_bundle`-level #58 wiring test.
