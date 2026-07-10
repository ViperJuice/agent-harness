"""Tests for the model-id-source guard (scripts/check_model_id_sources.py).

Proves the guard both PASSES on the real tree and CATCHES a planted violation —
a guard that only ever passes is worthless. Loads the script by file path
(it lives under scripts/, not the importable package).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_model_id_sources.py"

# The guard is a REPO-SOURCE check; `scripts/` is not packaged into the wheel, so
# in the standalone-from-wheel clean-room gate the script is absent. Skip there —
# scanning an installed package for repo-source hardcodes is meaningless.
pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="repo-only guard script (scripts/ not in the wheel); irrelevant standalone-from-wheel",
)


def _load_guard():
    spec = importlib.util.spec_from_file_location("check_model_id_sources", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard() if _SCRIPT.exists() else None


def test_guard_passes_on_real_tree() -> None:
    """The committed source tree must be clean (all sanctioned edges marked)."""
    violations = guard.scan_tree()
    assert violations == [], "\n".join(
        f"{v.rel_path}:{v.lineno}: {v.snippet}" for v in violations
    )


def test_main_exits_zero_on_real_tree() -> None:
    assert guard.main() == 0


def test_catches_unmarked_literal_in_non_registry_file() -> None:
    """A concrete model ID pinned in a string, unmarked, in a non-registry file -> flagged."""
    source = 'FALLBACK = "gpt-5.6-sol"\n'
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert len(violations) == 1
    assert violations[0].lineno == 1
    assert "gpt-5.6-sol" in violations[0].snippet


def test_scan_tree_catches_planted_file_end_to_end(tmp_path: Path) -> None:
    """End-to-end: a planted .py with an unmarked literal inside a scanned tree is flagged.

    Exercises ``scan_tree()`` — the exact function the CLI/CI ``main()`` calls —
    against a synthetic package root, not just the lower-level ``check_source``.
    """
    pkg_root = tmp_path
    scan_root = pkg_root / "src" / "phase_loop_runtime"
    scan_root.mkdir(parents=True)
    (scan_root / "planted_module.py").write_text(
        'FALLBACK = "gpt-5.6-sol"\n', encoding="utf-8"
    )
    violations = guard.scan_tree(scan_root=scan_root, package_root=pkg_root)
    assert len(violations) == 1
    assert violations[0].rel_path == "src/phase_loop_runtime/planted_module.py"
    assert "gpt-5.6-sol" in violations[0].snippet


def test_scan_tree_ignores_marked_planted_file_end_to_end(tmp_path: Path) -> None:
    """The same planted file WITH a trailing marker passes the end-to-end scan."""
    pkg_root = tmp_path
    scan_root = pkg_root / "src" / "phase_loop_runtime"
    scan_root.mkdir(parents=True)
    (scan_root / "planted_module.py").write_text(
        'FALLBACK = "gpt-5.6-sol"  # model-id-source: sanctioned edge\n', encoding="utf-8"
    )
    assert guard.scan_tree(scan_root=scan_root, package_root=pkg_root) == []


def test_marker_suppresses_the_violation() -> None:
    """The same literal WITH a trailing marker is a sanctioned edge -> not flagged."""
    source = 'FALLBACK = "gpt-5.6-sol"  # model-id-source: sanctioned edge\n'
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert violations == []


def test_registry_allowlist_suppresses_the_violation() -> None:
    """An unmarked literal in a registry-allowlist file is allowed."""
    source = 'MODELS = ["claude-opus-4-8", "gpt-5.6-sol"]\n'
    violations = guard.check_source(source, "src/phase_loop_runtime/profiles.py")
    assert violations == []


def test_comment_only_model_id_is_ignored() -> None:
    """A model ID that appears only in a `#` comment is prose, not a value-pin."""
    source = "X = 1  # the default is claude-opus-4-8 for every harness\n"
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert violations == []


def test_docstring_model_id_is_ignored() -> None:
    """A model ID inside a docstring is a prose example, not a value-pin."""
    source = (
        "def invoke():\n"
        '    """Override with {"claude": "claude-sonnet-5"} to pin the seat."""\n'
        "    return None\n"
    )
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert violations == []


def test_gemini_display_label_is_not_matched() -> None:
    """The human-readable 'Gemini 3.1 Pro' label is not an API id -> not matched."""
    source = 'GEMINI = "Gemini 3.1 Pro (High)"\n'
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert violations == []


def test_gemini_api_style_id_is_matched() -> None:
    """An API-style gemini-<n> id IS a value-pin and must be caught."""
    source = 'GEMINI = "gemini-3"\n'
    violations = guard.check_source(source, "src/phase_loop_runtime/some_module.py")
    assert len(violations) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
