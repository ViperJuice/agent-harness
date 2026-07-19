"""Regression test for agent-harness#219(a): the verification suite runs under an
interpreter that satisfies the target repo's ``requires-python``.

Mechanism C: an explicit ``automation.python`` pin wins; otherwise auto-resolve
from ``requires-python`` and shim ``python``/``python3`` onto the lowest
satisfying host ``pythonX.Y``. When no satisfying interpreter exists, fail closed
with a named blocker (surfaced as a non-zero suite exit).

"Can it fail?" bar: on a host whose default ``python3`` is below the target floor
(this host: 3.10, target ``>=3.11``), the pre-fix runner ran the suite under the
host default and a ``requires-python>=3.11`` build failed. The resolution below
selects a satisfying interpreter (3.11+), and the end-to-end suite exits 0 only
because the shim is on PATH — reverting the fix runs the host 3.10 and the suite
exits non-zero.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from phase_loop_runtime.verification_evidence import (
    _resolve_suite_interpreter,
    run_verification,
    validate_verification_artifact,
)


def _effective_minor(interp) -> tuple[int, int]:
    """(major, minor) of the interpreter the suite would actually use.

    ah#221: when the host default already satisfies requires-python, the shim dir
    exists only to carry the non-satisfying versioned-name shadows and does NOT
    redirect bare ``python``/``python3`` (so an active venv is preserved). In that
    shadows-only case the suite runs the host default, so fall back to
    ``interp.interpreter`` when the shim has no ``python3`` link.
    """
    shim_python3 = interp.shim_dir / "python3" if interp.shim_dir is not None else None
    if shim_python3 is not None and shim_python3.exists():
        target = shim_python3
    else:
        assert interp.interpreter is not None
        target = Path(interp.interpreter)
    out = subprocess.check_output(
        [str(target), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        text=True,
    ).strip()
    major, minor = out.split(".")
    return (int(major), int(minor))


def _write_pyproject(repo: Path, requires_python: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "target"\nversion = "0.0.0"\nrequires-python = "{requires_python}"\n',
        encoding="utf-8",
    )


def test_suite_interpreter_satisfies_requires_python(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_pyproject(repo, ">=3.11")
    run_path = repo / ".phase-loop" / "run"
    run_path.mkdir(parents=True)

    interp = _resolve_suite_interpreter(repo, run_path, python_pin=None)
    assert interp.blocker is None, f"unexpected blocker: {interp.blocker}"
    # Whether or not a shim was needed, the EFFECTIVE interpreter satisfies the
    # target's requires-python.
    assert _effective_minor(interp) >= (3, 11)


def test_suite_end_to_end_runs_under_satisfying_interpreter(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_pyproject(repo, ">=3.11")
    run_dir = repo / ".phase-loop" / "run"

    result = run_verification(
        repo,
        run_dir,
        commands=[],
        # Exits 0 only if the resolved interpreter is >= 3.11.
        suite_command=["python3", "-c", "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)"],
        env_refresh=None,
        timeout_s=60.0,
    )
    assert result.suite is not None
    assert result.suite.exit_code == 0, "suite must run under a requires-python>=3.11 interpreter"


def test_no_satisfying_interpreter_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_pyproject(repo, ">=3.99")  # impossible on any real host
    run_path = repo / ".phase-loop" / "run"
    run_path.mkdir(parents=True)

    interp = _resolve_suite_interpreter(repo, run_path, python_pin=None)
    assert interp.shim_dir is None
    assert interp.blocker is not None
    assert "requires-python" in interp.blocker

    # End-to-end: the unsatisfiable constraint is recorded as a non-zero suite
    # exit (fail closed), so the downstream evidence gate blocks.
    run_dir = repo / ".phase-loop" / "run2"
    result = run_verification(
        repo,
        run_dir,
        commands=[],
        suite_command=["python3", "-c", "print('should not run')"],
        env_refresh=None,
        timeout_s=60.0,
    )
    assert result.suite is not None
    assert result.suite.exit_code != 0


def test_commands_only_plan_fails_closed_on_unsatisfiable_interpreter(tmp_path):
    """agent-harness#220 round-4 (gemini): a plan with `commands` but NO
    `suite_command`, on a repo whose requires-python no host interpreter can
    satisfy, must NOT run the commands on the host default and pass. The blocker
    previously fenced only the suite, so a commands-only plan bypassed the pin.

    Revert-verify: with the fix reverted, `run_verification` runs the (green)
    command on the host default, produces an all-zero artifact, and the evidence
    gate passes — silently bypassing requires-python.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_pyproject(repo, ">=3.99")  # impossible on any real host
    run_dir = repo / ".phase-loop" / "run"

    result = run_verification(
        repo,
        run_dir,
        # A command that would exit 0 on the host default; it must NOT run.
        commands=[["python3", "-c", "import sys; sys.exit(0)"]],
        suite_command=None,
        env_refresh=None,
        timeout_s=60.0,
    )

    # The whole verification is fenced: env_refresh and the suite are absent, and
    # the artifact carries at least one non-zero exit so the gate hard-blocks.
    assert result.env_refresh is None
    assert result.suite is None
    assert any(cmd.exit_code != 0 for cmd in result.commands), (
        "a commands-only plan on an unsatisfiable interpreter must synthesize a "
        "non-zero command exit, not run the command on the host default"
    )

    # End-to-end: the persisted artifact fails the evidence gate (fail closed).
    artifact = run_dir / "verification.json"
    validation = validate_verification_artifact(artifact)
    assert validation.ok is False
    assert validation.code == "nonzero_exit"


def test_suite_bash_lc_bare_python_runs_under_shim(tmp_path):
    # The documented/dogfood suite form is a login shell invoking a BARE `python`
    # (`bash -lc "... python -m pytest ..."`), not a direct argv. `-l` sources
    # profile files that can reorder PATH, so the shim must still win. Locks that
    # the shim (both `python` and `python3` links) survives the login shell.
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_pyproject(repo, ">=3.11")
    run_dir = repo / ".phase-loop" / "run"

    result = run_verification(
        repo,
        run_dir,
        commands=[],
        suite_command=[
            "bash",
            "-lc",
            "python -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)'",
        ],
        env_refresh=None,
        timeout_s=60.0,
    )
    assert result.suite is not None
    assert result.suite.exit_code == 0, (
        "bare `python` under `bash -lc` must resolve to the requires-python-"
        ">=3.11 interpreter via the shim, not the host default"
    )


@pytest.mark.skipif(shutil.which("python3.12") is None, reason="python3.12 not on host")
def test_automation_python_pin_overrides_autoresolve(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # No requires-python at all — the pin alone drives the choice.
    run_path = repo / ".phase-loop" / "run"
    run_path.mkdir(parents=True)

    interp = _resolve_suite_interpreter(repo, run_path, python_pin="python3.12")
    assert interp.blocker is None
    assert interp.shim_dir is not None
    assert _effective_minor(interp) == (3, 12)

    # A bogus pin fails closed with a named blocker.
    bogus = _resolve_suite_interpreter(repo, run_path, python_pin="python9.9")
    assert bogus.shim_dir is None
    assert bogus.blocker is not None and "python9.9" in bogus.blocker
