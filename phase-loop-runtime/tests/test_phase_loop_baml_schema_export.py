import json
import unittest
from unittest.mock import patch

from phase_loop_runtime.baml_modular import BamlValidationError, export_function_schema, inject_schema_description


UNSUPPORTED_SCHEMA_KEYS = {"allOf", "anyOf", "oneOf", "not", "if", "then"}


def _walk_schema(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_schema(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_schema(child)


class PhaseLoopBamlSchemaExportTest(unittest.TestCase):
    def test_emit_phase_closeout_schema_exports_codex_compatible_object_schema(self):
        schema = export_function_schema("EmitPhaseCloseout")

        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
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
            self.assertIn(field, schema["properties"])
        self.assertIn("complete", schema["properties"]["terminal_status"]["enum"])
        self.assertIn("blocked", schema["properties"]["verification_status"]["enum"])
        self.assertIn("dirty_worktree_conflict", schema["properties"]["blocker_class"]["enum"])
        self.assertIn(None, schema["properties"]["blocker_class"]["enum"])
        for node in _walk_schema(schema):
            self.assertTrue(UNSUPPORTED_SCHEMA_KEYS.isdisjoint(node))

    def test_schema_export_is_deterministic(self):
        left = json.dumps(export_function_schema("EmitPhaseCloseout"), sort_keys=True)
        right = json.dumps(export_function_schema("EmitPhaseCloseout"), sort_keys=True)
        self.assertEqual(left, right)

    def test_dotfiles_class_name_schema_exports_nested_object_arrays(self):
        schema = export_function_schema("DotfilesTaskCatalog")

        self.assertEqual(schema["title"], "DotfilesTaskCatalog")
        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), {"tasks", "audiences", "references"})
        task_items = schema["properties"]["tasks"]["items"]
        self.assertEqual(task_items["type"], "object")
        self.assertFalse(task_items["additionalProperties"])
        self.assertEqual(
            set(task_items["required"]),
            {"id", "title", "audience", "owner", "dependencies", "status"},
        )
        self.assertEqual(task_items["properties"]["dependencies"]["items"]["type"], "string")
        for node in _walk_schema(schema):
            self.assertTrue(UNSUPPORTED_SCHEMA_KEYS.isdisjoint(node))

    def test_emit_phase_closeout_schema_remains_unchanged_after_class_exports(self):
        before = export_function_schema("EmitPhaseCloseout")
        export_function_schema("DotfilesAdoptionManifest")
        after = export_function_schema("EmitPhaseCloseout")

        self.assertEqual(before, after)

    def test_unknown_or_unavailable_baml_export_raises_validation_error(self):
        with self.assertRaises(BamlValidationError):
            export_function_schema("UnknownFunction")
        with patch("phase_loop_runtime.baml_modular._read_baml_files", return_value={"broken.baml": "class X {\n  value int\n}\n"}):
            with self.assertRaises(BamlValidationError):
                export_function_schema("EmitPhaseCloseout")

    def test_schema_description_is_deterministic_and_schema_derived(self):
        schema = export_function_schema("EmitPhaseCloseout")
        first = inject_schema_description("Original prompt", schema)
        second = inject_schema_description("Original prompt", schema)

        self.assertEqual(first, second)
        self.assertIn("Phase-loop closeout JSON schema description:", first)
        self.assertIn("schema_sha256:", first)
        self.assertIn("- produced_if_gates: type=\"array\"", first)
        self.assertTrue(first.endswith("Original prompt"))


if __name__ == "__main__":
    unittest.main()
