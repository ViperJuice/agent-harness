import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.baml_modular import BamlValidationError, export_function_schema, parse_baml_response
from phase_loop_runtime.plan_manifest import (
    DotfilesPlanEntry,
    DotfilesPlanLifecycleEvent,
    DotfilesPlanRef,
    append_entry,
    import_existing_phase_plans,
    read_manifest,
    update_lifecycle,
    validate_manifest,
)


import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/import_existing_plans_to_manifest.py"


class PhaseLoopPlanManifestTest(unittest.TestCase):
    def test_baml_schema_exports_and_rejects_invalid_type(self):
        schema = export_function_schema("DotfilesPlanManifest")
        self.assertEqual(schema["title"], "DotfilesPlanManifest")
        self.assertIn("plans", schema["properties"])

        fixture = {
            "schema_version": 1,
            "plans": [
                {
                    "slug": "v38-MS",
                    "file": "plans/phase-plan-v38-MS.md",
                    "type": "phase",
                    "status": "committed",
                    "created_at": "2026-05-30T00:00:00Z",
                    "updated_at": "2026-05-30T00:00:00Z",
                    "owner_skill": "codex-plan-phase",
                    "handoff_ref": None,
                    "reflection_ref": None,
                    "task_summary": None,
                    "acceptance_criteria_count": None,
                    "roadmap_ref": {
                        "slug": "phase-plans-v38",
                        "file": "specs/phase-plans-v38.md",
                        "type": "phase",
                        "status": "committed",
                    },
                    "phase_alias": "MS",
                    "if_gates_produced": ["IF-0-MS-1"],
                    "lanes": ["SL-0"],
                    "lifecycle": [
                        {
                            "transition": "committed",
                            "by": "codex-plan-phase",
                            "at": "2026-05-30T00:00:00Z",
                            "metadata": {"entries": [{"key": "roadmap", "value": "v38"}]},
                        }
                    ],
                }
            ],
        }
        self.assertEqual(parse_baml_response("DotfilesPlanManifest", json.dumps(fixture)).payload, fixture)

        invalid = dict(fixture)
        invalid["plans"] = [dict(fixture["plans"][0], type="roadmap")]
        with self.assertRaises(BamlValidationError):
            parse_baml_response("DotfilesPlanManifest", json.dumps(invalid))

        ref_schema = export_function_schema("DotfilesPlanRef")
        self.assertEqual(set(ref_schema["required"]), {"slug", "file", "type", "status"})
        self.assertIn("digest", ref_schema["properties"])

    def test_append_entry_is_file_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plans").mkdir()
            (repo / "plans/example.md").write_text("# Example\n", encoding="utf-8")
            entry = _detailed_entry("plans/example.md")

            append_entry(repo, entry)
            first = (repo / "plans/manifest.json").read_bytes()
            append_entry(repo, entry)
            second = (repo / "plans/manifest.json").read_bytes()

            self.assertEqual(first, second)
            self.assertEqual(read_manifest(repo).plans[0].slug, "detailed-example")

    def test_lifecycle_transitions_and_invalid_transition_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plans").mkdir()
            (repo / "plans/example.md").write_text("# Example\n", encoding="utf-8")
            append_entry(repo, _detailed_entry("plans/example.md"))

            update_lifecycle(repo, "detailed-example", "executing", "codex-execute-detailed", {"run_id": "1"})
            update_lifecycle(repo, "detailed-example", "completed", "codex-execute-detailed", {})
            entry = read_manifest(repo).plans[0]
            self.assertEqual(entry.status, "completed")
            self.assertEqual([event.transition for event in entry.lifecycle], ["committed", "executing", "completed"])

            with self.assertRaises(ValueError):
                update_lifecycle(repo, "detailed-example", "executing", "codex-execute-detailed", {})

        for terminal in ("failed", "orphaned"):
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                (repo / "plans").mkdir()
                (repo / "plans/example.md").write_text("# Example\n", encoding="utf-8")
                append_entry(repo, _detailed_entry("plans/example.md"))
                update_lifecycle(repo, "detailed-example", "executing", "codex-execute-detailed", {})
                update_lifecycle(repo, "detailed-example", terminal, "codex-execute-detailed", {})
                self.assertEqual(read_manifest(repo).plans[0].status, terminal)

    def test_validate_manifest_reports_malformed_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            manifest = repo / "plans/manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "plans": [
                            {
                                "slug": "dup",
                                "file": "plans/missing.md",
                                "type": "roadmap",
                                "status": "committed",
                                "created_at": "2026-05-30T00:00:00Z",
                                "updated_at": "2026-05-30T00:00:00Z",
                                "owner_skill": "codex-plan-phase",
                                "lifecycle": [{"transition": "committed", "by": "codex", "at": "now", "metadata": []}],
                            },
                            {
                                "slug": "dup",
                                "file": "plans/missing.md",
                                "type": "detailed",
                                "status": "committed",
                                "created_at": "2026-05-30T00:00:00Z",
                                "updated_at": "2026-05-30T00:00:00Z",
                                "owner_skill": "codex-plan-detailed",
                                "phase_alias": "BAD",
                                "lifecycle": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = validate_manifest(manifest)
            self.assertFalse(result.valid)
            combined = "\n".join(result.errors)
            self.assertIn("duplicates dup", combined)
            self.assertIn("type must be phase or detailed", combined)
            self.assertIn("file does not exist", combined)
            self.assertIn("metadata must be an object", combined)
            self.assertIn("task_summary is required", combined)
            self.assertIn("mixes phase-only fields", combined)

    def test_import_existing_phase_plans_and_script_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "plans").mkdir()
            (repo / "specs").mkdir()
            (repo / "specs/phase-plans-v38.md").write_text("# Roadmap\n", encoding="utf-8")
            (repo / "plans/phase-plan-v38-MS.md").write_text(
                """---
phase_loop_plan_version: 1
phase: MS
roadmap: specs/phase-plans-v38.md
---

# MS

## Interface Freeze Gates
- [ ] IF-0-MS-1

### SL-0 - Schema
""",
                encoding="utf-8",
            )

            imported = import_existing_phase_plans(repo)
            self.assertEqual(len(imported.plans), 1)
            self.assertEqual(imported.plans[0].status, "imported")
            self.assertEqual(imported.plans[0].phase_alias, "MS")
            self.assertEqual(imported.plans[0].if_gates_produced, ("IF-0-MS-1",))

            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT / "vendor/phase-loop-runtime/src")
            subprocess.run([sys.executable, str(SCRIPT), str(repo)], check=True, env=env, capture_output=True, text=True)
            first = (repo / "plans/manifest.json").read_bytes()
            subprocess.run([sys.executable, str(SCRIPT), str(repo)], check=True, env=env, capture_output=True, text=True)
            second = (repo / "plans/manifest.json").read_bytes()
            self.assertEqual(first, second)
            self.assertTrue(validate_manifest(repo / "plans/manifest.json").valid)


def _detailed_entry(file: str) -> DotfilesPlanEntry:
    return DotfilesPlanEntry(
        slug="detailed-example",
        file=file,
        type="detailed",
        status="committed",
        created_at="2026-05-30T00:00:00Z",
        updated_at="2026-05-30T00:00:00Z",
        owner_skill="codex-plan-detailed",
        task_summary="Example bounded change",
        acceptance_criteria_count=1,
        lifecycle=(DotfilesPlanLifecycleEvent("committed", "codex-plan-detailed", "2026-05-30T00:00:00Z", {}),),
    )


if __name__ == "__main__":
    unittest.main()
