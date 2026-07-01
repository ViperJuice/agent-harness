---
name: codex-advisor-panel
description: Run a cross-vendor advisor panel through the agent-harness runtime primitive when a high-stakes change needs independent review evidence.
---

# Advisor Panel

Use this skill when a plan, implementation diff, release closeout, or other high-stakes artifact needs an independent cross-vendor review panel.

## Source Of Truth

The advisor-panel implementation is owned by `agent-harness`:

- Runtime primitive: `phase_loop_runtime.panel_invoker`
- Entry points: `PanelRequest`, `available_panel_legs`, and `invoke_panel`
- Governed workflow integration: phase-loop governed review/pre-merge paths

Do not call dotfiles advisor-panel scripts, copy provider-specific shell scripts, or introduce a separate implementation in the skill body. The skill is a thin operator guide over the runtime primitive.

## Use

1. Prefer the repo's governed phase-loop path when reviewing phase execution or pre-merge work.
2. For a standalone smoke or diagnostic, build a metadata-only review artifact and call `phase_loop_runtime.panel_invoker.invoke_panel`.
3. Require every leg to end with `AGREE`, `PARTIALLY AGREE`, or `DISAGREE`.
4. Treat `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE` as structured evidence, not successful reviews.
5. Keep provider API keys out of the environment; the runtime strips known API-key variables and uses local subscription CLIs.

## Standalone Smoke Shape

```python
from phase_loop_runtime.panel_invoker import available_panel_legs, invoke_panel

artifact = "Review artifact with acceptance criteria and verification evidence."
panel = invoke_panel(artifact, available_panel_legs())
for leg in panel.legs:
    print(leg.leg, leg.status)
```
