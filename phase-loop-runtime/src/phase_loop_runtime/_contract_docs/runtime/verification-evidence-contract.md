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

`verification.log` stores raw combined stdout and stderr from the recorded subprocesses, followed (agent-harness#243) by a single trailing seal line `verification-artifact-sha256:<hex>` — a canonical digest of `verification.json` (see the whole-artifact integrity paragraph). `log_offset` is the start byte offset for each stage's output and `log_end_offset` (v2) is its exclusive end (both point strictly before the seal trailer, so a stage's `raw_tail` never includes it). `log_sha256` is the SHA-256 digest of the final log bytes (seal trailer included).

**Failure diagnostics (verdict layer, non-frozen).** `validate_verification_artifact` returns `VerificationArtifactValidation`; its `to_json()` includes a `diagnostics` list. On the `nonzero_exit` verdict (where the log is already sha-authenticated), it carries one entry per failing stage — in declared order `commands` → `env_refresh` → `suite` — each `{role, index, argv, exit_code, failure_kind, raw_tail, truncated, diagnostic_status}`. `raw_tail` is a bounded (`DIAGNOSTIC_TAIL_BYTES`) tail sliced from the stage's exact `[log_offset, log_end_offset)` region; `diagnostic_status` is `missing_output` when a failing stage produced no captured bytes (still emitted, with typed context — a failed verdict is never diagnostic-empty). Diagnostics are NOT built on the `log_sha256_mismatch` / `missing_log` integrity branches (an unauthenticated log's tail could be forged); those block on the integrity failure, which is the more severe reason.

**Diagnostic offset-integrity (threat model).** Each failing stage's `raw_tail` is sliced from its recorded `[log_offset, log_end_offset)` region, validated fail-closed against its execution-order neighbours' recorded offsets (`prev.log_end_offset <= log_offset <= log_end_offset <= next.log_offset`, bounded by `[0, len(log)]`); any violation yields an empty tail, never a slice into an adjacent stage. This makes a **single-field** offset tamper (which produces an inconsistent chain) fail closed. This neighbour-bounds check is the offset-integrity guard for an **unsealed** artifact (see below); on a **sealed** artifact it remains in place as defense-in-depth, but the whole-artifact seal (which covers offsets — see next paragraph) now catches offset tampers first, including a **coordinated** multi-field tamper across two stages that stays internally consistent with the neighbour-bounds check (e.g. extending one stage's `log_end_offset` and moving the next stage's `log_offset` to the same value).

**Whole-artifact integrity (agent-harness#243).** Beyond the per-stage offset table (which `log_sha256` does not cover), the ENTIRE `verification.json` is sealed. At write time a canonical digest of the artifact payload — **all fields except the derived `log_sha256`**, including the per-stage `log_offset`/`log_end_offset` fields — is embedded as the `verification-artifact-sha256:<hex>` trailer line in `verification.log`, and then `log_sha256` is computed over the whole log — so the log's SHA also seals the artifact digest. At validate time, the digest is recomputed from `verification.json` and compared to the sealed trailer BEFORE the pass/fail branch (after `log_sha256_mismatch` / the load-time malformed/oversized guards, so the log is already authenticated) — a FAILING artifact is seal-protected too, not just a would-be-PASS one. A mismatch yields `artifact_seal_mismatch` and fails closed. This catches a **multi-field / structural** edit the per-stage checks miss — most importantly a verdict-flipping edit such as deleting a failed `commands[]` entry to forge a pass, and (since offsets are covered) a coordinated per-stage offset tamper that would otherwise pass the neighbour-bounds check. The seal is keyless (an editor who can rewrite the artifact, the log trailer, AND recompute `log_sha256` can still re-seal — the same trust level #209 assumed), so this raises the bar to a coordinated multi-file edit rather than being cryptographically unforgeable; it is defense-in-depth. **Back-compat / sealed-vs-unsealed split:** an artifact whose log carries no seal trailer (a v1/older run, or an externally-built log) skips the seal check entirely and still validates — for that unsealed case, the per-stage neighbour-bounds check above remains the sole offset-integrity guard, exactly as under #209. Separately, an artifact larger than `MAX_ARTIFACT_BYTES` is rejected `oversized_artifact` before it is parsed.

**Redaction posture (agent-harness#243, agent-harness#266).** `raw_tail` is a bounded excerpt of `verification.log` bytes surfaced into every PERSISTED copy of a validation payload — the real egress widening this redaction narrows (the `DIAGNOSTIC_TAIL_BYTES` cap bounds record size, not disclosure — a secret is tiny). A prior round redacted only the rebuilt closeout record, on the premise that the other persisted copies (`launch.json`, the `child_automation` copy, the `events.jsonl` ledger event, the hotfix `artifact_validation`) were local-artifact-trust-class and not read into any prompt/egress path. That premise was FALSE and is corrected here: `phase-loop state --json` / `inspect_state()` reads the entire `launch.json` back out verbatim as `latest_launch_metadata`, and the harness SKILL directs agents to run `state --json` for exact state; the repair prompt separately directs an agent to inspect `events.jsonl` directly. Both are deterministic egress paths for whatever `runner_verification`/`artifact_validation` carries.

Redaction is therefore now applied **at the SOURCE** — the single point each of these code paths first captures a `VerificationArtifactValidation.to_json()` payload into something that will be persisted (`redaction.apply_diagnostics_redaction`, a thin SSOT wrapper around `redact_diagnostics_metadata_only`) — rather than only in the closeout record after the fact. Any diagnostic whose fields carry a secret/PII-shaped value (detected with the same `_FORBIDDEN_METADATA_PATTERNS` the closeout malformed-metadata gate enforces) is redacted to METADATA-ONLY: `raw_tail` and `argv` are dropped, leaving role/index/exit_code/failure_kind/truncated plus byte/arg counts, a `redacted` flag, and a `redaction_reason`. Because redaction happens at the source, every downstream persisted copy inherits the redacted form: the closeout record, `launch.json` (and therefore `state --json` / `inspect_state()`), the `child_automation` copy, the `events.jsonl` ledger event (including the `reconcile --verification-log` manual-repair event), and the hotfix `artifact_validation` (both the ledger event and the printed CLI payload). The on-disk `verification.log` is left FULL — it is the intentional local source of truth and is never redacted; only these persisted, potentially-re-read copies are narrowed. `PHASE_LOOP_VERIFY_REDACT_DIAGNOSTICS=all` forces full `raw_tail`/`argv` suppression on every diagnostic across all of these source-redacted copies.

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
