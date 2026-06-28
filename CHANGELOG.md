# Changelog

All notable changes to `agent-harness` (the `phase-loop-runtime` package + the
`phase-loop-skills` bundle) are documented here. This project adheres to semantic
versioning; the release tag, the package `version`, and this file are kept in lockstep.

## Unreleased

Closeout convergence fixes — both resolve infinite re-dispatch loops at the source.

- **#5:** build-regenerated **gitignored** artifacts no longer enter the closeout dirty
  set. A path matching a gitignore pattern (`git check-ignore --no-index`, which matches
  even *tracked* paths) is dropped in `_dirty_paths`, so a regenerated codegen output can't
  be classified `unowned` -> `dirty_worktree_conflict` -> an endless repair loop.
- **#6:** a phase whose verified work is **already on the base branch** (nothing staged to
  commit) now finalizes as a no-op (`closeout_action=noop_already_committed`,
  `closeout_commit=HEAD`) and advances, instead of `git commit` exiting non-zero, being
  mistaken for a commit failure, and re-dispatching forever.

(The deterministic-blocker loop-breaker and a `reconcile --to-status complete` escape hatch
from the fix plans remain optional follow-ups — the above resolves both loops directly.)

## v0.1.4 — planning & execution rigor (rigor-v1)

Adds autonomy-first review gates and planner guidance. **Default behavior is
unchanged**: every new gate runs at `warn` severity (records a finding to the
closeout and the loop continues); gates block only when an operator opts in via
`PHASE_LOOP_REVIEW=block`, and **no new gate ever sets `human_required`**. Human
review cadence is meant to come from bounded runs (`--max-phases`), not in-loop
stalls.

- **Pluggable closeout-validator hook** (`closeout_validators.py`): the single
  seam review gates register through, with a `warn`/`block` severity model and
  the `PHASE_LOOP_REVIEW` control (default `warn`). `closeout.py` stays
  single-writer.
- **Doc-delta gate**: a public-surface change (CLI/schema/contract docs/README/
  CHANGELOG) with no recorded `doc_delta_decision` raises a finding.
- **Verification-evidence gate**: closes the generic-phase hole — a phase
  reporting `passed` with no evidence artifact (and no typed opt-out reason)
  raises a finding. The legacy `RG`/`--verification-log` hard gate is unchanged.
- **Visual-evidence gate**: a UI/visual change (`*.tsx/jsx/vue/svelte/css`,
  `components/**`) with no `visual_evidence_path` raises a finding.
- **New blocker class** `review_gate_block` (non-human, agent-recoverable).
- **Planner guidance**: model/effort tiering (right-size each lane, escalate
  with a reason via the `## Execution Policy` section); `validate_plan_doc.py`
  gains WARN checks for a terminal docs lane, non-testable acceptance criteria,
  and missing browser/screenshot steps on UI plans.
- **Mode-aware handoff**: the `/clear` recommendation is interactive-TUI-only;
  autonomous runs rely on the written handoff + fresh runner process, or a
  dispatched subagent.
- **Run-end findings summary**: at the end of a (bounded) run the runner emits an
  aggregated, de-duplicated summary of the review findings to stderr, so a human
  reviewing between `--max-phases` batches sees them without the loop ever
  stalling.
- **Hygiene**: untracked committed `__pycache__/*.pyc` bytecode caches.

## v0.1.3

- **Fix:** break the cross-phase dirty start-gate dead-end (#1) — the start-gate's
  recommended `reconcile` recovery no longer points at a command that only accepts
  `blocked` phases, so a repo with accumulated `.phase-loop/` state can always recover.
- **Hygiene:** removed a committed `build/` directory + `egg-info` (a stale build
  artifact carrying `__version__ = "0.1.0"` that setuptools intermittently reused,
  making installs report the wrong version) and added a `.gitignore` for build artifacts.

## v0.1.2

Packaging and documentation polish — no runtime behavior change.

- `phase-loop-runtime` package `version` now tracks the release tag (was reporting
  `0.1.0` on the `v0.1.1` tag).
- Public package metadata: harness-neutral `description` (no longer "vendored for
  dotfiles"), `[project.urls]`, license classifier, Python-version classifiers, author.
- Rewrote `phase-loop-runtime/README.md` for the public install flow (the prior copy
  referenced private `vendor/...` paths and "not published").
- Added this CHANGELOG, a `phase-loop-skills/` bundle README, and a
  `docs/TEAM-ONBOARDING.md` quickstart.
- Installer default ref bumped to `v0.1.2`.

## v0.1.1

- Synced the runtime to the post-TESTDECOUPLE state: bundled `_contract_docs/` and
  `_test_fixtures/` package data so the runtime-core test suite resolves them via
  `importlib.resources` and passes standalone in the extracted layout.
- Re-ran the public-release scrub.

Supersedes v0.1.0 (which predated TESTDECOUPLE and was missing the bundled
contract-docs package data).

## v0.1.0

- Initial public extraction of the harness-neutral phase-loop runtime + the
  cross-harness workflow-skills bundle from a private fleet repo, under Apache-2.0.
- **Superseded by v0.1.1** — do not pin v0.1.0.
