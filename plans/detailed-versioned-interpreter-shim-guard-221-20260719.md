# Detailed plan: robust versioned/absolute suite-interpreter guard (ah#221)

## Task
`#219a`'s interpreter shim (`_build_interpreter_shim`) prepends a dir to the suite/commands
subprocess `PATH` whose **bare** `python`/`python3` resolve to a `requires-python`-satisfying
interpreter. But a suite (or a plan-level `commands` verification bullet) that explicitly invokes a
**versioned** interpreter — `python3.10` — bypasses the bare-name shim and can run GREEN under an
unsupported interpreter on a `requires-python`-constrained repo. The regex-string-scan detector
that #220 shipped for this was removed as unsound (fail-open on shell metacharacters + wired only
to `suite_command`; false-block on `pythonX.Y` string literals / env paths). Redo it robustly at
**executable-resolution** (not string) level: extend the shim to also shadow every **non-satisfying**
`python3.X` name with a **fail-closed wrapper**, applied on every path where a `requires-python`
constraint exists. Document the absolute-path (`/usr/bin/python3.10`) case as the author's explicit
declared-interpreter escape hatch (PATH-shimming inherently cannot intercept an absolute path).

## Research summary
Source-verified on current main. The shim is `verification_evidence.py:_build_interpreter_shim`
(~:174), which symlinks only `python`/`python3` to the resolved interpreter; it is applied via
`path_prepend=shim_dir` to **both** `commands` (~:386) and `suite_command` (~:390) in
`run_verification`, so — unlike the removed regex blocker — a shim-based guard has **no
commands-fail-open gap**. `_resolve_suite_interpreter` (~:191) decides the interpreter across three
spec-bearing branches: the `automation.python` **pin** branch (~:219, builds a bare-name shim), the
**`all_present_ok`** early-return (~:234, builds **no** shim when the host `python`/`python3`
already satisfy), and the **auto-resolve** branch (~:237). The versioned-name shadow must fire in
**all three**, since a `python3.10` on `PATH` bypasses a suite whose bare names are fine. Candidate
versions are `_CANDIDATE_MINORS = range(8, 15)` (3.8–3.14) and `_version_satisfies('3.X', specs)`
(PEP 440) is the existing satisfaction predicate. The regex removed cleanly (no
`_suite_argv_interpreter_blocker` remains on main). A shim intercepts at executable resolution, so it
is robust in **both** directions the regex failed: `python3.10&&pytest` still hits the wrapper, while
`python3.12 -c 'print("python3.10")'` and `PYTHONPATH=/opt/python3.10 pytest` are untouched (the shim
shadows the executable name, not string/env content).

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py` (modify)
- `_build_interpreter_shim(run_path, interpreter, shadow_names)` — **modify** — new `shadow_names`
  param (list of `python3.X` basenames to fail-close) and make `interpreter` optional. Behavior:
  create `_interp_shim/`; when `interpreter` is not None, symlink `python`/`python3` → it (existing
  fallback exec-wrapper on `OSError` unchanged); for each name in `shadow_names`, write an executable
  fail-closed wrapper (`#!/bin/sh` that prints an actionable message to stderr — e.g.
  `"<name> does not satisfy requires-python (<specs>); use bare python/python3 or an explicit absolute interpreter"` —
  and `exit 1`), `chmod 0o755`. Reason: shadow the exact executable name so a versioned invocation
  fails closed rather than running below-floor; no string parsing.
- `_resolve_suite_interpreter(repo, run_path, python_pin)` — **modify** — restructure so that
  **whenever `specs` is non-empty** the returned shim shadows non-satisfying versioned names in
  every branch. Compute once:
  `shadow_names = [f"python3.{m}" for m in _CANDIDATE_MINORS if not _version_satisfies(f"3.{m}", specs)]`
  (self-excludes the satisfying interpreter's own version). Then:
  - pin branch (satisfying pin): `_build_interpreter_shim(run_path, resolved, shadow_names)`.
  - `all_present_ok` branch: build a shim with `interpreter=None` (bare names already satisfy — do
    not redirect them) but `shadow_names` present, and return it as `shim_dir` (was `None`).
  - auto-resolve branch (host default below floor): `_build_interpreter_shim(run_path, candidate, shadow_names)`.
  - Unchanged fail-closed blockers: pin-not-found, pin-below-floor, no-satisfying-interpreter, and the
    `not specs` → host-default (no constraint, no shim). Update the Mechanism-C docstring to state
    the versioned-name fail-closed shadow + the absolute-path escape-hatch scope.

### `phase-loop-runtime/tests/test_verification_interpreter_guard_221.py` (create — UNMARKED module)
- `test_build_interpreter_shim_shadows_versioned_names` — **add** — call `_build_interpreter_shim`
  with a real satisfying interpreter (`sys.executable`) and `shadow_names=["python3.10"]`; assert the
  shim dir has an executable `python3.10` that exits non-zero with an actionable stderr, and that
  `python`/`python3` resolve to the interpreter.
- `test_resolve_suite_interpreter_shadows_nonsatisfying_in_all_branches` — **add** — drive
  `_resolve_suite_interpreter` on a repo whose `requires-python` excludes some minors (e.g. a bounded
  `>=3.11,<3.13` to exercise the advisor's upper-bound case); assert the returned `shim_dir` shadows
  the non-satisfying minors (both below AND above the range) and NOT the satisfying ones, in the
  pin / all-present-ok / auto-resolve branches. Use `sys.executable`'s own version to construct specs
  so the test is host-independent (no reliance on a real below-floor interpreter being installed).
- `test_versioned_interpreter_fails_closed_through_run_verification` — **add** — fabricate a fake
  below-floor interpreter (a small script named `python3.<nonsat>` on a temp dir) referenced by a
  `commands` entry AND a `suite_command`; run `run_verification` and assert BOTH the `commands` path
  and the `suite_command` path record a non-zero exit (the shim wrapper intercepts the fabricated
  name). Do NOT depend on a real host `python3.10`. Reason: pins the dual-path coverage that makes
  this strictly better than the removed blocker.
- `test_string_literal_and_env_path_are_not_false_blocked` — **add** — a suite
  `python3.12 -c 'print("python3.10")'` (satisfying interpreter, non-sat token only in a string) and
  a `PYTHONPATH=/opt/python3.10 python -c 'pass'` run green — proving the shim does not reproduce the
  regex false-block. (Use `sys.executable` as the satisfying interpreter.)

## Documentation impact
- `CHANGELOG.md` — **add** — entry: the versioned/absolute-interpreter guard is redone at
  executable-resolution level; a `commands`/`suite_command` entry naming a **non-satisfying**
  `python3.X` now **fails closed** via a shim wrapper (behavior change), covering both the suite and
  commands paths; string literals / env paths are no longer false-blocked; an **absolute-path**
  interpreter remains the author's explicit declared choice (documented escape hatch). Supersedes the
  #221 "documented limitation" note.
- `phase-loop-runtime/src/phase_loop_runtime/_contract_docs/runtime/verification-evidence-contract.md`
  — **modify only if it documents the interpreter mechanism** — the frozen `verification.json`
  field list is NOT touched; if the doc describes Mechanism-C interpreter resolution, add the
  versioned-name fail-closed shadow + absolute-path scope. If it only covers the artifact schema,
  `Documentation impact: none` for this file.
- `_resolve_suite_interpreter` docstring — **modify** — state the non-satisfying-versioned-name
  fail-closed shadow and the PATH-only scope (absolute path = declared choice).

## Dependencies & order
1. `_build_interpreter_shim` signature + wrappers first (call sites depend on it).
2. `_resolve_suite_interpreter` restructure (compute `shadow_names`, wire all branches).
3. Docstring + CHANGELOG.
4. Tests last.

## Verification
```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python -m pytest tests/test_verification_interpreter_guard_221.py -q
# no regression to the existing interpreter/verification suites
PYTHONPATH=src:tests python -m pytest tests/test_verification_evidence.py tests/test_preflight_verification.py -q
PYTHONPATH=src:tests python -m pytest -m "not dotfiles_integration" -q   # CI-visible subset
```
Behaviors: a fabricated non-satisfying `python3.X` in `commands` OR `suite_command` fails closed; a
satisfying bare `python`/`python3` and a satisfying versioned interpreter run green; a non-sat token
inside a string literal / env path runs green (no false-block); a repo with no `requires-python`
builds no shim. Edge cases: bounded specifier (`<3.13` shadows 3.13/3.14 too); symlink-unavailable
host (exec-wrapper fallback still shadows); the satisfying interpreter's own `python3.X` is never
shadowed.

## Acceptance criteria
- [ ] `_resolve_suite_interpreter` returns a `shim_dir` that shadows every `_CANDIDATE_MINORS`
      `python3.X` for which `_version_satisfies('3.X', specs)` is False (including above an upper
      bound), and none that satisfy — in the pin, `all_present_ok`, and auto-resolve branches.
- [ ] A `commands` entry AND a `suite_command` that name a non-satisfying versioned interpreter both
      fail closed through `run_verification` (fabricated interpreter; no dependency on a real host
      `python3.10`), pinned by an UNMARKED test.
- [ ] A non-satisfying `pythonX.Y` token appearing only in a string literal or an env-var path is
      NOT blocked (no regex false-block regression); a repo without `requires-python` builds no shim.
- [ ] CHANGELOG records the behavior change and the absolute-path escape hatch; existing
      `test_verification_evidence.py` / `test_preflight_verification.py` stay green.

## Execution Policy
- execute: effort=medium, reason=correctness-sensitive interpreter-resolution guard with a
  PEP 440 satisfaction predicate and multi-branch coverage, but bounded to one module + tests + docs.
