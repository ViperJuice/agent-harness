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

_GATE_NAMES = ("presence", "local_integrity", "layout_validity", "version_skew", "git_discipline", "spec_conformance")

# spec_conformance (HGATE) — the generic conformance ladder + default bar. Advisory
# panel (Fable + Codex 5.5 + Gemini 3.1 Pro, unanimous) set the default PASS bar at
# `hash-checked`+ ("bar B"): it's the lowest rung where the claim is checkable by the
# existing local_integrity digest machinery, so a self-asserted `hash-checked` obligates
# a recorded digest rather than being a free claim. `presence-only` is an INFO-grade
# soft warn (declared but not digest-anchored); `present-nonconforming`/`foreign`/
# `unmanaged` are loud. The per-archetype/per-doc conformance floor as CONTRACT REGISTRY
# DATA is the deferred ratchet (do NOT hardcode org policy here). These labels mirror the
# contract `maturity-labels` registry; `certified` is a deprecated alias of `parity-certified`.
_SPEC_PROJECTION_CLASSES = ("proj-S", "proj-code")
_CONFORMANCE_LADDER = {  # conforming rungs, low -> high
    "presence-only": 0,
    "hash-checked": 1,
    "realized-edge-observed": 2,
    "parity-certified": 3,
    "authority-certified": 4,
}
_CONFORMANCE_ALIASES = {"certified": "parity-certified"}  # deprecated alias, ranked as parity-certified
_NONCONFORMING_LABELS = frozenset({"present-nonconforming", "foreign", "unmanaged"})
_CONFORMANCE_FLOOR = "hash-checked"  # bar B: the generic default PASS floor


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
        "git_discipline": _gate_git_discipline(repo_path, mode=mode),
        "spec_conformance": _gate_spec_conformance(manifest, mode=mode),
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


def _gate_spec_conformance(manifest: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    """HGATE: warn when a DECLARED spec-projection (proj-S/proj-code) sits below the
    conformance bar. Structural — reads declared maturity, no byte/digest verification
    (that is local_integrity's job for hash-checked, and a future canon-backed verifier's
    for higher rungs). Full no-op when the repo declares no spec-projections (OPA-style
    optional layer). Two-tier: `present-nonconforming`/`foreign`/`unmanaged` are loud
    (may block under hard mode); a below-bar non-sanctioned `presence-only` is an
    info-grade soft warn that NEVER blocks (so honest early adopters are nudged, not
    punished).

    HONESTY -- this gate is FORWARD INFRASTRUCTURE, dormant on schema-valid manifests
    with today's contract data: (a) every proj-S/proj-code required-doc row is
    `l0_stub_allowed` at a `presence-only` floor, so the info + below-floor branches
    are dead until the per-archetype conformance ratchet raises floors (deferred to
    contract-registry data); (b) the manifest schema's maturity enum is only
    {presence-only, hash-checked, realized-edge-observed}, so the non-conforming labels
    that drive the loud branches are schema-INVALID and already caught by
    layout_validity. So today it fires nothing layout_validity does not; it activates
    without code change when the schema enum + the ratchet expand. Known ceiling:
    `hash-checked` is the only rung the harness can locally verify (via local_integrity,
    and only for `path`-backed docs -- `ref`-backed hash-checked is not yet digest-
    checked); `realized-edge-observed`+ pass as accepted claims, verified downstream by
    a canon-backed verifier, not here."""
    decl_mode, archetypes, modifiers = _declared_archetypes_modifiers(manifest)
    try:
        rows = compose_required_documents(mode=decl_mode, archetypes=archetypes, modifiers=modifiers)
    except ValueError:
        # A malformed declaration is presence/layout's finding, not conformance's.
        return {"status": "passed", "maturity": "presence-only", "findings": [],
                "note": "declaration invalid; deferred to presence/layout gates"}
    proj_rows = {r.id: r for r in rows if r.doc_class in _SPEC_PROJECTION_CLASSES}
    docs_by_id = {d.get("id"): d for d in manifest.get("documents", []) if isinstance(d, Mapping)}
    declared = [(pid, proj_rows[pid], docs_by_id[pid]) for pid in sorted(proj_rows) if pid in docs_by_id]
    if not declared:
        return {"status": "passed", "maturity": "presence-only", "findings": [],
                "note": "no spec-projection documents declared; conformance gate is a no-op"}

    bar_rank = _CONFORMANCE_LADDER[_CONFORMANCE_FLOOR]
    loud: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for pid, row, entry in declared:
        raw = str(entry.get("maturity") or "").strip()
        label = _CONFORMANCE_ALIASES.get(raw, raw)
        floor_rank = _CONFORMANCE_LADDER.get(_CONFORMANCE_ALIASES.get(row.maturity_floor, row.maturity_floor), 0)
        if not raw:
            loud.append({"code": "spec_maturity_missing", "doc_id": pid,
                         "message": f"spec-projection '{pid}' declares no maturity"})
        elif label == "present-nonconforming":
            loud.append({"code": "spec_nonconforming", "doc_id": pid, "maturity": raw,
                         "message": f"spec-projection '{pid}' is 'present-nonconforming' -- the projection asserts the code does NOT match spec"})
        elif label in _NONCONFORMING_LABELS:  # foreign / unmanaged -- governance-status, NOT a non-conformance assertion
            loud.append({"code": "spec_ungoverned", "doc_id": pid, "maturity": raw,
                         "message": f"spec-projection '{pid}' declares governance status '{raw}' (not this repo's, or ungoverned) -- outside the conformance ladder"})
        elif label not in _CONFORMANCE_LADDER:
            loud.append({"code": "spec_maturity_unknown", "doc_id": pid, "maturity": raw,
                         "message": f"spec-projection '{pid}' declares an unrecognized maturity '{raw}'"})
        elif _CONFORMANCE_LADDER[label] < floor_rank:
            # Below the doc's OWN contract-declared maturity_floor -- a real regression.
            loud.append({"code": "spec_below_declared_floor", "doc_id": pid, "maturity": raw, "floor": row.maturity_floor,
                         "message": f"spec-projection '{pid}' is '{raw}' but the contract floor for it is '{row.maturity_floor}'"})
        elif _CONFORMANCE_LADDER[label] < bar_rank and not row.l0_stub_allowed:
            # Below the generic conformance bar AND not a contract-sanctioned L0 stub -> info nudge.
            info.append({"code": "spec_below_conformance_bar", "doc_id": pid, "maturity": raw,
                         "message": f"spec-projection '{pid}' is '{raw}'; the conformance bar is '{_CONFORMANCE_FLOOR}'+ (declared but not digest-anchored). Informational -- pass requires {_CONFORMANCE_FLOOR}+ unless it is a sanctioned L0 stub or local policy overrides."})
        # else: at/above its contract floor and (hash-checked+ OR a sanctioned L0 stub) -> conforming

    if loud:
        status = _gate_status(loud + info, mode=mode)  # loud findings may block under hard mode
    elif info:
        status = "warn"  # info-grade never blocks, even under hard mode
    else:
        status = "passed"
    # This gate does NO digest work itself -- it reads DECLARED maturity. Report its own
    # maturity as presence-only (honest); hash-checked is the bar it enforces, not a claim
    # about what it verified (that is local_integrity's job, and a canon-backed verifier's
    # for higher rungs).
    return {"status": status, "maturity": "presence-only", "findings": loud + info}


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


def _gate_git_discipline(repo: Path, *, mode: str) -> dict[str, Any]:
    """Slice-G git-discipline guardrail: classify the repo's refs against the
    neutral contract's ``pipeline_ref_classes`` registry and check the
    write-footprint / branch-naming invariants. Consumes the contract, does not
    reinvent it.

    Contract-absent degrade: when the installed ``consiliency_contract`` predates
    the git-discipline contract (< 0.4), this is a NEUTRAL no-op (``passed`` with
    a note), NOT a warning -- so existing governed scans are unaffected until the
    carrying contract ships. Unlike ``version_skew`` this gate honours ``hard``
    (``capped_warn=False``): the guardrail is a legitimate block-opt-in.
    """
    from . import git_discipline as gd

    registry = gd.load_ref_classes()
    if not gd.available(registry):
        return {
            "status": "passed",
            "maturity": "presence-only",
            "findings": [],
            "note": "installed consiliency_contract lacks the git-discipline contract (<0.4); guardrail latent",
        }
    assert registry is not None
    protocol = gd.load_protocol()
    facts = gd.gather_repo_ref_facts(repo)
    findings = gd.evaluate_git_discipline(
        current_branch=facts["current_branch"],
        dirty_paths=facts["dirty_paths"],
        local_branches=facts["local_branches"],
        registry=registry,
        protocol=protocol,
    )
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
