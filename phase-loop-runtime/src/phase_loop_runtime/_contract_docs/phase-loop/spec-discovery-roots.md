# Phase-Loop Spec Discovery Roots

Standalone dotfiles phase-loop runs use root `specs/**` as the default
human-visible future-spec discovery root when no phase plan, source bundle, or
repo-local config names a different root.

Legacy or project-specific seed roots such as `Specs/**` are explicit input
roots only. They can be cited by a phase plan or validated source bundle as
seed material, but they are not automatic storage roots for future accepted
specs. Accepted future specs should normalize toward root `specs/**` for later
governed-pipeline intake.

Governed-pipeline owns canonical spec adoption, `.pipeline/specs/**`,
archive manifests, managed mirror manifests, mirror writes, source-bundle
emission, canonical refresh, source-truth reconciliation, replan, and preflight
block decisions; those decisions are governed-pipeline-owned. Dotfiles may consume validated metadata and echo
metadata-only adoption roles such as `active_canonical_spec`,
`managed_mirror_file`, `mirror_manifest`, `archive_manifest`, and
`unmanaged_spec_input`, but it does not decide whether a spec is canonical,
archived, mirrored, refreshed, or blocked.

Closeout and docs evidence stays metadata-only. Do not include raw spec bodies,
raw diffs, provider payloads, credentials, local environment values, private
evidence, or absolute private paths as runtime evidence.

Closeout may cite repo-relative spec roots, changed-path categories, SHA-256
hashes, source-bundle identity, protected-source roles, advisory reason codes,
and evidence-ref paths. Standalone root `specs/**` changes are advisory
`unmanaged_spec` future input by default. Managed root mirror and archived spec
classification comes only from validated source-bundle metadata. Dotfiles emits
`canonical_refresh_recommended` and reason codes as metadata-only hints;
governed-pipeline decides whether those hints trigger refresh, mirror, archive,
replan, or block actions.

DFADOPTBRIDGE fixtures apply the same rule to governed-pipeline v11 adoption
coverage. `dfadoptbridge_unmanaged_spec_input.json` represents root `specs/**`
as unmanaged spec input metadata, while mirror manifest and archive manifest
fixtures represent only repo-relative paths, SHA-256 fields, protected-source
roles, changed-path categories, advisory reason codes, and evidence refs.

DFADOPTSOAK keeps that boundary as a release gate. Standalone root `specs/**`
changes are advisory unmanaged-spec hints. Non-default roots such as `Specs/**`
remain non-advisory unless a phase plan, validated source bundle, or repo-local
configuration explicitly names the root. Explicit intake roles such as
`adapter_configured_intake_root` and `legacy_specs_bundle` classify those
repo-relative paths as unmanaged spec input metadata only; they do not authorize
dotfiles to read raw private source bytes or write downstream mirror artifacts.
