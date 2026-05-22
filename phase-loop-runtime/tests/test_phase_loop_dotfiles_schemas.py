import json
import unittest

from phase_loop_runtime.baml_modular import BamlValidationError, export_function_schema, parse_baml_response


FORBIDDEN_SCHEMA_KEYS = {"allOf", "anyOf", "oneOf", "not", "if", "then", "uniqueItems"}


FIXTURES = {
    "DotfilesAdoptionManifest": {
        "source_roots": [
            {
                "path_glob": "docs/**",
                "classification": "authority",
                "owner": "dotfiles",
                "ingestion_policy": "pull",
            }
        ],
        "schema_refs": [
            {
                "class_name": "DotfilesAdoptionManifest",
                "source_path": "vendor/phase-loop-runtime/baml_src/dotfiles_adoption_manifest.baml",
                "digest": "sha256:example",
            }
        ],
        "c4_document_refs": [
            {
                "title": "phase-loop context",
                "source_path": "docs/phase-loop/context.md",
                "anchor": "phase-loop-context",
            }
        ],
        "task_catalog_refs": [
            {
                "catalog_id": "sourcepack",
                "source_path": "docs/phase-loop/tasks.md",
                "audience": "operator",
            }
        ],
        "operating_mode": "standalone",
        "redacted_metadata_ref": "docs/dotfiles-visibility-contract.md",
        "visibility_contract_ref": "docs/dotfiles-visibility-contract.md",
        "version": "v1",
        "generated_at": "2026-05-22T00:00:00Z",
    },
    "DotfilesRuntimeProjection": {
        "runtime_version": "0.1.0",
        "protocol_version": "phase-loop-protocol-v1",
        "harness": "codex",
        "source_bundle_digest": "sha256:example",
        "closeout_status": "complete",
        "handoff_status": "written",
        "current_phase_boundary": "DOTSCHEMAS",
        "last_event_iso": "2026-05-22T00:00:00Z",
        "install_status": "installed",
        "gitignore_init_status": "present",
        "operating_mode": "standalone",
    },
    "DotfilesC4Document": {
        "title": "Dotfiles phase-loop substrate",
        "mermaid_context_source": "C4Context\n  title Dotfiles",
        "mermaid_container_source": "C4Container\n  title Phase Loop",
        "mermaid_component_source": "C4Component\n  title BAML Schemas",
        "anchors": [
            {
                "id": "dotfiles-phase-loop",
                "title": "Dotfiles phase-loop substrate",
                "source_path": "docs/phase-loop/context.md",
            }
        ],
        "description": "Source Mermaid for deterministic downstream rendering.",
    },
    "DotfilesTaskCatalog": {
        "tasks": [
            {
                "id": "DOTSCHEMAS",
                "title": "Freeze dotfiles BAML schema pack",
                "audience": "operator",
                "owner": "dotfiles",
                "dependencies": ["DOTCONTRACT"],
                "status": "complete",
            }
        ],
        "audiences": ["operator", "planner"],
        "references": [
            {
                "title": "Protocol",
                "source_path": "vendor/phase-loop-runtime/protocol/protocol.md",
                "anchor": "dotfiles-schema-pack",
            }
        ],
    },
}


EXPECTED_FIELDS = {
    "DotfilesAdoptionManifest": {
        "source_roots",
        "schema_refs",
        "c4_document_refs",
        "task_catalog_refs",
        "operating_mode",
        "redacted_metadata_ref",
        "visibility_contract_ref",
        "version",
        "generated_at",
    },
    "DotfilesRuntimeProjection": {
        "runtime_version",
        "protocol_version",
        "harness",
        "source_bundle_digest",
        "closeout_status",
        "handoff_status",
        "current_phase_boundary",
        "last_event_iso",
        "install_status",
        "gitignore_init_status",
        "operating_mode",
    },
    "DotfilesC4Document": {
        "title",
        "mermaid_context_source",
        "mermaid_container_source",
        "mermaid_component_source",
        "anchors",
        "description",
    },
    "DotfilesTaskCatalog": {"tasks", "audiences", "references"},
}


def _walk_schema(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_schema(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_schema(child)


class PhaseLoopDotfilesSchemasTest(unittest.TestCase):
    def test_all_dotfiles_schemas_export_and_parse_representative_fixtures(self):
        for class_name, fixture in FIXTURES.items():
            with self.subTest(class_name=class_name):
                schema = export_function_schema(class_name)

                self.assertEqual(schema["title"], class_name)
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(schema["required"]), EXPECTED_FIELDS[class_name])
                self.assertEqual(set(schema["required"]), set(schema["properties"]))
                for node in _walk_schema(schema):
                    self.assertTrue(FORBIDDEN_SCHEMA_KEYS.isdisjoint(node))

                parsed = parse_baml_response(class_name, json.dumps(fixture))
                self.assertEqual(parsed.payload, fixture)

    def test_dotfiles_schema_parse_rejects_undeclared_fields(self):
        fixture = dict(FIXTURES["DotfilesRuntimeProjection"])
        fixture["raw_private_path"] = "/home/example/.ssh/id_rsa"

        with self.assertRaises(BamlValidationError):
            parse_baml_response("DotfilesRuntimeProjection", json.dumps(fixture))

    def test_task_catalog_tasks_export_as_structured_entries(self):
        schema = export_function_schema("DotfilesTaskCatalog")
        task_schema = schema["properties"]["tasks"]["items"]

        self.assertEqual(task_schema["type"], "object")
        self.assertEqual(
            set(task_schema["required"]),
            {"id", "title", "audience", "owner", "dependencies", "status"},
        )
        self.assertEqual(task_schema["properties"]["dependencies"]["type"], "array")
        self.assertEqual(task_schema["properties"]["dependencies"]["items"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
