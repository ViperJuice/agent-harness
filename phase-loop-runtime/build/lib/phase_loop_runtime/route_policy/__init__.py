"""Language-neutral Claude route-selection policy artifacts.

This package is a marker so the golden route-selection fixtures ship as
importable resource data (`importlib.resources`). The fixtures
(`fixtures/route_selection.golden.json`) are the frozen, cross-language
contract that BOTH the Python runtime (`resolve_claude_route` +
`claude_route_billing_posture` / `claude_route_fallback_posture` in
`phase_loop_runtime.launcher`) and governed-pipeline's TS `resolveClaudeRoute`
(gp #25) validate against. See `docs/phase-loop/route-policy-spec.md` for the
language-neutral decision table.
"""
