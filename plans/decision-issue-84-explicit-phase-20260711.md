# Decision note — agent-harness#84 (explicit `--phase` vs blocked sibling)

**Lane:** CLEANSHIP Phase 1 RUNCORE, lane (e) — investigation.
**Date:** 2026-07-11
**Anchor:** `origin/main` @ `9538604` (runner.py byte-identical to the roadmap anchor `9f50de7`).
**Decision:** **Fix-here (regression-guarded); no additional serial-path code change required.** The
adjacent concurrent-path variant that WAS a real bug is fixed in lane (d).

## The report (agent-harness#84)

Filed against `phase-loop 0.1.11`. With `SEAL` blocked (non-human
`repeated_verification_failure`) and `ROOM` planned/independent, an explicit
`phase-loop --phase ROOM --lane-scheduler concurrent … run` launched another `SEAL`
repair instead of dispatching `ROOM`.

## What was verified on current `main`

Empirical repro (`tests/test_phase_loop_issue_84_explicit_phase.py`, plus throwaway
probes) seeding the exact scenario — `DEPLOY=complete`, `SEAL=blocked` (non-human,
staged `seal.py` evidence), `ROOM=planned` — and driving the **full** `run_loop`
dispatch with `--phase ROOM` (`build_prompt` patched to capture the decided
`(phase, action)`):

- **Selection:** `_select_ready_phase(repo, roadmap, classifications, "ROOM")`
  returns `ROOM`. (`_select_ready_phase(…, None)` returns `SEAL` — the no-`--phase`
  behavior, which is correct: a blocked phase is picked first only when the operator
  did not name one.)
- **Dispatch:** the full dispatch launches **`ROOM`, action `execute`** — never a
  `SEAL` repair. `ROOM` is `planned` with a current plan-doc, so
  `launch_action = "execute"`; the `launch_action = "repair"` branch is entered only
  for a *blocked current alias*, and `ROOM` is not blocked.

So the **serial-path selection-level symptom does not reproduce on `main`.**

## Why it does not reproduce (and why it is not AUTOSEL)

- `_select_ready_phase` has honored an explicit `--phase` (`if phase: return
  phase.upper()`) since the package was first vendored (commit `6a3d05d`, "WIP
  phase-loop EXTRACT"). This predates the report's toolchain drift.
- **AUTOSEL / #152 does not fix (or touch) this.** `git show 9f50de7 -- runner.py
  discovery.py` changes **zero** phase-selection code (no `_select_ready_phase`,
  `_select_parallel_dispatch_phase`, or `select_roadmap` hunks) — matching roadmap
  assumption 4. #152 only changed default-executor resolution.
- `--lane-scheduler concurrent` sets **lane-level** parallelism
  (`lane_scheduler_mode`), not phase-level concurrency; and `coordinator_waves` is
  empty whenever `--phase` is set (`runner.py`: `coordinator_waves = … if
  parallel_dispatch and phase is None else ()`). So with `--phase ROOM` the dispatch
  is effectively serial and the coordinator-waves selector is never consulted.

## The adjacent bug that WAS real (lane d)

The concurrent coordinator-waves selector `_select_parallel_dispatch_phase(waves,
classifications)` **dropped** the explicit `--phase` (the serial `else` branch
passed it; this branch did not). When coordinator waves are active, a fully-blocked
earlier wave could halt the loop even though the operator named a ready independent
phase in a later wave. That is the true "explicit phase ignored on the concurrent
path" defect and is fixed in **lane (d)** (`_select_parallel_dispatch_phase` now
accepts and honors `phase`), with `tests/test_phase_loop_concurrent_explicit_phase.py`.

## Decision & residual scope

- **Fix-here:** add `tests/test_phase_loop_issue_84_explicit_phase.py` as a
  regression guard pinning `(ROOM, execute)` on the serial `--phase` path, and land
  lane (d) for the concurrent variant. No further serial-path code change is needed —
  the behavior is already correct on `main`.
- **Residual (scoped-defer, keep #84 open only if reproducible on ≥0.6.2):** any
  remaining #84-shaped symptom would have to originate **downstream** of a correct
  `ROOM` selection — e.g. a cross-phase-dirty start-gate interaction when `ROOM` is
  dispatched while `SEAL` leaves staged evidence, with `--allow-cross-phase-dirty`
  supplied. That is a different mechanism (start-gate, not phase selection) and is
  out of RUNCORE's closeout/dispatch/repair scope. Recommend closing #84 as resolved
  by the explicit-phase honoring + lane (d), and reopening a narrowly-scoped
  start-gate issue only if a reporter reproduces on a current build.
