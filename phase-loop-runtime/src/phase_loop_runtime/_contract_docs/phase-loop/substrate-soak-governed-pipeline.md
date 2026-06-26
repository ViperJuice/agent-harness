# SUBSTRATESOAK Governed Pipeline v25 Compatibility

SUBSTRATESOAK proves compatibility through dotfiles-local fixtures and
metadata-only closeout examples. The fixture matrix lives at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/substratesoak_*.json`,
and dotfiles owns only the local fixture source plus the tests that validate
that source.

Governed Pipeline owns mirror regeneration. Governed Pipeline owns closeout ingest.
Dotfiles does not take ownership of downstream mirror refresh,
canonical adoption, archive, replan, preflight, or Portal projection decisions.
The compatibility receipt records no sibling repository mutation.

## Metadata Contract

Governed Pipeline may consume only metadata fields from the local fixture
matrix:

- pipeline mode
- source bundle path
- source bundle SHA-256
- phase id
- protected-source roles
- artifact paths
- evidence refs
- changed-path categories
- verification status
- blocker class
- canonical-refresh advisory reason codes

Those fields are identifiers, hashes, classifications, or redacted references.
They are not permission to read source bundle bytes, protected source contents,
private evidence, or downstream runtime state.

## Boundaries

The downstream mirror location
`packages/pipeline-runtime/test/fixtures/phase-loop-bridge/`, governed-pipeline specs,
`.pipeline/**`, sibling repositories, Raw evidence, Provider payloads,
credentials, and local environment values are outside the dotfiles write and
read boundary for this phase. Governed Pipeline remains the owner for any
derived mirror copy or closeout ingestion behavior.
