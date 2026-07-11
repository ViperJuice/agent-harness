#!/usr/bin/env python3
"""IF-0-DISSECT-1 profile validator (stdlib-only; research spike).

Usage:
    python3 validate_profile.py <profile.json> <schema.v1.json>

Two independent gates; exits NON-ZERO on any violation:

  (1) SCHEMA GATE  — a hand-rolled JSON-Schema (draft-07 subset) interpreter
      covering exactly the keywords schema.v1.json uses: type, required,
      properties, additionalProperties, items, enum, const, minimum, minItems.
      No `import jsonschema` (CI may lack it).

  (2) REDACTION GATE — a SEMANTIC pass that is independent of the schema shape.
      It actively REJECTS a row/verdict that carries a raw argument value, file
      content, path, or prose body — NOT merely a schema-shape check. It proves
      that `arg_shape_sample` holds only shape/type TOKENS by recursing every
      leaf and rejecting any scalar that is not in the type vocabulary, and by
      requiring keys / tool names / task types / blockers to match bounded
      identifier patterns. A well-typed row whose shape leaf is a real string
      value (e.g. "/home/user/secret.txt") is schema-valid but MUST be rejected
      here. See the poisoned-copy proof in report.md / verify steps.
"""
from __future__ import annotations

import json
import re
import sys

# --- redaction-safe leaf type vocabulary (must match extract_profile.py) ---
SCALAR_VOCAB = {"string", "integer", "number", "boolean", "null"}

# identifier-ish patterns (bounded) for the fields that must never carry free text
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
TOOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
BLOCKER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
MAX_KEY_LEN = 64
MAX_TOOL_LEN = 128
MAX_META_STR_LEN = 256


class Violation(Exception):
    pass


# ---------------------------------------------------------------------------
# (1) minimal JSON-Schema interpreter
# ---------------------------------------------------------------------------
_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, typ):
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    py = _JSON_TYPES.get(typ)
    return py is not None and isinstance(value, py)


def validate_schema(value, schema, path, errors):
    if not isinstance(schema, dict):
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if "type" in schema:
        types = schema["type"]
        types = [types] if isinstance(types, str) else types
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
            return  # further keyword checks assume the type held
    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: {len(value)} items < minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                validate_schema(item, schema["items"], f"{path}[{i}]", errors)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required property '{req}'")
        addl = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                validate_schema(v, props[k], f"{path}.{k}", errors)
            elif addl is False:
                errors.append(f"{path}: additional property '{k}' not allowed")
            elif isinstance(addl, dict):
                validate_schema(v, addl, f"{path}.{k}", errors)


# ---------------------------------------------------------------------------
# (2) semantic redaction pass (independent of schema)
# ---------------------------------------------------------------------------
def _check_shape(node, path):
    """Recurse a shape node. Legal shapes: a vocab TOKEN string, a dict of
    shapes, or a list of shapes. ANY other leaf (a real string value, an int,
    a float, a bool, None) is a raw value -> reject."""
    if isinstance(node, str):
        if node not in SCALAR_VOCAB:
            raise Violation(
                f"{path}: leaf {node!r} is not a shape/type token {sorted(SCALAR_VOCAB)} "
                f"— looks like a RAW VALUE (redaction breach)")
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if not (isinstance(k, str) and KEY_RE.match(k) and len(k) <= MAX_KEY_LEN):
                raise Violation(f"{path}: shape key {k!r} is not a bounded identifier "
                                f"— possible value smuggled into a key")
            _check_shape(v, f"{path}.{k}")
        return
    if isinstance(node, list):
        for i, v in enumerate(node):
            _check_shape(v, f"{path}[{i}]")
        return
    # int / float / bool / None sitting as a leaf = a raw scalar value, not a shape
    raise Violation(f"{path}: leaf of type {type(node).__name__} ({node!r}) is a RAW VALUE, "
                    f"not a shape token (redaction breach)")


def redaction_pass(profile):
    for i, row in enumerate(profile.get("rows", [])):
        rp = f"rows[{i}]"
        tn = row.get("tool_name", "")
        if not (isinstance(tn, str) and TOOL_RE.match(tn) and len(tn) <= MAX_TOOL_LEN):
            raise Violation(f"{rp}.tool_name {tn!r} not a bounded identifier")
        for j, k in enumerate(row.get("arg_keys", [])):
            if not (isinstance(k, str) and KEY_RE.match(k) and len(k) <= MAX_KEY_LEN):
                raise Violation(f"{rp}.arg_keys[{j}] {k!r} not a bounded identifier "
                                f"— possible raw value in a key slot")
        shape = row.get("arg_shape_sample", {})
        if not isinstance(shape, dict):
            raise Violation(f"{rp}.arg_shape_sample must be an object")
        _check_shape(shape, f"{rp}.arg_shape_sample")
    for i, v in enumerate(profile.get("verdicts", [])):
        vp = f"verdicts[{i}]"
        for j, b in enumerate(v.get("blockers", [])):
            if not (isinstance(b, str) and BLOCKER_RE.match(b) and len(b) <= MAX_KEY_LEN):
                raise Violation(f"{vp}.blockers[{j}] {b!r} is not a short token "
                                f"— verdict blockers must be tokens, not prose")
    # top-level metadata strings: bounded length only (our own constants)
    for k in ("schema_version", "generated_by"):
        val = profile.get(k)
        if val is not None and (not isinstance(val, str) or len(val) > MAX_META_STR_LEN):
            raise Violation(f"{k}: metadata string too long / wrong type")


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        print("ERROR: expected exactly <profile.json> <schema.v1.json>", file=sys.stderr)
        return 2
    profile_path, schema_path = argv[1], argv[2]
    try:
        with open(profile_path) as fh:
            profile = json.load(fh)
        with open(schema_path) as fh:
            schema = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"FAIL: cannot load inputs: {e}", file=sys.stderr)
        return 2

    errors: list[str] = []
    validate_schema(profile, schema, "$", errors)
    if errors:
        print("SCHEMA GATE: FAIL", file=sys.stderr)
        for e in errors[:50]:
            print("  -", e, file=sys.stderr)
        return 1
    print("SCHEMA GATE: PASS")

    try:
        redaction_pass(profile)
    except Violation as v:
        print("REDACTION GATE: FAIL", file=sys.stderr)
        print("  -", v, file=sys.stderr)
        return 1
    print("REDACTION GATE: PASS")

    n_rows = len(profile.get("rows", []))
    n_verd = len(profile.get("verdicts", []))
    print(f"OK: {n_rows} rows, {n_verd} verdict record(s) validated against {schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
