"""PUSHFLOW (POST070FIX phase 2) — push-after-merge visibility.

Three lanes, all git-plumbing/doctor level (NO runner.py):

(a) closeout pushes by DEFAULT at the CLI arg layer — the `run`/`resume`/`dry-run`
    default flips from `manual` to `push`; `--no-push` restores `manual`; an
    explicit `--closeout-mode` always wins; the push degrades gracefully with no
    push remote (the runner records `push_refused`, never an error).
(b) `commits_ahead_of_origin` mirrors `main_behind` in the worktree index + a
    `phase-loop doctor` aggregate — WARN by default, opt-in `--fail-on-ahead`
    soft-block, never human_required.
(c) `phase-loop doctor` warns when the pinned agent clone
    (`~/.local/share/agent-harness`) is behind `RELEASE_PIN`.
"""
from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime import doctor
from phase_loop_runtime import git_topology
from phase_loop_runtime import worktree_index as wi
from phase_loop_runtime.cli import build_parser, main as cli_main
from phase_loop_runtime.cli import _resolve_run_closeout_mode


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for rel, body in files.items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


# --------------------------------------------------------------------------- #
# Lane (a): closeout-push default at the CLI arg layer
# --------------------------------------------------------------------------- #
class CloseoutModeDefaultTest(unittest.TestCase):
    def _mode(self, argv: list[str]) -> str:
        args = build_parser().parse_args(argv)
        command = args.command or ("dry-run" if args.dry_run else "run")
        return _resolve_run_closeout_mode(args, command)

    def test_run_defaults_to_push(self):
        # The lived fix: a bare `phase-loop run` now pushes on closeout instead of
        # accumulating unpushed commits locally.
        self.assertEqual(self._mode(["run", "--repo", "."]), "push")
        self.assertEqual(self._mode(["resume", "--repo", "."]), "push")
        self.assertEqual(self._mode(["dry-run", "--repo", "."]), "push")

    def test_implicit_run_forms_default_to_push(self):
        # `phase-loop` with no subcommand is an implicit `run`; `phase-loop --dry-run`
        # is an implicit `dry-run`. Both must get the push default too (CR: codex).
        self.assertEqual(self._mode(["--repo", "."]), "push")
        self.assertEqual(self._mode(["--repo", ".", "--dry-run"]), "push")

    def test_no_push_suppresses_to_manual(self):
        self.assertEqual(self._mode(["run", "--repo", ".", "--no-push"]), "manual")
        self.assertEqual(self._mode(["dry-run", "--repo", ".", "--no-push"]), "manual")

    def test_explicit_closeout_mode_always_wins_after_subcommand(self):
        self.assertEqual(self._mode(["run", "--repo", ".", "--closeout-mode", "manual"]), "manual")
        self.assertEqual(self._mode(["run", "--repo", ".", "--closeout-mode", "commit"]), "commit")
        # An explicit --closeout-mode beats --no-push (the operator asked for it).
        self.assertEqual(self._mode(["run", "--repo", ".", "--closeout-mode", "push", "--no-push"]), "push")

    def test_explicit_closeout_mode_wins_before_subcommand(self):
        # CR (codex): a value in the BEFORE-subcommand position must survive the
        # subcommand parse (was clobbered to the subparser default -> push).
        self.assertEqual(self._mode(["--closeout-mode", "commit", "run", "--repo", "."]), "commit")
        self.assertEqual(self._mode(["--closeout-mode", "manual", "run", "--repo", "."]), "manual")
        # Before-subcommand explicit push still beats a trailing --no-push.
        self.assertEqual(self._mode(["--closeout-mode", "push", "run", "--repo", ".", "--no-push"]), "push")
        # Implicit run with a before-subcommand explicit mode.
        self.assertEqual(self._mode(["--repo", ".", "--closeout-mode", "manual"]), "manual")

    def test_common_args_survive_before_subcommand(self):
        # ah#84: like --closeout-mode above, EVERY common option placed BEFORE the
        # subcommand must survive the subcommand parser's copy-back. `--phase ROOM run`
        # used to yield phase=None, so the runner repaired a blocked phase instead of ROOM.
        p = build_parser()
        self.assertEqual(p.parse_args(["--phase", "ROOM", "run"]).phase, "ROOM")
        # after the subcommand still works, and both orders agree
        self.assertEqual(p.parse_args(["run", "--phase", "ROOM"]).phase, "ROOM")
        # the sibling silent-drops in the reported command are fixed too
        self.assertEqual(p.parse_args(["--max-phases", "1", "run"]).max_phases, 1)
        self.assertIs(p.parse_args(["--json", "run"]).json, True)
        self.assertIs(p.parse_args(["--dry-run", "run"]).dry_run, True)
        # omitted -> top-level default, with NO AttributeError (SUPPRESS is subparser-only)
        omitted = p.parse_args(["run"])
        self.assertIsNone(omitted.phase)
        self.assertIs(omitted.json, False)
        self.assertEqual(omitted.allow_executor, [])
        # the pattern holds on another common subcommand
        self.assertEqual(p.parse_args(["--phase", "ROOM", "resume"]).phase, "ROOM")

    def test_before_subcommand_json_survives_on_own_json_subcommands(self):
        # ah#84 (CR): doctor / run-train / train-status / advisor-board register their OWN
        # --json (outside the common-args helper); they must SUPPRESS too, or `--json <cmd>`
        # before the subcommand is silently reset to False.
        p = build_parser()
        cases = [("doctor", []), ("run-train", ["--train", "x"]),
                 ("train-status", []), ("advisor-board", ["x"])]
        for cmd, extra in cases:
            self.assertIs(p.parse_args(["--json", cmd, *extra]).json, True, cmd)
            self.assertIs(p.parse_args([cmd, *extra]).json, False, cmd)  # omitted default

    def test_dual_declared_args_survive_subparser_clobber_force_replan(self):
        # ah#233: the same subparser-copy-back clobber #232 fixed for the common-args
        # helper ALSO affects dual-declared options (top-level parser AND the
        # run/resume/dry-run subparser) that live outside `_add_common_subparser_args`.
        # Empirically: `--force-replan run` -> False and
        # `--allow-cross-phase-dirty REASON run` -> None (both silently dropped) before
        # the subparser copies gained `default=argparse.SUPPRESS`.
        p = build_parser()

        self.assertIs(p.parse_args(["--force-replan", "run"]).force_replan, True)
        self.assertIs(p.parse_args(["run", "--force-replan"]).force_replan, True)
        self.assertIs(p.parse_args(["run"]).force_replan, False)

        self.assertEqual(
            p.parse_args(["--allow-cross-phase-dirty", "reason", "run"]).allow_cross_phase_dirty,
            "reason",
        )
        self.assertEqual(
            p.parse_args(["run", "--allow-cross-phase-dirty", "reason"]).allow_cross_phase_dirty,
            "reason",
        )
        self.assertIsNone(p.parse_args(["run"]).allow_cross_phase_dirty)

        self.assertIs(p.parse_args(["--full-phase", "run"]).full_phase, True)
        self.assertIs(p.parse_args(["run"]).full_phase, False)

        self.assertIs(p.parse_args(["--no-deprecation-hints", "run"]).no_deprecation_hints, True)
        self.assertIs(p.parse_args(["run"]).no_deprecation_hints, False)

        self.assertEqual(p.parse_args(["--rotate-executors", "codex,claude", "run"]).rotate_executors, "codex,claude")
        self.assertIsNone(p.parse_args(["run"]).rotate_executors)

        self.assertEqual(p.parse_args(["--rotation-mode", "work_unit", "run"]).rotation_mode, "work_unit")
        self.assertEqual(p.parse_args(["run"]).rotation_mode, "phase")

        self.assertEqual(
            p.parse_args(["--rotation-on-policy-pin", "fallback-next", "run"]).rotation_on_policy_pin,
            "fallback-next",
        )
        self.assertEqual(p.parse_args(["run"]).rotation_on_policy_pin, "skip")

        # the pattern also holds on resume; --full-phase/--no-deprecation-hints are not
        # registered on dry-run's subparser (scoped to run/resume only, unchanged by #233).
        self.assertIs(p.parse_args(["--force-replan", "resume"]).force_replan, True)
        self.assertEqual(p.parse_args(["--rotate-executors", "codex", "dry-run"]).rotate_executors, "codex")

    def test_execute_leg_stays_manual(self):
        # The inner execute leg keeps the manual default; the flip is scoped to the
        # outer run loop and must NOT turn execute legs into pushers.
        self.assertEqual(self._mode(["execute", "FOO", "--repo", ".", "--output", "x"]), "manual")


class CloseoutPushMechanismTest(unittest.TestCase):
    """The push mechanism the default now activates (runner.py:8185 path):
    `resolve_closeout_push_target` gates the push; with a clean tracking branch it
    is allowed and a real `git push` lands; with no remote it refuses gracefully."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        _configure(self.repo)
        _git(self.repo, "checkout", "-q", "-B", "main")
        _commit(self.repo, "init", {"README.md": "hi\n"})
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_fires_by_default_when_clean_tracking_branch(self):
        # New local commit ahead of origin; the resolver allows the push and the
        # push actually lands the commit on the bare origin.
        head = _commit(self.repo, "phase work", {"README.md": "hi\nmore\n"})
        decision = git_topology.resolve_closeout_push_target(self.repo)
        self.assertTrue(decision.get("allowed"), decision)
        # This is exactly what runner.py:8185 does under closeout_mode == "push".
        subprocess.run(
            ["git", "-C", str(self.repo), "push", str(decision["remote"]), f"HEAD:{decision['push_ref']}"],
            check=True,
        )
        self.assertEqual(_git(self.origin, "rev-parse", "refs/heads/main"), head)

    def test_no_remote_degrades_to_refusal_never_raises(self):
        # A repo with no push remote/upstream must refuse GRACEFULLY (the runner
        # records push_refused) rather than raising — so push-by-default is safe
        # even when there is nowhere to push.
        root = Path(self.tmp.name) / "solo"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        _configure(root)
        _commit(root, "init", {"a.txt": "x\n"})
        decision = git_topology.resolve_closeout_push_target(root)
        self.assertFalse(decision.get("allowed"), decision)
        self.assertIn(
            decision.get("refusal_reason"),
            {"missing_push_target", "missing_push_remote", "ambiguous_upstream_ref"},
        )


# --------------------------------------------------------------------------- #
# Lane (b): commits_ahead_of_origin signal
# --------------------------------------------------------------------------- #
class CommitsAheadSignalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        _configure(self.repo)
        _git(self.repo, "checkout", "-q", "-B", "main")
        _commit(self.repo, "init", {"README.md": "hi\n"})
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main"], check=True)
        # A worktree two commits ahead of origin/main.
        self.worktree_path = root / "repo-feat"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "feat/ahead", str(self.worktree_path), "origin/main"],
            check=True,
        )
        _configure(self.worktree_path)
        _commit(self.worktree_path, "one", {"docs/a.txt": "1\n"})
        _commit(self.worktree_path, "two", {"docs/b.txt": "2\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_commits_ahead_counted_per_worktree(self):
        report = wi.build_index(self.repo)
        by_branch = {wt.branch: wt for wt in report.worktrees}
        self.assertEqual(by_branch["feat/ahead"].commits_ahead_of_origin, 2)
        # main is at origin -> zero ahead.
        self.assertEqual(by_branch["main"].commits_ahead_of_origin, 0)

    def test_render_shows_ahead_and_warn_over_threshold(self):
        report = wi.build_index(self.repo)
        text = wi.render_human(report)
        self.assertIn("[2 ahead]", text)
        # Below threshold -> no over-threshold worktrees.
        self.assertEqual(wi.worktrees_ahead_over_threshold(report), ())
        # Above a tiny threshold -> the branch is flagged.
        over = wi.worktrees_ahead_over_threshold(report, threshold=1)
        self.assertEqual({wt.branch for wt in over}, {"feat/ahead"})

    def test_fail_on_ahead_cli_opt_in(self):
        # Default (no --fail-on-ahead): exit 0 even though a branch is ahead.
        rc = cli_main(["worktree-index", "--repo", str(self.repo)])
        self.assertEqual(rc, 0)
        # Opt-in with a threshold the branch exceeds -> exit 1. Patch the module
        # threshold so the two-commit worktree trips it.
        original = wi.AHEAD_WARN_THRESHOLD
        wi.AHEAD_WARN_THRESHOLD = 1
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["worktree-index", "--repo", str(self.repo), "--fail-on-ahead"])
        finally:
            wi.AHEAD_WARN_THRESHOLD = original
        self.assertEqual(rc, 1)

    def test_doctor_divergence_aggregate(self):
        div = doctor.build_worktree_divergence(self.repo)
        self.assertEqual(div["base_ref"], "origin/main")
        self.assertGreaterEqual(div["max_commits_ahead"], 2)
        self.assertIn(div["verdict"], {"ok", "warn"})
        self.assertEqual(div["threshold"], wi.AHEAD_WARN_THRESHOLD)

    def test_doctor_divergence_degrades_on_non_repo(self):
        # A non-git path must not raise — ok/zero aggregate.
        div = doctor.build_worktree_divergence(Path(self.tmp.name) / "nope")
        self.assertEqual(div["verdict"], "ok")
        self.assertEqual(div["max_commits_ahead"], 0)


# --------------------------------------------------------------------------- #
# Lane (c): pinned-clone RELEASE_PIN staleness in doctor
# --------------------------------------------------------------------------- #
class PinnedCloneStalenessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.clone = Path(self.tmp.name) / "agent-harness"
        self.clone.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_clone_version_from_release_pin_file(self):
        (self.clone / "RELEASE_PIN").write_text("v0.6.0\n", encoding="utf-8")
        self.assertEqual(doctor._pinned_clone_version(self.clone), "0.6.0")

    def test_absent_clone_is_none(self):
        self.assertIsNone(doctor._pinned_clone_version(Path(self.tmp.name) / "missing"))

    def _clone_entry(self, repo: Path):
        bom = doctor.build_bom(repo, fetch=lambda url: None)
        return next(e for e in bom if e["ecosystem"] == "git-clone")

    def test_clone_behind_release_pin_is_stale(self):
        # repo with RELEASE_PIN v0.7.0, clone at 0.6.0 -> stale (behind).
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        (repo / "RELEASE_PIN").write_text("v0.7.0\n", encoding="utf-8")
        (self.clone / "RELEASE_PIN").write_text("v0.6.0\n", encoding="utf-8")
        import os

        os.environ["AGENT_HARNESS_HOME"] = str(self.clone)
        try:
            entry = self._clone_entry(repo)
        finally:
            del os.environ["AGENT_HARNESS_HOME"]
        self.assertEqual(entry["pinned"], "0.6.0")
        self.assertEqual(entry["latest"], "0.7.0")
        self.assertEqual(entry["verdict"], "stale")
        # Never gates — WARN only.
        self.assertFalse(entry["gating"])

    def test_clone_current_when_matching(self):
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        (repo / "RELEASE_PIN").write_text("v0.7.0\n", encoding="utf-8")
        (self.clone / "RELEASE_PIN").write_text("v0.7.0\n", encoding="utf-8")
        import os

        os.environ["AGENT_HARNESS_HOME"] = str(self.clone)
        try:
            entry = self._clone_entry(repo)
        finally:
            del os.environ["AGENT_HARNESS_HOME"]
        self.assertEqual(entry["verdict"], "current")

    def test_clone_absent_is_unknown(self):
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        (repo / "RELEASE_PIN").write_text("v0.7.0\n", encoding="utf-8")
        import os

        os.environ["AGENT_HARNESS_HOME"] = str(Path(self.tmp.name) / "missing")
        try:
            entry = self._clone_entry(repo)
        finally:
            del os.environ["AGENT_HARNESS_HOME"]
        self.assertEqual(entry["verdict"], "unknown")


if __name__ == "__main__":
    unittest.main()
