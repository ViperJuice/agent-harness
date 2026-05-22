from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.adoption_bundle import generate_adoption_bundle, stable_json_bytes
from phase_loop_runtime.baml_modular import export_function_schema, parse_baml_response
from phase_loop_runtime.cli import main


REPO_ROOT = Path(__file__).resolve().parents[3]
BAML_SRC = REPO_ROOT / "vendor" / "phase-loop-runtime" / "baml_src"
C4_DIR = REPO_ROOT / "docs" / "c4"
ROADMAP = REPO_ROOT / "specs" / "phase-plans-v22.md"
FORBIDDEN_SCHEMA_KEYS = {"allOf", "anyOf", "oneOf", "not", "if", "then", "uniqueItems"}
FORBIDDEN_RUNTIME_TOKENS = ("/home/", "/Users/", "/mnt/", "op://", "sk-", "AKIA", "ghp_")


def _standalone_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PIPELINE_MODE", "PHASE_LOOP_PIPELINE_MODE", "GOVERNED_PIPELINE_BASE_URL"}
        and not key.startswith("GOVERNED_PIPELINE_")
    }
    return env


def _walk_schema(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_schema(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_schema(child)


def _baml_class_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("class "):
            names.append(stripped.split()[1])
    return names


class PhaseLoopV22PrinciplesTest(unittest.TestCase):
    def test_standalone_status_does_not_require_governed_pipeline_configuration(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, _standalone_env(), clear=True), redirect_stdout(stdout):
            rc = main(
                [
                    "status",
                    "--repo",
                    str(REPO_ROOT),
                    "--roadmap",
                    str(ROADMAP),
                    "--pipeline-mode",
                    "standalone",
                    "--json",
                ]
            )

        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["pipeline_mode"], "standalone")

    def test_no_html_or_rendered_mermaid_assets_are_source_outputs(self):
        html_outputs = [
            path.relative_to(REPO_ROOT).as_posix()
            for root in ("docs", "specs")
            for path in (REPO_ROOT / root).rglob("*.html")
            if "node_modules" not in path.parts
        ]
        rendered_c4_outputs = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in C4_DIR.rglob("*")
            if path.suffix.lower() in {".html", ".png", ".svg", ".pdf"}
        ]

        self.assertEqual(html_outputs, [])
        self.assertEqual(rendered_c4_outputs, [])
        for path in sorted(C4_DIR.glob("phase-loop-runtime-*.mmd")):
            source = path.read_text(encoding="utf-8")
            self.assertTrue(source.startswith(("C4Context", "C4Container", "C4Component")))

    def test_dotfiles_baml_schemas_are_openai_dialect_clean(self):
        class_names = []
        for path in sorted(BAML_SRC.glob("*.baml")):
            class_names.extend(_baml_class_names(path))

        self.assertTrue(class_names)
        for class_name in class_names:
            with self.subTest(class_name=class_name):
                schema = export_function_schema(class_name)
                for node in _walk_schema(schema):
                    self.assertTrue(FORBIDDEN_SCHEMA_KEYS.isdisjoint(node))

    def test_runtime_projection_output_is_schema_valid_and_redacted(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, _standalone_env(), clear=True), redirect_stdout(stdout):
            rc = main(
                [
                    "status",
                    "--repo",
                    str(REPO_ROOT),
                    "--roadmap",
                    str(ROADMAP),
                    "--pipeline-mode",
                    "standalone",
                    "--runtime-projection",
                    "--json",
                ]
            )

        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        parse_baml_response("DotfilesRuntimeProjection", json.dumps(payload))
        serialized = json.dumps(payload, sort_keys=True)
        for token in FORBIDDEN_RUNTIME_TOKENS:
            self.assertNotIn(token, serialized)

    def test_adoption_bundle_generation_is_byte_deterministic(self):
        left = generate_adoption_bundle(REPO_ROOT, generated_at="2026-05-22T00:00:00Z")
        right = generate_adoption_bundle(REPO_ROOT, generated_at="2026-05-22T00:00:00Z")

        self.assertEqual(stable_json_bytes(left), stable_json_bytes(right))


if __name__ == "__main__":
    unittest.main()
