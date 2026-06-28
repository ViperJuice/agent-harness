# Detailed plan: fix #6 — runner cannot finalize a phase whose work was committed out-of-band (re-dispatches forever)

> Plan artifact for agent-harness issue #6. Bounded change to `phase-loop-runtime`.
> Implement on a branch (`fix/6-finalize-out-of-band-phase`), PR, then it ships in the next release.

## Task

When a phase's work is **already on the base branch** (committed out-of-band, e.g. via merged
PRs) before the runner executes that phase, the executor repeatedly reports
`terminal_status=complete` / `verification_status=passed` with the phase's IF gate produced and
`next_action`="proceed to the next phase" — but **the runner never finalizes/advances**. It
re-dispatches the same phase as a fresh work-unit forever (~11 attempts, capped only by
`--max-phases`). In commit-mode closeout there is nothing to commit, so no closeout commit is
made, the stored `closeout_summary` stays pinned to the *previous* phase, and neither
`--closeout-mode manual` nor `phase-loop reconcile --verification-status passed` finalizes it.

## Research summary

- **Commit-mode closeout** (`runner.py` ~7374-7423): builds a commit message, `git add -- <paths>`,
  then `commit_result = _run_git_closeout(repo, "commit", "-F", "-", …)`. **Only the success
  branch** (`commit_result.returncode == 0`, ~line 7414-7423) sets `status="complete"`,
  `verification_status="passed"`, and records `closeout_commit`. When there is **nothing to
  commit** (work already on base → empty staged diff), `git commit` exits **non-zero**, so it
  falls into `_commit_failure_closeout(stage="commit", …)` (~line 7407-7413) → the phase is
  treated as a **commit failure**, never finalized → re-dispatched. That is the bug: "nothing
  to commit" is indistinguishable from a real commit failure in the current code path.
- **Advance gating:** finalization/advance (`advance()` ~line 234) is driven off the closeout
  `status`; a non-`complete` closeout never advances the phase, so the loop re-selects it.
- **Reconcile** (`reconcile.py`) only resets a blocked phase back to `planned`; there is no
  "accept this verified phase as complete" path, so the documented manual escape does not work.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/runner.py` (modify) — primary fix
- In the commit-mode closeout branch (after the `git add`, ~line 7385-7393, **before** running
  `git commit`): detect the empty-staged case explicitly with
  `git -C <repo> diff --cached --quiet` (returncode 0 == nothing staged). Add a helper
  `_closeout_nothing_to_commit(repo) -> bool`.
- When nothing is staged **AND** the child terminal status is a success (`terminal_status`
  ∈ complete/awaiting_phase_closeout with `verification_status=passed` and the phase's IF
  gate(s) produced — reuse `_child_terminal_status` ~line 636 and the existing IF-gate check):
  treat it as a **no-op finalization** instead of running `git commit`:
  - `status = "complete"`, `metadata["closeout"]["verification_status"] = "passed"`.
  - `metadata["closeout"].update({"closeout_action": "noop_already_committed", "closeout_commit": <current HEAD via _git_output(repo,"rev-parse","HEAD")>})`.
  - Reason: the phase's verified work is already on the base; an empty closeout is *success*,
    not failure. Recording the current HEAD as `closeout_commit` keeps the closeout_summary
    pointer correct (it must reflect *this* phase, not the previous one — that pinning bug is a
    direct symptom of never reaching the success branch).
- Leave the genuine failure path intact: a non-zero `git commit` with a real error (not
  "nothing to commit") still routes to `_commit_failure_closeout`. Only the **empty-staged +
  verified** combination short-circuits to finalize. Be precise: gate on `diff --cached --quiet`,
  not on parsing git's "nothing to commit" stderr string.

### `phase-loop-runtime/src/phase_loop_runtime/reconcile.py` + `cli.py` (modify) — secondary / belt-and-suspenders
- Add a reconcile path to **accept a verified phase as complete** for cases the runner still
  can't auto-finalize (e.g. manual closeout mode). Concretely: extend `phase-loop reconcile`
  with `--to-status complete` (or a `--accept-complete` flag) that, when the phase's last
  terminal summary shows `verification_status=passed` + the IF gate produced, writes the
  finalized closeout state (status `complete`, `closeout_commit` = current HEAD,
  `closeout_action="reconcile_accept_complete"`) and advances — instead of only the existing
  reset-to-`planned`. Guard it: refuse unless the recorded terminal summary is actually passed
  with the produced IF gate (don't let an operator force-complete unverified work).
- Update the relevant `--to-status` choices / help text in `cli.py` (the `reconcile`
  subparser) and the reconcile status-transition validation.

## Documentation impact
- `CHANGELOG.md` — note: "commit-mode closeout with nothing to commit (work already on base)
  now finalizes the phase instead of looping; `reconcile --to-status complete` accepts a
  verified phase." (doc-delta gate will want a recorded decision / CHANGELOG entry.)
- `phase-loop-runtime/_contract_docs/phase-loop/protocol.md` — if closeout actions /
  reconcile transitions are enumerated there, add `noop_already_committed` and the new
  reconcile transition.

## Dependencies & order
1. `runner.py` no-op-finalize (the primary fix; resolves the loop on its own).
2. `reconcile.py` + `cli.py` accept-complete (independent; secondary escape hatch).

## Verification
```bash
cd phase-loop-runtime
# primary unit: commit-mode closeout with an empty staged diff + verified terminal status
#   -> status "complete", closeout_action "noop_already_committed", closeout_commit == HEAD:
pytest -k "closeout and (noop or nothing_to_commit or already_committed or out_of_band)" -q
# regression: a genuine git-commit failure still routes to _commit_failure_closeout:
pytest -k "commit_failure_closeout" -q
# reconcile: --to-status complete finalizes a verified phase, refuses an unverified one:
pytest -k "reconcile and complete" -q
pytest -q   # full suite green
```
Add the primary unit test (construct a repo where the phase's work is already committed, run
commit-mode closeout, assert finalization + advance, assert no re-dispatch). These tests do
not exist yet.

## Acceptance criteria
- [ ] Commit-mode closeout with nothing to commit + a verified terminal status (passed + IF
      gate produced) finalizes the phase (`status=complete`, `closeout_action=noop_already_committed`,
      `closeout_commit=HEAD`) and the runner **advances** — no re-dispatch.
- [ ] `closeout_summary` after the no-op closeout reflects **this** phase, not the previous one.
- [ ] A real `git commit` failure still routes to `_commit_failure_closeout` (no regression).
- [ ] `phase-loop reconcile --to-status complete` finalizes a verified phase and refuses an
      unverified one.
- [ ] `pytest -q` green; new tests pass; CHANGELOG updated.
