"""CS-0.6 -- the four L0 `.consiliency/` gates (presence, local-integrity,
layout-validity, version-skew), soft/warn by default and wired into closeout."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import make_repo
from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.consiliency_gates import scan_consiliency_gates
from phase_loop_runtime.consiliency_scaffold import scaffold


class ConsiliencyGatesConsentTest(unittest.TestCase):
    def test_repo_without_manifest_is_a_pure_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = scan_consiliency_gates(repo)
            self.assertEqual(result["status"], "skipped")
            self.assertFalse(result["consent"])
            self.assertEqual(result["gates"], {})

    def test_no_repo_is_a_pure_no_op(self):
        result = scan_consiliency_gates(None)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["consent"])


class ConsiliencyGatesScanTest(unittest.TestCase):
    def _scaffolded_repo(self, td: str) -> Path:
        repo = make_repo(Path(td))
        scaffold(repo, mode="archetyped", archetypes=("service",))
        return repo

    def test_missing_required_doc_warns_by_default_never_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            # make_repo already wrote README.md; LICENSE is declared-missing by
            # the scaffolder (l0_stub_allowed: false) -- a real gap.
            self.assertFalse((repo / "LICENSE").exists())
            result = scan_consiliency_gates(repo)
            self.assertEqual(result["mode"], "warn")
            self.assertEqual(result["status"], "warn")
            self.assertEqual(result["gates"]["presence"]["status"], "warn")
            codes = {f["code"] for f in result["gates"]["presence"]["findings"]}
            self.assertIn("missing_file", codes)

    def test_hard_mode_blocks_on_missing_required_doc(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            result = scan_consiliency_gates(repo, env={"PHASE_LOOP_CONSILIENCY_GATES": "hard"})
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["gates"]["presence"]["status"], "blocked")

    def test_all_required_docs_present_passes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            (repo / "LICENSE").write_text("MIT\n", encoding="utf-8")
            result = scan_consiliency_gates(repo)
            # NOTE (CS-0.11 contract bump to consiliency-contract 0.2.0): the
            # published contract-version-status.schema.json still pins
            # package.version/repo_contract_version to the literal
            # "^0\.1\.0$" -- manifest.schema.json's contract_version pattern
            # and the version-skew-protocol compatible_ranges were both
            # bumped to the 0.2.x range for 0.2.0, but this one schema was
            # not. A freshly scaffolded status.json declaring contract
            # version "0.2.0" is therefore always schema-invalid under the
            # vendored 0.2.0 contract, which surfaces as a soft
            # layout_validity warn (never a block). What this test actually
            # exercises -- the presence gate -- still passes cleanly.
            self.assertEqual(result["gates"]["presence"]["status"], "passed")
            self.assertEqual(result["status"], "warn")

    def test_gates_mode_off_skips_entirely_even_with_a_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            result = scan_consiliency_gates(repo, env={"PHASE_LOOP_CONSILIENCY_GATES": "off"})
            self.assertEqual(result["status"], "skipped")
            self.assertTrue(result["consent"])
            self.assertEqual(result["gates"], {})

    def test_layout_validity_flags_a_malformed_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            manifest_file = repo / ".consiliency" / "manifest.json"
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest["declaration"] = {"mode": "archetyped", "archetypes": ["not-a-real-archetype"]}
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            result = scan_consiliency_gates(repo)
            self.assertEqual(result["gates"]["layout_validity"]["status"], "warn")
            self.assertTrue(result["gates"]["layout_validity"]["findings"])

    def test_unparsable_manifest_is_a_layout_validity_finding_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            manifest_file = repo / ".consiliency" / "manifest.json"
            manifest_file.write_text("{not json", encoding="utf-8")
            result = scan_consiliency_gates(repo)
            self.assertTrue(result["consent"])
            self.assertEqual(result["gates"]["layout_validity"]["status"], "warn")

    def test_version_skew_gate_reports_compatible_for_a_freshly_scaffolded_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            result = scan_consiliency_gates(repo)
            skew = result["gates"]["version_skew"]
            self.assertEqual(skew["compatibility"], "compatible")
            self.assertEqual(skew["maturity"], "realized-edge-observed")

    def test_version_skew_gate_never_blocks_even_in_hard_mode(self):
        # The manifest schema pins `contract_version` to the 0.2.x range (CS-0.11
        # bumped the vendored contract pin to consiliency-contract 0.2.0), so a
        # schema-valid manifest can never actually exercise a skew finding --
        # call the gate function directly with a deliberately mismatched (pre-0.2)
        # version to prove the capped-warn behavior (not just that a compatible
        # manifest trivially passes).
        from phase_loop_runtime.consiliency_gates import _gate_version_skew

        skewed = _gate_version_skew({"contract_version": "0.1.0"}, mode="hard")
        self.assertEqual(skewed["compatibility"], "incompatible")
        self.assertTrue(skewed["findings"])
        # Even under opt-in hard mode, version-skew is normatively warn-only
        # at Phase 0 (version-skew-protocol.schema `phase0_severity` const).
        self.assertEqual(skewed["status"], "warn")

    def test_local_integrity_is_a_no_op_when_nothing_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._scaffolded_repo(td)
            result = scan_consiliency_gates(repo)
            self.assertEqual(result["gates"]["local_integrity"]["status"], "passed")
            self.assertEqual(result["gates"]["local_integrity"]["findings"], [])


class ConsiliencyGatesCloseoutWiringTest(unittest.TestCase):
    def _base_closeout_kwargs(self):
        return dict(
            phase_alias="TEST",
            plan_path="plan.md",
            terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
        )

    def test_no_scan_threaded_in_is_inert(self):
        closeout = build_phase_loop_closeout(**self._base_closeout_kwargs())
        self.assertNotIn("consiliency_gates", closeout)

    def test_warn_scan_is_recorded_but_never_blocks(self):
        scan = {
            "status": "warn",
            "mode": "warn",
            "consent": True,
            "manifest_path": "/tmp/x/.consiliency/manifest.json",
            "gates": {"presence": {"status": "warn", "maturity": "presence-only", "findings": [{"code": "missing_file"}]}},
        }
        closeout = build_phase_loop_closeout(consiliency_gates=scan, **self._base_closeout_kwargs())
        self.assertEqual(closeout["consiliency_gates"], "warn")
        self.assertEqual(closeout["terminal_status"], "complete")
        self.assertFalse(closeout["blocker"]["human_required"])

    def test_blocked_scan_blocks_the_closeout_without_human_required(self):
        scan = {
            "status": "blocked",
            "mode": "hard",
            "consent": True,
            "manifest_path": "/tmp/x/.consiliency/manifest.json",
            "gates": {"presence": {"status": "blocked", "maturity": "presence-only", "findings": [{"code": "missing_file"}]}},
        }
        closeout = build_phase_loop_closeout(consiliency_gates=scan, **self._base_closeout_kwargs())
        self.assertEqual(closeout["consiliency_gates"], "blocked")
        self.assertEqual(closeout["terminal_status"], "blocked")
        self.assertEqual(closeout["blocker"]["blocker_class"], "consiliency_gate_blocked")
        self.assertFalse(closeout["blocker"]["human_required"])

    def test_skipped_scan_never_blocks(self):
        scan = {"status": "skipped", "mode": "warn", "consent": False, "manifest_path": None, "gates": {}}
        closeout = build_phase_loop_closeout(consiliency_gates=scan, **self._base_closeout_kwargs())
        self.assertEqual(closeout["consiliency_gates"], "skipped")
        self.assertEqual(closeout["terminal_status"], "complete")


if __name__ == "__main__":
    unittest.main()
