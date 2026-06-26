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


def test_collaborator_bootstrap_doc_is_cited_from_runtime_docs():
    manifest = read("docs/phase-loop/harness-substrate-manifest.md")
    boundary = read("docs/phase-loop/runtime-boundary.md")

    assert "docs/phase-loop/collaborator-bootstrap.md" in manifest
    assert "docs/phase-loop/collaborator-bootstrap.md" in boundary


def test_collaborator_bootstrap_names_only_supported_surfaces():
    doc = read("docs/phase-loop/collaborator-bootstrap.md")
    for token in [
        "vendor/phase-loop-runtime",
        "vendor/phase-loop-skills",
        "phase-loop --repo",
        "install --harness",
        "repo-local",
        "user-local",
        "~/.claude/skills",
        "~/.codex/skills",
        "~/.gemini/skills",
        "~/.config/opencode/skills",
    ]:
        assert token in doc


def test_collaborator_bootstrap_keeps_downstream_adoption_mediated():
    doc = read("docs/phase-loop/collaborator-bootstrap.md")
    assert "ReGenesis adoption stays mediated through Governed Pipeline" in doc
    for token in [
        "does not grant dotfiles permission for ReGenesis",
        "Governed Pipeline",
        "Portal",
        "Greenfield",
        "`.pipeline/**`",
        "private evidence",
        "credentials",
        "provider-supplied data",
        "local environment values",
        "legacy `.codex/phase-loop/**`",
    ]:
        assert token in doc


def test_collaborator_bootstrap_excludes_owner_runtime_surfaces():
    doc = read("docs/phase-loop/collaborator-bootstrap.md")
    for token in [
        "host bootstrap",
        "shell profile sourcing",
        "SSH setup",
        "MCP gateway setup",
        "credential setup",
        "terminal configuration",
        "Zellij configuration",
        "cron",
        "launchd",
        "generic 1Password setup",
        "raw source evidence",
        "sibling repos",
    ]:
        assert token in doc
