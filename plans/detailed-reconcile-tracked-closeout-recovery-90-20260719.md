# Detailed plan: reconcile artifact-backed recovery from tracked closeout markdown (ah#90 / sub-fix D)

## Task
`phase-loop reconcile --verification-status passed --verification-log <closeout.md>` rejects a
**tracked, committed** closeout markdown artifact as `malformed_artifact`, because
`_validate_reconcile_verification_log` validates it as a runner-owned `verification.json`
(`ARTIFACT_NAME`). Operators whose roadmap already reached CLOSEOUT with durable committed
closeout artifacts — but no ephemeral `.phase-loop/runs/verification.json` (e.g. after an
interrupted Codex session) — cannot rehydrate completed phase state; the loop reports the roadmap
as `unplanned` and tries to re-plan from the start. Deliver #90 Desired-behavior option 1: an
explicit, **artifact-backed recovery mode** that accepts a tracked closeout markdown as recovery
evidence, provenance-labeled `tracked_closeout_artifact`, requiring an operator reason + closeout
commit SHA, verified to be **git-tracked at that commit**, and NEVER masquerading as a fresh runner
verification pass. Preserve fail-closed behavior for normal (non-recovery) acceptance.

## Research summary
Source-verified on current main. The rejection is at `cli.py:2446-2456`: when
`--verification-status passed`, reconcile calls `_validate_reconcile_verification_log(repo,
verification_log)` (`cli.py:2511`), which at `:2529` runs `validate_verification_artifact(path)` —
requiring a `verification.json` shape. A `*-closeout.md` fails → `malformed_artifact` → `return 2`.
Existing recovery scaffolding is already present: `--recovery-mode` (`cli.py:2423`) with required
`--closeout-commit` / `--repair-summary` / `--verification-status` (`:2433-2443`), and the
`manual_repair` completion event (`:2468-2502`) carrying an optional `verification_evidence` dict
(`:2483-2484`). Crucially, `reconcile.py` does **not** consume `manual_repair.verification_evidence`
(grep-confirmed) — it is recorded as audit metadata; the `status="complete"` manual_repair event
drives completion. So the fix lives at the CLI gate: for the recovery path, build a
provenance-labeled evidence dict instead of validating as `verification.json`. There is no existing
`tracked_closeout_artifact` / `manifest_lifecycle` / `runner_verification_json` provenance
vocabulary — introduce it. `ARTIFACT_NAME="verification.json"` / `LOG_NAME="verification.log"` are
in `verification_evidence.py:19-20`. NOTE: `tests/test_phase_loop_cli.py` is
`pytestmark = dotfiles_integration` (CI-excluded) — put regression tests in an UNMARKED module and
extract a testable helper.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/cli.py` (modify)
- reconcile subparser (~`cli.py:542`, near `--verification-log`) — **add** `--closeout-artifact
  <path>` — new flag: a path to a **tracked committed** closeout markdown to adopt as recovery
  evidence (distinct from `--verification-log`, which stays "runner verification.json"). Help text
  states it is artifact-backed recovery, not fresh verification.
- reconcile handler (~`cli.py:2445-2456`) — **modify** — branch the evidence resolution:
  - If `--closeout-artifact` is set: require the recovery invariants (`--closeout-commit` +
    `--repair-summary`; imply/require recovery semantics) and reject if BOTH `--verification-log`
    and `--closeout-artifact` are passed (mutually exclusive). Then build
    `verification_evidence = _validate_tracked_closeout_artifact(repo, closeout_artifact,
    closeout_commit)`; on `not ok`, print the same `code`/`artifact_path` error and `return 2`
    (fail-closed if the artifact is untracked / absent-at-commit / outside repo).
  - Else keep the existing `--verification-log` → `_validate_reconcile_verification_log` path
    unchanged (normal runner-verification acceptance stays fail-closed).
- `_validate_tracked_closeout_artifact(repo, value, closeout_commit)` — **add** — recovery-evidence
  validator. Returns a dict shaped like the existing evidence dicts but provenance-labeled:
  1. Resolve the path repo-relative (mirror `_validate_reconcile_verification_log:2514-2528`
     in-repo / outside-repo checks → `artifact_outside_repo`).
  2. Require the artifact to be **git-tracked and present at `closeout_commit`**:
     `git -C <repo> cat-file -e <closeout_commit>:<repo-relative-path>` (nonzero →
     `{ok: False, code: "closeout_artifact_not_committed", artifact_path}`). THIS is the safety
     anchor — an untracked/hand-crafted prose file cannot masquerade as completion evidence.
  3. Require non-empty content (guard a zero-byte file → `code: "empty_closeout_artifact"`).
  4. On success return `{ok: True, code: "recovered_from_tracked_closeout", provenance:
     "tracked_closeout_artifact", evidence: "recovery", artifact_path: <abs>, closeout_commit:
     <sha>}`. The distinct `provenance` + `code` ensure it can never be read as
     `runner_verification_json`.
- `manual_repair` assembly (~`cli.py:2478-2486`) — **modify** — when the evidence provenance is
  `tracked_closeout_artifact`, stamp `manual_repair["evidence_provenance"] =
  "tracked_closeout_artifact"` (audit signal) alongside the existing `verification_evidence`
  attachment, so `status`/downstream and the event ledger record that completion came from a
  tracked closeout, not a runner pass. (Confirm during implementation that `verification_status`
  recorded here is honest — recovery evidence should not silently set `passed` semantics beyond
  what the operator asserted; keep the operator-supplied `--verification-status`.)

### `phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py` (modify)
- **add** a small provenance-vocabulary constant (e.g. `EVIDENCE_PROVENANCE_TRACKED_CLOSEOUT =
  "tracked_closeout_artifact"`, `EVIDENCE_PROVENANCE_RUNNER_JSON = "runner_verification_json"`) so
  the label is defined once and reused by the CLI + any future consumer. Reason: avoid a bare
  string literal for a semantically-load-bearing provenance label. (If a more fitting home exists,
  place it there; do not introduce a new module.)

### `phase-loop-runtime/tests/test_reconcile_tracked_closeout_recovery_90.py` (create — UNMARKED)
- `test_validate_tracked_closeout_artifact_*` — **add** — unit-cover the helper against a real git
  fixture (`make_repo`): (a) tracked+committed non-empty markdown at the commit → `ok`,
  `provenance == "tracked_closeout_artifact"`, `code == "recovered_from_tracked_closeout"`;
  (b) untracked / not-present-at-commit file → `ok False`, `closeout_artifact_not_committed`;
  (c) empty tracked file → `empty_closeout_artifact`; (d) path outside repo → `artifact_outside_repo`.
- `test_reconcile_recovers_completed_phase_from_tracked_closeout` — **add** — end-to-end via the CLI
  entrypoint (invoke `main(["reconcile", …, "--closeout-artifact", <tracked closeout.md>,
  "--closeout-commit", <sha>, "--repair-summary", …, "--verification-status", "passed"])`) on a
  fixture repo with a committed roadmap + plan + closeout markdown: assert exit 0 and the phase is
  `complete` (not `unplanned`) after reconcile, and the recorded event carries
  `evidence_provenance == "tracked_closeout_artifact"`. Must be hermetic + UNMARKED.
- `test_reconcile_rejects_untracked_closeout_markdown` — **add** — the same invocation with an
  UNTRACKED markdown (or a path not present at the commit) exits non-zero with
  `closeout_artifact_not_committed` — proving prose cannot masquerade (the core safety constraint).
- `test_verification_log_path_still_rejects_markdown` — **add** — the pre-existing
  `--verification-log <closeout.md>` path still returns `malformed_artifact` (no regression to
  normal fail-closed acceptance).

## Documentation impact
- `CHANGELOG.md` — **add** — entry under `[Unreleased]`: reconcile gains `--closeout-artifact` for
  artifact-backed recovery from a tracked committed closeout markdown (provenance
  `tracked_closeout_artifact`; requires closeout commit + operator reason; never a fresh runner
  pass; untracked prose rejected). Required by the committed-diff docs-freshness CI gate.
- reconcile `--help` text for the new flag (in-code, above) — the operator-facing contract.
- `verification-evidence-contract.md` — **none** — that frozen contract governs `verification.json`
  only; this recovery evidence is a distinct, separately-labeled provenance and does not alter the
  runner artifact shape. (Confirm during implementation; if a reconcile/recovery contract doc
  exists, note the new provenance there.)

## Dependencies & order
1. Add the provenance constant(s) in `verification_evidence.py`.
2. Add `_validate_tracked_closeout_artifact` (with the git-tracked-at-commit safety check).
3. Add the `--closeout-artifact` flag + handler branch + mutual-exclusion with `--verification-log`.
4. Stamp `evidence_provenance` on the manual_repair metadata.
5. Tests (unmarked) + CHANGELOG last.

## Verification
```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python -m pytest tests/test_reconcile_tracked_closeout_recovery_90.py -q
# no regression to existing reconcile / verification-evidence CLI behavior
PYTHONPATH=src:tests python -m pytest tests/test_reconcile_verification_log.py tests/test_verification_evidence.py -q
PYTHONPATH=src:tests python -m pytest -m "not dotfiles_integration" -q   # CI-visible subset
```
Behaviors: reconcile with `--closeout-artifact <tracked closeout.md>` (+ commit + reason) marks the
phase `complete` and records `tracked_closeout_artifact` provenance; an untracked markdown is
rejected `closeout_artifact_not_committed`; `--verification-log <closeout.md>` still rejects
`malformed_artifact`; passing both flags errors. Edge cases: zero-byte closeout; closeout path
outside repo; short vs full commit SHA (`git cat-file -e` resolves both).

## Acceptance criteria
- [ ] `reconcile --closeout-artifact <tracked committed closeout.md> --closeout-commit <sha>
      --repair-summary <text> --verification-status passed` exits 0 and marks the phase `complete`,
      with the recorded manual_repair event carrying `evidence_provenance ==
      "tracked_closeout_artifact"` (not a runner-verification provenance).
- [ ] An UNTRACKED closeout markdown (or one not present at `--closeout-commit`) is rejected
      `closeout_artifact_not_committed` (prose cannot masquerade as passed verification).
- [ ] The existing `--verification-log <path>` runner-verification path is unchanged and still
      rejects a markdown as `malformed_artifact`; passing both `--verification-log` and
      `--closeout-artifact` errors.
- [ ] Regression tests live in an UNMARKED module (CI `-m "not dotfiles_integration"` runs them);
      `test_reconcile_verification_log.py` / `test_verification_evidence.py` stay green.

## Execution Policy
- execute: effort=medium, reason=safety-sensitive operator recovery surface (accepting committed
  prose as completion evidence) with a git-tracked-at-commit anchor; bounded to cli.py + one helper.
