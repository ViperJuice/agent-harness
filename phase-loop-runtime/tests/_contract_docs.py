"""TESTDECOUPLE SL-2: resolve the runtime's bundled contract docs.

Runtime-core tests read the runtime's OWN contract docs (e.g.
``docs/phase-loop/substrate-soak-*.md``). In dotfiles those live under the
top-level ``docs/`` tree; in the extracted ``agent-harness`` layout there is no
such tree, so they travel inside the wheel as the ``_contract_docs`` package-data
(synced from the canonical sources by ``scripts/sync_runtime_package_data.py``).

Core tests resolve them HERE via ``importlib.resources`` instead of
``Path(__file__).resolve().parents[3] / "docs" / ...`` so they pass standalone.

Mapping (canonical dotfiles path -> bundled relpath under _contract_docs/):
    docs/phase-loop/<name>.md                      -> phase-loop/<name>.md
    docs/runtime/<name>.md                         -> runtime/<name>.md
    vendor/phase-loop-runtime/protocol/protocol.md -> phase-loop/protocol.md
        (the FULL canonical protocol doc, not the shared/phase-loop/protocol.md stub)

The runtime's OWN evidence-audit calibration fixtures travel the same way under
``_test_fixtures`` and resolve via ``fixture_path()``.
"""
from __future__ import annotations

from importlib.resources import files

_ROOT = files("phase_loop_runtime") / "_contract_docs"
_FIXTURES = files("phase_loop_runtime") / "_test_fixtures"


def fixture_path(*parts: str):
    """Return a Traversable for a bundled test fixture, e.g. ``fixture_path(
    "evidence-audit-calibration", "known-fake", "fake-uniform-scores", "scores.json")``.
    Anchors on the installed package (like ``contract_doc``) so it resolves both
    in-tree and standalone."""
    node = _FIXTURES
    for part in parts:
        node = node / part
    return node


def contract_doc(*parts: str):
    """Return a Traversable for a bundled contract doc, e.g.
    ``contract_doc("phase-loop", "substrate-soak-report.md")``. Works both in-tree
    (resolves the source-tree ``_contract_docs``) and standalone (resolves the
    wheel's package-data) because ``importlib.resources.files`` anchors on the
    installed/importable ``phase_loop_runtime`` package, not on ``parents[3]``."""
    node = _ROOT
    for part in parts:
        node = node / part
    return node


def contract_doc_text(*parts: str, encoding: str = "utf-8") -> str:
    """Read a bundled contract doc as text."""
    return contract_doc(*parts).read_text(encoding=encoding)
