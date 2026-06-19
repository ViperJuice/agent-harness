from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_regenesis_packet_covers_repo_local_instruction_adoption():
    doc = read("docs/phase-loop/downstream-instruction-scope/regenesis.md")

    for token in [
        "regenesis_instruction_scope_packet.v1",
        "`AGENTS.md`",
        "project-local `CLAUDE.md` importing `@AGENTS.md`",
        "`.agents/skills` posture",
        "Jesse-safe onboarding",
        "Optional Governed Pipeline adoption",
        "Claude-specific overlay",
        "Collaborator-safe skill posture",
        "Jesse-safe onboarding verification",
    ]:
        assert token in doc


def test_regenesis_packet_freezes_owned_surfaces_and_non_goals():
    doc = read("docs/phase-loop/downstream-instruction-scope/regenesis.md")

    for token in [
        "ReGenesis owns `AGENTS.md`",
        "project-local `CLAUDE.md`",
        "`.agents/skills`",
        "repo specs",
        "onboarding docs",
        "phase artifacts",
        "Dotfiles owns no ReGenesis repo edits in this phase",
        "follows dotfiles packets and Governed Pipeline expectation setting",
        "planning-only ReGenesis handoff",
        "owner-fleet dotfiles",
        "shell profile sourcing",
        "SSH setup",
        "generic 1Password",
        "MCP gateway setup",
        "raw evidence",
        "provider-supplied data",
        "credentials",
        "environment values",
        "legacy `.codex/phase-loop/**`",
    ]:
        assert token in doc
