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


def test_downstream_packet_readme_defines_schema_and_inventory():
    doc = read("docs/phase-loop/downstream-instruction-scope/README.md")

    for token in [
        "downstream_instruction_packet_schema.v1",
        "packet",
        "target_repo",
        "depends_on",
        "instruction_sources",
        "owned_surfaces",
        "non_goals",
        "redaction_posture",
        "closeout_evidence",
        "governed-pipeline.md",
        "consiliency-portal.md",
        "regenesis.md",
        "metadata-only",
        "no-direct-sibling-repo-write",
        "Governed Pipeline receives ingestion and canonical-refresh work first",
        "Consiliency Portal consumes Governed Pipeline projection state",
        "ReGenesis adopts repo-local instruction changes after dotfiles packets",
    ]:
        assert token in doc


def test_downstream_packet_readme_preserves_dotfiles_boundary():
    doc = read("docs/phase-loop/downstream-instruction-scope/README.md")

    for token in [
        "Dotfiles owns this packet directory and its tests",
        "sibling-repo mutations remain owned by those repositories",
        "secret material",
        "provider-supplied data",
        "raw evidence",
        "environment values",
        "legacy `.codex/phase-loop/**`",
    ]:
        assert token in doc
