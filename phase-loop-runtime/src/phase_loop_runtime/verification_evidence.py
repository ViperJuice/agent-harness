from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 2
# agent-harness#209: v1 artifacts (no per-stage log_end_offset/failure_kind) still
# load — the v2 fields are additive and default to None on a v1 payload.
_SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
ARTIFACT_NAME = "verification.json"
LOG_NAME = "verification.log"

# agent-harness#209: per-stage raw-diagnostic tail cap. Bounds record/memory size;
# it is NOT a secret-leak mitigation (a secret is tiny) — closeout-diagnostic
# redaction is a separate follow-up. See the verification-evidence contract doc.
DIAGNOSTIC_TAIL_BYTES = 4096

# agent-harness#209: typed failure origins, observed by the runner at execution time
# (not re-derived from exit_code — a child that itself returns 124/127 must NOT be
# mislabeled timeout/error).
FAILURE_KIND_TIMEOUT = "timeout"
FAILURE_KIND_ERROR = "error"
FAILURE_KIND_NONZERO_EXIT = "nonzero_exit"
_VALID_FAILURE_KINDS = frozenset({FAILURE_KIND_TIMEOUT, FAILURE_KIND_ERROR, FAILURE_KIND_NONZERO_EXIT})

# ah#90: evidence-provenance labels. Reconcile can adopt completion evidence from more than one
# source; the label makes the provenance explicit so a committed prose closeout can never be read
# as (or masquerade as) a fresh runner verification pass.
EVIDENCE_PROVENANCE_RUNNER_JSON = "runner_verification_json"
EVIDENCE_PROVENANCE_TRACKED_CLOSEOUT = "tracked_closeout_artifact"

# agent-harness#219(a): directories that never carry a target package's own
# requires-python (vendored / build / cache trees). Pruned when scanning for
# pyproject.toml so a big repo scan stays cheap and does not pick up a
# dependency's requires-python by mistake.
_PYPROJECT_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
# Candidate interpreter minor versions, ascending — the lowest satisfying the
# target's requires-python is preferred (matches CI's "run on the floor" posture).
_CANDIDATE_MINORS = tuple(range(8, 15))  # python3.8 .. python3.14


@dataclass(frozen=True)
class SuiteInterpreter:
    """Outcome of resolving a suite interpreter for the target's requires-python.

    ``shim_dir`` — a directory to prepend to the suite subprocess ``PATH``. When a
    below/above-floor bare interpreter had to be redirected it holds
    ``python``/``python3`` links to the satisfying interpreter; when the host default
    already satisfies (ah#221) it holds only the fail-closed shadows of the
    non-satisfying versioned ``python3.X`` names (bare names are left untouched so an
    active venv is preserved). ``None`` only when there is no ``requires-python``
    constraint at all. ``blocker`` — a clear, named reason when no satisfying
    interpreter exists; the caller fails closed. ``interpreter`` — the resolved path,
    for the log.
    """

    shim_dir: "Path | None"
    blocker: str | None
    interpreter: str | None


def _iter_pyproject_files(repo: Path, *, limit: int = 50) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        # Prune vendored/build/cache dirs in place so os.walk skips them.
        dirnames[:] = [d for d in dirnames if d not in _PYPROJECT_PRUNE_DIRS and not d.startswith(".venv")]
        if "pyproject.toml" in filenames:
            found.append(Path(dirpath) / "pyproject.toml")
            if len(found) >= limit:
                break
    return found


def _read_requires_python_specs(repo: Path) -> list[str]:
    """Collect ``requires-python`` specifiers from the target repo's non-vendored
    ``pyproject.toml`` files (regex read — avoids a tomllib/tomli dependency)."""
    specs: list[str] = []
    for pyproject in _iter_pyproject_files(repo):
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if match:
            spec = match.group(1).strip()
            if spec and spec not in specs:
                specs.append(spec)
    return specs


def _version_satisfies(version: str, specs: list[str]) -> bool:
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version
    except ImportError:
        # `packaging` is a hard dependency; only a broken install reaches the simple fallback.
        return _version_satisfies_simple(version, specs)
    try:
        parsed = Version(version)
        return all(parsed in SpecifierSet(spec, prereleases=True) for spec in specs)
    except (InvalidVersion, InvalidSpecifier):
        # ah#221 CR: a malformed version or requires-python specifier cannot be verified — FAIL
        # CLOSED, rather than silently retrying under the permissive fallback and accepting it.
        return False


def _tuple3(version: str) -> tuple[int, int, int]:
    """``(major, minor, micro)`` of a dotted version, zero-padded — so patch-level
    constraints compare correctly in the ``packaging``-unavailable fallback (ah#221 CR)."""
    parts = [int(p) for p in re.findall(r"\d+", version)[:3]]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _version_satisfies_simple(version: str, specs: list[str]) -> bool:
    """Fallback when ``packaging`` (a hard dependency) is unavailable: compare full
    ``(major, minor, micro)`` tuples against comma-separated constraints. Uses the full
    three-component version so a patch-level bound (``>=3.11.5``) is not fail-open. ``==X.Y.*`` /
    ``!=X.Y.*`` wildcards are handled by prefix match. Any clause form this fallback does NOT model
    (``~=``, ``===``, epochs, pre/post/dev releases) — and an unparseable version — FAILS CLOSED
    (returns unsatisfied) rather than silently accepting an interpreter it cannot verify."""
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", version.strip()):
        # Not a plain 1-3 component dotted-numeric version ("garbage3.11.9", "3.11.9pre", or a
        # 4+-component "3.11.0.1" that `_tuple3` would truncate) → fail closed.
        return False
    target = _tuple3(version)
    for spec in specs:
        for clause in spec.split(","):
            clause = clause.strip()
            if not clause:
                continue  # empty clause (e.g. trailing comma) imposes no constraint
            wild = re.fullmatch(r"(==|!=)\s*(\d+(?:\.\d+)*)\.\*", clause)
            if wild:
                prefix = tuple(int(p) for p in wild.group(2).split("."))
                matches = target[: len(prefix)] == prefix
                if wild.group(1) == "==" and not matches:
                    return False
                if wild.group(1) == "!=" and matches:
                    return False
                continue
            # An operator is REQUIRED — a bare version ("3.11") is not a valid PEP 440 specifier and
            # must fail closed, not default to ">=".
            match = re.fullmatch(r"(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})", clause)
            if not match:
                return False  # bare version, or unsupported form (~=, ===, epoch, …) → fail closed
            op = match.group(1)
            bound = _tuple3(match.group(2))
            if op == ">=" and not target >= bound:
                return False
            if op == ">" and not target > bound:
                return False
            if op == "<=" and not target <= bound:
                return False
            if op == "<" and not target < bound:
                return False
            if op == "==" and not target == bound:
                return False
            if op == "!=" and target == bound:
                return False
    return True


def _interpreter_path(name: str) -> Path | None:
    candidate = Path(name)
    if candidate.is_absolute() or os.sep in name or (os.altsep and os.altsep in name):
        return candidate if candidate.exists() and os.access(candidate, os.X_OK) else None
    resolved = shutil.which(name)
    return Path(resolved) if resolved else None


def _interpreter_minor_version(interpreter: Path) -> str | None:
    try:
        out = subprocess.check_output(
            [str(interpreter), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    version = out.strip()
    return version or None


def _lowest_satisfying_interpreter(specs: list[str], repo: "Path | None" = None) -> Path | None:
    for minor in _CANDIDATE_MINORS:
        interpreter = _interpreter_path(f"python3.{minor}")
        if interpreter is None:
            continue
        version = _interpreter_full_version(interpreter, repo)  # full version, repo-context probe
        if version and _version_satisfies(version, specs):
            return interpreter
    return None


# Wide fixed candidate set of versioned python executable names to consider shadowing when a
# requires-python constraint exists (ah#221). Decoupled from `_CANDIDATE_MINORS` (the auto-resolve
# host-probe list, which must stay bounded): writing a fail-closed wrapper is free, so shadow a wide
# minor range plus python2* so an old `python3.7` or a future `python3.15` can't reopen the hole.
_SHADOW_CANDIDATES = tuple(
    [(f"python3.{m}", f"3.{m}") for m in range(0, 40)]
    + [("python2.7", "2.7"), ("python2.6", "2.6"), ("python2", "2.0")]
)


def _interpreter_full_version(interpreter: Path, cwd: "Path | None" = None) -> str | None:
    """``major.minor.micro`` of ``interpreter`` (for patch-level requires-python).

    Probes with ``cwd`` (the target repo) so a version-manager shim (pyenv/asdf) reports the SAME
    interpreter the suite will actually execute under ``cwd=repo`` — otherwise it could report a
    satisfying global version at resolve time while selecting an unsupported one from the repo's
    ``.python-version`` at run time (ah#221 CR)."""
    try:
        out = subprocess.check_output(
            [str(interpreter), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,  # a pathological python3.X must not stall guard construction
            cwd=str(cwd) if cwd is not None else None,
        )
    except Exception:
        return None
    return out.strip() or None


def _nonsatisfying_shadow_names(specs: list[str], repo: "Path | None" = None) -> tuple[str, ...]:
    """Versioned python executable names that do NOT satisfy ``specs`` (empty when no constraint).

    For a candidate that RESOLVES on the host, the executable's FULL (patch-level) version is
    compared, so a patch-bound constraint is handled precisely: ``python3.11`` is shadowed under
    ``<3.11.5`` when the host's is 3.11.9 (fail-closed, not fail-open), and NOT shadowed under
    ``>=3.11.5`` when the host's is 3.11.9 (no false-block). For a candidate not present on the
    host, the nominal ``3.X`` version is used (it cannot be invoked via PATH anyway). Covers
    below-floor AND above a bounded upper bound, and self-excludes any satisfying version.
    """
    if not specs:
        return ()
    names: list[str] = []
    for name, nominal in _SHADOW_CANDIDATES:
        resolved = _interpreter_path(name)
        if resolved is not None:
            full = _interpreter_full_version(resolved, repo)
            if full is None:
                names.append(name)  # present but unprobeable → fail CLOSED (do not trust nominal)
                continue
            version = full
        else:
            version = nominal  # absent: cannot be invoked via PATH anyway
        if not _version_satisfies(version, specs):
            names.append(name)
    return tuple(names)


def _build_interpreter_shim(
    run_path: Path,
    interpreter: "Path | None",
    shadow_names: "tuple[str, ...] | list[str]" = (),
    specs: "list[str] | None" = None,
) -> Path:
    """Build the ``_interp_shim`` PATH dir (ah#219a / ah#221).

    When ``interpreter`` is given, ``python``/``python3`` resolve to it. Each name in
    ``shadow_names`` (e.g. ``python3.10``) is shadowed by a fail-closed wrapper so a suite or
    ``commands`` entry that explicitly names a ``requires-python``-non-satisfying versioned
    interpreter errors instead of running below the floor. Interception is at executable
    resolution, so a string literal / env path mentioning the name is unaffected. An *absolute*
    interpreter path bypasses PATH entirely and is the author's explicit declared choice.
    """
    shim_dir = run_path / "_interp_shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    if interpreter is not None:
        target = interpreter.resolve()
        for name in ("python", "python3"):
            link = shim_dir / name
            try:
                if link.exists() or link.is_symlink():
                    link.unlink()
                os.symlink(target, link)
            except OSError:
                # Fall back to a tiny exec wrapper if symlinks are unavailable.
                link.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
                link.chmod(0o755)
    reason = f"requires-python ({', '.join(specs)})" if specs else "the target's requires-python"
    for name in shadow_names:
        wrapper = shim_dir / name
        if wrapper.exists() or wrapper.is_symlink():
            wrapper.unlink()
        message = (
            f"phase-loop: {name} does not satisfy {reason}; use a satisfying interpreter "
            "(bare python/python3, or an explicit absolute interpreter path)."
        )
        safe = message.replace("'", "'\\''")  # single-quote for /bin/sh, escape embedded quotes
        wrapper.write_text(f"#!/bin/sh\nprintf '%s\\n' '{safe}' >&2\nexit 1\n", encoding="utf-8")
        wrapper.chmod(0o755)
    return shim_dir


def _resolve_suite_interpreter(repo: Path, run_path: Path, python_pin: str | None) -> SuiteInterpreter:
    """Resolve an interpreter satisfying the target repo's ``requires-python``.

    Mechanism C (agent-harness#219(a) + #221): an explicit ``automation.python`` pin
    wins when present; otherwise auto-resolve from ``requires-python`` and shim
    ``python``/``python3`` onto the lowest satisfying host ``pythonX.Y``. When a
    ``requires-python`` constraint exists, the shim ALSO shadows every NON-satisfying
    versioned ``python3.X`` name (below OR above a bounded specifier) with a fail-closed
    wrapper, so a suite/``commands`` entry that explicitly names an unsupported versioned
    interpreter errors instead of running green below/above the floor. This is an
    executable-resolution guard (no command-string parsing of the interpreter), so a versioned
    name inside a string literal or env path is unaffected. A realistic login shell
    (``bash -lc "cmd"``) that re-sources a PATH-reordering profile is handled by re-prepending the
    shim inside the ``-c`` payload (``_relogin_shell_shim``), so the shim wins even against a profile
    that puts a below-floor ``python3.X`` first. Escape hatches (operator's declared environment,
    adversary-equivalent): an *absolute*-path interpreter (``/usr/bin/python3.10``); and — tracked
    for hardening in ah#241 — exotic ``bash --login -O opt -c`` option forms and an interpreter
    absent at resolve time but introduced by the profile under a patch-level constraint. Returns a
    ``shim_dir`` to prepend to the suite ``PATH``, or a named ``blocker`` when none satisfies.
    """
    specs = _read_requires_python_specs(repo)
    # Non-satisfying versioned names to fail-close (only when a constraint exists).
    shadow_names = _nonsatisfying_shadow_names(specs, repo)

    if python_pin:
        # The pin is the operator's explicit interpreter choice — but it must still
        # satisfy the target's requires-python (CR codex#3): a pin below the floor
        # is an authoring error and fails closed rather than running a suite that
        # can't build the package.
        resolved = _interpreter_path(python_pin)
        if resolved is None:
            return SuiteInterpreter(None, f"automation.python pin '{python_pin}' not found on host", None)
        if specs:
            version = _interpreter_full_version(resolved, repo)  # full version, repo-context probe
            if not version or not _version_satisfies(version, specs):
                return SuiteInterpreter(
                    None,
                    f"automation.python pin '{python_pin}' (version {version or 'unknown'}) "
                    f"does not satisfy requires-python ({', '.join(specs)})",
                    None,
                )
        return SuiteInterpreter(
            _build_interpreter_shim(run_path, resolved, shadow_names, specs), None, str(resolved.resolve())
        )

    if not specs:
        return SuiteInterpreter(None, None, None)  # no constraint → host default

    # The suite may invoke bare ``python`` OR ``python3``. Only redirect the bare names when a
    # present one is below the floor — but ALWAYS build a shim so the versioned-name shadows are
    # on PATH (a satisfying bare ``python`` does not protect against an explicit ``python3.10``).
    present = [p for p in (_interpreter_path("python3"), _interpreter_path("python")) if p is not None]
    # Full version — a present bare interpreter whose PATCH version is unsupported (e.g. 3.11.9
    # under <3.11.5), or that cannot be probed, must NOT count as satisfying (else the shadows-only
    # branch would leave it unredirected and the suite would run green under it).
    all_present_ok = bool(present) and all(
        (version := _interpreter_full_version(candidate, repo)) and _version_satisfies(version, specs)
        for candidate in present
    )
    if all_present_ok:
        # Bare names already satisfy: do not redirect them, but still shadow non-satisfying
        # versioned names so a `python3.10` in the suite/commands fails closed.
        return SuiteInterpreter(
            _build_interpreter_shim(run_path, None, shadow_names, specs), None, str(present[0].resolve())
        )

    candidate = _lowest_satisfying_interpreter(specs, repo)
    if candidate is None:
        joined = ", ".join(specs)
        return SuiteInterpreter(None, f"no host interpreter satisfies requires-python ({joined})", None)
    return SuiteInterpreter(
        _build_interpreter_shim(run_path, candidate, shadow_names, specs), None, str(candidate.resolve())
    )


@dataclass(frozen=True)
class VerificationCommandEvidence:
    argv: list[str]
    cwd: str
    exit_code: int
    duration_s: float
    log_offset: int
    # agent-harness#209 (schema v2, additive): exact end of this stage's bytes in
    # verification.log + the runner-observed failure origin. None on a v1 artifact.
    log_end_offset: int | None = None
    failure_kind: str | None = None


@dataclass(frozen=True)
class VerificationEnvRefreshEvidence:
    triggered: bool
    manifests: list[str]
    install_argv: list[str]
    exit_code: int
    log_offset: int | None = None
    log_end_offset: int | None = None
    failure_kind: str | None = None


@dataclass(frozen=True)
class VerificationSuiteEvidence:
    argv: list[str]
    exit_code: int
    duration_s: float
    log_offset: int | None = None
    log_end_offset: int | None = None
    failure_kind: str | None = None


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
    operational_exemptions: list[dict[str, Any]] | None = None


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
    # agent-harness#209: per-failing-stage raw diagnostics (typed failure_kind +
    # bounded raw-output tail, in declared order). Populated only on the
    # nonzero_exit branch (authenticated log). Empty on ok / integrity failures.
    diagnostics: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "artifact_path": self.artifact_path,
            "log_path": self.log_path,
            "exit_summary": self.exit_summary or {},
            "findings": list(self.findings),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }


def run_verification(
    repo: Path,
    run_dir: Path,
    commands: list[list[str]],
    suite_command: list[str] | None,
    env_refresh: object,
    timeout_s: float | None,
    operational_exemptions: list[Mapping[str, Any]] | None = None,
    python_pin: str | None = None,
    phase_alias: str | None = None,
) -> VerificationResult:
    repo_path = _resolve_repo(repo)
    run_path = _resolve_run_dir(repo_path, run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    log_path = run_path / LOG_NAME
    artifact_path = run_path / ARTIFACT_NAME
    started_at = _utc_now()

    # agent-harness#219(a): resolve an interpreter satisfying the target repo's
    # requires-python (honoring an automation.python pin) and shim it onto the
    # verification subprocess PATH, so a bare ``python`` in the suite/commands is
    # not silently the host default that fails a requires-python>=3.11 build.
    interpreter = _resolve_suite_interpreter(repo_path, run_path, python_pin)
    shim_dir = interpreter.shim_dir

    with log_path.open("wb") as log_file:
        if interpreter.blocker:
            # Fail closed for the WHOLE verification (agent-harness#220 round-4,
            # gemini): the resolved interpreter cannot satisfy the target's
            # requires-python (or a pin below the floor), so run NOTHING on the
            # wrong interpreter — not env_refresh, not the commands, not the suite.
            # A green host-default exit must never yield a `passed` artifact.
            # Previously the blocker fenced ONLY the suite, so a plan with
            # `commands` but NO `suite_command` still ran env_refresh + commands on
            # the host default and could pass, bypassing the pin/requires-python.
            # Synthesize a non-zero result so `_nonzero_exit_findings` hard-blocks
            # the evidence gate even for a commands-only plan.
            log_file.write(f"suite interpreter unavailable: {interpreter.blocker}\n".encode("utf-8"))
            log_file.flush()
            # agent-harness#209: this synthetic 127 evidence is built OUTSIDE
            # ``_run_process``; point its log region at the "unavailable" line above
            # and tag failure_kind=error so the diagnostic surfaces the blocker
            # reason (requires-python / pin mismatch) instead of degrading to a
            # scrubbed ``missing_output``.
            blocker_end = log_file.tell()
            env_result = None
            if suite_command is not None:
                command_results = []
                suite_result = VerificationSuiteEvidence(
                    argv=list(suite_command),
                    exit_code=127,
                    duration_s=0.0,
                    log_offset=0,
                    log_end_offset=blocker_end,
                    failure_kind=FAILURE_KIND_ERROR,
                )
            else:
                # No suite to carry the non-zero exit — synthesize a 127 command so
                # the exit_summary has a non-zero entry the gate blocks on.
                command_results = [
                    VerificationCommandEvidence(
                        argv=["<interpreter-unavailable>"],
                        cwd=str(repo_path),
                        exit_code=127,
                        duration_s=0.0,
                        log_offset=0,
                        log_end_offset=blocker_end,
                        failure_kind=FAILURE_KIND_ERROR,
                    )
                ]
                suite_result = None
        else:
            if interpreter.interpreter is not None:
                log_file.write(f"suite interpreter: {interpreter.interpreter}\n".encode("utf-8"))
                log_file.flush()
            env_result = _record_env_refresh(
                repo_path,
                log_file,
                env_refresh,
                timeout_s,
                path_prepend=shim_dir,
                # CR codex#4: install deps under the SAME interpreter the suite runs
                # under (the resolved/shimmed one), not the host `sys.executable`.
                suite_interpreter=interpreter.interpreter,
            )
            command_results = [
                _run_process(repo_path, log_file, argv, timeout_s, path_prepend=shim_dir) for argv in commands
            ]
            suite_result = None
            if suite_command is not None:
                suite_evidence = _run_process(repo_path, log_file, suite_command, timeout_s, path_prepend=shim_dir)
                suite_result = VerificationSuiteEvidence(
                    argv=suite_evidence.argv,
                    exit_code=suite_evidence.exit_code,
                    duration_s=suite_evidence.duration_s,
                    # agent-harness#209: carry the suite's exact log region + observed
                    # failure origin (previously discarded) so the gate can localize
                    # a suite failure's diagnostic.
                    log_offset=suite_evidence.log_offset,
                    log_end_offset=suite_evidence.log_end_offset,
                    failure_kind=suite_evidence.failure_kind,
                )

    finished_at = _utc_now()
    log_sha256 = hashlib.sha256(log_path.read_bytes()).hexdigest()
    result = VerificationResult(
        schema_version=SCHEMA_VERSION,
        run_id=run_path.name,
        phase_alias=_phase_alias(repo_path, phase_alias),
        commands=command_results,
        env_refresh=env_result,
        suite=suite_result,
        started_at=started_at,
        finished_at=finished_at,
        log_sha256=log_sha256,
        operational_exemptions=[dict(item) for item in operational_exemptions or []],
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
        executable = _executable_argv(argv)[0] if _executable_argv(argv) else ""
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
    if data["schema_version"] not in _SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported verification evidence schema_version: {data['schema_version']}")
    commands = [_command_from_payload(item) for item in _require_list(data["commands"], "commands")]
    env_refresh = None
    if data["env_refresh"] is not None:
        env_refresh = _env_refresh_from_payload(data["env_refresh"])
    suite = None
    if data["suite"] is not None:
        suite = _suite_from_payload(data["suite"])
    _validate_v2_failure_kinds(int(data["schema_version"]), commands, suite, env_refresh)
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


def _validate_v2_failure_kinds(
    schema_version: int,
    commands: list[VerificationCommandEvidence],
    suite: "VerificationSuiteEvidence | None",
    env_refresh: "VerificationEnvRefreshEvidence | None",
) -> None:
    """CR codex#2 (round 2) + Fable (round 3): a schema-v2 SUBPROCESS-backed stage that
    FAILED must carry a runner-observed ``failure_kind`` — the runner always records one,
    so its absence means a tampered/malformed artifact (a v2 timeout with its field
    stripped would otherwise load and mislabel as ``nonzero_exit``). A stage is
    subprocess-backed iff it recorded a ``log_offset`` (an int): commands and suite always
    are; an env_refresh is only exempt when it ran NO subprocess (externally-supplied
    ``exit_code`` -> ``log_offset is None``). Also enforce the runner's own invariants:
    ``timeout`` <=> exit 124 and ``error`` <=> exit 127, so a valid-enum-but-inconsistent
    value is rejected. (The reverse — a child that itself exits 124/127 relabelled
    ``nonzero_exit`` — is intentionally NOT rejected: it is indistinguishable from a
    genuine child 124/127 in the frozen artifact, a documented limitation.) v1 artifacts
    are exempt (the field did not exist)."""
    if schema_version < 2:
        return
    stages: list[tuple[str, Any]] = [("commands", c) for c in commands]
    if suite is not None:
        stages.append(("suite", suite))
    if env_refresh is not None and isinstance(getattr(env_refresh, "log_offset", None), int):
        stages.append(("env_refresh", env_refresh))
    for label, stage in stages:
        if stage.exit_code == 0:
            continue
        if stage.failure_kind is None:
            raise ValueError(f"{label} failed (exit={stage.exit_code}) but v2 evidence omits failure_kind")
        if stage.failure_kind == FAILURE_KIND_TIMEOUT and stage.exit_code != 124:
            raise ValueError(f"{label} failure_kind=timeout inconsistent with exit_code={stage.exit_code}")
        if stage.failure_kind == FAILURE_KIND_ERROR and stage.exit_code != 127:
            raise ValueError(f"{label} failure_kind=error inconsistent with exit_code={stage.exit_code}")


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
        log_bytes = log_path.read_bytes()
    except OSError as exc:
        return VerificationArtifactValidation(
            ok=False,
            code="missing_log",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            exit_summary=_exit_summary(result),
            findings=(str(exc),),
        )
    actual_log_sha256 = hashlib.sha256(log_bytes).hexdigest()
    if actual_log_sha256 != result.log_sha256:
        # agent-harness#209: the log is UNAUTHENTICATED here — do NOT surface a raw
        # tail from it (it could be forged/misleading). The verdict blocks on the
        # more-severe integrity failure, not a stage diagnostic.
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
        # agent-harness#209: the log is AUTHENTICATED at this point (sha matched), so
        # a bounded per-stage raw tail is trustworthy. Localize each failing stage.
        diagnostics = _build_failure_diagnostics(result, log_bytes)
        # Anti-scrubbing invariant (structural, NOT a strippable assert): a
        # nonzero-exit-class failed verdict can never carry empty diagnostics.
        # ``_build_failure_diagnostics`` iterates the identical stage set as
        # ``_nonzero_exit_findings``, so this is non-empty by construction; the guard
        # below fails closed to the raw findings if that invariant is ever violated.
        if not diagnostics:  # pragma: no cover - construction guarantees non-empty
            diagnostics = tuple(
                {"role": "unknown", "index": None, "argv": [], "exit_code": None,
                 "failure_kind": FAILURE_KIND_NONZERO_EXIT, "raw_tail": finding,
                 "truncated": False, "diagnostic_status": "present"}
                for finding in nonzero
            )
        return VerificationArtifactValidation(
            ok=False,
            code="nonzero_exit",
            artifact_path=str(artifact_path),
            log_path=str(log_path),
            exit_summary=_exit_summary(result),
            findings=tuple(nonzero),
            diagnostics=diagnostics,
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


def detect_changed_dependency_manifests(repo: Path, base_ref: str, head_ref: str | None = None) -> list[str]:
    repo_path = _resolve_repo(repo)
    command = ["git", "diff", "--name-only", base_ref]
    if head_ref:
        command.append(head_ref)
    try:
        completed = subprocess.run(
            command,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode not in {0, 1}:
        return []
    manifests: list[str] = []
    for line in completed.stdout.splitlines():
        path = line.strip()
        if not path or path.startswith("../") or path.startswith("/"):
            continue
        name = PurePath(path).name
        if name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "pyproject.toml", "uv.lock"}:
            manifests.append(path)
        elif name.startswith("requirements") and name.endswith(".txt"):
            manifests.append(path)
    return sorted(dict.fromkeys(manifests))


def resolve_install_command(repo: Path, manifests: list[str]) -> list[str] | None:
    repo_path = _resolve_repo(repo)
    names = {PurePath(path).name for path in manifests}
    if names.intersection({"package.json", "package-lock.json"}):
        return ["npm", "install"]
    if "pnpm-lock.yaml" in names:
        return ["pnpm", "install"]
    if "uv.lock" in names or ("pyproject.toml" in names and shutil.which("uv")):
        return ["uv", "sync"]
    requirements = sorted(name for name in names if name.startswith("requirements") and name.endswith(".txt"))
    if requirements:
        return [sys.executable, "-m", "pip", "install", "-r", requirements[0]]
    if "pyproject.toml" in names and (repo_path / "pyproject.toml").exists():
        return [sys.executable, "-m", "pip", "install", "-e", "."]
    return None


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


def _stage_bounds(result: VerificationResult, log_len: int) -> dict[int, tuple[int, int]]:
    """CR (rounds 2-3): each stage's authoritative byte window ``(lower, upper)`` from its
    EXECUTION-order neighbours (env_refresh -> commands -> suite) — ``upper`` = the NEXT
    stage's recorded ``log_offset`` (``log_len`` for the last), ``lower`` = the PREVIOUS
    stage's recorded ``log_end_offset`` (``0`` for the first). Both edges are needed:
    round 2 (codex+gemini) showed a tampered END leaking the NEXT stage's bytes; round 3
    (Fable) showed a tampered backward START leaking a PRECEDING stage's bytes. Positional
    (not a sorted/strict-``>`` scan) so a zero-output stage that shares a start offset with
    its neighbour is still bounded correctly. Keyed by ``id(stage)`` (distinct instances)."""
    ordered: list[Any] = []
    if result.env_refresh is not None and isinstance(getattr(result.env_refresh, "log_offset", None), int):
        ordered.append(result.env_refresh)
    for command in result.commands:
        if isinstance(command.log_offset, int):
            ordered.append(command)
    if result.suite is not None and isinstance(getattr(result.suite, "log_offset", None), int):
        ordered.append(result.suite)
    bounds: dict[int, tuple[int, int]] = {}
    for i, stage in enumerate(ordered):
        prev_end = ordered[i - 1].log_end_offset if i > 0 else 0
        lower = prev_end if isinstance(prev_end, int) else 0
        upper = ordered[i + 1].log_offset if i + 1 < len(ordered) else log_len
        bounds[id(stage)] = (lower, upper)
    return bounds


def _bounded_tail(region: bytes) -> tuple[str, bool]:
    """CR Fable#1: return a tail whose UTF-8 byte length is <= DIAGNOSTIC_TAIL_BYTES
    even for multibyte / binary output. Slice bytes, decode, then hard-trim on a char
    boundary so ``errors='replace'`` (3 bytes per bad byte) can't blow the cap."""
    truncated = len(region) > DIAGNOSTIC_TAIL_BYTES
    text = region[-DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    if len(encoded) > DIAGNOSTIC_TAIL_BYTES:
        truncated = True
        # drop a possibly-partial leading char so the final string is valid + bounded
        text = encoded[-DIAGNOSTIC_TAIL_BYTES:].decode("utf-8", errors="ignore")
    return text, truncated


def _stage_raw_tail(log_bytes: bytes, stage: Any, lower: int, upper: int) -> tuple[str, bool, bool]:
    """agent-harness#209: slice a failing stage's OWN bytes from verification.log using
    its recorded ``[log_offset, log_end_offset)`` region (captured at run time, never
    reconstructed post-hoc). Returns (tail, truncated, range_valid). The recorded range is
    VALIDATED against BOTH execution-order neighbour bounds, not clamped-and-guessed: in the
    normal flow stages are written contiguously so ``start`` == ``lower`` (prev stage's end)
    and ``end`` == ``upper`` (next stage's start) exactly. Any range violating
    ``lower <= start <= end <= upper`` (with ``0 <= lower`` and ``upper <= len(log)``) is a
    tampered/malformed artifact — FAIL CLOSED to an empty tail rather than surface bytes
    that may belong to another stage. Round 2 (codex+gemini) needed the upper bound (a
    tampered END leaking the NEXT stage); round 3 (Fable) needed the lower bound (a tampered
    backward START leaking a PRECEDING stage). A stage with no recorded region (v1 artifact,
    or an env_refresh that ran no subprocess) yields ``("", False, False)`` — the caller
    flags ``missing_output`` while still emitting typed exit context."""
    start = getattr(stage, "log_offset", None)
    end = getattr(stage, "log_end_offset", None)
    if not isinstance(start, int) or not isinstance(end, int):
        return "", False, False
    if not (0 <= lower <= start <= end <= upper <= len(log_bytes)):
        return "", False, False
    if end == start:
        return "", False, True  # a legitimately output-less stage (range valid, empty)
    tail, truncated = _bounded_tail(log_bytes[start:end])
    return tail, truncated, True


def _stage_diagnostic(
    role: str, index: int | None, argv: list[str], stage: Any, log_bytes: bytes, bounds: tuple[int, int]
) -> dict[str, Any]:
    lower, upper = bounds
    raw_tail, truncated, _range_valid = _stage_raw_tail(log_bytes, stage, lower, upper)
    # CR codex#2: only fall back to nonzero_exit when the stage records NO origin
    # (v1 artifact, or an env_refresh with an externally-supplied exit_code that ran no
    # subprocess). A recorded-but-unknown value is rejected at load, so it can't reach
    # here — this default never escalates to a fabricated timeout/error.
    failure_kind = getattr(stage, "failure_kind", None) or FAILURE_KIND_NONZERO_EXIT
    return {
        "role": role,
        "index": index,
        "argv": list(argv),
        "exit_code": stage.exit_code,
        "failure_kind": failure_kind,
        "raw_tail": raw_tail,
        "truncated": truncated,
        # codex plan-review: distinguish a silent (zero-byte) failure from one whose
        # output we preserved — a scrubbed/output-less failure is itself suspicious.
        "diagnostic_status": "present" if raw_tail else "missing_output",
    }


def _build_failure_diagnostics(result: VerificationResult, log_bytes: bytes) -> tuple[dict[str, Any], ...]:
    """Per-failing-stage typed diagnostics in the SAME declared order
    ``_nonzero_exit_findings`` traverses (commands -> env_refresh -> suite). Guaranteed
    non-empty whenever ``_nonzero_exit_findings`` is non-empty (identical stage set),
    which structurally enforces the #209 anti-scrubbing rule."""
    bounds = _stage_bounds(result, len(log_bytes))
    full = (0, len(log_bytes))

    diagnostics: list[dict[str, Any]] = []
    for index, command in enumerate(result.commands):
        if command.exit_code != 0:
            diagnostics.append(
                _stage_diagnostic("command", index, command.argv, command, log_bytes, bounds.get(id(command), full))
            )
    if result.env_refresh is not None and result.env_refresh.exit_code != 0:
        diagnostics.append(
            _stage_diagnostic(
                "env_refresh", None, result.env_refresh.install_argv, result.env_refresh, log_bytes,
                bounds.get(id(result.env_refresh), full),
            )
        )
    if result.suite is not None and result.suite.exit_code != 0:
        diagnostics.append(
            _stage_diagnostic("suite", None, result.suite.argv, result.suite, log_bytes, bounds.get(id(result.suite), full))
        )
    return tuple(diagnostics)


_LOGIN_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})


def _relogin_shell_shim(argv: list[str], shim_dir: "Path | None") -> list[str]:
    """ah#221: a LOGIN shell (``bash -lc``) re-sources profile files that can prepend a below-floor
    ``python3.X`` AHEAD of the shim on PATH, defeating the guard. Re-prepend the shim dir at the
    start of the ``-c`` payload — which runs AFTER profile loading — so the shim wins again. Only
    the login (``-l``/``--login``) + ``-c`` shell form is rewritten; a non-login shell does not
    source profiles, and a direct-argv invocation is already covered by the PATH prepend."""
    if shim_dir is None or not argv:
        return argv
    if os.path.basename(argv[0]) not in _LOGIN_SHELLS:
        return argv
    is_login = False
    c_index: int | None = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--login":
            is_login = True
        elif a == "-c":
            c_index = i + 1
            break
        elif a.startswith("-") and not a.startswith("--"):
            # combined short flags, e.g. -lc, -il
            if "l" in a[1:]:
                is_login = True
            if "c" in a[1:]:
                c_index = i + 1
                break
        else:
            break  # a non-flag arg before -c: not a form we rewrite
        i += 1
    if not is_login or c_index is None or c_index >= len(argv):
        return argv
    if argv[c_index].startswith("-"):
        # The command string is the first NON-option arg after `-c`; an option or `--` may precede
        # it (`bash -lc -- 'cmd'`, `bash -lc -x 'cmd'`). Don't rewrite an ambiguous form — fail
        # open to the plain PATH prepend rather than corrupt the option slot.
        return argv
    shim_q = "'" + str(shim_dir).replace("'", "'\\''") + "'"  # single-quote the literal path
    rewritten = list(argv)
    rewritten[c_index] = f'export PATH={shim_q}:"$PATH"; ' + rewritten[c_index]
    return rewritten


def _run_process(
    repo: Path,
    log_file: Any,
    argv: Sequence[str],
    timeout_s: float | None,
    path_prepend: Path | None = None,
) -> VerificationCommandEvidence:
    command_argv = [str(part) for part in argv]
    process_env, process_argv = _process_env_and_argv(command_argv)
    if path_prepend is not None:
        # agent-harness#219(a): prepend the resolved-interpreter shim so a bare
        # ``python``/``python3`` in the command resolves to the satisfying
        # interpreter rather than the host default.
        process_env = dict(process_env if process_env is not None else os.environ)
        process_env["PATH"] = f"{path_prepend}{os.pathsep}{process_env.get('PATH', '')}"
        # ah#221: keep the shim winning inside a login shell whose profile reorders PATH.
        process_argv = _relogin_shell_shim(process_argv, path_prepend)
    offset = log_file.tell()
    started = time.monotonic()
    # agent-harness#209: failure_kind is the runner-OBSERVED origin (which branch
    # ran), never re-derived from exit_code downstream — a child that itself returns
    # 124/127 stays nonzero_exit, not timeout/error.
    failure_kind: str | None = None
    if not process_argv:
        log_file.write(b"empty verification command argv\n")
        log_file.flush()
        return VerificationCommandEvidence(
            command_argv, ".", 127, _duration(started), offset,
            log_end_offset=log_file.tell(), failure_kind=FAILURE_KIND_ERROR,
        )
    try:
        completed = subprocess.run(
            process_argv,
            cwd=repo,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s if timeout_s and timeout_s > 0 else None,
            check=False,
        )
        output = completed.stdout or b""
        log_file.write(output)
        log_file.flush()
        exit_code = int(completed.returncode)
        if exit_code != 0:
            failure_kind = FAILURE_KIND_NONZERO_EXIT
    except subprocess.TimeoutExpired as exc:
        output = exc.output or b""
        if isinstance(output, str):
            output = output.encode("utf-8", errors="replace")
        log_file.write(output)
        log_file.write(f"\nverification command timed out after {timeout_s}s\n".encode("utf-8"))
        log_file.flush()
        exit_code = 124
        failure_kind = FAILURE_KIND_TIMEOUT
    except OSError as exc:
        # CR codex#3: not only FileNotFoundError — a PermissionError (non-executable
        # target) or OSError(ENOEXEC) (bad interpreter/binary) is ALSO a stage failure,
        # not an exception that should crash the whole verification. Record it as
        # evidence (exit 127, failure_kind=error) so a diagnostic is still produced.
        log_file.write(f"{exc}\n".encode("utf-8", errors="replace"))
        log_file.flush()
        exit_code = 127
        failure_kind = FAILURE_KIND_ERROR
    return VerificationCommandEvidence(
        command_argv, ".", exit_code, _duration(started), offset,
        log_end_offset=log_file.tell(), failure_kind=failure_kind,
    )


def _is_pip_invocation(argv: Sequence[str]) -> bool:
    return len(argv) >= 3 and str(argv[1]) == "-m" and str(argv[2]) == "pip"


def _align_install_interpreter(install_argv: list[str], suite_interpreter: str | None) -> list[str]:
    """CR codex#4 / gemini#4: run a pip env-refresh under the SAME interpreter the
    suite runs under. ``resolve_install_command`` bakes in ``sys.executable``, but
    the suite runs under the resolved/shimmed interpreter — so deps would install
    for the wrong Python (→ ModuleNotFoundError → spurious fail-closed block).
    Substitute the pip interpreter only; leave npm/uv/pnpm untouched.
    """
    if suite_interpreter and _is_pip_invocation(install_argv):
        return [suite_interpreter, *install_argv[1:]]
    return install_argv


def _record_env_refresh(
    repo: Path,
    log_file: Any,
    env_refresh: object,
    timeout_s: float | None,
    path_prepend: Path | None = None,
    suite_interpreter: str | None = None,
) -> VerificationEnvRefreshEvidence | None:
    if env_refresh is None:
        return None
    if isinstance(env_refresh, Mapping):
        triggered = bool(env_refresh.get("triggered", True))
        manifests = [str(item) for item in env_refresh.get("manifests", [])]
        install_argv = [str(item) for item in env_refresh.get("install_argv", env_refresh.get("argv", []))]
        install_argv = _align_install_interpreter(install_argv, suite_interpreter)
        if "exit_code" in env_refresh:
            return VerificationEnvRefreshEvidence(triggered, manifests, install_argv, int(env_refresh["exit_code"]))
        if not triggered or not install_argv:
            return VerificationEnvRefreshEvidence(triggered, manifests, install_argv, 0)
    else:
        triggered = True
        manifests = []
        install_argv = [str(item) for item in env_refresh] if isinstance(env_refresh, Sequence) and not isinstance(env_refresh, (str, bytes)) else []
        install_argv = _align_install_interpreter(install_argv, suite_interpreter)
    process = _run_process(repo, log_file, install_argv, timeout_s, path_prepend=path_prepend)
    # agent-harness#209: carry the env-refresh subprocess's exact log region + observed
    # failure origin so an env_refresh failure (at the HEAD of the log) is localized
    # precisely, not lost to a whole-log tail.
    return VerificationEnvRefreshEvidence(
        triggered, manifests, install_argv, process.exit_code,
        log_offset=process.log_offset,
        log_end_offset=process.log_end_offset,
        failure_kind=process.failure_kind,
    )


def _result_to_payload(result: VerificationResult) -> dict[str, Any]:
    payload = {
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
    if result.operational_exemptions:
        payload["operational_exemptions"] = list(result.operational_exemptions)
    return payload


def _apply_stage_v2_fields(payload: dict[str, Any], stage: Any) -> dict[str, Any]:
    # agent-harness#209: additive schema-v2 stage fields, omit-if-None. On a v2 run the
    # offsets are always present; failure_kind is omitted on a passing stage (None) and
    # a v1-shaped stage omits all three.
    if getattr(stage, "log_end_offset", None) is not None:
        payload["log_end_offset"] = stage.log_end_offset
    if getattr(stage, "failure_kind", None) is not None:
        payload["failure_kind"] = stage.failure_kind
    if getattr(stage, "log_offset", None) is not None and "log_offset" not in payload:
        payload["log_offset"] = stage.log_offset
    return payload


def _command_to_payload(command: VerificationCommandEvidence) -> dict[str, Any]:
    return _apply_stage_v2_fields(
        {
            "argv": list(command.argv),
            "cwd": command.cwd,
            "exit_code": command.exit_code,
            "duration_s": command.duration_s,
            "log_offset": command.log_offset,
        },
        command,
    )


def _env_refresh_to_payload(env_refresh: VerificationEnvRefreshEvidence) -> dict[str, Any]:
    return _apply_stage_v2_fields(
        {
            "triggered": env_refresh.triggered,
            "manifests": list(env_refresh.manifests),
            "install_argv": list(env_refresh.install_argv),
            "exit_code": env_refresh.exit_code,
        },
        env_refresh,
    )


def _suite_to_payload(suite: VerificationSuiteEvidence) -> dict[str, Any]:
    return _apply_stage_v2_fields(
        {
            "argv": list(suite.argv),
            "exit_code": suite.exit_code,
            "duration_s": suite.duration_s,
        },
        suite,
    )


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


def _phase_alias(repo: Path, provided: str | None = None) -> str:
    # ah#85: resolve the verification artifact's phase alias, preferring (in order) an
    # explicit operator env override, then a ``provided`` LIVE run alias threaded by the
    # caller (the run that actually produced this verification), then ``current_phase``
    # from state.json. Threading ``provided`` stops verification.json from mis-attributing
    # the phase after a mid-run roadmap amendment changes ``current_phase`` (the env
    # escape-hatch still wins; the state.json read remains the last-resort fallback for
    # callers with no live run alias, e.g. hotfix / train re-verify).
    for key in ("PHASE_LOOP_PHASE_ALIAS", "PHASE_ALIAS"):
        value = os.environ.get(key)
        if value:
            return value
    if provided:
        return provided
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
        if _is_env_assignment(value):
            continue
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


def _executable_argv(argv: Sequence[str]) -> list[str]:
    return [str(part) for part in argv if not _is_env_assignment(str(part))]


def _process_env_and_argv(argv: Sequence[str]) -> tuple[dict[str, str] | None, list[str]]:
    env = os.environ.copy()
    command: list[str] = []
    env_changed = False
    for index, part in enumerate(argv):
        value = str(part)
        if not command and _is_env_assignment(value):
            key, raw = value.split("=", 1)
            env[key] = raw
            env_changed = True
            continue
        command = [str(item) for item in argv[index:]]
        break
    return (env if env_changed else None), command


def _is_env_assignment(value: str) -> bool:
    key, sep, _raw = value.partition("=")
    return bool(sep) and key.replace("_", "").isalnum() and key[0].isalpha()


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


def _optional_int(data: Any, key: str) -> int | None:
    # agent-harness#209: v2 stage fields are optional; a v1 artifact omits them.
    value = data.get(key) if isinstance(data, Mapping) else None
    if value is None:
        return None
    return _require_int(value, key)


def _optional_str(data: Any, key: str) -> str | None:
    value = data.get(key) if isinstance(data, Mapping) else None
    if value is None:
        return None
    return _require_str(value, key)


def _optional_failure_kind(data: Any) -> str | None:
    # CR codex#2: failure_kind is a closed enum — reject an unknown value at load
    # (-> malformed_artifact) rather than passing garbage through as if it were a
    # runner-observed origin. None is allowed (v1 / no origin recorded).
    value = _optional_str(data, "failure_kind")
    if value is not None and value not in _VALID_FAILURE_KINDS:
        raise ValueError(f"invalid failure_kind: {value!r}")
    return value


def _command_from_payload(data: Any) -> VerificationCommandEvidence:
    _require_keys(data, {"argv", "cwd", "exit_code", "duration_s", "log_offset"})
    return VerificationCommandEvidence(
        argv=[_require_str(item, "argv[]") for item in _require_list(data["argv"], "argv")],
        cwd=_require_str(data["cwd"], "cwd"),
        exit_code=_require_int(data["exit_code"], "exit_code"),
        duration_s=_require_float(data["duration_s"], "duration_s"),
        log_offset=_require_int(data["log_offset"], "log_offset"),
        log_end_offset=_optional_int(data, "log_end_offset"),
        failure_kind=_optional_failure_kind(data),
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
        log_offset=_optional_int(data, "log_offset"),
        log_end_offset=_optional_int(data, "log_end_offset"),
        failure_kind=_optional_failure_kind(data),
    )


def _suite_from_payload(data: Any) -> VerificationSuiteEvidence:
    _require_keys(data, {"argv", "exit_code", "duration_s"})
    return VerificationSuiteEvidence(
        argv=[_require_str(item, "argv[]") for item in _require_list(data["argv"], "argv")],
        exit_code=_require_int(data["exit_code"], "exit_code"),
        duration_s=_require_float(data["duration_s"], "duration_s"),
        log_offset=_optional_int(data, "log_offset"),
        log_end_offset=_optional_int(data, "log_end_offset"),
        failure_kind=_optional_failure_kind(data),
    )
