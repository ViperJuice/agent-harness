# phase-loop-runtime

Vendored phase-loop runtime package for this dotfiles repository.

Install locally from the repository root:

```bash
python3 -m pip install -e file://$PWD/vendor/phase-loop-runtime
```

The editable install exposes two console scripts:

- `phase-loop`
- `codex-phase-loop`

Both commands call `phase_loop_runtime.cli:main` and keep the existing parser
and version behavior. The canonical protocol document is bundled at
`protocol/protocol.md`.

This package is vendored for v18 and is not published to PyPI in this phase.
