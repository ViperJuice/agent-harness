# Phase-Loop Extraction Readiness

This document evaluates whether the phase-loop runtime should be extracted from the `dotfiles` repository into a standalone shared agent runtime repo.

## Decision Summary

**Current Status**: Package-ready boundary, extraction not yet performed.

The phase-loop runtime has achieved sufficient architectural maturity and interface stability to be prepared for a standalone repository. The boundary between personal machine configuration (dotfiles) and shared agent orchestration (phase-loop) is now clearly defined, but this roadmap does not move code, create a submodule, or rewrite installation.
The concrete path boundary is recorded in
`docs/phase-loop/harness-substrate-manifest.md`; extraction readiness depends
on that manifest rather than an implicit dotfiles-wide ownership claim.
If extraction happens later, the standalone repository and package identity is
`phase-loop-runtime`; it keeps the Python import package `phase_loop_runtime`, the
neutral command `phase-loop`, and the backward-compatible alias
`codex-phase-loop`.

Extraction is not required for governed-pipeline v7 execution. Governed-pipeline
may continue using the current documented CLI, schema, bridge fixture, closeout,
and canonical `.phase-loop/` state surfaces before and after extraction.

## Evaluation Criteria

| Criterion | Evaluation | Status |
| :--- | :--- | :--- |
| **Interface Stability** | The `phase_loop_closeout.v1` schema and CLI surface are frozen in `shared/phase-loop/protocol.md`. | **Passed** |
| **API Boundary** | A public Python API and CLI entrypoint are documented in `docs/phase-loop/runtime-boundary.md`. | **Passed** |
| **Cross-Repo Compatibility** | Fixtures for downstream ingestion exist under `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/`. | **Passed** |
| **Independence** | Proven mechanically by the DECOUPLE Gate-A clean-room harness (`vendor/phase-loop-runtime/scripts/gate_a_cleanroom.sh`, `tests/test_gate_a_wheel_isolation.py`), which builds a wheel and installs it into an isolated venv with no dotfiles checkout reachable + user-site disabled, in BOTH plugin configurations. (1) Default fleet install (the in-tree dotfiles profile registered under `phase_loop_runtime.profile_commands`): `adoption-bundle`/`sync-skills`/`build-bundle`/`hotfix` are present (each exactly once) and `version`/`status`/`dry-run`/`execute --bundle` + the gp bridge smoke run. (2) The seam (the `profile_commands` group stripped from the installed dist-info): those commands are absent. In BOTH, no resolved import / BAML / skill-root / manifest / command path points under the dotfiles checkout, and `baml_src` resolves via `importlib.resources` package-data. DECOUPLE proves path-independence + the plugin seam in place; physically splitting the dotfiles profile (and its skill roots) into a separate distribution is EXTRACT. | **Passed (Gate A, both configs)** |
| **Installation Parity** | `bootstrap.sh` can still install phase-loop (as a submodule or package) without breaking dotfiles. | **Passed** |
| **Substrate Manifest** | `docs/phase-loop/harness-substrate-manifest.md` separates phase-loop substrate paths from host bootstrap, shell, terminal, SSH, generic 1Password, MCP gateway, and unrelated editor configuration. | **Passed** |

## Remaining Package Prerequisites

Before a standalone repository or package move, the extraction owner must:

- Use `phase-loop-runtime` as the package/repository name and choose only the
  publish strategy.
- Move only the manifest-listed substrate paths; do not include host bootstrap,
  shell profile setup, terminal setup, SSH config, generic 1Password setup, MCP
  gateway setup, unrelated editor configuration, private data, ignored raw data,
  credentials, evidence-source files, or runner state.
- Preserve the neutral `phase-loop` command and the backward-compatible
  `codex-phase-loop` alias.
- Preserve canonical `.phase-loop/` writes and legacy `.codex/phase-loop/`
  read fallback behavior.
- Preserve `phase_loop_closeout.v1`, `phase-source-bundle.v1`, and direct
  `execute <phase> --bundle --output --mode execute|repair|review` behavior.
- Add package metadata and CI that run the runtime-boundary, CLI, docs, bridge
  fixture, adapter proof, and py_compile checks without shell profile sourcing,
  dotfiles-specific credentials, provider live credentials by default, or
  ambient `~/.codex` state.

## Extraction Risks and Mitigations

| Risk | Mitigation |
| :--- | :--- |
| **Drift** | Use the standalone repo as a git submodule in `dotfiles` to ensure local testing remains valid. |
| **Dependency Management** | Maintain a strict `requirements.txt` or `pyproject.toml` that doesn't assume dotfiles environments. |
| **Secret Management** | Keep core imports, parser construction, status rendering, direct bridge diagnostics, and runtime path helpers independent of 1Password, Vercel, Supabase, and other dotfiles-specific credentials. |

## Migration Path

1. **Confirm Move Set**: Use `docs/phase-loop/harness-substrate-manifest.md` and `docs/phase-loop/runtime-boundary.md` as the exact allowlist for source, tests, fixtures, docs, scripts, and wrapper files.
2. **Move Source**: Relocate only `vendor/phase-loop-runtime/src/phase_loop_runtime/**`, `phase-loop`, `codex-phase-loop`, `shared/phase-loop/protocol.md`, `tests/test_phase_loop*.py`, `vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/**`, `scripts/smoke-codex-phase-loop`, `scripts/smoke-phase-loop-live-adapters`, and the in-scope `docs/phase-loop/*.md` runtime docs named by the manifest.
3. **Exclude Dotfiles Environment State**: Do not move host bootstrap outside the listed wrappers, shell config, terminal or Zellij config, SSH config, generic 1Password setup, MCP gateway setup, unrelated editor config, private data, ignored raw data, credentials, evidence-source files, `.phase-loop/**` runner state, or `.codex/phase-loop/**` legacy state.
4. **Submodule or Package Integration**: Integrate `phase-loop-runtime` back into `dotfiles` as a submodule or package while preserving existing bootstrap behavior.
5. **Alias Compatibility**: Maintain `phase-loop` and `codex-phase-loop` as separate backward-compatible entrypoints over the same `phase_loop_runtime.cli` parser.
6. **CI Handoff**: Run runtime-boundary, CLI, docs, bridge fixture, adapter proof, and py_compile checks in default CI without provider credentials; keep authenticated live adapter proof opt-in.

## Downstream Expectations

Downstream repositories (e.g., `governed-pipeline`) should consume the phase-loop runtime via:
- The frozen `phase_loop_closeout.v1` schema.
- The standard CLI `execute` command with `--bundle`, `--output`, and
  `--mode execute|repair|review`; unsupported modes and stale
  `phase-source-bundle.v1` inputs fail as typed non-human contract blockers.
- The cross-repo fixtures provided for integration testing.
- The path inventory in `docs/phase-loop/harness-substrate-manifest.md` when
  they need to cite dotfiles-hosted harness substrate files.

For governed-pipeline v7, extraction is a non-blocking deployment choice rather
than a prerequisite. SUBSTRATESOAK may continue against the dotfiles-hosted
runtime boundary and the same documented surfaces.

## Decision Record

- **Isolated vs. Extracted**: Extraction remains a future option to allow the agent runtime to evolve independently of personal machine setup.
- **Impact on Dotfiles**: No impact on existing installation flows. `bootstrap.sh` remains the authoritative entrypoint for local setup.
- **Current Roadmap Boundary**: RUNNERPACK records package prerequisites only; it does not create a submodule, move code, or rewrite bootstrap installation behavior.
