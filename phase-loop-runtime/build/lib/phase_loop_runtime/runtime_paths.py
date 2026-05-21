from __future__ import annotations

import subprocess
import re
from pathlib import Path


PHASE_LOOP_DIRNAME = ".phase-loop"
LEGACY_PHASE_LOOP_PARENT = ".codex"
LEGACY_PHASE_LOOP_DIRNAME = "phase-loop"
EXCLUDE_ENTRIES = (".phase-loop/", ".codex/phase-loop/")


def phase_loop_dir(repo: Path) -> Path:
    return repo / PHASE_LOOP_DIRNAME


def legacy_phase_loop_dir(repo: Path) -> Path:
    return repo / LEGACY_PHASE_LOOP_PARENT / LEGACY_PHASE_LOOP_DIRNAME


def phase_loop_read_dir(repo: Path) -> Path:
    canonical = phase_loop_dir(repo)
    if canonical.exists():
        return canonical
    legacy = legacy_phase_loop_dir(repo)
    return legacy if legacy.exists() else canonical


def phase_loop_read_dirs(repo: Path) -> tuple[Path, ...]:
    canonical = phase_loop_dir(repo)
    legacy = legacy_phase_loop_dir(repo)
    dirs = []
    if canonical.exists():
        dirs.append(canonical)
    if legacy.exists():
        dirs.append(legacy)
    return tuple(dirs or (canonical,))


def phase_loop_runs_dir(repo: Path) -> Path:
    return phase_loop_dir(repo) / "runs"


def lane_worktree_root(repo: Path, *, workspace_mount: Path | None = None) -> Path:
    mount = workspace_mount or Path("/mnt/workspace")
    if mount.exists():
        return mount / "worktrees"
    return repo.parent


def lane_worktree_path(
    repo: Path,
    *,
    branch: str,
    lane_id: str,
    project: str | None = None,
    workspace_mount: Path | None = None,
) -> Path:
    project_slug = _path_slug(project or repo.name)
    branch_slug = _path_slug(branch)
    lane_slug = _path_slug(lane_id)
    return lane_worktree_root(repo, workspace_mount=workspace_mount) / f"{project_slug}-{branch_slug}-{lane_slug}"


def phase_loop_runs_dirs(repo: Path) -> tuple[Path, ...]:
    dirs = tuple(path / "runs" for path in phase_loop_read_dirs(repo))
    existing = tuple(path for path in dirs if path.exists())
    return existing or (phase_loop_runs_dir(repo),)


def phase_loop_run_context_file(run_root: Path) -> Path:
    return run_root / "context.md"


def phase_loop_claude_bundle_root(run_root: Path) -> Path:
    return run_root / "claude-bundle"


def phase_loop_claude_plugin_dir(run_root: Path) -> Path:
    return phase_loop_claude_bundle_root(run_root) / "plugin"


def phase_loop_claude_settings_file(run_root: Path) -> Path:
    return phase_loop_claude_bundle_root(run_root) / "settings.json"


def phase_loop_claude_agents_file(run_root: Path) -> Path:
    return phase_loop_claude_bundle_root(run_root) / "agents.json"


def phase_loop_claude_mcp_config_file(run_root: Path) -> Path:
    return phase_loop_claude_bundle_root(run_root) / "mcp.json"


def phase_loop_stop_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "stop"


def phase_loop_stop_files(repo: Path) -> tuple[Path, ...]:
    canonical = phase_loop_stop_file(repo)
    legacy = legacy_phase_loop_dir(repo) / "stop"
    files = [canonical]
    if legacy.exists():
        files.append(legacy)
    return tuple(files)


def phase_loop_tui_handoff_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "tui-handoff.md"


def phase_loop_tui_handoff_read_file(repo: Path) -> Path:
    canonical = phase_loop_tui_handoff_file(repo)
    if canonical.exists():
        return canonical
    legacy = legacy_phase_loop_dir(repo) / "tui-handoff.md"
    return legacy if legacy.exists() else canonical


def phase_loop_state_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "state.json"


def phase_loop_executor_degradation_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "executor-degradation.json"


def phase_loop_state_read_file(repo: Path) -> Path:
    canonical = phase_loop_state_file(repo)
    if canonical.exists():
        return canonical
    legacy = legacy_phase_loop_dir(repo) / "state.json"
    return legacy if legacy.exists() else canonical


def phase_loop_event_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "events.jsonl"


def phase_loop_event_read_files(repo: Path) -> tuple[Path, ...]:
    legacy = legacy_phase_loop_dir(repo) / "events.jsonl"
    canonical = phase_loop_event_file(repo)
    if canonical.exists():
        return (canonical,)
    if legacy.exists():
        return (legacy,)
    return (canonical,)


def phase_loop_active_loop_file(repo: Path) -> Path:
    return phase_loop_dir(repo) / "active-loop.json"


def phase_loop_active_loop_read_files(repo: Path) -> tuple[Path, ...]:
    canonical = phase_loop_active_loop_file(repo)
    legacy = legacy_phase_loop_dir(repo) / "active-loop.json"
    if canonical.exists():
        return (canonical,)
    if legacy.exists():
        return (legacy,)
    return (canonical,)


def git_info_exclude(repo: Path) -> Path | None:
    try:
        path_text = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None
    if not path_text:
        return None
    path = Path(path_text)
    if not path.is_absolute():
        path = repo / path
    return path


def ensure_phase_loop_excluded(repo: Path) -> None:
    exclude = git_info_exclude(repo)
    if exclude is None:
        return
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text() if exclude.exists() else ""
        existing = {line.strip() for line in text.splitlines()}
        missing = [entry for entry in EXCLUDE_ENTRIES if entry not in existing]
        if not missing:
            return
        prefix = "" if not text or text.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\n".join(missing) + "\n")
    except OSError:
        return


def _path_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = re.sub(r"-+", "-", slug).strip("-._")
    return slug or "unknown"
