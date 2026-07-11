"""RUNCORE lane (a) / #78 — a governed dry-run must not perform closeout side effects.

Repro of agent-harness#78: ``phase-loop run ... --dry-run --closeout-mode commit``
against a phase already in ``awaiting_phase_closeout`` entered
``_perform_phase_closeout`` — launching the governed premerge panel and staging
the worktree — instead of remaining side-effect-free.

The fix guards the two ``_perform_phase_closeout`` call sites inside
``_prepare_phase_launch`` (the awaiting-closeout dispatch and the repair-recovery
re-closeout) with a ``dry_run`` preview-and-break. ``dry_run`` is NOT threaded
into ``_perform_phase_closeout`` itself; the guard lives at the call site so the
closeout body stays side-effect-free by construction. The third call site
(inside the launch reduction) is already unreachable under dry-run because the
launch path short-circuits with a ``dry_run`` terminal before it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan
from phase_loop_smoke_utils import append_phase_event, write_phase_state


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout


def _seed_awaiting_closeout(tmp_path: Path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("owned.py",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    # Phase-owned output left dirty by a prior (real) execute leg.
    (repo / "owned.py").write_text("phase output written by execute\n", encoding="utf-8")
    append_phase_event(repo, roadmap, "CONTRACT", "awaiting_phase_closeout")
    write_phase_state(repo, roadmap, {"CONTRACT": "awaiting_phase_closeout"})
    return repo, roadmap


def test_dry_run_does_not_invoke_closeout_at_awaiting_dispatch(tmp_path):
    repo, roadmap = _seed_awaiting_closeout(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    import phase_loop_runtime.runner as runner_mod

    with patch.object(
        runner_mod, "_perform_phase_closeout", wraps=runner_mod._perform_phase_closeout
    ) as spy:
        snapshot, _results = run_loop(
            repo,
            roadmap,
            phase="CONTRACT",
            closeout_mode="commit",
            dry_run=True,
        )

    # The load-bearing contract: the side-effecting closeout is never entered.
    spy.assert_not_called()
    # No commit, index clean, phase-owned output still dirty in the worktree.
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert _git(repo, "status", "--short").strip() != ""  # owned.py still dirty
    assert _git(repo, "diff", "--cached", "--name-only").strip() == ""  # nothing staged
    # Phase state was not advanced past awaiting_phase_closeout.
    assert snapshot.phases["CONTRACT"] == "awaiting_phase_closeout"
    # A dry-run preview terminal was recorded for the pending closeout.
    last = read_events(repo)[-1]
    assert last["metadata"].get("dry_run_only") is True
    assert last["metadata"]["terminal_summary"]["terminal_status"] == "dry_run"


def test_dry_run_preview_event_names_pending_closeout(tmp_path):
    repo, roadmap = _seed_awaiting_closeout(tmp_path)

    with patch("phase_loop_runtime.runner._governed_premerge_review") as panel:
        run_loop(repo, roadmap, phase="CONTRACT", closeout_mode="commit", dry_run=True)

    # The governed premerge panel must never be launched on a dry run (#78 core).
    panel.assert_not_called()
    last = read_events(repo)[-1]
    preview = last["metadata"].get("closeout_preview")
    assert preview is not None
    assert preview["pending_status"] == "awaiting_phase_closeout"
    assert preview["closeout_mode"] == "commit"
