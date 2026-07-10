"""AHADOPT lane (c): install-ref auto-track.

The install scripts no longer hardcode a stale release ref (`v0.1.13` / `v0.3.0`).
They resolve the current release from the checked-in ``RELEASE_PIN`` file, which is
held == the published package version by the ``release-consistency`` CI gate.

These tests assert the *auto-track* property, not merely grep-absence of an old
string:
- ``RELEASE_PIN`` (minus its leading ``v``) equals the ``phase-loop-runtime``
  package version declared in ``pyproject.toml`` (offline, always runs);
- ``install-agent-harness.sh`` contains no hardcoded ``v0.1.13`` and *does* resolve
  from ``RELEASE_PIN`` (offline);
- ``RELEASE_PIN`` equals the version currently published on PyPI
  (network; skipped offline, mirroring the doctor BOM's degrade-to-unknown stance).

All tests skip cleanly when the repo-root artifacts are not reachable (i.e. the
package was installed standalone, extracted out of the agent-harness checkout).
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib

import pytest

PYPI_DIST = "phase-loop-runtime"


def _repo_root() -> Path | None:
    """Walk up from this test file to the agent-harness checkout root.

    The root is identified by carrying BOTH the RELEASE_PIN file and the
    install-agent-harness.sh installer. Returns None when neither is reachable
    (standalone-extracted package) so the tests skip rather than fail.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "RELEASE_PIN").is_file() and (parent / "install-agent-harness.sh").is_file():
            return parent
    return None


def _pyproject_version(root: Path) -> str:
    data = tomllib.loads((root / "phase-loop-runtime" / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _pin(root: Path) -> str:
    return (root / "RELEASE_PIN").read_text(encoding="utf-8").strip()


def _pin_version(root: Path) -> str:
    pin = _pin(root)
    return pin[1:] if pin.startswith("v") else pin


def test_release_pin_matches_package_version() -> None:
    root = _repo_root()
    if root is None:
        pytest.skip("RELEASE_PIN / install-agent-harness.sh not reachable (standalone package)")
    assert _pin_version(root) == _pyproject_version(root), (
        f"RELEASE_PIN {_pin(root)!r} does not match pyproject version "
        f"{_pyproject_version(root)!r}; bump RELEASE_PIN in lockstep with the release."
    )


def test_release_pin_is_a_v_prefixed_semverish_ref() -> None:
    root = _repo_root()
    if root is None:
        pytest.skip("RELEASE_PIN not reachable (standalone package)")
    assert re.fullmatch(r"v\d+\.\d+\.\d+", _pin(root)), (
        f"RELEASE_PIN {_pin(root)!r} must be a v-prefixed X.Y.Z ref"
    )


def test_installer_has_no_hardcoded_stale_ref_and_resolves_from_pin() -> None:
    root = _repo_root()
    if root is None:
        pytest.skip("install-agent-harness.sh not reachable (standalone package)")
    installer = (root / "install-agent-harness.sh").read_text(encoding="utf-8")
    assert "v0.1.13" not in installer, "installer still hardcodes the stale v0.1.13 ref"
    assert "RELEASE_PIN" in installer, "installer must resolve the ref from RELEASE_PIN"
    # The resolver must not fall back to a baked-in hardcoded release ref.
    assert not re.search(r"AGENT_HARNESS_REF:-v\d", installer), (
        "installer must not default AGENT_HARNESS_REF to a hardcoded version"
    )


def test_release_pin_matches_pypi_latest() -> None:
    """The resolved ref == the version currently published on PyPI.

    Network-bound: skipped (not failed) when PyPI is unreachable, matching the
    doctor BOM's degrade-to-unknown-offline contract.
    """
    root = _repo_root()
    if root is None:
        pytest.skip("RELEASE_PIN not reachable (standalone package)")
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{PYPI_DIST}/json", timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.skip(f"PyPI unreachable ({exc}); auto-track vs registry not checked offline")
    latest = str(payload["info"]["version"])
    assert _pin_version(root) == latest, (
        f"RELEASE_PIN {_pin(root)!r} (-> {_pin_version(root)!r}) != PyPI latest "
        f"{PYPI_DIST}=={latest}; the install pin has drifted behind the published release."
    )
