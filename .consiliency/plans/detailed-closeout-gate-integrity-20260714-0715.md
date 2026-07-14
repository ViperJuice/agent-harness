# Detailed plan: Governed closeout & gate-integrity hardening (phase-loop-runtime)

## Task
Fold in the three closeout/verification defects the SPECCONFORM run surfaced (agent-harness#218, #219) — all one class: **the governed `complete` marker is not trustworthy on its own.** This plan hardens three bounded, high-value sites so a phase cannot be marked `complete`/`verification: passed` when (a) its owned deliverable is a new all-untracked directory, (b) its suite ran under an interpreter that can't satisfy the repo's `requires-python`, or (c) a suite command exited non-zero. The larger acceptance-criteria→command **coverage** capability (#219 (ii)) is called out as a separate follow-up slice (see Dependencies & order) because it requires a net-new criteria parser that exceeds a bounded plan.

## Research summary
Two Explore passes mapped the exact sites (package root `phase-loop-runtime/src/phase_loop_runtime/`):

- **#218 dir-collapse (ownership scope violation).** Runtime's own dirty collection (`_dirty_paths`, `runner.py:7554`; `git_ops.snapshot_git_dirty_paths:14`) uses `git status --porcelain --untracked-files=all`, which expands untracked dirs — clean. The collapse enters **only** via the executor's self-reported `phase_owned_dirty_paths`/`dirty_paths` (`closeout.py:172` and `:234`) → reconcile snapshot (`reconcile.py:267-268`) → the fallback partial-classifier `fallback_dirty_paths = snapshot.dirty_paths` (`runner.py:7995`). The matcher `PlanOwnership.matches_dirty_output` (`discovery.py:131`) → `matches` (`:124`) → `_owned_pattern_matches` (`discovery.py:2183`) does `fnmatchcase(path, pattern)` for globs and `startswith` only for patterns ending `/`; it **never expands a directory into its member files**, so a bare-dir string like `pkg/newmod/` misses every file-level owned glob (`pkg/newmod/*.py`) and routes to `unowned_remainder`, firing `closeout_scope_violation`/`unowned_dirty_remainder` at `runner.py:8123`/`:8140` (and `:8420`/`:8434`). `reconcile.py:645` (plain porcelain) is a red herring — it lives only in the `merge_conflict` self-clear branch and never feeds scope classification.
- **#219 (a) interpreter.** `automation.suite_command` comes from front-matter (`discovery.py:643-653`, `resolve_suite_command_doc:575`), normalized via `shlex.split`/list (`discovery.py:656-680`), and run by `run_verification` (`verification_evidence.py:89`) → `_run_process` (`:346`) → `subprocess.run(process_argv)` (`:356`) with **no shell and no interpreter resolution**. A bare `python …` (or a plan-authored `bash -lc "python …"`) resolves `python` from PATH — 3.10 on this runner — and fails `requires-python>=3.11` package builds (`ResolutionImpossible` / exit 1) even though CI (py3.12) and an independent py3.12 run pass 26/26.
- **#219 (b-i) false-green.** `verification_status` is read from the executor's self-asserted closeout JSON (`closeout.py:109-110`, `:344`: `terminal.get("verification_status") or automation.get(...)`), NOT from the VerificationResult per-command exit codes. The exit-code reducer `_nonzero_exit_findings` (`verification_evidence.py:334`) exists but is only consumed by `validate_verification_artifact` (`:237-246`, `code="nonzero_exit"`). The gate `_apply_verification_evidence_gate` (`closeout.py:336`) runs only when reported=="passed" (`:345`), requires the artifact only when `_verification_evidence_required` is true (`closeout.py:547` — true ONLY for alias `RG` or plans literally containing `IF-0-RG-1`/`--verification-log`), else returns `None` and passes ungated (`closeout.py:365`); and even a validated non-zero artifact only downgrades to a warning unless `_verification_enforcement_mode` (`runner.py:5910`) is `hard` (default `warn`; `PHASE_LOOP_VERIFY_ENFORCE`). Completeness = terminal=="complete" AND verification=="passed" AND no blocker (`closeout.py:700-707`). Net: a non-RG governed phase can be `passed`/`complete` with non-zero exits or no evidence at all.
- **#219 (b-ii) acceptance coverage (out of this plan).** Acceptance `- [ ]` checkboxes are never parsed into structured criteria — `plan_ir.py:233-236` only keyword-detects "acceptance" to label a reducer lane; `governed_bundle.py:42` slices the section into a review prompt; `plan_manifest.py:55/463` stores a count. No criteria↔command mapping exists anywhere.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/git_ops.py` (modify)
- `expand_dir_dirty_paths(repo, paths)` — **add** — new shared helper: given a list of repo-relative dirty paths, replace any entry that resolves to a directory in the repo (or ends `/`) with its constituent files via `git -C <repo> status --porcelain --untracked-files=all -- <dir>` (tracked-and-untracked members); pass through file entries unchanged. One source of truth reused by both #218 sites below. Reason: #218 — normalize collapsed bare-dir entries to file granularity before ownership matching.

### `phase-loop-runtime/src/phase_loop_runtime/closeout.py` (modify)
- executor self-report ingestion around `:172` and `:234` — **modify** — pass `terminal.get("phase_owned_dirty_paths")` and the dirty-paths set through `git_ops.expand_dir_dirty_paths(repo, …)` so an executor-collapsed new directory enters as its member files. Reason: #218 — the collapse's sole entry point.
- `_verification_evidence_required` around `:547` — **modify** — return true for ANY governed phase whose active plan carries an `automation.suite_command` (not only `RG`/`IF-0-RG-1`), so a self-asserted "passed" with no VerificationResult artifact no longer passes ungated. Keep the existing RG triggers. Reason: #219 (b-i).
- `_apply_verification_evidence_gate` around `:336` — **modify** — when a VerificationResult artifact exists and `_nonzero_exit_findings(result)` is non-empty, set the effective `verification_status` to `failed` (or `blocked`) irrespective of the executor's self-asserted value; the per-command exit codes are authoritative over the self-assertion. Reason: #219 (b-i) — exit codes must override self-report.

### `phase-loop-runtime/src/phase_loop_runtime/runner.py` (modify)
- `_classify_dirty_paths` around `:7650` and the fallback partial-classifier `:7999-8072` — **modify** — normalize `fallback_dirty_paths` (`:7995`) via `git_ops.expand_dir_dirty_paths(repo, …)` before `matches_dirty_output`, as defense-in-depth for any collapsed dir that still reaches the classifier. Reason: #218.
- `_verification_enforcement_mode` around `:5910` — **modify** — treat a **non-zero suite/command exit** as a HARD fail-closed condition even under the default `warn` mode (warn continues to apply only to softer findings like evidence-drift). Do not change the default mode string for other findings. Reason: #219 (b-i) — non-zero exit must always block, not warn.

### `phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py` (modify)
- `_process_env_and_argv` around `:534` / `_run_process` around `:346` — **modify** — before running the suite, resolve an interpreter satisfying the target repo's `requires-python` and expose it to the suite subprocess. Recommended mechanism (see Dependencies & order for the decision): honor an explicit `automation.python` pin from the plan front-matter when present; otherwise read `requires-python` from the target package `pyproject.toml`(s), resolve the lowest satisfying `pythonX.Y` on the host, and prepend a scoped shim dir (`python`/`python3` → that interpreter) to the suite subprocess `PATH`. Fail closed with a clear blocker when no satisfying interpreter exists. Reason: #219 (a).

### `phase-loop-runtime/src/phase_loop_runtime/discovery.py` (modify — optional, defense-in-depth)
- `_owned_pattern_matches` around `:2183` — **modify (optional)** — additionally treat a directory dirty path as owned when an owned glob targets files strictly inside it (pattern startswith `<dirpath>`), so the matcher is correct even if a caller forgets to expand. The runner/closeout expansion above is the primary fix; include this only if it stays a 3-line guard. Reason: #218 defense-in-depth.

### Tests (new) — `phase-loop-runtime/tests/` (create; put in an UNMARKED module so CI's `-m "not dotfiles_integration"` runs them)
- `test_closeout_new_owned_directory_closes_clean` — a phase whose owned contract is `pkg/newmod/**` (file-level) and whose executor self-reports the collapsed bare dir `pkg/newmod/` closes out `complete` with the dir committed, **without** `--closeout-allow-unowned` and **without** a `closeout_scope_violation`. Reason: #218 regression.
- `test_nonzero_suite_command_fails_closed` — a governed phase whose VerificationResult has a non-zero command exit yields `verification_status` failed/blocked at closeout **even when** the executor self-asserts `passed`, under the default enforcement mode. Reason: #219 (b-i) regression.
- `test_self_asserted_passed_without_evidence_is_gated` — a non-RG governed phase with a `suite_command` but no VerificationResult artifact does not reach `complete`. Reason: #219 (b-i) regression.
- `test_suite_interpreter_satisfies_requires_python` — given a target package `requires-python>=3.11` and a host default `python` below it, the resolved suite interpreter satisfies the constraint (or fails closed with a named blocker). Reason: #219 (a) regression.

## Documentation impact
- `phase-loop-runtime/CHANGELOG.md` — **add** — entry under `[Unreleased]`: dir-aware ownership classification; non-zero-suite fail-closed; requires-python-aware suite interpreter. Committed-diff docs-freshness gate requires this before the public-surface change merges (see [[docs-audit-changelog-gate]] in prior art).
- `automation.python` front-matter field — **add** — document the optional per-phase interpreter pin in the phase-plan authoring reference (wherever `automation.suite_command` is documented). If no such doc exists, state it inline in the roadmap/plan skills' `automation` description.
- No frozen-vocabulary/protocol file is modified (blocker-class enums, closeout JSON schema, and IF-gate grammar are untouched — this changes derivation logic, not the wire contract).

## Dependencies & order
1. **`git_ops.expand_dir_dirty_paths` first** — both #218 edits (closeout.py, runner.py) consume it.
2. **Interpreter mechanism is the one design decision.** Options: (A) `automation.python` pin only — simplest, but every phase plan must opt in; (B) auto-resolve from `requires-python` + PATH shim — zero-config, matches CI, but needs host-interpreter discovery; (C) both (pin overrides auto). **Recommended: C** (pin wins; auto-resolve is the safety net). Resolve this before implementing the verification_evidence.py edit.
3. **Fail-closed ordering:** `_verification_evidence_required` broadening and the exit-code override must land together — broadening evidence-required without the exit-code override would only add "missing evidence" blocks, not catch red suites; the override without broadening would still miss the no-artifact case.
4. **Deferred (separate slice): #219 (b-ii) acceptance-criteria→command coverage.** Needs a net-new parser turning `- [ ]` acceptance items into structured criteria and a closeout check that each maps to an executed+passed verification command. This is a new capability (nothing parses criteria today) and is out of scope here — recommend a dedicated `/claude-plan-detailed` or a roadmap phase. Until it lands, the cross-vendor CR remains the backstop that catches unmet acceptance criteria (as it did for REPLAY).
5. **Fleet sequencing:** landing this de-risks the remaining SPECCONFORM phases (EXTRACT/PROMOTE/PORTAL), which otherwise need the py3.12-shim + gitignore + CR-as-gate workarounds used for PKGARCH/REPLAY. Recommend landing before EXTRACT if wall-clock allows; SPECCONFORM can proceed with workarounds meanwhile.

## Execution Policy
- execute: effort=high, reason=closeout/verification is safety-sensitive governance logic (fail-closed semantics, ownership trust); subtle to get right and easy to regress.

## Verification
Run from `phase-loop-runtime/` (PYTHONPATH per [[phase-loop-suite-pythonpath-invocation]]):
```bash
PYTHONPATH=src:tests python -m pytest tests/test_closeout_new_owned_directory_closes_clean.py \
  tests/test_nonzero_suite_command_fails_closed.py \
  tests/test_self_asserted_passed_without_evidence_is_gated.py \
  tests/test_suite_interpreter_satisfies_requires_python.py -q
# full closeout/verification regression (no behavior regressions elsewhere):
PYTHONPATH=src:tests python -m pytest tests -k "closeout or verification or dirty or ownership or reconcile" -q
```
Behaviors to observe: a brand-new all-owned directory commits clean with no `closeout_scope_violation`; a non-zero suite exit yields `verification: failed/blocked` under default mode; a satisfying interpreter is selected for a `requires-python>=3.11` target on a py3.10-default host.

`automation.suite_command`: `["bash","-lc","cd phase-loop-runtime && PYTHONPATH=src:tests python -m pytest tests -k 'closeout or verification or dirty or ownership or reconcile' -q"]` (self-dogfoods the requires-python resolution once #219(a) lands).

## Acceptance criteria
- [ ] A phase whose owned deliverable is a brand-new all-untracked directory (executor self-reports the collapsed bare dir) closes out `complete` with the directory committed, with no `closeout_scope_violation`/`unowned_dirty_remainder` and no `--closeout-allow-unowned`.
- [ ] At closeout, `verification_status` is `failed`/`blocked` whenever the VerificationResult has any non-zero command/suite exit, overriding an executor self-asserted `passed`, under the default enforcement mode.
- [ ] A non-RG governed phase carrying an `automation.suite_command` cannot reach `complete` on a self-asserted `passed` with no VerificationResult artifact.
- [ ] The suite subprocess runs under an interpreter satisfying the target repo's `requires-python` (honoring an `automation.python` pin when present), or fails closed with a named blocker when none exists.
- [ ] No regression in the existing closeout/verification/reconcile test suite; CHANGELOG `[Unreleased]` records all three fixes.
