# Detailed plan: reject real-exec lane-scheduler without work-unit-mode (ah#186)

## Task
Consiliency/agent-harness#186 — `phase-loop --lane-scheduler concurrent` WITHOUT
`--work-unit-mode` parses the lane IR and records a ready wave, but then dispatches ONE
monolithic `execute-phase` process with an EMPTY runner-supplied owned-file contract
("Active plan owned files:" with no paths); the executor fails closed with
`dirty_worktree_conflict`. It should fail at preflight (or auto-select work-unit mode), not
start an executor that can own no phase files.

## Research summary
Direct recon of `runner.py` / `cli.py` / the wave-runner tests:
- **Root cause (structural).** The per-lane owned-file contract only reaches an executor
  via the work-unit dispatch (`launch_harness_lane_work_unit`, which carries `owned_files`),
  driven ONLY by the work-unit branch. Without `--work-unit-mode`, the lane branch records
  the wave via `launch_work_unit_attempt` (writes work-unit state + a launch event but does
  NOT dispatch), and the OUTER loop then takes the monolithic `build_prompt(..., plan=plan)`
  path whose owned files come from `_extract_plan_owned_files`, which resolves to `()` for a
  lane-structured plan → the blank "Active plan owned files" section → `dirty_worktree_conflict`.
- **The combination is VALID for `--dry-run`, incoherent only for real execution.** All
  seven existing `run_loop(..., lane_scheduler_mode="concurrent"/"serialized")` tests
  (`tests/test_phase_loop_wave_runner.py:119,197,219,247,287,320` +
  `tests/test_phase_loop_runner.py:783`) pass `dry_run=True` and assert `results == []` — they
  exercise wave SELECTION + work-unit RECORDING (a preview), never a real monolithic dispatch,
  so they never hit the empty-contract executor. A blanket "lane-scheduler requires
  work-unit-mode" guard would BREAK all seven. The guard must be scoped to real execution.
- **`run_loop` has the needed params** (`runner.py:1145`): `dry_run=False`,
  `work_unit_mode=False`, `lane_scheduler_mode="off"`. There is already a sibling **fail-loud
  footgun guard at the top of the run loop** — `concurrent` real-exec + `manual` closeout
  raises `ValueError` (around `runner.py:1247-1261`), right next to
  `require_literal(lane_scheduler_mode, ("off","serialized","concurrent"), ...)` (~`:1270`).
  That is the idiomatic home for this guard: it catches CLI, programmatic, and test callers.
- The lane branch / `_launch_ready_lane_wave` / the monolithic dispatch are all correct in
  their own right; the defect is the missing preflight that lets the incoherent real-exec
  combination start.

## Design decision: preflight-REJECT for real exec (not auto-select)
Fail loud at the run-loop preflight when `lane_scheduler_mode ∈ {serialized, concurrent}` AND
`not work_unit_mode` AND `not dry_run` — matching the existing `concurrent`+`manual-closeout`
`ValueError` idiom and the issue's "fail at preflight" option. Do NOT silently auto-enable
`--work-unit-mode` (surprising; work-unit mode changes execution/closeout semantics the
operator didn't request). Dry-run is explicitly PRESERVED (the wave-preview the seven tests
rely on). Put the guard at the run-loop top so every caller is covered — the CLI surfaces the
`ValueError` the same way it already surfaces the sibling `concurrent`+`manual` guard.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/runner.py` (modify)
- In `run_loop`, at the existing top-of-loop preflight block (near the
  `concurrent`+`manual-closeout` `ValueError` and the `require_literal(lane_scheduler_mode…)`
  at ~`:1247-1270`) — add a guard:
  ```python
  if lane_scheduler_mode in ("serialized", "concurrent") and not work_unit_mode and not dry_run:
      raise ValueError(
          "--lane-scheduler serialized/concurrent requires --work-unit-mode for real "
          "execution: the ready lane wave is recorded but NOT dispatched without it, so a "
          "single monolithic executor runs with an empty owned-file contract and fails "
          "closed (dirty_worktree_conflict). Add --work-unit-mode to execute the lanes, or "
          "use --dry-run to preview the wave."
      )
  ```
  — reason: fail the incoherent real-exec combination at preflight (the #186 fix), with a
  message that names the cause and both remedies. Placed after the `lane_scheduler_mode`
  literal validation so an invalid mode is still rejected first. Add a comment referencing
  ah#186. **No change to the lane branch, `_launch_ready_lane_wave`, or the monolithic
  dispatch** — the defect is only the missing preflight.

### `phase-loop-runtime/tests/test_phase_loop_wave_runner.py` (modify — add regression)
- Add `test_real_exec_lane_scheduler_without_work_unit_mode_is_rejected`: call
  `run_loop(repo, roadmap, phase="RUNNER", dry_run=False, lane_scheduler_mode="concurrent")`
  (a lane-structured plan, work_unit_mode omitted) and assert it raises `ValueError` whose
  message mentions `--work-unit-mode` — reason: pins the preflight reject.
- Add `test_dry_run_lane_scheduler_without_work_unit_mode_still_previews`: the SAME call with
  `dry_run=True` still returns `results == []` and records the wave (mirror the existing
  concurrent-wave test) — reason: proves the guard did NOT break the dry-run preview.
- Add `test_real_exec_lane_scheduler_with_work_unit_mode_is_allowed`: `dry_run=False,
  lane_scheduler_mode="serialized", work_unit_mode=True` does NOT raise the guard (it may fail
  later for unrelated real-exec reasons — assert only that the guard's `ValueError` is not
  raised, e.g. via `assertNotRaises`-style: run under a patched dispatch or assert the message
  is absent) — reason: confirms the guard is scoped to the missing-work-unit case only.
  (If a full real-exec `run_loop` is impractical in-unit, assert the guard predicate directly
  by patching just past it, or narrow to a preflight-only helper if one exists.)

## Documentation impact
- `CHANGELOG.md` — add — `--lane-scheduler serialized/concurrent` now fails fast at preflight
  when `--work-unit-mode` is absent and it is a real (non-dry-run) execution, instead of
  starting a monolithic executor with an empty owned-file contract that fails closed with
  `dirty_worktree_conflict`. Dry-run wave preview is unchanged.
- No other docs.

## Frozen-vocabulary confirmation
No frozen vocabulary/protocol touched — a new `ValueError` preflight only. `lane_scheduler_mode`
literals (`off`/`serialized`/`concurrent`) and `blocker_class` enums are unchanged.

## Dependencies & order
None — a single guard in `run_loop` + its tests. No CLI change required (the run-loop guard
covers the CLI path). No dispatcher/lane-IR change.

## Execution Policy
- execute: effort=low, reason=one preflight `ValueError` mirroring an adjacent existing guard
  + three scoped run_loop tests.

## Verification
```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python -m pytest tests/test_phase_loop_wave_runner.py tests/test_phase_loop_runner.py -q
# the seven dry-run wave tests MUST stay green (the guard must not touch dry-run)
PYTHONPATH=src:tests python -m pytest tests/ -q -k "lane_scheduler or wave or work_unit or concurrent"
```
Edge cases: (a) real-exec concurrent/serialized + no work-unit-mode → `ValueError` at
preflight; (b) dry-run same combo → preview unchanged (`results == []`, wave recorded);
(c) real-exec + work-unit-mode → guard not triggered; (d) `--lane-scheduler off` (default) →
never guarded; (e) an invalid `lane_scheduler_mode` still rejected by the pre-existing
`require_literal`.

## Acceptance criteria
- [ ] `run_loop(..., dry_run=False, lane_scheduler_mode="concurrent")` (no work_unit_mode)
      raises `ValueError` mentioning `--work-unit-mode` — no executor is started.
- [ ] The same call with `dry_run=True` still returns `results == []` and records the ready
      wave (the seven existing wave-runner tests stay green).
- [ ] `lane_scheduler_mode="off"` and `work_unit_mode=True` real-exec paths are NOT guarded.
- [ ] New regression tests pass; the lane_scheduler/wave/work_unit suites stay green.
