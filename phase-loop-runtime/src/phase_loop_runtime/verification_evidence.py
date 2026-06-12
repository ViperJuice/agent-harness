from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
ARTIFACT_NAME = "verification.json"
LOG_NAME = "verification.log"


@dataclass(frozen=True)
class VerificationCommandEvidence:
    argv: list[str]
    cwd: str
    exit_code: int
    duration_s: float
    log_offset: int


@dataclass(frozen=True)
class VerificationEnvRefreshEvidence:
    triggered: bool
    manifests: list[str]
    install_argv: list[str]
    exit_code: int


@dataclass(frozen=True)
class VerificationSuiteEvidence:
    argv: list[str]
    exit_code: int
    duration_s: float


@dataclass(frozen=True)
class VerificationResult:
    schema_version: int
    run_id: str
    phase_alias: str
    commands: list[VerificationCommandEvidence]
    env_refresh: VerificationEnvRefreshEvidence | None
    suite: VerificationSuiteEvidence | None
    started_at: str
    finished_at: str
    log_sha256: str


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    message: str
    command_index: int
    argument_index: int | None = None
    value: str | None = None


@dataclass(frozen=True)
class VerificationArtifactValidation:
    ok: bool
    code: str
    artifact_path: str
    log_path: str | None = None
    exit_summary: dict[str, Any] | None = None
    findings: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "artifact_path": self.artifact_path,
            "log_path": self.log_path,
            "exit_summary": self.exit_summary or {},
            "findings": list(self.findings),
        }


def run_verification(
    repo: Path,
    run_dir: Path,
    commands: list[list[str]],
    suite_command: list[str] | None,
    env_refresh: object,
    timeout_s: float | None,
) -> VerificationResult:
    repo_path = _resolve_repo(repo)
    run_path = _resolve_run_dir(repo_path, run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    log_path = run_path / LOG_NAME
    artifact_path = run_path / ARTIFACT_NAME
    started_at = _utc_now()

    with log_path.open("wb") as log_file:
        env_result = _record_env_refresh(repo_path, log_file, env_refresh, timeout_s)
        command_results = [_run_process(repo_path, log_file, argv, timeout_s) for argv in commands]
        suite_result = None
        if suite_command is not None:
            suite_evidence = _run_process(repo_path, log_file, suite_command, timeout_s)
            suite_result = VerificationSuiteEvidence(
                argv=suite_evidence.argv,
                exit_code=suite_evidence.exit_code,
                duration_s=suite_evidence.duration_s,
            )

    finished_at = _utc_now()
    log_sha256 = hashlib.sha256(log_path.read_bytes()).hexdigest()
    result = VerificationResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_path.name,
        phase_alias=_phase_alias(repo_path),
        commands=command_results,
        env_refresh=env_result,
        suite=suite_result,
        started_at=started_at,
        finished_at=finished_at,
        log_sha256=log_sha256,
    )
    _write_artifact_atomic(artifact_path, _result_to_payload(result))
    return result


def validate_verification_commands(repo: Path, commands: list[list[str]]) -> list[ValidationFinding]:
    repo_path = _resolve_repo(repo)
    findings: list[ValidationFinding] = []
    for command_index, argv in enumerate(commands):
        if not argv:
            findings.append(
                ValidationFinding(
                    code="empty_argv",
                    message="verification command argv must not be empty",
                    command_index=command_index,
                )
            )
            continue
        executable = argv[0]
        if not _executable_resolves(repo_path, executable):
            findings.append(
                ValidationFinding(
                    code="unresolved_executable",
                    message="verification command executable does not resolve",
                    command_index=command_index,
                    argument_index=0,
                    value=executable,
                )
            )
        for argument_index, value in _iter_path_arguments(argv):
            finding = _validate_path_argument(repo_path, command_index, argument_index, value)
            if finding is not None:
                findings.append(finding)
    return findings


def load_verification_artifact(path: Path) -> VerificationResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _require_keys(
        data,
        {
            "schema_version",
            "run_id",
            "phase_alias",
            "commands",
            "env_refresh",
            "suite",
            "started_at",
            "finished_at",
            "log_sha256",
        },
    )
    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported verification evidence schema_version: {data['schema_version']}")
    commands = [_command_from_payload(item) for item in _require_list(data["commands"], "commands")]
    env_refresh = None
    if data["env_refresh"] is not None:
        env_refresh = _env_refresh_from_payload(data["env_refresh"])
    suite = None
    if data["suite"] is not None:
        suite = _suite_from_payload(data["suite"])
    return VerificationResult(
        schema_version=data["schema_version"],
        run_id=_require_str(data["run_id"], "run_id"),
        phase_alias=_require_str(data["phase_alias"], "phase_alias"),
        commands=commands,
        env_refresh=env_refresh,
        suite=suite,
        started_at=_require_str(data["started_at"], "started_at"),
        finished_at=_require_str(data["finished_at"], "finished_at"),
        log_sha256=_require_str(data["log_sha256"], "log_sha256"),
    )


def validate_verification_artifact(path: Path) -> VerificationArtifactValidation:
    artifact_path = Path(path)
    log_path = artifact_path.parent / LOG_NAME
    try:
        result = load_verification_artifact(artifact_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return VerificationArtifactValidation(
            ok=False,
            code="malformed_artifact",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            findings=(str(exc),),
        )
    try:
        actual_log_sha256 = hashlib.sha256(log_path.read_bytes()).hexdigest()
    except OSError as exc:
        return VerificationArtifactValidation(
            ok=False,
            code="missing_log",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            exit_summary=_exit_summary(result),
            findings=(str(exc),),
        )
    if actual_log_sha256 != result.log_sha256:
        return VerificationArtifactValidation(
            ok=False,
            code="log_sha256_mismatch",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            exit_summary=_exit_summary(result),
            findings=("verification.log sha256 does not match verification.json log_sha256",),
        )
    nonzero = _nonzero_exit_findings(result)
    if nonzero:
        return VerificationArtifactValidation(
            ok=False,
            code="nonzero_exit",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            exit_summary=_exit_summary(result),
            findings=tuple(nonzero),
        )
    return VerificationArtifactValidation(
        ok=True,
        code="ok",
        artifact_path=str(artifact_path),
        log_path=str(log_path),
        exit_summary=_exit_summary(result),
    )


def append_evidence_entry(doc_path: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise ValueError("evidence entry must be a metadata mapping")
    path = Path(doc_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": _utc_now(), "entry": dict(entry)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    needs_separator = path.exists() and path.stat().st_size > 0
    if needs_separator:
        try:
            with path.open("rb") as existing:
                existing.seek(-1, os.SEEK_END)
                needs_separator = existing.read(1) != b"\n"
        except OSError:
            needs_separator = False
    with path.open("ab") as handle:
        if needs_separator:
            handle.write(b"\n")
        handle.write(encoded.encode("utf-8"))
    return payload


def _exit_summary(result: VerificationResult) -> dict[str, Any]:
    return {
        "commands": [command.exit_code for command in result.commands],
        "env_refresh": result.env_refresh.exit_code if result.env_refresh is not None else None,
        "suite": result.suite.exit_code if result.suite is not None else None,
    }


def _nonzero_exit_findings(result: VerificationResult) -> list[str]:
    findings: list[str] = []
    for index, command in enumerate(result.commands):
        if command.exit_code != 0:
            findings.append(f"commands[{index}].exit_code={command.exit_code}")
    if result.env_refresh is not None and result.env_refresh.exit_code != 0:
        findings.append(f"env_refresh.exit_code={result.env_refresh.exit_code}")
    if result.suite is not None and result.suite.exit_code != 0:
        findings.append(f"suite.exit_code={result.suite.exit_code}")
    return findings


def _run_process(repo: Path, log_file: Any, argv: Sequence[str], timeout_s: float | None) -> VerificationCommandEvidence:
    command_argv = [str(part) for part in argv]
    offset = log_file.tell()
    started = time.monotonic()
    if not command_argv:
        log_file.write(b"empty verification command argv\n")
        log_file.flush()
        return VerificationCommandEvidence([], ".", 127, _duration(started), offset)
    try:
        completed = subprocess.run(
            command_argv,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s if timeout_s and timeout_s > 0 else None,
            check=False,
        )
        output = completed.stdout or b""
        log_file.write(output)
        log_file.flush()
        exit_code = int(completed.returncode)
    except subprocess.TimeoutExpired as exc:
        output = exc.output or b""
        if isinstance(output, str):
            output = output.encode("utf-8", errors="replace")
        log_file.write(output)
        log_file.write(f"\nverification command timed out after {timeout_s}s\n".encode("utf-8"))
        log_file.flush()
        exit_code = 124
    except FileNotFoundError as exc:
        log_file.write(f"{exc}\n".encode("utf-8", errors="replace"))
        log_file.flush()
        exit_code = 127
    return VerificationCommandEvidence(command_argv, ".", exit_code, _duration(started), offset)


def _record_env_refresh(
    repo: Path,
    log_file: Any,
    env_refresh: object,
    timeout_s: float | None,
) -> VerificationEnvRefreshEvidence | None:
    if env_refresh is None:
        return None
    if isinstance(env_refresh, Mapping):
        triggered = bool(env_refresh.get("triggered", True))
        manifests = [str(item) for item in env_refresh.get("manifests", [])]
        install_argv = [str(item) for item in env_refresh.get("install_argv", env_refresh.get("argv", []))]
        if "exit_code" in env_refresh:
            return VerificationEnvRefreshEvidence(triggered, manifests, install_argv, int(env_refresh["exit_code"]))
        if not triggered or not install_argv:
            return VerificationEnvRefreshEvidence(triggered, manifests, install_argv, 0)
    else:
        triggered = True
        manifests = []
        install_argv = [str(item) for item in env_refresh] if isinstance(env_refresh, Sequence) and not isinstance(env_refresh, (str, bytes)) else []
    process = _run_process(repo, log_file, install_argv, timeout_s)
    return VerificationEnvRefreshEvidence(triggered, manifests, install_argv, process.exit_code)


def _result_to_payload(result: VerificationResult) -> dict[str, Any]:
    return {
        "schema_version": result.schema_version,
        "run_id": result.run_id,
        "phase_alias": result.phase_alias,
        "commands": [_command_to_payload(command) for command in result.commands],
        "env_refresh": _env_refresh_to_payload(result.env_refresh) if result.env_refresh else None,
        "suite": _suite_to_payload(result.suite) if result.suite else None,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "log_sha256": result.log_sha256,
    }


def _command_to_payload(command: VerificationCommandEvidence) -> dict[str, Any]:
    return {
        "argv": list(command.argv),
        "cwd": command.cwd,
        "exit_code": command.exit_code,
        "duration_s": command.duration_s,
        "log_offset": command.log_offset,
    }


def _env_refresh_to_payload(env_refresh: VerificationEnvRefreshEvidence) -> dict[str, Any]:
    return {
        "triggered": env_refresh.triggered,
        "manifests": list(env_refresh.manifests),
        "install_argv": list(env_refresh.install_argv),
        "exit_code": env_refresh.exit_code,
    }


def _suite_to_payload(suite: VerificationSuiteEvidence) -> dict[str, Any]:
    return {
        "argv": list(suite.argv),
        "exit_code": suite.exit_code,
        "duration_s": suite.duration_s,
    }


def _write_artifact_atomic(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp_file:
        tmp_file.write(encoded)
        tmp_path = Path(tmp_file.name)
    tmp_path.replace(path)


def _resolve_repo(repo: Path) -> Path:
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"repo does not exist or is not a directory: {repo}")
    return repo_path


def _resolve_run_dir(repo: Path, run_dir: Path) -> Path:
    run_path = Path(run_dir)
    if not run_path.is_absolute():
        run_path = repo / run_path
    run_path = run_path.resolve()
    if not _is_relative_to(run_path, repo):
        raise ValueError(f"run_dir must be inside repo: {run_dir}")
    return run_path


def _phase_alias(repo: Path) -> str:
    for key in ("PHASE_LOOP_PHASE_ALIAS", "PHASE_ALIAS"):
        value = os.environ.get(key)
        if value:
            return value
    state_path = repo / ".phase-loop/state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return "unknown"
    value = data.get("current_phase")
    return value if isinstance(value, str) and value else "unknown"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _duration(started: float) -> float:
    return round(time.monotonic() - started, 6)


def _executable_resolves(repo: Path, executable: str) -> bool:
    if not executable:
        return False
    candidate = Path(executable)
    if candidate.is_absolute():
        return candidate.exists() and os.access(candidate, os.X_OK)
    if "/" in executable or "\\" in executable:
        resolved = (repo / candidate).resolve()
        return _is_relative_to(resolved, repo) and resolved.exists() and os.access(resolved, os.X_OK)
    return shutil.which(executable) is not None


def _iter_path_arguments(argv: Sequence[str]) -> list[tuple[int, str]]:
    references: list[tuple[int, str]] = []
    cwd_flags = {"--cwd", "--chdir", "-C"}
    for index, value in enumerate(argv[1:], start=1):
        if value in cwd_flags and index + 1 < len(argv):
            references.append((index + 1, argv[index + 1]))
            continue
        for prefix in ("--cwd=", "--chdir=", "cwd="):
            if value.startswith(prefix):
                references.append((index, value.removeprefix(prefix)))
                break
        else:
            if _looks_path_like(value):
                references.append((index, value))
    return references


def _looks_path_like(value: str) -> bool:
    return value.startswith(("/", "./", "../")) or "/" in value or "\\" in value


def _validate_path_argument(repo: Path, command_index: int, argument_index: int, value: str) -> ValidationFinding | None:
    raw_path = Path(value)
    resolved = raw_path.resolve() if raw_path.is_absolute() else (repo / raw_path).resolve()
    if not _is_relative_to(resolved, repo):
        return ValidationFinding(
            code="outside_repo_path",
            message="verification command references a path outside the repo",
            command_index=command_index,
            argument_index=argument_index,
            value=value,
        )
    if not resolved.exists():
        return ValidationFinding(
            code="missing_path",
            message="verification command references a repo-relative path that does not exist",
            command_index=command_index,
            argument_index=argument_index,
            value=value,
        )
    return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _require_keys(data: Any, keys: set[str]) -> None:
    if not isinstance(data, dict):
        raise ValueError("verification artifact must be an object")
    missing = keys.difference(data)
    if missing:
        raise ValueError(f"verification artifact missing required fields: {', '.join(sorted(missing))}")


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_int(value: Any, field: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_float(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _command_from_payload(data: Any) -> VerificationCommandEvidence:
    _require_keys(data, {"argv", "cwd", "exit_code", "duration_s", "log_offset"})
    return VerificationCommandEvidence(
        argv=[_require_str(item, "argv[]") for item in _require_list(data["argv"], "argv")],
        cwd=_require_str(data["cwd"], "cwd"),
        exit_code=_require_int(data["exit_code"], "exit_code"),
        duration_s=_require_float(data["duration_s"], "duration_s"),
        log_offset=_require_int(data["log_offset"], "log_offset"),
    )


def _env_refresh_from_payload(data: Any) -> VerificationEnvRefreshEvidence:
    _require_keys(data, {"triggered", "manifests", "install_argv", "exit_code"})
    if not isinstance(data["triggered"], bool):
        raise ValueError("triggered must be a boolean")
    return VerificationEnvRefreshEvidence(
        triggered=data["triggered"],
        manifests=[_require_str(item, "manifests[]") for item in _require_list(data["manifests"], "manifests")],
        install_argv=[_require_str(item, "install_argv[]") for item in _require_list(data["install_argv"], "install_argv")],
        exit_code=_require_int(data["exit_code"], "exit_code"),
    )


def _suite_from_payload(data: Any) -> VerificationSuiteEvidence:
    _require_keys(data, {"argv", "exit_code", "duration_s"})
    return VerificationSuiteEvidence(
        argv=[_require_str(item, "argv[]") for item in _require_list(data["argv"], "argv")],
        exit_code=_require_int(data["exit_code"], "exit_code"),
        duration_s=_require_float(data["duration_s"], "duration_s"),
    )
