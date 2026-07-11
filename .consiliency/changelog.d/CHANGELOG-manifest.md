<!-- POST070FIX Phase MANIFEST — per-entry manifest validation robustness.
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

- **A single stale/renamed/missing manifest entry no longer invalidates the whole
  manifest (agent-harness#164).** Manifest-backed roadmap/plan discovery now
  validates the plan manifest **per-entry**: one bad entry (e.g. a plan file that
  was renamed or removed on disk) is skipped — treated orphaned — while the valid
  entries still resolve. Previously `discovery._phase_manifest_entries` gated on
  the all-or-nothing `validate_manifest(...).valid`, so a single bad entry hid the
  entire manifest and silently degraded discovery back to regex/glob (the manifest
  became invisible with no operator signal on the discovery path). A structural
  failure (unparseable JSON, wrong `schema_version`, or a non-array `plans`) still
  hides the whole manifest, since nothing in it is trustworthy. The skipped
  entry's operator signal (`manifest_plan_file_missing`) continues to fire
  independently from `reconcile._reconcile_plan_manifest`. The consumer
  materializes only the valid rows via `plan_manifest.valid_phase_entries`
  (index-aligned to `validate_manifest`), so even a *parse-hostile* sibling row
  (a non-object entry / `roadmap_ref` / lifecycle event that the all-or-nothing
  `read_manifest` load raises on) no longer re-hides the valid entries — closing
  the residual whole-manifest-degrade class flagged by the cross-vendor review.

- **IF-0-MANIFEST-1 — per-entry manifest validation result shape (frozen).**
  `plan_manifest.validate_manifest` now returns a `ValidationResult` with
  `structural_valid: bool` + `structural_errors` (the whole-manifest verdict) and
  `entries: tuple[EntryValidationResult, ...]` (a per-entry verdict aligned to the
  `plans` array by `index`), plus a `valid_indices()` helper. The legacy
  `valid`/`errors` attributes are preserved as backward-compatible aggregate
  properties (structural + all per-entry errors), so existing callers and the
  malformed-entry validation tests are unchanged. RUNCORE2 rebases on this shape.
