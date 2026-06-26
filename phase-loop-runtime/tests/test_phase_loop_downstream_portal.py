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


def test_portal_packet_covers_projection_and_display_work():
    doc = read("docs/phase-loop/downstream-instruction-scope/consiliency-portal.md")

    for token in [
        "portal_instruction_scope_packet.v1",
        "Governed Pipeline projection fields",
        "Instruction-source badges or status copy",
        "Spec-delta state projection",
        "Repository dependency posture warnings",
        "owner-fleet",
        "reusable-harness",
        "repo-local-collaborator",
        "`spec_delta_closeout.v1` decision",
    ]:
        assert token in doc


def test_portal_packet_freezes_dependency_order_and_ownership():
    doc = read("docs/phase-loop/downstream-instruction-scope/consiliency-portal.md")

    for token in [
        "Portal owns UI components",
        "API routes",
        "projection-consumption types",
        "Portal-local tests",
        "Governed Pipeline owns upstream ingest and projection production",
        "Dotfiles owns no Portal repo edits in this phase",
        "Portal follows Governed Pipeline",
        "planning-only Portal handoff",
        "raw evidence",
        "provider-supplied data",
        "credentials",
        "environment values",
        "legacy `.codex/phase-loop/**`",
    ]:
        assert token in doc
