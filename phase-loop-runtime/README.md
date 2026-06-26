# phase-loop-runtime

The harness-neutral **phase-loop** orchestration runtime and CLI. It drives the
roadmap → plan → execute workflow by dispatching each phase to whatever harness you
choose (Claude / Codex / Gemini / OpenCode); the runtime itself makes no model calls.
Part of the public [`agent-harness`](https://github.com/ViperJuice/agent-harness)
monorepo (Apache-2.0).

## Install

Most users should use the repo's installer (it also installs the workflow skills):

```sh
git clone https://github.com/ViperJuice/agent-harness
agent-harness/install-agent-harness.sh --harness claude
```

To install just this runtime package directly (e.g. as a pinned dependency):

```sh
# isolated tool install:
uv tool install "git+https://github.com/ViperJuice/agent-harness@v0.1.2#subdirectory=phase-loop-runtime"
# …or into the current environment:
pip install "git+https://github.com/ViperJuice/agent-harness@v0.1.2#subdirectory=phase-loop-runtime"
```

This exposes two console scripts — `phase-loop` and `codex-phase-loop` — both calling
`phase_loop_runtime.cli:main`. The canonical protocol document ships in the wheel as
package data and is also installed to `share/phase-loop-runtime/protocol/protocol.md`.

## Roadmap validation

Lint a phase-plan roadmap spec (required headings, unique aliases, acyclic dependency
DAG, IF-gate reconciliation, lane-count hints) via the always-installed runtime:

```sh
phase-loop validate-roadmap specs/phase-plans-v1.md
# module form — only when phase_loop_runtime is on the ACTIVE python's path (a pip
# install into your env); under `uv tool install` isolation use the console command:
python3 -m phase_loop_runtime.roadmap_lint specs/phase-plans-v1.md
```

Both wrap `phase_loop_runtime.roadmap_lint` (the single source of truth). Exit 0 =
clean; non-zero prints each issue on stderr.

## Workflow skills bundle

The runtime also installs the harness-neutral workflow-skills bundle. The skill
sources live in the sibling [`phase-loop-skills/`](../phase-loop-skills) directory,
with unprefixed base directories and optional `_overrides/<harness>/` overlays.

```sh
phase-loop install --harness codex --source <path-to>/phase-loop-skills --symlink --dry-run
phase-loop install --harness codex --source <path-to>/phase-loop-skills --symlink --apply
```

Path resolution is provided by `phase_loop_runtime.skill_paths`, which keeps handoffs
repo-local, preserves harness-specific reflection roots, and documents the default
install roots for Claude, Codex, Gemini, and OpenCode.

## Closeout ownership gate & operator break-glass

When a phase verifies green but the executor touched files outside the plan's declared
owned-files globs, the **graduated closeout gate** classifies the beyond-ownership
remainder (`closeout_classifier.classify_unowned_path`):

- SAFE classes (`docs`, `plans`, `handoffs`, `config_nonsource`) auto-commit as a
  recorded `soft` exception.
- UNSAFE classes (`source`, `ci`, `secrets`, `lockfile`) block with
  `closeout_scope_violation`.

The operator escape is `phase-loop run --phase <P> --closeout-allow-unowned "<reason>"`
(also valid on `resume`/`dry-run`; reason required and non-empty; `--phase` required,
bounding the override to a single phase). It folds the `source`/`ci`/`lockfile`
remainder into the closeout commit as a recorded `break_glass` exception carrying the
reason. **`secrets` are never break-glassable** — a `.env*`/`*.pem`/`secrets/**` path
blocks regardless of the reason. See `protocol.md` → "Closeout Exceptions".

The closeout verdict is runner-authoritative: when the runner rejects a child's
closeout, the persisted `terminal-summary.json` reflects the runner's blocking verdict
(the child's self-reported `complete`/`passed` is not overlaid back), preventing a
stale "complete" summary from reconcile-skipping the work on the next run.
