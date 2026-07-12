from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from phase_loop_runtime.discovery import select_roadmap
from phase_loop_runtime.cli import main
from phase_loop_runtime.events import append_event, append_work_unit_event, event_path, read_work_unit_events
from phase_loop_runtime.roadmap_authority import (
    LATCH_MARKER,
    REQUIRED_MARKER,
    RoadmapAuthorityError,
    assert_roadmap_authorized,
    roadmap_authority_file,
    roadmap_authority_latch_file,
    roadmap_authority_required_file,
    roadmap_authority_worktree_latch_file,
)
from phase_loop_runtime.pipeline_adapter.merge_policy import MergePolicy
from phase_loop_runtime.pipeline_adapter.ratification import emit_ratification_passed
from phase_loop_runtime.runner import _append_tier3_audit_event
from phase_loop_runtime.models import WorkUnitEventMetadata, WorkUnitIdentity
from phase_loop_runtime.state_degradation import record_degradation
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import make_repo, provenanced_event, provenanced_state


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_worktree_latch(repo: Path) -> None:
    latch = roadmap_authority_worktree_latch_file(repo)
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.write_text(LATCH_MARKER, encoding="utf-8")
    latch.chmod(0o400)


def _write_authority(repo: Path, active: Path, retired: Path) -> None:
    marker = roadmap_authority_required_file(repo)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(REQUIRED_MARKER, encoding="utf-8")
    marker.chmod(0o400)
    latch = roadmap_authority_latch_file(repo)
    latch.write_text(LATCH_MARKER, encoding="utf-8")
    latch.chmod(0o400)
    path = roadmap_authority_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    retired_sha256 = _sha256(retired)
    common_blob = Path("phase-loop-retired-roadmaps") / f"{retired_sha256}.md"
    blob_path = path.parent / common_blob
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_bytes(retired.read_bytes())
    blob_path.chmod(0o400)
    path.write_text(
        json.dumps(
            {
                "schema": "phase_loop_roadmap_authority.v1",
                "status": "active",
                "active_roadmap": str(active.relative_to(repo)),
                "active_roadmap_sha256": _sha256(active),
                "retired_roadmaps": [{"path": str(retired.relative_to(repo)), "sha256": retired_sha256, "common_blob": str(common_blob)}],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    _write_worktree_latch(repo)


def test_submodule_gitfile_uses_its_own_git_directory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "submodule"
        git_dir = root / "modules" / "submodule"
        repo.mkdir()
        git_dir.mkdir(parents=True)
        (repo / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

        assert roadmap_authority_file(repo) == git_dir / "phase-loop-roadmap-authority.json"


def test_non_git_directory_preserves_legacy_ungoverned_behavior() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)

        assert roadmap_authority_file(repo) == repo / ".git" / "phase-loop-roadmap-authority.json"
        assert assert_roadmap_authorized(repo, "specs/legacy.md") == repo / "specs" / "legacy.md"


def test_malformed_gitfile_still_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        (repo / ".git").write_text("invalid\n", encoding="utf-8")

        with pytest.raises(RoadmapAuthorityError, match="marker path unavailable"):
            roadmap_authority_file(repo)


def test_explicit_retired_roadmap_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)

        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            select_roadmap(repo, retired)
        assert select_roadmap(repo, active) == active.resolve()


def test_authority_selects_active_roadmap_before_ambiguous_glob() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)

        assert select_roadmap(repo) == active.resolve()


def test_retired_roadmap_event_append_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)

        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            append_event(repo, provenanced_event(repo, retired, "CONTRACT", "planned"))
        assert not event_path(repo).exists()


def test_retired_roadmap_digest_drift_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        authority = json.loads(roadmap_authority_file(repo).read_text(encoding="utf-8"))
        blob = roadmap_authority_file(repo).parent / authority["retired_roadmaps"][0]["common_blob"]
        blob.chmod(0o600)
        blob.write_text("# tampered historical roadmap\n", encoding="utf-8")
        blob.chmod(0o400)

        with pytest.raises(RoadmapAuthorityError, match="retired roadmap digest mismatch"):
            select_roadmap(repo, active)
        with pytest.raises(RoadmapAuthorityError, match="retired roadmap digest mismatch"):
            select_roadmap(repo)


def test_active_selection_uses_common_retired_blob_when_worktree_path_is_absent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        retired.unlink()

        assert select_roadmap(repo) == active.resolve()
        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            select_roadmap(repo, retired)


def test_maintenance_mutations_require_available_active_roadmap() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        active.unlink()

        assert main(["migrate-events", "--repo", str(repo)]) == 2
        assert main(["archive-state", "--repo", str(repo)]) == 2


def test_malformed_authority_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        _write_authority(repo, roadmap, roadmap)
        path = roadmap_authority_file(repo)
        path.write_text("{}", encoding="utf-8")

        with pytest.raises(RoadmapAuthorityError, match="invalid roadmap authority schema"):
            select_roadmap(repo, roadmap)


def test_missing_active_rejects_all_events() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        branch_switch = provenanced_event(
            repo,
            active,
            "CONTRACT",
            "planned",
            action="coordinator.branch_switched",
        )
        normal = provenanced_event(repo, active, "CONTRACT", "planned")
        active.unlink()

        with pytest.raises(RoadmapAuthorityError, match="digest mismatch"):
            append_event(repo, branch_switch)
        with pytest.raises(RoadmapAuthorityError, match="digest mismatch"):
            append_event(repo, normal)


def test_divergent_active_rejects_all_events() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        branch_switch = provenanced_event(
            repo,
            active,
            "CONTRACT",
            "planned",
            action="coordinator.branch_switched",
        )
        normal = provenanced_event(repo, active, "CONTRACT", "planned")
        active.write_text("# divergent branch roadmap\n", encoding="utf-8")

        with pytest.raises(RoadmapAuthorityError, match="digest mismatch"):
            append_event(repo, branch_switch)
        with pytest.raises(RoadmapAuthorityError, match="digest mismatch"):
            append_event(repo, normal)


def test_work_unit_without_roadmap_uses_active_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        event = WorkUnitEventMetadata(
            identity=WorkUnitIdentity(phase="CONTRACT", kind="lane_execute", lane_id="SL-0", attempt=1),
            status="pending",
            event_type="launch",
        )

        append_work_unit_event(repo, event)

        assert read_work_unit_events(repo)[0]["roadmap"] == str(active.resolve())


def test_work_unit_without_authority_preserves_empty_roadmap() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        event = WorkUnitEventMetadata(
            identity=WorkUnitIdentity(phase="CONTRACT", kind="lane_execute", lane_id="SL-0", attempt=1),
            status="pending",
            event_type="launch",
        )

        append_work_unit_event(repo, event)

        assert read_work_unit_events(repo)[0]["roadmap"] == ""


def test_required_marker_blocks_removed_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        marker = roadmap_authority_required_file(repo)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(REQUIRED_MARKER, encoding="utf-8")
        marker.chmod(0o400)

        with pytest.raises(RoadmapAuthorityError, match="required roadmap authority is missing"):
            select_roadmap(repo, roadmap)


def test_latch_blocks_removed_authority_and_required_marker() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        latch = roadmap_authority_latch_file(repo)
        latch.parent.mkdir(parents=True, exist_ok=True)
        latch.write_text(LATCH_MARKER, encoding="utf-8")
        latch.chmod(0o400)

        with pytest.raises(RoadmapAuthorityError, match="required roadmap authority is missing"):
            select_roadmap(repo, roadmap)


def test_linked_worktree_shares_retired_roadmap_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = make_repo(root / "primary")
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", str(active.relative_to(repo))], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-m", "add active roadmap"],
            check=True,
            capture_output=True,
        )
        linked = root / "linked"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "linked-authority-test", str(linked), "HEAD^"],
            check=True,
            capture_output=True,
        )
        _write_authority(repo, active, retired)
        _write_worktree_latch(linked)
        state_path = linked / ".phase-loop" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_bytes(b'{"preserved":true}\n')
        state_before = state_path.read_bytes()

        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            select_roadmap(linked, linked / "specs" / "phase-plans-v1.md")
        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            append_event(
                linked,
                provenanced_event(linked, linked / "specs" / "phase-plans-v1.md", "CONTRACT", "planned"),
            )
        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            write_state(linked, provenanced_state(linked, linked / "specs" / "phase-plans-v1.md", {"CONTRACT": "blocked"}))
        assert state_path.read_bytes() == state_before
        assert not event_path(linked).exists()
        with pytest.raises(RoadmapAuthorityError, match="digest mismatch"):
            select_roadmap(linked)


def test_linked_worktree_missing_git_pointer_remains_latched() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = make_repo(root / "primary")
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", str(active.relative_to(repo))], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-m", "add active roadmap"],
            check=True,
            capture_output=True,
        )
        linked = root / "linked"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "linked-missing-git-test", str(linked), "HEAD^"],
            check=True,
            capture_output=True,
        )
        _write_authority(repo, active, retired)
        _write_worktree_latch(linked)
        git_pointer = linked / ".git"
        git_pointer.unlink()

        with pytest.raises(RoadmapAuthorityError, match="required roadmap authority is missing"):
            append_event(
                linked,
                provenanced_event(linked, linked / "specs" / "phase-plans-v1.md", "CONTRACT", "planned"),
            )
        assert not event_path(linked).exists()


def test_cli_reports_controlled_retired_roadmap_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)

        result = main(["status", "--repo", str(repo), "--roadmap", str(retired)])

        assert result == 2
        assert "roadmap authority refusal" in capsys.readouterr().err


def test_retired_worktree_migrate_handoffs_apply_preserves_source_bytes() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = make_repo(root / "primary")
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", str(active.relative_to(repo))], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-m", "add active roadmap"],
            check=True,
            capture_output=True,
        )
        linked = root / "linked"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "linked-migrate-test", str(linked), "HEAD^"],
            check=True,
            capture_output=True,
        )
        _write_authority(repo, active, retired)
        _write_worktree_latch(linked)
        source = root / "home" / ".codex" / "skills" / "example" / "handoffs" / "run" / "latest.md"
        source.parent.mkdir(parents=True)
        source.write_text(f"---\nrepo_root: {linked}\n---\nretired handoff\n", encoding="utf-8")
        before = source.read_bytes()

        with patch.dict(os.environ, {"HOME": str(root / "home")}):
            result = main(
                [
                    "migrate-handoffs",
                    "--repo",
                    str(linked),
                    "--roadmap",
                    "specs/phase-plans-v1.md",
                    "--apply",
                ]
            )

        assert result == 2
        assert source.read_bytes() == before
        assert not (linked / ".dev-skills").exists()


@pytest.mark.parametrize("command", ["run", "resume", "dry-run"])
def test_reset_capability_rejects_retired_roadmap_before_mutation(command: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)
        record_degradation(repo, "claude", "account_or_billing_setup", "RUNNER", "Claude auth missing", 300)
        degradation = repo / ".phase-loop" / "executor-degradation.json"
        before = degradation.read_bytes()

        result = main(
            [
                command,
                "--repo",
                str(repo),
                "--roadmap",
                str(retired),
                "--reset-capability",
            ]
        )

        assert result == 2
        assert degradation.read_bytes() == before


def test_direct_ratification_and_tier3_writers_reject_retired_roadmap() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repo = make_repo(Path(directory))
        retired = repo / "specs" / "phase-plans-v1.md"
        active = repo / "specs" / "phase-plans-v2.md"
        active.write_text(retired.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
        _write_authority(repo, active, retired)

        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            emit_ratification_passed(
                repo,
                "v1",
                "CONTRACT",
                "complete",
                MergePolicy(on_pass="required", approvers=("ops",)),
                {"terminal_status": "complete"},
                roadmap_path=retired,
            )
        with pytest.raises(RoadmapAuthorityError, match="roadmap retired"):
            _append_tier3_audit_event(
                repo=repo,
                roadmap=retired,
                phase="CONTRACT",
                metadata={},
                model="gpt-5.6-sol",
                reasoning_effort="max",
                source="test",
            )
