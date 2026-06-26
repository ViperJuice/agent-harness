"""TESTDECOUPLE SL-0.1: cover the dotfiles tree detector + the skip hook.

Two properties:
  1. ``dotfiles_tree_present()`` / ``dotfiles_root()`` report PRESENT when the
     ``claude-config/`` + ``bootstrap.sh`` markers exist above the package, and
     ABSENT when they do not (simulated by relocating the resolved file under a
     marker-less tree).
  2. A ``dotfiles_integration``-marked item is collected-then-SKIPPED when the
     helper reports absent — i.e. the ``conftest.py``
     ``pytest_collection_modifyitems`` hook is wired to the helper. The PRESENT
     case is covered by the in-tree full-suite run, where every integration test
     actually executes (this suite runs under the dotfiles checkout).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import _dotfiles_tree


@pytest.mark.dotfiles_integration
def test_present_when_markers_above_package():
    # In-tree this suite runs under the dotfiles checkout, so the markers exist.
    # Marked dotfiles_integration: standalone (no tree) this assertion cannot hold,
    # so the conftest hook skips it there. The absent-case tests below DO run
    # standalone (they assert the helper reports absent), which is the point.
    root = _dotfiles_tree.dotfiles_root()
    assert root is not None
    assert (root / "claude-config").is_dir()
    assert (root / "bootstrap.sh").is_file()
    assert _dotfiles_tree.dotfiles_tree_present() is True


def test_absent_when_no_markers(monkeypatch, tmp_path):
    # Simulate the standalone layout: a package whose parents contain no dotfiles
    # markers. Point the detector's anchor file at a marker-less tree and clear
    # the cache so the walk re-runs.
    fake_pkg = tmp_path / "agent-harness" / "phase-loop-runtime" / "tests"
    fake_pkg.mkdir(parents=True)
    fake_file = fake_pkg / "_dotfiles_tree.py"
    fake_file.write_text("# stand-in\n", encoding="utf-8")
    monkeypatch.setattr(_dotfiles_tree, "__file__", str(fake_file))
    _dotfiles_tree.dotfiles_root.cache_clear()
    try:
        assert _dotfiles_tree.dotfiles_root() is None
        assert _dotfiles_tree.dotfiles_tree_present() is False
    finally:
        _dotfiles_tree.dotfiles_root.cache_clear()


class _FakeItem:
    """Minimal stand-in for a collected pytest item: carries a marker and records
    skip markers applied to it, mirroring Item.get_closest_marker / add_marker."""

    def __init__(self, *, marked: bool):
        self._marked = marked
        self.applied = []

    def get_closest_marker(self, name):
        if name == "dotfiles_integration" and self._marked:
            return pytest.mark.dotfiles_integration.mark
        return None

    def add_marker(self, marker):
        self.applied.append(marker)


def _hook_skips(*, tree_present, monkeypatch):
    """Run conftest.pytest_collection_modifyitems with the tree present/absent and
    return whether the integration item and the plain item got a skip marker."""
    import conftest

    monkeypatch.setattr(conftest, "dotfiles_tree_present", lambda: tree_present)
    integration_item = _FakeItem(marked=True)
    plain_item = _FakeItem(marked=False)
    conftest.pytest_collection_modifyitems(config=None, items=[integration_item, plain_item])
    return bool(integration_item.applied), bool(plain_item.applied)


def test_integration_item_skipped_when_tree_absent(monkeypatch):
    """When the helper reports the tree ABSENT, the modifyitems hook applies a skip
    to dotfiles_integration items (collected-then-skipped) and leaves others."""
    integ_skipped, plain_skipped = _hook_skips(tree_present=False, monkeypatch=monkeypatch)
    assert integ_skipped is True
    assert plain_skipped is False


def test_integration_item_runs_when_tree_present(monkeypatch):
    """When the helper reports the tree PRESENT, the hook is a no-op (items run)."""
    integ_skipped, plain_skipped = _hook_skips(tree_present=True, monkeypatch=monkeypatch)
    assert integ_skipped is False
    assert plain_skipped is False
