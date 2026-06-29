"""Tests for the closeout convergence fixes (issues #5 and #6), revised after panel review.

#5: build-regenerated gitignored artifacts must not be classified as un-owned spillover
    (-> dirty_worktree_conflict -> infinite repair loop). The exclusion is at the *unowned*
    classification, NOT a blanket drop from the dirty set — so an OWNED gitignored path still
    commits (no data loss) and a genuinely-unowned NON-ignored path still blocks.
#6: a phase whose verified work is already on the base branch (nothing to commit) finalizes
    as a no-op — but ONLY when terminal_status == "complete" (== verification_status passed),
    so a blocked/failed/non-verified phase is never silently finalized.
"""
from __future__ import annotations

import subprocess

from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import (
    _classify_dirty_paths,
    _closeout_nothing_staged,
    _gitignored_paths,
    _perform_phase_closeout,
)
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# --- helpers ---------------------------------------------------------------

def test_gitignored_paths_helper_matches_even_tracked(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "gen.py").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "gen.py")
    _git(repo, "commit", "-m", "track gen.py")
    (repo / ".gitignore").write_text("gen.py\n*.log\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore gen.py + logs")
    # --no-index matches the tracked gen.py AND a never-seen *.log, not src/main.py:
    assert _gitignored_paths(repo, ["gen.py", "x.log", "src/main.py"]) == {"gen.py", "x.log"}


def test_closeout_nothing_staged(tmp_path):
    repo = make_repo(tmp_path)
    assert _closeout_nothing_staged(repo) is True
    (repo / "x.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    assert _closeout_nothing_staged(repo) is False


# --- #5: classification, not a blanket drop (no data loss) -----------------

def test_classify_gitignored_unowned_excluded_owned_kept_real_spillover_blocks(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "GEN", roadmap, owned_files=("src/owned.py", "tracked_ignored.py"))
    # gitignore a generated dir AND a tracked owned file (broad-pattern edge):
    (repo / ".gitignore").write_text("src/generated/\ntracked_ignored.py\n", encoding="utf-8")
    commit_fixture_paths(repo, "plan + ignores", plan, repo / ".gitignore")

    post = ["src/owned.py", "src/generated/client.py", "tracked_ignored.py", "stray.py"]
    result = _classify_dirty_paths(repo, roadmap, plan, [], post, current_phase="GEN")

    # gitignored UNOWNED generated output: not un-owned spillover (no block), recorded:
    assert "src/generated/client.py" not in result["unowned_dirty_paths"]
    assert "src/generated/client.py" in result["gitignored_dirty_paths"]
    # gitignored but OWNED file: still phase-owned -> committed (NO DATA LOSS, the panel's #5 concern):
    assert "tracked_ignored.py" in result["phase_owned_dirty_paths"]
    assert "src/owned.py" in result["phase_owned_dirty_paths"]
    # a genuinely-unowned, NON-ignored path STILL blocks (no over-suppression):
    assert "stray.py" in result["unowned_dirty_paths"]


# --- #6: no-op finalize, gated strictly on complete ------------------------

def _snapshot(repo, roadmap, phase, terminal_status):
    return StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={phase: "awaiting_phase_closeout"},
        current_phase=phase,
        phase_owned_dirty=True,
        phase_owned_dirty_paths=("README.md",),
        dirty_paths=("README.md",),
        closeout_terminal_status=terminal_status,
        **snapshot_provenance(roadmap),
    )


def test_closeout_noop_finalizes_when_complete_and_work_already_committed(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "OOB", roadmap, owned_files=("README.md",))
    (repo / "README.md").write_text("final phase output\n", encoding="utf-8")
    # the phase's work is ALREADY committed (out-of-band) — nothing left to commit:
    commit_fixture_paths(repo, "OOB work already on base", plan, repo / "README.md")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    status, event = _perform_phase_closeout(
        repo, roadmap, "OOB", _snapshot(repo, roadmap, "OOB", "complete"),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )

    assert event.blocker is None, event.blocker
    assert status == "complete", status
    assert event.metadata["closeout"]["closeout_action"] == "noop_already_committed"
    assert event.metadata["closeout"]["verification_status"] == "passed"
    # no new commit was created (the work was already on base):
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before


def test_closeout_does_not_noop_finalize_when_not_complete(tmp_path):
    # The panel's #6 regression: a non-complete (here: not-yet-verified) terminal status with
    # an empty index must NOT be fabricated into a completed/passed phase.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "OOB", roadmap, owned_files=("README.md",))
    (repo / "README.md").write_text("final phase output\n", encoding="utf-8")
    commit_fixture_paths(repo, "work already on base", plan, repo / "README.md")

    status, event = _perform_phase_closeout(
        repo, roadmap, "OOB", _snapshot(repo, roadmap, "OOB", "executed"),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )

    # must NOT take the no-op-complete shortcut for a non-"complete" terminal status:
    assert event.metadata["closeout"].get("closeout_action") != "noop_already_committed"
    assert status != "complete"


def test_closeout_commits_owned_tracked_then_ignored_change(tmp_path):
    # Panel round-2 (codex): prove end-to-end — not just at the classifier — that an OWNED
    # change to a tracked-then-gitignored file is actually COMMITTED by closeout (no data loss).
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "GEN", roadmap, owned_files=("gen.py",))
    # track gen.py FIRST, THEN gitignore it (the realistic tracked-then-ignored order):
    (repo / "gen.py").write_text("v1\n", encoding="utf-8")
    commit_fixture_paths(repo, "track gen.py + plan", plan, repo / "gen.py")
    (repo / ".gitignore").write_text("gen.py\n", encoding="utf-8")
    commit_fixture_paths(repo, "now gitignore gen.py", repo / ".gitignore")
    (repo / "gen.py").write_text("v2 regenerated by the phase\n", encoding="utf-8")  # owned change
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"GEN": "awaiting_phase_closeout"}, current_phase="GEN",
        phase_owned_dirty=True, phase_owned_dirty_paths=("gen.py",),
        dirty_paths=("gen.py",), closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "GEN", snapshot,
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )
    assert event.blocker is None, event.blocker
    assert status == "complete", status
    # the OWNED tracked-ignored change was committed — no data loss:
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before
    assert "v2 regenerated by the phase" in _git(repo, "show", "HEAD:gen.py").stdout


def _governed_closeout_repo(tmp_path):
    """A committable owned change, ready for `_perform_phase_closeout`."""
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "GEN", roadmap, owned_files=("src/owned.py",))
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "owned.py").write_text("v1\n", encoding="utf-8")
    commit_fixture_paths(repo, "track owned.py + plan", plan, repo / "src" / "owned.py")
    (repo / "src" / "owned.py").write_text("GOVERNED_CHANGE = 1\n", encoding="utf-8")
    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"GEN": "awaiting_phase_closeout"}, current_phase="GEN",
        phase_owned_dirty=True, phase_owned_dirty_paths=("src/owned.py",),
        dirty_paths=("src/owned.py",), closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )
    return repo, roadmap, snapshot


def test_governed_premerge_block_holds_the_commit(tmp_path):
    # Advisor-panel relocation: the gate runs INSIDE _perform_phase_closeout, after
    # `git add` (so it reviews the staged index) and before the commit. A panel block
    # must hold the commit — status blocked, HEAD unchanged, the change NOT committed.
    from unittest.mock import patch
    from phase_loop_runtime import runner
    from phase_loop_runtime.governed_premerge import LoopResult

    repo, roadmap, snapshot = _governed_closeout_repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    blocked = LoopResult(
        mergeable=False, ran=True, rounds=3,
        terminal_blocker={"human_required": False, "blocker_class": "review_gate_block"},
        reason="non_convergence",
    )
    with patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
         patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
        status, event = _perform_phase_closeout(
            repo, roadmap, "GEN", snapshot,
            resolve_profile("execute"), action="execute", closeout_mode="commit",
            run_mode="governed",
        )
    assert status == "blocked", status
    assert event.blocker["blocker_class"] == "review_gate_block"
    assert event.blocker["human_required"] is False
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before  # NOT committed


def test_governed_premerge_pass_commits(tmp_path):
    # The same flow, but the panel passes → the phase commits normally (the gate is
    # a clean pass-through). Autonomous parity: no governed metadata leaks a block.
    from unittest.mock import patch
    from phase_loop_runtime import runner
    from phase_loop_runtime.governed_premerge import LoopResult

    repo, roadmap, snapshot = _governed_closeout_repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    with patch.object(runner, "governed_premerge_for_run",
                      return_value=LoopResult(mergeable=True, ran=True, rounds=1)), \
         patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
        status, event = _perform_phase_closeout(
            repo, roadmap, "GEN", snapshot,
            resolve_profile("execute"), action="execute", closeout_mode="commit",
            run_mode="governed",
        )
    assert status == "complete", status
    assert event.blocker is None
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before
    assert "GOVERNED_CHANGE" in _git(repo, "show", "HEAD:src/owned.py").stdout


def test_governed_autonomous_default_commits_byte_identical(tmp_path):
    # The autonomous default never invokes the gate — a run_mode-default closeout
    # commits exactly as before (the relocated gate is a no-op off the governed path).
    from unittest.mock import patch
    from phase_loop_runtime import runner

    repo, roadmap, snapshot = _governed_closeout_repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    with patch.object(runner, "governed_premerge_for_run") as gov:
        status, event = _perform_phase_closeout(
            repo, roadmap, "GEN", snapshot,
            resolve_profile("execute"), action="execute", closeout_mode="commit",
        )  # run_mode defaults to "autonomous"
    gov.assert_not_called()                       # zero panel cost on the default path
    assert status == "complete", status
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != head_before


def test_governed_block_unstages_rejected_changes(tmp_path):
    # CR fix #3: `git add` stages the owned paths BEFORE the gate; on a governed
    # block the index must be reset so a later out-of-loop/manual `git commit`
    # (no pathspec) cannot land the panel-rejected change. Worktree is untouched.
    from unittest.mock import patch
    from phase_loop_runtime import runner
    from phase_loop_runtime.governed_premerge import LoopResult

    repo, roadmap, snapshot = _governed_closeout_repo(tmp_path)
    blocked = LoopResult(
        mergeable=False, ran=True, rounds=3,
        terminal_blocker={"human_required": False, "blocker_class": "review_gate_block"},
        reason="non_convergence",
    )
    with patch.object(runner, "governed_premerge_for_run", return_value=blocked), \
         patch.object(runner, "available_panel_legs", return_value=("codex", "gemini")):
        status, _ = _perform_phase_closeout(
            repo, roadmap, "GEN", snapshot,
            resolve_profile("execute"), action="execute", closeout_mode="commit",
            run_mode="governed",
        )
    assert status == "blocked", status
    # Nothing staged (index == HEAD): a stray `git commit` would commit nothing.
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""
    # The worktree change is preserved (not discarded), still uncommitted.
    assert "GOVERNED_CHANGE" in (repo / "src" / "owned.py").read_text()
