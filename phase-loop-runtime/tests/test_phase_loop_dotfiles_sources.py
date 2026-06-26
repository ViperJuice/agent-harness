import json
import re
import unittest
from pathlib import Path

from phase_loop_runtime.baml_modular import parse_baml_response


import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

REPO_ROOT = Path(__file__).resolve().parents[3]
C4_DIR = REPO_ROOT / "docs" / "c4"
TASK_CATALOG_PATH = REPO_ROOT / "docs" / "tasks" / "dotfiles-task-catalog.md"
C4_DOCUMENT_PATH = C4_DIR / "phase-loop-runtime-c4-document.md"
REQUIRED_AUDIENCES = {
    "executive",
    "management",
    "development",
    "debugging",
    "infrastructure",
    "security_compliance",
    "agent",
}


def _read_repo_text(relative_path):
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _extract_bulleted_mapping(section):
    values = {}
    for line in section.splitlines():
        match = re.match(r"\s*(?:- )?([^:]+):\s*(.*)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _section_after(text, heading):
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def _build_c4_fixture():
    document = C4_DOCUMENT_PATH.read_text(encoding="utf-8")
    title = _section_after(document, "Title").strip()
    description = _section_after(document, "Description").strip()

    anchors = []
    for block in _section_after(document, "Anchors").strip().split("\n- "):
        clean = block.removeprefix("- ").strip()
        if not clean:
            continue
        anchors.append(_extract_bulleted_mapping(clean))

    return {
        "title": title,
        "mermaid_context_source": _read_repo_text("docs/c4/phase-loop-runtime-context.mmd"),
        "mermaid_container_source": _read_repo_text("docs/c4/phase-loop-runtime-container.mmd"),
        "mermaid_component_source": _read_repo_text("docs/c4/phase-loop-runtime-component.mmd"),
        "anchors": anchors,
        "description": description,
    }


def _build_task_catalog_fixture():
    catalog = TASK_CATALOG_PATH.read_text(encoding="utf-8")
    audiences = re.findall(r"^- ([a-z_]+)$", _section_after(catalog, "Audiences"), re.MULTILINE)

    references = []
    for block in _section_after(catalog, "References").strip().split("\n- "):
        clean = block.removeprefix("- ").strip()
        if not clean:
            continue
        references.append(_extract_bulleted_mapping(clean))

    tasks = []
    for task_block in re.split(r"^### Task\s*$", _section_after(catalog, "Tasks"), flags=re.MULTILINE):
        fields = _extract_bulleted_mapping(task_block)
        if not fields:
            continue
        fields["dependencies"] = [
            dependency.strip()
            for dependency in fields["dependencies"].split(",")
            if dependency.strip()
        ]
        tasks.append(fields)

    return {"tasks": tasks, "audiences": audiences, "references": references}


class PhaseLoopDotfilesSourcesTest(unittest.TestCase):
    def test_c4_document_source_fixture_parses_as_dotfiles_c4_document(self):
        fixture = _build_c4_fixture()

        parsed = parse_baml_response("DotfilesC4Document", json.dumps(fixture))

        self.assertEqual(parsed.payload, fixture)
        self.assertIn("C4Context", fixture["mermaid_context_source"])
        self.assertIn("C4Container", fixture["mermaid_container_source"])
        self.assertIn("C4Component", fixture["mermaid_component_source"])
        self.assertIn("phase-loop-runtime-context.mmd", C4_DOCUMENT_PATH.read_text(encoding="utf-8"))
        self.assertTrue(fixture["anchors"])

    def test_c4_mermaid_sources_lint_without_rendered_outputs(self):
        expectations = {
            "phase-loop-runtime-context.mmd": ("C4Context", "dotfiles", "governed-pipeline", "consiliency-portal", "phase-loop runtime"),
            "phase-loop-runtime-container.mmd": ("C4Container", "runtime_boundary", "BAML", "closeout", "harness"),
            "phase-loop-runtime-component.mmd": ("C4Component", "runtime_boundary", "baml_modular", "closeout_validation", "runner", "skill bundle"),
        }

        for file_name, required_terms in expectations.items():
            with self.subTest(file_name=file_name):
                source = (C4_DIR / file_name).read_text(encoding="utf-8")
                self.assertTrue(source.startswith(required_terms[0]))
                self.assertRegex(source, r"(?m)^  title ")
                for term in required_terms:
                    self.assertIn(term, source)

    def test_task_catalog_source_fixture_parses_as_dotfiles_task_catalog(self):
        fixture = _build_task_catalog_fixture()

        parsed = parse_baml_response("DotfilesTaskCatalog", json.dumps(fixture))

        self.assertEqual(parsed.payload, fixture)
        self.assertEqual(set(fixture["audiences"]), REQUIRED_AUDIENCES)
        self.assertEqual({task["audience"] for task in fixture["tasks"]}, REQUIRED_AUDIENCES)
        for task in fixture["tasks"]:
            self.assertEqual(
                set(task),
                {"id", "title", "audience", "owner", "dependencies", "status"},
            )
            self.assertIn(task["audience"], REQUIRED_AUDIENCES)
            self.assertIsInstance(task["dependencies"], list)


if __name__ == "__main__":
    unittest.main()
