# agent-harness

The harness-neutral **phase-loop** orchestration runtime + cross-harness workflow
skills, extracted from a private fleet repo into a public, Apache-2.0 package.

- **`phase-loop-runtime/`** — the orchestration engine + CLIs (`phase-loop`,
  `codex-phase-loop`). Deterministic; it dispatches each roadmap phase to a child
  executor (codex / claude / gemini / opencode / pi) — it isn't tied to one harness.
- **`phase-loop-skills/`** — the workflow skill bundle (phase-roadmap-builder,
  plan/execute-phase, plan/execute-detailed, skill-improvement-planner, skill-editor,
  phase-loop) with per-harness overrides for claude / codex / gemini / opencode.

## Install

Cross-OS (macOS / Linux), no tailnet / 1Password / Homebrew / dotfiles clone:

```sh
git clone https://github.com/ViperJuice/agent-harness
agent-harness/install-agent-harness.sh --harness all   # claude + codex + gemini + opencode (or pick one: claude|codex|gemini|opencode)

# …or the one-liner:
curl -fsSL https://raw.githubusercontent.com/ViperJuice/agent-harness/main/install-agent-harness.sh | bash -s -- --harness claude
```

This installs the pinned `phase-loop`/`codex-phase-loop` CLIs (via `uv tool`) and the
harness workflow skills into your harness skill root (`~/.claude/skills`,
`~/.codex/skills`, `~/.gemini/skills`, `~/.config/opencode/skills`). `--ref vX.Y.Z`
pins a release (default: the latest stable). Re-run to update.

A plain `pip install "git+…/agent-harness@<tag>#subdirectory=phase-loop-runtime"`
also works out of the box: the assembled workflow skill bundle ships **inside** the
wheel, so `phase-loop run`/`dry-run` resolve their skill packs with no dotfiles
checkout. (A custom `PHASE_LOOP_SKILL_SOURCE_PLUGINS` provider, if you set one, must
return **absolute** roots.)

> [!IMPORTANT]
> **PyPI package name is `phase-loop-runtime` — not `agent-harness`.** The
> `agent-harness` project on PyPI is an **unrelated third party** (coincidentally
> similar version numbers); do **not** publish this runtime there, and do **not**
> `pip install agent-harness` expecting this CLI. The published wheel is
> `pip install phase-loop-runtime==X.Y.Z` (built by `.github/workflows/publish-pypi.yml`).

## Outside-agent conformance

Outside-agent release preparation is documented in
`docs/releases/outside-agent-release-handoff.md`, with package/check evidence,
contract/vector pin metadata, governed-pipeline validator pin instructions, and
the maintainer-owned publish boundary. The user-facing conformance contract and
advisory-vs-authoritative split remain in `docs/outside-agent-conformance.md`.
The advisory `outside-agent-preflight` command is available for local producer
checks; governed-pipeline must use `outside-agent-validate` for authoritative
validator evidence after maintainers publish or pin the runtime.

## Autonomy & review gates

The runner is built to drive phases **unattended**. Closeout review gates
(doc-delta, verification-evidence, visual-evidence) default to recording a
finding and continuing — they never stall a run or require a human. Dial
strictness with `PHASE_LOOP_REVIEW`:

- `warn` (default) — record findings to the closeout, the loop continues.
- `block` — a finding refuses `complete` (with an agent-recoverable, non-human
  blocker; the agent fixes it by updating docs, attaching a verification log or
  screenshot, or recording a justified opt-out).
- `off` — skip the gates entirely.

For periodic human review, bound the run (`--max-phases N`) and read the findings
summary between runs rather than blocking mid-loop. See `CHANGELOG.md` (rigor-v1)
for the full list of gates.

## Model routing (two axes)

Model selection has two independent axes:

- **`model_policy`** — *what model*. A `model_class` role layer
  (`planner`/`implementer`/`worker`) resolves to a concrete model per executor.
  This repo ships a default (planning at `max` effort, implementation at the
  implementer model); a checkout with no policy keeps the legacy resolution.
- **`run_mode`** — *how governed*. `autonomous` (default) runs unattended with no
  panel; `governed` (opt-in, `--governed` / `PHASE_LOOP_RUN_MODE=governed`) adds a
  codex+gemini advisor-panel review at planning and pre-merge, bounded, with a
  non-human escalation terminal. **Live on the serial path** (model-routing-v2);
  concurrent-wave dispatch is not governed yet.

"Autonomous default" means the **run_mode**, not the absence of a policy — the
tiered `model_policy` is on by default; the panel is what's opt-in. See
`CHANGELOG.md` (model-routing-v1).

## Cross-repo release train

The `phase-loop run-train` coordinator orchestrates changes that span multiple
repos in a single atomic train: draft PRs open in topo order, a train-level
governed review gates the entire diff, then nodes merge sequentially with each
downstream re-verified against the upstream **MERGED SHA** before its own merge.

### Quick start

```sh
phase-loop run-train --train train-roadmap.md            # P3: open all draft PRs
phase-loop run-train --train train-roadmap.md --governed # P4: review + sequential merge
```

### Train roadmap format

```markdown
# Release Train: my-feature

## Nodes

### Node: repo-a / specs/plan-a.md
**Depends on:** (none)
**Channel:** (none)

### Node: repo-b / specs/plan-b.md
**Depends on:** repo-a / specs/plan-a.md
**Channel:** submodule path=vendor/repo-a
```

Channel types: `submodule path=<path>` or `pin file=<file> key=<yaml-key>`.

### Safety invariants (structural, not advisory)

- **Zero PRs on preflight failure** — preflight runs on all repos before any PR opens.
- **No merge before train approval** — governed review gates the full diff;
  a rejection is a non-human terminal (`human_required=False`).
- **False-green killer** — before each downstream merge, `set_upstream_ref` is
  called with the upstream MERGED SHA (not the draft SHA) and the downstream is
  re-verified. Order is asserted in `tests/test_train_invariants.py`.
- **Forward-only** — a downstream re-verify failure halts the train; upstream
  merges stay merged.
- **Train state off `.phase-loop/`** — the ledger is never written inside any
  repo's `.phase-loop/` directory.
- **Crash-resumable** — re-run with the same `--train` file; the ledger drives
  which nodes are skipped (already merged) or retried (blocked).
- **Autonomous boundary** — without `--governed`, the coordinator stops at
  `drafts_open`; cross-repo merges are never auto-merged.

### Documented limitation

The merged downstream PR carries the **draft-time upstream pin**, not the
merge-commit SHA. Use expand/contract upstream contracts (additive first,
backward-compatible) so sequential merges are safe. See
`_contract_docs/phase-loop/protocol.md` ("Cross-Repo Release Train") for the
full protocol spec and ledger shape.

## License

Apache-2.0 (see `LICENSE` / `NOTICE`).
