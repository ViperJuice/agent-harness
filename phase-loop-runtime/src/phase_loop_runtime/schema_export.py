"""Canonical phase-loop closeout schema export (SCHEMA SL-1).

The ONE canonical closeout shape is the nested :class:`PhaseLoopCloseout`
dataclass (``models.py``). This module *derives* a declared JSON-Schema and a flat
field-list FROM that dataclass — walking ``dataclasses.fields`` +
``typing.get_type_hints`` and RECURSING into the nested sub-dataclasses
(``PhaseLoopAutomation``/``Artifacts``/``Verification``/``Blocker``/``SourceBundle``/
``SourceTruthImpact``/``SpecDeltaCloseout``). Scalar types, nested required-sets,
nullable unions, and enums all come from the dataclass, so "single source of
truth" is real: a dataclass change that isn't regenerated fails ``--check`` and the
committed-artifact drift test.

The generated artifact is committed at ``schemas/phase_loop_closeout.schema.json``
and bundled as package-data; :func:`canonical_schema` resolves it via
``importlib.resources`` (the post-DECOUPLE pattern, mirroring ``baml_modular``)
so the bundled-artifact path works from a wheel-installed venv. :func:`check`
builds the canonical schema *fresh* from the live dataclass, so a stale committed
artifact cannot mask drift.

``required`` semantics: a field is optional iff its resolved annotation is a union
containing ``None`` AND it is not part of the closeout validator's mandatory set.
The runtime's malformed-closeout validator (``closeout.py``) is the ground truth for
the top-level required set; this module exposes that set as the single source the
validator consumes, killing the duplicate hand-maintained tuple.

Generation is deterministic (sorted keys, explicitly sorted lists, no volatile
content) so the byte-stable artifact is a meaningful parity baseline.
"""

from __future__ import annotations

import dataclasses
import importlib.resources
import json
import types
import typing
from pathlib import Path
from typing import Any

from .models import (
    PIPELINE_CLOSEOUT_OUTCOMES,
    PIPELINE_CLOSEOUT_SCHEMA,
    PhaseLoopCloseout,
)

#: Repo-relative location of the committed/bundled artifact (resolved via
#: importlib.resources at runtime; this constant is for docs/diagnostics).
SCHEMA_RESOURCE = "schemas/phase_loop_closeout.schema.json"

#: Static provenance recorded INSIDE the JSON (never a // comment — the artifact
#: must remain valid JSON for json.load + --check). No timestamps/abs paths so it
#: stays byte-stable and metadata_only.
_PROVENANCE = (
    "GENERATED from phase_loop_runtime.models.PhaseLoopCloseout by "
    "`phase-loop export-schema`. Do not hand-edit; regenerate instead."
)

#: The top-level closeout fields the runtime's malformed-closeout validator
#: requires present-and-non-null (closeout.py:phase_loop_closeout_diagnostic).
#: `schema` is validated separately (it is a fixed literal), and
#: `spec_delta_closeout` is genuinely optional, so neither appears here. This is
#: the SINGLE SOURCE the validator consumes — do not maintain a second copy.
CLOSEOUT_REQUIRED_NESTED_FIELDS: tuple[str, ...] = (
    "phase",
    "terminal_status",
    "automation",
    "artifacts",
    "verification",
    "blocker",
    "source_bundle",
    "source_truth_impact",
)


def _strip_optional(annotation: Any) -> tuple[Any, bool]:
    """Return ``(inner_type, is_optional)`` for a possibly-``X | None`` annotation."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = tuple(a for a in typing.get_args(annotation) if a is not type(None))
        is_optional = len(args) != len(typing.get_args(annotation))
        inner = args[0] if len(args) == 1 else annotation
        return inner, is_optional
    return annotation, False


def _scalar_schema(tp: Any) -> dict[str, Any] | None:
    """Map a scalar Python type to its JSON-Schema type, else ``None``."""
    # bool is a subclass of int — check it first.
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    return None


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    """Derive a JSON-Schema fragment from a (non-optional) resolved annotation."""
    inner, _ = _strip_optional(annotation)
    origin = typing.get_origin(inner)
    scalar = _scalar_schema(inner)
    if scalar is not None:
        return scalar
    if dataclasses.is_dataclass(inner):
        return _dataclass_schema(inner)
    if origin in (tuple, list):
        args = typing.get_args(inner)
        # tuple[X, ...] / list[X]; freeform `dict[..., Any]` items collapse to object.
        item = args[0] if args else Any
        item_inner, _ = _strip_optional(item)
        if dataclasses.is_dataclass(item_inner):
            return {"type": "array", "items": _dataclass_schema(item_inner)}
        item_scalar = _scalar_schema(item_inner)
        if item_scalar is not None:
            return {"type": "array", "items": item_scalar}
        return {"type": "array", "items": {"type": "object"}}
    if origin is dict:
        return {"type": "object"}
    # Any / unrecognised -> permissive object (don't over-specify freeform shapes).
    return {"type": "object"}


def _dataclass_schema(cls: Any) -> dict[str, Any]:
    """Recursively build a JSON-Schema `object` for a dataclass.

    A nested field is treated as required iff its annotation is NOT a
    union-with-None — a conservative contract convention. (It is not a guarantee of
    presence in every emitted instance: a few fields with custom `to_json` `or None`
    emit logic, e.g. an empty `protected_sources` tuple, may be absent in practice.
    Nothing validates live instances against this artifact, so the convention is the
    declared shape, not an instance invariant.) Nullable fields get a
    `["...","null"]` union type and are excluded from `required`.
    """
    hints = typing.get_type_hints(cls)
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for fld in dataclasses.fields(cls):
        annotation = hints[fld.name]
        _, is_optional = _strip_optional(annotation)
        prop = _annotation_schema(annotation)
        if is_optional:
            base_type = prop.get("type", "object")
            if isinstance(base_type, str):
                prop = {**prop, "type": [base_type, "null"]}
        else:
            required.append(fld.name)
        properties[fld.name] = prop
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required),
        "properties": {name: properties[name] for name in sorted(properties)},
    }


def field_names() -> tuple[str, ...]:
    """The declared field set of :class:`PhaseLoopCloseout`, in declaration order."""
    return tuple(f.name for f in dataclasses.fields(PhaseLoopCloseout))


def required_closeout_fields() -> tuple[str, ...]:
    """Single source for the closeout validator's mandatory nested fields.

    Consumed by ``closeout.py``'s malformed-closeout diagnostic so the required
    set is not maintained in two places.
    """
    return CLOSEOUT_REQUIRED_NESTED_FIELDS


def _top_level_property(name: str, annotation: Any) -> dict[str, Any]:
    """Property entry for a top-level closeout field (with closeout-specific consts).

    A top-level field is null-allowed only when it is BOTH annotated nullable AND
    not in the validator's mandatory set. ``source_truth_impact`` is annotated
    ``... | None`` (the builder always populates it) but the validator requires it
    present-and-non-null, so it stays non-null here — matching what consumers
    actually enforce.
    """
    if name == "schema":
        return {"type": "string", "const": PIPELINE_CLOSEOUT_SCHEMA}
    if name == "terminal_status":
        return {"type": "string", "enum": sorted(PIPELINE_CLOSEOUT_OUTCOMES)}
    _, is_optional = _strip_optional(annotation)
    prop = _annotation_schema(annotation)
    null_allowed = is_optional and name not in CLOSEOUT_REQUIRED_NESTED_FIELDS
    if null_allowed:
        base_type = prop.get("type", "object")
        if isinstance(base_type, str):
            prop = {**prop, "type": [base_type, "null"]}
    return prop


def build_schema() -> dict[str, Any]:
    """Build the canonical JSON-Schema FROM the dataclass (single source of truth)."""
    hints = typing.get_type_hints(PhaseLoopCloseout)
    fields = dataclasses.fields(PhaseLoopCloseout)
    properties = {f.name: _top_level_property(f.name, hints[f.name]) for f in fields}
    # Required = the validator's mandatory nested fields + the `schema` literal.
    # `spec_delta_closeout` is the only genuinely optional top-level field.
    required = sorted({*CLOSEOUT_REQUIRED_NESTED_FIELDS, "schema"})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": PIPELINE_CLOSEOUT_SCHEMA,
        "title": "phase_loop_closeout",
        "$comment": _PROVENANCE,
        "type": "object",
        "required": required,
        "additionalProperties": False,
        "properties": {name: properties[name] for name in sorted(properties)},
    }


def build_field_list() -> dict[str, Any]:
    """Build the flat field-list payload gp consumes (all declared fields)."""
    return {
        "schema_id": PIPELINE_CLOSEOUT_SCHEMA,
        "source": "phase_loop_runtime.models.PhaseLoopCloseout",
        "fields": sorted(field_names()),
        "required": sorted({*CLOSEOUT_REQUIRED_NESTED_FIELDS, "schema"}),
    }


def render(schema: dict[str, Any]) -> str:
    """Deterministic serialization: sorted keys, trailing newline, no volatile data."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _resource_path() -> Path:
    """Resolve the bundled artifact via importlib.resources (wheel-safe)."""
    from .runtime_resources import package_root

    root = package_root()
    if root is not None:
        packaged = root / SCHEMA_RESOURCE
        if packaged.is_file():
            return packaged
    # Editable/source-layout fallback (same dir importlib.resources resolves once
    # installed from a wheel).
    fallback = Path(__file__).resolve().parent / SCHEMA_RESOURCE
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        f"bundled closeout schema not found ({SCHEMA_RESOURCE}); regenerate via "
        "`phase-loop export-schema --output <artifact>`"
    )


def canonical_schema() -> dict[str, Any]:
    """Load the bundled canonical schema artifact (the in-package, wheel-resolved copy)."""
    return json.loads(_resource_path().read_text(encoding="utf-8"))


def _diff_json(canonical: Any, supplied: Any, path: str = "") -> list[str]:
    """Recursively diff two JSON values, naming the actual differing paths."""
    if isinstance(canonical, dict) and isinstance(supplied, dict):
        diffs: list[str] = []
        for key in sorted(set(canonical) | set(supplied)):
            here = f"{path}.{key}" if path else key
            if key not in supplied:
                diffs.append(f"{here}: missing (canonical={canonical[key]!r})")
            elif key not in canonical:
                diffs.append(f"{here}: unexpected (supplied={supplied[key]!r})")
            else:
                diffs.extend(_diff_json(canonical[key], supplied[key], here))
        return diffs
    if isinstance(canonical, list) and isinstance(supplied, list):
        if canonical != supplied:
            return [f"{path or '<root>'}: list differs (canonical={canonical!r} supplied={supplied!r})"]
        return []
    if canonical != supplied:
        return [f"{path or '<root>'}: canonical={canonical!r} supplied={supplied!r}"]
    return []


def check(path: Path) -> list[str]:
    """Compare a supplied artifact against the canonical schema built FRESH from the
    live dataclass.

    Building fresh (not loading the committed artifact) means a stale committed
    artifact cannot mask a dataclass change — ``--check`` fails on any divergence
    between the supplied artifact and the current dataclass-derived shape. Returns a
    list of human-readable diffs naming the differing fields; empty means parity.
    """
    canonical = build_schema()
    try:
        supplied = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read/parse supplied artifact: {exc}"]
    return _diff_json(canonical, supplied)
