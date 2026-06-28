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
  panel; `governed` (opt-in) adds a 3-harness advisor-panel review at planning
  and pre-merge, bounded, with a non-human escalation terminal.

"Autonomous default" means the **run_mode**, not the absence of a policy — the
tiered `model_policy` is on by default; the panel is what's opt-in. See
`CHANGELOG.md` (model-routing-v1).

## License

Apache-2.0 (see `LICENSE` / `NOTICE`).
