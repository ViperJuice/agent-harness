from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Bound every child process so a hung wheel build / pip dependency resolution /
# node interchange call fails as TimeoutExpired instead of hanging the suite.
# Generous ceiling: the wheel build + pip install is the slow leg; node calls
# are sub-second.
_SUBPROCESS_TIMEOUT = 300


def _required_root(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is required for the SPECPKGMIN cross-repo seam")
    root = Path(value).resolve()
    if not root.is_dir():
        pytest.fail(f"{name} is not a directory: {root}")
    return root


def test_installed_wheel_dogfoods_frozen_seams_and_gp_interchange(tmp_path: Path):
    spec_root = _required_root("SPEC_ROOT")
    gp_root = _required_root("GP_ROOT")
    harness_root = _required_root("HARNESS_ROOT")
    assert harness_root == Path(__file__).resolve().parents[2]

    package_root = spec_root / "packages" / "consiliency-spec-ingest"
    dist = tmp_path / "dist"
    venv = tmp_path / "venv"
    build_source = tmp_path / "package-source"
    shutil.copytree(package_root, build_source, ignore=shutil.ignore_patterns("build", "*.egg-info"))
    subprocess.run(
        [sys.executable, "-m", "build", str(build_source), "--wheel", "--outdir", str(dist)],
        check=True,
        timeout=_SUBPROCESS_TIMEOUT,
        cwd=spec_root,
    )
    wheel = next(dist.glob("*.whl"))
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=_SUBPROCESS_TIMEOUT)
    python = venv / "bin" / "python"
    subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", str(wheel)], check=True, timeout=_SUBPROCESS_TIMEOUT)

    certificate_path = tmp_path / "certificate.json"
    probe = """
import json
import sys
import consiliency_spec_ingest as ingest

result = ingest.evaluate({\"nodes\": []}, {\"nodes\": [], \"revision\": \"specpkgmin-empty\"}, {\"entries\": []}, config={})
try:
    ingest.ingest_repo(\".\", graph_id=\"specpkgmin\")
except ingest.CapabilityUnavailable as error:
    unavailable = {\"capability\": error.capability, \"required_extra\": error.required_extra, \"available\": error.available}
else:
    raise AssertionError(\"ingest_repo must remain unavailable in the base wheel\")
print(json.dumps({\"certificate\": result[\"certificate\"], \"unavailable\": unavailable, \"loaded\": sorted(sys.modules)}))
"""
    observed = json.loads(subprocess.run([python, "-c", probe], check=True,
        timeout=_SUBPROCESS_TIMEOUT, text=True, capture_output=True).stdout)
    assert observed["certificate"]["overall_result_state"] == "not_applicable"
    assert observed["unavailable"] == {"capability": "ingest_repo", "required_extra": "ingest", "available": False}
    assert not any(token in name for token in ("spec_brownfield", "treesitter", "canon_core", "realized") for name in observed["loaded"])
    certificate_path.write_text(json.dumps(observed["certificate"]), encoding="utf-8")

    fixtures = gp_root / "packages" / "pipeline-runtime" / "test" / "fixtures" / "specpkgmin"
    helper = fixtures / "specpkgmin-interchange.mjs"
    command = ["node", str(helper), str(certificate_path), str(fixtures / "canonical-spec-registry.json"), str(fixtures / "catalog-schema.v1.json")]
    interchange = json.loads(subprocess.run(command, cwd=gp_root, check=True,
        timeout=_SUBPROCESS_TIMEOUT, text=True, capture_output=True).stdout)
    assert interchange["result"]["ok"] is True
    assert interchange["result"]["verdict"]["overall_result_state"] == "not_applicable"
    assert interchange["result"]["ratifiable"] is False
    assert interchange["registry_bytes_unchanged"] is True
    assert interchange["catalog_bytes_unchanged"] is True

    malformed_path = tmp_path / "malformed-vacuity.json"
    malformed = dict(observed["certificate"])
    malformed["vacuity"] = "not-an-object"
    malformed_path.write_text(json.dumps(malformed), encoding="utf-8")
    malformed_result = json.loads(
        subprocess.run(
            ["node", str(helper), str(malformed_path), str(fixtures / "canonical-spec-registry.json"), str(fixtures / "catalog-schema.v1.json")],
            cwd=gp_root,
            check=True,
        timeout=_SUBPROCESS_TIMEOUT,
            text=True,
            capture_output=True,
        ).stdout
    )
    assert malformed_result["result"]["ok"] is False
    assert malformed_result["result"]["blocker"]["blocker_class"] == "malformed_spec_certificate"
