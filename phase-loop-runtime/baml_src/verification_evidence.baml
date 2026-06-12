class VerificationCommandEvidence {
  argv string[]
  cwd string
  exit_code int
  duration_s float
  log_offset int
}

class VerificationEnvRefreshEvidence {
  triggered bool
  manifests string[]
  install_argv string[]
  exit_code int
}

class VerificationSuiteEvidence {
  argv string[]
  exit_code int
  duration_s float
}

class VerificationValidationFinding {
  code string
  message string
  command_index int
  argument_index int?
  value string?
}

class VerificationEvidence {
  schema_version int
  run_id string
  phase_alias string
  commands VerificationCommandEvidence[]
  env_refresh VerificationEnvRefreshEvidence?
  suite VerificationSuiteEvidence?
  started_at string
  finished_at string
  log_sha256 string
}
