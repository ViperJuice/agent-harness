# Detailed plan: clamp grok effort at the grokexec CLI boundary (ah#224)

## Task
Consiliency/agent-harness#224 — the grokexec/launcher path passes the requested effort
**raw** to `grok --reasoning-effort`, but the grok CLI accepts only `high | medium | low`
(verified live in ah#222: `--reasoning-effort max` → "unknown effort level 'max'; use one
of: high, medium, low"). So an **explicit** `max` / `xhigh` / `minimal` grokexec run (all in
`NORMALIZED_EFFORT_LEVELS`) crashes grok. Clamp those to grok's valid tokens at the CLI
boundary — mirroring how codex already clamps `max→xhigh` and the panel path (#222) clamps
`max→high`. Default path (`medium`) is already valid, so this is **explicit-effort-only**
(lower urgency, as the issue notes).

## Research summary
One Explore pass (citations verified in it):
- **The clamp must be APPLIED — fixing the map alone is a non-fix.** `selected_effort` rides
  `ModelSelection.effort` → `LaunchSpec.selected_effort` (`launcher.py:1273`) and the grok
  argv sets `--reasoning-effort` to `selection.effort` **verbatim** (`launcher.py:748-749`),
  with a FALSE comment at `launcher.py:719-723` ("passes straight through … no clamp needed,
  unlike codex's max→xhigh").
- **The `effort_map` is DOUBLE-DEAD for grok.** Its only reader is
  `profiles.py:229` (`capability.effort_map.get(...)`), inside the
  `unsupported_policy_behavior=="fallback"` branch of `normalize_provider_effort`
  (`profiles.py:204-232`), reachable only when the effort is NOT in `supported_efforts`. grok
  declares `supported_efforts=_ALL_EFFORTS` (all 6 levels, `capability_registry.py:494`), so
  every request hits the "supported" early-return (`profiles.py:223`) and the effort_map
  fallback is never reached. The identity `effort_map={e:e …}` (`capability_registry.py:497`)
  + its "accepts a superset … no clamp" note (`:499`) are dead **and** false.
- **Codex is the exact model to mirror (NOT the effort_map).** codex uses a bespoke
  CLI-boundary dict `_CODEX_CLI_EFFORT_OVERRIDES={"max":"xhigh"}` + `_codex_cli_effort()`
  (`launcher.py:369-378`), applied at `launcher.py:397`; codex's own effort_map is likewise
  dead. The panel sibling is `advisor_board/harness_mapping.py:73` `_GROK_EFFORT` (`max→high`)
  — **but it lacks `minimal`/`xhigh` keys**, so a naive copy would KeyError; the grokexec
  override must cover all three invalid levels.
- Types/tests: `NORMALIZED_EFFORT_LEVELS=("minimal","low","medium","high","xhigh","max")`
  (`models.py:57`); `ProviderPolicyCapability` fields at `models.py:629-652`. Specs are built
  via `resolve_profile_for_executor(action=…, executor="grok")` → `build_grok_command(cwd,
  selection, …)` (`test_grokexec.py:47,402-403,452`); the passthrough assertion is
  `test_grokexec.py:108-109` (`cmd[cmd.index("--reasoning-effort")+1] == spec.selected_effort`,
  + narrative at `:87`).

## Design decision: CLAMP (mirror codex), do not reject
An explicit `max` should run at grok's ceiling (`high`), consistent with ah#222 (panel
`max→high`) and codex (`max→xhigh`) — NOT fail closed. Therefore **keep grok's
`supported_efforts` broad** (the normalized levels remain valid *requests*, translated at the
CLI boundary exactly as codex does) and add a **CLI-boundary override** as the load-bearing
clamp. Do NOT narrow `supported_efforts` to `("low","medium","high")`: with grok's
`unsupported_policy_behavior=_FAIL_CLOSED`, narrowing would make the policy layer REJECT
`max`/`xhigh`/`minimal` during resolution (a hard failure) instead of clamping — the opposite
of the intended, ah#222-consistent behavior.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/launcher.py` (modify) — LOAD-BEARING
- Add `_GROK_CLI_EFFORT_OVERRIDES: dict[str, str] = {"minimal": "low", "xhigh": "high",
  "max": "high"}` and `_grok_cli_effort(effort: str) -> str` (returns
  `_GROK_CLI_EFFORT_OVERRIDES.get(effort, effort)`) beside `_codex_cli_effort`
  (~`:369-378`) — reason: clamp the three normalized levels grok's CLI rejects to its valid
  ceiling/floor; `low`/`medium`/`high` pass through unchanged (mirrors codex's
  `.get(effort, effort)`).
- In the grok argv build (~`:748-749`), wrap the effort value with `_grok_cli_effort(...)`
  so `--reasoning-effort` receives a valid grok token — reason: the actual fix; the CLI now
  never sees `max`/`xhigh`/`minimal`.
- Delete/rewrite the false comment (~`:719-723`) — reason: it currently asserts grok accepts
  a superset and needs no clamp; correct it to "grok's CLI accepts only `high|medium|low`;
  `minimal`/`xhigh`/`max` are clamped at the CLI boundary via `_grok_cli_effort`, like codex's
  `max→xhigh`."

### `phase-loop-runtime/src/phase_loop_runtime/capability_registry.py` (modify) — accuracy
- grok `ProviderPolicyCapability` (~`:494-499`): correct the false note (`:499`) — reason:
  "accepts a superset … no clamp" is backwards; state grok accepts only `high|medium|low` and
  is clamped at the CLI boundary (`launcher._grok_cli_effort`), matching codex.
- `effort_map` (`:497`): replace the identity map with the real clamp
  `{"minimal": "low", "xhigh": "high", "max": "high"}` — reason: even though this fallback is
  currently unreached (see below), a lying identity map is a latent trap; making it accurate
  means the policy-layer fallback would also clamp correctly if ever engaged. **Keep
  `supported_efforts=_ALL_EFFORTS` and `unsupported_policy_behavior=_FAIL_CLOSED` unchanged**
  (per the design decision — narrowing would flip to reject-not-clamp). Add a one-line note
  that the CLI-boundary override is the live mechanism; this map is defense-in-depth.

### `phase-loop-runtime/tests/test_grokexec.py` (modify)
- Update the passthrough assertion (`:108-109`) + narrative (`:87`) — reason: effort is no
  longer verbatim; assert the CLI receives the clamped token.
- Add `test_grok_effort_clamped_to_cli_ceiling` (or extend the existing effort test):
  construct explicit-effort grok specs and assert argv `--reasoning-effort` is
  `max→high`, `xhigh→high`, `minimal→low`, and `low`/`medium`/`high` pass through unchanged —
  reason: pins the fix and prevents a regression to raw passthrough. Reuse the existing
  `resolve_profile_for_executor(..., executor="grok")` → `build_grok_command` builder (or a
  `ModelSelection(effort=...)`), mirroring the existing effort test's construction.

## Documentation impact
- `CHANGELOG.md` — add — the grokexec/launcher grok leg now clamps `minimal`/`xhigh`/`max`
  to grok's valid `--reasoning-effort` tokens (low/high/high) at the CLI boundary, so an
  explicit high-effort grokexec run no longer crashes the grok CLI (sibling of the panel-path
  fix in ah#222/#225).
- No other docs — the two false code comments are corrected inline as part of the change.

## Frozen-vocabulary confirmation
`NORMALIZED_EFFORT_LEVELS` (`models.py:57`) and the `ProviderPolicyCapability` contract
(`models.py:629-652`) are NOT modified — only grok's policy *data* (note + effort_map) and the
launcher CLI-boundary translation. No new vocabulary is introduced.

## Dependencies & order
1. Launcher CLI-boundary clamp (the load-bearing fix) + comment. 2. capability_registry note +
effort_map accuracy. 3. Tests. No migration, no consumer change (`build_grok_command`'s output
shape is unchanged except the effort token value).

## Execution Policy
- execute: effort=low, reason=mechanical CLI-boundary clamp mirroring the existing
  `_codex_cli_effort` pattern + a table-driven regression test.

## Verification
```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python -m pytest tests/test_grokexec.py -q
# broader: nothing else asserts grok effort passthrough
PYTHONPATH=src:tests python -m pytest tests/ -q -k "grok or capability or profiles or launcher or dispatch"
# behavioral: an explicit-max grok command must emit a valid CLI token
PYTHONPATH=src python -c "from phase_loop_runtime.launcher import _grok_cli_effort; \
assert (_grok_cli_effort('max'), _grok_cli_effort('xhigh'), _grok_cli_effort('minimal')) == ('high','high','low'); \
assert (_grok_cli_effort('low'), _grok_cli_effort('medium'), _grok_cli_effort('high')) == ('low','medium','high'); \
print('clamp ok')"
```
Edge cases: (a) explicit `max`/`xhigh`/`minimal` → `high`/`high`/`low` in argv, never the raw
level; (b) `low`/`medium`/`high` unchanged; (c) default (`medium`) path unchanged; (d) no other
test asserts raw grok effort passthrough.

## Acceptance criteria
- [ ] `_grok_cli_effort` maps `max→high`, `xhigh→high`, `minimal→low`, and passes
      `low`/`medium`/`high` through unchanged.
- [ ] A grok command built for an explicit `max` (and `xhigh`, `minimal`) effort emits a
      valid `--reasoning-effort` token (`high`/`high`/`low`), never `max`/`xhigh`/`minimal`.
- [ ] The false "accepts a superset / no clamp needed" comments in `launcher.py` and the
      `capability_registry.py` grok note are corrected; grok `supported_efforts` /
      `unsupported_policy_behavior` are unchanged (clamp, not reject).
- [ ] `test_grokexec.py` passes with the clamp assertion; the grok/capability/profiles/launcher
      suites stay green.
