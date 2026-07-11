<!-- CLEANSHIP Phase 1 RUNCORE — runner.py closeout / dispatch / repair bugfixes.
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

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
  snapshot carries no dirty summary, so the remainder is force-committed under the
  audited reason. Secrets remain non-break-glassable and keep the phase blocked.

- **A valid planned repair closeout clears the stale blocker instead of looping
  repair (`ViperJuice/agent-harness#59`).** When a bounded repair child reshaped
  the plan and emitted a valid closeout (`terminal_status=planned`,
  `verification_status=not_run`, `dirty_paths=[]`, no blocker, `human_required=null`)
  leaving the tree clean, the parent runner kept the stale non-human blocked state
  and relaunched the same repair path. `repair_precondition_for_snapshot` now clears
  the planned-repair-closeout case (beyond `dirty_worktree_conflict`) so the phase
  re-executes from the repaired plan — conditioned on the repair child's own
  planned/clean evidence and a clean tree, not on `blocker_class` alone, so a
  genuinely un-repaired blocker still repairs and a later interrupted repair does
  not clear.

- **Explicit `--phase` is honored on the concurrent scheduler path (new bug found
  during CLEANSHIP).** The serial selector already honored an explicit `--phase`,
  but the concurrent coordinator-waves selector `_select_parallel_dispatch_phase`
  dropped it — wave order picked the phase and a fully-blocked earlier wave halted
  the loop even when the operator asked for a ready independent phase in a later
  wave. The selector now accepts and applies the explicit phase, mirroring the
  serial path.

- **#84 investigation (`ViperJuice/agent-harness#84`).** The reported serial-path
  symptom (`--phase ROOM` repairs a blocked `SEAL` instead of dispatching the
  explicit independent phase) does not reproduce on current `main`: the serial
  selector already honors an explicit `--phase` and the full dispatch launches
  `ROOM` execute. AUTOSEL/#152 touches zero phase-selection code (confirmed). The
  adjacent concurrent coordinator-waves variant WAS a real drop and is fixed above.
  A regression guard pins `(ROOM, execute)` on the serial path; see
  `plans/decision-issue-84-explicit-phase-20260711.md`.
