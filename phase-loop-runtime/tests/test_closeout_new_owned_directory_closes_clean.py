"""Regression test for agent-harness#218 (dir-collapse ownership scope violation).

When a phase's owned deliverable is a brand-new directory whose contract is a
*file-level* glob (``pkg/newmod/*.py``) and the executor self-reports the
*collapsed* bare directory (``pkg/newmod/``) instead of its member files, the
ownership matcher (file-level ``fnmatchcase``) never matches the bare-directory
string. The path routes to the unowned remainder and trips a spurious
``closeout_scope_violation`` — even though every file inside the directory is
plainly owned.

The fix normalizes a collapsed bare-directory entry to its member files (via
``git_ops.expand_dir_dirty_paths``) before ownership matching, so the phase
closes ``complete`` with the directory committed — no ``--closeout-allow-unowned``
and no scope violation.

"Can it fail?" bar: with the fix reverted, the collapsed ``pkg/newmod/`` entry
fails ``matches_dirty_output`` (``pkg/newmod/*.py`` requires a ``.py`` suffix a
bare dir lacks) → the closeout blocks with a scope/ownership refusal instead of
committing clean. Confirmed against the pre-fix runner.
"""

from __future__ import annotations

import subprocess

from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import _perform_phase_closeout
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_closeout_new_owned_directory_closes_clean(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    # File-level owned glob (NOT ``pkg/newmod/**`` — that would already match a
    # bare-dir string under fnmatch and make this test vacuous).
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("pkg/newmod/*.py",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)

    # A brand-new, entirely untracked directory of owned files on disk.
    newmod = repo / "pkg" / "newmod"
    newmod.mkdir(parents=True)
    (newmod / "__init__.py").write_text("\n", encoding="utf-8")
    (newmod / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # The executor self-reports the COLLAPSED bare directory rather than its
    # member files (the #218 shape).
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("pkg/newmod/",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo,
        roadmap,
        "CONTRACT",
        snapshot,
        resolve_profile("execute"),
        action="execute",
        closeout_mode="commit",
    )

    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete", f"expected complete, got {status}"
    # Specifically: no scope violation / unowned-remainder refusal.
    refusal = event.metadata["closeout"].get("closeout_refusal_reason")
    assert refusal != "unowned_dirty_remainder", f"unexpected scope refusal: {refusal!r}"
    assert event.metadata["closeout"].get("unowned_dirty_paths") in (None, [], ())

    # The whole directory was committed.
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "expected a new commit"
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "pkg/newmod/__init__.py" in committed
    assert "pkg/newmod/core.py" in committed
    # Nothing left dirty.
    assert _git(repo, "status", "--short").stdout.strip() == ""
