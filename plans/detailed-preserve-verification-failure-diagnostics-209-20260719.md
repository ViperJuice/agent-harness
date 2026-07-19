# Detailed plan: preserve raw failure diagnostics on verification failure (Consiliency/agent-harness#209)

## Task

When runner-executed verification fails, the runner-owned artifact must
**localize and preserve the raw diagnostic of the stage that broke** — not scrub
it to a bare `exit_code`. Per Consiliency/agent-harness#209:

1. A failed/blocked verdict must carry a **bounded raw diagnostic** (a tail of the
   failing stage's captured output) + a typed `failure_kind ∈ {timeout, error,
   nonzero_exit}` + exit context (argv, role, exit_code).
2. An **empty diagnostic on a failed verdict is itself rejected** — you cannot
   claim "verification failed" while surfacing no evidence of *why* (anti-scrubbing;
   scrubbed diagnostics were a named contributor to the #213 thrash).
3. A declared **multi-step chain** preserves per-step pass/fail in **declared
   order** and reduces **fail-closed** if an earlier step failed even when a later
   step passed.

## Research summary

Verified against source (`phase-loop-runtime/src/phase_loop_runtime/`):

- `_run_process` (`verification_evidence.py:815`) captures **combined stdout+stderr**
  into `verification.log` (`stderr=subprocess.STDOUT`) and records `exit_code`
  (`TimeoutExpired`→124 + marker; `FileNotFoundError`/empty argv→127; else child's
  code). Each `VerificationCommandEvidence` records a **start** offset only
  (`log_offset = log_file.tell()`). **`VerificationSuiteEvidence` and
  `VerificationEnvRefreshEvidence` record no offset** (the suite's start `tell()` is
  discarded at `:533-537`).
- The gate verdict is `VerificationArtifactValidation` (`:435`), from
  `validate_verification_artifact` (`:628`). On a red chain `findings` is just
  `("commands[0].exit_code=1",)` — no typed kind, no raw excerpt.
  `_nonzero_exit_findings` (`:757`) already iterates **all** commands + env_refresh
  + suite and blocks on any non-zero → **ordered fail-closed reduction already
  exists** (repro confirms). Order of `validate_verification_artifact`: it verifies
  `log_sha256` **before** `_nonzero_exit_findings`, so on the `nonzero_exit` code
  path the log is **already authenticated**.
- Durability: `_apply_verification_evidence_gate` (`closeout.py:336`) →
  `validation.to_json()` as `validation_payload` → `verification_results`
  (`:108-190`) → `PhaseLoopVerification(results=tuple(...))` (`:245`) →
  `closeout.to_json()` → **persisted closeout record** (`models.py:932`, `asdict`
  +`clean_dict`; `clean_dict` strips only `None`). Consumed downstream by
  `prompts.py` (fed into the next agent's context) and `state_ops`.

**Repro on current main** (two-step chain, step1 writes `DISTINCTIVE_FAILURE_REASON`
+ exits 1, step2 passes): gate → `ok=False code=nonzero_exit`,
`findings=('commands[0].exit_code=1',)`, `exit_summary.commands=[1,0]`. The stderr
is in `verification.log` but **absent from the verdict**; `failure_kind` absent.
Gap confirmed; fail-closed reduction confirmed already-present.

## Design decision: run-time capture via additive schema v2 (revised after 3-seat plan review)

**Round-1 plan review (codex + gemini + Fable) unanimously rejected the
post-hoc, verdict-only enrichment (original "Option A").** The decisive,
independently-reproduced finding: because `suite`/`env_refresh` carry **no log
offset**, the gate cannot compute stage boundaries post-hoc — the last command's
tail swallows a passing suite's output (false attribution), and an env_refresh
failure at the log *head* is missed by a tail slice. Separately, `failure_kind`
**cannot be safely derived from `exit_code` alone** post-hoc: a child that itself
returns 124/127 would be mislabeled `timeout`/`error`. Only the runner, at
execution time, knows the true origin (it sets 124 in the `TimeoutExpired` branch,
127 in the `FileNotFoundError` branch).

**Therefore: capture exact boundaries + true failure origin at RUN TIME and
persist them.** The `verification.json` schema is versioned precisely for this
("freezes the **additive** verification evidence artifact for **future runner
wiring**" — and #209 *is* that wiring). We bump to **schema v2**, additively:

- gemini asked for v2 offsets outright; codex accepts "v2 or an equivalently
  durable framed record"; Fable is fine with any run-time capture. v2 satisfies all
  three and is the schema's designed extension path.
- This is the advisor's pre-authorized fallback ("a minimal additive v2 bump stays
  a live fallback if the verdict layer proves insufficient") — the verdict layer
  proved insufficient for exactly the boundary + failure-origin reasons above.

### Frozen-vocabulary handling

`verification.json` is schema-frozen at v1 by
`_contract_docs/runtime/verification-evidence-contract.md`. This change **bumps the
contract to v2**, additively (all v1 fields retained; new fields optional).
`load_verification_artifact` gains **v1 back-compat**: a v1 artifact loads with the
new fields defaulted (`log_end_offset=None`, `failure_kind=None`), so existing
persisted run dirs still parse. `SCHEMA_VERSION` → `2`. The contract doc is updated
in the same commit (docs-freshness gate requires it).

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py` (modify)

- `SCHEMA_VERSION` — `1` → `2`.
- `_run_process` (`:815`) — **modify** — return exact boundaries + run-time
  failure origin. Record `end_offset = log_file.tell()` **after** writing each
  stage's output. Classify at run time: success→`None`; `TimeoutExpired`
  branch→`"timeout"`; `FileNotFoundError`/empty-argv branch→`"error"`; other
  non-zero returncode→`"nonzero_exit"` (this is where a child's own 124/127 stays
  `nonzero_exit`, not mislabeled). Return these on the evidence object.
- `VerificationCommandEvidence` (`:388`) — **modify** — add
  `log_end_offset: int | None = None`, `failure_kind: str | None = None`.
- `VerificationSuiteEvidence` (`:405`) — **modify** — add
  `log_offset: int | None = None`, `log_end_offset: int | None = None`,
  `failure_kind: str | None = None`. Populate from the suite `_run_process`
  evidence (`:533-537`) instead of discarding the offsets.
- `VerificationEnvRefreshEvidence` (`:397`) — **modify** — add the same three
  fields; `_record_env_refresh` already runs through `_run_process` (`:907`), so
  map its evidence through.
- `_command_to_payload` / `_suite_to_payload` / `_env_refresh_to_payload` — **modify**
  — serialize the new fields (omit-if-None to keep green-run artifacts lean).
- `_command_from_payload` / `_suite_from_payload` / `_env_refresh_from_payload` +
  `load_verification_artifact` `_require_keys` — **modify** — the required-key set
  is unchanged (new fields optional); read new fields with `.get(...)` defaulting
  to `None` (v1 back-compat).
- `load_verification_artifact` version guard (`~:605`) — **modify** — change
  `if data["schema_version"] != SCHEMA_VERSION: raise` to accept the set
  `{1, 2}` (`if data["schema_version"] not in _SUPPORTED_SCHEMA_VERSIONS`). Without
  this the `!=` rejects every v1 artifact outright; test (i) is the guard. Add
  `_SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})`.
- **`interpreter.blocker` branch** (`run_verification`, `~:480-513`) — **modify** —
  this branch synthesizes 127 evidence **outside** `_run_process` after writing the
  `"suite interpreter unavailable: {blocker}\n"` line, so under the new design it
  would emit `raw_tail=""` + `diagnostic_status="missing_output"` +
  `failure_kind=None` — a **scrubbed diagnostic for a common, important failure**
  (requires-python / pin mismatch), exactly what #209 prevents. Fix: for **both** the
  commands-only synthesized `VerificationCommandEvidence` (`:505`) and the
  `VerificationSuiteEvidence` (`:496`), set the region to span the "unavailable" line
  (`log_offset=0`, `log_end_offset=log_file.tell()` captured after the write) and
  `failure_kind="error"`, so the diagnostic surfaces the blocker reason.
- `DIAGNOSTIC_TAIL_BYTES = 4096` — **add** — module constant, the per-stage raw
  tail cap (bounds memory/record size; it is **not** a secret-leak mitigation — a
  secret is <100 bytes, so no cap size prevents disclosure. The real egress
  mitigation is the redaction follow-up issue, not the number. See Redaction
  posture).
- `_build_failure_diagnostics(result, log_bytes) -> tuple[dict, ...]` — **add** —
  for each stage with a non-zero exit, in the declared order
  `commands[0..n]` → `env_refresh` → `suite` (matching `_nonzero_exit_findings`):
  ```
  {"role": "command"|"env_refresh"|"suite", "index": int|None,
   "argv": [...], "exit_code": int, "failure_kind": <from artifact>,
   "raw_tail": <tail of log_bytes[log_offset:log_end_offset], cap DIAGNOSTIC_TAIL_BYTES>,
   "truncated": bool, "diagnostic_status": "present"|"missing_output"}
  ```
  Slicing uses the **exact recorded `[log_offset, log_end_offset)`** — no boundary
  reconstruction. `failure_kind` is read from the artifact (run-time observed), not
  re-derived. A stage whose recorded region is empty → `raw_tail=""`,
  `diagnostic_status="missing_output"` (codex's distinct signal for a silent
  failure) — but the entry is **still present** with typed `{role, argv, exit_code,
  failure_kind}` (gemini/Fable: typed context is itself evidence). This reconciles
  the two seats' divergence: the diagnostic is never absent, and a genuinely
  output-less failure is explicitly flagged rather than papered over.
- `VerificationArtifactValidation` (`:435`) — **modify** — add
  `diagnostics: tuple[dict[str, Any], ...] = ()`; `.to_json()` emits
  `"diagnostics": list(self.diagnostics)`.
- `validate_verification_artifact` (`:628`) — **modify** — on the `nonzero_exit`
  branch (log already authenticated at that point), populate `diagnostics=` via
  `_build_failure_diagnostics`. **Anti-scrubbing invariant (structural, not
  happy-path):** assert that a `nonzero_exit`-class failed verdict has
  `diagnostics != ()`; this is guaranteed because every failing stage yields at
  least a typed entry. Do **not** build stage diagnostics on the integrity-failure
  branches (`log_sha256_mismatch` / `missing_log`): there the log is unauthenticated
  or absent, so a tail could be forged/misleading — those verdicts block on
  integrity, which is the more severe reason (codex: don't surface an
  unauthenticated tail).

  > Scope guard: the executor-self-report path (`closeout.py:344-346`,
  > `reported != "passed" → return None`) stays **out of scope** — #209 is the
  > runner-executed multi-stage proof, not the agent's self-assertion.

### `phase-loop-runtime/src/phase_loop_runtime/closeout.py` (no functional change; verify pass-through)

`validation.to_json()` now includes `diagnostics`; it already flows through
`verification_results` → `PhaseLoopVerification.results` → persisted closeout.
**No edit** (confirmed `clean_dict` keeps the non-`None` list). Recorded as a
deliberate no-op.

## Documentation impact

- `.../_contract_docs/runtime/verification-evidence-contract.md` — **modify** —
  bump to **schema v2**; document the additive `log_end_offset` + `failure_kind`
  (and suite/env_refresh `log_offset`); document the verdict-layer `diagnostics`
  field (typed `failure_kind`, run-time origin, exact `[log_offset,
  log_end_offset)` slicing, `DIAGNOSTIC_TAIL_BYTES` cap, declared order,
  `diagnostic_status` marker, anti-scrubbing invariant, integrity-branch
  exclusion). State v1 back-compat (v1 artifacts load with new fields `None`).
  **Redaction posture:** the raw stderr is *already* persisted in full in
  `verification.log` at the same trust level; the diagnostic surfaces a bounded tail
  into the closeout record (which `prompts.py` may feed downstream) — a real but
  modest egress **widening** (disk log → closeout/ledger/prompt), not a new capture.
  The `DIAGNOSTIC_TAIL_BYTES` cap bounds size, **not** disclosure (a secret is tiny).
  Closeout-record redaction is out of #209 scope (no redaction exists
  in the verification path today; a red pytest already dumps secrets into
  `verification.log`) → **follow-up issue** for opt-in closeout-diagnostic redaction.
- `CHANGELOG.md` — **add** — entry: verification verdict now carries a typed
  `failure_kind` + bounded raw-output tail per failing stage in declared order;
  `verification.json` bumped to schema v2 (additive: per-stage `log_end_offset` +
  `failure_kind`, suite/env_refresh `log_offset`); v1 artifacts still load
  (Consiliency/agent-harness#209).
- No `README`/`AGENTS`/`llms.txt` footprint.

## Dependencies & order

1. `_run_process` boundary + run-time `failure_kind` capture.
2. Dataclass fields + payload to/from + `SCHEMA_VERSION=2` + v1-back-compat load.
3. `_build_failure_diagnostics` + `DIAGNOSTIC_TAIL_BYTES` + verdict field + wire into
   `validate_verification_artifact` (nonzero-exit branch only) + anti-scrubbing invariant.
4. Contract-doc v2 + CHANGELOG.
5. Regression tests.

No migration (v1 artifacts load); no external dependency. Redaction unchanged
except the bounded tail widening noted above (follow-up filed).

## Verification

From `phase-loop-runtime/` (unmarked module → not `dotfiles_integration`-skipped):

```bash
PYTHONPATH=src:tests python3 -m pytest tests/test_verification_evidence.py \
  tests/test_closeout_verification_gate.py -q
```

Regression asserts (baked into `test_verification_evidence.py`):
- (a) single failing command: `diagnostics[0].raw_tail` contains the real stderr;
  `failure_kind=="nonzero_exit"`; `argv` present.
- (b) **failing last command + verbose PASSING suite** (the round-1 gap): the
  command's `raw_tail` contains the **command's** stderr and **not** the suite's
  output — proves the exact recorded boundary, not an EOF tail. Terminal not passed.
- (c) two-step chain step1 fails / step2 passes: verdict `ok is False`;
  `diagnostics[0].index==0` with step1's tail; `exit_summary.commands==[1,0]`
  (declared order preserved).
- (d) **env_refresh failure** with later passing commands: env_refresh diagnostic's
  `raw_tail` contains the env error (sliced from its head-of-log region), not the
  commands' tail.
- (e) **runner timeout** → `failure_kind=="timeout"`; **child that itself exits 124**
  → `failure_kind=="nonzero_exit"` (not `timeout`); missing exec (127) → `"error"`.
- (f) no-output failure (`exit 1`, no stdout/stderr) → `diagnostics` non-empty,
  typed context present, `diagnostic_status=="missing_output"` (anti-scrubbing).
- (g) over-cap output → `len(raw_tail) <= DIAGNOSTIC_TAIL_BYTES`, `truncated==True`.
- (h) green run → `diagnostics == ()`.
- (i) **v1 back-compat**: a hand-written v1 `verification.json` (no new fields) loads
  without error, new fields default `None`.
- (j) **interpreter-blocker** (requires-python/pin mismatch): the synthesized 127
  stage's diagnostic surfaces the `"suite interpreter unavailable: ..."` line in
  `raw_tail` with `failure_kind=="error"` and `diagnostic_status=="present"` — not a
  scrubbed `missing_output`.
- Mutation check: revert the `_run_process` end-offset capture → (b)/(d) fail
  (tails cross stage boundaries).

## Acceptance criteria

- [ ] A failing verification stage yields a verdict `diagnostics[]` entry with the
  stage's **own** real output tail (proven by the failing-last-command +
  passing-suite case and the env_refresh-failure case — no cross-stage bleed).
- [ ] `failure_kind` reflects the **runner-observed** origin: a runner timeout is
  `timeout` while a child that returns 124 is `nonzero_exit`; missing exec is `error`.
- [ ] A `nonzero_exit`-class failed verdict can never carry empty `diagnostics`; a
  no-output failure still surfaces typed context + `diagnostic_status=="missing_output"`.
- [ ] Declared multi-step chains keep per-step order and reduce fail-closed
  (verified: step1-fails/step2-passes → not `passed`).
- [ ] `raw_tail` bounded by `DIAGNOSTIC_TAIL_BYTES`; over-cap sets `truncated==True`.
- [ ] `verification.json` is schema **v2**, additive; a **v1** artifact still loads
  (new fields `None`); contract doc updated to v2 with the redaction-posture note.
- [ ] Green run → `diagnostics == ()`.

## Execution Policy

- execute: effort=high, reason=security/verification-sensitive fail-closed verdict
  layer + a frozen-contract schema bump with back-compat and cross-stage boundary
  edge cases; not mechanical. Bounded to one module + contract doc + CHANGELOG +
  tests.
