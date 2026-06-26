# SUBSTRATESOAK Portal v40 Projection Boundary

Portal v40 display evidence is projection-mediated. Dotfiles may publish only
Governed Pipeline-mediated metadata for the Portal display path; it does not
publish Portal runtime output.

Allowed display metadata:

- phase alias
- terminal status
- verification status
- blocker class
- human-required flag
- changed paths
- artifact refs
- evidence ref hashes
- source-bundle identity
- protected-source roles
- advisory reason codes

The BAML source at
`vendor/phase-loop-runtime/src/phase_loop_runtime/baml_src/emit_phase_closeout.baml` remains the schema
authority. This document cites schema-adjacent field names only so the Portal
projection can stay aligned with Governed Pipeline ingest without creating a
second schema.

## Denied Dotfiles Outputs

Portal routing layer, Portal display layer, database storage, projection payloads,
lifecycle state, Portal contracts, auth state, Raw evidence, Provider payloads,
credentials, local environment values, host-specific paths, `.pipeline/**`, and
legacy .codex/phase-loop/** are outside the SUBSTRATESOAK dotfiles output
surface. Any Portal-visible value must arrive through Governed Pipeline
projection using redacted metadata and hashes.
