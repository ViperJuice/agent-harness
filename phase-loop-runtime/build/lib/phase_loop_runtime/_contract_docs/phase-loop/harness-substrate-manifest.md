# Phase-Loop Harness Substrate Manifest

This manifest defines the dotfiles paths that downstream repositories may cite
as the phase-loop harness substrate. It is a path ownership inventory, not a
schema contract. Shared artifact shapes remain frozen in
`shared/phase-loop/protocol.md`, and public runtime interfaces remain listed in
`docs/phase-loop/runtime-boundary.md`.
Harness workflow skill names, recommended install roots, Pi Agent role-style
exceptions, and direct route compatibility are frozen in the SKILLPACK manifest
at `docs/phase-loop/harness-skill-matrix.md`.
Downstream repositories may cite only the substrate paths listed here; they
must not treat broad dotfiles paths as Pipeline-owned authority surfaces.
IF-0-SUBSTRATE-1 freezes that public substrate inventory as only
`vendor/phase-loop-runtime/**`, CLI wrappers, bridge skills, shared runner
skills, protocol docs, fixtures, tests, scripts, and canonical `.phase-loop/**`
state.

## In-Scope Substrate Paths

Downstream harness and pipeline integrations may cite these paths when they
need a stable reference to the dotfiles-hosted phase-loop substrate.

### Runtime Code

- `vendor/phase-loop-runtime/src/phase_loop_runtime/**`: phase-loop CLI, discovery, runner,
  reconciliation, launch, state, profile, maintenance, and rendering code.
- `phase-loop`: neutral CLI wrapper.
- `codex-phase-loop`: Codex-compatible CLI alias.

### Bridge Skills

- `codex-config/skills/codex-phase-loop/**`: Codex TUI bridge for phase-loop
  status, resume, dry-run, and bounded runner workflows.
- `codex-config/skills/codex-plan-phase/**`: Codex phase planning workflow.
- `codex-config/skills/codex-execute-phase/**`: Codex phase execution workflow.
- `codex-config/skills/codex-phase-roadmap-builder/**`: Codex roadmap builder
  workflow.
- `codex-config/skills/codex-task-contextualizer/**`: Codex subagent briefing
  workflow.

### Shared Runner Skills

- `shared/skills/code-cli-runner/**`: shared CLI runner substrate for
  provider-specific execution skills.
- `shared/skills/codex-cli-runner/**`: Codex CLI runner guidance.
- `shared/skills/gemini-cli-runner/**`: Gemini runner guidance — drives the
  Antigravity (`agy`) CLI (the standalone `gemini` CLI was sunset; the phase-loop
  gemini executor and this runner both target `agy`).

### Protocol and Runtime Docs

- `shared/phase-loop/protocol.md`: canonical shared artifact, closeout,
  delegation, state, and execution-policy protocol.
- `docs/phase-loop/runtime-boundary.md`: public CLI, Python API, artifact, and
  state boundary.
- `docs/phase-loop/harness-skill-matrix.md`: canonical harness workflow skill
  naming matrix, Pi Agent exception list, route compatibility statement, and
  governed-pipeline `.pipeline/skills/**` namespace guard.
- `docs/phase-loop/spec-discovery-roots.md`: standalone root `specs/**`
  discovery and legacy `Specs/**` explicit-input guidance.
- `docs/phase-loop/dfadoptbridge-fixtures.md`: governed-pipeline v11 adoption
  bridge fixture matrix and SHA-256 metadata field reference.
- `docs/phase-loop/dfskillgovsoak.md`: governed bridge compatibility release
  gate, fixture classification, downstream GPSKILLSOAK evidence summary, and
  optional live proof boundary.
- `docs/phase-loop/extraction-readiness.md`: optional future extraction record.
- `docs/phase-loop/granular-execution-policy.md`: runner-owned model and
  effort policy guidance.
- `docs/phase-loop/harness-capability-matrix.md`: adapter capability and proof
  status.
- `docs/phase-loop/lane-scheduler.md`: lane-scheduler operator contract.
- `docs/phase-loop/pi-loop-control.md`: PI loop-control integration boundary.
- `docs/phase-loop/collaborator-bootstrap.md`: collaborator-safe runtime and
  harness workflow skill install surface for downstream citation.
- `docs/phase-loop/collaborator-skill-rollback.md`: rollback and uninstall
  guidance for installed workflow skill packs.

### Fixtures, Tests, and Scripts

- `tests/test_phase_loop*.py`: phase-loop runtime, bridge, policy, adapter,
  documentation, and protocol contract tests.
- `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/**`: canonical closeout and
  pipeline bridge fixtures. Downstream repositories may cite this path as
  dotfiles-owned fixture substrate.
- `scripts/smoke-codex-phase-loop`: offline phase-loop resume and reentry
  smoke.
- `scripts/smoke-phase-loop-live-adapters`: authenticated disposable live
  adapter proof script.

### Runner State Paths

- `.phase-loop/state.json`: canonical current runtime state.
- `.phase-loop/events.jsonl`: canonical append-only runtime event ledger.
- `.phase-loop/active-loop.json`: canonical active-loop metadata.
- `.phase-loop/runs/<run-id>/`: run-local context, launch metadata, logs, and
  closeout artifacts.
- `.phase-loop/tui-handoff.md`: operator-readable handoff.
- `.codex/phase-loop/**`: legacy read fallback only; new writes use
  `.phase-loop/**`.

## Out-of-Scope Dotfiles Surfaces

The paths and domains below are environment-normalization surfaces. Downstream
pipeline and harness integrations must not cite them as phase-loop substrate or
require them for phase-loop compatibility.

- Host bootstrap outside the listed CLI wrappers, including broad
  `bootstrap.sh` behavior and machine package setup.
- Shell config such as `.zshrc`, `.bashrc`, aliases, prompts, completions, and
  profile fragments.
- Terminal and Zellij config, including terminal themes, panes, layouts, and
  local session defaults.
- SSH configuration, host aliases, private keys, agent setup, and tailnet
  machine access.
- Generic 1Password setup, vault layout, personal item names, and credential
  material.
- MCP gateway setup and personal gateway server configuration outside an
  explicit phase-loop bridge contract.
- Unrelated editor configuration, formatters, language-server preferences, and
  personal IDE state.
- Private, ignored, raw-data, credential, and evidence-source files unless a
  phase plan or source bundle explicitly allowlists the exact path or glob.
- Provider payloads, raw provider transcripts, raw model outputs, and local
  environment values.

## Downstream Citation Rules

- Cite this manifest for dotfiles path ownership and in-scope substrate
  inventory.
- Cite `shared/phase-loop/protocol.md` for artifact schemas, closeout fields,
  blocker vocabulary, delegation requests, and execution-policy protocol.
- Cite `docs/phase-loop/runtime-boundary.md` for public CLI, Python API, state,
  and artifact paths.
- Cite `docs/phase-loop/harness-skill-matrix.md` for canonical workflow skill
  names, Pi Agent exceptions, direct launcher compatibility, and the
  governed-pipeline `.pipeline/skills/**` namespace boundary.
- Cite `docs/phase-loop/collaborator-bootstrap.md` when a downstream repo needs
  the collaborator-safe runtime and workflow skill installation contract
  without inheriting owner-machine bootstrap behavior.
- Treat any path not listed in the in-scope substrate section as private to the
  dotfiles repository unless a future roadmap phase adds it here.
- Treat governed-pipeline downstream mirror location
  `packages/pipeline-runtime/test/fixtures/phase-loop-bridge/` as a consumer
  path. Downstream mirror updates, closeout ingest, canonical refresh, replan, and
  preflight block decisions are governed-pipeline-owned.
- Do not treat governed-pipeline mirror paths as dotfiles write targets; the
  mirror is not a dotfiles write target.
- Treat root `specs/**` as the default standalone future-spec discovery root
  only when a plan, source bundle, or repo-local config does not override it.
  Legacy or project-specific seed roots such as `Specs/**` are explicit input
  roots only.
- Treat canonical spec adoption, archive manifests, managed mirror manifests,
  mirror writes, source-truth reconciliation, source-bundle emission, canonical
  refresh, replan, and preflight block decisions as governed-pipeline-owned.
- Treat closeout spec-root citations as metadata-only. Dotfiles may emit
  repo-relative paths, changed-path categories, hashes, source-bundle identity,
  protected-source roles, advisory reason codes, and evidence refs, but not raw
  spec bodies, raw diffs, private evidence bytes, credentials, provider
  payloads, or local environment values.
- Do not infer Pipeline ownership, protected-source authority, or permission to
  read or write `.pipeline/**`, governed-pipeline specs, Portal contracts,
  Greenfield authority files, private evidence, raw data, credentials, ignored
  outputs, provider-supplied data, legacy `.codex/phase-loop/` state, or local
  environment files from a broad dotfiles checkout path.
- Shared runner skill examples must keep Portal and Greenfield mediated through
  governed-pipeline closeout ingest, Portal projection, and Greenfield metadata-only authority refs. Governed Pipeline owns that closeout ingest boundary. Those references are not direct dotfiles write targets, and they do not make governed-pipeline, Portal, Greenfield,
  `.pipeline/**`, or a source bundle mandatory for standalone dotfiles
  planning, execution, bridge, or CLI runner use.

## DFTRUTHSOAK Evidence Surface

DFTRUTHSOAK consumes only the listed substrate paths as dotfiles-owned
evidence: local standalone closeout behavior, pipeline-required source-bundle
diagnostics, stale-input blockers, source-truth advisory hints, bridge
fixtures, and downstream mirror refs. Governed-pipeline remains the owner of
mirror updates, closeout ingest, canonical refresh, replan, and preflight block
decisions. Portal projection and Greenfield metadata-only authority refs stay
contract-mediated downstream evidence and are not direct dotfiles write
targets.

## DFADOPTBRIDGE Fixture Surface

DFADOPTBRIDGE adds adoption complete, blocked adoption metadata, stale source
bundle, stale mirror manifest, unmanaged spec input, archive manifest touched,
standalone non-adoption, deprecated-alias rejection, and redaction rejection
fixtures under `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/`. The fixtures are
metadata-only `phase_loop_closeout.v1` examples for governed-pipeline v11
adoption ingest. They may be mirrored by governed-pipeline under
`packages/pipeline-runtime/test/fixtures/phase-loop-bridge/`, but dotfiles does
not write that downstream mirror path.

Pipeline-required DFADOPTBRIDGE fixtures cite `source_bundle.path`,
`source_bundle.sha256`, `source_bundle.phase_id`,
`source_bundle.protected_sources[].path`,
`source_bundle.protected_sources[].sha256`,
`source_bundle.protected_sources[].role`, `artifacts.plan_sha256`, and
`artifacts.evidence_refs[].sha256`. Standalone non-adoption coverage omits
Pipeline-only bundle identity. The fixtures do not include raw spec bodies, raw
diffs, provider-supplied data, credentials, local environment values, private
evidence, or absolute private paths.

## DFADOPTSOAK Release-Gate Boundary

DFADOPTSOAK is the local integration soak for this substrate. It covers
standalone root `specs/**` advisory closeout hints, explicit non-default
spec-root intake metadata, unconfigured non-default root non-advisory behavior,
pipeline-required source-bundle echo, stale-input blockers, v14 bridge fixture
retention, DFADOPTBRIDGE fixture parity, and unittest-first verification.

The phase consumes governed-pipeline `CADOPTSOAK` as read-only upstream
metadata. Governed-pipeline remains responsible for mirror copies, adoption
decisions, archive creation, managed mirror refresh, source-truth
reconciliation, source-bundle emission, canonical refresh, replan, and preflight
block decisions. DFADOPTSOAK does not make governed-pipeline, Portal,
Greenfield, `.pipeline/**`, raw source evidence, raw data, credentials, local
environment values, ignored outputs, or legacy `.codex/phase-loop/**` state
dotfiles write targets.

## DFSKILLGOVSOAK Release-Gate Boundary

DFSKILLGOVSOAK is the final v16 normalization release gate for this substrate.
It consumes dotfiles bridge fixtures, expected skill-pack metadata, launcher
dry-run proof, documentation checks, and governed-pipeline GPSKILLSOAK as
read-only evidence. Governed-pipeline mirrored scenarios and dotfiles-only
scenarios are classified in `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/README.md`
and `docs/phase-loop/dfskillgovsoak.md`.

The phase does not require optional live adapter proof and does not authorize
dotfiles writes to governed-pipeline mirror fixtures, `.pipeline/**`, Portal
files, Greenfield files, raw/private evidence, credential material, local
environment values, provider-supplied data, `.phase-loop/**`, or legacy
`.codex/phase-loop/**` state.
