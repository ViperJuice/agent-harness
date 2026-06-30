# P3 re-CR reconciliation (#29) — consolidated coordinator + pin

3 legs @ 0fb1771: native Claude (repo-verified) PARTIALLY AGREE; Codex + Gemini DISAGREE.
Settled with primary evidence (the Claude leg has repo access; I confirmed the union gap directly).

## Genuinely fixed (Claude repo-verified, NOT papered over)
F1–F5 (snapshot-paths publishing, real pin consumability, exception→blocked, live-SHA resume,
head_sha separated from upstream_merge_sha). Live signatures match; pre-fix code provably fails the
new tests; workspace fail-loud + T-E preflight reject are airtight. Keep.

## Must fix before P4
1. **CRITICAL — union the channel-injected paths into the published PR.** `train_runner` sets
   `owned_paths = snapshot.phase_owned_dirty_paths or dirty_paths` and `set_upstream_ref` returns
   nothing → the coordinator-injected manifest can be DROPPED from the PR (the pin local build used
   but the remote PR omits). The pin test passes only because its stub snapshot lists `manifest.json`.
   FIX: `set_upstream_ref` returns the paths it modified; the coordinator UNIONS them into
   `owned_paths`; add a real-seam test whose `run_loop` snapshot EXCLUDES the manifest yet the PR
   still contains it.
2. **DEFER the auto-rebuild-on-resume feature** (it's non-functional + a recurring hole source).
   `publish_from_worktree` has no update-existing-PR path, so re-publishing a rebuilt downstream
   whose draft PR is open → `gh pr create` fails (blocked) or duplicates. And an out-of-band upstream
   push isn't detected. FIX: resume only skips confirmed-open nodes; if an upstream changed (rebuilt
   this run, OR live head_sha != ledger head_sha) and the downstream PR is open, **block with a clear
   manual-handling reason** — no silent skip, no broken re-publish. Document auto-rebuild as deferred.
3. **Hardening:** `file_path.resolve()` must stay within `workspace.resolve()` (reject `../`/absolute);
   raise rather than silently clobber a non-dict JSON key; reject empty/malformed keys; on a missing
   resume head SHA, block (do NOT inject a moving branch name for a pin).
