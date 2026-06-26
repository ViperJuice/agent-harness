# Verification Evidence Contract

VC freezes the additive verification evidence artifact for future runner wiring. Until a later phase consumes it, the only producer is `phase_loop_runtime.verification_evidence.run_verification`.

`run_verification(repo, run_dir, commands, suite_command, env_refresh, timeout_s) -> VerificationResult` writes two sibling files under the supplied run directory:

- `verification.json`
- `verification.log`

`verification.json` uses schema version 1 and contains exactly these top-level fields:

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

`verification.log` stores raw combined stdout and stderr from the recorded subprocesses. `log_offset` is the byte offset for each command's output. `log_sha256` is the SHA-256 digest of the final log bytes.

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
