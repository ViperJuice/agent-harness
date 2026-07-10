# agent-harness

The harness-neutral **phase-loop** orchestration runtime + cross-harness workflow
skills, extracted from a private fleet repo into a public, Apache-2.0 package.

- **`phase-loop-runtime/`** — the orchestration engine + CLIs (`phase-loop`,
  `codex-phase-loop`). Deterministic; it dispatches each roadmap phase to a child
  executor (codex / claude / gemini / opencode / pi) — it isn't tied to one harness.
- **`phase-loop-skills/`** — the workflow skill bundle (phase-roadmap-builder,
  plan/execute-phase, plan/execute-detailed, skill-improvement-planner, skill-editor,
  phase-loop) with per-harness overrides for claude / codex / gemini / opencode.

## Quickstart (60 seconds)

```sh
pip install consiliency-harness   # the install-friendly front door → phase-loop-runtime
phase-loop run                    # autonomous by default: no subscription auth, no dotfiles
```

That's it. `consiliency-harness` is a thin **dependency shim** that pulls the
`phase-loop-runtime` engine (it ships no code and no console script of its own; the
obvious PyPI name `agent-harness` is an **unrelated third party**). With zero
configuration you get:

- **Zero-auth, autonomous by default.** `run_mode` is `autonomous` — the runner
  drives phases unattended, with no advisor panel and **no subscription login
  required**. Opt into governed review with `--governed`.
- **Degraded-CLI tolerant.** Missing or unbilled executor CLIs (codex / claude /
  gemini / opencode / pi) are **skipped, not fatal** — a run continues on whatever
  is installed and authed.
- **Skills already inside the wheel.** `phase-loop run` / `dry-run` resolve their
  workflow skill packs from `skills_bundle/**` shipped **in the wheel** — no
  dotfiles checkout. (A custom `PHASE_LOOP_SKILL_SOURCE_PLUGINS` provider, if you
  set one, must return **absolute** roots.)

## Install — two surfaces

**Both surfaces are supported; pick by how you drive the runner.**

**(1) Wheel-bundled skills** — the primitive itself. `pip install
consiliency-harness` (or the engine directly, `pip install phase-loop-runtime`)
gives you the `phase-loop` / `codex-phase-loop` CLIs with the workflow skills
bundled in the wheel, so `phase-loop run` works with no dotfiles. Pin a release with
`pip install consiliency-harness==X.Y.Z`.

**(2) Interactive-harness skills** — if you drive an interactive harness (Claude
Code, Codex, Gemini, OpenCode) and want the workflow skills **copied into that
harness's skill root** (`~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`,
`~/.config/opencode/skills`), run the installer script. Cross-OS (macOS / Linux),
no tailnet / 1Password / Homebrew / dotfiles clone:

```sh
git clone https://github.com/ViperJuice/agent-harness
agent-harness/install-agent-harness.sh --harness all   # claude + codex + gemini + opencode (or pick one)

# …or the one-liner:
curl -fsSL https://raw.githubusercontent.com/ViperJuice/agent-harness/main/install-agent-harness.sh | bash -s -- --harness claude
```

This installs the `phase-loop`/`codex-phase-loop` CLIs (via `uv tool`) **and** the
harness workflow skills into your skill root. It auto-resolves the current release
(no hardcoded ref); `--ref vX.Y.Z` pins one explicitly. Re-run to update.

> [!IMPORTANT]
> **The install-friendly name is `consiliency-harness`; the engine is
> `phase-loop-runtime`.** Do **not** `pip install agent-harness` — the
> `agent-harness` project on PyPI is an **unrelated third party** (coincidentally
> similar version numbers), not this CLI. `consiliency-harness` is a pure dependency
> shim (sole dep `phase-loop-runtime>=0.6.1`, zero `[project.scripts]`) that resolves
> to the real engine; `pip install phase-loop-runtime==X.Y.Z` installs that engine
> directly. Both are built by trusted-publishing release workflows under
> `.github/workflows/` (never an agent-typed credential).

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
