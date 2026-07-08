"""CS-0.11 -- brownfield ingestion: shape-to-conform, then verify.

`phase-loop consiliency-ingest --repo <path>` looks at whether the repo
already has a `.consiliency/manifest.json` -- the single detection/consent
point the whole `.consiliency` stack shares (see
:func:`consiliency_layout.find_consiliency_manifest`) -- and picks one of two
disjoint branches:

* **SHAPE** (manifest absent): a brownfield repo hasn't been touched yet.
  This is CONSENT-GATED on an explicit ``--adopt``/``adopt=True`` flag -- an
  unflagged repo is a pure no-op, nothing is read beyond the one existence
  check and nothing is written. When adopted, this module delegates the base
  layout entirely to the CS-0.5 scaffolder (:func:`consiliency_scaffold.scaffold`)
  -- it never reimplements doc placement -- and then layers a CS-0.12
  (contract 0.2.0) adoption profile and a PROPOSED governed-set allowlist
  onto the manifest scaffold() just wrote, in the same first-writer pass.
  The proposal is conservative: only the paths the required-documents
  registry composed for the declared archetype(s) -- i.e. exactly the rows
  scaffold() itself declared in ``documents`` -- are proposed. Nothing
  ambiguous is auto-claimed, and the default ignore-set (scratchpads,
  other-harness namespaces such as `.phase-loop/`, `.claude/`, `.codex/`,
  ...) is never a candidate, defensively re-checked here even though the
  required-documents registry can never itself emit an ignored path.
* **VERIFY** (manifest present): every subsequent pass, including a pass
  over a hand-corrupted `.consiliency/`. This module NEVER rewrites; it runs
  the CS-0.6 L0 gates (:func:`consiliency_gates.scan_consiliency_gates`)
  as-is and additionally labels each declared document against the CS-0.12
  governance-scope decision (governed / foreign / present-nonconforming),
  again read-only. Detection is manifest-FILE-presence, not
  manifest-schema-validity, so a corrupted manifest is flagged (via the
  existing layout-validity gate) and never mistaken for "absent" -- it can
  never accidentally re-trigger the shape branch and overwrite a human's
  repair.

Governance-scope decision (CS-0.12 / contract 0.2.0): :func:`evaluate_governance_scope`
is a pure function over (adoption profile, governed_set, ignore_set, facet,
subject) implementing the ``governance_scope_scenario`` conformance surface
the vendored contract ships (``consiliency_contract``'s
``conformance/vectors/{adoption,governed-set,ignore-set,doc-label}-*.json``).
Branch order, kept in sync with those vectors:

1. no adoption profile -> ungoverned (``not-adopted``)
2. adoption profile present but ``adopted: false`` -> ungoverned (``adoption-declined``)
3. requested facet not in ``adopted_scope`` -> ungoverned (``out-of-scope-facet``)
4. path matches the ignore-set (default + repo-declared) -> ungoverned
   (``ignored``) -- this overrides the governed-set allowlist; it is the
   safety floor scratchpads and other-harness docs sit behind.
5. path matches no governed_set selector -> ungoverned (``undeclared-ungoverned``);
   a present-but-undeclared subject is additionally labeled ``foreign``.
6. otherwise -> governed (``declared``); an ``observed_label`` of
   ``present-nonconforming`` on the subject additionally warns without
   changing the governed verdict.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from consiliency_contract import load_registry

from .consiliency_gates import scan_consiliency_gates
from .consiliency_layout import (
    ARCHETYPE_IDS,
    contract_version_status_schema,
    find_consiliency_manifest,
    installed_contract_version,
    interface_declaration_schema,
    load_consiliency_manifest,
    manifest_path,
    manifest_schema,
)
from .consiliency_scaffold import ScaffoldResult, scaffold

#: The `adopted_scope` facets CS-0.6 actually gates today (presence,
#: local-integrity, layout-validity, version-skew all live under the
#: `.consiliency/` LAYOUT and are enforced by the L0 GATES). The contract's
#: adoptionProfile also allows `projections`/`cert`, but nothing in
#: agent-harness enforces those facets yet -- claiming them here would be
#: consent theater, so this module only ever declares scope it backs.
_ADOPTED_SCOPE_ENFORCED: tuple[str, ...] = ("layout", "gates")

#: Manifest document ids whose target artifact is machine-generated JSON with
#: a real vendored schema -- the only L0 surface honest enough to support a
#: content-conformance label. Prose docs are presence-only by design (see
#: consiliency_scaffold's module docstring): this module never judges
#: hand-authored markdown, so it never labels a prose doc nonconforming.
_SCHEMA_CHECKED_DOC_IDS: dict[str, Any] = {
    "contract-version-status": contract_version_status_schema,
    "interface-declaration": interface_declaration_schema,
}

_MESSAGES: dict[str, str] = {
    "governance.not_adopted": "Repo has no adoption profile; the document is ungoverned.",
    "governance.adoption_declined": "Adoption is explicitly declined (adopted:false); the document is ungoverned.",
    "governance.out_of_scope_facet": "The requested facet is not in adopted_scope; the document is ungoverned for this facet.",
    "governance.ignored": "Path matches the ignore-set; it is ungoverned and never ingested, overriding the governed_set match.",
    "governance.undeclared": "No governed_set selector matches the document; it is ungoverned.",
    "governance.foreign": "Present artifact is not in the governed set; labeled foreign (governed:false).",
    "governance.governed": "Document is declared in the governed set and within an adopted scope facet.",
    "governance.present_nonconforming": "Governed document is present but does not conform to its declared class.",
}


def _default_ignore_set() -> tuple[str, ...]:
    return tuple(load_registry("default_ignore_set").get("namespaces", ()))


def _path_ignored(path: str, ignore_set: Sequence[str]) -> bool:
    """Default-ignore-set matching rule (`consiliency_contract`'s registry):
    a metachar-free entry ending in `/` is a bare directory name and matches
    at ANY depth; anything containing a glob metacharacter is a glob
    evaluated against the whole repo-relative path."""
    parts = PurePosixPath(path).parts
    for entry in ignore_set:
        has_metachar = any(ch in entry for ch in "*?[")
        if not has_metachar and entry.endswith("/"):
            if entry.rstrip("/") in parts[:-1]:
                return True
        elif fnmatch.fnmatch(path, entry):
            return True
    return False


def _selector_matches(selector: Mapping[str, Any], subject: Mapping[str, Any]) -> bool:
    by = selector.get("by")
    value = str(selector.get("value") or "")
    if by == "path":
        return str(subject.get("path") or "") == value
    if by == "glob":
        return fnmatch.fnmatch(str(subject.get("path") or ""), value)
    if by == "class":
        return str(subject.get("class") or "") == value
    return False


def evaluate_governance_scope(
    *,
    adoption: Mapping[str, Any] | None,
    governed_set: Sequence[Mapping[str, Any]] = (),
    ignore_set: Sequence[str] = (),
    facet: str | None = None,
    subject: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure CS-0.12 governance-scope decision. See module docstring for the
    branch order this implements against the vendored contract's
    `governance_scope_scenario` conformance vectors."""

    def _decision(*, governed: bool, reason: str, code: str, status: str = "accepted", labels: tuple[str, ...] = ()) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "consiliency.conformance_decision.v1",
            "status": status,
            "maturity": "presence-only",
            "findings": [{"code": code, "severity": "warn" if status == "warn" else "info", "message": _MESSAGES[code]}],
            "governed": governed,
            "reason": reason,
        }
        if labels:
            result["labels"] = list(labels)
        return result

    if adoption is not None and adoption.get("adopted") is False:
        return _decision(governed=False, reason="adoption-declined", code="governance.adoption_declined")
    if not adoption or not adoption.get("adopted"):
        return _decision(governed=False, reason="not-adopted", code="governance.not_adopted")

    adopted_scope = tuple(adoption.get("adopted_scope") or ())
    if facet is not None and facet not in adopted_scope:
        return _decision(governed=False, reason="out-of-scope-facet", code="governance.out_of_scope_facet")

    if _path_ignored(str(subject.get("path") or ""), ignore_set):
        return _decision(governed=False, reason="ignored", code="governance.ignored")

    if not any(_selector_matches(selector, subject) for selector in governed_set):
        if subject.get("observed_label") == "foreign":
            return _decision(governed=False, reason="undeclared-ungoverned", code="governance.foreign", labels=("foreign",))
        return _decision(governed=False, reason="undeclared-ungoverned", code="governance.undeclared")

    if subject.get("observed_label") == "present-nonconforming":
        return _decision(
            governed=True, reason="declared", code="governance.present_nonconforming",
            status="warn", labels=("present-nonconforming",),
        )
    return _decision(governed=True, reason="declared", code="governance.governed")


def _primary_archetype(mode: str, archetypes: tuple[str, ...]) -> str:
    """The contract's `adoptionProfile.archetype` is singular (one enum
    value, including `baseline-only`), while `declaration.archetypes` is a
    set. When more than one archetype is declared, this picks the first in
    registry order -- the same precedence `compose_required_documents`
    already uses to resolve composition order -- as the profile's headline
    archetype. This is a judgment call: the manifest's own `declaration`
    remains the source of truth for the full set; `adoption.archetype` is
    only ever a single-value summary of it."""
    if mode == "baseline-only" or not archetypes:
        return "baseline-only"
    for candidate in ARCHETYPE_IDS:
        if candidate in archetypes:
            return candidate
    return archetypes[0]


def _propose_governed_set(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    ignore_set = _default_ignore_set()
    selectors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in manifest.get("documents", []):
        path = entry.get("path")
        if not path or path in seen:
            continue
        # Defensive: the required-documents registry can never itself emit
        # an ignored path, but a governed-set proposal must never claim one
        # even if that invariant ever drifts.
        if _path_ignored(str(path), ignore_set):
            continue
        seen.add(path)
        selectors.append({"by": "path", "value": path})
    return selectors


def _observe_document_label(repo: Path, entry: Mapping[str, Any]) -> str | None:
    """The only L0-honest content signal this module computes: whether a
    machine-generated JSON artifact (status.json / interfaces.json)
    validates against its own vendored schema. Prose docs are never judged
    -- this module doesn't invent a class-conformance detector for
    hand-authored markdown."""
    path = entry.get("path")
    if not path:
        return None
    file_path = repo / str(path)
    if not file_path.is_file():
        return None  # absence is the CS-0.6 presence gate's concern, not a conformance label.
    schema_loader = _SCHEMA_CHECKED_DOC_IDS.get(str(entry.get("id")))
    if schema_loader is None:
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "present-nonconforming"
    errors = list(Draft202012Validator(schema_loader()).iter_errors(payload))
    return "present-nonconforming" if errors else None


@dataclass(frozen=True)
class IngestResult:
    repo: Path
    mode: str  # "shape" | "verify" | "skipped"
    dry_run: bool
    adopted: bool
    manifest_path: Path
    scaffold: dict[str, Any] | None
    gate_scan: dict[str, Any] | None
    governed_set: tuple[dict[str, Any], ...]
    document_labels: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": str(self.repo),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "adopted": self.adopted,
            "manifest_path": str(self.manifest_path),
            "scaffold": self.scaffold,
            "gate_scan": self.gate_scan,
            "governed_set": list(self.governed_set),
            "document_labels": list(self.document_labels),
            "findings": list(self.findings),
        }


def ingest(
    repo: str | Path,
    *,
    adopt: bool = False,
    check_only: bool = False,
    mode: str = "baseline-only",
    archetypes: tuple[str, ...] = (),
    modifiers: tuple[str, ...] = (),
    repo_id: str | None = None,
    display_name: str | None = None,
    dry_run: bool = False,
) -> IngestResult:
    """Brownfield ingestion. See the module docstring for the SHAPE/VERIFY split.

    ``check_only`` DECOUPLES "run the conformance check" from "is this repo
    adopted". It is strictly read-only -- it NEVER shapes, so it ignores
    ``adopt``. On an adopted repo it is exactly the VERIFY pass. On an
    un-adopted repo (no ``.consiliency/manifest``) it does NOT return the silent
    green ``skipped`` no-op that a plain (non-check) unflagged pass returns;
    instead it returns an explicit, honest ``mode == "not-adopted"`` result so a
    pre-PR actor is not misled into reading a no-op as a pass. The CLI maps that
    mode to a distinct non-zero exit (see ``_consiliency_ingest_command``).
    """
    repo = Path(repo)
    existing = find_consiliency_manifest(repo)
    if existing is not None:
        return _verify(repo, existing_manifest_path=existing, dry_run=dry_run)

    target_manifest_path = manifest_path(repo)
    if check_only:
        # Honest not-adopted signal: there is genuinely nothing to verify, and
        # saying so out loud is the point -- a no-op is NOT a pass.
        return IngestResult(
            repo=repo, mode="not-adopted", dry_run=dry_run, adopted=False,
            manifest_path=target_manifest_path, scaffold=None, gate_scan=None,
            governed_set=(), document_labels=(),
            findings=(
                {
                    "code": "adoption.not_adopted",
                    "severity": "info",
                    "message": (
                        "No .consiliency/manifest present: this repo is NOT ADOPTED, "
                        "so there is nothing to verify. --check-only reports this "
                        "explicitly (distinct from a passing verify) so a no-op is "
                        "never mistaken for a pass."
                    ),
                },
            ),
        )
    if not adopt:
        return IngestResult(
            repo=repo, mode="skipped", dry_run=dry_run, adopted=False,
            manifest_path=target_manifest_path, scaffold=None, gate_scan=None,
            governed_set=(), document_labels=(),
            findings=(
                {
                    "code": "adoption.not_requested",
                    "severity": "info",
                    "message": "No .consiliency/manifest present and --adopt was not passed; repo left untouched.",
                },
            ),
        )
    return _shape(
        repo, mode=mode, archetypes=archetypes, modifiers=modifiers,
        repo_id=repo_id, display_name=display_name, dry_run=dry_run,
    )


def _shape(
    repo: Path,
    *,
    mode: str,
    archetypes: tuple[str, ...],
    modifiers: tuple[str, ...],
    repo_id: str | None,
    display_name: str | None,
    dry_run: bool,
) -> IngestResult:
    scaffold_result: ScaffoldResult = scaffold(
        repo, mode=mode, archetypes=archetypes, modifiers=modifiers,
        repo_id=repo_id, display_name=display_name, dry_run=dry_run,
    )
    target_manifest_path = scaffold_result.manifest_path

    if dry_run:
        # Mirror scaffold()'s own dry-run contract: nothing on disk, report
        # what WOULD happen using the same rows the (unwritten) manifest
        # would have declared.
        placeholder_docs = [{"path": p} for p in (*scaffold_result.created_paths, *scaffold_result.referenced_paths)]
        proposed = _propose_governed_set({"documents": placeholder_docs})
        return IngestResult(
            repo=repo, mode="shape", dry_run=True, adopted=True,
            manifest_path=target_manifest_path, scaffold=scaffold_result.to_json(),
            gate_scan=None, governed_set=tuple(proposed), document_labels=(), findings=(),
        )

    manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    proposed = _propose_governed_set(manifest)
    manifest["adoption"] = {
        "adopted": True,
        "contract_version": installed_contract_version(),
        "archetype": _primary_archetype(mode, archetypes),
        "adopted_scope": list(_ADOPTED_SCOPE_ENFORCED),
    }
    manifest["governed_set"] = proposed
    Draft202012Validator(manifest_schema()).validate(manifest)
    target_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    gate_scan = scan_consiliency_gates(repo)
    return IngestResult(
        repo=repo, mode="shape", dry_run=False, adopted=True,
        manifest_path=target_manifest_path, scaffold=scaffold_result.to_json(),
        gate_scan=gate_scan, governed_set=tuple(proposed), document_labels=(), findings=(),
    )


def _verify(repo: Path, *, existing_manifest_path: Path, dry_run: bool) -> IngestResult:
    gate_scan = scan_consiliency_gates(repo)
    manifest = load_consiliency_manifest(repo)
    document_labels: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    governed_set: tuple[dict[str, Any], ...] = ()
    adopted = False

    if manifest is not None:
        governed_set = tuple(manifest.get("governed_set") or ())
        adoption = manifest.get("adoption")
        adopted = bool(adoption and adoption.get("adopted"))
        ignore_set = _default_ignore_set()
        for entry in manifest.get("documents", []):
            path = entry.get("path")
            if not path:
                continue
            observed_label = _observe_document_label(repo, entry)
            subject = {"kind": "doc", "path": path, "class": entry.get("class"), "observed_label": observed_label}
            decision = evaluate_governance_scope(
                adoption=adoption, governed_set=list(governed_set),
                ignore_set=ignore_set, facet="layout", subject=subject,
            )
            document_labels.append({"doc_id": entry.get("id"), "path": path, **decision})
            if decision["status"] == "warn":
                findings.append(
                    {
                        "doc_id": entry.get("id"),
                        "path": path,
                        "code": decision["findings"][0]["code"],
                        "message": decision["findings"][0]["message"],
                    }
                )

    return IngestResult(
        repo=repo, mode="verify", dry_run=dry_run, adopted=adopted,
        manifest_path=existing_manifest_path, scaffold=None, gate_scan=gate_scan,
        governed_set=governed_set, document_labels=tuple(document_labels), findings=tuple(findings),
    )
