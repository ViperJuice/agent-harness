# Detailed plan: fix #5 — closeout treats build-regenerated gitignored artifacts as un-owned spillover (infinite dirty_worktree_conflict loop)

> Plan artifact for agent-harness issue #5. Bounded change to `phase-loop-runtime`.
> Implement on a branch (`fix/5-gitignored-closeout-spillover`), PR, then it ships in the next release.

## Task

A phase whose build verification **regenerates a gitignored artifact** (e.g.
`baml-cli generate` → `src/baml/generated/`, listed in `.gitignore`) hits a non-converging
loop: the closeout dirty-worktree audit flags the regenerated-but-ignored output as
**un-owned spillover** → `blocker_class=dirty_worktree_conflict` (`human_required=false`) →
the runner re-dispatches a repair turn → the repair re-runs the same build → the same
ignored output reappears → the **identical blocker recurs every time**. Observed ~15
consecutive attempts × ~90k tokens, the phase's real work passing the whole time
(`verification_status=passed`, IF gate produced).

Two independent fixes (either one alone breaks the loop; ship both):
- **A (root cause):** gitignored paths must not be classified as spillover.
- **B (safety net):** a deterministic recurring `human_required=false` blocker must escalate
  instead of retrying forever.

## Research summary

- **Dirty-set enumeration:** `git_ops.py:14 snapshot_git_dirty_paths(repo)` runs
  `git status --porcelain --untracked-files=all` and returns the changed paths. NOTE:
  porcelain excludes *untracked* ignored files, but a path that is **tracked yet matches a
  gitignore pattern** (common for committed-then-ignored generated output, or a generated
  dir that was ever added) still appears as `M`/`A`. So gitignored regenerated paths can and
  do enter the dirty set.
- **Classification:** `closeout.py:42 reduce_lane_dirty_paths(dirty_paths, lanes, …)` already
  has a clean exclusion pattern — it short-circuits `pre_existing_paths` →
  `classification="pre_existing"` and `reducer_paths` → `"reducer_owned"` *before* the
  owned-lane check that otherwise yields `classification="unowned"` (closeout.py:53-66). The
  caller turns `unowned` paths into the `dirty_worktree_conflict` blocker
  (`blocker_summary` "Required build verification regenerated ignored … outputs outside the
  … owned-file contract", emitted around `runner.py:7360-7366`).
- **No loop-breaker:** the runner re-dispatches a `human_required=false`
  `dirty_worktree_conflict` repair turn with no memory of whether the *same* blocker (same
  `dirty_paths`) already recurred. A deterministic blocker therefore never self-heals.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/git_ops.py` (modify)
- `snapshot_git_dirty_paths` — **add** a sibling helper `gitignored_paths(repo, paths) -> set[str]`
  (add — reason: a single source of truth for "is this path gitignored"). Implement with one
  batched call: `git -C <repo> check-ignore --stdin -z` fed the candidate paths (fall back to
  `()` / empty set on any `CalledProcessError`, mirroring the existing `except Exception` guard).
  Do **not** change what `snapshot_git_dirty_paths` itself returns — keep it the raw dirty set;
  the gitignore filtering happens at classification (below) so other callers are unaffected.

### `phase-loop-runtime/src/phase_loop_runtime/closeout.py` (modify)
- `reduce_lane_dirty_paths` (around line 42) — add a new keyword `gitignored_paths: tuple[str, ...] | list[str] = ()`
  and a new short-circuit branch *before* the `_owning_lane` check (i.e. alongside the existing
  `pre_existing` / `reducers` branches, ~line 53-59): if `path in gitignored`, append
  `DirtyPathClassification(path=path, classification="gitignored")` and `continue`. Reason:
  a declared-disposable build artifact is not tracked-work spillover, so it must not become
  `unowned`.
- `DirtyPathClassification` — confirm `"gitignored"` is an accepted `classification` value
  (modify the literal/validation if the field is constrained; search for where
  `DirtyPathClassification` is defined and where classifications are consumed).
- **Caller of `reduce_lane_dirty_paths`** (find it — it assembles `dirty_paths` +
  `pre_existing_paths` and turns the result into the closeout `dirty_worktree_conflict`
  blocker; it is in the closeout build path, not `closeout.py:reduce_lane_dirty_paths` itself):
  compute `gitignored = gitignored_paths(repo, dirty_paths)` and pass it through; ensure the
  blocker is only raised for classifications in `{"unowned"}` (NOT `"gitignored"`/`"pre_existing"`).
  Record the excluded gitignored paths in the closeout metadata (e.g. a
  `gitignored_dirty_paths` field next to the existing `unowned_dirty_paths` /
  `pre_existing_dirty_paths` in `state.py:67-71`) for observability.

### `phase-loop-runtime/src/phase_loop_runtime/runner.py` (modify) — fix B, loop-breaker
- In the dispatch/repair loop that re-dispatches a `human_required=false`
  `dirty_worktree_conflict` (the same loop that calls `advance()` ~line 232/234 and tracks
  per-phase attempt state): track the **last blocker fingerprint** = `(blocker_class, sorted(dirty_paths))`.
  When the *identical* fingerprint recurs `N` consecutive times (constant, default 2; make it a
  module constant, not magic), stop re-dispatching and emit a terminal blocker with
  `blocker_class="stuck_loop"`, `human_required=True`, `terminal_status="blocked"`, summarizing
  the recurring dirty paths. Reason: a deterministic blocker that reproduces identical state
  will never self-heal; burning `--max-phases` dispatches is the worst outcome.
- Reuse `stuck_loop` from the frozen blocker taxonomy (it already exists in the
  `blocker_class` literal set — confirm in `models.py`); do not invent a new class.

## Documentation impact
- `CHANGELOG.md` — add the fix under the next version's entry (gitignored-closeout-spillover
  + stuck-loop escalation). The doc-delta gate (rigor-v1) will flag a contract change with no
  `doc_delta_decision`, so record one or update CHANGELOG.
- `phase-loop-runtime/_contract_docs/phase-loop/protocol.md` — if the closeout
  classification vocabulary or the `stuck_loop` escalation rule is specified there, add
  `gitignored` to the classification list and note the deterministic-blocker escalation.

## Dependencies & order
1. `git_ops.py` helper (no deps).
2. `closeout.py` classification (consumes the helper).
3. caller wiring + `state.py` metadata field.
4. `runner.py` loop-breaker (independent of 1-3; can land in the same PR).

## Verification
```bash
cd phase-loop-runtime
# A — unit: gitignored regenerated path classifies as "gitignored", not "unowned":
pytest -k "reduce_lane_dirty_paths or gitignored or closeout_classification" -q
# B — unit: identical dirty_worktree_conflict recurring N× escalates to stuck_loop/human_required:
pytest -k "stuck_loop or dirty_loop or repair_escalat" -q
# integration (add): a repo with a gitignored generated dir whose build regenerates it,
#   run a phase closeout -> expect terminal_status advances (no dirty_worktree_conflict),
#   gitignored_dirty_paths recorded, NO repair re-dispatch.
pytest -q   # full suite stays green
```
Add the two unit tests above (they do not exist yet — write them against the new behavior).

## Acceptance criteria
- [ ] A gitignored path in the closeout dirty set is classified `gitignored` and does **not**
      produce `dirty_worktree_conflict`; `verification_status` stays `passed`; the phase finalizes.
- [ ] `snapshot_git_dirty_paths` is unchanged (other callers unaffected); gitignore filtering
      happens only at closeout classification.
- [ ] An identical `human_required=false` `dirty_worktree_conflict` recurring N consecutive
      times escalates to `stuck_loop` / `human_required=true` instead of re-dispatching.
- [ ] `pytest -q` green; the two new unit tests pass; CHANGELOG updated.
