from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePath
from typing import Any

from .baml_modular import parse_baml_response
from .plan_manifest import read_manifest


SOURCE_AUTHORITY_CONTRACT = Path("docs/dotfiles-source-authority-contract.md")
VISIBILITY_CONTRACT = Path("docs/dotfiles-visibility-contract.md")
C4_DOCUMENT = Path("docs/c4/phase-loop-runtime-c4-document.md")
TASK_CATALOG = Path("docs/tasks/dotfiles-task-catalog.md")
RUNTIME_PROJECTION_SURFACE = "phase-loop status --runtime-projection --json"
BAML_SCHEMA_ROOT = Path("vendor/phase-loop-runtime/baml_src")
ADOPTION_BUNDLE_PATH = Path("docs/adoption/dotfiles-adoption-bundle.json")


def generate_adoption_bundle(
    repo: Path,
    generated_at: str | None = None,
    operating_mode: str = "standalone",
) -> dict[str, object]:
    root = repo.resolve()
    payload: dict[str, object] = {
        "source_roots": _source_roots(root),
        "schema_refs": _schema_refs(root),
        "plan_refs": _plan_refs(root),
        "c4_document_refs": _c4_document_refs(root),
        "task_catalog_refs": _task_catalog_refs(root),
        "operating_mode": operating_mode,
        "redacted_metadata_ref": RUNTIME_PROJECTION_SURFACE,
        "visibility_contract_ref": str(VISIBILITY_CONTRACT),
        "version": "v1",
        "generated_at": generated_at or _stable_generated_at(root),
    }
    parse_baml_response("DotfilesAdoptionManifest", json.dumps(payload, sort_keys=True))
    return payload


def stable_json_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def adoption_bundle_status(repo: Path) -> dict[str, object]:
    root = repo.resolve()
    bundle_path = root / ADOPTION_BUNDLE_PATH
    bundle = _load_committed_bundle(bundle_path)
    current_refs = _schema_refs(root)
    bundled_refs = bundle.get("schema_refs")
    if not isinstance(bundled_refs, list):
        raise ValueError("adoption bundle schema_refs must be a list")
    stale_refs = _stale_schema_refs(bundled_refs, current_refs)
    return {
        "status": "stale" if stale_refs else "fresh",
        "bundle": str(ADOPTION_BUNDLE_PATH),
        "stale_refs": stale_refs,
        "schema_refs": current_refs,
    }


def refresh_adoption_bundle(repo: Path) -> dict[str, object]:
    root = repo.resolve()
    bundle_path = root / ADOPTION_BUNDLE_PATH
    bundle = _load_committed_bundle(bundle_path)
    generated = generate_adoption_bundle(
        root,
        generated_at=str(bundle.get("generated_at") or ""),
        operating_mode=str(bundle.get("operating_mode") or "standalone"),
    )
    refreshed = stable_json_bytes(generated) != bundle_path.read_bytes()
    if refreshed:
        bundle_path.write_bytes(stable_json_bytes(generated))
    return {
        "status": "fresh",
        "bundle": str(ADOPTION_BUNDLE_PATH),
        "refreshed": refreshed,
    }


def _load_committed_bundle(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"adoption bundle not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("adoption bundle must be a JSON object")
    parse_baml_response("DotfilesAdoptionManifest", json.dumps(payload, sort_keys=True))
    return payload


def _stale_schema_refs(bundled_refs: list[object], current_refs: list[dict[str, str]]) -> list[dict[str, str]]:
    current_by_path = {ref["source_path"]: ref["digest"] for ref in current_refs}
    bundled_by_path: dict[str, str] = {}
    for ref in bundled_refs:
        if not isinstance(ref, dict):
            raise ValueError("adoption bundle schema_refs entries must be objects")
        source_path = ref.get("source_path")
        digest = ref.get("digest")
        if not isinstance(source_path, str) or not isinstance(digest, str):
            raise ValueError("adoption bundle schema_refs entries require source_path and digest")
        bundled_by_path[source_path] = digest
    stale: list[dict[str, str]] = []
    for source_path, current_digest in current_by_path.items():
        bundled_digest = bundled_by_path.get(source_path)
        if bundled_digest != current_digest:
            stale.append(
                {
                    "source_path": source_path,
                    "bundle_digest": bundled_digest or "missing",
                    "current_digest": current_digest,
                }
            )
    for source_path, bundled_digest in bundled_by_path.items():
        if source_path not in current_by_path:
            stale.append(
                {
                    "source_path": source_path,
                    "bundle_digest": bundled_digest,
                    "current_digest": "missing",
                }
            )
    return sorted(stale, key=lambda item: item["source_path"])


def _source_roots(repo: Path) -> list[dict[str, str]]:
    rows = _contract_rows(repo / SOURCE_AUTHORITY_CONTRACT)
    roots = [
        {
            "path_glob": row["path_glob"],
            "classification": row["classification"],
            "owner": row["owner"],
            "ingestion_policy": row["ingestion_policy"],
        }
        for row in rows
        if row.get("classification") == "authority"
    ]
    return sorted(roots, key=lambda item: item["path_glob"])


def _schema_refs(repo: Path) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for path in sorted((repo / BAML_SCHEMA_ROOT).glob("*.baml")):
        source_path = _repo_relative(repo, path)
        refs.append(
            {
                "class_name": _schema_ref_name(path),
                "source_path": source_path,
                "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            }
        )
    return refs


def _plan_refs(repo: Path) -> list[dict[str, str]]:
    try:
        manifest = read_manifest(repo)
    except (OSError, ValueError):
        return []
    refs: list[dict[str, str]] = []
    for entry in manifest.plans:
        path = PurePath(entry.file)
        if path.is_absolute() or ".." in path.parts:
            continue
        plan_path = repo / Path(entry.file)
        if not plan_path.is_file():
            continue
        refs.append(
            {
                "slug": entry.slug,
                "type": entry.type,
                "file": entry.file,
                "digest": f"sha256:{hashlib.sha256(plan_path.read_bytes()).hexdigest()}",
                "status": entry.status,
            }
        )
    return sorted(refs, key=lambda item: (item["type"], item["slug"], item["file"]))


def _c4_document_refs(repo: Path) -> list[dict[str, str]]:
    document = (repo / C4_DOCUMENT).read_text(encoding="utf-8")
    anchors: list[dict[str, str]] = []
    for block in _section_after(document, "Anchors").strip().split("\n- "):
        fields = _bulleted_mapping(block.removeprefix("- ").strip())
        if not fields:
            continue
        anchors.append(
            {
                "title": fields["title"],
                "source_path": fields["source_path"],
                "anchor": fields["id"],
            }
        )
    return sorted(anchors, key=lambda item: (item["source_path"], item["anchor"]))


def _task_catalog_refs(repo: Path) -> list[dict[str, str]]:
    catalog = (repo / TASK_CATALOG).read_text(encoding="utf-8")
    audiences = re.findall(r"^- ([a-z_]+)$", _section_after(catalog, "Audiences"), re.MULTILINE)
    return [
        {
            "catalog_id": f"dotfiles-task-catalog-{audience}",
            "source_path": str(TASK_CATALOG),
            "audience": audience,
        }
        for audience in sorted(audiences)
    ]


def _contract_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        rows.append(
            {
                "path_glob": cells[0].strip("`"),
                "classification": cells[1],
                "owner": cells[2],
                "ingestion_policy": cells[3],
            }
        )
    return rows


def _schema_ref_name(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    classes = re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", text, re.MULTILINE)
    exported = [name for name in classes if name.startswith("Dotfiles") or name == "PhaseLoopCloseoutV1"]
    return exported[-1] if exported else path.stem


def _section_after(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.index(marker) + len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        return text[start:]
    return text[start:next_heading]


def _bulleted_mapping(section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"\s*(?:- )?([^:]+):\s*(.*)", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _stable_generated_at(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%cI", "--"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "1970-01-01T00:00:00Z"
    value = result.stdout.strip()
    return value.replace("+00:00", "Z") if value else "1970-01-01T00:00:00Z"


def _repo_relative(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo).as_posix()
