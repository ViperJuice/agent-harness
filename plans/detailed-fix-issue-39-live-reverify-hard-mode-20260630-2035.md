# Detailed Plan: Issue #39 Live Reverify Hard-Mode Fail-Closed

## Task

Fix `agent-harness` #39 so the train coordinator's live reverify path fails closed when a phase plan has no executable verification evidence and `PHASE_LOOP_VERIFY_ENFORCE=hard`.

Default/warn behavior should remain unchanged: a missing `## Verification` block should still warn or pass where the current policy permits it. The hard-mode failure must be machine-actionable and must not request human intervention.

## Code Research

Primary files:

- `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`
- `phase-loop-runtime/src/phase_loop_runtime/runner.py`
- `phase-loop-runtime/src/phase_loop_runtime/discovery.py`
- `phase-loop-runtime/tests/test_train_invariants.py`
- `phase-loop-runtime/tests/test_train_merge.py`
- `phase-loop-runtime/tests/test_train_runner.py`

Findings:

- `_live_reverify` extracts plan verification commands via `verification_commands_from_plan(plan)` and suite command via `resolve_suite_command_doc(...)`.
- The current false-green path is:
  - no plan commands
  - no suite command
  - `_live_reverify` returns `True`
- `runner.py` already defines the desired hard-mode semantics:
  - `_verification_enforcement_mode()` reads `PHASE_LOOP_VERIFY_ENFORCE`
  - hard mode maps missing suite verification to blocker class `verification_evidence_missing`
  - `human_required` is `False`
- `discovery.verification_commands_from_plan` returns `([], [])` when there is no `## Verification` section.
- Existing `test_train_invariants.py` currently asserts that no verification commands returns `True`; that test should become explicit about warn/default mode and add a hard-mode counterpart.

## Implementation Steps

### 1. Add a local train reverify enforcement helper

Modify `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`.

Add a small local helper near `_live_reverify`:

- Read `PHASE_LOOP_VERIFY_ENFORCE`
- Normalize to lowercase
- Return `"hard"` only when the environment value is exactly `hard`
- Treat unset or unknown values as warn/default

Do not import `_verification_enforcement_mode` from `runner.py`; `runner.py` is a large module and `train_runner.py` should not add a new dependency edge just to reuse a tiny environment check.

### 2. Change only the missing-evidence branch

In `_live_reverify`, replace the existing `if not commands and suite_command is None: return True` behavior with:

- return `False` when train reverify enforcement mode is `hard`
- otherwise return `True`

Keep the rest of `_live_reverify` intact:

- command execution behavior should not change
- suite execution behavior should not change
- `operational_exemptions` behavior should not change
- no new human-required blocker should be introduced in this function

### 3. Update invariant tests

Modify `phase-loop-runtime/tests/test_train_invariants.py`.

Replace or split the current no-verification assertion into:

- default/warn mode: missing verification still returns `True`
- hard mode: missing verification returns `False`

Use `monkeypatch.delenv("PHASE_LOOP_VERIFY_ENFORCE", raising=False)` for the default-mode test and `monkeypatch.setenv("PHASE_LOOP_VERIFY_ENFORCE", "hard")` for the hard-mode test.

The hard-mode test must exercise `_live_reverify` itself with a real temporary phase plan lacking `## Verification`; avoid replacing the behavior with a stub.

### 4. Keep merge semantics covered

Run the existing merge/train tests after the invariant tests. Add a new merge-loop test only if a current regression is not caught by the direct `_live_reverify` hard-mode test.

The desired coverage boundary is:

- direct hard-mode test proves missing verification no longer returns success
- existing merge/train tests prove reverify failure remains a merge blocker

## Verification

Run targeted tests first:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime
PYTHONPATH=src python -m pytest tests/test_train_invariants.py -q
PYTHONPATH=src python -m pytest tests/test_train_merge.py tests/test_train_runner.py -q
```

Then run the runtime suite:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630/phase-loop-runtime
PYTHONPATH=src python -m pytest -q
```

If the repository has the migrated agent contract available, also run:

```bash
cd /mnt/workspace/worktrees/agent-harness-open-issues-planning-20260630
just agent:fast
```

## Acceptance Criteria

- With `PHASE_LOOP_VERIFY_ENFORCE=hard`, `_live_reverify` returns `False` when both plan commands and suite command are absent.
- With `PHASE_LOOP_VERIFY_ENFORCE` unset or non-hard, existing behavior remains compatible.
- The change does not introduce a human-required blocker for this condition.
- Train merge behavior still blocks on reverify failure.
- Targeted train tests and the runtime pytest suite pass.

## Out Of Scope

- Changing discovery semantics for `## Verification`
- Requiring every phase plan to add `automation.suite_command`
- Changing runner preflight blocker wording or classes
- Changing governed-pipeline or dotfiles behavior

