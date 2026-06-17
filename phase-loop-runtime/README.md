# phase-loop-runtime

Vendored phase-loop runtime package for this dotfiles repository.

Install the console scripts from inside the package directory:

```bash
cd vendor/phase-loop-runtime && python3 -m pip install . --no-build-isolation
```

This is a **non-editable** install on purpose. Editable (`-e`) mode is rejected
by the build backend under pip's build isolation, and even when forced it drops
only a PEP 660 `.pth` finder — which the shell-out tests cannot import after they
swap `HOME` (they extend `PYTHONPATH` with user-site, and `PYTHONPATH` entries do
not activate `.pth` finders). A plain install copies the current source into
site-packages, so the console scripts and the test suite both see live code.

Because the install is a snapshot, **re-run the install command after editing the
vendored source** so the `phase-loop` entry point picks up your changes.

The install exposes two console scripts:

- `phase-loop`
- `codex-phase-loop`

Both commands call `phase_loop_runtime.cli:main` and keep the existing parser
and version behavior. The canonical protocol document is bundled at
`protocol/protocol.md`.

This package is vendored and is not published to PyPI in this phase.

## Closeout ownership gate & operator break-glass

When a phase verifies green but the executor touched files outside the plan's
declared owned-files globs, the **graduated closeout gate** classifies the
beyond-ownership remainder (`closeout_classifier.classify_unowned_path`):

- SAFE classes (`docs`, `plans`, `handoffs`, `config_nonsource`) auto-commit as a
  recorded `soft` exception.
- UNSAFE classes (`source`, `ci`, `secrets`, `lockfile`) block with
  `closeout_scope_violation`.

The operator escape is `phase-loop run --phase <P> --closeout-allow-unowned
"<reason>"` (also valid on `resume`/`dry-run`; reason required and non-empty;
`--phase` required, which bounds the override to a single phase). It folds the
`source`/`ci`/`lockfile` remainder into the closeout commit as a recorded
`break_glass` exception carrying the reason. **`secrets` are never
break-glassable** — a `.env*`/`*.pem`/`secrets/**` path blocks regardless of the
reason. An empty reason yields `operator_override_missing_reason`. See
`protocol/protocol.md` → "Closeout Exceptions" for the full contract.

### Closeout verdict is runner-authoritative (issue #38)

When the runner rejects a child's closeout (e.g. a `produced_if_gates`
`contract_bug`), the persisted `terminal-summary.json` reflects the runner's
**blocking** verdict — the child's self-reported `complete`/`passed` is not
overlaid back onto it. This prevents the next run from reading a stale "complete"
summary and reconcile-skipping the work. The child's claim is preserved in the
event ledger (`child_automation`) for forensics.

## Roadmap validation

Lint a phase-plan roadmap spec (required headings, unique aliases, acyclic
dependency DAG, IF-gate reconciliation, lane-count hints) via the always-installed
runtime — no skill-bundle script required:

```bash
phase-loop validate-roadmap specs/phase-plans-v1.md
# module form — only when phase_loop_runtime is on the ACTIVE python's path
# (e.g. a pip install into your env); does NOT resolve under `uv tool install`
# isolation, where you should use the console command above:
python3 -m phase_loop_runtime.roadmap_lint specs/phase-plans-v1.md
```

Both wrap `phase_loop_runtime.roadmap_lint` (the single source of truth). The
skill bundle's `phase-roadmap-builder/scripts/validate_roadmap.py` is a thin shim
over it. Exit 0 = clean; non-zero prints each issue on stderr.

## Skills Bundle

The vendored runtime also exposes the harness-neutral Skills Bundle installer.
Workflow skill sources live under `vendor/phase-loop-skills/` with unprefixed
base directories and optional `_overrides/<harness>/` overlay directories.

Use `phase-loop install` to install harness-prefixed workflow skills:

```bash
phase-loop install --harness codex --source vendor/phase-loop-skills --symlink --dry-run
phase-loop install --harness codex --source vendor/phase-loop-skills --symlink --apply
```

Path resolution is provided by `phase_loop_runtime.skill_paths`. The resolver
keeps handoffs repo-local, preserves harness-specific reflection roots, and
documents the default install roots for Claude, Codex, Gemini, and OpenCode.
