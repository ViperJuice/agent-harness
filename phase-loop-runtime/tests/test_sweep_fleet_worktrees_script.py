"""Safety self-test for the ``sweep_fleet_worktrees.sh`` fleet-detection backstop.

The fleet sweep scans EVERY dir under the shared worktrees base across MANY
owning repos and can ``sudo rm -rf`` a permission-locked dir. Two safety surfaces
are genuinely broader than the single-repo ``prune_merged_worktrees.sh`` and are
pinned here:

  * it must NEVER select a PRIMARY checkout — for ANY owning repo, resolved per
    candidate (not just the one repo the sweep happens to run in); and
  * it must NEVER remove a path OUTSIDE the approved base ``PHASE_LOOP_WORKTREES_BASE``.

The predicates are exercised directly (a ``--dry-run`` transcript never reaches
the removal path, so it cannot prove confinement), mirroring the prune test.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]          # phase-loop-runtime/
_SCRIPT = _REPO_ROOT / "scripts" / "sweep_fleet_worktrees.sh"

# The helper is a REPO-SOURCE script under phase-loop-runtime/scripts/; it is not
# packaged into the wheel (not in [tool.setuptools.package-data]), so in the
# standalone-from-wheel clean-room gate _SCRIPT is absent. Skip there — safety-
# testing a repo script against an installed wheel is moot.
pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="repo-only helper (scripts/ not in the wheel); irrelevant standalone-from-wheel",
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True,
        capture_output=True, text=True,
    ).stdout


def _source_and_eval(snippet: str) -> subprocess.CompletedProcess:
    """Source the helper as a library (no sweep) and run a bash snippet against
    its extracted predicates."""
    body = f'SWEEP_FLEET_WORKTREES_LIB=1 source "{_SCRIPT}"\n' + snippet
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True,
    )


def test_script_exists_and_is_executable():
    assert _SCRIPT.is_file(), _SCRIPT
    assert os.access(_SCRIPT, os.X_OK), "helper must be executable"


def test_no_blanket_sudo_rm_only_confined_permission_path():
    text = _SCRIPT.read_text(encoding="utf-8")
    # Exactly ONE executable sudo line (ignore comments/warnings that mention sudo).
    sudo_cmds = [
        ln for ln in text.splitlines()
        if "sudo " in ln and not ln.lstrip().startswith("#") and "echo " not in ln
    ]
    assert len(sudo_cmds) == 1, f"exactly one sudo command expected, got {sudo_cmds}"
    assert 'sudo -n rm -rf -- "$path"' in text, "sudo rm must be -n and -- guarded"
    # It must be reachable only after both the permission-error and base checks.
    assert "grep -qi 'permission denied'" in text
    assert "path_under_base" in text


def test_path_under_base_predicate():
    # strictly-under → true; equal-to-base → false; sibling-prefix → false; empty → false.
    res = _source_and_eval(
        'path_under_base /mnt/wt/base/a /mnt/wt/base && echo under-yes || echo under-no\n'
        'path_under_base /mnt/wt/base    /mnt/wt/base && echo eq-yes    || echo eq-no\n'
        'path_under_base /mnt/wt/base-evil /mnt/wt/base && echo evil-yes || echo evil-no\n'
        'path_under_base "" /mnt/wt/base && echo empty-yes || echo empty-no\n'
        'path_under_base /mnt/wt/base/a "" && echo nobase-yes || echo nobase-no\n'
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.split()
    assert out == ["under-yes", "eq-no", "evil-no", "empty-no", "nobase-no"], out


def test_remove_dir_base_confined_before_any_removal():
    """remove_dir must gate on path_under_base BEFORE any git-remove / rm / sudo,
    so a path outside the approved base is refused with nothing touched. The
    predicate itself is proven in test_path_under_base_predicate; here we pin that
    remove_dir routes through it up front (structural, since remove_dir lives below
    the LIB guard and is not sourceable in isolation — matching the prune helper)."""
    text = _SCRIPT.read_text(encoding="utf-8")
    body = text.split("remove_dir()", 1)[1]
    # The base check and its refusal must appear before the first rm/sudo in the fn.
    base_idx = body.find("path_under_base")
    refuse_idx = body.find("OUTSIDE approved base")
    rm_idx = min(
        i for i in (body.find("rm -rf"), body.find("sudo ")) if i != -1
    )
    assert base_idx != -1 and refuse_idx != -1, "base guard missing from remove_dir"
    assert base_idx < rm_idx, "path_under_base must gate before any removal"
    assert refuse_idx < rm_idx, "outside-base refusal must precede any removal"


@pytest.fixture()
def fleet_layout(tmp_path: Path) -> dict[str, Path]:
    """A base dir containing: repoA's own primary checkout, repoA's merged+clean
    linked worktree, and repoB's primary checkout — all as immediate children of
    the base (the fleet sweep scans base children directly)."""
    base = tmp_path / "worktrees"
    base.mkdir()

    def _make_repo(primary: Path) -> None:
        primary.mkdir(parents=True)
        _git(primary, "init", "-q", "-b", "main")
        (primary / "f").write_text("x")
        _git(primary, "add", ".")
        _git(primary, "commit", "-qm", "init")
        _git(primary, "update-ref", "refs/remotes/origin/main", "HEAD")

    # repoA primary lives UNDER the base (worst case: a primary checkout sitting in
    # the sweep dir). Its merged linked worktree is a sibling under the base.
    repo_a = base / "repoA-primary"
    _make_repo(repo_a)
    linked_a = base / "repoA-merged"
    _git(repo_a, "worktree", "add", "-q", "-b", "feat/merged", str(linked_a), "main")

    # repoB primary, also under the base.
    repo_b = base / "repoB-primary"
    _make_repo(repo_b)

    # A plain non-git directory under the base (stray output, not a worktree).
    non_git = base / "stray-not-a-worktree"
    non_git.mkdir()
    (non_git / "junk").write_text("noise")

    # A genuine ORPHAN: a linked worktree of repoB whose owning admin entry is
    # removed, so its `.git` gitdir pointer dangles (owner-gone, unrecoverable).
    orphan = base / "repoB-orphaned"
    _git(repo_b, "worktree", "add", "-q", "-b", "feat/orphan", str(orphan), "main")
    # Delete the owning admin dir the orphan's `.git` file points at.
    gitdir_line = (orphan / ".git").read_text().strip()
    admin = gitdir_line.split("gitdir:", 1)[1].strip()
    admin_path = Path(admin)
    if not admin_path.is_absolute():
        admin_path = (orphan / admin_path).resolve()
    import shutil
    shutil.rmtree(admin_path)

    return {
        "base": base,
        "repo_a": repo_a,
        "linked_a": linked_a,
        "repo_b": repo_b,
        "non_git": non_git,
        "orphan": orphan,
    }


def test_owning_primary_resolves_each_repos_main_tree(fleet_layout):
    """owning_primary() resolves the main checkout for whichever repo owns the
    given dir — the per-candidate primary the fleet sweep must exclude."""
    res = _source_and_eval(
        f'echo "A=$(owning_primary {fleet_layout["linked_a"]})"\n'
        f'echo "B=$(owning_primary {fleet_layout["repo_b"]})"\n'
    )
    assert res.returncode == 0, res.stderr
    lines = dict(l.split("=", 1) for l in res.stdout.splitlines() if "=" in l)
    assert Path(lines["A"]).resolve() == fleet_layout["repo_a"].resolve()
    assert Path(lines["B"]).resolve() == fleet_layout["repo_b"].resolve()


def test_is_orphan_worktree_predicate(fleet_layout):
    """is_orphan_worktree: true for a gitdir-pointer whose target is gone; false for
    a live linked worktree, a primary checkout, and a plain non-git dir."""
    res = _source_and_eval(
        f'is_orphan_worktree {fleet_layout["orphan"]}   && echo orphan-yes  || echo orphan-no\n'
        f'is_orphan_worktree {fleet_layout["linked_a"]} && echo live-yes    || echo live-no\n'
        f'is_orphan_worktree {fleet_layout["repo_a"]}   && echo primary-yes || echo primary-no\n'
        f'is_orphan_worktree {fleet_layout["non_git"]}  && echo nongit-yes  || echo nongit-no\n'
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.split()
    assert out == ["orphan-yes", "live-no", "primary-no", "nongit-no"], out


def test_dry_run_never_selects_any_primary_checkout(fleet_layout):
    """End-to-end --dry-run over the whole base: NO primary checkout (repoA's or
    repoB's) may appear in a PRUNE line; only the merged+clean linked worktree may."""
    res = subprocess.run(
        ["bash", str(_SCRIPT), "--dry-run"],
        cwd=fleet_layout["repo_a"], capture_output=True, text=True,
        env={**os.environ, "PHASE_LOOP_WORKTREES_BASE": str(fleet_layout["base"])},
    )
    assert res.returncode == 0, res.stderr
    combined = res.stdout + res.stderr
    prune_lines = [l for l in combined.splitlines() if l.startswith("PRUNE:")]
    keep_lines = [l for l in combined.splitlines() if l.startswith("KEEP:")]
    for line in prune_lines:
        assert str(fleet_layout["repo_a"]) + " " not in line + " ", line
        assert str(fleet_layout["repo_b"]) + " " not in line + " ", f"primary selected: {line}"
    # The merged+clean linked worktree IS a legitimate prune candidate.
    assert any(str(fleet_layout["linked_a"]) in l for l in prune_lines), (
        f"expected merged linked worktree in PRUNE set; got: {prune_lines}"
    )
    # The orphan (owner-gone) IS a legitimate prune candidate.
    assert any(
        str(fleet_layout["orphan"]) in l and "ORPHANED" in l for l in prune_lines
    ), f"expected orphan in PRUNE set; got: {prune_lines}"
    # The plain non-git dir must be KEPT, never pruned (the sweep must not abort on
    # it either — reaching this assertion at all proves the set-e survival).
    assert any(str(fleet_layout["non_git"]) in l for l in keep_lines), (
        f"non-git dir must be KEPT; keep lines: {keep_lines}"
    )
    for line in prune_lines:
        assert str(fleet_layout["non_git"]) not in line, f"non-git dir pruned: {line}"


def test_alert_threshold_exits_nonzero_when_met(fleet_layout):
    """Two prunable dirs (merged linked worktree + orphan): --alert-threshold 2
    exits 2; a threshold of 5 exits 0."""
    env = {**os.environ, "PHASE_LOOP_WORKTREES_BASE": str(fleet_layout["base"])}
    met = subprocess.run(
        ["bash", str(_SCRIPT), "--dry-run", "--alert-threshold", "2"],
        cwd=fleet_layout["repo_a"], capture_output=True, text=True, env=env,
    )
    assert met.returncode == 2, (met.stdout, met.stderr)
    assert "ALERT" in met.stderr

    under = subprocess.run(
        ["bash", str(_SCRIPT), "--dry-run", "--alert-threshold", "5"],
        cwd=fleet_layout["repo_a"], capture_output=True, text=True, env=env,
    )
    assert under.returncode == 0, (under.stdout, under.stderr)


def test_prune_removes_safe_dirs_and_never_primaries(fleet_layout):
    """End-to-end --prune: the merged linked worktree and the orphan are removed;
    every primary checkout and the non-git dir survive untouched. Confinement holds
    for the orphan (the one path that can destroy unrecoverable working-tree files):
    everything removed was strictly under the base."""
    env = {**os.environ, "PHASE_LOOP_WORKTREES_BASE": str(fleet_layout["base"])}
    res = subprocess.run(
        ["bash", str(_SCRIPT), "--prune"],
        cwd=fleet_layout["repo_a"], capture_output=True, text=True, env=env,
    )
    assert res.returncode == 0, (res.stdout, res.stderr)
    # Removed: merged linked worktree + orphan.
    assert not fleet_layout["linked_a"].exists(), "merged worktree not removed"
    assert not fleet_layout["orphan"].exists(), "orphan not removed"
    # Survivors: both primaries + the non-git dir.
    assert fleet_layout["repo_a"].exists(), "repoA primary removed!"
    assert fleet_layout["repo_b"].exists(), "repoB primary removed!"
    assert fleet_layout["non_git"].exists(), "non-git dir removed!"
    # Nothing outside the base was touched — the base's parent still holds it.
    assert fleet_layout["base"].exists()
