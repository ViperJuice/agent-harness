from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_docs_split_owner_bootstrap_from_collaborator_path():
    readme = read("README.md")
    onboarding = read("ONBOARDING.md")
    hosts = read("hosts/README.md")

    assert "./bootstrap-mac-skills.sh" in readme
    assert "The full owner `bootstrap.sh` is for" in readme
    assert "Runtime Boundary and Isolation" in readme
    assert "collaborator" in onboarding
    assert "maintainer fleet path" in onboarding.lower()
    assert "ReGenesis-safe" in onboarding
    assert "Full `bootstrap.sh` is for maintainer fleet hosts only" in hosts


def test_collaborator_docs_name_personal_surfaces_not_touched():
    docs = "\n".join([read("README.md"), read("ONBOARDING.md"), read("hosts/README.md")])
    required = [
        "shell profile",
        "SSH",
        "credentials",
        "MCP",
        "terminal",
        "Zellij",
        "cron",
        "launchd",
        "generic 1Password",
        "provider payloads",
        "raw evidence",
        "local environment values",
        "sibling repos",
        "legacy `.codex/phase-loop/**`",
    ]
    for token in required:
        assert token in docs
