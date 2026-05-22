from __future__ import annotations

import json
import hashlib
import os
import re
import site
import sysconfig
import types
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class BamlValidationError(ValueError):
    """Local, redacted BAML validation failure."""


class PhaseLoopCloseoutV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_status: str
    verification_status: Literal["not_run", "passed", "failed", "blocked"]
    dirty_paths: list[str]
    produced_if_gates: list[str]
    next_action: str | None = None
    blocker_class: str | None = None
    blocker_summary: str | None = None
    human_required: bool | None = None
    required_human_inputs: list[str]

    @field_validator("terminal_status")
    @classmethod
    def _terminal_status_literal(cls, value: str) -> str:
        PHASE_STATUSES = _enum_literals("terminal_status")
        if value not in PHASE_STATUSES:
            raise ValueError(f"invalid terminal_status: {value}")
        return value

    @field_validator("blocker_class")
    @classmethod
    def _blocker_class_literal(cls, value: str | None) -> str | None:
        BLOCKER_CLASSES = _enum_literals("blocker_class")
        if value is not None and value not in (*BLOCKER_CLASSES, "none"):
            raise ValueError(f"invalid blocker_class: {value}")
        return value

    @field_validator("produced_if_gates")
    @classmethod
    def _complete_requires_gates(cls, value: list[str], info) -> list[str]:
        if info.data.get("terminal_status") == "complete" and not value:
            raise ValueError("completed closeout reported zero produced_if_gates")
        return value


@dataclass(frozen=True)
class BamlRequest:
    id: str | None
    url: str
    method: str
    headers: dict[str, str]
    body: dict[str, Any]
    prompt: str


@dataclass(frozen=True)
class ParsedResponse:
    function_name: str
    payload: dict[str, Any]
    value: Any


def build_baml_request(function_name: str, payload: dict[str, Any] | None = None) -> BamlRequest:
    runtime, ctx_manager = _runtime()
    try:
        request = runtime.build_request_sync(
            function_name,
            payload or {},
            ctx_manager.clone_context(),
            None,
            None,
            _filtered_env(),
            False,
        )
    except Exception as exc:  # pragma: no cover - exact BAML errors vary by version
        raise BamlValidationError(_sanitize_error(exc)) from exc
    body = request.body.json()
    return BamlRequest(
        id=getattr(request, "id", None),
        url=str(request.url),
        method=str(request.method),
        headers={str(key): str(value) for key, value in dict(request.headers).items()},
        body=body,
        prompt=_extract_prompt(body),
    )


def parse_baml_response(function_name: str, raw_text: str) -> ParsedResponse:
    if _is_class_name(function_name):
        schema = export_function_schema(function_name)
        payload = _find_json_payload(str(raw_text or ""))
        _validate_payload_against_schema(payload, schema)
        return ParsedResponse(function_name=function_name, payload=payload, value=payload)

    runtime, ctx_manager = _runtime()
    enum_module, class_module = _type_modules()
    try:
        value = runtime.parse_llm_response(
            function_name,
            str(raw_text or ""),
            enum_module,
            class_module,
            class_module,
            False,
            ctx_manager.clone_context(),
            None,
            None,
            _filtered_env(),
        )
        if isinstance(value, PhaseLoopCloseoutV1):
            typed = value
        elif hasattr(value, "model_dump"):
            typed = PhaseLoopCloseoutV1.model_validate(value.model_dump())
        elif isinstance(value, dict):
            typed = PhaseLoopCloseoutV1.model_validate(value)
        else:
            typed = PhaseLoopCloseoutV1.model_validate(_find_json_payload(str(raw_text or "")))
    except Exception as exc:
        raise BamlValidationError(_sanitize_error(exc)) from exc
    return ParsedResponse(function_name=function_name, payload=typed.model_dump(), value=typed)


def export_function_schema(function_name: str) -> dict[str, Any]:
    baml_files = _read_baml_files()
    baml_text = "\n".join(baml_files.values())
    return_type = _export_target_type(baml_text, function_name)
    fields = _class_fields(baml_text, return_type)
    enum_literals = _enum_literal_map()
    required = [field_name for field_name, _field_type, _optional in fields]
    properties = {
        field_name: _schema_for_baml_field(baml_text, field_name, field_type, optional, enum_literals, seen=(return_type,))
        for field_name, field_type, optional in fields
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "PhaseLoopNativeCloseout" if return_type == "PhaseLoopCloseoutV1" else return_type,
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def inject_schema_description(prompt: str, schema: dict[str, Any]) -> str:
    rendered = _render_schema_description(schema)
    body = str(prompt or "").strip()
    return f"{rendered}\n\n{body}" if body else rendered


@lru_cache(maxsize=1)
def _runtime():
    from baml_py import BamlCtxManager, BamlRuntime

    files = _read_baml_files()
    runtime = BamlRuntime.from_files("baml_src", files, _filtered_env())
    return runtime, BamlCtxManager(runtime)


def _read_baml_files() -> dict[str, str]:
    src_dir = _baml_src_dir()
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(src_dir.glob("*.baml"))
        if path.is_file()
    }


def _baml_src_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "baml_src",
        Path(sysconfig.get_paths().get("data", "")) / "share" / "phase-loop-runtime" / "baml_src",
        Path(site.USER_BASE) / "share" / "phase-loop-runtime" / "baml_src",
        Path(__file__).resolve().parents[4] / "share" / "phase-loop-runtime" / "baml_src",
    ]
    for candidate in candidates:
        if (candidate / "emit_phase_closeout.baml").exists():
            return candidate
    raise BamlValidationError("BAML source file not found: emit_phase_closeout.baml")


def _function_return_type(baml_text: str, function_name: str) -> str:
    match = re.search(rf"\bfunction\s+{re.escape(function_name)}\s*\([^)]*\)\s*->\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{{", baml_text, re.DOTALL)
    if not match:
        raise BamlValidationError(f"BAML function not found: {function_name}")
    return match.group(1)


def _export_target_type(baml_text: str, name: str) -> str:
    try:
        return _function_return_type(baml_text, name)
    except BamlValidationError:
        if _class_exists(baml_text, name):
            return name
        raise


def _is_class_name(name: str) -> bool:
    return _class_exists("\n".join(_read_baml_files().values()), name)


def _class_exists(baml_text: str, class_name: str) -> bool:
    return bool(re.search(rf"\bclass\s+{re.escape(class_name)}\s*\{{", baml_text))


def _class_fields(baml_text: str, class_name: str) -> list[tuple[str, str, bool]]:
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\s*\{{(?P<body>.*?)\n\}}", baml_text, re.DOTALL)
    if not match:
        raise BamlValidationError(f"BAML class not found: {class_name}")
    fields: list[tuple[str, str, bool]] = []
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        field_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*(?:\[\])?)(\?)?", line)
        if not field_match:
            raise BamlValidationError(f"unsupported BAML class field syntax in {class_name}: {line}")
        fields.append((field_match.group(1), field_match.group(2), bool(field_match.group(3))))
    if not fields:
        raise BamlValidationError(f"BAML class has no exportable fields: {class_name}")
    return fields


@lru_cache(maxsize=1)
def _enum_literal_map() -> dict[str, tuple[str, ...]]:
    baml_text = "\n".join(_read_baml_files().values())
    result: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in baml_text.splitlines():
        line = raw_line.strip()
        header = re.fullmatch(r"//\s*([A-Za-z_][A-Za-z0-9_]*)\s+enum literals:\s*(.*)", line)
        if header:
            current = header.group(1)
            result[current] = []
            _extend_enum_literals(result[current], header.group(2))
            continue
        if current and line.startswith("//"):
            content = line[2:].strip()
            if " enum literals:" in content:
                current = None
                continue
            _extend_enum_literals(result[current], content)
            continue
        if current and line and not line.startswith("//"):
            current = None
    return {key: tuple(values) for key, values in result.items()}


def _extend_enum_literals(values: list[str], text: str) -> None:
    for item in text.split(","):
        literal = item.strip().strip("`.")
        if literal:
            values.append(literal)


def _enum_literals(field_name: str) -> tuple[str, ...]:
    values = _enum_literal_map().get(field_name)
    if not values:
        raise BamlValidationError(f"BAML enum literals not found for field: {field_name}")
    return values


def _schema_for_baml_field(
    baml_text: str,
    field_name: str,
    field_type: str,
    optional: bool,
    enum_literals: dict[str, tuple[str, ...]],
    *,
    seen: tuple[str, ...],
) -> dict[str, Any]:
    array_item_type = field_type[:-2] if field_type.endswith("[]") else None
    if field_type == "string":
        schema: dict[str, Any] = {"type": ["string", "null"] if optional else "string"}
    elif field_type == "bool":
        schema = {"type": ["boolean", "null"] if optional else "boolean"}
    elif array_item_type == "string":
        if optional:
            schema = {"type": ["array", "null"], "items": {"type": "string"}}
        else:
            schema = {"type": "array", "items": {"type": "string"}}
    elif array_item_type and _class_exists(baml_text, array_item_type):
        item_schema = _schema_for_baml_class(baml_text, array_item_type, enum_literals, seen=seen)
        if optional:
            schema = {"type": ["array", "null"], "items": item_schema}
        else:
            schema = {"type": "array", "items": item_schema}
    elif _class_exists(baml_text, field_type):
        object_schema = _schema_for_baml_class(baml_text, field_type, enum_literals, seen=seen)
        if optional:
            schema = {**object_schema, "type": ["object", "null"]}
        else:
            schema = object_schema
    else:
        raise BamlValidationError(f"unsupported BAML field type for schema export: {field_type}")
    if field_name in enum_literals:
        enum_values: list[str | None] = list(enum_literals[field_name])
        if optional:
            enum_values.append(None)
        schema["enum"] = enum_values
    description = _FIELD_DESCRIPTIONS.get(field_name)
    if description:
        schema["description"] = description
    return schema


def _schema_for_baml_class(
    baml_text: str,
    class_name: str,
    enum_literals: dict[str, tuple[str, ...]],
    *,
    seen: tuple[str, ...],
) -> dict[str, Any]:
    if class_name in seen:
        raise BamlValidationError(f"recursive BAML class export is unsupported: {class_name}")
    fields = _class_fields(baml_text, class_name)
    required = [field_name for field_name, _field_type, _optional in fields]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            field_name: _schema_for_baml_field(
                baml_text,
                field_name,
                field_type,
                optional,
                enum_literals,
                seen=(*seen, class_name),
            )
            for field_name, field_type, optional in fields
        },
    }


def _validate_payload_against_schema(payload: Any, schema: dict[str, Any], path: str = "$") -> None:
    allowed_type = schema.get("type")
    if isinstance(allowed_type, list) and payload is None and "null" in allowed_type:
        return
    effective_type = next((item for item in allowed_type if item != "null"), None) if isinstance(allowed_type, list) else allowed_type
    if effective_type == "object":
        if not isinstance(payload, dict):
            raise BamlValidationError(f"{path} must be an object")
        required = set(schema.get("required", ()))
        missing = required.difference(payload)
        extra = set(payload).difference(schema.get("properties", {}))
        if missing:
            raise BamlValidationError(f"{path} missing required fields: {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False and extra:
            raise BamlValidationError(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
        for field_name, field_schema in schema.get("properties", {}).items():
            if field_name in payload:
                _validate_payload_against_schema(payload[field_name], field_schema, f"{path}.{field_name}")
    elif effective_type == "array":
        if not isinstance(payload, list):
            raise BamlValidationError(f"{path} must be an array")
        for index, item in enumerate(payload):
            _validate_payload_against_schema(item, schema.get("items", {}), f"{path}[{index}]")
    elif effective_type == "string":
        if not isinstance(payload, str):
            raise BamlValidationError(f"{path} must be a string")
    elif effective_type == "boolean":
        if not isinstance(payload, bool):
            raise BamlValidationError(f"{path} must be a boolean")
    else:
        raise BamlValidationError(f"{path} has unsupported schema type: {allowed_type}")
    if "enum" in schema and payload not in schema["enum"]:
        raise BamlValidationError(f"{path} has unsupported literal")


_FIELD_DESCRIPTIONS = {
    "terminal_status": "Final phase status claimed by the executor closeout.",
    "verification_status": "Verification outcome for the reported phase work.",
    "dirty_paths": "Repo-relative dirty paths left after execution.",
    "produced_if_gates": "Interface-freeze gates actually produced by this closeout.",
    "next_action": "Concise next action for the operator or runner. May be null.",
    "blocker_class": "Frozen blocker class when terminal_status is blocked. Null otherwise.",
    "blocker_summary": "Actionable non-secret blocker summary. Null when not blocked.",
    "human_required": "Whether the blocker requires a human decision. Null when not blocked.",
    "required_human_inputs": "Non-secret human inputs required to unblock execution. Empty when not blocked.",
}


def _render_schema_description(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    lines = [
        "Phase-loop closeout JSON schema description:",
        f"schema_sha256: {hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        f"type: {schema.get('type')}",
        f"additionalProperties: {json.dumps(schema.get('additionalProperties'))}",
        "required: " + ", ".join(str(field) for field in schema.get("required", ())),
        "properties:",
    ]
    for field_name in schema.get("required", ()):
        field_schema = schema.get("properties", {}).get(field_name, {})
        line = f"- {field_name}: type={json.dumps(field_schema.get('type'), sort_keys=True)}"
        if "enum" in field_schema:
            line += "; enum=" + ", ".join("null" if value is None else str(value) for value in field_schema["enum"])
        if field_schema.get("items"):
            line += "; items=" + json.dumps(field_schema["items"], sort_keys=True, separators=(",", ":"))
        lines.append(line)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _type_modules() -> tuple[types.ModuleType, types.ModuleType]:
    enum_module = types.ModuleType("phase_loop_runtime.baml_enums")
    class_module = types.ModuleType("phase_loop_runtime.baml_classes")
    class_module.PhaseLoopCloseoutV1 = PhaseLoopCloseoutV1
    return enum_module, class_module


def _filtered_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if value is not None}


def _extract_prompt(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        elif content is not None:
            parts.append(str(content))
    return "\n\n".join(part for part in parts if part).strip()


def _find_json_payload(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise BamlValidationError("no JSON object found in BAML response")


def _sanitize_error(exc: BaseException) -> str:
    message = str(exc)
    if isinstance(exc, ValidationError):
        message = "; ".join(error.get("msg", "validation error") for error in exc.errors())
    message = re.sub(r"(?i)(api[_-]?key|authorization|token|secret|password)[^\\s,;]*", r"\\1=<redacted>", message)
    message = " ".join(message.split())
    if len(message) > 500:
        message = message[:497] + "..."
    return message or exc.__class__.__name__
