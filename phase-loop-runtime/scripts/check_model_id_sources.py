#!/usr/bin/env python3
"""Guard: no scattered/duplicated hardcoded model IDs in phase_loop_runtime source.

The rule this enforces ("reference the central constant, don't inline the model
ID"). A recent bug duplicated a literal ``"gpt-5.5"`` in ``panel_invoker`` fallbacks
instead of referencing its own ``DEFAULT_LEG_MODELS`` single-source dict; nothing
caught it. This script does.

HYBRID design: a small registry-file allowlist (files whose *job* is to enumerate
model<->tier/lane mappings) plus a per-line ``# model-id-source: <reason>`` escape
marker for the handful of sanctioned single-source constant definitions that live
outside a registry file. Anything else that pins a concrete model ID in a string
literal fails the build.

String detection is ``tokenize``-based, so ``#`` comments are excluded for free,
and module/class/function docstrings are skipped via AST — a model ID that appears
only as a prose *example* inside a docstring (``{"claude": "claude-sonnet-5"}``) is
NOT a value-pin and must not trip the guard.

Pure stdlib. ``python3 scripts/check_model_id_sources.py`` -> exit 0 clean, 1 dirty.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

# ---------------------------------------------------------------------------
# What counts as a concrete model ID. These match the fleet's real API-style
# ids. Deliberately NOT matched: the human-readable "Gemini 3.1 Pro (High)"
# display label (it is not an API id). API-style ``gemini-<n>`` IS matched.
# ---------------------------------------------------------------------------
MODEL_ID_REGEX = re.compile(
    r"""
    \b(
        gpt-[0-9]\.[0-9]+(?:-sol|-terra|-luna|-mini)?   # gpt-5.6-sol, gpt-5.6-mini
      | claude-(?:opus|sonnet|haiku)-[0-9]              # claude-opus-4-8, claude-sonnet-5
      | claude-fable-[0-9]                               # claude-fable-5
      | gemini-[0-9]                                     # gemini-3, gemini API-style ids
      | o[0-9]-mini                                      # o3-mini
    )
    """,
    re.VERBOSE,
)

# Trailing sanctioned-edge escape marker (in a ``#`` comment on the same line).
MARKER = "model-id-source:"

# ---------------------------------------------------------------------------
# REGISTRY ALLOWLIST — files whose job is to enumerate model<->tier/lane
# mappings. A concrete model ID in one of these is expected and allowed. Paths
# are relative to the phase-loop-runtime package root (the dir holding ``src/``).
#
# CRITERION: only files that pin model IDs in actual CODE (dict/tuple/Seat
# literals the tokenizer sees as STRING tokens) belong here — NOT files that
# merely mention IDs in comments or docstrings. The tokenizer already ignores
# comment/docstring text, so allowlisting a comment-only file is dead weight and
# would silently exempt a FUTURE real code-level hardcode in it. Each entry below
# was verified (with the file dropped from this set) to flag >=1 code-level ID;
# advisor_board/{config,matrix,schema,standin,validation}.py were checked and
# had IDs only in comments/docstrings, so they are deliberately NOT listed.
# ---------------------------------------------------------------------------
REGISTRY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "src/phase_loop_runtime/advisor_board/fixtures.py",
        "src/phase_loop_runtime/advisor_board/presets.py",
        "src/phase_loop_runtime/advisor_board/registries.py",
        "src/phase_loop_runtime/advisor_board/resolver.py",
        "src/phase_loop_runtime/profiles.py",
    }
)

# Directory the source tree lives under (package root == this script's parent's parent).
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOT = PACKAGE_ROOT / "src" / "phase_loop_runtime"


class Violation:
    """One offending value-pin: file (rel path), 1-based line, source snippet."""

    __slots__ = ("rel_path", "lineno", "snippet")

    def __init__(self, rel_path: str, lineno: int, snippet: str) -> None:
        self.rel_path = rel_path
        self.lineno = lineno
        self.snippet = snippet

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Violation({self.rel_path}:{self.lineno})"


def _docstring_linenos(source: str) -> set[int]:
    """Line numbers occupied by module/class/function docstrings.

    A docstring is a bare string expression that is the first statement of a
    module, class, or function body. Model IDs inside one are prose examples,
    not value-pins, so those physical lines are excluded from string scanning.
    """
    lines: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = getattr(first.value, "end_lineno", first.value.lineno)
            for ln in range(first.value.lineno, end + 1):
                lines.add(ln)
    return lines


def check_source(source: str, rel_path: str) -> list[Violation]:
    """Return value-pin violations for one file's source text.

    A violation is a concrete model ID appearing inside a NON-docstring string
    literal, in a file that is neither in the registry allowlist nor marked with
    ``# model-id-source:`` on that physical line.
    """
    if rel_path in REGISTRY_ALLOWLIST:
        return []

    physical_lines = source.splitlines()
    docstring_lines = _docstring_linenos(source)
    violations: list[Violation] = []
    seen: set[int] = set()

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A file we cannot tokenize can't be safely scanned; treat as clean
        # rather than fail-closed on unrelated syntax quirks. (The real tree
        # always tokenizes; this only guards pathological synthetic inputs.)
        return []

    for tok in tokens:
        if tok.type != tokenize.STRING:
            continue  # COMMENT tokens (and everything else) are excluded here.
        if not MODEL_ID_REGEX.search(tok.string):
            continue
        lineno = tok.start[0]
        # Skip docstrings: model ID there is a prose example, not a value-pin.
        if any(ln in docstring_lines for ln in range(tok.start[0], tok.end[0] + 1)):
            continue
        physical = physical_lines[lineno - 1] if 0 < lineno <= len(physical_lines) else ""
        if MARKER in physical:
            continue  # sanctioned edge, explicitly marked.
        if lineno in seen:
            continue
        seen.add(lineno)
        violations.append(Violation(rel_path, lineno, physical.strip()))

    return violations


def iter_source_files(scan_root: Path) -> list[Path]:
    """Python source files under ``scan_root``, excluding tests/build/cache."""
    files: list[Path] = []
    for path in sorted(scan_root.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts or "build" in parts or "tests" in parts:
            continue
        files.append(path)
    return files


def scan_tree(scan_root: Path = SCAN_ROOT, package_root: Path = PACKAGE_ROOT) -> list[Violation]:
    """Scan the real source tree; return all value-pin violations."""
    all_violations: list[Violation] = []
    for path in iter_source_files(scan_root):
        rel = path.relative_to(package_root).as_posix()
        source = path.read_text(encoding="utf-8")
        all_violations.extend(check_source(source, rel))
    return all_violations


def main(argv: list[str] | None = None) -> int:
    violations = scan_tree()
    if not violations:
        return 0
    for v in violations:
        sys.stderr.write(f"{v.rel_path}:{v.lineno}: {v.snippet}\n")
    sys.stderr.write(
        "\nconcrete model ID outside a registry file and not marked — reference the "
        "central constant (e.g. DEFAULT_LEG_MODELS) or add a trailing "
        "`# model-id-source: <reason>` if this is a sanctioned edge.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
