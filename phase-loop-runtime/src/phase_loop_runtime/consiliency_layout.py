"""Shared `.consiliency/` layout constants and lookup helpers (CS-0.5/CS-0.6).

Single source of truth for where the `.consiliency/` artifacts live and how a
manifest is composed against the vendored `consiliency_contract` package data.
The scaffolder (``consiliency_scaffold``), the L0 gates
(``consiliency_gates``), and the runner's consent check must all agree on
this path -- drift here would be a silent bug (an operator could scaffold a
manifest the gates never find).

Version-gated DUAL-READ (CS-0.5 scope): this module is purely additive. It
never reads or writes ``.phase-loop/`` or ``.pipeline/``, and nothing in the
existing runtime is rewired to prefer `.consiliency/` yet -- that fallback
seam is for CS-0.12 once the adoption-profile consent check lands. Today,
`.consiliency/` and legacy layouts simply coexist.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from consiliency_contract import CONTRACT_VERSION, load_registry, load_schema

#: Repo-relative root for all Consiliency-standard artifacts.
CONSILIENCY_DIR = ".consiliency"
#: Filenames within CONSILIENCY_DIR, pinned to the shapes used by the
#: contract's own conformance vectors (manifest-valid-product.json uses
#: ".consiliency/status.json" and ".consiliency/interfaces.json").
MANIFEST_FILENAME = "manifest.json"
STATUS_FILENAME = "status.json"
INTERFACES_FILENAME = "interfaces.json"
#: Stub documents the scaffolder is allowed to author live under this
#: sub-namespace so they can never collide with a repo's own doc layout.
STUB_DOCS_SUBDIR = "docs"

ARCHETYPE_IDS: tuple[str, ...] = ("product", "service", "library", "infra", "tooling-meta", "experiment", "document")
MODIFIER_IDS: tuple[str, ...] = ("data-bearing", "public", "regulated", "user-facing")


def consiliency_root(repo: Path) -> Path:
    return Path(repo) / CONSILIENCY_DIR


def manifest_path(repo: Path) -> Path:
    return consiliency_root(repo) / MANIFEST_FILENAME


def status_path(repo: Path) -> Path:
    return consiliency_root(repo) / STATUS_FILENAME


def interfaces_path(repo: Path) -> Path:
    return consiliency_root(repo) / INTERFACES_FILENAME


def find_consiliency_manifest(repo: str | Path) -> Path | None:
    """The one place that decides whether a repo has opted into Consiliency.

    Every consumer (scaffolder overwrite checks, all four L0 gates, and the
    runner's top-of-loop/closeout hooks) MUST call this rather than
    re-deriving the path, so the CS-0.6 consent gate ("act only on repos that
    HAVE a `.consiliency/manifest`") is enforced identically everywhere.
    """
    path = manifest_path(Path(repo))
    return path if path.is_file() else None


def load_consiliency_manifest(repo: str | Path) -> dict[str, Any] | None:
    """Parsed manifest, or ``None`` when absent OR unparsable.

    Unparsable is folded into "no consent" rather than raised -- a corrupt
    manifest must never crash the loop; the layout-validity gate is what
    reports that condition as a finding.
    """
    path = find_consiliency_manifest(repo)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class RequiredDocRow:
    id: str
    doc_class: str
    required: bool
    maturity_floor: str
    l0_stub_allowed: bool
    l0_note: str | None = None
    source: str = "baseline"  # baseline | archetype:<id> | modifier:<id>


class RequiredDocumentConflict(ValueError):
    """Two composed rows share an id but are not byte-identical."""


def compose_required_documents(
    *,
    mode: str,
    archetypes: tuple[str, ...] = (),
    modifiers: tuple[str, ...] = (),
) -> tuple[RequiredDocRow, ...]:
    """Compose the required-document set per the registry's own composition
    order: baseline first, then archetypes in REGISTRY order (not input
    order), then modifiers in registry order. Byte-identical duplicate ids are
    de-duplicated; conflicting duplicate ids fail (registry policy)."""
    if mode not in ("baseline-only", "archetyped"):
        raise ValueError(f"invalid declaration mode: {mode!r}")
    registry = load_registry("required_documents")
    archetype_registry = load_registry("archetypes")
    registry_archetype_order = [entry["id"] for entry in archetype_registry["archetypes"]]
    registry_modifier_order = [entry["id"] for entry in archetype_registry["modifiers"]]

    rows_by_id: dict[str, RequiredDocRow] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}

    def _add(raw: Mapping[str, Any], source: str) -> None:
        doc_id = raw["id"]
        if doc_id in raw_by_id and raw_by_id[doc_id] != dict(raw):
            raise RequiredDocumentConflict(
                f"required-document id {doc_id!r} conflicts between {rows_by_id[doc_id].source!r} and {source!r}"
            )
        if doc_id in raw_by_id:
            return  # byte-identical duplicate -- de-dupe silently.
        raw_by_id[doc_id] = dict(raw)
        rows_by_id[doc_id] = RequiredDocRow(
            id=doc_id,
            doc_class=raw["class"],
            required=bool(raw.get("required", True)),
            maturity_floor=raw["maturity_floor"],
            l0_stub_allowed=bool(raw.get("l0_stub_allowed", False)),
            l0_note=raw.get("l0_note"),
            source=source,
        )

    for raw in registry["baseline"]:
        _add(raw, "baseline")
    if mode == "archetyped":
        for archetype_id in registry_archetype_order:
            if archetype_id not in archetypes:
                continue
            for raw in registry.get("archetypes", {}).get(archetype_id, ()):
                _add(raw, f"archetype:{archetype_id}")
        for modifier_id in registry_modifier_order:
            if modifier_id not in modifiers:
                continue
            for raw in registry.get("modifiers", {}).get(modifier_id, ()):
                _add(raw, f"modifier:{modifier_id}")
    return tuple(rows_by_id[doc_id] for doc_id in raw_by_id)


def installed_contract_version() -> str:
    return CONTRACT_VERSION


def manifest_schema() -> dict[str, Any]:
    return load_schema("manifest")


def contract_version_status_schema() -> dict[str, Any]:
    return load_schema("contract_version_status")


def interface_declaration_schema() -> dict[str, Any]:
    return load_schema("interface_declaration")


def version_skew_protocol() -> dict[str, Any]:
    return load_schema("version_skew_protocol")


def loop_gate_protocol() -> dict[str, Any]:
    return load_schema("loop_gate_protocol")
