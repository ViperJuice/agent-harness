# P3 coordinator — 3-agent CR reconciliation (#29)

Native Claude (repo-verified): **DISAGREE**. Gemini: **DISAGREE**. Codex: transient-fail (empty). 2-of-2 returning legs converge — block-class. The green suite hid everything because `run_loop`/`publish`/`set_upstream_ref` are all stubbed.

## Block-class (must fix before P4)
1. **Empty/blocked draft PRs (UNANIMOUS).** `train_runner.py:322` discards `run_loop`'s return; `resolve_owned_paths` defaults to `[node.roadmap]` (`:258-260`). Real `run_loop` returns `(StateSnapshot, list)` and `StateSnapshot.phase_owned_dirty_paths` (models.py:2046) IS the produced set to publish. So the PR is roadmap-only; and untracked produced code keeps the worktree dirty → `post_commit_dirty_worktree` → `publication_blocked`. Downstream then injects an upstream `head_sha` that lacks the implementation → corrupts the whole chain. **Fix:** `snapshot,_ = run_loop_fn(...)`; `owned_paths = resolve_owned_paths(node) if provided else list(snapshot.phase_owned_dirty_paths or snapshot.dirty_paths)`; add a CLI flag.
2. **Injection is hollow / silently skipped (both).** (a) Claude: `cross_repo_channel.py:159-174` — `pin` writes `.phase-loop-upstream-pin/<name>`, `workspace` writes `.phase-loop-workspace-ref/ref`, and NOTHING reads those sentinels → 2/3 channel kinds build against the absent upstream (the "hollow injection" the design panel thought IF-0-P2-2 closed). (b) Gemini: `train_runner.py:311-316` — if an upstream ref is missing from `completed_nodes` the coordinator silently skips `set_upstream_ref` and runs `run_loop` anyway. **Fix:** make `pin`/`workspace` injection actually consumed before `run_loop` (channel executor performs the real resolve) OR fail-loud "unsupported channel kind" in the MVP; and FAIL LOUD (block, don't run) if an upstream ref can't be resolved.

## High
3. **Exceptions bypass blocked-ledger; train never validated (Claude).** `set_upstream_ref`(none) and `run_loop` raise uncaught at `:316/:322` → traceback AFTER PRs open, node stuck `"running"`. CLI parses (`load_train_roadmap`) but never `validate_train_loud`. **Fix:** try/except inject+run_loop → ledger `blocked` + return blocked; call `validate_train_loud` in preflight (malformed train opens zero PRs).
4. **Resume hazards (both).** Static `rec.upstream_merge_sha` used instead of the live PR head (stale-SHA on out-of-band push); a downstream is skipped if its PR is open even when an upstream was re-built this run. **Fix:** resume reads live `head_sha`; don't skip a downstream whose upstream was (re)built this run.

## Latent P4 trap (Claude secondary)
`upstream_merge_sha` is overloaded with the DRAFT head on `pr_open` records (`train_runner.py:374`) while `train_ledger.py:32` reserves it for the MERGED sha → P4 false-green if it reads without gating `status=="merged"`. Fix the field semantics now.

## Tests
The fix MUST add tests that exercise the REAL data flow (not wholesale stubs): `run_loop` returning dirty paths → those exact paths published; channel injection actually making the upstream consumable (or failing loud); exception → `blocked`; resume against a live SHA. "It was called" assertions are insufficient.
