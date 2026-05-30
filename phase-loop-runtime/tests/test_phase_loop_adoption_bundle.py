import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.adoption_bundle import generate_adoption_bundle, stable_json_bytes
from phase_loop_runtime.baml_modular import parse_baml_response


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "docs" / "adoption" / "dotfiles-adoption-bundle.json"
DOC_PATH = REPO_ROOT / "docs" / "adoption" / "dotfiles-adoption-bundle.md"
PROTOCOL_PATH = REPO_ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md"
RUNTIME_PROJECTION_REF = "phase-loop status --runtime-projection --json"
FORBIDDEN_REF_PARTS = ("private", "runtime_state", ".phase-loop", ".dev-skills")


def _load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _resolve_repo_path(relative_path: str) -> Path:
    path = (REPO_ROOT / relative_path).resolve()
    path.relative_to(REPO_ROOT)
    return path


def _glob_has_match(path_glob: str) -> bool:
    matches = list(REPO_ROOT.glob(path_glob))
    return bool(matches)


def _build_c4_fixture():
    source = (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-c4-document.md").read_text(encoding="utf-8")
    return {
        "title": _section_after(source, "Title").strip(),
        "mermaid_context_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-context.mmd").read_text(encoding="utf-8"),
        "mermaid_container_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-container.mmd").read_text(encoding="utf-8"),
        "mermaid_component_source": (REPO_ROOT / "docs" / "c4" / "phase-loop-runtime-component.mmd").read_text(encoding="utf-8"),
        "anchors": [
            _bulleted_mapping(block.removeprefix("- ").strip())
            for block in _section_after(source, "Anchors").strip().split("\n- ")
            if block.removeprefix("- ").strip()
        ],
        "description": _section_after(source, "Description").strip(),
    }


def _build_task_catalog_fixture():
    source = (REPO_ROOT / "docs" / "tasks" / "dotfiles-task-catalog.md").read_text(encoding="utf-8")
    tasks = []
    for task_block in re.split(r"^### Task\s*$", _section_after(source, "Tasks"), flags=re.MULTILINE):
        fields = _bulleted_mapping(task_block)
        if not fields:
            continue
        fields["dependencies"] = [item.strip() for item in fields["dependencies"].split(",") if item.strip()]
        tasks.append(fields)
    return {
        "tasks": tasks,
        "audiences": re.findall(r"^- ([a-z_]+)$", _section_after(source, "Audiences"), re.MULTILINE),
        "references": [
            _bulleted_mapping(block.removeprefix("- ").strip())
            for block in _section_after(source, "References").strip().split("\n- ")
            if block.removeprefix("- ").strip()
        ],
    }


def _runtime_projection_fixture():
    return {
        "runtime_version": "0.1.0",
        "protocol_version": "phase-loop-protocol-v1",
        "harness": "codex",
        "source_bundle_digest": "none",
        "closeout_status": "complete",
        "handoff_status": "written",
        "current_phase_boundary": "DOTADOPT",
        "last_event_iso": "2026-05-22T00:00:00Z",
        "plans_in_flight": 0,
        "plans_executing": [],
        "last_plan_event_iso": "none",
        "install_status": "installed",
        "gitignore_init_status": "present",
        "operating_mode": "standalone",
    }


def _section_after(text, heading):
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def _bulleted_mapping(section):
    values = {}
    for line in section.splitlines():
        match = re.match(r"\s*(?:- )?([^:]+):\s*(.*)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _copy_bundle_inputs(repo: Path) -> None:
    for relative in (
        "docs/dotfiles-source-authority-contract.md",
        "docs/c4/phase-loop-runtime-c4-document.md",
        "docs/tasks/dotfiles-task-catalog.md",
    ):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    shutil.copytree(REPO_ROOT / "vendor" / "phase-loop-runtime" / "baml_src", repo / "vendor" / "phase-loop-runtime" / "baml_src")


class PhaseLoopAdoptionBundleTest(unittest.TestCase):
    def test_fixture_parses_as_dotfiles_adoption_manifest(self):
        fixture = _load_fixture()

        parsed = parse_baml_response("DotfilesAdoptionManifest", json.dumps(fixture))

        self.assertEqual(parsed.payload, fixture)
        self.assertEqual(fixture["operating_mode"], "standalone")
        self.assertIsInstance(fixture["plan_refs"], list)
        self.assertEqual(fixture["redacted_metadata_ref"], RUNTIME_PROJECTION_REF)
        self.assertEqual(fixture["visibility_contract_ref"], "docs/dotfiles-visibility-contract.md")

    def test_fixture_matches_deterministic_generator_output(self):
        fixture = _load_fixture()
        generated = generate_adoption_bundle(
            REPO_ROOT,
            generated_at=fixture["generated_at"],
            operating_mode=fixture["operating_mode"],
        )

        self.assertEqual(stable_json_bytes(generated), FIXTURE_PATH.read_bytes())

    def test_repeated_generation_with_explicit_timestamp_is_byte_identical(self):
        left = generate_adoption_bundle(REPO_ROOT, generated_at="2026-05-22T00:00:00Z")
        right = generate_adoption_bundle(REPO_ROOT, generated_at="2026-05-22T00:00:00Z")

        self.assertEqual(stable_json_bytes(left), stable_json_bytes(right))

    def test_schema_digests_match_current_baml_bytes(self):
        fixture = _load_fixture()
        schema_paths = {path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "vendor" / "phase-loop-runtime" / "baml_src").glob("*.baml")}

        self.assertEqual({ref["source_path"] for ref in fixture["schema_refs"]}, schema_paths)
        for ref in fixture["schema_refs"]:
            path = _resolve_repo_path(ref["source_path"])
            expected = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            self.assertEqual(ref["digest"], expected)
            self.assertRegex(ref["digest"], r"^sha256:[0-9a-f]{64}$")

    def test_referenced_paths_are_repo_relative_and_safe(self):
        fixture = _load_fixture()

        for root in fixture["source_roots"]:
            value = root["path_glob"]
            self.assertFalse(value.startswith(("/", "~")))
            self.assertEqual(root["classification"], "authority")
            self.assertTrue(_glob_has_match(value), value)
            self.assertTrue(all(forbidden not in json.dumps(root) for forbidden in FORBIDDEN_REF_PARTS))

        concrete_refs = [fixture["visibility_contract_ref"]]
        concrete_refs.extend(ref["source_path"] for ref in fixture["schema_refs"])
        concrete_refs.extend(ref["file"] for ref in fixture["plan_refs"])
        concrete_refs.extend(ref["source_path"] for ref in fixture["c4_document_refs"])
        concrete_refs.extend(ref["source_path"] for ref in fixture["task_catalog_refs"])
        for relative_path in concrete_refs:
            with self.subTest(relative_path=relative_path):
                self.assertFalse(relative_path.startswith(("/", "~")))
                self.assertNotIn("..", Path(relative_path).parts)
                self.assertTrue(_resolve_repo_path(relative_path).exists())
                self.assertTrue(all(forbidden not in relative_path for forbidden in FORBIDDEN_REF_PARTS))

        for ref in fixture["plan_refs"]:
            self.assertRegex(ref["digest"], r"^sha256:[0-9a-f]{64}$")
            self.assertIn(ref["type"], {"phase", "detailed"})
            self.assertTrue(all(forbidden not in json.dumps(ref) for forbidden in FORBIDDEN_REF_PARTS))

    def test_manifest_plan_refs_are_repo_relative_sorted_and_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            _copy_bundle_inputs(repo)
            (repo / "plans").mkdir(parents=True)
            alpha = repo / "plans" / "phase-plan-v38-ALPHA.md"
            beta = repo / "plans" / "detailed-beta.md"
            alpha.write_text("# ALPHA\n", encoding="utf-8")
            beta.write_text("# Beta\n", encoding="utf-8")
            (repo / "plans" / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "plans": [
                            {
                                "slug": "z-missing",
                                "file": "plans/missing.md",
                                "type": "phase",
                                "status": "committed",
                                "created_at": "2026-05-30T00:00:00Z",
                                "updated_at": "2026-05-30T00:00:00Z",
                                "owner_skill": "codex-plan-phase",
                                "lifecycle": [],
                            },
                            {
                                "slug": "v38-alpha",
                                "file": "plans/phase-plan-v38-ALPHA.md",
                                "type": "phase",
                                "status": "executing",
                                "created_at": "2026-05-30T00:00:00Z",
                                "updated_at": "2026-05-30T00:00:00Z",
                                "owner_skill": "codex-plan-phase",
                                "lifecycle": [],
                            },
                            {
                                "slug": "detailed-beta",
                                "file": "plans/detailed-beta.md",
                                "type": "detailed",
                                "status": "committed",
                                "created_at": "2026-05-30T00:00:00Z",
                                "updated_at": "2026-05-30T00:00:00Z",
                                "owner_skill": "codex-plan-detailed",
                                "lifecycle": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            bundle = generate_adoption_bundle(repo, generated_at="2026-05-30T00:00:00Z")

            self.assertEqual([ref["slug"] for ref in bundle["plan_refs"]], ["detailed-beta", "v38-alpha"])
            self.assertEqual(bundle["plan_refs"][0]["digest"], f"sha256:{hashlib.sha256(beta.read_bytes()).hexdigest()}")
            self.assertEqual(bundle["plan_refs"][1]["digest"], f"sha256:{hashlib.sha256(alpha.read_bytes()).hexdigest()}")
            parse_baml_response("DotfilesAdoptionManifest", json.dumps(bundle))

    def test_simulated_governed_pipeline_adoption_flow(self):
        fixture = _load_fixture()

        for ref in fixture["schema_refs"]:
            self.assertEqual(ref["digest"], f"sha256:{hashlib.sha256(_resolve_repo_path(ref['source_path']).read_bytes()).hexdigest()}")
        c4_fixture = _build_c4_fixture()
        task_fixture = _build_task_catalog_fixture()

        parse_baml_response("DotfilesC4Document", json.dumps(c4_fixture))
        parse_baml_response("DotfilesTaskCatalog", json.dumps(task_fixture))
        parse_baml_response("DotfilesRuntimeProjection", json.dumps(_runtime_projection_fixture()))

        for key, prefix in (
            ("mermaid_context_source", "C4Context"),
            ("mermaid_container_source", "C4Container"),
            ("mermaid_component_source", "C4Component"),
        ):
            self.assertTrue(c4_fixture[key].startswith(prefix))

    def test_docs_and_protocol_name_the_adoption_contract(self):
        text = DOC_PATH.read_text(encoding="utf-8") + "\n" + PROTOCOL_PATH.read_text(encoding="utf-8")
        for term in (
            "docs/adoption/dotfiles-adoption-bundle.json",
            "DotfilesAdoptionManifest",
            "generate_adoption_bundle",
            "governed-pipeline",
            "pull-only",
            "sha256",
            "no HTML",
            "Portal",
        ):
            self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
