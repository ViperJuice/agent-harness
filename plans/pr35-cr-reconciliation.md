# PR #35 (#29) whole-feature CR reconciliation — false-green killer is a no-op

Native-Claude leg (repo-verified, suite-run) **DISAGREE**; codex/gemini CLI legs failed environmentally
(dotfiles #135). Finding 1 confirmed DIRECTLY by primary evidence — it BLOCKS the merge.

## BLOCK 1 (CRITICAL) — P4 re-verify never re-executes verification against the merged pin
Cross-phase P3→P4 interaction, deterministic in the default/only-CLI-reachable config:
1. `run_loop` defaults `closeout_mode="manual"` (runner.py:1115). P3's per-node `run_loop`
   (train_runner.py:736) leaves each node at `awaiting_phase_closeout`, NOT `complete`.
2. P4 `_live_reverify` (train_runner.py:289-292) re-runs `run_loop(workspace, roadmap, run_mode=...)`
   with NO verify/closeout override — defaults (`action="run"`, manual closeout). Docstring claims
   "verify mode" but no such arg is passed.
3. On `status=="awaiting_phase_closeout"` + manual mode, dispatch hits a BARE `break`
   (runner.py:1898-1899) — no executor, no closeout, NO verification. `run_loop` returns the cached
   P3 snapshot unchanged. (Closeout/verify only runs when `closeout_mode != "manual"`: runner.py:3894.)
4. Snapshot `closeout_terminal_status` ∉ bad-set, `human_required=False`, `blocker_class=None` →
   `_live_reverify` returns True (the exact INV-6 `None→True` mapping). Downstream MERGES, never
   verified against the merged pin. The `set_upstream_ref(merged_sha)` at :958 mutates a file nothing
   reads. This also HOLLOWS the "draft-pin safe under expand/contract" doc (safe only IF reverify
   catches violations — it catches nothing).
   **FIX:** `_live_reverify` must actually RE-EXECUTE the downstream's verification against the
   injected merged pin and return pass/fail — via the verification machinery
   (`verification_commands_from_plan` + `run_verification`, runner.py:5621-5652) run directly against
   the workspace, OR a `run_loop` invocation/mode that genuinely re-runs verification (NOT the
   manual-closeout no-op). Determine which actually re-executes; do not trust the docstring.

## BLOCK 2 (coverage) — zero tests exercise the real `_live_reverify`→`run_loop` path
All reverify tests stub the buggy seam (INV-6 patches `run_loop` with hand-built snapshots
test_train_invariants.py:738-741; P4 merge tests inject `_reverify_fn`). The headline safety property
has NO end-to-end coverage of its real implementation. **FIX:** a real integration test on a post-P3
workspace (node at `awaiting_phase_closeout`) where injecting a CONTRACT-BREAKING merged pin makes
`_live_reverify` return False and the downstream merge halt — and the matching pass case. Must fail
against the current no-op code.

## SHOULD-FIX 3 — multi-phase nodes publish half-built draft PRs
`run_train` calls `run_loop` once (default `max_phases=1`) and publishes gated only on
`publish_result.status=="published"` (train_runner.py:736,773-797), never on roadmap completion. A
>1-phase node ships a partial draft PR; `validate_train_loud` doesn't constrain it. **FIX:** loop to
completion (or fail-loud if the post-run snapshot isn't all-`complete`) before publishing.
