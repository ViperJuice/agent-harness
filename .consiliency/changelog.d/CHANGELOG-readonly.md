<!-- CLEANSHIP Phase 3 READONLY — status/reconcile read-only guarantee (agent-harness#62).
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

- **`phase-loop status` no longer dirties `plans/manifest.json`
  (`ViperJuice/agent-harness#62`).** A `phase-loop status` (or `handoff`) that
  reconciled the plan manifest could append a synthetic auto-import row or flip a
  missing-file entry to `orphaned`, silently mutating a tracked file on a pure read
  path. `reconcile()` now takes a keyword-only `read_only` flag (default `False`,
  so every write-intent caller is byte-for-byte unchanged) threaded into
  `_reconcile_plan_manifest`, where it skips the `append_entry` and
  `update_lifecycle` writers by construction while still surfacing the same ledger
  warnings. `status_snapshot()` defaults to `read_only=True`, and the `status` and
  `handoff` CLI commands pass it explicitly — so a read invocation leaves the
  worktree byte-clean (`git status` empty before and after). The `.phase-loop/`
  state and TUI-handoff writes were already git-excluded; the manifest was the only
  tracked-tree write on the read path.

- **Duplicate-ACCEPT drift confirmed resolved by the `#46` dedup.** The
  `_manifest_file_phase_key` dedup (keying auto-imports on normalized
  file + phase alias, not slug) already prevents a committed, accepted planner
  entry from being re-appended as a second `imported` row when reconcile rescans
  the same phase-plan file. Verified end-to-end (no monkeypatched importer) and
  shown to be load-bearing — reverting to slug-only keying re-introduces the
  duplicate. No new dedup logic was required.
