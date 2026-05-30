import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BAML_SOURCE = ROOT / "vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml"
BAML_SRC_DIR = ROOT / "vendor/phase-loop-runtime/baml_src"


class PhaseLoopBamlSchemaSourceTest(unittest.TestCase):
    def test_emit_phase_closeout_contract_is_declared(self):
        text = BAML_SOURCE.read_text(encoding="utf-8")
        self.assertIn("function EmitPhaseCloseout(", text)
        for arg in ("phase_alias: string", "plan_produces: string[]", "plan_owned_files: string[]", "closeout_commit_sha: string?"):
            self.assertIn(arg, text)
        self.assertIn("-> PhaseLoopCloseoutV1", text)

    def test_closeout_fields_and_literals_are_present(self):
        text = BAML_SOURCE.read_text(encoding="utf-8")
        for field in (
            "terminal_status",
            "verification_status",
            "dirty_paths",
            "produced_if_gates",
            "next_action",
            "blocker_class",
            "blocker_summary",
            "human_required",
            "required_human_inputs",
        ):
            self.assertIn(field, text)
        for literal in ("complete", "blocked", "not_run", "passed", "contract_bug", "dirty_worktree_conflict"):
            self.assertIn(literal, text)
        self.assertIn("Completed closeouts must include every IF gate", text)
        self.assertIn("must not leave produced_if_gates empty", text)

    def test_dotfiles_schema_sources_are_declared(self):
        expected = {
            "dotfiles_adoption_manifest.baml": (
                "class DotfilesAdoptionManifest",
                "source_roots",
                "schema_refs",
                "plan_refs",
                "c4_document_refs",
                "task_catalog_refs",
                "operating_mode",
                "redacted_metadata_ref",
                "visibility_contract_ref",
                "version",
                "generated_at",
            ),
            "dotfiles_runtime_projection.baml": (
                "class DotfilesRuntimeProjection",
                "runtime_version",
                "protocol_version",
                "harness",
                "source_bundle_digest",
                "closeout_status",
                "handoff_status",
                "current_phase_boundary",
                "last_event_iso",
                "plans_in_flight",
                "plans_executing",
                "last_plan_event_iso",
                "install_status",
                "gitignore_init_status",
                "operating_mode",
            ),
            "dotfiles_plan_manifest.baml": (
                "class DotfilesPlanManifest",
                "class DotfilesPlanRef",
                "digest string?",
                "plans",
                "roadmap_ref",
                "lifecycle",
            ),
            "dotfiles_c4_document.baml": (
                "class DotfilesC4Document",
                "title",
                "mermaid_context_source",
                "mermaid_container_source",
                "mermaid_component_source",
                "anchors",
                "description",
            ),
            "dotfiles_task_catalog.baml": (
                "class DotfilesTaskCatalog",
                "class DotfilesTask",
                "tasks",
                "audiences",
                "references",
                "id",
                "title",
                "audience",
                "owner",
                "dependencies",
                "status",
            ),
        }
        for file_name, fragments in expected.items():
            text = (BAML_SRC_DIR / file_name).read_text(encoding="utf-8")
            for fragment in fragments:
                self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
