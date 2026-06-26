from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rollback_doc_covers_status_dry_run_symlink_copy_and_rerun():
    doc = read("docs/phase-loop/collaborator-skill-rollback.md")
    for token in [
        "phase-loop --repo \"$DOTFILES_DIR\" install --status --json",
        "dry run before apply",
        "stale symlinks",
        "Symlink-Mode Rollback",
        "unlink",
        "Copy-Mode Rollback",
        "Do not remove user-owned non-symlink skills",
        "After `git pull`, rerun the collaborator bootstrap",
        "./bootstrap-mac-skills.sh",
    ]:
        assert token in doc


def test_rollback_doc_forbids_personal_and_downstream_targets():
    doc = read("docs/phase-loop/collaborator-skill-rollback.md")
    for token in [
        "shell profiles",
        "SSH config",
        "MCP config",
        "credential stores",
        "cron",
        "launchd",
        "Zellij config",
        "sibling repos",
        "`.pipeline/**`",
        "legacy `.codex/phase-loop/**`",
        "ReGenesis",
        "Governed Pipeline",
        "Portal",
        "Greenfield",
    ]:
        assert token in doc
