#!/usr/bin/env python3
"""Gate A in-venv probe: asserts clean-room independence of phase_loop_runtime.

Runs INSIDE the isolated venv created by gate_a_cleanroom.sh. Exits non-zero with
a diagnostic on the first violation. Checks both resolved-path independence and
that the runtime CLI commands actually run against the wheel artifact.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DOTFILES_ROOT = Path(os.environ["DOTFILES_ROOT"]).resolve()


def fail(msg: str) -> None:
    print(f"GATE-A FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def assert_not_under_dotfiles(label: str, path) -> None:
    if path is None:
        return
    resolved = Path(str(path)).resolve()
    try:
        resolved.relative_to(DOTFILES_ROOT)
    except ValueError:
        return  # good: not under dotfiles
    fail(f"{label} resolves under dotfiles checkout: {resolved}")


def main() -> None:
    # --- import independence ------------------------------------------------
    import phase_loop_runtime

    pkg_file = Path(phase_loop_runtime.__file__).resolve()
    assert_not_under_dotfiles("phase_loop_runtime.__file__", pkg_file)
    if "site-packages" not in str(pkg_file):
        fail(f"phase_loop_runtime not loaded from site-packages: {pkg_file}")

    # --- BAML resolution ----------------------------------------------------
    from phase_loop_runtime.baml_modular import _baml_src_dir

    baml_dir = _baml_src_dir()
    assert_not_under_dotfiles("_baml_src_dir()", baml_dir)
    if not (Path(baml_dir) / "emit_phase_closeout.baml").is_file():
        fail(f"baml_src missing emit_phase_closeout.baml at {baml_dir}")

    # --- skill source roots: no dotfiles walk without config ----------------
    from phase_loop_runtime import skill_inventory

    runner_root = skill_inventory._runner_repo_root()
    if runner_root is not None:
        assert_not_under_dotfiles("_runner_repo_root()", runner_root)
    # #12: a pinned install ships the assembled skill bundle inside the package, so
    # resolution must now yield a real dir UNDER site-packages (never None, never
    # dotfiles) for the core workflow skills — `run`/`dry-run` depend on it.
    rundir_for_skills = Path(os.environ["GATE_A_RUNDIR"])
    for skill in (
        "claude-phase-roadmap-builder",
        "claude-plan-phase",
        "claude-execute-phase",
        "claude-phase-loop",
    ):
        resolved_skill = skill_inventory.resolve_source_skill_dir(rundir_for_skills, "claude", skill)
        assert_not_under_dotfiles("resolve_source_skill_dir", resolved_skill)
        if resolved_skill is None:
            fail(f"resolve_source_skill_dir returned None for {skill!r} (packaged skills_bundle not shipping?)")
        if "site-packages" not in str(resolved_skill):
            fail(f"resolve_source_skill_dir({skill!r}) not under site-packages: {resolved_skill}")
        if not (Path(resolved_skill) / "SKILL.md").is_file():
            fail(f"resolved skill dir {resolved_skill} has no SKILL.md")

    # --- manifest path: explicit-root, repo-relative ------------------------
    from phase_loop_runtime import plan_manifest

    if Path(plan_manifest.MANIFEST_PATH).is_absolute():
        fail("plan_manifest.MANIFEST_PATH is an absolute hardcode")

    # --- dotfiles command presence depends on the profile-plugin config --------
    # GATE_A_EXPECT_COMMANDS = "present" (default fleet install: the dotfiles
    # profile is registered under phase_loop_runtime.profile_commands) or "absent"
    # (the seam: the group has been stripped from the installed venv).
    from phase_loop_runtime import cli

    expect = os.environ.get("GATE_A_EXPECT_COMMANDS", "present")
    parser = cli.build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if getattr(action, "choices", None):
            try:
                names.update(action.choices.keys())
            except AttributeError:
                pass
    dotfiles_commands = ("adoption-bundle", "sync-skills", "build-bundle", "hotfix")
    if expect == "absent":
        for command in dotfiles_commands:
            if command in names:
                fail(f"seam broken: dotfiles command {command!r} present with the profile_commands group stripped")
        # also assert exactly-once would be vacuous here; the seam mode is done
        # after path + version checks below.
    else:
        for command in dotfiles_commands:
            count = sum(1 for n in names if n == command)
            if command not in names:
                fail(f"fleet install missing dotfiles command {command!r} (profile plugin not registered)")
        # Guard the dedup fix (#1): each command must appear exactly once even
        # though the dotfiles profile is reachable via both the entry-point group
        # and any PHASE_LOOP_PROFILE_PLUGINS opt-in.
        choice_lists = [
            list(a.choices.keys())
            for a in parser._actions
            if getattr(a, "choices", None) and hasattr(a.choices, "keys")
        ]
        for command in dotfiles_commands:
            total = sum(cl.count(command) for cl in choice_lists)
            if total != 1:
                fail(f"dotfiles command {command!r} registered {total}x (expected exactly 1 -- dedup regression)")

    rundir = Path(os.environ["GATE_A_RUNDIR"])

    def run_cli(args, **kw):
        return subprocess.run(
            ["phase-loop", *args], text=True, capture_output=True, cwd=rundir, **kw
        )

    # --- gp bridge smoke + version -----------------------------------------
    r = run_cli(["--version"])
    if r.returncode != 0 or "phase-loop" not in r.stdout:
        fail(f"phase-loop --version failed: rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

    r = run_cli(["version"])
    if r.returncode != 0:
        fail(f"phase-loop version failed: rc={r.returncode} err={r.stderr!r}")

    # --- status / dry-run: must run (any rc) without an unhandled traceback -
    for cmd in (["status", "--json"], ["dry-run"]):
        r = run_cli(cmd)
        if "Traceback (most recent call last)" in r.stderr:
            fail(f"phase-loop {cmd} raised an unhandled exception:\n{r.stderr}")

    # --- execute --bundle: runs against the wheel + renders a closeout ------
    out_path = rundir / "gate_a_closeout.json"
    r = run_cli(
        [
            "execute",
            "GATEA",
            "--bundle",
            os.environ["GATE_A_BUNDLE"],
            "--output",
            str(out_path),
        ]
    )
    if "Traceback (most recent call last)" in r.stderr:
        fail(f"phase-loop execute --bundle raised an unhandled exception:\n{r.stderr}")
    if not out_path.is_file():
        fail(f"execute --bundle wrote no closeout at {out_path}: rc={r.returncode} err={r.stderr!r}")
    try:
        json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"execute --bundle closeout is not valid JSON: {exc}")

    # --- fleet install: the dotfiles command the pre-commit hook calls works ---
    if expect == "present":
        r = run_cli(["adoption-bundle", "status", "--repo", str(rundir), "--json"])
        # The hook's real invocation: it must NOT die with argparse "invalid choice"
        # (the fleet-breaking regression). A non-zero rc for "no bundle here" is fine;
        # an argparse usage error (rc 2 + "invalid choice") is not.
        if "invalid choice" in (r.stderr + r.stdout):
            fail(f"adoption-bundle is not a registered command in a fleet install: {r.stderr or r.stdout}")
        if "Traceback (most recent call last)" in r.stderr:
            fail(f"adoption-bundle status raised an unhandled exception:\n{r.stderr}")

    print(f"GATE-A PROBE OK ({expect})")
    print(f"  package      : {pkg_file}")
    print(f"  baml_src     : {baml_dir}")
    print(f"  runner_root  : {runner_root}")
    print(f"  closeout     : {out_path}")


if __name__ == "__main__":
    main()
