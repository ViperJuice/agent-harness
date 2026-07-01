"""Tests for the harness-neutral repo-validation resolver (repo_validation.py).

These cover the three required behaviors of the neutral contract resolver:
  * discovery HIT   -- just-module, flat just recipe, and package.json script;
  * discovery MISS  -- unmigrated repos FAIL CLOSED (exit 20);
  * exit-code MAP   -- 0 (ok), 30 (command failed), 21 (runner missing),
                       10 (not a git work tree), 2 (usage).

The resolver splits pure discovery/planning (`resolve`) from the subprocess
(`run_plan`), so the map is exercised without a live `just`/`npm` by pointing
PATH at a temp bin dir holding fake runner shims with chosen exit codes.

CLI-INDEPENDENT: this module imports only `phase_loop_runtime` library code and
exercises the CLI via `main([...])`. It registers no dotfiles profile.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from phase_loop_runtime import repo_validation as rv
from phase_loop_runtime.cli import main


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _fake_bin(bin_dir: Path, name: str, exit_code: int) -> Path:
    """Create an executable shim named `name` that always exits `exit_code`."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / name
    # Absolute interpreter so the shim still runs when PATH is isolated to bin_dir.
    shim.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def _isolate_path(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    """Restrict PATH to `bin_dir` so shutil.which sees only planted shims."""

    bin_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", str(bin_dir))


_JUST_MODULE_JUSTFILE = "mod agent\n"
_AGENT_JUST = "gate:\n\techo gate-ran\nfast:\n\techo fast-ran\n"


# --------------------------------------------------------------------------- #
# discovery HIT
# --------------------------------------------------------------------------- #
def test_discovers_just_module_contract(tmp_path: Path) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    assert rv.just_contract_command(tmp_path, "gate") == "agent::gate"
    assert rv.just_contract_command(tmp_path, "fast") == "agent::fast"
    # A target the module does not declare is not discovered.
    assert rv.just_contract_command(tmp_path, "full") is None


def test_discovers_mod_agent_optional_and_skips_comments(tmp_path: Path) -> None:
    _write(tmp_path, "justfile", "# mod agent  <- commented, must be ignored\nmod? agent\n")
    _write(tmp_path, "agent.just", _AGENT_JUST)
    assert rv.has_agent_just_module(tmp_path / "justfile") is True
    assert rv.just_contract_command(tmp_path, "gate") == "agent::gate"


def test_discovers_flat_just_recipe(tmp_path: Path) -> None:
    # No `mod agent`; a flat `agent:gate` recipe in the root Justfile.
    _write(tmp_path, "Justfile", "agent:gate:\n\techo flat-gate\n")
    assert rv.just_contract_command(tmp_path, "gate") == "agent:gate"


def test_discovers_package_script(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"scripts": {"agent:gate": "vitest run"}}')
    assert rv.package_has_script(tmp_path, "agent:gate") is True
    assert rv.package_has_script(tmp_path, "agent:full") is False


def test_package_has_script_tolerates_broken_json(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{ not valid json")
    assert rv.package_has_script(tmp_path, "agent:gate") is False


# --------------------------------------------------------------------------- #
# discovery MISS -> fail closed (exit 20)
# --------------------------------------------------------------------------- #
def test_unmigrated_repo_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind is None
    assert plan.exit_code == rv.EXIT_NO_CONTRACT == 20
    assert rv.run_plan(plan) == 20


def test_never_guesses_native_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A repo with a Makefile/pytest present but NO agent contract still fails closed.
    _isolate_path(monkeypatch, tmp_path / "bin")
    _write(tmp_path, "Makefile", "test:\n\tpytest\n")
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.exit_code == 20
    assert plan.argv is None


# --------------------------------------------------------------------------- #
# exit-code MAP: 0 / 30 via a runnable plan and a fake `just`
# --------------------------------------------------------------------------- #
def test_runnable_just_success_maps_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    _fake_bin(tmp_path / "bin", "just", exit_code=0)
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind == "just"
    assert plan.argv == ("just", "agent::gate")
    assert rv.run_plan(plan) == rv.EXIT_OK == 0


def test_runnable_command_failure_maps_to_thirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    _fake_bin(tmp_path / "bin", "just", exit_code=1)
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.argv == ("just", "agent::gate")
    assert rv.run_plan(plan) == rv.EXIT_COMMAND_FAILED == 30


# --------------------------------------------------------------------------- #
# exit-code MAP: 21 -- a contract exists but its runner is missing
# --------------------------------------------------------------------------- #
def test_just_contract_without_just_maps_to_twentyone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    _isolate_path(monkeypatch, tmp_path / "bin")  # empty bin: no just
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind == "just"
    assert plan.exit_code == rv.EXIT_MISSING_RUNNER == 21
    assert rv.run_plan(plan) == 21


def test_package_contract_without_manager_maps_to_twentyone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "package.json", '{"scripts": {"agent:gate": "vitest run"}}')
    _isolate_path(monkeypatch, tmp_path / "bin")  # empty bin: no pnpm/npm/yarn/bun
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind == "package"
    assert plan.exit_code == 21


# --------------------------------------------------------------------------- #
# precedence + on-PATH fallback (behavioral identity with the bash wrapper)
# --------------------------------------------------------------------------- #
def test_just_wins_over_package_when_just_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    _write(tmp_path, "package.json", '{"scripts": {"agent:gate": "vitest run"}}')
    _fake_bin(tmp_path / "bin", "just", exit_code=0)
    _fake_bin(tmp_path / "bin", "npm", exit_code=0)
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind == "just"
    assert plan.argv == ("just", "agent::gate")


def test_falls_back_to_package_when_just_declared_but_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    _write(tmp_path, "agent.just", _AGENT_JUST)
    _write(tmp_path, "package.json", '{"scripts": {"agent:gate": "vitest run"}}')
    _fake_bin(tmp_path / "bin", "npm", exit_code=0)  # npm present, just absent
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.contract_kind == "package"
    assert plan.argv == ("npm", "run", "agent:gate")
    assert plan.note and "just is not on PATH" in plan.note


def test_lockfile_pins_package_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "package.json", '{"scripts": {"agent:gate": "vitest run"}}')
    _write(tmp_path, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    # Only npm is on PATH, but the lockfile pins pnpm -> runner missing -> 21.
    _fake_bin(tmp_path / "bin", "npm", exit_code=0)
    _isolate_path(monkeypatch, tmp_path / "bin")
    plan = rv.resolve(tmp_path, "gate")
    assert plan.exit_code == 21
    # With pnpm present, the pinned manager is chosen.
    _fake_bin(tmp_path / "bin", "pnpm", exit_code=0)
    plan2 = rv.resolve(tmp_path, "gate")
    assert plan2.argv == ("pnpm", "run", "agent:gate")


# --------------------------------------------------------------------------- #
# repo-root resolution + full CLI exit codes (10 / 2 / 0-doctor)
# --------------------------------------------------------------------------- #
def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def test_find_repo_root_outside_git_is_none(tmp_path: Path) -> None:
    # tmp_path is not a git work tree.
    assert rv.find_repo_root(tmp_path) is None


def test_cli_not_a_git_worktree_exits_ten(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["--repo", str(tmp_path), "repo-validate", "gate"])
    assert rc == rv.EXIT_NO_REPO == 10


def test_cli_unknown_target_exits_usage(tmp_path: Path) -> None:
    # Bypass argparse `choices` (which would exit 2 itself) by calling cli_main
    # directly, asserting the resolver's own usage guard returns 2.
    assert rv.cli_main(target="bogus", cwd=str(tmp_path)) == rv.EXIT_USAGE == 2


def test_cli_doctor_reports_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _git_init(tmp_path)
    rc = main(["--repo", str(tmp_path), "repo-validate", "doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Agent validation doctor" in out
    assert "Declared agent contracts:" in out


def test_cli_check_is_doctor_alias(tmp_path: Path) -> None:
    _git_init(tmp_path)
    assert main(["--repo", str(tmp_path), "repo-validate", "check"]) == 0


def test_cli_end_to_end_fail_closed(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _git_init(tmp_path)
    rc = main(["--repo", str(tmp_path), "repo-validate", "gate"])
    assert rc == 20
    assert "no explicit agent validation contract" in capsys.readouterr().out.lower()


def test_cli_json_output_shape(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    import json

    _git_init(tmp_path)
    rc = main(["--repo", str(tmp_path), "--json", "repo-validate", "gate"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 20
    assert payload["target"] == "gate"
    assert payload["contract_kind"] is None
    assert payload["exit_code"] == 20


def test_cli_json_is_inspection_only_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import json

    _git_init(tmp_path)
    _write(tmp_path, "Justfile", _JUST_MODULE_JUSTFILE)
    # If the runner were invoked it would exit 1; --json must NOT run it.
    _write(tmp_path, "agent.just", "gate:\n\texit 1\n")
    bin_dir = tmp_path / "bin"
    _fake_bin(bin_dir, "just", exit_code=1)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    rc = rv.cli_main(target="gate", cwd=str(tmp_path), as_json=True)
    payload = json.loads(capsys.readouterr().out)
    # A runnable contract was resolved (argv set) but not executed -> rc 0, not 30.
    assert payload["argv"] == ["just", "agent::gate"]
    assert rc == 0
