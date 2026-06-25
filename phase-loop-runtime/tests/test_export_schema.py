"""Tests for the generic `phase-loop export-schema` command (SCHEMA SL-1).

These assert that the exported JSON-Schema is genuinely DERIVED from the canonical
`PhaseLoopCloseout` dataclass (recursing into the nested sub-dataclasses, with
correct scalar types and nullable unions), that the committed artifact cannot drift
from the dataclass, that the runtime's closeout validator consumes the same
required-field set, and that `--check` enforces parity against the live dataclass.

CLI-INDEPENDENT: this module imports only `phase_loop_runtime` library code and
exercises the CLI via `main([...])` / a console-script subprocess. It registers
no dotfiles profile and is wired into the cli-independent-unit-guard.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from phase_loop_runtime import schema_export
from phase_loop_runtime.cli import main
from phase_loop_runtime.models import (
    PIPELINE_CLOSEOUT_SCHEMA,
    PhaseLoopCloseout,
)


DECLARED_FIELDS = tuple(f.name for f in dataclasses.fields(PhaseLoopCloseout))
# Required = the validator's mandatory nested fields + the `schema` literal.
# `spec_delta_closeout` is the only genuinely optional top-level field.
EXPECTED_REQUIRED = sorted({*schema_export.CLOSEOUT_REQUIRED_NESTED_FIELDS, "schema"})


def _emit(tmp_path: Path, fmt: str = "json-schema") -> Path:
    out = tmp_path / f"emitted.{fmt}.json"
    rc = main(["export-schema", "--output", str(out), "--format", fmt])
    assert rc == 0
    assert out.is_file()
    return out


def test_properties_cover_all_declared_fields(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    assert set(schema["properties"]) == set(DECLARED_FIELDS)


def test_required_matches_validator_set_plus_schema(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    assert schema["required"] == EXPECTED_REQUIRED
    # spec_delta_closeout is optional; source_truth_impact stays required (the
    # builder always populates it and the validator requires it non-null).
    assert "spec_delta_closeout" not in schema["required"]
    assert "source_truth_impact" in schema["required"]


def test_nullable_field_uses_draft_2020_union_type(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    # draft-2020-12 union typing, NOT OpenAPI "nullable": true.
    spec_delta = schema["properties"]["spec_delta_closeout"]
    assert spec_delta["type"] == ["object", "null"]
    assert "nullable" not in spec_delta
    # A nullable scalar inside a nested dataclass is also a union type.
    automation = schema["properties"]["automation"]
    assert automation["properties"]["next_skill"]["type"] == ["string", "null"]


def test_schema_is_genuinely_recursive(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    automation = schema["properties"]["automation"]
    # The nested dataclass is expanded, not a bare {"type": "object"}.
    assert automation["type"] == "object"
    assert set(automation["properties"]) >= {"status", "human_required", "next_skill"}
    # Scalar types derived from annotations.
    assert automation["properties"]["status"]["type"] == "string"
    assert automation["properties"]["human_required"]["type"] == "boolean"
    # tuple[str, ...] -> typed array.
    verification = schema["properties"]["verification"]
    assert verification["properties"]["commands"] == {"type": "array", "items": {"type": "string"}}
    # The nested required set excludes defaulted/nullable nested fields.
    assert "next_skill" not in automation["required"]
    assert "status" in automation["required"]


def test_terminal_status_and_schema_consts(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    assert schema["properties"]["schema"]["const"] == PIPELINE_CLOSEOUT_SCHEMA
    assert schema["properties"]["terminal_status"]["enum"] == sorted(
        schema["properties"]["terminal_status"]["enum"]
    )
    assert "complete" in schema["properties"]["terminal_status"]["enum"]


def test_field_list_format(tmp_path: Path) -> None:
    payload = json.loads(_emit(tmp_path, fmt="field-list").read_text())
    assert payload["fields"] == sorted(DECLARED_FIELDS)
    assert payload["required"] == EXPECTED_REQUIRED
    assert payload["schema_id"] == PIPELINE_CLOSEOUT_SCHEMA


def test_canonical_schema_resolves_via_importlib_resources() -> None:
    schema = schema_export.canonical_schema()
    assert isinstance(schema, dict)
    assert schema["required"] == EXPECTED_REQUIRED


def test_committed_artifact_matches_live_dataclass() -> None:
    # THE drift guard: the committed/bundled artifact must equal a schema built
    # FRESH from the live dataclass. A dataclass change without regeneration fails
    # here (and --check). Run inside the wheel install this also proves bundling.
    assert schema_export.canonical_schema() == schema_export.build_schema()


def test_closeout_validator_consumes_single_source() -> None:
    # The malformed-closeout validator must reference the schema_export helper, not
    # a duplicated hand-maintained tuple (CR #4).
    from phase_loop_runtime import closeout

    payload = {"schema": PIPELINE_CLOSEOUT_SCHEMA, "terminal_status": "complete"}
    diag = closeout.phase_loop_closeout_diagnostic(payload)
    assert diag is not None
    # The first missing required field reported is the first in the single-source
    # ordering that is absent.
    first_missing = next(
        f for f in schema_export.required_closeout_fields() if f not in payload
    )
    assert first_missing in diag["message"]


def test_check_passes_on_emitted_artifact(tmp_path: Path) -> None:
    out = _emit(tmp_path)
    assert main(["export-schema", "--check", str(out)]) == 0


def test_check_fails_on_required_mutation(tmp_path: Path) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    schema["required"].append("bogus_extra_field")
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(schema))
    rc = main(["export-schema", "--check", str(mutated)])
    assert rc != 0


def test_check_fails_on_nested_mutation(tmp_path: Path) -> None:
    # A deep change (a nested scalar type) the top-level diffs would not enumerate
    # must still fail --check via the recursive comparison.
    schema = json.loads(_emit(tmp_path).read_text())
    schema["properties"]["automation"]["properties"]["status"]["type"] = "integer"
    mutated = tmp_path / "nested.json"
    mutated.write_text(json.dumps(schema))
    rc = main(["export-schema", "--check", str(mutated)])
    assert rc != 0


def test_check_diff_names_the_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema = json.loads(_emit(tmp_path).read_text())
    schema["properties"]["automation"]["properties"]["status"]["type"] = "integer"
    mutated = tmp_path / "named.json"
    mutated.write_text(json.dumps(schema))
    assert main(["export-schema", "--check", str(mutated)]) != 0
    err = capsys.readouterr().err
    assert "automation" in err and "status" in err


def test_emit_is_deterministic(tmp_path: Path) -> None:
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    assert main(["export-schema", "--output", str(out_a)]) == 0
    assert main(["export-schema", "--output", str(out_b)]) == 0
    assert out_a.read_bytes() == out_b.read_bytes()


def test_provenance_is_valid_json_and_static(tmp_path: Path) -> None:
    out = _emit(tmp_path)
    schema = json.loads(out.read_text())  # must parse: no // comments
    blob = out.read_text().lower()
    # No volatile content that would break byte-stability / metadata_only.
    assert "timestamp" not in blob
    assert "$comment" in schema or "title" in schema


def test_console_script_check_smoke() -> None:
    # The installed console script resolves the bundled artifact and self-checks.
    script = Path(sys.executable).with_name("phase-loop")
    if not script.exists():
        pytest.skip("phase-loop console script not on this interpreter")
    emit = subprocess.run(
        [str(script), "export-schema", "--output", "/tmp/_smoke_schema.json"],
        capture_output=True,
        text=True,
    )
    assert emit.returncode == 0, emit.stderr
    chk = subprocess.run(
        [str(script), "export-schema", "--check", "/tmp/_smoke_schema.json"],
        capture_output=True,
        text=True,
    )
    assert chk.returncode == 0, chk.stderr
