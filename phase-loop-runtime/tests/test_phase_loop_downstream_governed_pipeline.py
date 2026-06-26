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


def test_governed_pipeline_packet_names_ingest_and_refresh_work():
    doc = read("docs/phase-loop/downstream-instruction-scope/governed-pipeline.md")

    for token in [
        "governed_pipeline_instruction_scope_packet.v1",
        "instruction-source provenance",
        "`spec_delta_closeout.v1` ingest",
        "source-bundle recording of instruction surfaces",
        "canonical refresh handoff",
        "Source provenance",
        "Source-bundle recording",
        "Spec-closeout ingest",
        "Canonical refresh handoff",
        "Closeout evidence updates",
    ]:
        assert token in doc


def test_governed_pipeline_packet_freezes_ownership_and_non_goals():
    doc = read("docs/phase-loop/downstream-instruction-scope/governed-pipeline.md")

    for token in [
        "Governed Pipeline owns `.pipeline/**`",
        "source-bundle schemas",
        "protected-source freshness",
        "canonical refresh",
        "replan logic",
        "closeout ingest",
        "downstream mirror writes",
        "projection production",
        "Dotfiles owns only this packet artifact",
        "precedes Consiliency Portal projection work",
        "planning-only Governed Pipeline handoff",
        "private evidence",
        "raw evidence",
        "provider-supplied data",
        "credentials",
        "environment values",
        "legacy `.codex/phase-loop/**`",
    ]:
        assert token in doc
