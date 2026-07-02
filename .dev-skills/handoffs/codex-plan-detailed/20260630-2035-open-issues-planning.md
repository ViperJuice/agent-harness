# codex-plan-detailed handoff - open issues planning

Generated on 2026-06-30 20:35 UTC from branch `codex/open-issues-planning-20260630`.

## Artifacts

- `plans/open-issues-planning-assessment-20260630-2035.md`
- `plans/detailed-fix-issue-39-live-reverify-hard-mode-20260630-2035.md`
- `plans/detailed-fix-issue-33-background-subprocess-liveness-20260630-2035.md`

## Classification

- `agent-harness` #39 is a detailed-plan task. It is a bounded train reverify bug in `train_runner.py`.
- `agent-harness` #33 is a detailed-plan task. Stdin handling already exists; the remaining work is CPU-aware liveness classification and salvage evidence.
- `agent-harness` #26 does not currently need a local `agent-harness` detailed plan. Latest issue context points to governed-pipeline and dotfiles follow-ups.

## Research Anchors

- #39: `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`, `runner.py`, `discovery.py`, `tests/test_train_invariants.py`, `tests/test_train_merge.py`, `tests/test_train_runner.py`.
- #33: `phase-loop-runtime/src/phase_loop_runtime/launcher.py`, `observability.py`, `tests/test_phase_loop_launcher.py`, `tests/test_observability.py`.

## Verification Performed

- `git diff --check` passed.
- Roadmap validation was run for `specs/phase-plans-v4.md`; it is recorded in the roadmap handoff.

## Next Step

For execution, start with #39 because it is narrow and safety-relevant. #33 can run separately once owner boundaries with panel subprocess execution remain clear.

