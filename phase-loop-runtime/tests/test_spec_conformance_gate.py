"""HGATE -- the spec_conformance gate. Advisory panel (Fable + Codex 5.5 +
Gemini 3.1 Pro, unanimous) set the default PASS bar at `hash-checked`+ ("bar B"),
with `presence-only` an info-grade soft warn, `present-nonconforming`/`foreign`/
`unmanaged` loud, and contract-sanctioned L0 stubs (l0_stub_allowed) exempt so an
honest fresh adopter is nudged, not punished.

NOTE: the loud-branch tests (present-nonconforming/foreign/empty/unknown) set maturities
the current manifest schema enum does NOT allow -- on a schema-valid manifest layout_validity
catches them first, so these exercise the gate LOGIC that activates once the schema enum
expands. The info tier is likewise unreachable via the scaffold (all real proj rows are
l0_stub_allowed), so it is exercised directly with a synthetic non-sanctioned row."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import make_repo
from phase_loop_runtime.consiliency_gates import scan_consiliency_gates
from phase_loop_runtime.consiliency_layout import consiliency_root, find_consiliency_manifest
from phase_loop_runtime.consiliency_scaffold import scaffold

# glossary is a proj-S doc, sbom is proj-code -- both l0_stub_allowed at presence-only.
_PROJ_DOC = "glossary"


def _scaffold(td: str) -> Path:
    repo = make_repo(Path(td))
    scaffold(repo, mode="archetyped", archetypes=("service",), modifiers=("public",))
    return repo


def _set_maturity(repo: Path, doc_id: str, maturity: str) -> None:
    manifest_file = find_consiliency_manifest(repo)
    data = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    for d in data.get("documents", []):
        if d.get("id") == doc_id:
            d["maturity"] = maturity
    Path(manifest_file).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _drop_docs(repo: Path, classes_ids: set[str]) -> None:
    manifest_file = find_consiliency_manifest(repo)
    data = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    data["documents"] = [d for d in data.get("documents", []) if d.get("id") not in classes_ids]
    Path(manifest_file).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _add_posture_override(repo: Path, overrides: dict[str, str]) -> None:
    manifest_file = find_consiliency_manifest(repo)
    data = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    data["gate_posture_overrides"] = overrides
    Path(manifest_file).write_text(json.dumps(data, indent=2), encoding="utf-8")


class SpecConformanceGateTest(unittest.TestCase):
    def _conf(self, repo: Path, mode: str = "warn") -> dict:
        result = scan_consiliency_gates(repo, env={"PHASE_LOOP_CONSILIENCY_GATES": mode})
        return result["gates"]["spec_conformance"]

    def test_fresh_scaffold_sanctioned_l0_stubs_pass(self):
        # glossary/sbom are presence-only l0_stub_allowed=True -> the contract sanctions
        # them as L0 stubs, so a compliant fresh adopter must NOT be warned.
        with tempfile.TemporaryDirectory() as td:
            gate = self._conf(_scaffold(td))
            self.assertEqual(gate["status"], "passed")
            self.assertEqual(gate["findings"], [])

    def test_no_spec_projections_declared_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _drop_docs(repo, {"glossary", "sbom"})
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "passed")
            self.assertIn("no-op", gate.get("note", ""))

    def test_present_nonconforming_warns_loud(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "present-nonconforming")
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "warn")
            self.assertIn("spec_nonconforming", {f["code"] for f in gate["findings"]})

    def test_foreign_is_governance_status_not_nonconformance(self):
        # foreign/unmanaged are governance-status labels, NOT a "code doesn't match spec"
        # assertion -- they get spec_ungoverned, a distinct loud finding.
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "foreign")
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "warn")
            codes = {f["code"] for f in gate["findings"]}
            self.assertIn("spec_ungoverned", codes)
            self.assertNotIn("spec_nonconforming", codes)

    def test_empty_maturity_is_its_own_finding(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "")
            gate = self._conf(repo)
            self.assertIn("spec_maturity_missing", {f["code"] for f in gate["findings"]})

    def test_gate_reports_its_own_maturity_honestly_as_presence_only(self):
        # It does no digest work -- it must not claim hash-checked maturity.
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._conf(_scaffold(td))["maturity"], "presence-only")

    def test_info_tier_nudges_a_nonsanctioned_presence_only_projection_and_never_blocks(self):
        # The bar-B info tier is UNREACHABLE via the scaffold (every real proj row is
        # l0_stub_allowed), so exercise it directly with a synthetic NON-sanctioned row.
        from unittest import mock
        from phase_loop_runtime import consiliency_gates as cg
        from phase_loop_runtime.consiliency_layout import RequiredDocRow

        row = RequiredDocRow(id="myproj", doc_class="proj-code", required=True,
                             maturity_floor="presence-only", l0_stub_allowed=False,
                             l0_note=None, source="test")
        manifest = {"declaration": {"mode": "archetyped", "archetypes": ["service"], "modifiers": []},
                    "documents": [{"id": "myproj", "path": "myproj.md", "maturity": "presence-only"}]}
        with mock.patch.object(cg, "compose_required_documents", return_value=[row]):
            warn = cg._gate_spec_conformance(manifest, mode="warn")
            hard = cg._gate_spec_conformance(manifest, mode="hard")
        # Posture registry: spec_below_conformance_bar defaults to `observe` -> a recorded
        # NOTE that never escalates the gate past passed (forgiving default), even under hard.
        self.assertEqual(warn["status"], "passed")
        self.assertEqual(hard["status"], "passed")
        finding = next(f for f in warn["findings"] if f["code"] == "spec_below_conformance_bar")
        self.assertEqual(finding["posture"], "observe")  # recorded, not warned

    def test_layout_validity_accepts_a_manifest_that_dials_its_teeth(self):
        # The dial is only operable if the manifest schema ALLOWS gate_posture_overrides
        # (manifest.schema additionalProperties was False -> it rejected the key pre-0.6.3).
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _add_posture_override(repo, {"spec_nonconforming": "observe", "write_footprint_violation": "enforce"})
            result = scan_consiliency_gates(repo)
            self.assertEqual(result["gates"]["layout_validity"]["status"], "passed")

    def test_retracting_an_advisory_emits_a_visible_posture_retracted_note(self):
        # De-fanging is never silent: dialing spec_nonconforming down to observe records
        # the finding as a note AND adds the non-retractable posture_retracted note.
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "present-nonconforming")
            _add_posture_override(repo, {"spec_nonconforming": "observe"})
            gate = self._conf(repo)
            codes = {f["code"] for f in gate["findings"]}
            self.assertIn("posture_retracted", codes)
            nonconf = next(f for f in gate["findings"] if f["code"] == "spec_nonconforming")
            self.assertEqual(nonconf["posture"], "observe")  # retract honored (advisory)

    def test_nonconforming_is_advisory_warn_by_default_even_under_hard(self):
        # Retractable teeth: spec_nonconforming is ADVISORY (warn), not an invariant --
        # so by default it never blocks, even under hard mode. Forgiving default.
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "present-nonconforming")
            gate = self._conf(repo, mode="hard")
            self.assertEqual(gate["status"], "warn")  # advisory: warns, does not block

    def test_a_repo_can_extend_teeth_to_block_nonconformance_under_hard(self):
        # Opt-in teeth: raising spec_nonconforming to `enforce` per-repo makes it block
        # under the operator's hard master switch.
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "present-nonconforming")
            _add_posture_override(repo, {"spec_nonconforming": "enforce"})
            self.assertEqual(self._conf(repo, mode="hard")["status"], "blocked")
            self.assertEqual(self._conf(repo, mode="warn")["status"], "warn")  # hard still the master switch

    def test_hash_checked_projection_passes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "hash-checked")
            _set_maturity(repo, "sbom", "hash-checked")
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "passed")
            self.assertEqual(gate["findings"], [])

    def test_deprecated_certified_alias_ranks_as_parity_certified(self):
        # The bare `certified` label is a deprecated alias of parity-certified; it is
        # above the bar and must pass, not be treated as an unknown maturity.
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "certified")
            _set_maturity(repo, "sbom", "certified")
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "passed")

    def test_unknown_maturity_warns(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _scaffold(td)
            _set_maturity(repo, _PROJ_DOC, "totally-made-up")
            gate = self._conf(repo)
            self.assertEqual(gate["status"], "warn")
            self.assertIn("spec_maturity_unknown", {f["code"] for f in gate["findings"]})


if __name__ == "__main__":
    unittest.main()
