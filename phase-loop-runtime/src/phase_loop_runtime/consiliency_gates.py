"""CS-0.6 -- the four L0 `.consiliency/` gates.

Wired at top-of-loop (an advisory print, like the existing ``_governed_warning``
early-loop notice) and at closeout (threaded into ``build_phase_loop_closeout``,
like ``docs_freshness`` -- see issue #18). :func:`scan_consiliency_gates` is the
single pure pre-scan both call sites share; it never mutates the repo.

Gate families (``consiliency_contract``'s ``loop-gate-protocol``):

* **presence** -- every doc the composed required-documents registry demands
  for the manifest's declared archetype(s)/modifier(s) has a manifest entry
  whose declared ``path``/``ref`` actually resolves.
* **local-integrity** -- documents the manifest has promoted to
  ``hash-checked`` maturity have a recorded git-scoped digest snapshot that
  still matches. A no-op today: Phase 0's required-documents registry floors
  every doc at ``presence-only``, so nothing is hash-checked yet -- the gate
  exists so upgrading a doc's maturity later has somewhere to land.
* **layout-validity** -- the manifest (and, if present, the status/interface
  artifacts) validate against the vendored ``consiliency_contract`` schemas.
* **version-skew** -- the repo-declared ``contract_version`` against the
  installed ``consiliency_contract`` package version, per the version-skew
  protocol's own compatible ranges.

CONSENT GATE: this module acts ONLY on repos that already have a
`.consiliency/manifest.json` (see :func:`find_consiliency_manifest`). A repo
without one is a pure no-op -- ``status: "skipped"``, nothing read beyond the
one existence check, nothing written. The full adoption-profile consent check
(CS-0.12 / contract 0.2.0) tightens this later; this is the seam.

Default-safe posture (roadmap CS-0.6, mirrors docs_freshness/issue #18):
SOFT/warn by default. Blocking is opt-in via ``PHASE_LOOP_CONSILIENCY_GATES=hard``.
``human_required`` is NEVER set by this module. The version-skew gate additionally
never escalates past ``warn`` even in ``hard`` mode -- the version-skew protocol's
own ``default_behavior.phase0_severity`` is a normative ``const: "warn"`` at
Phase 0, independent of any per-repo gate-mode opt-in.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .consiliency_layout import (
    compose_required_documents,
    consiliency_root,
    contract_version_status_schema,
    find_consiliency_manifest,
    installed_contract_version,
    interface_declaration_schema,
    load_consiliency_manifest,
    manifest_schema,
    version_skew_protocol,
)

CONSILIENCY_GATES_ENV = "PHASE_LOOP_CONSILIENCY_GATES"
CONSILIENCY_GATES_MODES: tuple[str, ...] = ("off", "warn", "hard")
DEFAULT_CONSILIENCY_GATES_MODE = "warn"

_GATE_NAMES = ("presence", "local_integrity", "layout_validity", "version_skew")


def resolve_consiliency_gates_mode(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = str(env.get(CONSILIENCY_GATES_ENV) or "").strip().lower()
    return value if value in CONSILIENCY_GATES_MODES else DEFAULT_CONSILIENCY_GATES_MODE


def _skipped(mode: str, *, consent: bool, manifest_path: str | None = None) -> dict[str, Any]:
    return {
        "status": "skipped",
        "mode": mode,
        "consent": consent,
        "manifest_path": manifest_path,
        "gates": {},
    }


def scan_consiliency_gates(
    repo: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    mode = resolve_consiliency_gates_mode(env)
    if not repo:
        return _skipped(mode, consent=False)
    repo_path = Path(repo)
    manifest_file = find_consiliency_manifest(repo_path)
    if manifest_file is None:
        return _skipped(mode, consent=False)
    if mode == "off":
        return _skipped(mode, consent=True, manifest_path=str(manifest_file))
    manifest = load_consiliency_manifest(repo_path)
    if manifest is None:
        # Present but unparsable -- that's exactly a layout-validity finding,
        # not a crash and not "no consent" (the repo DID opt in).
        gate = {
            "status": "blocked" if mode == "hard" else "warn",
            "maturity": "presence-only",
            "findings": [{"code": "manifest_unparsable", "message": f"{manifest_file} is not valid JSON"}],
        }
        return {
            "status": gate["status"],
            "mode": mode,
            "consent": True,
            "manifest_path": str(manifest_file),
            "gates": {"layout_validity": gate},
        }

    gates = {
        "presence": _gate_presence(repo_path, manifest, mode=mode),
        "local_integrity": _gate_local_integrity(repo_path, manifest, mode=mode),
        "layout_validity": _gate_layout_validity(repo_path, manifest, mode=mode),
        "version_skew": _gate_version_skew(manifest, mode=mode),
    }
    if any(g["status"] == "blocked" for g in gates.values()):
        overall = "blocked"
    elif any(g["status"] == "warn" for g in gates.values()):
        overall = "warn"
    else:
        overall = "passed"
    return {
        "status": overall,
        "mode": mode,
        "consent": True,
        "manifest_path": str(manifest_file),
        "gates": gates,
    }


def _gate_status(findings: list[dict[str, Any]], *, mode: str, capped_warn: bool = False) -> str:
    if not findings:
        return "passed"
    if mode == "hard" and not capped_warn:
        return "blocked"
    return "warn"


def _declared_archetypes_modifiers(manifest: Mapping[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    declaration = dict(manifest.get("declaration") or {})
    decl_mode = str(declaration.get("mode") or "baseline-only")
    archetypes = tuple(declaration.get("archetypes") or ())
    modifiers = tuple(declaration.get("modifiers") or ())
    return decl_mode, archetypes, modifiers


def _gate_presence(repo: Path, manifest: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    decl_mode, archetypes, modifiers = _declared_archetypes_modifiers(manifest)
    try:
        rows = compose_required_documents(mode=decl_mode, archetypes=archetypes, modifiers=modifiers)
    except ValueError as exc:
        findings = [{"code": "declaration_invalid", "message": str(exc)}]
        return {"status": _gate_status(findings, mode=mode), "maturity": "presence-only", "findings": findings}

    documents_by_id = {d.get("id"): d for d in manifest.get("documents", []) if isinstance(d, Mapping)}
    findings: list[dict[str, Any]] = []
    for row in rows:
        entry = documents_by_id.get(row.id)
        if entry is None:
            findings.append({"code": "missing_manifest_entry", "doc_id": row.id})
            continue
        path = entry.get("path")
        ref = entry.get("ref")
        if path:
            if not (repo / str(path)).exists():
                findings.append({"code": "missing_file", "doc_id": row.id, "path": str(path)})
        elif ref:
            if not str(dict(ref).get("value") or "").strip():
                findings.append({"code": "empty_ref", "doc_id": row.id})
        else:
            findings.append({"code": "no_path_or_ref", "doc_id": row.id})
    return {"status": _gate_status(findings, mode=mode), "maturity": "presence-only", "findings": findings}


def _git_show(repo: Path, path: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "show", f"HEAD:{path}"], stderr=subprocess.DEVNULL
        )
    except Exception:
        return None


def _gate_local_integrity(repo: Path, manifest: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    hash_checked = [
        d for d in manifest.get("documents", []) if isinstance(d, Mapping) and d.get("maturity") == "hash-checked" and d.get("path")
    ]
    if not hash_checked:
        return {
            "status": "passed",
            "maturity": "presence-only",
            "findings": [],
            "note": "no hash-checked documents declared; nothing to snapshot yet",
        }
    snapshot_file = consiliency_root(repo) / "integrity-snapshot.json"
    snapshot: dict[str, str] = {}
    if snapshot_file.is_file():
        try:
            snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            snapshot = {}
    findings: list[dict[str, Any]] = []
    checked_any = False
    for entry in hash_checked:
        doc_id = str(entry.get("id"))
        path = str(entry.get("path"))
        content = _git_show(repo, path)
        if content is None:
            findings.append({"code": "unreadable_git_blob", "doc_id": doc_id, "path": path})
            continue
        digest = hashlib.sha256(content).hexdigest()
        recorded = snapshot.get(doc_id)
        if recorded is None:
            findings.append({"code": "no_local_snapshot", "doc_id": doc_id, "path": path})
            continue
        checked_any = True
        if recorded != digest:
            findings.append({"code": "hash_drift", "doc_id": doc_id, "path": path, "expected": recorded, "actual": digest})
    return {
        "status": _gate_status(findings, mode=mode),
        "maturity": "hash-checked" if checked_any else "presence-only",
        "findings": findings,
    }


def _gate_layout_validity(repo: Path, manifest: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for err in Draft202012Validator(manifest_schema()).iter_errors(dict(manifest)):
        findings.append({"code": "manifest_schema_invalid", "message": err.message, "path": list(err.absolute_path)})

    status_rel = None
    for entry in manifest.get("documents", []):
        if isinstance(entry, Mapping) and entry.get("id") == "contract-version-status":
            status_rel = entry.get("path")
    if status_rel:
        status_file = repo / str(status_rel)
        if status_file.is_file():
            try:
                status_doc = json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                findings.append({"code": "status_unparsable", "path": str(status_rel)})
            else:
                for err in Draft202012Validator(contract_version_status_schema()).iter_errors(status_doc):
                    findings.append({"code": "status_schema_invalid", "message": err.message, "path": list(err.absolute_path)})

    interfaces_rel = manifest.get("interfaces")
    if interfaces_rel:
        interfaces_file = repo / str(interfaces_rel)
        if interfaces_file.is_file():
            try:
                interfaces_doc = json.loads(interfaces_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                findings.append({"code": "interfaces_unparsable", "path": str(interfaces_rel)})
            else:
                for err in Draft202012Validator(interface_declaration_schema()).iter_errors(interfaces_doc):
                    findings.append({"code": "interfaces_schema_invalid", "message": err.message, "path": list(err.absolute_path)})

    return {"status": _gate_status(findings, mode=mode), "maturity": "presence-only", "findings": findings}


_RANGE_RE = re.compile(r"^>=\s*([0-9.]+)\s*<\s*([0-9.]+)$")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[.\-+]", version) if part.isdigit())


def _version_in_range(version: str, range_str: str) -> bool | None:
    match = _RANGE_RE.match(range_str.strip())
    if not match:
        return None
    try:
        v = _version_tuple(version)
        lo = _version_tuple(match.group(1))
        hi = _version_tuple(match.group(2))
    except ValueError:
        return None
    return lo <= v < hi


def _contract_compatible_range() -> str:
    """The compatible-version range for ``contract_version``.

    The vendored package publishes this as a JSON-Schema ``const`` (there is
    no separate data instance for the protocol's own numbers), so it is read
    out of the schema structure at runtime -- never hand-copied as a literal.
    """
    schema = version_skew_protocol()
    return str(
        schema.get("properties", {})
        .get("compatible_ranges", {})
        .get("properties", {})
        .get("contract", {})
        .get("const", "")
    )


def _gate_version_skew(manifest: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    installed_version = installed_contract_version()
    repo_version = str(manifest.get("contract_version") or "").strip()
    contract_range = _contract_compatible_range()
    findings: list[dict[str, Any]] = []
    if not repo_version:
        compatibility = "unknown"
        findings.append({"code": "missing_contract_version"})
    else:
        in_range = _version_in_range(repo_version, contract_range)
        if in_range is None:
            compatibility = "unknown"
            findings.append({"code": "unparsable_range", "range": contract_range})
        elif not in_range:
            compatibility = "incompatible"
            findings.append(
                {
                    "code": "version_skew",
                    "repo_contract_version": repo_version,
                    "installed_contract_version": installed_version,
                    "compatible_range": contract_range,
                }
            )
        else:
            compatibility = "compatible"
    # Normative: version-skew stays warn-only at Phase 0 regardless of the
    # gate mode opt-in (version-skew-protocol.schema `default_behavior.
    # phase0_severity` is a fixed const, not a per-consumer policy knob).
    return {
        "status": _gate_status(findings, mode=mode, capped_warn=True),
        "maturity": "realized-edge-observed",
        "compatibility": compatibility,
        "installed_contract_version": installed_version,
        "repo_contract_version": repo_version or None,
        "findings": findings,
    }
