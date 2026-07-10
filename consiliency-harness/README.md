# consiliency-harness

**The install-friendly front door to the `phase-loop-runtime` orchestration
primitive.** This is a pure dependency shim — it ships no code and no console
script of its own. Installing it pulls the real engine:

```sh
pip install consiliency-harness
phase-loop --version        # provided by phase-loop-runtime (the shim adds nothing)
phase-loop run              # autonomous by default, no subscription auth required
```

## Why this package exists

The obvious PyPI name `agent-harness` is taken by an **unrelated third party**, so
`pip install agent-harness` installs someone else's code. The orchestration
runtime is published as [`phase-loop-runtime`](https://pypi.org/project/phase-loop-runtime/).
`consiliency-harness` is the discoverable name that depends on it, so newcomers can
`pip install consiliency-harness` and get the real thing.

- **Sole dependency:** `phase-loop-runtime>=0.6.1`.
- **Zero `[project.scripts]`:** the shim declares no entry point, so it cannot
  shadow the runtime's own `phase-loop` / `codex-phase-loop` commands.
- **Pure metadata:** the wheel contains no importable module — only the
  dependency edge to the runtime.

## What you get

Everything is provided by `phase-loop-runtime`. See the
[agent-harness README](https://github.com/ViperJuice/agent-harness#readme) for the
full quickstart, including the wheel-bundled skills path (`phase-loop run` with no
dotfiles) and the interactive-harness skills path.

## License

Apache-2.0 (see the [agent-harness repository](https://github.com/ViperJuice/agent-harness)).
