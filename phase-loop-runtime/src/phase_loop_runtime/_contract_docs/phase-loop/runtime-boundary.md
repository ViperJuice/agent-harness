# Phase-Loop Runtime Boundary

This document defines the stable public interface for the phase-loop runtime. Components not listed here are considered internal and subject to change without notice.

Path ownership for the dotfiles-hosted harness substrate is recorded in
`docs/phase-loop/harness-substrate-manifest.md`. This document defines the
public runtime contract; the substrate manifest defines which dotfiles paths
downstream repositories may cite.
IF-0-SUBSTRATE-1 keeps that citation surface limited to runtime code, CLI
wrappers, bridge skills, shared runner skills, protocol docs, fixtures, tests,
scripts, and canonical `.phase-loop/**` state.
The public skill namespace contract is recorded in
`docs/phase-loop/harness-skill-matrix.md`; it freezes harness workflow names,
Pi Agent role-style exceptions, direct route compatibility, and the
governed-pipeline `.pipeline/skills/**` boundary.

## CLI Interface

The primary entrypoint is `phase-loop` (aliased as `codex-phase-loop`).

### Commands

- `run`: Start or continue phase execution.
- `resume`: Resume the current phase after a non-terminal exit or blocker.
- `status`: Show the current phase and loop status.
- `dry-run`: Parse the roadmap and current plan without executing work.
- `maintain-skills`: Analyze and propose improvements to harness-local skills based on execution reflections.
- `sync-skills`: Audit or repair harness-local bridge skills for manual reentry.
  (DECOUPLE: provided by the dotfiles-profile plugin — see the profile-command
  note below — not by the generic runtime.)
- `state`: Inspect the current durable state.
- `handoff`: Generate a TUI handoff artifact for the current state.
- `archive-state`: Move the current state to an archive directory.
- `monitor`: Poll the runtime state and run notification commands on status changes.
- `version`: Print the installed phase-loop version. The top-level
  `--version` flag reports the same public version string.
- `execute <phase>`: Direct bridge invocation for external runners. Supports
  `--bundle <phase-source-bundle.v1>`, `--output <phase_loop_closeout.v1>`,
  and `--mode execute|repair|review`. Unsupported modes and invalid bridge
  inputs are typed non-human contract blockers.

#### Profile-command plugin seam (DECOUPLE)

The dotfiles-domain commands `adoption-bundle`, `sync-skills`, `build-bundle`, and
`hotfix` are NOT part of the generic runtime surface. They are registered only when
a profile plugin is loaded, via the `phase_loop_runtime.profile_commands`
entry-point group (declared by a profile distribution) or the
`PHASE_LOOP_PROFILE_PLUGINS` opt-in (a comma-separated list of `module:callable`
specs). A clean wheel install with no profile plugin exposes none of them; the
in-tree dotfiles profile is `phase_loop_runtime.dotfiles_profile_plugin`. Profiles
likewise contribute skill source roots via the `phase_loop_runtime.skill_sources`
group. This is the seam validated by the DECOUPLE Gate-A clean-room harness.

### Core Flags

- `--repo`: Path to the repository (default: `.`).
- `--roadmap`: Path to the roadmap file.
- `--phase`: Target phase alias.
- `--json`: Emit machine-readable JSON output.
- `--dry-run`: Enable dry-run mode.

### External-Runner Invocation Contract

This is the stable contract an external orchestrator (e.g. governed-pipeline)
binds to when it delegates a phase to the phase-loop backend. The entrypoint
name, the bridge subcommand, its argument shape, and the in/out schemas are a
stability-guaranteed surface; changes are versioned, not silent.

- **Entrypoint:** the console script `phase-loop` (legacy alias
  `codex-phase-loop`). There is no `dotfiles` command — the runtime ships
  `phase-loop`/`codex-phase-loop` only. Callers SHOULD resolve the entrypoint
  configurably (a documented default of `phase-loop`, overridable via an env var
  such as `PHASE_LOOP_CLI`) rather than hardcoding a command name, so the
  binding survives non-PATH/venv installs, pinned versions, and renames. Do not
  invent a repo-named (`dotfiles`) command.
- **Bridge command:**
  `phase-loop execute <phase> --bundle <phase-source-bundle.v1> --output <path> [--mode execute|repair|review]`.
- **Input:** a validated `phase-source-bundle.v1` artifact (`--bundle`). In
  `pipeline_required` mode the bridge fails closed unless the bundle and its
  identity/freshness inputs validate together (see Operating Modes).
- **Output:** exactly one `phase_loop_closeout.v1` JSON file written to
  `--output`, plus a typed exit code (see Exit Codes). The closeout is the
  structured result; callers read it rather than parsing the event ledger.
- **Capability / version negotiation:** callers gate on `phase-loop --version`
  (top-level `--version` reports the public version string) against a documented
  version floor before invoking; when the entrypoint is absent or below the
  floor, the caller degrades gracefully (e.g. artifact-only ingestion) instead
  of failing the run.

## Operating Modes

Standalone phase-loop use is the default local operator path. `run`, `resume`,
`status`, `dry-run`, and manual `codex-plan-phase` / `codex-execute-phase`
workflows remain valid without governed-pipeline, `.pipeline/**`, Portal,
Greenfield, a source bundle, credentials, Host bootstrap, Shell config, shell
profile sourcing, SSH setup, MCP gateway setup, generic 1Password setup,
provider-supplied payloads, or local environment contents. `.phase-loop/` is the
canonical runtime state surface; `.codex/phase-loop/` is a legacy read fallback
only when canonical state is absent.

`pipeline_optional` runs may consume Pipeline source-bundle metadata when an
external orchestrator supplies it, but missing Pipeline metadata does not make
a legacy standalone phase plan invalid.

`pipeline_required` direct bridge invocation must fail closed before execution
unless these inputs validate together: bundle path, bundle SHA-256,
Pipeline phase id or alias, roadmap identity, protected-source entries,
protected-source file hashes, and source-bundle freshness. Governed Pipeline
owns adoption, source-bundle emission, canonical refresh, replan, closeout
ingest, Greenfield reduction, and Portal projection. Governed-pipeline owns
canonical source-truth refresh, source-bundle emission,
protected-source freshness, scheduling, closeout ingest, Greenfield reduction,
and Portal projection; dotfiles consumes the supplied bundle and emits redacted
closeout metadata.
Governed-pipeline also owns canonical spec adoption, archive manifests,
managed mirror manifests, mirror writes, source-truth reconciliation, canonical
refresh, replan, and preflight block decisions. Dotfiles may echo validated
metadata-only adoption roles in closeout, but it does not decide canonical
adoption, archive membership, mirror refresh, or unmanaged spec promotion.

Standalone dotfiles discovery treats root `specs/**` as the default
human-visible future-spec intake root when no phase plan, source bundle, or
repo-local config overrides it. Legacy or project-specific seed roots such as
`Specs/**` are explicit input roots only, not automatic future-spec storage
roots. See `docs/phase-loop/spec-discovery-roots.md`.

## Python API

The following modules under `phase_loop_runtime.*` are considered public:

- `phase_loop_runtime.cli`: CLI entrypoint and parser definition.
- `phase_loop_runtime.conformance`: Named conformance-check surface for actor-side self-check and the CR-fence. Covers the SHAPE / GOVERNANCE tier (the six L0 consiliency gates, `scan_consiliency_gates`) and the CERT / SCHEMA tier (`validate_certificate` — structural conformance of a declared parity certificate to the contract-distributed `certificate` schema; NOT authority / provenance / signing, which stays in gp).
- `phase_loop_runtime.discovery`: Roadmap and repository resolution.
- `phase_loop_runtime.handoff`: TUI handoff generation and metadata.
- `phase_loop_runtime.models`: Core data models, schemas, and literals.
- `phase_loop_runtime.maintenance`: Skill maintenance and improvement logic.
- `phase_loop_runtime.observability`: Notification and monitoring utilities.
- `phase_loop_runtime.profiles`: Model and effort profile definitions.
- `phase_loop_runtime.reconcile`: State and roadmap reconciliation.
- `phase_loop_runtime.render`: Text and JSON rendering for CLI output.
- `phase_loop_runtime.runtime_paths`: Canonical `.phase-loop/` path helpers and legacy
  `.codex/phase-loop/` read-fallback helpers.
- `phase_loop_runtime.runner`: The main loop execution engine.
- `phase_loop_runtime.state_ops`: State inspection and archiving operations.
- `phase_loop_runtime.state`: Durable state persistence (JSON).

## Artifacts and Paths

- `.phase-loop/`: Canonical runtime directory.
- `.phase-loop/state.json`: Current loop state.
- `.phase-loop/events.jsonl`: Append-only event ledger.
- `.phase-loop/runs/<run-id>/`: Artifacts for a specific run.
- `.codex/phase-loop/`: Legacy compatibility root. It may be read only when
  canonical `.phase-loop/` state is absent; new writes target `.phase-loop/`.
- `plans/phase-plan-*.md`: Phase-specific execution plans.
- `specs/phase-plans-*.md`: Multi-phase roadmaps.
- `specs/**`: Standalone future-spec discovery and advisory intake root unless
  a phase plan, source bundle, or repo-local config overrides it.
- `Specs/**`: Legacy or project-specific seed input root only when explicitly
  named by a phase plan, source bundle, or repo-local config.

## Scheduler-Owned Lane Inputs

The runtime may consume scheduler-owned lane assignment metadata, but does not
own scheduling, runtime ledger policy, worktree allocation, merge reduction, or
authority digests. The accepted assignment surface is lane id, wave id,
worktree path, base SHA, isolation mode, owned files, read-only refs, harness
route, model, effort, and fallback reason. Greenfield authority refs and
governed-pipeline scheduling data remain external inputs, not dotfiles-owned
runtime authority.

`--lane-scheduler concurrent` launches scheduler-approved ready waves only
when every writer is `parallel_safe`, writable ownership is disjoint, reducer
lanes are excluded, and each writer has a `git_worktree` assignment at the
current base SHA. `--lane-scheduler serialized` and the default coarse phase
path remain compatibility modes and may run on the main worktree without
assignment metadata.

Closeout metadata may report lane identity, wave identity, worktree identity,
verification status, changed paths, source-truth impact categories, advisory
canonical-refresh reason codes, source-bundle identity, protected-source roles,
and redacted evidence refs. Evidence refs are artifact paths and digests only;
raw spec bodies, raw diffs, raw transcripts, credential material, private
files, local environment contents, and provider tokens are outside the public
runtime contract.

## Bridge Fixture Boundary

Dotfiles owns the native v1 bridge fixtures at
`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/**`. These fixtures are
metadata-only examples for `phase_loop_closeout.v1` outcomes, including
complete, blocked, stale source bundle, failed verification, human-required,
malformed, standalone-no-bundle, and canonical-refresh-recommended cases.
Native fixture JSON uses nested `automation`, `artifacts`, `verification`,
`blocker`, `source_bundle`, and `source_truth_impact` objects and does not
include deprecated root-level v5 automation aliases.
DFADOPTHINTS fixtures cover standalone unmanaged spec hints,
pipeline-required adoption roles, managed mirror specs, archive manifests,
canonical refresh recommendations, and malformed redaction rejection.

Governed-pipeline may mirror those fixtures under the downstream mirror location
`packages/pipeline-runtime/test/fixtures/phase-loop-bridge/` for its own
ingest tests. That mirror path is a downstream consumer and not a dotfiles write target.
Governed-pipeline owns mirror updates, closeout ingest, canonical refresh,
replan, and preflight block decisions; those decisions are governed-pipeline-owned.

DFTRUTHSOAK is the final source-truth reconciliation soak for this dotfiles
runtime boundary. Its evidence surface is local and metadata-only: standalone
closeout, pipeline-required closeout, stale-input blockers, source-truth
advisory hints, bridge fixtures, and downstream mirror refs. Portal-facing
evidence remains governed-pipeline projection metadata, not Portal lifecycle,
database, or contract writes from dotfiles. Greenfield-facing evidence remains
path and digest authority refs mediated by governed-pipeline, not Greenfield
reducer or authority-file writes from dotfiles.

## Skill and Prompt Guardrails

Operator skills and generated prompts must preserve standalone dotfiles use:
planning, execution, bridge, and shared CLI runner workflows remain valid
without governed-pipeline, Portal, Greenfield, `.pipeline/**`, or a source
bundle. Pipeline metadata is additive and may be copied into plan frontmatter
only from a validated source bundle or explicit `pipeline_required` run
context.
Harness workflow skill names are governed by
`docs/phase-loop/harness-skill-matrix.md`. That matrix is the public contract
for `<harness>-<workflow>` skill names, the Pi Agent role-style exceptions, and
compatibility for direct Codex, Gemini, OpenCode, and Claude Code `claude -p`
routes.
Collaborator consumption of the runtime and harness workflow skill packs is
documented in `docs/phase-loop/collaborator-bootstrap.md`. That document is the
install citation surface for downstream repos; it does not expand the runtime
boundary to host bootstrap, owner dotfiles, personal credentials, terminal
state, or direct writes to governed-pipeline, Portal, Greenfield, ReGenesis, or
`.pipeline/**`.

Portal and Greenfield examples are governed-pipeline-mediated boundaries:
Portal projection, governed-pipeline closeout ingest, and Greenfield metadata-only authority refs are not direct dotfiles write targets. Skills and
prompts must forbid inferred reads or writes to `.pipeline/**`,
governed-pipeline specs, Portal contracts, Greenfield authority files, private
evidence, raw data, raw source evidence, credentials, provider-supplied data, and legacy `.codex/phase-loop/` state unless the active plan and source bundle explicitly
own the exact path or glob.

DFPARSOAK is the release-gate exercise of this boundary. Its metadata-only
`GFPARSOAK` and `GPPARSOAK` inputs cite repo-relative receipt paths and sha256
digests only. Its local scheduler-owned git-worktree wave records Pi Agent,
Claude Code CLI, Codex, and Gemini route metadata while keeping redacted
evidence as handles or hashes and preserving no sibling-repo mutation.

## Exit Codes

- `0`: Success (or completion in `run`/`resume`).
- `1`: General failure or blocked status.
- `2`: Ambiguous roadmap selection (requires human intervention).

## Completeness for Extraction

This inventory represents the full set of public interfaces required for the vendored package `phase-loop-runtime` to operate as a future standalone repository if publication is needed later. Governed-pipeline and other downstream consumers may use the documented CLI, schema, fixture, and state surfaces through the vendored package. Any tool or service consuming phase-loop should limit its integration to these documented surfaces.
The harness substrate manifest separates those public surfaces from broader
dotfiles environment normalization such as host bootstrap, shell config,
terminal config, SSH, generic 1Password setup, MCP gateway setup, and unrelated
editor config.
DFFAKESMOKE substrate receipt fields for downstream Pipeline consumption are
documented in `docs/phase-loop/dffakesmoke-substrate-receipt.md`.

The standalone identity must preserve the Python import package `phase_loop_runtime`,
the neutral `phase-loop` command, and the backward-compatible `codex-phase-loop`
alias. `phase-loop` and `codex-phase-loop` remain separate entrypoints over the
same parser; dotfiles bootstrap installs the vendored package in editable mode.

Package-like use of the public modules, parser construction, status rendering,
direct bridge diagnostics, and runtime path helpers must work from
`vendor/phase-loop-runtime` without shell profile sourcing,
1Password, Vercel, Supabase, or other dotfiles-specific credentials, and
without ambient `~/.codex` state.

- **Contract Coverage**: 100% of the `phase_loop_closeout.v1` schema is supported.
- **CLI Parity**: The CLI provides full lifecycle management (`run`,
  `resume`, `status`, `dry-run`, `maintain-skills`, `sync-skills`, `state`,
  `handoff`, `archive-state`, `monitor`, and `version`) plus deterministic
  direct bridge invocation through `execute <phase>`.
- **Schema Stability**: All machine-readable artifacts (JSON/JSONL) follow the frozen protocol.

## DFPARSOAK Integrated Soak

DFPARSOAK publishes the final integrated substrate receipt at
`docs/phase-loop/dfparsoak-receipt.md` and the local execution runbook at
`docs/phase-loop/dfparsoak-runbook.md`. Those artifacts freeze the dotfiles
side of lane, wave, worktree, harness route, model, effort, fallback, and
redacted evidence metadata while keeping governed-pipeline as scheduler and
closeout-ingest authority.
