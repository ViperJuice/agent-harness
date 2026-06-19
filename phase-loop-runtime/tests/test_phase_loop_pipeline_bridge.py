import unittest
import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.closeout import phase_loop_closeout_diagnostic

DEPRECATED_V5_ROOT_FIELDS = {
    "status",
    "next_skill",
    "next_command",
    "verification_status",
    "artifact",
    "artifact_state",
}

FORBIDDEN_METADATA_TOKENS = (
    "raw transcript",
    "raw diff",
    "diff --git",
    "provider " "payload",
    "raw provider " "payload",
    "credential payload",
    "api_key=",
    "local env value",
    "/home/",
    "/users/",
    "private evidence",
    "private evidence bytes",
)

GOVERNED_PIPELINE_MIRRORED_FIXTURES = {
    "dfbundlecloseout_complete.json",
    "dfbundlecloseout_blocked.json",
    "dfbundlecloseout_failed_verification.json",
    "dfbundlecloseout_human_required.json",
    "dfbundlecloseout_stale_bundle.json",
    "dfbundlecloseout_standalone.json",
    "dfdriftsignal_complete.json",
    "dfdriftsignal_blocked.json",
    "dfdriftsignal_standalone_advisory.json",
    "dfdriftsignal_pipeline_required_advisory.json",
    "dfdriftsignal_canonical_refresh_recommended.json",
    "dftruthsoak_standalone_success.json",
    "dftruthsoak_pipeline_required_success.json",
    "dftruthsoak_stale_source_bundle.json",
    "dftruthsoak_mismatched_protected_source_hash.json",
    "dftruthsoak_unauthorized_protected_source_write.json",
    "dftruthsoak_canonical_refresh_recommended.json",
    "dftruthsoak_failed_verification.json",
    "dftruthsoak_human_required.json",
    "dfadopthints_standalone_unmanaged_spec.json",
    "dfadopthints_pipeline_required_adoption_roles.json",
    "dfadopthints_canonical_refresh_recommended.json",
    "dfadoptbridge_adoption_complete.json",
    "dfadoptbridge_blocked_adoption_metadata.json",
    "dfadoptbridge_stale_source_bundle.json",
    "dfadoptbridge_stale_mirror_manifest.json",
    "dfadoptbridge_unmanaged_spec_input.json",
    "dfadoptbridge_archive_manifest_touched.json",
}

DOTFILES_ONLY_FIXTURES = {
    "complete.json",
    "blocked.json",
    "stale_input.json",
    "failed_verification.json",
    "human_required.json",
    "dfparsoak_complete.json",
    "dfparsoak_blocked.json",
    "dfparsoak_failed_verification.json",
    "dfparsoak_stale_input.json",
    "dfparsoak_human_required.json",
    "dfparsoak_receipt.json",
    "dfparsoak_upstream_receipts.json",
    "dfadoptbridge_standalone_non_adoption.json",
}

CANONICAL_REFRESH_ADVISORY_FIXTURES = {
    "dfdriftsignal_canonical_refresh_recommended.json",
    "dftruthsoak_canonical_refresh_recommended.json",
    "dfadopthints_canonical_refresh_recommended.json",
    "dfadoptbridge_archive_manifest_touched.json",
    "dfadoptbridge_stale_mirror_manifest.json",
}

class TestPhaseLoopPipelineBridge(unittest.TestCase):
    FIXTURE_DIR = str(Path(__file__).resolve().parent / "fixtures" / "phase_loop_pipeline_bridge")
    FIXTURES = [
        "complete.json",
        "blocked.json",
        "stale_input.json",
        "failed_verification.json",
        "human_required.json",
        "dfparsoak_complete.json",
        "dfparsoak_blocked.json",
        "dfparsoak_failed_verification.json",
        "dfparsoak_stale_input.json",
        "dfparsoak_human_required.json",
        "dfparsoak_receipt.json",
        "dfbundlecloseout_complete.json",
        "dfbundlecloseout_blocked.json",
        "dfbundlecloseout_failed_verification.json",
        "dfbundlecloseout_human_required.json",
        "dfbundlecloseout_stale_bundle.json",
        "dfbundlecloseout_standalone.json",
        "dfdriftsignal_complete.json",
        "dfdriftsignal_blocked.json",
        "dfdriftsignal_standalone_advisory.json",
        "dfdriftsignal_pipeline_required_advisory.json",
        "dfdriftsignal_canonical_refresh_recommended.json",
        "dftruthsoak_standalone_success.json",
        "dftruthsoak_pipeline_required_success.json",
        "dftruthsoak_stale_source_bundle.json",
        "dftruthsoak_mismatched_protected_source_hash.json",
        "dftruthsoak_unauthorized_protected_source_write.json",
        "dftruthsoak_canonical_refresh_recommended.json",
        "dftruthsoak_failed_verification.json",
        "dftruthsoak_human_required.json",
        "dfadopthints_standalone_unmanaged_spec.json",
        "dfadopthints_pipeline_required_adoption_roles.json",
        "dfadopthints_canonical_refresh_recommended.json",
        "dfadoptbridge_adoption_complete.json",
        "dfadoptbridge_blocked_adoption_metadata.json",
        "dfadoptbridge_stale_source_bundle.json",
        "dfadoptbridge_stale_mirror_manifest.json",
        "dfadoptbridge_unmanaged_spec_input.json",
        "dfadoptbridge_archive_manifest_touched.json",
        "dfadoptbridge_standalone_non_adoption.json",
    ]
    MALFORMED_FIXTURES = [
        "malformed.json",
        "dfbundlecloseout_malformed_missing_source_bundle_sha.json",
        "dfbundlecloseout_malformed_nested_object.json",
        "dfbundlecloseout_malformed_terminal_status.json",
        "dfbundlecloseout_malformed_redaction.json",
        "dfbundlecloseout_malformed_deprecated_root.json",
        "dfdriftsignal_malformed_redaction.json",
        "dftruthsoak_malformed_closeout.json",
        "dftruthsoak_malformed_redaction.json",
        "dfadopthints_malformed_redaction.json",
        "dfadoptbridge_malformed_deprecated_flat_aliases.json",
        "dfadoptbridge_malformed_redaction.json",
    ]

    def test_fixtures_exist(self):
        """Verify that all expected fixture files exist."""
        for fixture in self.FIXTURES + self.MALFORMED_FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            self.assertTrue(os.path.exists(path), f"Fixture {fixture} missing at {path}")

    def test_fixtures_are_valid_json(self):
        """Verify that all fixture files contain valid JSON."""
        for fixture in self.FIXTURES + self.MALFORMED_FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, 'r') as f:
                try:
                    data = json.load(f)
                    self.assertIsInstance(data, dict, f"Fixture {fixture} is not a JSON object")
                except json.JSONDecodeError as e:
                    self.fail(f"Fixture {fixture} is not valid JSON: {e}")

    def test_fixtures_match_closeout_schema(self):
        """Verify that bridge fixtures pass the runtime closeout diagnostic."""
        for fixture in self.FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                diagnostic = phase_loop_closeout_diagnostic(json.load(f))
            self.assertIsNone(diagnostic, f"Fixture {fixture} failed diagnostic: {diagnostic}")

    def test_valid_native_v1_fixtures_use_nested_fields_without_deprecated_roots(self):
        for fixture in self.FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            self.assertTrue({"automation", "artifacts", "verification", "blocker", "source_bundle", "source_truth_impact"}.issubset(data), fixture)
            self.assertTrue(DEPRECATED_V5_ROOT_FIELDS.isdisjoint(data), fixture)

    def test_malformed_closeout_is_rejected(self):
        for fixture in self.MALFORMED_FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                diagnostic = phase_loop_closeout_diagnostic(json.load(f))

            self.assertIsNotNone(diagnostic, f"Fixture {fixture} unexpectedly passed diagnostic")
            assert diagnostic is not None
            self.assertIn(diagnostic["kind"], {"malformed_closeout", "missing_source_bundle_sha256"})

    def test_fixture_matrix_covers_bridge_parity_scenarios(self):
        fixture_names = set(self.FIXTURES)
        malformed_names = set(self.MALFORMED_FIXTURES)
        scenario_files = {
            "complete": {"complete.json", "dfbundlecloseout_complete.json", "dfdriftsignal_complete.json"},
            "blocked": {"blocked.json", "dfbundlecloseout_blocked.json", "dfdriftsignal_blocked.json"},
            "stale source bundle": {"stale_input.json", "dfbundlecloseout_stale_bundle.json"},
            "failed verification": {"failed_verification.json", "dfbundlecloseout_failed_verification.json"},
            "human required": {"human_required.json", "dfbundlecloseout_human_required.json"},
            "malformed": {"malformed.json", "dfbundlecloseout_malformed_deprecated_root.json"},
            "standalone no bundle": {"dfbundlecloseout_standalone.json", "dfdriftsignal_standalone_advisory.json"},
            "canonical refresh recommended": {"dfdriftsignal_canonical_refresh_recommended.json"},
            "DFTRUTHSOAK standalone success": {"dftruthsoak_standalone_success.json"},
            "DFTRUTHSOAK pipeline-required success": {"dftruthsoak_pipeline_required_success.json"},
            "DFTRUTHSOAK stale source bundle": {"dftruthsoak_stale_source_bundle.json"},
            "DFTRUTHSOAK mismatched protected-source hash": {"dftruthsoak_mismatched_protected_source_hash.json"},
            "DFTRUTHSOAK unauthorized protected-source write": {"dftruthsoak_unauthorized_protected_source_write.json"},
            "DFTRUTHSOAK canonical refresh recommended": {"dftruthsoak_canonical_refresh_recommended.json"},
            "DFTRUTHSOAK failed verification": {"dftruthsoak_failed_verification.json"},
            "DFTRUTHSOAK human required": {"dftruthsoak_human_required.json"},
            "DFTRUTHSOAK malformed closeout": {"dftruthsoak_malformed_closeout.json"},
            "DFTRUTHSOAK redaction violation": {"dftruthsoak_malformed_redaction.json"},
            "DFADOPTHINTS standalone unmanaged spec": {"dfadopthints_standalone_unmanaged_spec.json"},
            "DFADOPTHINTS pipeline-required adoption roles": {"dfadopthints_pipeline_required_adoption_roles.json"},
            "DFADOPTHINTS canonical refresh recommended": {"dfadopthints_canonical_refresh_recommended.json"},
            "DFADOPTHINTS redaction violation": {"dfadopthints_malformed_redaction.json"},
            "DFADOPTBRIDGE adoption complete": {"dfadoptbridge_adoption_complete.json"},
            "DFADOPTBRIDGE blocked adoption metadata": {"dfadoptbridge_blocked_adoption_metadata.json"},
            "DFADOPTBRIDGE stale source bundle": {"dfadoptbridge_stale_source_bundle.json"},
            "DFADOPTBRIDGE stale mirror manifest": {"dfadoptbridge_stale_mirror_manifest.json"},
            "DFADOPTBRIDGE unmanaged spec input": {"dfadoptbridge_unmanaged_spec_input.json"},
            "DFADOPTBRIDGE archive manifest touched": {"dfadoptbridge_archive_manifest_touched.json"},
            "DFADOPTBRIDGE standalone non-adoption": {"dfadoptbridge_standalone_non_adoption.json"},
            "DFADOPTBRIDGE deprecated flat aliases": {"dfadoptbridge_malformed_deprecated_flat_aliases.json"},
            "DFADOPTBRIDGE redaction violation": {"dfadoptbridge_malformed_redaction.json"},
        }
        for scenario, names in scenario_files.items():
            self.assertTrue(names & (fixture_names | malformed_names), scenario)

    def test_standalone_fixtures_do_not_require_bundle_path_or_hash(self):
        for fixture in self.FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            source_bundle = data.get("source_bundle", {})
            if source_bundle.get("pipeline_mode") == "standalone":
                self.assertNotIn("path", source_bundle, fixture)
                self.assertNotIn("sha256", source_bundle, fixture)
                self.assertIsNone(phase_loop_closeout_diagnostic(data), fixture)

    def test_valid_fixtures_remain_metadata_only(self):
        for fixture in self.FIXTURES:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                serialized = json.dumps(json.load(f)).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")

    def test_dfskillgovsoak_classifies_mirrored_and_dotfiles_only_fixtures(self):
        fixture_names = set(self.FIXTURES)
        self.assertTrue(GOVERNED_PIPELINE_MIRRORED_FIXTURES.issubset(fixture_names))
        self.assertTrue((DOTFILES_ONLY_FIXTURES - {"dfparsoak_upstream_receipts.json"}).issubset(fixture_names))
        self.assertTrue(GOVERNED_PIPELINE_MIRRORED_FIXTURES.isdisjoint(DOTFILES_ONLY_FIXTURES))

        for fixture in GOVERNED_PIPELINE_MIRRORED_FIXTURES:
            data = self._load(fixture)
            self.assertIsNone(phase_loop_closeout_diagnostic(data), fixture)
            self.assertTrue(DEPRECATED_V5_ROOT_FIELDS.isdisjoint(data), fixture)
            self.assertEqual(data["source_truth_impact"]["redaction_posture"], "metadata_only", fixture)
            for evidence in data["artifacts"].get("evidence_refs", []):
                if isinstance(evidence, dict):
                    self._assert_repo_relative(evidence.get("path", ""), "mirrored fixture evidence must be repo-relative")

        for fixture in self.MALFORMED_FIXTURES:
            diagnostic = phase_loop_closeout_diagnostic(self._load(fixture))
            self.assertIsNotNone(diagnostic, f"{fixture} must remain rejection coverage")

    def test_dfskillgovsoak_canonical_refresh_is_advisory_only(self):
        for fixture in CANONICAL_REFRESH_ADVISORY_FIXTURES:
            data = self._load(fixture)
            self.assertTrue(data["source_truth_impact"]["canonical_refresh_recommended"], fixture)
            serialized = json.dumps(data).lower()
            self.assertNotIn("raw", serialized, fixture)
            self.assertNotIn("credential", serialized, fixture)
            self.assertNotIn("provider " "payload", serialized, fixture)

        unknown = self._load("dfbundlecloseout_standalone.json")
        categories = {
            item["category"]
            for item in unknown["source_truth_impact"]["changed_path_boundaries"]
        }
        self.assertIn("unknown", categories)
        self.assertEqual(unknown["source_bundle"], {"pipeline_mode": "standalone"})

    def test_dfparsoak_fixtures_include_lane_metadata_and_redacted_evidence_refs(self):
        for fixture in [name for name in self.FIXTURES if name.startswith("dfparsoak_")]:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            lane = data["lane"]
            for field in (
                "lane_id",
                "wave_id",
                "worktree_path",
                "worktree_isolation_mode",
                "base_sha",
                "harness_route",
                "work_unit_kind",
                "verification_status",
                "changed_paths",
                "evidence_refs",
            ):
                self.assertIn(field, lane, f"{fixture} missing lane field {field}")
            serialized = json.dumps(data).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")

    def test_dfbundlecloseout_fixture_matrix_covers_required_scenarios(self):
        expected = {
            "dfbundlecloseout_complete.json",
            "dfbundlecloseout_blocked.json",
            "dfbundlecloseout_failed_verification.json",
            "dfbundlecloseout_human_required.json",
            "dfbundlecloseout_stale_bundle.json",
            "dfbundlecloseout_standalone.json",
        }
        self.assertTrue(expected.issubset(set(self.FIXTURES)))

    def test_dfbundlecloseout_fixtures_keep_evidence_metadata_only(self):
        for fixture in [name for name in self.FIXTURES if name.startswith("dfbundlecloseout_")]:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            artifacts = data["artifacts"]
            self.assertIn("evidence_refs", artifacts)
            self.assertIn("artifact_paths", artifacts)
            serialized = json.dumps(data).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")

    def test_dfdriftsignal_fixture_matrix_covers_advisory_impact_scenarios(self):
        expected = {
            "dfdriftsignal_complete.json",
            "dfdriftsignal_blocked.json",
            "dfdriftsignal_standalone_advisory.json",
            "dfdriftsignal_pipeline_required_advisory.json",
            "dfdriftsignal_canonical_refresh_recommended.json",
        }
        self.assertTrue(expected.issubset(set(self.FIXTURES)))
        for fixture in expected:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            impact = data["source_truth_impact"]
            self.assertIn("changed_path_boundaries", impact)
            self.assertIn("canonical_refresh_recommended", impact)
            self.assertIn("canonical_refresh_reason_codes", impact)
            self.assertEqual(impact["redaction_posture"], "metadata_only")
        with open(os.path.join(self.FIXTURE_DIR, "dfdriftsignal_canonical_refresh_recommended.json"), "r") as f:
            refresh = json.load(f)
        self.assertTrue(refresh["source_truth_impact"]["canonical_refresh_recommended"])

    def test_dfdriftsignal_malformed_redaction_fixture_rejects_forbidden_metadata(self):
        path = os.path.join(self.FIXTURE_DIR, "dfdriftsignal_malformed_redaction.json")
        with open(path, "r") as f:
            diagnostic = phase_loop_closeout_diagnostic(json.load(f))

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic["kind"], "malformed_closeout")

    def test_dftruthsoak_fixture_matrix_covers_final_truth_soak(self):
        expected_valid = {
            "dftruthsoak_standalone_success.json",
            "dftruthsoak_pipeline_required_success.json",
            "dftruthsoak_stale_source_bundle.json",
            "dftruthsoak_mismatched_protected_source_hash.json",
            "dftruthsoak_unauthorized_protected_source_write.json",
            "dftruthsoak_canonical_refresh_recommended.json",
            "dftruthsoak_failed_verification.json",
            "dftruthsoak_human_required.json",
        }
        expected_malformed = {
            "dftruthsoak_malformed_closeout.json",
            "dftruthsoak_malformed_redaction.json",
        }
        self.assertTrue(expected_valid.issubset(set(self.FIXTURES)))
        self.assertTrue(expected_malformed.issubset(set(self.MALFORMED_FIXTURES)))
        for fixture in expected_valid:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["phase"], "DFTRUTHSOAK")
            self.assertIsNone(phase_loop_closeout_diagnostic(data), fixture)
            serialized = json.dumps(data).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")
        with open(os.path.join(self.FIXTURE_DIR, "dftruthsoak_canonical_refresh_recommended.json"), "r") as f:
            refresh = json.load(f)
        self.assertTrue(refresh["source_truth_impact"]["canonical_refresh_recommended"])
        self.assertIn("portal_contract_refs_touched", refresh["source_truth_impact"]["canonical_refresh_reason_codes"])
        self.assertIn("greenfield_authority_refs_touched", refresh["source_truth_impact"]["canonical_refresh_reason_codes"])

    def test_dfparsoak_receipt_cites_metadata_only_upstream_receipts(self):
        receipt = self._load("dfparsoak_receipt.json")
        upstreams = self._load("dfparsoak_upstream_receipts.json")

        self._assert_dfparsoak_contract(receipt, upstreams)

    def test_dfparsoak_contract_rejects_unsafe_upstream_and_evidence_shapes(self):
        receipt = self._load("dfparsoak_receipt.json")
        upstreams = self._load("dfparsoak_upstream_receipts.json")

        unsafe_cases = (
            ("missing upstream receipt", [], "upstream receipts must cite GFPARSOAK and GPPARSOAK"),
            (
                "stale digest",
                [dict(upstreams[0], sha256="0" * 64), upstreams[1]],
                "upstream digest mismatch",
            ),
            (
                "failed verification",
                [dict(upstreams[0], verification_status="failed"), upstreams[1]],
                "upstream receipt must have passed verification",
            ),
            (
                "host absolute path",
                [dict(upstreams[0], path="<HOME-REDACTED>/code/greenfield/receipt.json"), upstreams[1]],
                "upstream receipt path must be repo-relative",
            ),
            (
                "raw transcript evidence",
                [dict(upstreams[0], evidence_refs=("raw-transcript:provider-output",)), upstreams[1]],
                "upstream evidence refs must be metadata-only handles",
            ),
            (
                "secret-shaped field",
                [dict(upstreams[0], evidence_refs=("api_key=fixture-secret-value",)), upstreams[1]],
                "upstream evidence refs must be metadata-only handles",
            ),
            (
                "sibling mutation claim",
                [dict(upstreams[0], sibling_repo_mutation=True), upstreams[1]],
                "upstream receipt must not claim sibling repo mutation",
            ),
        )
        for label, candidate_upstreams, expected_error in unsafe_cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(AssertionError, expected_error):
                    self._assert_dfparsoak_contract(receipt, candidate_upstreams)

        unsafe_receipt = {
            **receipt,
            "artifacts": {
                **receipt["artifacts"],
                "evidence_refs": [{"path": "raw-log/provider-payload.json", "sha256": "3" * 64}],
            },
        }
        with self.assertRaisesRegex(AssertionError, "receipt evidence refs must be redacted handles"):
            self._assert_dfparsoak_contract(unsafe_receipt, upstreams)

    def _load(self, fixture: str):
        path = os.path.join(self.FIXTURE_DIR, fixture)
        with open(path, "r") as f:
            return json.load(f)

    def _assert_dfparsoak_contract(self, receipt: dict, upstreams: list[dict]):
        diagnostic = phase_loop_closeout_diagnostic(receipt)
        self.assertIsNone(diagnostic, f"DFPARSOAK receipt failed diagnostic: {diagnostic}")
        self.assertEqual(receipt["phase"], "DFPARSOAK")
        self.assertEqual(receipt["source_bundle"]["pipeline_mode"], "pipeline_optional")
        self.assertEqual(receipt["verification"]["status"], "passed")

        expected = {
            "GFPARSOAK": receipt["artifacts"]["upstream_receipts"]["GFPARSOAK"]["sha256"],
            "GPPARSOAK": receipt["artifacts"]["upstream_receipts"]["GPPARSOAK"]["sha256"],
        }
        found = {item.get("phase_alias"): item for item in upstreams}
        self.assertEqual(set(found), {"GFPARSOAK", "GPPARSOAK"}, "upstream receipts must cite GFPARSOAK and GPPARSOAK")
        for phase, digest in expected.items():
            item = found[phase]
            self.assertEqual(item.get("sha256"), digest, "upstream digest mismatch")
            self.assertRegex(item.get("sha256", ""), r"^[0-9a-f]{64}$")
            self.assertEqual(item.get("verification_status"), "passed", "upstream receipt must have passed verification")
            self.assertTrue(item.get("metadata_only"), "upstream receipt must be metadata-only")
            self.assertTrue(item.get("produced_interfaces"), "upstream receipt must name produced interfaces")
            self.assertFalse(item.get("sibling_repo_mutation", False), "upstream receipt must not claim sibling repo mutation")
            self._assert_repo_relative(item.get("path", ""), "upstream receipt path must be repo-relative")
            self._assert_metadata_evidence_refs(item.get("evidence_refs", ()), "upstream evidence refs must be metadata-only handles")

        assignment = receipt["lane"]
        self.assertEqual(assignment["worktree_isolation_mode"], "git_worktree")
        self.assertEqual(assignment["harness_route"], "codex")
        self.assertTrue(assignment["fallback_reason"])
        self._assert_metadata_evidence_refs(
            receipt["artifacts"]["evidence_refs"],
            "receipt evidence refs must be redacted handles",
        )
        for path in receipt["artifacts"]["changed_paths"]:
            self._assert_repo_relative(path, "changed paths must be repo-relative")
        serialized = json.dumps(receipt, sort_keys=True).lower()
        for forbidden in ("raw-log", "raw-transcript", "raw-prompt", "provider-payload", "api_key=", "credential=", "local env="):
            self.assertNotIn(forbidden, serialized)

    def _assert_repo_relative(self, value: str, message: str):
        self.assertTrue(value, message)
        self.assertFalse(value.startswith("/"), message)
        self.assertNotIn("..", Path(value).parts, message)

    def _assert_metadata_evidence_refs(self, refs, message: str):
        allowed_prefixes = ("phase-loop-run:", "log:redacted:sha256:", "receipt:sha256:", "metrics:sha256:")
        self.assertTrue(refs, message)
        for ref in refs:
            if isinstance(ref, dict):
                path = str(ref.get("path", ""))
                sha256 = str(ref.get("sha256", ""))
                self._assert_repo_relative(path, message)
                self.assertRegex(sha256, r"^[0-9a-f]{64}$", message)
                lowered = path.lower()
            else:
                self.assertIsInstance(ref, str, message)
                self.assertTrue(ref.startswith(allowed_prefixes), message)
                lowered = ref.lower()
            for forbidden in ("raw", "prompt", "payload", "api_key", "secret", "credential", "/home/", "/mnt/"):
                self.assertNotIn(forbidden, lowered, message)

    def test_dfadopthints_fixture_matrix_covers_adoption_sensitive_hints(self):
        expected_valid = {
            "dfadopthints_standalone_unmanaged_spec.json",
            "dfadopthints_pipeline_required_adoption_roles.json",
            "dfadopthints_canonical_refresh_recommended.json",
        }
        self.assertTrue(expected_valid.issubset(set(self.FIXTURES)))
        for fixture in expected_valid:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["phase"], "DFADOPTHINTS")
            self.assertIsNone(phase_loop_closeout_diagnostic(data), fixture)
            serialized = json.dumps(data).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")
        with open(os.path.join(self.FIXTURE_DIR, "dfadopthints_standalone_unmanaged_spec.json"), "r") as f:
            standalone = json.load(f)
        self.assertEqual(
            standalone["source_truth_impact"]["changed_path_boundaries"][0]["category"],
            "unmanaged_spec",
        )
        with open(os.path.join(self.FIXTURE_DIR, "dfadopthints_pipeline_required_adoption_roles.json"), "r") as f:
            pipeline = json.load(f)
        roles = [item["role"] for item in pipeline["source_bundle"]["protected_sources"]]
        self.assertIn("managed_mirror_file", roles)
        self.assertIn("archive_manifest", roles)
        categories = {item["category"] for item in pipeline["source_truth_impact"]["changed_path_boundaries"]}
        self.assertIn("managed_root_mirror_spec", categories)
        self.assertIn("archive_manifest", categories)
        with open(os.path.join(self.FIXTURE_DIR, "dfadopthints_malformed_redaction.json"), "r") as f:
            diagnostic = phase_loop_closeout_diagnostic(json.load(f))
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic["kind"], "malformed_closeout")

    def test_dfadoptbridge_fixture_matrix_covers_cadoptloop_parity(self):
        expected_valid = {
            "dfadoptbridge_adoption_complete.json",
            "dfadoptbridge_blocked_adoption_metadata.json",
            "dfadoptbridge_stale_source_bundle.json",
            "dfadoptbridge_stale_mirror_manifest.json",
            "dfadoptbridge_unmanaged_spec_input.json",
            "dfadoptbridge_archive_manifest_touched.json",
            "dfadoptbridge_standalone_non_adoption.json",
        }
        expected_malformed = {
            "dfadoptbridge_malformed_deprecated_flat_aliases.json",
            "dfadoptbridge_malformed_redaction.json",
        }
        self.assertTrue(expected_valid.issubset(set(self.FIXTURES)))
        self.assertTrue(expected_malformed.issubset(set(self.MALFORMED_FIXTURES)))
        for fixture in expected_valid:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["phase"], "DFADOPTBRIDGE")
            self.assertIsNone(phase_loop_closeout_diagnostic(data), fixture)
            self.assertTrue({"automation", "artifacts", "verification", "blocker", "source_bundle", "source_truth_impact"}.issubset(data))
            self.assertTrue(DEPRECATED_V5_ROOT_FIELDS.isdisjoint(data), fixture)
            serialized = json.dumps(data).lower()
            for token in FORBIDDEN_METADATA_TOKENS:
                self.assertNotIn(token, serialized, f"{fixture} contains {token}")

        for fixture in expected_valid - {"dfadoptbridge_standalone_non_adoption.json"}:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                data = json.load(f)
            source_bundle = data["source_bundle"]
            self.assertEqual(source_bundle["pipeline_mode"], "pipeline_required", fixture)
            self.assertIn("path", source_bundle, fixture)
            self.assertIn("sha256", source_bundle, fixture)
            self.assertIn("phase_id", source_bundle, fixture)
            self.assertIn("protected_sources", source_bundle, fixture)
            roles = {item["role"] for item in source_bundle["protected_sources"]}
            self.assertTrue(roles & {"active_canonical_spec", "managed_mirror_file", "mirror_manifest", "archive_manifest", "unmanaged_spec_input"}, fixture)
            for evidence in data["artifacts"]["evidence_refs"]:
                self.assertIn("path", evidence, fixture)
                self.assertFalse(evidence["path"].startswith("/"), fixture)

        with open(os.path.join(self.FIXTURE_DIR, "dfadoptbridge_standalone_non_adoption.json"), "r") as f:
            standalone = json.load(f)
        self.assertEqual(standalone["source_bundle"], {"pipeline_mode": "standalone"})

        for fixture in expected_malformed:
            path = os.path.join(self.FIXTURE_DIR, fixture)
            with open(path, "r") as f:
                diagnostic = phase_loop_closeout_diagnostic(json.load(f))
            self.assertIsNotNone(diagnostic, fixture)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "malformed_closeout")

if __name__ == "__main__":
    unittest.main()
