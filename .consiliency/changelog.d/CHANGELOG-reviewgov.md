# REVIEWGOV — advisor-board auth-aware composition + streaming verdicts

## Fixed
- **Advisor board no longer seats an unauthenticated vendor (#151, IF-0-REVIEWGOV-1).**
  `compose_review_board` now composes on `is_available ∧ auth_ok`: a
  PATH-present-but-unauthenticated vendor (e.g. a `grok` binary on PATH with no
  logged-in session) is treated as **down** — dropped and backfilled onto an
  authenticated vendor with a distinct lens, exactly like a PATH-absent vendor.
  The auth gate reuses each executor's own cached, timeout-bounded, fail-closed
  `auth_ok` (`executor_availability.auth_ok_for` over the record's
  `auth_preflight_probes`), so the board's verdict is single-sourced with the
  dispatch path's and never re-implements probing. The LIVE convening path
  (`config.load_boards` → the composed `code-review` board) is auth-aware **by
  default** (the gate short-circuits for vendors with no CLI on PATH, so a host
  without a vendor installed never shells out); pass `auth_ok=lambda _v: True` to
  isolate the availability dimension in a test.

## Added
- **Opt-in streaming verdict delivery on the shared `_run_legs_ordered`
  (IF-0-REVIEWGOV-2).** `invoke_panel` / `invoke_board` gain optional
  `on_leg_complete` (a per-leg callback) and `stream_dir` (incremental per-leg
  verdict files) parameters. When set, each leg's verdict is delivered the moment
  it lands — no head-of-line blocking on the slowest leg — while the consolidated
  return is still re-sorted to submission order. Both default to the exact
  historical behavior, so the load-bearing `invoke_panel` path and its
  byte-identical advisor-board golden are untouched. The streaming side-channel is
  fail-open (a raising callback or an unwritable `stream_dir` never breaks the pool
  or fails a leg).
- **`default_board_auth_ok`** — the reusable, fail-closed board auth probe, exported
  from `advisor_board`. It is the default gate `load_boards` applies (so the live
  `code-review` board is auth-aware by default); a caller can also inject its own
  `auth_ok` to override or, in a test, isolate the availability dimension.
