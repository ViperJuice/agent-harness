# Agent Harness — Team Onboarding

The **agent-harness** gives you our phase-loop workflow skills (roadmap → plan → execute,
plus one-off detailed planning and a skill editor) on your own machine. It's public and
standalone — **no VPN/tailnet, no 1Password, no shared pipeline, nothing from anyone's fleet.**

## Install (pick your harness)

Cross-platform (macOS / Linux). Replace `claude` with `codex`, `gemini`, or `opencode`:

```sh
# clone-then-run (you can read the script first):
git clone https://github.com/ViperJuice/agent-harness
agent-harness/install-agent-harness.sh --harness claude

# …or the one-liner:
curl -fsSL https://raw.githubusercontent.com/ViperJuice/agent-harness/main/install-agent-harness.sh | bash -s -- --harness claude
```

Pin a specific release for the whole team with `--ref v0.1.3`.

**Prereqs:** git, and your harness CLI already installed (Claude Code / Codex / Gemini /
OpenCode). The installer brings everything else (it installs `uv` if you don't have it).

## What you get

- The `phase-loop` runtime CLI (and `codex-phase-loop`).
- These workflow skills installed into your harness's skill root
  (`~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`, or
  `~/.config/opencode/skills`):
  - **phase-roadmap-builder** — turn a plan/conversation into a phased roadmap
  - **plan-phase** — architect one phase into parallel swim-lanes
  - **execute-phase** — run a phase's lanes to completion
  - **plan-detailed** — a single bounded change, no roadmap overhead
  - **phase-loop** — drive the loop end-to-end
  - **skill-editor** / **skill-improvement-planner** — author/refine skills

## Use it (standalone — no pipeline required)

In your harness, invoke the skills like any other slash-command/skill, e.g. in Claude Code:

```
/claude-phase-roadmap-builder   # → produces specs/phase-plans-v1.md
/claude-plan-phase P1           # → plans/phase-plan-v1-P1.md
/claude-execute-phase P1        # → runs the lanes
```

(Codex/Gemini/OpenCode use the same skills with their own prefixes.) For a small one-off
change, skip the roadmap and use `…-plan-detailed`. The runtime is harness-neutral and makes
no external calls — it just orchestrates phases on whatever harness you chose. Governed-pipeline
integration exists but is entirely optional; you don't need it.

## Update / pin / uninstall

- **Update:** re-run the installer (it fetches the pinned ref and re-applies).
- **Pin a version:** `--ref vX.Y.Z` (everyone on the same release).
- **Uninstall:** `uv tool uninstall phase-loop-runtime` and remove the
  `*-phase-*` skill symlinks from your skill root.

## Troubleshooting

- **`phase-loop: command not found`** — make sure `~/.local/bin` is on your `PATH`
  (the installer puts the CLI there); open a new shell or `hash -r`.
- **Already have a `phase-loop` on PATH?** Check which one wins with
  `command -v phase-loop` and `phase-loop --version`.

Repo + issues: <https://github.com/ViperJuice/agent-harness> · Apache-2.0.
