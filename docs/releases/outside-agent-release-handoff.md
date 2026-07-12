# Outside-Agent Release Handoff

This handoff records release-preparation evidence for the outside-agent
conformance runtime. It is metadata-only and does not publish a package, create a
tag, dispatch a workflow, edit governed-pipeline, or claim production merge
enforcement is live.

## Package Identity

- Package: `phase-loop-runtime`
- Version: `0.7.2`
- Runtime `phase_loop_runtime.__version__`: `0.7.2`
- Version pin prepared for downstream pinning: `phase-loop-runtime==0.7.2`
- Console scripts: `phase-loop`, `codex-phase-loop`

## Validator Identity

- Governed-pipeline validator authority: `governed_pipeline_validator`
- Validator version: `0.7.2`
- Validator command: `phase-loop outside-agent-validate`
- Advisory preflight command: `phase-loop outside-agent-preflight`
- Advisory output remains supporting evidence only; governed-pipeline remains
  the authoritative acceptance boundary.

## Contract Pin

- `contract_package`: `consiliency-spec`
- `contract_version`: `0.1.0`
- `contract_git_sha`: `c1085483a015ae61aba5fa3064fbd3a96ccc9a33`
- `schema_version`: `outside_agent_submission.v0.1`
- `verdict_schema_version`: `outside_agent_route_verdict.v0.1`
- `vector_manifest_name`: `test-vectors/outside-agent/manifest.json`
- `vector_manifest_hash`: `33cdb767831ee8eaf45961cdb7ccb5b8b21ac69ec054b0da7304e08a2d06434e`
- `source_owner`: `Consiliency/spec`
- `redaction_posture`: `metadata_only`

## Release-Check Evidence

- `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_real_output.py tests/test_outside_agent_advisory.py -q` passed with 14 tests.
- `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md` passed for 5 phases.
- `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q` passed with 39 tests.
- `python -m pip install --upgrade build` completed and installed `build` 1.5.1 in the user environment.
- `python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime` succeeded.
- `git status --short` after the release-check lane showed the new release-surface test file as the only tracked dirty path at that point.

## Package Surface Inventory

- Wheel artifact: `phase_loop_runtime-0.7.2-py3-none-any.whl`
- Sdist artifact: `phase_loop_runtime-0.7.2.tar.gz`
- Wheel top-level entries: `phase_loop_runtime`, `phase_loop_runtime-0.7.2.data`, `phase_loop_runtime-0.7.2.dist-info`
- Wheel file count: 401
- Sdist top-level entries: `MANIFEST.in`, `PKG-INFO`, `README.md`, `protocol`, `pyproject.toml`, `setup.cfg`, `src`, `tests`
- Sdist file count: 849
- Wheel entry points: `phase-loop = phase_loop_runtime.cli:main`; `codex-phase-loop = phase_loop_runtime.cli:main`
- Runtime plugin entry points: `dotfiles = phase_loop_runtime.dotfiles_profile_plugin:register_profile_commands`; `dotfiles = phase_loop_runtime.skill_sources_plugin:register_skill_sources`

## Governed-Pipeline Pinning

Governed-pipeline should consume this runtime as an authoritative validator only
after a maintainer publishes or pins it. Pin the released package
`phase-loop-runtime==0.7.2`, then call:

```bash
phase-loop outside-agent-validate path/to/outside-agent-submission.json \
  --output outside-agent-verdict.json \
  --submitted-ref src/agent.py \
  --submitted-ref docs/evidence.md
```

The governed-pipeline side should also pin the Consiliency/spec contract fields
listed above, including `contract_version`, `contract_git_sha`,
`schema_version`, `verdict_schema_version`, and `vector_manifest_hash`.

## Outside-Agent Advisory Preflight

Outside-agent producers can run local advisory preflight before submitting work:

```bash
phase-loop outside-agent-preflight path/to/outside-agent-submission.json \
  --output outside-agent-advisory.json
```

The advisory result can catch metadata-only schema, redaction, provenance, and
digest issues early. It is not acceptance authority and must not be treated as a
merge verdict.

## Release Step — bump `RELEASE_PIN` in lockstep (PUSHFLOW)

- When cutting a release, bump the checked-in `RELEASE_PIN` to the new
  `vX.Y.Z` **in the same release commit** as the `phase-loop-runtime` package
  version (they are kept equal by the release-consistency guard in
  `tests/test_release_pin_autotrack.py`).
- `install-agent-harness.sh` pins the persistent clone at
  `~/.local/share/agent-harness` (or `$AGENT_HARNESS_HOME`) to `RELEASE_PIN`. If
  `RELEASE_PIN` is not bumped, previously installed clones stay behind (the live
  gap where clones sat at `0.6.0` under `RELEASE_PIN=v0.7.0`).
- `phase-loop doctor` surfaces a `stale` BOM verdict for
  `pinned agent clone (~/.local/share/agent-harness)` when a local clone is behind
  `RELEASE_PIN`. The remediation is to re-run `install-agent-harness.sh` (which
  runs `git -C ~/.local/share/agent-harness fetch + checkout $REF`). The check is
  advisory (WARN, never gating).

## Maintainer Dispatch Boundary

- The package is not published from this handoff.
- A git tag is not created from this handoff.
- The PyPI workflow is not dispatched from this handoff.
- Production governed-pipeline enforcement is not claimed by this handoff.
- Maintainers own publish, tag, workflow dispatch, and downstream production pin
  rollout after reviewing this evidence.
