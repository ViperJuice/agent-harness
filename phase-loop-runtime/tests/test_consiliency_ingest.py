"""CS-0.11 -- brownfield ingestion: shape-to-conform, then verify.

First pass on an unmanaged repo shapes a schema-valid `.consiliency/` (CS-0.5
scaffold + a CS-0.12 adoption profile + a proposed governed-set allowlist);
every subsequent pass only verifies (CS-0.6 L0 gates + governance-scope
document labels), never rewrites, and a hand-corrupted `.consiliency/` is
flagged rather than silently re-shaped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from consiliency_contract import list_vectors, load_schema, load_vector
from phase_loop_test_utils import make_repo
from phase_loop_runtime.consiliency_layout import ARCHETYPE_IDS, manifest_path
from phase_loop_runtime.consiliency_ingest import evaluate_governance_scope, ingest

BIN = (sys.executable, "-m", "phase_loop_runtime.cli")


class GovernanceScopeConformanceTest(unittest.TestCase):
    """`evaluate_governance_scope` against every conformance vector the
    vendored contract ships for the governance-scope surface (adoption,
    governed-set, ignore-set, doc-label). `governed`, the decision `status`,
    the finding `code`, and `labels` are the normative surface
    (`decision.schema.json` + the vector's own `expected.governed`) and are
    asserted exactly for all vectors.

    `expected.reason` is asserted too EXCEPT for the two ignore-set vectors:
    both route through the identical bare-dir-any-depth ignore rule (one
    matches at the repo root, one nested), yet the vectors name them
    "ignored-scratch" vs "ignored-nested" -- a per-scenario descriptive
    label keyed off the fixture's own namespace name, not a generalizable
    computed property a from-scratch implementation can be expected to
    reproduce verbatim. This module emits a single "ignored" reason for
    both; see `consiliency_ingest._path_ignored`.
    """

    _RECOMPUTABLE_REASON_VECTORS = frozenset(
        {"ignore-set-nested-path-any-depth", "ignore-set-scratch-never-governed"}
    )

    def test_every_governance_scope_vector(self):
        names = [n for n in list_vectors() if n.startswith(("adoption-", "governed-set-", "ignore-set-", "doc-label-"))]
        self.assertGreaterEqual(len(names), 9, "expected the full governance-scope vector set to be present")
        for name in names:
            with self.subTest(vector=name):
                vector = load_vector(name)
                vector_id = vector["id"]
                inp = vector["input"]
                result = evaluate_governance_scope(
                    adoption=inp.get("adoption"),
                    governed_set=inp.get("governed_set") or (),
                    ignore_set=inp.get("ignore_set") or (),
                    facet=inp.get("facet"),
                    subject=inp["subject"],
                )
                expected = vector["expected"]
                decision = vector["decision"]
                self.assertEqual(result["governed"], expected["governed"])
                self.assertEqual(result["status"], decision["status"])
                self.assertEqual(result["findings"][0]["code"], decision["findings"][0]["code"])
                self.assertEqual(result.get("labels"), decision.get("labels"))
                if vector_id not in self._RECOMPUTABLE_REASON_VECTORS:
                    self.assertEqual(result["reason"], expected.get("reason"))


class ConsiliencyIngestConsentTest(unittest.TestCase):
    def test_unmanaged_repo_without_adopt_is_a_pure_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = ingest(repo)
            self.assertEqual(result.mode, "skipped")
            self.assertFalse(result.adopted)
            self.assertFalse((repo / ".consiliency").exists())

    def test_unmanaged_repo_with_adopt_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = ingest(repo, adopt=True, mode="baseline-only")
            self.assertEqual(result.mode, "shape")
            self.assertTrue(result.adopted)
            self.assertTrue(manifest_path(repo).is_file())


class ConsiliencyIngestShapeTest(unittest.TestCase):
    def _validate_manifest(self, repo: Path) -> dict:
        manifest = json.loads(manifest_path(repo).read_text(encoding="utf-8"))
        Draft202012Validator(load_schema("manifest")).validate(manifest)
        return manifest

    def test_each_archetype_shapes_a_schema_valid_manifest(self):
        for archetype in ARCHETYPE_IDS:
            with self.subTest(archetype=archetype):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    result = ingest(repo, adopt=True, mode="archetyped", archetypes=(archetype,))
                    self.assertEqual(result.mode, "shape")
                    manifest = self._validate_manifest(repo)
                    self.assertEqual(manifest["adoption"]["archetype"], archetype)
                    self.assertEqual(manifest["adoption"]["adopted"], True)
                    self.assertTrue(manifest["governed_set"])

    def test_governed_set_is_proposed_from_declared_documents_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = ingest(repo, adopt=True, mode="baseline-only")
            manifest = self._validate_manifest(repo)
            declared_paths = {d["path"] for d in manifest["documents"]}
            governed_paths = {s["value"] for s in manifest["governed_set"]}
            self.assertTrue(governed_paths)
            self.assertTrue(governed_paths.issubset(declared_paths))
            self.assertEqual(len(result.governed_set), len(manifest["governed_set"]))

    def test_scratchpad_and_other_harness_dirs_are_never_claimed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            notes_dir = repo / "notes"
            notes_dir.mkdir()
            (notes_dir / "x.wip.md").write_text("scratch note\n", encoding="utf-8")
            phase_loop_dir = repo / ".phase-loop"
            phase_loop_dir.mkdir()
            (phase_loop_dir / "state.json").write_text("{}\n", encoding="utf-8")

            result = ingest(repo, adopt=True, mode="baseline-only")
            governed_paths = {s["value"] for s in result.governed_set}
            self.assertFalse(any(".phase-loop" in p for p in governed_paths))
            self.assertFalse(any("wip" in p for p in governed_paths))
            # never touched, not merely unclaimed
            self.assertEqual((notes_dir / "x.wip.md").read_text(encoding="utf-8"), "scratch note\n")
            self.assertEqual((phase_loop_dir / "state.json").read_text(encoding="utf-8"), "{}\n")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = ingest(repo, adopt=True, mode="baseline-only", dry_run=True)
            self.assertEqual(result.mode, "shape")
            self.assertTrue(result.dry_run)
            self.assertFalse((repo / ".consiliency").exists())
            self.assertTrue(result.governed_set)

    def test_never_touches_phase_loop_or_pipeline_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            phase_loop_dir = repo / ".phase-loop"
            phase_loop_dir.mkdir()
            sentinel = phase_loop_dir / "state.json"
            sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")

            ingest(repo, adopt=True, mode="archetyped", archetypes=("tooling-meta",))

            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"sentinel": true}\n')


class ConsiliencyIngestVerifyTest(unittest.TestCase):
    def test_second_pass_is_a_clean_verify_with_no_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            ingest(repo, adopt=True, mode="archetyped", archetypes=("library",))
            before = manifest_path(repo).read_text(encoding="utf-8")

            second = ingest(repo, adopt=True, mode="archetyped", archetypes=("library",))

            after = manifest_path(repo).read_text(encoding="utf-8")
            self.assertEqual(second.mode, "verify")
            self.assertEqual(before, after)
            self.assertIsNotNone(second.gate_scan)
            governed = [label for label in second.document_labels if label["governed"]]
            self.assertTrue(governed, "declared documents should verify as governed")
            # Genuinely a clean verify now. Two historical soft-warn sources
            # are both gone: consiliency-contract 0.2.1 fixed
            # contract-version-status.schema.json's version pin
            # (^0.1.0 -> ^0.2.\\d+), so version-status conforms -- no more
            # governance.present_nonconforming finding, no
            # contract-version-status label. Then 0.3.0 rebalanced the
            # required-documents registry: `license` moved off the
            # `library` archetype onto the (unrequested) `public` modifier,
            # so the "library" fixture's missing LICENSE is no longer a
            # required-doc gap at all -- the presence gate has nothing left
            # to warn about.
            self.assertEqual(second.gate_scan["status"], "passed")
            self.assertEqual(second.gate_scan["gates"]["presence"]["status"], "passed")
            self.assertEqual({f["code"] for f in second.findings}, set())
            nonconforming = [label["doc_id"] for label in second.document_labels if label.get("labels")]
            self.assertEqual(nonconforming, [])

    def test_hand_corrupted_manifest_is_flagged_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            ingest(repo, adopt=True, mode="baseline-only")
            manifest_file = manifest_path(repo)
            manifest_file.write_text("{not valid json", encoding="utf-8")

            result = ingest(repo, adopt=True, mode="baseline-only")

            self.assertEqual(result.mode, "verify")
            self.assertEqual(manifest_file.read_text(encoding="utf-8"), "{not valid json")
            self.assertEqual(result.gate_scan["gates"]["layout_validity"]["status"], "warn")
            self.assertEqual(result.document_labels, ())

    def test_verify_ignores_adopt_flag_and_never_reshapes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            ingest(repo, adopt=True, mode="baseline-only")
            glossary_path = repo / ".consiliency" / "docs" / "glossary.md"
            glossary_path.write_text("# Real, human-authored glossary\n", encoding="utf-8")

            result = ingest(repo, adopt=True, mode="archetyped", archetypes=("product",))

            self.assertEqual(result.mode, "verify")
            self.assertEqual(glossary_path.read_text(encoding="utf-8"), "# Real, human-authored glossary\n")

    def test_nonconforming_declared_doc_warns_and_is_labeled(self):
        # Corrupt interfaces.json rather than status.json: interfaces.json
        # genuinely conforms when freshly scaffolded (see
        # test_second_pass_is_a_clean_verify_with_no_rewrite's note on the
        # pre-existing status.json schema quirk), so corrupting it isolates
        # the present-nonconforming detection path from that known issue
        # instead of accidentally re-exercising it.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            ingest(repo, adopt=True, mode="baseline-only")
            interfaces_file = repo / ".consiliency" / "interfaces.json"
            interfaces = json.loads(interfaces_file.read_text(encoding="utf-8"))
            del interfaces["realized_edges"]  # required field -- makes the doc schema-invalid without touching JSON validity
            original = json.dumps(interfaces)
            interfaces_file.write_text(original, encoding="utf-8")

            result = ingest(repo, adopt=True, mode="baseline-only")

            self.assertEqual(result.mode, "verify")
            self.assertEqual(interfaces_file.read_text(encoding="utf-8"), original)  # never rewritten
            nonconforming = [
                label for label in result.document_labels
                if label["doc_id"] == "interface-declaration"
            ]
            self.assertEqual(len(nonconforming), 1)
            self.assertEqual(nonconforming[0]["labels"], ["present-nonconforming"])
            self.assertEqual(nonconforming[0]["status"], "warn")
            self.assertTrue(nonconforming[0]["governed"])  # still governed -- present-nonconforming warns, doesn't ungovern
            codes = {f["code"] for f in result.findings}
            self.assertIn("governance.present_nonconforming", codes)

    def test_manifest_without_adoption_profile_verifies_but_labels_everything_ungoverned(self):
        # A manifest scaffolded directly by `consiliency-scaffold` (bypassing
        # `consiliency-ingest --adopt`) has no adoption profile at all --
        # verify still runs the CS-0.6 gates, but the CS-0.12 governance
        # labels are all "not-adopted": presence of a manifest is a
        # different, older consent point than the adoption profile.
        from phase_loop_runtime.consiliency_scaffold import scaffold as cs05_scaffold

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            cs05_scaffold(repo, mode="baseline-only")

            result = ingest(repo)

            self.assertEqual(result.mode, "verify")
            self.assertFalse(result.adopted)
            self.assertTrue(result.document_labels)
            self.assertTrue(all(not label["governed"] for label in result.document_labels))
            self.assertTrue(all(label["reason"] == "not-adopted" for label in result.document_labels))


class ConsiliencyIngestCLITest(unittest.TestCase):
    def test_cli_unflagged_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--json"],
                text=True, capture_output=True, check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "skipped")
            self.assertFalse((repo / ".consiliency").exists())

    def test_cli_adopt_shapes_then_second_invocation_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            first = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--adopt", "--archetype", "service", "--json"],
                text=True, capture_output=True, check=True,
            )
            payload = json.loads(first.stdout)
            self.assertEqual(payload["mode"], "shape")
            self.assertTrue((repo / ".consiliency" / "manifest.json").is_file())

            second = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--adopt", "--archetype", "service", "--json"],
                text=True, capture_output=True, check=True,
            )
            payload2 = json.loads(second.stdout)
            self.assertEqual(payload2["mode"], "verify")

    def test_cli_rejects_baseline_only_with_archetype(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--adopt", "--archetype", "library", "--baseline-only"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)


class ConsiliencyIngestCheckOnlyTest(unittest.TestCase):
    """`--check-only` decouples "run the check" from "is this repo adopted": an
    un-adopted repo returns an explicit, honest not-adopted signal (mode
    `not-adopted`, distinct non-zero exit 3) instead of the silent green
    `skipped` no-op -- so a pre-PR actor is never misled into reading a no-op
    as a pass. On an adopted repo it is exactly the read-only verify pass."""

    def test_check_only_unadopted_is_an_explicit_not_adopted_signal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = ingest(repo, check_only=True)
            self.assertEqual(result.mode, "not-adopted")
            self.assertFalse(result.adopted)
            self.assertEqual(result.findings[0]["code"], "adoption.not_adopted")
            # Strictly read-only: nothing shaped.
            self.assertFalse((repo / ".consiliency").exists())

    def test_check_only_ignores_adopt_and_never_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            # Even with --adopt, --check-only must not write a manifest.
            result = ingest(repo, check_only=True, adopt=True, mode="baseline-only")
            self.assertEqual(result.mode, "not-adopted")
            self.assertFalse((repo / ".consiliency").exists())

    def test_check_only_on_adopted_repo_is_a_verify(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            ingest(repo, adopt=True, mode="baseline-only")
            result = ingest(repo, check_only=True)
            self.assertEqual(result.mode, "verify")
            self.assertIsNotNone(result.gate_scan)

    def test_cli_check_only_unadopted_exits_3_with_distinct_signal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--check-only", "--json"],
                text=True, capture_output=True,
            )
            # Distinct from usage-error 2 and passing 0.
            self.assertEqual(result.returncode, 3)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "not-adopted")
            self.assertFalse((repo / ".consiliency").exists())

    def test_cli_check_only_on_adopted_repo_exits_0(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--adopt", "--archetype", "service", "--json"],
                text=True, capture_output=True, check=True,
            )
            result = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--check-only", "--json"],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "verify")

    def test_cli_check_only_on_blocked_scan_exits_1_distinct_from_not_adopted(self):
        # An adopted service+public repo with LICENSE declared-missing blocks
        # under hard mode; --check-only must surface that as a distinct non-zero
        # exit (1), NOT the passing 0 -- a failing verify is not a pass either.
        env = {**os.environ, "PHASE_LOOP_CONSILIENCY_GATES": "hard"}
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--adopt",
                 "--archetype", "service", "--modifier", "public", "--json"],
                text=True, capture_output=True, check=True,
            )
            self.assertFalse((repo / "LICENSE").exists())
            result = subprocess.run(
                [*BIN, "consiliency-ingest", "--repo", str(repo), "--check-only", "--json"],
                text=True, capture_output=True, env=env,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "verify")
            self.assertEqual(payload["gate_scan"]["status"], "blocked")
            self.assertEqual(result.returncode, 1)  # distinct from 3 (not-adopted) and 2 (usage)


class ConformanceNamedLibraryTest(unittest.TestCase):
    """The named public surface `phase_loop_runtime.conformance` re-exports the
    IDENTICAL evaluator + pure cores (not a re-implementation) so an external
    CR-fence can import and run the same function the actor runs."""

    def test_named_entrypoint_reexports_the_same_function(self):
        from phase_loop_runtime import conformance
        from phase_loop_runtime.consiliency_gates import scan_consiliency_gates as impl
        from phase_loop_runtime.git_discipline import (
            evaluate_git_discipline as gd_impl,
            self_heal_partition as heal_impl,
        )
        from phase_loop_runtime.consiliency_ingest import evaluate_governance_scope as gov_impl

        self.assertIs(conformance.scan_consiliency_gates, impl)
        self.assertIs(conformance.evaluate_git_discipline, gd_impl)
        self.assertIs(conformance.self_heal_partition, heal_impl)
        self.assertIs(conformance.evaluate_governance_scope, gov_impl)
        for name in conformance.__all__:
            self.assertTrue(hasattr(conformance, name), name)

    def test_named_entrypoint_runs_on_an_arbitrary_repo(self):
        from phase_loop_runtime.conformance import scan_consiliency_gates
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            verdict = scan_consiliency_gates(str(repo))
            # Un-adopted repo: consent-gated no-op.
            self.assertEqual(verdict["status"], "skipped")
            self.assertFalse(verdict["consent"])


if __name__ == "__main__":
    unittest.main()
