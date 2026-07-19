import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import phase_loop_runtime.verification_evidence as ve
from phase_loop_runtime.verification_evidence import (
    _build_interpreter_shim,
    _nonsatisfying_shadow_names,
    _resolve_suite_interpreter,
    _version_satisfies_simple,
    run_verification,
)

# `>=3.9` is satisfied by every supported host (CI runs 3.10-3.12); it shadows exactly python3.8,
# so the tests need no real below-floor interpreter installed.
SATISFIED_SPEC = ">=3.9"
SHADOW = "python3.8"


def _write_pyproject(repo: Path, requires_python: str | None) -> None:
    rp = f'requires-python = "{requires_python}"\n' if requires_python else ""
    (repo / "pyproject.toml").write_text(f'[project]\nname = "t"\nversion = "0"\n{rp}', encoding="utf-8")


def _shim_names(run_path: Path, repo: Path, python_pin=None):
    si = _resolve_suite_interpreter(repo, run_path, python_pin)
    names = {p.name for p in si.shim_dir.iterdir()} if si.shim_dir else set()
    return si, names


class BuildInterpreterShimShadowTest(unittest.TestCase):
    def test_shadow_wrapper_is_executable_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            shim = _build_interpreter_shim(Path(td), Path(sys.executable), ("python3.10",), [">=3.11"])
            wrapper = shim / "python3.10"
            self.assertTrue(wrapper.exists() and os.access(wrapper, os.X_OK))
            proc = subprocess.run([str(wrapper), "-c", "print('x')"], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("does not satisfy", proc.stderr)
            # bare names resolve to the satisfying interpreter
            self.assertTrue((shim / "python").exists() and (shim / "python3").exists())

    def test_interpreter_none_only_shadows_no_bare_redirect(self):
        with tempfile.TemporaryDirectory() as td:
            shim = _build_interpreter_shim(Path(td), None, ("python3.8",), [">=3.9"])
            self.assertTrue((shim / "python3.8").exists())
            self.assertFalse((shim / "python").exists())
            self.assertFalse((shim / "python3").exists())


class ResolveSuiteInterpreterShadowTest(unittest.TestCase):
    def test_shadows_nonsatisfying_keeps_satisfying(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, SATISFIED_SPEC)
            run_path = repo / "run"
            run_path.mkdir()
            si, names = _shim_names(run_path, repo)
            self.assertIsNotNone(si.shim_dir)
            self.assertIn(SHADOW, names)  # python3.8 (below floor) shadowed
            self.assertNotIn("python3.9", names)  # satisfying, not shadowed
            self.assertNotIn("python3.12", names)

    def test_pin_branch_also_shadows(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, SATISFIED_SPEC)
            run_path = repo / "run"
            run_path.mkdir()
            si, names = _shim_names(run_path, repo, python_pin=sys.executable)
            self.assertIn(SHADOW, names)

    def test_shadow_names_helper_is_host_independent(self):
        # advisor: an upper bound makes higher minors unsupported too — test the pure helper
        # directly so the assertion is not vacuous on CI lanes where sys.executable differs.
        names = set(_nonsatisfying_shadow_names([">=3.9,<3.11"]))
        self.assertIn("python3.8", names)  # below the floor
        self.assertIn("python3.12", names)  # above the upper bound
        self.assertIn("python3.7", names)  # old minor outside the host-probe range
        self.assertIn("python2.7", names)  # python2 never satisfies a 3.x constraint
        self.assertNotIn("python3.9", names)  # within range
        self.assertNotIn("python3.10", names)  # within range
        self.assertEqual((), _nonsatisfying_shadow_names([]))  # no constraint → nothing

    def test_no_requires_python_builds_no_shim(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, None)
            run_path = repo / "run"
            run_path.mkdir()
            si = _resolve_suite_interpreter(repo, run_path, None)
            self.assertIsNone(si.shim_dir)
            self.assertIsNone(si.blocker)

    def test_patch_level_upper_bound_shadows_via_full_version(self):
        # codex: a patch-level UPPER bound must not fail open. A present python3.11 == 3.11.9 does
        # NOT satisfy `<3.11.5`, so it must be shadowed (the minor-only compare left it unshadowed).
        with mock.patch.object(ve, "_interpreter_path", side_effect=lambda n: Path("/fake/python3.11") if n == "python3.11" else None), \
             mock.patch.object(ve, "_interpreter_full_version", return_value="3.11.9"):
            names = set(_nonsatisfying_shadow_names(["<3.11.5"]))
        self.assertIn("python3.11", names)

    def test_patch_level_lower_bound_keeps_satisfying_full_version(self):
        # And it must not FALSE-block: a present python3.11 == 3.11.9 satisfies `>=3.11.5`.
        with mock.patch.object(ve, "_interpreter_path", side_effect=lambda n: Path("/fake/python3.11") if n == "python3.11" else None), \
             mock.patch.object(ve, "_interpreter_full_version", return_value="3.11.9"):
            names = set(_nonsatisfying_shadow_names([">=3.11.5"]))
        self.assertNotIn("python3.11", names)

    def test_auto_resolve_branch_shadows(self):
        # Deterministically force the auto-resolve branch: bare python/python3 are below the floor
        # and a satisfying versioned interpreter exists.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, ">=3.11")
            run_path = repo / "run"
            run_path.mkdir()
            below = Path("/fake/python3.9")
            satisfying = Path("/fake/python3.11")
            with mock.patch.object(ve, "_interpreter_path", side_effect=lambda n: below if n in ("python", "python3") else None), \
                 mock.patch.object(ve, "_interpreter_full_version", return_value="3.9.0"), \
                 mock.patch.object(ve, "_lowest_satisfying_interpreter", return_value=satisfying), \
                 mock.patch.object(ve, "_nonsatisfying_shadow_names", return_value=("python3.10",)):
                si = _resolve_suite_interpreter(repo, run_path, None)
            self.assertIsNotNone(si.shim_dir)
            names = {p.name for p in si.shim_dir.iterdir()}
            self.assertIn("python3.10", names)  # shadow present on the auto-resolve path
            self.assertTrue((si.shim_dir / "python3").is_symlink())  # bare redirected to satisfying

    def test_resolver_all_present_ok_uses_full_version(self):
        # codex round-2: the host-default (all_present_ok) decision must use the FULL version.
        # A host python3 == 3.11.9 does NOT satisfy `<3.11.5`, so it must NOT be treated as
        # satisfying (which would leave bare python unredirected and run the suite green under it).
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, "<3.11.5")
            run_path = repo / "run"
            run_path.mkdir()
            # Mock BOTH: minor-only ("3.11") would wrongly satisfy `<3.11.5`; full ("3.11.9") must not.
            with mock.patch.object(ve, "_interpreter_path", side_effect=lambda n: Path("/fake/python3") if n in ("python", "python3") else None), \
                 mock.patch.object(ve, "_interpreter_minor_version", return_value="3.11"), \
                 mock.patch.object(ve, "_interpreter_full_version", return_value="3.11.9"), \
                 mock.patch.object(ve, "_lowest_satisfying_interpreter", return_value=None):
                si = _resolve_suite_interpreter(repo, run_path, None)
            self.assertIsNotNone(si.blocker)  # 3.11.9 rejected → no satisfying interpreter → blocked

    def test_resolver_pin_uses_full_version(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, "<3.11.5")
            run_path = repo / "run"
            run_path.mkdir()
            with mock.patch.object(ve, "_interpreter_path", return_value=Path("/fake/python3.11")), \
                 mock.patch.object(ve, "_interpreter_minor_version", return_value="3.11"), \
                 mock.patch.object(ve, "_interpreter_full_version", return_value="3.11.9"):
                si = _resolve_suite_interpreter(repo, run_path, python_pin="python3.11")
            self.assertIsNotNone(si.blocker)
            self.assertIn("does not satisfy", si.blocker)

    def test_present_but_unprobeable_candidate_fails_closed(self):
        # codex round-2: a PRESENT candidate whose version cannot be established must fail CLOSED
        # (shadow), not fall back to the satisfying nominal minor.
        with mock.patch.object(ve, "_interpreter_path", side_effect=lambda n: Path("/fake/python3.11") if n == "python3.11" else None), \
             mock.patch.object(ve, "_interpreter_full_version", return_value=None):
            names = set(_nonsatisfying_shadow_names([">=3.11"]))
        self.assertIn("python3.11", names)  # nominally satisfies, but present+unprobeable → shadowed


class VersionSatisfiesSimpleFallbackTest(unittest.TestCase):
    # codex round-3: the `packaging`-unavailable fallback must not be minor-only (fail-open).
    def test_patch_level_bounds_are_not_fail_open(self):
        self.assertFalse(_version_satisfies_simple("3.11.0", [">=3.11.5"]))  # was fail-open
        self.assertFalse(_version_satisfies_simple("3.11.4", ["==3.11.5"]))  # was fail-open
        self.assertFalse(_version_satisfies_simple("3.11.4", [">=3.11.5"]))

    def test_patch_level_satisfying_cases(self):
        self.assertTrue(_version_satisfies_simple("3.11.9", [">=3.11.5"]))
        self.assertTrue(_version_satisfies_simple("3.11.5", [">=3.11.5"]))
        self.assertTrue(_version_satisfies_simple("3.11.4", ["<3.11.5"]))
        self.assertFalse(_version_satisfies_simple("3.11.9", ["<3.11.5"]))

    def test_minor_only_specs_still_work(self):
        self.assertTrue(_version_satisfies_simple("3.11.9", [">=3.9"]))
        self.assertFalse(_version_satisfies_simple("3.8.0", [">=3.9"]))
        self.assertTrue(_version_satisfies_simple("3.10.2", [">=3.9,<3.11"]))
        self.assertFalse(_version_satisfies_simple("3.11.0", [">=3.9,<3.11"]))


class VersionedInterpreterEndToEndTest(unittest.TestCase):
    def test_versioned_interpreter_fails_closed_in_commands_and_suite(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, SATISFIED_SPEC)
            run_dir = repo / ".phase-loop/runs/test"
            result = run_verification(
                repo,
                run_dir,
                [[SHADOW, "-c", "print('cmd')"]],  # commands entry names a shadowed interpreter
                [SHADOW, "-c", "print('suite')"],  # suite_command likewise
                None,
                15,
            )
            self.assertNotEqual(result.commands[0].exit_code, 0)
            self.assertIsNotNone(result.suite)
            self.assertNotEqual(result.suite.exit_code, 0)
            # Pin BOTH legs: the shim wrapper message must appear >= 2 times (once per leg), so a
            # lost suite-side PATH prepend cannot pass vacuously via command-not-found.
            log = (run_dir / "verification.log").read_text(encoding="utf-8")
            self.assertGreaterEqual(log.count("does not satisfy"), 2)

    def test_shim_wins_over_existing_below_floor_interpreter_on_path(self):
        # codex: prove the shim BEATS an already-installed below-floor interpreter, not just a
        # missing one. A fabricated python3.8 that would exit 0 is on PATH; the prepended shim
        # wrapper must intercept it and fail closed.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, SATISFIED_SPEC)  # >=3.9 -> python3.8 shadowed
            fakedir = repo / "fakebin"
            fakedir.mkdir()
            fake = fakedir / SHADOW
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")  # would pass if it ran
            fake.chmod(0o755)
            run_dir = repo / ".phase-loop/runs/test"
            with mock.patch.dict(os.environ, {"PATH": f"{fakedir}{os.pathsep}{os.environ.get('PATH', '')}"}):
                result = run_verification(repo, run_dir, [[SHADOW, "-c", "print('x')"]], None, None, 15)
            self.assertNotEqual(result.commands[0].exit_code, 0)  # shim wins, not the fake's exit 0
            self.assertIn("does not satisfy", (run_dir / "verification.log").read_text(encoding="utf-8"))

    def test_string_literal_and_env_path_are_not_false_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_pyproject(repo, SATISFIED_SPEC)
            run_dir = repo / ".phase-loop/runs/test"
            # The shadowed token appears only in a string literal / env path — a satisfying bare
            # interpreter must run green, unlike the removed regex detector.
            result = run_verification(
                repo,
                run_dir,
                [
                    ["python3", "-c", "print('mentions python3.8 only in output')"],
                    ["python3", "-c", "import os; os.environ['PYTHONPATH']='/opt/python3.8'; print('ok')"],
                ],
                None,
                None,
                15,
            )
            self.assertEqual([c.exit_code for c in result.commands], [0, 0])


if __name__ == "__main__":
    unittest.main()
