# Cross-Repo Compatibility Fixtures

This directory contains stable fixtures for native `phase_loop_closeout.v1`
closeout scenarios. Dotfiles owns these source fixtures at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/`. Governed-pipeline may mirror
them under `packages/pipeline-runtime/test/fixtures/phase-loop-bridge/` for
ingest tests, but mirror updates are governed-pipeline-owned and are not
written from this repository.

## Directory Layout

- `complete.json`: A standard successful phase execution.
- `blocked.json`: A phase blocked by a human-required input (e.g., `sandbox_command_restriction`).
- `stale_input.json`: A phase that stopped because its inputs (roadmap or plan) are stale.
- `failed_verification.json`: A phase where the implementation was attempted but verification failed.
- `human_required.json`: A phase that explicitly requires human intervention.
- `malformed.json`: An intentionally invalid closeout used to prove malformed closeout rejection.
- `dfbundlecloseout_complete.json`: DFBUNDLECLOSEOUT success with pipeline-required source bundle identity.
- `dfbundlecloseout_blocked.json`: DFBUNDLECLOSEOUT non-human contract blocker.
- `dfbundlecloseout_failed_verification.json`: DFBUNDLECLOSEOUT failed verification closeout.
- `dfbundlecloseout_human_required.json`: DFBUNDLECLOSEOUT operator-action blocker with redacted access metadata.
- `dfbundlecloseout_stale_bundle.json`: DFBUNDLECLOSEOUT stale source-bundle blocker.
- `dfbundlecloseout_standalone.json`: DFBUNDLECLOSEOUT standalone closeout without pipeline-only bundle identity.
- `dfbundlecloseout_malformed_*.json`: DFBUNDLECLOSEOUT malformed cases for missing bundle hash, deprecated root aliases, malformed nested object, invalid terminal status, and redaction rejection.
- `dfdriftsignal_complete.json`: DFDRIFTSIGNAL success with advisory impact metadata.
- `dfdriftsignal_blocked.json`: DFDRIFTSIGNAL non-human contract blocker with advisory impact metadata.
- `dfdriftsignal_standalone_advisory.json`: DFDRIFTSIGNAL standalone closeout with advisory source-truth hints.
- `dfdriftsignal_pipeline_required_advisory.json`: DFDRIFTSIGNAL pipeline-required closeout with advisory source-truth hints.
- `dfdriftsignal_canonical_refresh_recommended.json`: DFDRIFTSIGNAL fixture where changed paths recommend governed-pipeline canonical refresh.
- `dfdriftsignal_malformed_redaction.json`: DFDRIFTSIGNAL malformed fixture for forbidden impact/evidence metadata.
- `dftruthsoak_*`: DFTRUTHSOAK final soak fixtures covering standalone success, pipeline-required success, stale source bundle, mismatched protected-source hash, unauthorized protected-source write, canonical-refresh-recommended advisory, failed verification, human-required blocker, malformed closeout, and redaction violation.
- `dfadopthints_*`: DFADOPTHINTS metadata-only fixtures covering standalone unmanaged spec hints, pipeline-required adoption roles, managed mirror specs, archive manifests, canonical-refresh-recommended advisory, and redaction violation.
- `dfadoptbridge_*`: DFADOPTBRIDGE adoption bridge fixtures covering adoption complete, blocked adoption metadata, stale source bundle, stale mirror manifest, unmanaged spec input, archive manifest touched, standalone non-adoption, deprecated root aliases, and redaction violation.

The `dfbundlecloseout_*` fixtures are dotfiles-owned bridge examples that
governed-pipeline may mirror or ingest. They are not governed-pipeline state
writes and contain only metadata refs, paths, hashes, statuses, and blocker
summaries.

The `dfdriftsignal_*` fixtures add `source_truth_impact` metadata. They remain
advisory bridge examples only; governed-pipeline owns mirror updates, canonical
refresh decisions, replans, and preflight blocks.

The `dftruthsoak_*` fixtures are the final dotfiles truth-reconciliation soak
matrix. They prove the source fixtures can be mirrored downstream without
compatibility shims, but governed-pipeline remains the owner of mirror updates,
closeout ingest, canonical refresh, replan, and preflight block decisions.
Portal-facing and Greenfield-facing entries are metadata refs only.

The `dfadopthints_*` fixtures prove adoption-sensitive closeout hints without
raw source content. They use repo-relative paths, hashes, source-bundle
identity, protected-source roles, changed-path categories, reason codes, and
evidence refs only. Governed-pipeline remains the adoption, mirror, archive,
canonical refresh, replan, and block authority.

The `dfadoptbridge_*` fixtures are the governed-pipeline v11 adoption bridge
matrix. They are designed for downstream mirroring under
`packages/pipeline-runtime/test/fixtures/phase-loop-bridge/`, but that mirror
path remains consumer-owned. Pipeline-required fixtures include
`source_bundle.path`, `source_bundle.sha256`, `source_bundle.phase_id`,
protected-source `path`, protected-source `sha256`, protected-source `role`,
plan `sha256`, and evidence-ref `sha256` metadata. Standalone non-adoption
coverage keeps `source_bundle.pipeline_mode=standalone` and omits Pipeline-only
bundle identity.

## DFSKILLGOVSOAK Scenario Classification

DFSKILLGOVSOAK treats this directory as metadata-only bridge evidence. The
classification below separates governed-pipeline mirrored scenarios from
dotfiles-only coverage so downstream consumers do not infer write authority
from dotfiles fixtures.

| Scenario class | Fixtures | Boundary |
| --- | --- | --- |
| governed-pipeline mirrored | `dfbundlecloseout_*`, `dfdriftsignal_*`, `dftruthsoak_*`, `dfadopthints_*`, and valid `dfadoptbridge_*` pipeline-required fixtures | Governed-pipeline may mirror these for closeout ingest and compatibility tests, but mirror writes, closeout ingest, canonical refresh, replan, and preflight block decisions are governed-pipeline-owned. |
| dotfiles-only | `complete.json`, `blocked.json`, `stale_input.json`, `failed_verification.json`, `human_required.json`, `dfparsoak_*`, and `dfadoptbridge_standalone_non_adoption.json` | These prove local runner, lane, and standalone behavior; they are not downstream mirror requirements. |
| malformed rejection | `malformed.json`, `dfbundlecloseout_malformed_*`, `dfdriftsignal_malformed_*`, `dftruthsoak_malformed_*`, `dfadopthints_malformed_*`, and `dfadoptbridge_malformed_*` | These intentionally reject deprecated root-level automation fields, unsafe metadata, or invalid source-bundle shape and must not be presented as governed-pipeline mirrored valid cases. |
| canonical refresh advisory | `dfdriftsignal_canonical_refresh_recommended.json`, `dftruthsoak_canonical_refresh_recommended.json`, `dfadopthints_canonical_refresh_recommended.json`, `dfadoptbridge_archive_manifest_touched.json`, and `dfadoptbridge_stale_mirror_manifest.json` | These are metadata-only advisory inputs. They can recommend canonical refresh or preflight blocking, but governed-pipeline owns the decision and any resulting writes. |
| temporary legacy alias | `dfbundlecloseout_malformed_deprecated_root.json` and `dfadoptbridge_malformed_deprecated_flat_aliases.json` | Deprecated root-level aliases remain rejection coverage only. Native fixtures must keep nested `automation` fields. |
| unknown-skill coverage | `dfbundlecloseout_standalone.json` | Unknown changed-path categories remain local metadata-only advisory evidence and do not grant governed-pipeline, Portal, Greenfield, or `.pipeline/**` authority. |

## Schema: `phase_loop_closeout.v1`

All fixtures follow the `phase_loop_closeout.v1` schema as defined in `shared/phase-loop/protocol.md`.
Valid native v1 fixtures use nested `automation`, `artifacts`, `verification`,
`blocker`, `source_bundle`, and `source_truth_impact` objects. They do not
include deprecated root-level v5 automation aliases such as `status`,
`next_skill`, `next_command`, `verification_status`, `artifact`, or
`artifact_state`.

Standalone fixtures keep `source_bundle.pipeline_mode=standalone` and omit
Pipeline-only source bundle identity fields such as `source_bundle.path` and
`source_bundle.sha256`.

## Mirror Update Process

1. Update and verify the dotfiles source fixtures in this directory.
2. Run the bridge diagnostics in `vendor/phase-loop-runtime/tests/test_phase_loop_pipeline_bridge.py`.
3. Open governed-pipeline and update its mirror under
   `packages/pipeline-runtime/test/fixtures/phase-loop-bridge/` in a
   governed-pipeline-owned change.
4. Let governed-pipeline decide closeout ingest, canonical refresh, replan, and
   preflight block behavior from its own reducers.

### Example Structure

```json
{
  "schema": "phase_loop_closeout.v1",
  "phase": "PHASE_ALIAS",
  "terminal_status": "complete",
  "automation": {
    "status": "complete",
    "next_skill": "gemini-plan-phase",
    "next_command": "gemini-plan-phase plans/roadmap.md NEXT_PHASE",
    "next_model_hint": "plan",
    "next_effort_hint": "medium",
    "human_required": false,
    "blocker_class": "none",
    "blocker_summary": "none",
    "required_human_inputs": [],
    "verification_status": "passed",
    "artifact": "/path/to/artifact",
    "artifact_state": "staged"
  },
  "artifacts": {
    "plan_path": "plans/phase-plan.md",
    "plan_sha256": "...",
    "artifact_paths": {},
    "changed_paths": [],
    "evidence_refs": []
  },
  "verification": {
    "status": "passed",
    "commands": ["python3 -m unittest test_phase_loop_pipeline_bridge"]
  },
  "source_truth_impact": {
    "changed_path_boundaries": [
      {"path": "docs/phase-loop/contract-map.md", "category": "docs"}
    ],
    "canonical_refresh_recommended": true,
    "canonical_refresh_reason_codes": ["docs_source_truth_touched", "contract_refs_touched"],
    "redaction_posture": "metadata_only"
  }
}
```
