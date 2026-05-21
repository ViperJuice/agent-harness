from __future__ import annotations

import os
import signal
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path

from phase_loop_runtime.discovery import repo_identity
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import (
    FAKE_EXECUTORS,
    ROOT,
    PhaseLoopFixture,
    commit_fixture_paths,
    make_code_index_blocker_fixture,
    make_completed_roadmap_fixture,
    make_greenfield_closeout_fixture,
    make_message_board_fixture,
    make_repo,
    make_regenesis_amendment_fixture,
    validate_fake_executor_matrix,
    write_named_roadmap,
    write_phase_plan,
)


BIN = "codex-phase-loop"


@dataclass(frozen=True)
class LiveHarness:
    executor: str
    env_var: str
    binary: str


LIVE_HARNESSES: tuple[LiveHarness, ...] = (
    LiveHarness(executor="codex", env_var="PHASE_LOOP_ENABLE_CODEX_LIVE_TEST", binary="codex"),
    LiveHarness(executor="claude", env_var="PHASE_LOOP_ENABLE_CLAUDE_LIVE_TEST", binary="claude"),
    LiveHarness(executor="gemini", env_var="PHASE_LOOP_ENABLE_GEMINI_LIVE_TEST", binary="gemini"),
    LiveHarness(executor="opencode", env_var="PHASE_LOOP_ENABLE_OPENCODE_LIVE_TEST", binary="opencode"),
    LiveHarness(executor="pi", env_var="PHASE_LOOP_ENABLE_PI_LIVE_TEST", binary="pi"),
)
LIVE_HARNESS_BY_EXECUTOR = {harness.executor: harness for harness in LIVE_HARNESSES}


def make_two_phase_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "resume-fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "specs").mkdir()
    (repo / "plans").mkdir()
    (repo / "README.md").write_text("fixture\n")
    (repo / "specs" / "phase-plans-v1.md").write_text(
        "# Roadmap\n\n"
        "### Phase 0 - Alpha (ALPHA)\n\n"
        "### Phase 1 - Beta (BETA)\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    assert not (repo / ".phase-loop" / "state.json").exists()
    assert len((repo / "specs" / "phase-plans-v1.md").read_text().split("### Phase ")) == 3
    return repo


def write_plan(repo: Path, phase: str, tracked: bool = False) -> Path:
    path = write_phase_plan(repo, phase, repo / "specs" / "phase-plans-v1.md")
    if tracked:
        subprocess.run(["git", "add", str(path.relative_to(repo))], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", f"plan {phase.lower()}"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    return path


def append_phase_event(repo: Path, roadmap: Path, phase: str, status: str, source: str = "fixture", blocker: dict | None = None) -> None:
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase.upper(),
            action="resume",
            status=status,
            model="gpt-5.4",
            reasoning_effort="medium",
            source=source,
            blocker=blocker,
            **event_provenance(roadmap, phase),
        ),
    )


def write_phase_state(repo: Path, roadmap: Path, phases: dict[str, str]) -> None:
    write_state(repo, StateSnapshot(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phases=phases, **snapshot_provenance(roadmap)))


def isolated_home_env(home_dir: Path, base_env: dict | None = None) -> dict:
    """Build a subprocess env with HOME overridden but PYTHONPATH preserving user-site.

    Required for tests that shell out to `phase-loop` after swapping HOME — without
    this, the pip-installed phase_loop_runtime package (in user-site, HOME-derived)
    becomes invisible to the subprocess.
    """
    import site
    env = dict(base_env if base_env is not None else os.environ)
    env["HOME"] = str(home_dir)
    user_site = site.getusersitepackages()
    if user_site:
        existing = env.get("PYTHONPATH", "").split(os.pathsep) if env.get("PYTHONPATH") else []
        if user_site not in existing:
            existing.insert(0, user_site)
        env["PYTHONPATH"] = os.pathsep.join(existing)
    return env


@contextmanager
def isolated_codex_home(tmp_path: Path):
    import site
    old_home = os.environ.get("HOME")
    old_pythonpath = os.environ.get("PYTHONPATH")
    home = tmp_path / "codex-home"
    home.mkdir()

    # Preserve user-site package discovery (e.g., pip-installed phase_loop_runtime).
    # Subprocesses invoked under the swapped HOME would otherwise lose access to
    # packages installed in the real user-site (~/.local/lib/pythonX.Y/site-packages).
    user_site = site.getusersitepackages() if old_home else None
    if user_site:
        existing = old_pythonpath.split(os.pathsep) if old_pythonpath else []
        if user_site not in existing:
            existing.insert(0, user_site)
        os.environ["PYTHONPATH"] = os.pathsep.join(existing)

    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if old_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old_pythonpath


def write_skill_handoff(codex_home: Path, repo: Path, skill: str, phase: str, status: str, artifact: Path) -> Path:
    identity = repo_identity(repo)
    handoff_dir = codex_home / ".codex" / "skills" / skill / "handoffs" / identity.repo_hash / identity.branch_slug
    handoff_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"from: {skill}\n"
        f"timestamp: {utc_now()}\n"
        f"repo: {identity.repo_hash}\n"
        f"repo_root: {repo}\n"
        f"branch: {identity.branch}\n"
        f"branch_slug: {identity.branch_slug}\n"
        f"commit: {identity.commit}\n"
        "run_id: fixture\n"
        f"artifact: {artifact}\n"
        "---\n\n"
        "automation:\n"
        f"  status: {status}\n"
        "  next_skill: none\n"
        "  next_command: none\n"
        "  human_required: false\n"
        "  blocker_class: none\n"
        "  verification_status: passed\n"
        f"  artifact: {artifact}\n"
    )
    latest = handoff_dir / "latest.md"
    latest.write_text(content)
    run_specific = handoff_dir / f"{phase.lower()}-fixture.md"
    run_specific.write_text(content)
    return latest


def append_manual_import_event(
    repo: Path,
    roadmap: Path,
    phase: str,
    status: str,
    *,
    harness: str,
    skill: str,
    artifact: Path | None = None,
    installed_skill_warnings: tuple[str, ...] = (),
    bridge_skill_inventory: tuple[dict[str, object], ...] = (),
) -> None:
    metadata = {
        "manual_import": {
            "originating_harness": harness,
            "workflow_skill": skill,
            "artifact": str(artifact) if artifact is not None else None,
            "installed_skill_warnings": list(installed_skill_warnings),
            "bridge_skill_inventory": list(bridge_skill_inventory),
        }
    }
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase.upper(),
            action="resume",
            status=status,
            model="gpt-5.4",
            reasoning_effort="medium",
            source="manual",
            metadata=metadata,
            **event_provenance(roadmap, phase),
        ),
    )


def fake_manual_import_executors() -> tuple[str, ...]:
    return FAKE_EXECUTORS


def fake_live_gate_prerequisite() -> str:
    validate_fake_executor_matrix()
    return "fake harness parity regression proof"


def missing_secret_blocker() -> dict:
    blocker = {
        "human_required": True,
        "blocker_class": "missing_secret",
        "blocker_summary": "Required test credential metadata is absent.",
        "required_human_inputs": ["provide fixture credential item"],
        "access_attempts": [
            {
                "source": "op",
                "probe": "check fixture item field names",
                "result": "missing",
                "details": "vault=Fixture Secrets item=Phase Loop Smoke fields=token present=false",
                "timestamp": utc_now(),
            }
        ],
    }
    assert "secret-value" not in str(blocker)
    return blocker


def make_message_board_smoke_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_message_board_fixture(tmp_path)


def make_greenfield_closeout_smoke_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_greenfield_closeout_fixture(tmp_path)


def make_regenesis_amendment_smoke_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_regenesis_amendment_fixture(tmp_path)


def make_code_index_blocker_smoke_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_code_index_blocker_fixture(tmp_path)


def make_completed_roadmap_smoke_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_completed_roadmap_fixture(tmp_path)


def live_harness(executor: str) -> LiveHarness:
    return LIVE_HARNESS_BY_EXECUTOR[executor]


def codex_live_smoke_blocked_by_reentrancy() -> bool:
    return bool(os.environ.get("CODEX_THREAD_ID"))


def live_smoke_enabled(executor: str) -> bool:
    harness = live_harness(executor)
    if executor == "codex" and codex_live_smoke_blocked_by_reentrancy():
        return False
    return os.environ.get(harness.env_var) == "1" and shutil.which(harness.binary) is not None and BIN.exists()


def enabled_live_smoke_executors() -> tuple[str, ...]:
    return tuple(harness.executor for harness in LIVE_HARNESSES if live_smoke_enabled(harness.executor))


def opencode_live_smoke_enabled() -> bool:
    return live_smoke_enabled("opencode")


def claude_team_live_smoke_enabled() -> bool:
    return os.environ.get("PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST") == "1" and live_smoke_enabled("claude")


def make_live_roadmap_fixture(tmp_path: Path, executor: str) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="CONTRACT")


def make_live_plan_fixture(tmp_path: Path, executor: str) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("PLANONLY", f"{executor.title()} Plan Only"),))
    commit_fixture_paths(repo, f"{executor} live plan smoke fixture", roadmap)
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="PLANONLY")


def make_live_execute_fixture(tmp_path: Path, executor: str) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("DOCS", f"{executor.title()} Docs Only"),))
    body = (
        "# DOCS\n\n"
        "## Context\n\n"
        f"Docs-only execute smoke for `{executor}`.\n\n"
        "## Lanes\n\n"
        "### SL-0 - Docs\n"
        "- **Owned files**: `docs/status.md`\n"
        "- **Tasks**:\n"
        "  - impl: Write `docs/status.md` with a one-paragraph phase-loop smoke note.\n"
        "  - verify: `git status --short -- docs/status.md`\n"
    )
    plan = write_phase_plan(repo, "DOCS", roadmap, body=body, owned_files=("docs/status.md",))
    commit_fixture_paths(repo, f"{executor} live execute smoke fixture", roadmap, plan)
    write_phase_state(repo, roadmap, {"DOCS": "planned"})
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="DOCS", plan=plan)


def make_live_review_fixture(tmp_path: Path, executor: str) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("REVIEW", f"{executor.title()} Review Fixture"),))
    plan = write_phase_plan(
        repo,
        "REVIEW",
        roadmap,
        body=(
            "# REVIEW\n\n"
            "## Lanes\n\n"
            "### SL-0 - Review Fixture\n"
            "- **Owned files**: `docs/review-notes.md`\n"
        ),
        owned_files=("docs/review-notes.md",),
    )
    commit_fixture_paths(repo, f"{executor} live review smoke fixture", roadmap, plan)
    write_phase_state(repo, roadmap, {"REVIEW": "planned"})
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="REVIEW", plan=plan)


def make_live_repair_fixture(tmp_path: Path, executor: str) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("REPAIR", f"{executor.title()} Repair Fixture"),))
    plan = write_phase_plan(
        repo,
        "REPAIR",
        roadmap,
        body=(
            "# REPAIR\n\n"
            "## Lanes\n\n"
            "### SL-0 - Repair Fixture\n"
            "- **Owned files**: `docs/status.md`\n"
        ),
        owned_files=("docs/status.md",),
    )
    commit_fixture_paths(repo, f"{executor} live repair smoke fixture", roadmap, plan)
    runs_root = repo / ".phase-loop" / "runs" / "repair-fixture"
    runs_root.mkdir(parents=True, exist_ok=True)
    terminal_summary = {
        "terminal_status": "blocked",
        "verification_status": "blocked",
        "next_action": "Append a manual_repair event that clears the synthetic blocker if the repo is otherwise healthy.",
        "dirty_paths": [],
        "phase_owned_dirty": False,
        "phase_owned_dirty_paths": [],
        "unowned_dirty_paths": [],
        "pre_existing_dirty_paths": [],
    }
    (runs_root / "terminal-summary.json").write_text(json.dumps(terminal_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (runs_root / "launch.json").write_text(json.dumps({"fixture": "repair"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (runs_root / "output.log").write_text("synthetic repair blocker fixture\n", encoding="utf-8")
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="REPAIR",
            action="execute",
            status="blocked",
            model="gpt-5.4",
            reasoning_effort="medium",
            source="fixture",
            blocker={
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": "Synthetic repair fixture for live smoke proof.",
                "required_human_inputs": (),
                "access_attempts": (),
            },
            metadata={
                "artifacts": {
                    "root": str(runs_root),
                    "metadata": str(runs_root / "launch.json"),
                    "log": str(runs_root / "output.log"),
                    "terminal": str(runs_root / "terminal-summary.json"),
                },
                "terminal_summary": terminal_summary,
            },
            **event_provenance(roadmap, "REPAIR"),
        ),
    )
    write_phase_state(repo, roadmap, {"REPAIR": "blocked"})
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="REPAIR", plan=plan)


def make_mixed_harness_live_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("DOCS", "Mixed Harness Docs"),))
    commit_fixture_paths(repo, "mixed harness live smoke fixture", roadmap)
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="DOCS")


def make_live_team_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(repo, (("TEAMSAFE", "Claude Team Safe Fixture"),))
    plan = write_phase_plan(
        repo,
        "TEAMSAFE",
        roadmap,
        body=(
            "# TEAMSAFE\n\n"
            "## Lanes\n\n"
            "### SL-0 - One\n"
            "- **Owned files**: `src/one.py`\n\n"
            "### SL-1 - Two\n"
            "- **Owned files**: `src/two.py`\n"
        ),
    )
    commit_fixture_paths(repo, "claude team live smoke fixture", roadmap, plan)
    write_phase_state(repo, roadmap, {"TEAMSAFE": "planned"})
    return PhaseLoopFixture(repo=repo, roadmap=roadmap, execute_phase="TEAMSAFE", plan=plan)


def run_live_smoke(repo: Path, roadmap: Path, phase: str, executor: str) -> subprocess.CompletedProcess[str]:
    timeout = int(os.environ.get("PHASE_LOOP_LIVE_SMOKE_TIMEOUT_SECONDS", "600"))
    args = [
        str(BIN),
        "run",
        "--repo",
        str(repo),
        "--roadmap",
        str(roadmap),
        "--phase",
        phase,
        "--executor",
        executor,
        "--bypass-approvals",
    ]
    process = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        timeout_message = f"\nphase-loop live smoke timed out after {timeout}s for executor={executor} repo={repo}\n"
        return subprocess.CompletedProcess(args, 124, stdout or "", (stderr or "") + timeout_message)


def run_live_loop_action(
    repo: Path,
    roadmap: Path,
    phase: str,
    executor: str,
    product_action: str,
    *,
    claude_execution_mode: str | None = None,
):
    return run_loop(
        repo=repo,
        roadmap=roadmap,
        phase=phase,
        executor=executor,
        max_phases=1,
        action="run",
        model_profile=product_action,
        product_action_override=product_action,
        claude_execution_mode=claude_execution_mode,
        bypass_approvals=True,
    )


def make_opencode_live_plan_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_live_plan_fixture(tmp_path, "opencode")


def make_opencode_live_execute_fixture(tmp_path: Path) -> PhaseLoopFixture:
    return make_live_execute_fixture(tmp_path, "opencode")


def run_opencode_live_smoke(repo: Path, roadmap: Path, phase: str) -> subprocess.CompletedProcess[str]:
    return run_live_smoke(repo, roadmap, phase, "opencode")
