# P4 CR reconciliation (#29) — merge state machine

Panel degraded: native Claude (repo-verified) **DISAGREE**; codex empty (transient), gemini timeout.
Findings stand on PRIMARY EVIDENCE (I confirmed #1 directly: `StateSnapshot` has no `terminal_status`).
Same root as P3: the suite is green only because every test stubs the two seams (`_reverify_fn`,
`_pr_is_open`) whose LIVE DEFAULTS are broken.

## Block-class (must fix before P5)
1. **CRITICAL — false-green killer is a no-op in production.** `_live_reverify` (train_runner.py:296)
   reads `getattr(snapshot, "terminal_status", None)` — `StateSnapshot` has NO such field (real:
   `blocker_class`, `human_required`, `closeout_terminal_status` ∈ {complete|blocked|stale_input|
   failed_verification|human_required}; models.py:207-213,2029-2064). `run_loop` returns failures AS A
   SNAPSHOT (not an exception), so the default ALWAYS returns True → a downstream that fails re-verify
   against the merged upstream is MERGED. FIX: return False when `snapshot.blocker_class is not None`
   OR `snapshot.human_required` OR `snapshot.closeout_terminal_status != "complete"`. ADD a
   live-default smoke test that does NOT stub `_reverify_fn` (a StateSnapshot with a failure signal →
   re-verify False → downstream NOT merged).
2. **CRITICAL — crash-window resume rebuilds an already-merged PR.** Step-3 (train_runner.py:559-562)
   drops a `pr_open` node when `gh pr list --state open` (git_topology.py:195) shows not-open — but a
   MERGED PR isn't "open" → the merged-but-unrecorded node is dropped + rebuilt (fresh draft for a
   landed change); P4-7 passes only via `_pr_is_open=_pr_is_open_true` (the opposite of reality). FIX:
   before dropping/rebuilding a not-open `pr_open` node, check if it MERGED (gh merged-state /
   `_pr_merged_sha_fn`); if merged, record `merged(<recovered sha>)` + add to `completed_nodes` (do
   NOT rebuild). ADD a real-seam test: not-open-but-merged → recovered, no rebuild, no duplicate.
3. **medium-high — uncaught merge failure.** No try/except around `merge_pr_fn` (train_runner.py:944)
   with `subprocess check=True` (249-256) → a real `gh pr merge` failure escapes `run_train` with no
   `blocked` ledger / `merge_halted`. FIX: wrap merge → ledger `blocked` + return `merge_halted`
   (consistent with P3 Step-4 + P4 re-verify-failure handling).

## Follow-up (not block)
Train-level review passes the panel only PR URLs + short SHAs (train_runner.py:353-381), not the
cross-repo DIFF — a functioning but weak gate. Strengthen to feed the actual linked diffs (P5 or later).

## Process note
The recurring failure mode: Sonnet implementations pass their own stubbed tests while the live default
of the stubbed seam is broken. Briefs must REQUIRE live-default smoke tests for every seam with a real
default (re-verify, pr-open/merged checks, merge), not just stub-injected tests.
