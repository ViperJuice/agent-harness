# Verification Evidence Contract

VC freezes the additive verification evidence artifact for future runner wiring. Until a later phase consumes it, the only producer is `phase_loop_runtime.verification_evidence.run_verification`.

`run_verification(repo, run_dir, commands, suite_command, env_refresh, timeout_s, operational_exemptions=None, python_pin=None, phase_alias=None) -> VerificationResult` writes two sibling files under the supplied run directory. The optional `phase_alias` is the LIVE run alias (ah#85). `verification.json`'s `phase_alias` resolves in strict precedence order: the operator env override (`PHASE_LOOP_PHASE_ALIAS` / `PHASE_ALIAS`) wins if set, else the supplied `phase_alias` (the live run alias) if provided, else `.phase-loop/state.json:current_phase`, else `"unknown"`. Threading the live alias on the execute path keeps the artifact from mis-attributing the phase after a mid-run roadmap amendment changes `current_phase`; the env override remains the highest-priority operator escape-hatch.

- `verification.json`
- `verification.log`

`verification.json` uses schema version 2 and contains exactly these top-level fields:

- `schema_version`
- `run_id`
- `phase_alias`
- `commands`
- `env_refresh`
- `suite`
- `started_at`
- `finished_at`
- `log_sha256`

Each `commands[]` item contains `argv`, `cwd`, `exit_code`, `duration_s`, and `log_offset`. `env_refresh`, when present, contains `triggered`, `manifests`, `install_argv`, and `exit_code`. `suite`, when present, contains `argv`, `exit_code`, and `duration_s`.

**Schema v2 (agent-harness#209), additive.** Every stage (`commands[]`, `env_refresh`, `suite`) may additionally carry `log_end_offset` (the exclusive byte end of that stage's output in `verification.log`) and `failure_kind` (the runner-OBSERVED failure origin, one of `timeout` / `error` / `nonzero_exit`), and `env_refresh`/`suite` additionally carry their own `log_offset` (start). These fields are captured at execution time and omitted when null; `failure_kind` is never re-derived from `exit_code` downstream (a child that itself returns 124/127 stays `nonzero_exit`, not `timeout`/`error`). **v1 back-compat:** a v1 artifact (without these fields) still loads — the missing fields default to `None`. `load_verification_artifact` accepts `schema_version` in `{1, 2}`.

`verification.log` stores raw combined stdout and stderr from the recorded subprocesses. `log_offset` is the start byte offset for each stage's output and `log_end_offset` (v2) is its exclusive end. `log_sha256` is the SHA-256 digest of the final log bytes.

**Failure diagnostics (verdict layer, non-frozen).** `validate_verification_artifact` returns `VerificationArtifactValidation`; its `to_json()` includes a `diagnostics` list. On the `nonzero_exit` verdict (where the log is already sha-authenticated), it carries one entry per failing stage — in declared order `commands` → `env_refresh` → `suite` — each `{role, index, argv, exit_code, failure_kind, raw_tail, truncated, diagnostic_status}`. `raw_tail` is a bounded (`DIAGNOSTIC_TAIL_BYTES`) tail sliced from the stage's exact `[log_offset, log_end_offset)` region; `diagnostic_status` is `missing_output` when a failing stage produced no captured bytes (still emitted, with typed context — a failed verdict is never diagnostic-empty). Diagnostics are NOT built on the `log_sha256_mismatch` / `missing_log` integrity branches (an unauthenticated log's tail could be forged); those block on the integrity failure, which is the more severe reason.

**Diagnostic offset-integrity (threat model).** Each failing stage's `raw_tail` is sliced from its recorded `[log_offset, log_end_offset)` region, validated fail-closed against its execution-order neighbours' recorded offsets (`prev.log_end_offset <= log_offset <= log_end_offset <= next.log_offset`, bounded by `[0, len(log)]`); any violation yields an empty tail, never a slice into an adjacent stage. This makes a **single-field** offset tamper (which produces an inconsistent chain) fail closed. The stage-offset table itself is not covered by `log_sha256` (only the log bytes are), so a **multi-field** tamper that keeps the chain internally consistent could reshuffle *which* authenticated log bytes appear in *which* diagnostic. That is deliberately out of scope: every byte involved is already in the SHA-authenticated `verification.log` at the same trust level (an editor of `verification.json` can already read it), and the reshuffle never changes the pass/fail verdict — the authoritative gate is the per-stage `exit_code`, which is checked independently. Signing the offset table would be a separate hardening.

**Redaction posture.** `raw_tail` is a bounded excerpt of bytes ALREADY persisted in full in `verification.log` at the same trust level (a red test suite already writes its output there). Surfacing the tail into the closeout record (which downstream prompts may read) is a real but modest egress widening, not a new capture path. The `DIAGNOSTIC_TAIL_BYTES` cap bounds record size, not disclosure (a secret is tiny) — closeout-diagnostic redaction is a separate, out-of-scope follow-up.

The JSON artifact is written atomically by creating a same-directory temporary file and replacing `verification.json`. Re-running identical command inputs rewrites the same artifact shape and log content, except for timestamps and durations.

Command failures, missing executables, and timeouts are evidence data. They are represented by nonzero `exit_code` values and do not make `run_verification` raise. Programmer errors, such as a missing repo or a run directory outside the repo, raise before evidence is written.

The plan-time validator API is `validate_verification_commands(repo, commands) -> list[ValidationFinding]`. It is read-only and returns structured findings for empty argv, unresolved `argv[0]`, explicit repo-relative path references that do not exist, and cwd/path references that resolve outside the repo.

`load_verification_artifact(path)` validates the persisted artifact and returns `VerificationResult`.

Evidence marked `evidence: operational` can be recorded for operator inspection, but it cannot mark runner-executed verification as passed. A later runner phase must still reduce actual command exit data before claiming verification success.

## Hotfix Consumers

`phase-loop hotfix --init-stub <path>` writes a minimal stub with `objective`
and `verification_command` fields without creating a run directory.

`phase-loop hotfix --reason <text> --plan <stub-path>` is an emergency
consumer of the same IF-0-VC-1 evidence path. It creates
`.phase-loop/runs/<ts>-hotfix-<slug>/`, runs dependency-manifest env refresh,
the stub verification command, and the effective suite command through
`run_verification`, then validates the resulting artifact before ledger
closeout. A passed hotfix event must include `work_unit: hotfix`, the redacted
reason, plan stub, `verification_artifact_path`, `verification_log_path`, and
the artifact validation summary. A missing, malformed, tampered, or nonzero
artifact blocks the hotfix closeout instead of reporting `passed`.

Use hotfix for a single bounded change with no interface freeze. Anything that
changes interfaces, roadmap scope, or downstream work uses a roadmap phase.
