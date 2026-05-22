from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from phase_loop_runtime.adoption_bundle import generate_adoption_bundle, stable_json_bytes
from phase_loop_runtime.baml_modular import parse_baml_response


REPO_ROOT = Path(__file__).resolve().parents[3]
ADOPTION_BUNDLE_PATH = REPO_ROOT / "docs" / "adoption" / "dotfiles-adoption-bundle.json"
C4_DOCUMENT_PATH = REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-c4-document.md"
TASK_CATALOG_PATH = REPO_ROOT / "docs" / "tasks" / "dotfiles-task-catalog.md"
RUNTIME_PROJECTION_FIXTURE = {
    "runtime_version": "0.1.0",
    "protocol_version": "phase-loop-protocol-v1",
    "harness": "codex",
    "source_bundle_digest": "none",
    "closeout_status": "complete",
    "handoff_status": "written",
    "current_phase_boundary": "DOTADOPT",
    "last_event_iso": "2026-05-22T00:00:00Z",
    "install_status": "installed",
    "gitignore_init_status": "present",
    "operating_mode": "standalone",
}


def _section_after(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def _bulleted_mapping(section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"\s*(?:- )?([^:]+):\s*(.*)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _load_adoption_bundle() -> dict[str, object]:
    return json.loads(ADOPTION_BUNDLE_PATH.read_text(encoding="utf-8"))


def _resolve_repo_relative(relative_path: str) -> Path:
    path = (REPO_ROOT / relative_path).resolve()
    path.relative_to(REPO_ROOT)
    return path


def _build_c4_fixture() -> dict[str, object]:
    document = C4_DOCUMENT_PATH.read_text(encoding="utf-8")
    anchors = []
    for block in _section_after(document, "Anchors").strip().split("\n- "):
        clean = block.removeprefix("- ").strip()
        if clean:
            anchors.append(_bulleted_mapping(clean))
    return {
        "title": _section_after(document, "Title").strip(),
        "mermaid_context_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-context.mmd").read_text(encoding="utf-8"),
        "mermaid_container_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-container.mmd").read_text(encoding="utf-8"),
        "mermaid_component_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-component.mmd").read_text(encoding="utf-8"),
        "anchors": anchors,
        "description": _section_after(document, "Description").strip(),
    }


def _build_task_catalog_fixture() -> dict[str, object]:
    catalog = TASK_CATALOG_PATH.read_text(encoding="utf-8")
    tasks = []
    for task_block in re.split(r"^### Task\s*$", _section_after(catalog, "Tasks"), flags=re.MULTILINE):
        fields = _bulleted_mapping(task_block)
        if not fields:
            continue
        fields["dependencies"] = [item.strip() for item in fields["dependencies"].split(",") if item.strip()]
        tasks.append(fields)
    return {
        "tasks": tasks,
        "audiences": re.findall(r"^- ([a-z_]+)$", _section_after(catalog, "Audiences"), re.MULTILINE),
        "references": [
            _bulleted_mapping(block.removeprefix("- ").strip())
            for block in _section_after(catalog, "References").strip().split("\n- ")
            if block.removeprefix("- ").strip()
        ],
    }


class PhaseLoopV22E2ETest(unittest.TestCase):
    def test_governed_pipeline_can_ingest_committed_adoption_bundle(self):
        bundle = _load_adoption_bundle()

        parse_baml_response("DotfilesAdoptionManifest", json.dumps(bundle))
        self.assertEqual(bundle["operating_mode"], "standalone")

        for root in bundle["source_roots"]:
            path_glob = root["path_glob"]
            self.assertFalse(path_glob.startswith(("/", "~")))
            self.assertNotIn("..", Path(path_glob).parts)
            self.assertTrue(list(REPO_ROOT.glob(path_glob)), path_glob)

        referenced_paths = [bundle["visibility_contract_ref"]]
        referenced_paths.extend(ref["source_path"] for ref in bundle["schema_refs"])
        referenced_paths.extend(ref["source_path"] for ref in bundle["c4_document_refs"])
        referenced_paths.extend(ref["source_path"] for ref in bundle["task_catalog_refs"])
        for relative_path in referenced_paths:
            with self.subTest(relative_path=relative_path):
                self.assertFalse(relative_path.startswith(("/", "~")))
                self.assertNotIn("..", Path(relative_path).parts)
                self.assertTrue(_resolve_repo_relative(relative_path).exists())

        for ref in bundle["schema_refs"]:
            path = _resolve_repo_relative(ref["source_path"])
            self.assertEqual(ref["digest"], f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}")

        parse_baml_response("DotfilesC4Document", json.dumps(_build_c4_fixture()))
        parse_baml_response("DotfilesTaskCatalog", json.dumps(_build_task_catalog_fixture()))
        parse_baml_response("DotfilesRuntimeProjection", json.dumps(RUNTIME_PROJECTION_FIXTURE))

    def test_committed_bundle_matches_repeated_stable_generation(self):
        bundle = _load_adoption_bundle()
        generated_once = generate_adoption_bundle(
            REPO_ROOT,
            generated_at=bundle["generated_at"],
            operating_mode=bundle["operating_mode"],
        )
        generated_twice = generate_adoption_bundle(
            REPO_ROOT,
            generated_at=bundle["generated_at"],
            operating_mode=bundle["operating_mode"],
        )

        self.assertEqual(stable_json_bytes(generated_once), ADOPTION_BUNDLE_PATH.read_bytes())
        self.assertEqual(stable_json_bytes(generated_once), stable_json_bytes(generated_twice))


if __name__ == "__main__":
    unittest.main()
