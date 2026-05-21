from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import DelegationBudget, DelegationRequest
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance


@dataclass(frozen=True)
class PhaseLoopFixture:
    repo: Path
    roadmap: Path
    execute_phase: str
    next_phase: str | None = None
    stale_phase: str | None = None
    plan: Path | None = None
    stale_plan: Path | None = None


FAKE_EXECUTORS = ("codex", "claude", "gemini", "opencode", "command")
FAKE_SCENARIOS = (
    "plan",
    "execute",
    "repair",
    "review",
    "blocked",
    "timeout",
    "malformed_output",
    "zero_byte_output",
    "verified_dirty_closeout",
    "orphan_cleanup",
)

DFFAKESMOKE_REQUIRED_SCENARIOS = (
    "two_lane_success",
    "three_lane_success",
    "overlap_rejection",
    "missing_worktree_assignment",
    "stale_base_assignment",
    "human_required_blocker",
    "malformed_closeout",
    "missing_closeout",
    "timeout",
    "redaction_rejection",
    "pi_lane_default",
    "legacy_codex_default",
    "substrate_receipt",
)


@dataclass(frozen=True)
class FakeExecutorScenario:
    name: str
    automation: str | None = None
    returncode: int = 0
    timed_out: bool = False
    interrupted: bool = False
    cleanup_evidence: dict[str, Any] | None = None
    zero_byte_log: bool = False
    writes_phase_owned_output: bool = False


def build_fake_automation_output(
    *,
    status: str,
    next_skill: str = "none",
    next_command: str = "none",
    human_required: bool = False,
    blocker_class: str = "none",
    blocker_summary: str = "none",
    verification_status: str = "passed",
    required_human_inputs: tuple[str, ...] = (),
    artifact: str = "none",
    artifact_state: str = "none",
) -> str:
    if required_human_inputs:
        inputs = "\n".join(f"  - {item}" for item in required_human_inputs)
        required = f"required_human_inputs:\n{inputs}\n"
    else:
        required = "required_human_inputs: []\n"
    return (
        "automation:\n"
        f"  status: {status}\n"
        f"  next_skill: {next_skill}\n"
        f"  next_command: {next_command}\n"
        "  next_model_hint: none\n"
        "  next_effort_hint: none\n"
        f"  human_required: {'true' if human_required else 'false'}\n"
        f"  blocker_class: {blocker_class}\n"
        f"  blocker_summary: {blocker_summary}\n"
        f"  {required}"
        f"  verification_status: {verification_status}\n"
        f"  artifact: {artifact}\n"
        f"  artifact_state: {artifact_state}\n"
    )


def fake_executor_scenarios(*, artifact: str = "none") -> dict[str, FakeExecutorScenario]:
    return {
        "plan": FakeExecutorScenario(
            name="plan",
            automation=build_fake_automation_output(
                status="planned",
                next_skill="codex-execute-phase",
                next_command="codex-execute-phase plans/phase-plan-v1-RUNNER.md",
                verification_status="not_run",
                artifact=artifact,
                artifact_state="staged",
            ),
        ),
        "execute": FakeExecutorScenario(
            name="execute",
            automation=build_fake_automation_output(status="executed", verification_status="passed", artifact=artifact),
        ),
        "repair": FakeExecutorScenario(
            name="repair",
            automation=build_fake_automation_output(status="executed", verification_status="passed", artifact=artifact),
        ),
        "review": FakeExecutorScenario(
            name="review",
            automation=build_fake_automation_output(status="complete", verification_status="passed", artifact=artifact),
        ),
        "blocked": FakeExecutorScenario(
            name="blocked",
            automation=build_fake_automation_output(
                status="blocked",
                human_required=False,
                blocker_class="repeated_verification_failure",
                blocker_summary="Fake harness blocker for deterministic regression coverage.",
                verification_status="blocked",
                artifact=artifact,
            ),
        ),
        "timeout": FakeExecutorScenario(name="timeout", timed_out=True),
        "malformed_output": FakeExecutorScenario(
            name="malformed_output",
            automation='{"result":"automation:\\n  status: executed\\n  next_skill: none\\n"}',
        ),
        "zero_byte_output": FakeExecutorScenario(name="zero_byte_output", automation="", zero_byte_log=True),
        "verified_dirty_closeout": FakeExecutorScenario(
            name="verified_dirty_closeout",
            automation=build_fake_automation_output(status="complete", verification_status="passed", artifact=artifact),
            writes_phase_owned_output=True,
        ),
        "orphan_cleanup": FakeExecutorScenario(
            name="orphan_cleanup",
            cleanup_evidence={"process_alive_after_cleanup": True},
        ),
    }


def validate_fake_executor_matrix() -> None:
    scenarios = fake_executor_scenarios()
    for executor in FAKE_EXECUTORS:
        for scenario_name in FAKE_SCENARIOS:
            scenario = scenarios[scenario_name]
            if scenario.automation is not None and scenario_name not in {"malformed_output", "zero_byte_output"}:
                parsed = scenario.automation
                for field in (
                    "status:",
                    "next_skill:",
                    "next_command:",
                    "human_required:",
                    "blocker_class:",
                    "blocker_summary:",
                    "required_human_inputs:",
                    "verification_status:",
                    "artifact:",
                    "artifact_state:",
                ):
                    assert field in parsed, f"{executor}:{scenario_name} missing {field}"


def validate_dffakesmoke_fake_smoke_matrix() -> None:
    matrix_path = FIXTURES / "phase_loop_fake_smoke" / "matrix.json"
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    scenarios = {item["name"]: item for item in payload["scenarios"]}
    missing = set(DFFAKESMOKE_REQUIRED_SCENARIOS) - set(scenarios)
    assert not missing, f"DFFAKESMOKE fake-smoke matrix missing scenarios: {sorted(missing)}"
    for name in DFFAKESMOKE_REQUIRED_SCENARIOS:
        scenario = scenarios[name]
        assert scenario.get("proof"), f"{name} missing proof target"
        assert scenario.get("expected"), f"{name} missing expected result"
        forbidden = " ".join(str(value) for value in scenario.values()).lower()
        assert "should-not-survive" not in forbidden, f"{name} leaks raw redaction fixture value"
    receipt = scenarios["substrate_receipt"]
    for field in (
        "phase",
        "roadmap_sha256",
        "plan_path",
        "fake_fixture_matrix",
        "smoke_commands",
        "work_unit_evidence_refs",
        "verification_status",
        "changed_path_boundaries",
        "redaction_posture",
    ):
        assert field in receipt["expected"], f"substrate_receipt missing {field}"


def build_fake_delegation_request(
    *,
    request_id: str,
    target_executor: str,
    product_action: str = "review",
    owned_files: tuple[str, ...] = ("notes.md",),
    expected_output: str = "Review findings",
    priority: str = "high",
) -> DelegationRequest:
    return DelegationRequest(
        request_id=request_id,
        product_action=product_action,
        target_executor=target_executor,
        reason=f"Fake harness {product_action} request for {target_executor}.",
        owned_files=owned_files,
        expected_output=expected_output,
        priority=priority,
        budget=DelegationBudget(max_seconds=60, notes="metadata only"),
    )


def assert_metadata_only_evidence_refs(testcase: Any, refs: tuple[Any, ...] | list[Any]) -> None:
    allowed_prefixes = ("phase-loop-run:", "log:redacted:sha256:", "receipt:sha256:", "metrics:sha256:")
    testcase.assertTrue(refs, "evidence refs must not be empty")
    for ref in refs:
        if isinstance(ref, dict):
            path = str(ref.get("path", ""))
            sha256 = str(ref.get("sha256", ""))
            testcase.assertTrue(path, f"unexpected evidence ref: {ref}")
            testcase.assertRegex(sha256, r"^[0-9a-f]{64}$")
            lowered = path.lower()
        else:
            testcase.assertIsInstance(ref, str)
            testcase.assertTrue(ref.startswith(allowed_prefixes), f"unexpected evidence ref: {ref}")
            lowered = ref.lower()
        for forbidden in ("raw", "transcript", "prompt", "provider", "payload", "api_key", "secret", "credential", "/home/", "/mnt/"):
            testcase.assertNotIn(forbidden, lowered)


def make_fake_launch_result(
    spec,
    scenario_name: str,
    *,
    executor: str | None = None,
    log_path: str | None = None,
) -> LaunchResult:
    scenario = fake_executor_scenarios(
        artifact=str(spec.plan) if getattr(spec, "plan", None) is not None else "none"
    )[scenario_name]
    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("" if scenario.zero_byte_log else (scenario.automation or "fake durable output\n"), encoding="utf-8")
    return LaunchResult(
        command=spec.command,
        returncode=scenario.returncode,
        output=scenario.automation or "",
        executor=executor or spec.executor,
        log_path=log_path,
        process_pid=12345 if scenario.zero_byte_log else None,
        started_at=utc_now() if scenario.zero_byte_log else None,
        finished_at=utc_now() if scenario.zero_byte_log else None,
        timed_out=scenario.timed_out,
        interrupted=scenario.interrupted,
        cleanup_evidence=scenario.cleanup_evidence,
    )


def make_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "specs").mkdir()
    (repo / "plans").mkdir()
    (repo / "README.md").write_text("fixture\n")
    roadmap = repo / "specs" / "phase-plans-v1.md"
    roadmap.write_text(
        "# Roadmap\n\n"
        "### Phase 0 — Contract (CONTRACT)\n\n"
        "### Phase 1 — Access (ACCESS)\n\n"
        "### Phase 2 — Runner (RUNNER)\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    return repo


def write_named_roadmap(repo: Path, phases: tuple[tuple[str, str], ...], version: str = "v1") -> Path:
    roadmap = repo / "specs" / f"phase-plans-{version}.md"
    body = "# Roadmap\n\n" + "\n\n".join(
        f"### Phase {index} - {title} ({alias})" for index, (alias, title) in enumerate(phases)
    )
    roadmap.write_text(f"{body}\n")
    return roadmap


def commit_fixture_paths(repo: Path, message: str, *paths: Path) -> None:
    relpaths = [str(path.relative_to(repo)) for path in paths]
    subprocess.run(["git", "add", *relpaths], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, stdout=subprocess.DEVNULL)


def write_phase_plan(
    repo: Path,
    phase: str,
    roadmap: Path,
    body: str = "",
    extra_frontmatter: dict[str, str] | None = None,
    owned_files: tuple[str, ...] | None = None,
) -> Path:
    plan = repo / "plans" / f"phase-plan-v1-{phase.upper()}.md"
    roadmap_hash = hashlib.sha256(roadmap.read_bytes()).hexdigest()
    rel_roadmap = roadmap.resolve().relative_to(repo.resolve())
    extra = "".join(f"{key}: {value}\n" for key, value in (extra_frontmatter or {}).items())
    if not body:
        owned = "none (read-only lane)" if owned_files is None else ", ".join(f"`{item}`" for item in owned_files)
        body = (
            f"# {phase.upper()}\n\n"
            "## Lanes\n\n"
            f"### SL-0 - {phase.upper()}\n"
            f"- **Owned files**: {owned}\n"
        )
    plan.write_text(
        "---\n"
        "phase_loop_plan_version: 1\n"
        f"phase: {phase.upper()}\n"
        f"roadmap: {rel_roadmap}\n"
        f"roadmap_sha256: {roadmap_hash}\n"
        f"{extra}"
        "---\n"
        f"{body}\n"
    )
    return plan


def provenanced_state(repo: Path, roadmap: Path, phases: dict[str, str]) -> StateSnapshot:
    return StateSnapshot(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phases=phases, **snapshot_provenance(roadmap))


def provenanced_event(repo: Path, roadmap: Path, phase: str, status: str, action: str = "status") -> LoopEvent:
    return LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase.upper(),
        action=action,
        status=status,
        model="gpt-5.4",
        reasoning_effort="medium",
        source="fixture",
        **event_provenance(roadmap, phase),
    )


def make_message_board_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo,
        (
            ("DELIVERY", "Direct Delivery"),
            ("CALLBACKS", "Callback Notifications"),
        ),
    )
    plan = write_phase_plan(repo, "DELIVERY", roadmap, owned_files=("inbox.log",))
    stale_plan = write_phase_plan(repo, "CALLBACKS", roadmap)
    commit_fixture_paths(repo, "message board regression fixture", roadmap, plan, stale_plan)
    return PhaseLoopFixture(
        repo=repo,
        roadmap=roadmap,
        execute_phase="DELIVERY",
        next_phase="CALLBACKS",
        stale_phase="CALLBACKS",
        plan=plan,
        stale_plan=stale_plan,
    )


def make_greenfield_closeout_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo,
        (
            ("ENFORCE", "Patch Enforcement"),
            ("RUNTIME", "Runtime Bridge"),
        ),
    )
    plan = write_phase_plan(repo, "ENFORCE", roadmap, owned_files=("artifacts/enforce-report.json",))
    commit_fixture_paths(repo, "greenfield closeout fixture", roadmap, plan)
    return PhaseLoopFixture(
        repo=repo,
        roadmap=roadmap,
        execute_phase="ENFORCE",
        next_phase="RUNTIME",
        plan=plan,
    )


def make_regenesis_amendment_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo,
        (
            ("AFFVERIFY", "Affordance Verification"),
            ("VISUAL", "Visual Fidelity"),
        ),
    )
    plan = write_phase_plan(repo, "AFFVERIFY", roadmap)
    stale_plan = write_phase_plan(repo, "VISUAL", roadmap)
    commit_fixture_paths(repo, "regenesis amendment fixture", roadmap, plan, stale_plan)
    return PhaseLoopFixture(
        repo=repo,
        roadmap=roadmap,
        execute_phase="AFFVERIFY",
        next_phase="MOBSHELL",
        stale_phase="VISUAL",
        plan=plan,
        stale_plan=stale_plan,
    )


def make_code_index_blocker_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo,
        (
            ("QUERY", "Query Ready"),
            ("RELEASE", "Release Closeout"),
        ),
    )
    plan = write_phase_plan(repo, "QUERY", roadmap)
    commit_fixture_paths(repo, "code index blocker fixture", roadmap, plan)
    return PhaseLoopFixture(
        repo=repo,
        roadmap=roadmap,
        execute_phase="QUERY",
        next_phase="RELEASE",
        plan=plan,
    )


def make_completed_roadmap_fixture(tmp_path: Path) -> PhaseLoopFixture:
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo,
        (
            ("OBSERVE", "Observe Current State"),
            ("RUNNER", "Runner Hardening"),
        ),
    )
    commit_fixture_paths(repo, "completed roadmap fixture", roadmap)
    return PhaseLoopFixture(
        repo=repo,
        roadmap=roadmap,
        execute_phase="RUNNER",
    )
