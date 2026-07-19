import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.verification_evidence import (
    _build_interpreter_shim,
    _nonsatisfying_shadow_names,
    _resolve_suite_interpreter,
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
