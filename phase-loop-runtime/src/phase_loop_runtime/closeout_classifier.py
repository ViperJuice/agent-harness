"""GATE (roadmap v40) — sensitivity classifier for beyond-ownership dirty paths.

`classify_unowned_path(repo_relpath)` maps a repo-relative path to a
``SensitivityVerdict`` whose ``sensitivity_class`` is a member of
``models.SENSITIVITY_CLASSES``. The graduated closeout gate auto-commits a
verified beyond-ownership path only when its verdict is ``safe``; everything else
blocks (deny-by-default).

Precedence is load-bearing and deny-by-default:
  1. UNSAFE-specific patterns first — secrets, lockfiles, CI config. These must win
     over any broad SAFE rule (e.g. a ``.github/workflows/*.yml`` is CI, not docs).
  2. tests → ``source`` (UNSAFE). Test paths only ever earn owned status via
     structural sibling matching upstream; a test reaching this classifier failed
     that and must not auto-commit.
  3. narrow SAFE rules — plans, handoffs, docs, and a *tight* config_nonsource
     allowlist (never a ``.toml``/``.yaml``/``.json`` suffix rule).
  4. fall through → ``source`` (UNSAFE). Unmatched is never SAFE.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .models import SAFE_SENSITIVITY_CLASSES


@dataclass(frozen=True)
class SensitivityVerdict:
    sensitivity_class: str
    safe: bool


# Tight allowlists — membership, not broad suffix rules.
_CONFIG_NONSOURCE_NAMES = frozenset(
    {".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".npmrc", ".prettierrc"}
)
_CONFIG_NONSOURCE_SUFFIXES = frozenset({".cfg", ".ini"})

_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".crt", ".keystore", ".jks"})
_LOCKFILE_NAMES = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        "cargo.lock",
        "go.sum",
        "gemfile.lock",
        "composer.lock",
        "requirements.txt",
    }
)
# Bare suffix → docs only for unambiguous documentation formats. A `.txt` is NOT
# auto-docs (e.g. src/foo.txt is source-adjacent); it is docs only under docs/.
_DOC_SUFFIXES = frozenset({".md", ".rst"})


def _verdict(sensitivity_class: str) -> SensitivityVerdict:
    return SensitivityVerdict(
        sensitivity_class=sensitivity_class,
        safe=sensitivity_class in SAFE_SENSITIVITY_CLASSES,
    )


def classify_unowned_path(repo_relpath: str) -> SensitivityVerdict:
    raw = (repo_relpath or "").strip()
    # Normalize: strip leading "./", lowercase for matching.
    norm = raw[2:] if raw.startswith("./") else raw
    lower = norm.lower()
    posix = PurePosixPath(lower)
    name = posix.name
    suffix = posix.suffix
    parts = posix.parts
    slashed = "/" + lower  # so "/tests/" infix matches a leading "tests/" too

    # --- 1. UNSAFE-specific patterns first (precedence) ---
    # secrets
    if (
        name == ".env"
        or name.startswith(".env.")
        or suffix in _SECRET_SUFFIXES
        or "secrets" in parts
    ):
        return _verdict("secrets")
    # lockfiles
    if name in _LOCKFILE_NAMES or name.endswith(".lock") or name.endswith("-lock.json"):
        return _verdict("lockfile")
    # CI config
    if (
        any(part in {".github", ".gitlab", ".circleci", ".gitea"} for part in parts)
        or lower.startswith("ci/")
        or "/workflows/" in slashed
    ):
        return _verdict("ci")
    # tests → source (UNSAFE) — GATE decision (see plan/IF-0-GATE-1)
    if (
        "/tests/" in slashed
        or "__tests__" in parts
        or "__fixtures__" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    ):
        return _verdict("source")

    # --- 2. narrow SAFE rules ---
    if lower.startswith("plans/"):
        return _verdict("plans")
    if ".dev-skills/handoffs/" in slashed:
        return _verdict("handoffs")
    if "/docs/" in slashed or name == "readme.md" or suffix in _DOC_SUFFIXES:
        return _verdict("docs")
    if name in _CONFIG_NONSOURCE_NAMES or suffix in _CONFIG_NONSOURCE_SUFFIXES:
        return _verdict("config_nonsource")

    # --- 3. deny-by-default: everything else (incl. .py/.toml/.yaml/.json/.sh) ---
    return _verdict("source")
