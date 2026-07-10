"""``phase-loop doctor`` — front-door adoptability report (IF-0-AHADOPT-2).

A NEW top-level command and a strict SUPERSET of ``repo-validate doctor``. It
REUSES the seed *patterns* — ``repo_validation.doctor_report()`` (tool/stack
probe) and ``install_status.build_install_status()`` (per-harness skill surface)
— and adds two things they lack:

1. **Install surfaces**, both of them: the wheel-bundled-skills path
   (``phase-loop run`` with no dotfiles) and the interactive-harness path
   (skills copied into ``~/.claude/skills`` &c.).
2. A **multi-registry BOM** (Bill of Materials): named consumer pins compared to
   the *live* npm + PyPI registry latest, each with a ``stale|current|unknown``
   verdict. The BOM degrades to ``unknown`` — never fails — when a registry is
   unreachable, and ``--fail-on-stale`` exits non-zero only on a ``stale`` verdict
   among the **gating** (repo-owned) targets.

DECOUPLE SL-1: this module pulls NO dotfiles-domain module
(``adoption_bundle`` / ``build_bundle`` / ``sync-skills``). It emits the checked-in
``phase-loop-doctor.v1`` schema and is metadata-only (no absolute paths).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import repo_validation
from .install_status import _assert_redacted, build_install_status

SCHEMA_ID = "phase-loop-doctor.v1"

# A fetcher maps a URL to the response body (str) or None on ANY failure — the
# offline-degrade seam and the injection point for the offline mock-registry test.
Fetcher = Callable[[str], Optional[str]]

# `phase-loop doctor` is a strict SUPERSET of `repo-validate doctor`: it probes
# every tool `repo_validation.doctor_report()` does (unlocks labels below) and
# ADDS the executor CLIs, and it folds doctor_report's `stack_hints` +
# `declared_contracts` into the payload. `_CORE_TOOL_UNLOCKS` labels the
# doctor_report tool set; unknown names fall back to the bare name.
_CORE_TOOL_UNLOCKS: dict[str, str] = {
    "git": "version control",
    "just": "just task runner (repo agent:* contracts)",
    "dagger": "dagger CI pipelines",
    "docker": "docker compose self-host (DEPLOY)",
    "node": "node / npx (governed-pipeline, DEPLOY)",
    "pnpm": "pnpm package install",
    "npm": "npm / npx package install",
    "yarn": "yarn package install",
    "bun": "bun runtime / package install",
    "uv": "uv tool install (primitive install path)",
    "python3": "python runtime",
    "cargo": "cargo (rust builds)",
}

# Executor CLIs added on top of the repo-validate tool set; `authed` is meaningful.
_EXECUTORS: tuple[tuple[str, str], ...] = (
    ("codex", "codex executor leg"),
    ("claude", "claude executor leg"),
    ("gemini", "gemini executor leg"),
    ("opencode", "opencode executor leg"),
    ("pi", "pi executor leg"),
)

# Best-effort auth heuristics: credential-file presence per executor CLI. Only a
# BOOLEAN is emitted (never a path), so the payload stays metadata-only.
_AUTH_HINTS: dict[str, tuple[str, ...]] = {
    "codex": ("~/.codex/auth.json",),
    "claude": ("~/.claude/.credentials.json", "~/.claude.json"),
    "gemini": ("~/.gemini/oauth_creds.json", "~/.gemini/antigravity/auth.json"),
    "opencode": ("~/.local/share/opencode/auth.json",),
    "pi": ("~/.pi/agent/auth.json",),
}


# --------------------------------------------------------------------------- #
# registry access (offline-degrading)
# --------------------------------------------------------------------------- #
def _default_fetch(url: str, timeout: float = 10.0) -> Optional[str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed hosts)
            return resp.read().decode("utf-8")
    except Exception:
        # Any failure (network down, 404, timeout) => None => verdict "unknown".
        return None


def _pypi_latest(name: str, fetch: Fetcher) -> Optional[str]:
    body = fetch(f"https://pypi.org/pypi/{name}/json")
    if not body:
        return None
    try:
        return str(json.loads(body)["info"]["version"])
    except Exception:
        return None


def _npm_latest(name: str, fetch: Fetcher) -> Optional[str]:
    body = fetch(f"https://registry.npmjs.org/{name.replace('/', '%2f')}")
    if not body:
        return None
    try:
        return str(json.loads(body)["dist-tags"]["latest"])
    except Exception:
        return None


def _verdict(pinned: Optional[str], latest: Optional[str]) -> str:
    if not pinned or not latest:
        return "unknown"
    try:
        from packaging.version import Version

        return "stale" if Version(latest) > Version(pinned) else "current"
    except Exception:
        return "unknown"


def _entry(
    target: str,
    ecosystem: str,
    pinned: Optional[str],
    latest: Optional[str],
    *,
    gating: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "target": target,
        "ecosystem": ecosystem,
        "pinned": pinned,
        "latest": latest,
        "verdict": _verdict(pinned, latest),
        "gating": gating,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# local pin resolution (all degrade to None when the source is not reachable)
# --------------------------------------------------------------------------- #
def _find_up(start: Path, name: str) -> Optional[Path]:
    start = start.resolve()
    for parent in (start, *start.parents):
        candidate = parent / name
        if candidate.exists():
            return candidate
    return None


# Version-shape guard: local pin sources feed the payload, so validate they look
# like a version (v?X.Y[.Z...]) and drop anything else to None — keeps arbitrary
# file content out of the metadata-only BOM (defense beyond the redactor prefixes).
_VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+[0-9A-Za-z.\-]*")


def _as_version(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not _VERSION_RE.fullmatch(raw):
        return None
    return raw[1:] if raw.startswith("v") else raw


def _release_pin(repo: Path) -> Optional[str]:
    pin_file = _find_up(repo, "RELEASE_PIN")
    if pin_file is None:
        return None
    return _as_version(pin_file.read_text(encoding="utf-8"))


def _contract_floor(repo: Path) -> Optional[str]:
    """The `consiliency-contract>=X` floor from phase-loop-runtime/pyproject.toml."""
    for rel in ("pyproject.toml", "phase-loop-runtime/pyproject.toml"):
        pyproj = _find_up(repo, rel) if "/" not in rel else (repo / rel)
        if pyproj is None or not pyproj.is_file():
            continue
        try:
            try:
                import tomllib
            except ModuleNotFoundError:  # py<3.11
                import tomli as tomllib  # type: ignore[no-redef]
            deps = tomllib.loads(pyproj.read_text(encoding="utf-8"))["project"]["dependencies"]
        except Exception:
            continue
        for dep in deps:
            if dep.replace(" ", "").startswith("consiliency-contract>="):
                floor = dep.split(">=", 1)[1]
                return floor.split(",", 1)[0].strip()
    return None


def _vendored_contract_version(repo: Path) -> Optional[str]:
    """The vendored `@consiliency/contract` version from .consiliency/manifest.json."""
    manifest = _find_up(repo, ".consiliency/manifest.json")
    if manifest is None:
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    version = data.get("contract_version") or data.get("adoption", {}).get("contract_version")
    return _as_version(str(version)) if version else None


# --------------------------------------------------------------------------- #
# BOM
# --------------------------------------------------------------------------- #
def build_bom(repo: Path, *, fetch: Optional[Fetcher] = None) -> list[dict[str, Any]]:
    """The named multi-registry BOM inventory v1.

    GATING (repo-owned, `--fail-on-stale` acts on these): the PyPI
    ``consiliency-contract`` floor and the install-script auto-track pin. The
    vendored npm mirrors and the mac-skills ref are REPORTED but not gated — their
    currency is owned by other repos'/vendoring processes, so failing this repo's
    doctor on their drift would be noise (it is surfaced, not enforced here).
    """
    fetch = fetch or _default_fetch
    return [
        _entry(
            "consiliency-contract",
            "pypi",
            _contract_floor(repo),
            _pypi_latest("consiliency-contract", fetch),
            gating=True,
            note="phase-loop-runtime dependency floor",
        ),
        _entry(
            "install-agent-harness.sh ref",
            "pypi(phase-loop-runtime)",
            _release_pin(repo),
            _pypi_latest("phase-loop-runtime", fetch),
            gating=True,
            note="auto-track install pin (RELEASE_PIN vs published runtime)",
        ),
        _entry(
            "@consiliency/contract",
            "npm",
            _vendored_contract_version(repo),
            _npm_latest("@consiliency/contract", fetch),
            gating=False,
            note="vendored contract (owned by consiliency-ingest; reported, not gated)",
        ),
        _entry(
            "@consiliency/canon-core",
            "npm",
            None,
            _npm_latest("@consiliency/canon-core", fetch),
            gating=False,
            note="no local pin in the primitive; reported for visibility",
        ),
        _entry(
            "mac-skills ref",
            "dotfiles",
            None,
            None,
            gating=False,
            note="owned by dotfiles; not resolvable from the decoupled primitive",
        ),
    ]


def stale_gating_targets(bom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in bom if e.get("gating") and e.get("verdict") == "stale"]


# --------------------------------------------------------------------------- #
# tools + install surfaces
# --------------------------------------------------------------------------- #
def _cli_authed(name: str, present: bool) -> Optional[bool]:
    if not present:
        return None
    for hint in _AUTH_HINTS.get(name, ()):  # boolean only — never the path
        if Path(hint).expanduser().exists():
            return True
    return False


def _tools_report(core_tools: dict[str, Optional[str]]) -> list[dict[str, Any]]:
    """Union of the repo-validate tool probe (converted to booleans — the raw
    values are absolute `shutil.which` paths, forbidden by the metadata-only
    redactor) and the executor CLIs."""
    out: list[dict[str, Any]] = []
    for name, path in core_tools.items():
        out.append(
            {
                "name": name,
                "present": path is not None,
                "authed": None,
                "unlocks": _CORE_TOOL_UNLOCKS.get(name, name),
            }
        )
    for name, unlocks in _EXECUTORS:
        present = shutil.which(name) is not None
        out.append(
            {
                "name": name,
                "present": present,
                "authed": _cli_authed(name, present),
                "unlocks": unlocks,
            }
        )
    return out


def _wheel_bundled_surface() -> dict[str, Any]:
    try:
        from importlib.resources import files

        bundle = files("phase_loop_runtime") / "skills_bundle"
        status = "present" if bundle.is_dir() else "missing"
    except Exception:
        status = "unknown"
    return {
        "surface": "wheel-bundled-skills",
        "status": status,
        "unlocks": "phase-loop run / dry-run with no dotfiles",
    }


def _interactive_surfaces(repo: Path) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    try:
        status = build_install_status(repo)
    except Exception:
        return surfaces
    for record in status.get("harnesses", ()):
        root = record.get("root_status")
        parity = record.get("skill_parity")
        if root == "missing":
            verdict = "missing"
        elif parity == "complete":
            verdict = "present"
        else:
            verdict = "partial"
        surfaces.append(
            {
                "surface": "interactive-harness-skills",
                "harness": record.get("harness"),
                "status": verdict,
            }
        )
    return surfaces


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _summary(tools: list[dict[str, Any]], bom: list[dict[str, Any]]) -> str:
    present = sum(1 for t in tools if t["present"])
    current = sum(1 for e in bom if e["verdict"] == "current")
    stale = sum(1 for e in bom if e["verdict"] == "stale")
    unknown = sum(1 for e in bom if e["verdict"] == "unknown")
    gating_stale = len(stale_gating_targets(bom))
    return (
        f"{present}/{len(tools)} tools present; "
        f"BOM {current} current / {stale} stale / {unknown} unknown "
        f"({gating_stale} gating-stale)"
    )


def build_doctor_report(
    repo: Path,
    *,
    fetch: Optional[Fetcher] = None,
    bom_fixture: Optional[Path] = None,
) -> dict[str, Any]:
    # Strict superset of `repo-validate doctor`: resolve the same root and reuse
    # its probe. `repo_validation` pulls no dotfiles-domain module (asserted by the
    # decouple test), so this stays on the decoupled import graph.
    root = repo_validation.find_repo_root(repo) or repo
    rv = repo_validation.doctor_report(root)
    tools = _tools_report(rv["tools"])  # type: ignore[arg-type]
    surfaces = [_wheel_bundled_surface(), *_interactive_surfaces(repo)]
    if bom_fixture is not None:
        bom = _load_bom_fixture(bom_fixture)
    else:
        bom = build_bom(root, fetch=fetch)
    report = {
        "schema": SCHEMA_ID,
        "summary": _summary(tools, bom),
        "tools": tools,
        # From repo_validation.doctor_report (superset). Metadata-only: filenames
        # and target/runner strings. `repo_root` is intentionally OMITTED — it is
        # an absolute path, which the metadata-only redactor forbids.
        "stack_hints": list(rv["stack_hints"]),  # type: ignore[arg-type]
        "declared_contracts": list(rv["declared_contracts"]),  # type: ignore[arg-type]
        "install_surfaces": surfaces,
        "bom": bom,
    }
    # Metadata-only guarantee: no absolute paths, no secrets. Reuses the same
    # redaction contract as phase-loop-install-status.v1.
    _assert_redacted(report)
    return report


def _load_bom_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "bom" in data:
        data = data["bom"]
    if not isinstance(data, list):
        raise ValueError(f"BOM fixture {path} must be a list or {{'bom': [...]}}")
    return data


def _print_doctor(report: dict[str, Any]) -> None:
    print("phase-loop doctor")
    print(f"  {report['summary']}")
    print("")
    print("Tools:")
    for tool in report["tools"]:
        authed = tool["authed"]
        auth_str = "" if authed is None else (" authed" if authed else " NOT authed")
        state = "present" if tool["present"] else "missing"
        print(f"  {tool['name']:<10} {state}{auth_str}   → {tool['unlocks']}")
    print("")
    print("Repo stack hints:")
    for hint in report.get("stack_hints", []):
        print(f"  {hint}")
    print("Declared agent contracts:")
    contracts = report.get("declared_contracts", [])
    if contracts:
        for entry in contracts:
            print(f"  {entry['target']:<12} {entry['runner']}")
    else:
        print("  none")
    print("")
    print("Install surfaces:")
    for surface in report["install_surfaces"]:
        harness = f" [{surface['harness']}]" if surface.get("harness") else ""
        print(f"  {surface['surface']}{harness}: {surface['status']}")
    print("")
    print("BOM (pin vs registry latest):")
    for entry in report["bom"]:
        gate = "gating" if entry["gating"] else "report"
        print(
            f"  [{entry['verdict']:<7}] {entry['target']:<32} "
            f"pinned={entry['pinned']} latest={entry['latest']} ({entry['ecosystem']}, {gate})"
        )


def run_doctor(
    *,
    repo: Path,
    as_json: bool,
    fail_on_stale: bool,
    bom_fixture: Optional[Path],
) -> int:
    report = build_doctor_report(repo, bom_fixture=bom_fixture)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor(report)
    if fail_on_stale:
        stale = stale_gating_targets(report["bom"])
        if stale:
            names = ", ".join(e["target"] for e in stale)
            # To stderr so `--json` stdout stays pure, machine-parseable JSON.
            print(f"FAIL: stale gating BOM target(s): {names}", file=sys.stderr, flush=True)
            return 1
    return 0
