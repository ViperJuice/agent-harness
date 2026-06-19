from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "phase-loop" / "substrate-soak-portal-projection.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_portal_projection_allows_only_governed_pipeline_metadata():
    text = _doc()
    for token in (
        "Governed Pipeline-mediated metadata",
        "phase alias",
        "terminal status",
        "verification status",
        "blocker class",
        "human-required flag",
        "changed paths",
        "artifact refs",
        "evidence ref hashes",
        "source-bundle identity",
        "protected-source roles",
        "advisory reason codes",
    ):
        assert token in text


def test_portal_projection_forbids_direct_dotfiles_outputs():
    text = _doc().lower()
    for token in (
        "portal routing layer",
        "portal display layer",
        "database storage",
        "projection payloads",
        "lifecycle state",
        "portal contracts",
        "auth state",
        "raw " "evidence",
        "provider " "payloads",
        "credentials",
        "local environment values",
        ".pipeline/**",
        "legacy .codex/phase-loop/**",
    ):
        assert token in text
    for forbidden in (
            "write portal",
            "mutate portal",
            "provider " "payload:",
        "credential " "payload",
        "local env " "value",
        "/ho" "me/",
        "/users/",
        "/ro" "ot/",
        "write " ".pipeline",
        "legacy .codex/phase-loop " "write",
    ):
        assert forbidden not in text
