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
  snapshot carries no dirty summary, **scoped to the remainder the prior closeout
  recorded** (the paths the operator's reason attests to) intersected with what is
  still dirty — so an unrelated live edit can never be force-committed under a reason
  that named only the phase's remainder. Secrets remain non-break-glassable and keep
  the phase blocked.
  The closeout now **isolates the index** before staging, review, and commit — it
  resets the index to `HEAD`, stages only the accepted `closeout_dirty_paths`, and
  commits the reviewed staged index (pathspec-less). Previously the pathspec-less
  commit ran over whatever was staged, so a pre-staged unrelated file — including a
  `.env`/secret the fallback deliberately excluded — was swept into the commit,
  silently defeating the secrets-never-break-glassable contract. Committing the
  isolated **staged** index (rather than a path-scoped `git commit -- <paths>`, which
  would re-read the working tree) also preserves the governed panel's
  "reviewed == committed" invariant against a mid-review worktree change. A
  **secret-only** break-glass remainder now also keeps the sticky
  `closeout_scope_violation` (`human_required`) gate instead of downgrading to a
  non-human `dirty_worktree_conflict`, so the loop cannot silently leave the human
  gate and run automation against a secret-dirty tree.

- **A valid planned repair closeout clears the stale blocker instead of looping
  repair (`ViperJuice/agent-harness#59`).** When a bounded repair child reshaped
  the plan and emitted a valid closeout (`terminal_status=planned`,
  `verification_status=not_run`, `dirty_paths=[]`, no blocker, `human_required=null`)
  leaving the tree clean, the parent runner kept the stale non-human blocked state
  and relaunched the same repair path. `repair_precondition_for_snapshot` now clears
  the planned-repair-closeout case (beyond `dirty_worktree_conflict`) so the phase
  re-executes from the repaired plan — conditioned on the repair child's own
  planned/not_run/clean evidence (every field required present) and a clean tree,
  not on `blocker_class` alone. A genuinely un-repaired blocker still repairs, and a
  later blocking event — even one with no `child_automation` metadata (e.g. a
  runner-emitted `repeated_verification_failure`) — supersedes an earlier planned
  child and does not clear. The evidence predicate is **fail-closed**: the
  positive-signal fields (`status=planned`, `verification_status=not_run`, an empty
  `dirty_paths` list, `human_required` present and not `true`) must all be present and
  valid, so a truncated/partial child payload cannot clear a blocker.

- **Explicit `--phase` consistency on the concurrent coordinator-waves selector.**
  `_select_parallel_dispatch_phase` did not accept a `phase` argument, so it could
  only pick by wave order. It now accepts and honors an explicit phase (bounded to
  the wave structure), mirroring the serial `_select_ready_phase`. This is a
  **defensive consistency** change, not a currently reachable bug fix: through
  `run_loop`, `coordinator_waves` is populated only when no explicit `--phase` is set,
  so an explicit phase is already served by the serial selector today; the guarantee
  matters only if that invariant changes.

- **#84 investigation (`ViperJuice/agent-harness#84`).** The reported serial-path
  symptom (`--phase ROOM` repairs a blocked `SEAL` instead of dispatching the
  explicit independent phase) does not reproduce on current `main`: the serial
  selector already honors an explicit `--phase` and the full dispatch launches
  `ROOM` execute. AUTOSEL/#152 touches zero phase-selection code (confirmed). The
  adjacent concurrent coordinator-waves selector was hardened for consistency (above),
  though it is not reachable with an explicit `--phase` through `run_loop` today.
  A regression guard pins `(ROOM, execute)` on the serial path; see
  `plans/decision-issue-84-explicit-phase-20260711.md`.
