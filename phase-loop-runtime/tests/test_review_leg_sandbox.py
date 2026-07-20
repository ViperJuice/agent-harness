"""IF-0-SANDBOX-1 — per-vendor review-leg sandbox regression (SANDBOX / REVIEWGOV D3).

A review leg must not be able to write the reviewed tree. The per-vendor
mechanism, FROZEN here, differs because the read-only lever differs per CLI:

  * codex  — the product-loop ``review`` action points codex ``--cd`` at a STAGED
              COPY (like agy), the airtight barrier: codex cannot reach the live tree
              regardless of which config layer (user/system/enterprise) declares an
              out-of-sandbox MCP server (openai/codex#4152). ``read_only`` threads the
              stage placeholder + defense-in-depth flags into ``build_codex_command``
              (ah#177): ``--sandbox read-only`` (blocks shell writes), ``--ignore-user-
              config`` (drops user MCP; auth still via CODEX_HOME; hermetic), and
              ``--skip-git-repo-check`` (the staged copy has no ``.git``). Previously
              the review action ran ``danger-full-access`` against the live tree — no
              boundary at all. A cross-vendor CR blocked the flag-only first cut as an
              overclaim (read-only + ignore-user-config still leaves system/enterprise
              MCP layers), which is why the staged copy — not just flags — is the fix.
  * claude  — plan/Read-only permission; ``as-is``.
  * grok    — headless ``grok -p`` auto-approves writes, so ``--sandbox`` is
              useless; the lever is a ``--tools`` read/search allow-list.
  * gemini  — ``agy`` honors NO read-only lever at all (``--sandbox`` still writes,
              no per-tool restriction), so the ONLY sound mechanism is a STAGED
              COPY of the tree — the ``review`` action points ``--add-dir`` at a
              throwaway gitignore-aware copy, never the live worktree.

These tests pin the launcher product-loop ``review`` surface (the one that pointed
agy at the live repo) and, as a second surface, that the panel/advisor-board legs
are confined to a bundle-only review dir that never contains the repo.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from phase_loop_runtime import launcher
from phase_loop_runtime.launcher import (
    GEMINI_REVIEW_STAGE_PREFIX,
    GROK_REVIEW_READONLY_TOOLS,
    _cleanup_paths,
    _resolve_review_stage,
    _stage_review_tree,
    build_codex_command,
    build_gemini_command,
    build_grok_command,
)
from phase_loop_runtime.profiles import resolve_profile_for_executor


def _git_review_repo(tmp_path: Path) -> Path:
    """A git checkout with a modified tracked file, an untracked-non-ignored file,
    and an ignored build artifact — so a staged copy can be checked for exactly the
    working-tree state (committed + uncommitted, minus ignored)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("print('committed')\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "artifact.o").write_text("BUILD-JUNK\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    # Uncommitted working-tree state a reviewer must see:
    (repo / "tracked.py").write_text("print('dirty working tree')\n", encoding="utf-8")
    (repo / "uncommitted.py").write_text("print('new untracked')\n", encoding="utf-8")
    return repo


# --- gemini/agy: review points at a staged copy, execute at the live repo --------

def test_gemini_review_command_uses_stage_placeholder_not_live_repo():
    selection = resolve_profile_for_executor(action="review", executor="gemini")
    cmd = build_gemini_command(
        Path("/repo"), selection, action="review", context_file=launcher.GEMINI_CONTEXT_PLACEHOLDER
    )
    add_dir = cmd[cmd.index("--add-dir") + 1]
    assert add_dir == f"{GEMINI_REVIEW_STAGE_PREFIX}/repo"
    assert add_dir != "/repo", "review agy must NOT be handed the live repo path"
    # A review leg auto-approves nothing; omission is documented but the staged copy
    # is the load-bearing guarantee.
    assert "--dangerously-skip-permissions" not in cmd


def test_gemini_execute_command_uses_live_repo_unchanged():
    selection = resolve_profile_for_executor(action="execute", executor="gemini")
    cmd = build_gemini_command(
        Path("/repo"), selection, action="execute", context_file=launcher.GEMINI_CONTEXT_PLACEHOLDER
    )
    add_dir = cmd[cmd.index("--add-dir") + 1]
    assert add_dir == "/repo"
    assert not add_dir.startswith(GEMINI_REVIEW_STAGE_PREFIX)
    assert "--dangerously-skip-permissions" in cmd


def test_stage_review_tree_is_gitignore_aware_working_tree_copy(tmp_path):
    repo = _git_review_repo(tmp_path)
    staged = _stage_review_tree(repo, None)
    try:
        assert staged != repo
        assert Path(staged).name.startswith("pl-review-stage-")
        # working-tree content, including the uncommitted modification
        assert (staged / "tracked.py").read_text(encoding="utf-8") == "print('dirty working tree')\n"
        # untracked-but-not-ignored file is present
        assert (staged / "uncommitted.py").is_file()
        # ignored artifacts and VCS metadata are excluded
        assert not (staged / "ignored").exists()
        assert not (staged / ".git").exists()
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def test_review_leg_write_cannot_touch_the_reviewed_tree(tmp_path):
    """The core sandbox guarantee: a write-capable leg mutating its (staged)
    workspace leaves the live reviewed tree untouched."""
    repo = _git_review_repo(tmp_path)
    staged = _stage_review_tree(repo, None)
    try:
        # Simulate an unconstrained agy leg mutating + creating files in its workspace.
        (staged / "tracked.py").write_text("MALICIOUS OVERWRITE\n", encoding="utf-8")
        (staged / "evil.py").write_text("evil\n", encoding="utf-8")
        # The live worktree is unchanged.
        assert (repo / "tracked.py").read_text(encoding="utf-8") == "print('dirty working tree')\n"
        assert not (repo / "evil.py").exists()
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def test_resolve_gemini_review_stage_materializes_then_cleans(tmp_path):
    repo = _git_review_repo(tmp_path)
    command = ["agy", "--add-dir", f"{GEMINI_REVIEW_STAGE_PREFIX}{repo}", "-p", "review"]

    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, GEMINI_REVIEW_STAGE_PREFIX, None, dry_run=False, materialized=materialized
    )
    staged = resolved[resolved.index("--add-dir") + 1]
    assert staged != str(repo)
    assert Path(staged).name.startswith("pl-review-stage-")
    assert (Path(staged) / "tracked.py").is_file()

    # The EXACT materialized path is reported (not inferred from the argv), and cleanup
    # removes it (#177 CR-F2).
    assert materialized == [staged]
    evidence = _cleanup_paths(tuple(materialized))
    assert evidence is not None and staged in evidence["removed"]
    assert not Path(staged).exists()


def test_resolve_gemini_review_stage_dry_run_does_not_materialize(tmp_path):
    repo = _git_review_repo(tmp_path)
    command = ["agy", "--add-dir", f"{GEMINI_REVIEW_STAGE_PREFIX}{repo}", "-p", "review"]
    before = set((tmp_path).iterdir())
    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, GEMINI_REVIEW_STAGE_PREFIX, None, dry_run=True, materialized=materialized
    )
    # Dry-run resolves to the live path (nothing runs) and creates no staged copy.
    assert resolved[resolved.index("--add-dir") + 1] == str(repo)
    assert materialized == []
    assert set((tmp_path).iterdir()) == before


def test_execute_live_add_dir_is_never_cleaned_as_a_stage(tmp_path):
    # #177 CR-F2: an agy EXECUTE command carries the LIVE repo in --add-dir. Even if
    # that repo were named `pl-review-stage-*`, it must NEVER be reported for cleanup —
    # ownership is tracked by exact materialized path, not an argv basename.
    live_repo = tmp_path / "pl-review-stage-production"
    live_repo.mkdir()
    (live_repo / "keep.py").write_text("live\n", encoding="utf-8")
    command = ["agy", "--add-dir", str(live_repo), "-p", "execute"]  # no stage placeholder
    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, GEMINI_REVIEW_STAGE_PREFIX, None, dry_run=False, materialized=materialized
    )
    assert resolved == command  # unchanged; nothing to stage
    assert materialized == []   # the live repo is not owned cleanup state
    assert live_repo.exists()


# --- grok: review carries the read-only tool allow-list --------------------------

def test_grok_review_command_read_only_tools_allow_list():
    selection = resolve_profile_for_executor(action="review", executor="grok")
    cmd = build_grok_command(
        Path("/repo"), selection, action="review", context_file=launcher.GROK_CONTEXT_PLACEHOLDER
    )
    assert cmd[cmd.index("--tools") + 1] == GROK_REVIEW_READONLY_TOOLS
    assert "--dangerously-skip-permissions" not in cmd
    # None of grok's write built-ins are in the allow-list.
    for write_tool in ("write", "search_replace", "run_terminal_command"):
        assert write_tool not in GROK_REVIEW_READONLY_TOOLS


def test_grok_execute_command_is_not_read_only():
    selection = resolve_profile_for_executor(action="execute", executor="grok")
    cmd = build_grok_command(
        Path("/repo"), selection, action="execute", context_file=launcher.GROK_CONTEXT_PLACEHOLDER
    )
    # execute must NOT carry the read-only review allow-list.
    if "--tools" in cmd:
        assert cmd[cmd.index("--tools") + 1] != GROK_REVIEW_READONLY_TOOLS


# --- codex: the product-loop review action runs --sandbox read-only (ah#177) ------

def test_codex_review_command_stages_cd_not_live_repo():
    # ah#177: the airtight barrier — the review leg's `--cd` carries the repo behind the
    # stage placeholder (resolved to a throwaway copy at launch), NOT the live repo, so
    # codex cannot reach the reviewed tree regardless of config-layer MCP servers.
    selection = resolve_profile_for_executor(action="review", executor="codex")
    cmd = build_codex_command(Path("/repo"), selection, "review prompt", read_only=True)
    cd_value = cmd[cmd.index("--cd") + 1]
    assert cd_value == f"{launcher.CODEX_REVIEW_STAGE_PREFIX}/repo"
    assert cd_value != "/repo", "review codex must NOT be handed the live repo path"
    # the staged copy has no .git, so codex exec must skip the git-repo check
    assert "--skip-git-repo-check" in cmd


def test_codex_review_command_defense_in_depth_flags():
    # ah#177: staged copy is primary; read-only + ignore-user-config are defense in
    # depth (block shell writes; drop user MCP; auth still via CODEX_HOME).
    selection = resolve_profile_for_executor(action="review", executor="codex")
    cmd = build_codex_command(Path("/repo"), selection, "review prompt", read_only=True)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in cmd
    assert "danger-full-access" not in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_codex_read_only_overrides_bypass_approvals():
    selection = resolve_profile_for_executor(action="review", executor="codex")
    # even if bypass_approvals is requested, a review leg stages --cd + stays read-only
    # (never granted write/exec on the reviewed tree).
    cmd = build_codex_command(Path("/repo"), selection, "p", read_only=True, bypass_approvals=True)
    assert cmd[cmd.index("--cd") + 1].startswith(launcher.CODEX_REVIEW_STAGE_PREFIX)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_codex_execute_command_is_danger_full_access_and_live_repo():
    selection = resolve_profile_for_executor(action="execute", executor="codex")
    cmd = build_codex_command(Path("/repo"), selection, "p")
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"
    # execute runs against the LIVE repo (it must write) and needs user config.
    assert cmd[cmd.index("--cd") + 1] == "/repo"
    assert "--ignore-user-config" not in cmd
    assert "--skip-git-repo-check" not in cmd


def test_resolve_codex_review_stage_materializes_then_cleans(tmp_path):
    repo = _git_review_repo(tmp_path)
    command = ["codex", "exec", "--cd", f"{launcher.CODEX_REVIEW_STAGE_PREFIX}{repo}", "-", "prompt"]

    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, launcher.CODEX_REVIEW_STAGE_PREFIX, None, dry_run=False, materialized=materialized
    )
    staged = resolved[resolved.index("--cd") + 1]
    assert staged != str(repo)
    assert Path(staged).name.startswith("pl-review-stage-")
    # working-tree state a reviewer sees, minus ignored artifacts and .git
    assert (Path(staged) / "tracked.py").read_text(encoding="utf-8") == "print('dirty working tree')\n"
    assert (Path(staged) / "uncommitted.py").is_file()
    assert not (Path(staged) / "ignored").exists()
    assert not (Path(staged) / ".git").exists()

    # the EXACT materialized --cd copy is reported (not inferred from argv) and removed.
    assert materialized == [staged]
    evidence = _cleanup_paths(tuple(materialized))
    assert evidence is not None and staged in evidence["removed"]
    assert not Path(staged).exists()


def test_resolve_codex_review_stage_dry_run_does_not_materialize(tmp_path):
    repo = _git_review_repo(tmp_path)
    command = ["codex", "exec", "--cd", f"{launcher.CODEX_REVIEW_STAGE_PREFIX}{repo}", "-", "prompt"]
    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, launcher.CODEX_REVIEW_STAGE_PREFIX, None, dry_run=True, materialized=materialized
    )
    # dry-run resolves to the live path (nothing runs) and creates no staged copy.
    assert resolved[resolved.index("--cd") + 1] == str(repo)
    assert materialized == []


def test_codex_execute_live_cd_is_never_cleaned_as_a_stage(tmp_path):
    # #177 CR-F2: an execute codex command carries the LIVE repo in --cd. Even named
    # `pl-review-stage-*`, it must NEVER be reported as owned cleanup state.
    live_repo = tmp_path / "pl-review-stage-prod-checkout"
    live_repo.mkdir()
    (live_repo / "keep.py").write_text("live\n", encoding="utf-8")
    command = ["codex", "exec", "--cd", str(live_repo), "prompt"]  # no stage placeholder
    materialized: list[str] = []
    resolved = _resolve_review_stage(
        command, launcher.CODEX_REVIEW_STAGE_PREFIX, None, dry_run=False, materialized=materialized
    )
    assert resolved == command
    assert materialized == []
    assert live_repo.exists()


def test_resolve_command_context_cleans_stage_on_later_failure(tmp_path, monkeypatch):
    # #177 CR-F3: staging happens in _resolve_command_context, BEFORE launch_with_spec's
    # cleanup try/finally. If a later resolution step raises (e.g. output-schema
    # materialization), the already-materialized stage copy must be cleaned, not leaked.
    from dataclasses import replace as _dc_replace

    from phase_loop_runtime.launcher import (
        CODEX_OUTPUT_SCHEMA_PLACEHOLDER,
        build_launch_request,
        build_launch_spec,
    )
    from phase_loop_runtime.prompts import build_prompt

    repo = _git_review_repo(tmp_path)
    bundle = build_prompt(
        "review", repo / "specs/phase-plans-v1.md", phase="P1", plan=repo / "plans/phase-plan-v1-P1.md"
    )
    request = build_launch_request(
        executor="codex", action="review", repo=repo,
        roadmap=repo / "specs/phase-plans-v1.md", phase="P1",
        plan=repo / "plans/phase-plan-v1-P1.md",
        model_selection=resolve_profile_for_executor(action="review", executor="codex"),
        prompt_bundle=bundle, json_output=True, bypass_approvals=False,
    )
    spec = build_launch_spec(request)
    # Ensure the schema branch runs AFTER staging: inject the output-schema placeholder
    # + a schema, then make schema materialization (the post-staging step) fail.
    cmd = list(spec.command)
    cmd[-1:-1] = ["--output-schema", CODEX_OUTPUT_SCHEMA_PLACEHOLDER]
    spec = _dc_replace(spec, command=cmd, codex_output_schema={"type": "object"})
    assert any(p.startswith(launcher.CODEX_REVIEW_STAGE_PREFIX) for p in spec.command)

    captured: dict[str, str] = {}
    real_stage = launcher._stage_review_tree

    def _spy_stage(r, lp):
        staged = real_stage(r, lp)
        captured["staged"] = str(staged)
        return staged

    def _boom(*a, **k):
        raise RuntimeError("schema materialization failed after staging")

    monkeypatch.setattr(launcher, "_stage_review_tree", _spy_stage)
    monkeypatch.setattr(launcher, "_materialize_codex_schema", _boom)

    with pytest.raises(RuntimeError, match="schema materialization failed"):
        launcher._resolve_command_context(spec, None, dry_run=False)
    # the staged copy materialized before the failure was cleaned up (not leaked).
    assert "staged" in captured
    assert not Path(captured["staged"]).exists()


def test_codex_review_launch_spec_threads_read_only_from_action():
    # end-to-end: the review ACTION must produce a read-only codex command; execute must not.
    from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
    from phase_loop_runtime.prompts import build_prompt

    def _spec(action):
        bundle = build_prompt(action, Path("/repo/specs/phase-plans-v1.md"), phase="P1", plan=Path("/repo/plans/phase-plan-v1-P1.md"))
        request = build_launch_request(
            executor="codex", action=action, repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"), phase="P1",
            plan=Path("/repo/plans/phase-plan-v1-P1.md"),
            model_selection=resolve_profile_for_executor(action=action, executor="codex"),
            prompt_bundle=bundle, json_output=True, bypass_approvals=False,
        )
        return build_launch_spec(request).command

    review = _spec("review")
    assert review[review.index("--cd") + 1].startswith(launcher.CODEX_REVIEW_STAGE_PREFIX)
    assert review[review.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in review
    assert "--skip-git-repo-check" in review
    execute = _spec("execute")
    assert execute[execute.index("--cd") + 1] == "/repo"
    assert execute[execute.index("--sandbox") + 1] == "danger-full-access"
    assert "--ignore-user-config" not in execute


# --- panel/advisor-board surface: legs confined to a bundle-only review dir -------

def test_panel_leg_review_dir_never_contains_the_repo(tmp_path, monkeypatch):
    """The cross-vendor CR (invoke_panel) stages a bundle-only review dir; the repo
    is never mounted for a non-claude leg, so it cannot be written by construction."""
    from phase_loop_runtime import panel_invoker

    repo = tmp_path / "reviewed-repo"
    repo.mkdir()
    (repo / "SECRET_SOURCE.py").write_text("live tree file\n", encoding="utf-8")

    seen: dict[str, list[str]] = {}

    def _fake_exec_leg(leg, review_dir, out_dir, timeout_s, artifact, mode, model, **kwargs):
        seen["entries"] = sorted(p.name for p in Path(review_dir).iterdir())
        return 0, "ok review", "log"

    monkeypatch.setattr(panel_invoker, "_exec_leg", _fake_exec_leg)
    status, _text = panel_invoker._default_spawn("gemini", "REVIEW BUNDLE BODY", repo_dir=repo)

    assert "SECRET_SOURCE.py" not in seen["entries"], "review dir must not contain the reviewed tree"
    assert seen["entries"] == ["review-bundle.md", "review-instructions.md"]
