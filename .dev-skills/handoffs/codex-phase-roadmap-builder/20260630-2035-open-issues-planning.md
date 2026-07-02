# codex-phase-roadmap-builder handoff - advisor panel ownership roadmap

Generated on 2026-06-30 20:35 UTC from branch `codex/open-issues-planning-20260630`.

## Artifact

- `specs/phase-plans-v4.md`

## Scope

Roadmap covers:

- `agent-harness` #36: own the advisor-panel runtime primitive and skill surface.
- `dotfiles` #135: fix Codex/Gemini panel leg input feeding.
- model-routing-v3: fold in Gemini 3.5 Flash and Claude Sonnet 5 routing.

## Important Source Note

The user corrected the Claude Sonnet target during planning. The roadmap now treats Sonnet-family Claude panel execution as Claude Sonnet 5 and records a Claude Code `v2.1.197` or later version gate before selecting it through Claude Code.

## Phase Aliases

- `PNLFOUND`: panel contract and routing baseline.
- `PNLFEED`: inline artifact feeding and CLI leg execution.
- `PNLCLAUDE`: repo-grounded Claude leg and whole-feature review.
- `PNLSKILL`: source-first advisor-panel skill bundle.
- `PNLREDACT`: dotfiles redaction and fleet cutover.
- `PNLVERIFY`: live default verification and release closure.

## Verification Performed

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md
```

Result: `validate_roadmap: OK - 6 phase(s) in specs/phase-plans-v4.md`.

## Next Step

Plan `PNLFOUND` first. Do not start dotfiles redaction until `PNLSKILL` produces an agent-harness-owned packaged skill source.

