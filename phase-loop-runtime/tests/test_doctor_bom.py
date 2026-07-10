"""AHADOPT lane (d): `phase-loop doctor` + multi-registry BOM (IF-0-AHADOPT-2).

Covers, offline and deterministically:
- the checked-in ``phase-loop-doctor.v1`` schema + golden fixture both validate;
- live doctor output (with an injected fetcher) validates against the schema;
- a mock npm+PyPI registry drives correct ``stale``/``current`` verdicts offline;
- an unreachable registry degrades to ``unknown`` — never raises, never fails;
- ``--fail-on-stale`` exits non-zero on a GATING stale target and zero when only a
  NON-gating target is stale;
- the payload is metadata-only (no absolute paths).
"""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import jsonschema
import pytest

from phase_loop_runtime import doctor

FIXTURES = Path(__file__).parent / "fixtures"


def _schema() -> dict:
    return json.loads(
        (files("phase_loop_runtime") / "schemas" / "phase-loop-doctor.v1.schema.json").read_text()
    )


def _golden() -> dict:
    return json.loads(
        (files("phase_loop_runtime") / "schemas" / "phase-loop-doctor.v1.golden.json").read_text()
    )


# --------------------------------------------------------------------------- #
# schema + golden
# --------------------------------------------------------------------------- #
def test_golden_validates_against_schema() -> None:
    jsonschema.validate(_golden(), _schema())


def test_golden_has_the_named_bom_inventory() -> None:
    names = {e["target"] for e in _golden()["bom"]}
    assert {
        "consiliency-contract",
        "@consiliency/contract",
        "@consiliency/canon-core",
    } <= names
    assert any("install-agent-harness.sh" in n for n in names)
    assert any("mac-skills" in n for n in names)


# --------------------------------------------------------------------------- #
# offline mock registry: verdicts without network
# --------------------------------------------------------------------------- #
def _mock_fetch(mapping: dict[str, str]):
    def fetch(url: str):
        for key, body in mapping.items():
            if key in url:
                return body
        return None

    return fetch


def _synthetic_repo(tmp_path: Path) -> Path:
    """A hermetic repo with known local pins (does not depend on the live
    checkout, so this passes standalone-from-wheel in the Gate A clean room)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.0"\n'
        'dependencies = ["consiliency-contract>=0.6.5,<0.7"]\n',
        encoding="utf-8",
    )
    (tmp_path / "RELEASE_PIN").write_text("v0.6.2\n", encoding="utf-8")
    consiliency = tmp_path / ".consiliency"
    consiliency.mkdir()
    (consiliency / "manifest.json").write_text(
        json.dumps({"contract_version": "0.3.0"}), encoding="utf-8"
    )
    return tmp_path


def test_mock_registry_drives_verdicts_offline(tmp_path) -> None:
    fetch = _mock_fetch(
        {
            # PyPI consiliency-contract latest == floor 0.6.5 -> current
            "pypi.org/pypi/consiliency-contract": json.dumps({"info": {"version": "0.6.5"}}),
            # PyPI phase-loop-runtime ahead of the pin (0.6.2) -> stale
            "pypi.org/pypi/phase-loop-runtime": json.dumps({"info": {"version": "9.9.9"}}),
            # npm @consiliency/contract latest ahead of vendored 0.3.0 -> stale
            "registry.npmjs.org/@consiliency%2fcontract": json.dumps({"dist-tags": {"latest": "0.6.5"}}),
            "registry.npmjs.org/@consiliency%2fcanon-core": json.dumps({"dist-tags": {"latest": "0.1.0"}}),
        }
    )
    repo = _synthetic_repo(tmp_path)
    bom = {e["target"]: e for e in doctor.build_bom(repo, fetch=fetch)}
    assert bom["consiliency-contract"]["verdict"] == "current"  # 0.6.5 == 0.6.5
    assert bom["install-agent-harness.sh ref"]["verdict"] == "stale"  # 0.6.2 < 9.9.9
    assert bom["@consiliency/contract"]["verdict"] == "stale"  # 0.3.0 < 0.6.5
    # canon-core has no local pin -> unknown even with a registry latest present
    assert bom["@consiliency/canon-core"]["verdict"] == "unknown"


def test_offline_registry_degrades_to_unknown_never_raises() -> None:
    offline = lambda url: None  # noqa: E731 - every fetch fails
    repo = Path(__file__).resolve().parents[1]
    bom = doctor.build_bom(repo, fetch=offline)
    for entry in bom:
        assert entry["verdict"] == "unknown", entry
    # And it validates + does not raise through the full report path.
    report = doctor.build_doctor_report(repo, fetch=offline)
    jsonschema.validate(report, _schema())


def test_live_report_validates_against_schema_with_fixture_bom() -> None:
    repo = Path(__file__).resolve().parents[1]
    report = doctor.build_doctor_report(repo, bom_fixture=FIXTURES / "bom-current.json")
    jsonschema.validate(report, _schema())


# --------------------------------------------------------------------------- #
# --fail-on-stale wiring
# --------------------------------------------------------------------------- #
def test_fail_on_stale_exits_nonzero_on_gating_stale(capsys) -> None:
    rc = doctor.run_doctor(
        repo=Path("."),
        as_json=False,
        fail_on_stale=True,
        bom_fixture=FIXTURES / "bom-stale.json",
    )
    assert rc == 1
    # The FAIL line goes to stderr so `--json` stdout stays pure JSON.
    assert "stale gating BOM target" in capsys.readouterr().err


def test_fail_on_stale_ignores_non_gating_stale(capsys) -> None:
    rc = doctor.run_doctor(
        repo=Path("."),
        as_json=False,
        fail_on_stale=True,
        bom_fixture=FIXTURES / "bom-current.json",
    )
    assert rc == 0


def test_json_stdout_stays_pure_even_when_failing_on_stale(capsys) -> None:
    # Regression: --json + --fail-on-stale must keep stdout as parseable JSON;
    # the FAIL diagnostic goes to stderr.
    rc = doctor.run_doctor(
        repo=Path("."),
        as_json=True,
        fail_on_stale=True,
        bom_fixture=FIXTURES / "bom-stale.json",
    )
    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.out)  # raises if stdout is not pure JSON
    assert payload["schema"] == "phase-loop-doctor.v1"
    assert "stale gating BOM target" in captured.err


def test_without_fail_flag_stale_bom_still_exits_zero() -> None:
    rc = doctor.run_doctor(
        repo=Path("."),
        as_json=True,
        fail_on_stale=False,
        bom_fixture=FIXTURES / "bom-stale.json",
    )
    assert rc == 0


# --------------------------------------------------------------------------- #
# verdict logic + redaction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "pinned,latest,expected",
    [
        ("0.6.5", "0.6.5", "current"),
        ("0.6.5", "0.6.6", "stale"),
        ("0.6.5", "0.6.4", "current"),
        (None, "0.6.5", "unknown"),
        ("0.6.5", None, "unknown"),
        ("garbage", "0.6.5", "unknown"),
    ],
)
def test_verdict_logic(pinned, latest, expected) -> None:
    assert doctor._verdict(pinned, latest) == expected


def test_report_is_metadata_only_no_absolute_paths() -> None:
    repo = Path(__file__).resolve().parents[1]
    report = doctor.build_doctor_report(repo, fetch=lambda url: None)
    serialized = json.dumps(report)
    for token in ("/home/", "/Users/", "/mnt/", "op://"):
        assert token not in serialized, f"doctor payload leaked {token!r}"
