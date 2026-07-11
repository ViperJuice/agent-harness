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
