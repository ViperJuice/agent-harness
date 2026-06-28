"""Locate package-data shipped inside the ``phase_loop_runtime`` wheel.

Single home for the ``importlib.resources`` wheel-anchor idiom that was triplicated
across ``baml_modular``/``schema_export``/``skill_inventory`` (#12 CR). Returns a real
on-disk ``Path``.

Limitation (consistent with the prior per-module helpers): under a zip-imported app
(PEX/shiv/zipapp) ``importlib.resources.files`` yields a non-filesystem ``Traversable``
whose ``str()`` is not a real directory, so these return ``None``. ``pip install``
(extracted ``site-packages``) is the supported mode.
"""
from __future__ import annotations

from pathlib import Path


def package_root() -> Path | None:
    """The extracted on-disk root of the installed ``phase_loop_runtime`` package, or
    ``None`` when it can't be resolved to a real directory."""
    try:
        import importlib.resources

        root = Path(str(importlib.resources.files("phase_loop_runtime")))
    except (ModuleNotFoundError, TypeError, FileNotFoundError):
        return None
    return root if root.is_dir() else None


def packaged_resource_dir(name: str) -> Path | None:
    """A package-data subdirectory shipped in the wheel (e.g. ``"skills_bundle"``), or
    ``None`` if the package root or the subdirectory is unavailable."""
    root = package_root()
    if root is None:
        return None
    candidate = root / name
    return candidate if candidate.is_dir() else None
