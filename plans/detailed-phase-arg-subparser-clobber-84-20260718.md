# Detailed plan: fix the argparse subparser clobber that drops pre-subcommand common args (ah#84)

## Task
Consiliency/agent-harness#84 — `phase-loop --phase ROOM ... run` dispatched a REPAIR of a
blocked sibling phase (SEAL) instead of the explicit independent phase (ROOM). Root cause
(empirically proven): a **pure argparse subparser clobber** in `cli.py`. Common args placed
BEFORE the subcommand token are captured by the top-level parser, then the subcommand's
`_SubParsersAction` parses into a fresh namespace and copies its defaults back — overwriting
the pre-subcommand values. `--phase ROOM` becomes `phase=None`; the runner then correctly
falls through to repairing the first blocked phase. The dispatcher is correct as-is; the fix
is **cli.py-only**.

## Research summary
Recon (empirically proven by running the parser) + direct read of `cli.py`:
- `build_parser()` (`cli.py:202-276`) declares every common arg at the **top level** with its
  normal default: `--phase` (`:214`, default None), `--roadmap` (`:213`), `--max-phases`
  (`:215`), `--model`/`--effort`/`--executor` (`:231-234`), the `--json`/`--dry-run`/… store-true
  flags, the `--allow-executor`/… append flags (default `[]`), `--heartbeat-interval-seconds`
  (default 30), etc. The top-level parser OWNS the before-subcommand position.
- `_add_common_subparser_args(sub, name=)` (`cli.py:51-105`) RE-declares those same args on
  every subcommand. Most have **no** `default=argparse.SUPPRESS`, so their subparser default
  (None / False / []) clobbers the top-level value. Only `--pipeline-mode` (`:99`) and
  `--lane-scheduler` (`:100-105`) already carry `default=argparse.SUPPRESS` — the exact proven
  pattern (and `--closeout-mode` `:264` does the same with a documented rationale at `:258-263`).
- **Proven clobber** (recon ran the parser): `['--phase','ROOM','run']` → `phase=None` (BUG);
  `['run','--phase','ROOM']` → `phase='ROOM'` (works); `['--phase','ROOM','--lane-scheduler',
  'concurrent','run']` → `phase=None, lane=concurrent` (only the SUPPRESS'd arg survives).
  `--max-phases 1` placed pre-`run` is ALSO silently dropped (same class).
- **Causal chain (runner is correct):** `phase=None` → `_select_ready_phase(...,None)`
  (`runner.py:5676`) → blocked-first fallthrough (`:5680-5682`) returns SEAL → `_prepare_phase
  _launch` sees `status=="blocked"` (`:2042`) → `launch_action="repair"` (`:2178`). No
  dispatcher change is needed.
- **SUPPRESS is safe (no KeyError):** because each arg is ALSO declared at the top level with
  a real default, `args.<name>` is always present from the top-level namespace; the subparser
  SUPPRESS only prevents the clobber. This is exactly why `--lane-scheduler`/`--closeout-mode`
  work in either option position.

## Design decision: class-fix, not just `--phase`
Fix the whole footgun class (every common subparser arg), not only `--phase` — `--max-phases`,
`--model`, `--effort`, the store-true flags, etc. are all silently dropped when placed
pre-subcommand today, the same latent bug. Use the SAME targeted `default=argparse.SUPPRESS`
pattern already applied to `--pipeline-mode`/`--lane-scheduler`, NOT
`subparsers.add_parser(argument_default=argparse.SUPPRESS)` — a subparser-wide SUPPRESS would
also strip defaults from the `execute` subcommand's `phase_arg` positional + `--bundle`/
`--output`/`--mode` (`cli.py:289-292`) and risk `args.bundle` KeyErrors. Targeted per-arg
SUPPRESS in `_add_common_subparser_args` keeps the blast radius to the common args only.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/cli.py` (modify)
- `_add_common_subparser_args` (`:51-105`) — add `default=argparse.SUPPRESS` to each common
  `sub.add_argument(...)` that lacks it (`--repo`, `--roadmap`, `--phase`, `--max-phases`,
  `--model-profile`, `--model`, `--effort`, `--executor`, `--command-name`,
  `--command-template`, `--claude-execution-mode`, `--allow-executor`, `--fallback-executor`,
  `--disable-executor`, `--require-capability`, `--json`, `--dry-run`, `--observe`,
  `--no-observe`, `--stream-output`, `--bypass-approvals`, `--heartbeat-interval-seconds`,
  `--quiet-warning-seconds`, `--quiet-blocker-seconds`, `--no-heartbeat`, `--work-unit-mode`,
  `--source-bundle`). Reason: the subparser copy must NOT re-default a value the user placed
  before the subcommand; the top-level parser owns that default. `--pipeline-mode` /
  `--lane-scheduler` already have it — leave them. Add a one-line comment referencing ah#84 +
  the existing `--closeout-mode` SUPPRESS rationale.
  - Note: `--repo` — the top-level default is `"."` (`:210`); the `closeout-drift-audit` /
    `fleet-map` branch uses `action="append"` (`:58`). SUPPRESS on the subparser copy is still
    correct (top-level `"."`/`[]` survives; a pre-subcommand `--repo X` is preserved). Confirm
    the append/scalar branch both behave under a with/without-value test.

### `phase-loop-runtime/tests/` (modify or add — a cli-parse regression module)
- Add `test_cli_common_args_survive_before_subcommand` (put in an existing cli parse test
  module, e.g. `test_phase_loop_cli*.py`, or a new unmarked `test_cli_arg_clobber_84.py`):
  build the parser via `cli.build_parser()` and assert:
  - `parse(['--phase','ROOM','run']).phase == 'ROOM'` (the reported bug);
  - `parse(['run','--phase','ROOM']).phase == 'ROOM'` (after-subcommand still works);
  - `parse(['run']).phase is None` (omitted → top-level default, NO AttributeError);
  - `parse(['--max-phases','1','run']).max_phases == 1` (sibling silent-drop);
  - `parse(['--json','run']).json is True` (store-true before the subcommand);
  - a store-true default holds: `parse(['run']).json is False`.
  Cover at least `run` and one other common subcommand (e.g. `resume`).

## Documentation impact
- `CHANGELOG.md` — add — common options (`--phase`, `--max-phases`, `--model`, `--json`, …)
  placed BEFORE the subcommand (`phase-loop --phase ROOM run`) are no longer silently reset to
  their defaults by the subcommand parser; an explicit `--phase` now reaches the dispatcher (so
  it dispatches the requested phase instead of falling through to repairing a blocked one).
- No other docs. (The `execute` positional + `--bundle`/`--output`/`--mode` are deliberately
  NOT SUPPRESS'd — out of scope.)

## Frozen-vocabulary confirmation
No frozen vocabulary or protocol contract is touched — only argparse `default=` metadata on
existing flags. No new args, no renamed args, no runner/dispatcher change.

## Out of scope (note as follow-up)
The secondary latent hardening from #84's "Expected" — when an explicit phase genuinely can't
run, `_select_ready_phase` should fail closed with a typed refusal rather than silently repair
a blocked sibling. With the CLI fixed, ROOM reaches the selector and dispatches normally, so
this is a separate hardening; file/track separately, do NOT fold in.

## Dependencies & order
None — single-function change + its parse test. No runner change, no consumer change (every
`args.<name>` reader already reads a top-level-provided value).

## Execution Policy
- execute: effort=low, reason=single-function argparse metadata change following an
  already-proven in-file pattern (`--lane-scheduler`/`--closeout-mode`) + a table-driven
  parse test.

## Verification
```bash
cd phase-loop-runtime
# the reported bug + the class, directly against the real parser
PYTHONPATH=src python -c "from phase_loop_runtime.cli import build_parser; p=build_parser(); \
a=p.parse_args(['--phase','ROOM','run']); assert a.phase=='ROOM', a.phase; \
assert p.parse_args(['run','--phase','ROOM']).phase=='ROOM'; \
assert p.parse_args(['run']).phase is None; \
assert p.parse_args(['--max-phases','1','run']).max_phases==1; \
assert p.parse_args(['--json','run']).json is True; \
assert p.parse_args(['run']).json is False; print('clobber fixed')"
# new regression test + the existing cli/parse suites (nothing pins the clobber)
PYTHONPATH=src:tests python -m pytest tests/ -q -k "cli or parse or arg or subcommand or 84"
```
Edge cases: (a) common arg before subcommand survives; (b) after subcommand still works;
(c) omitted → top-level default, no AttributeError; (d) store-true / append / typed-int args;
(e) the `execute` positional + `--bundle` are unaffected (still required/parsed).

## Acceptance criteria
- [ ] `build_parser().parse_args(['--phase','ROOM','run']).phase == 'ROOM'` (was `None`).
- [ ] `parse_args(['--max-phases','1','run']).max_phases == 1` and `parse_args(['--json','run'])
      .json is True` (the sibling silent-drops are fixed).
- [ ] `parse_args(['run']).phase is None` and `.json is False` — omitted common args fall back
      to the top-level default with no `AttributeError`.
- [ ] The new parse regression test passes; the existing cli/parse suites stay green; runner.py
      is unchanged.
