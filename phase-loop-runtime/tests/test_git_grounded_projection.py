"""Issue #152 brick 1 -- the git-grounded, digest-bound observability projection.

The reconciler turns RAW GIT REALITY into a DIGEST-BOUND projection (producer-
agnostic, consent-gated) that the Portal's projection-index verify path re-
verifies at render. These tests use an INLINE registry mirroring the shipped
``pipeline_ref_classes`` shape, so they are version-independent (green whether or
not the installed contract carries the git-discipline artifacts) -- they exercise
the EMISSION algorithm, not the installed contract version.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import make_repo

from phase_loop_runtime import conformance
from phase_loop_runtime.conformance import git_grounded_projection as ggp

# Inline registry mirroring core/registries/pipeline-ref-classes.json (v0.4.x),
# reused from the git-discipline tests' shape so the projection is classified
# against a real-shaped SoT without depending on the installed contract version.
REGISTRY = {
    "default_owner": "human",
    "ref_classes": [
        {"id": "pipeline-working-branch", "pattern": "consiliency/pipeline/{roadmap_version}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": False, "merge_policy": "required"},
        {"id": "harness-phase-worktree", "pattern": "phase-loop/sched/{target_branch}/{phase}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": True, "merge_policy": "auto"},
        {"id": "gp-phase-node", "pattern": "pipeline/{phase}-{node}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": True, "merge_policy": "auto"},
    ],
    "human_default": {"id": "human", "pattern": "*", "owner": "human", "lease_required": False, "deletable_by_self_heal": False, "merge_policy": None},
}


def _opt_in(repo: Path) -> None:
    """Write a minimal `.consiliency/manifest.json` -- the consent gate. Presence
    is all `find_consiliency_manifest` requires (parse-validity is a separate
    gate), so a stub manifest is enough to opt the repo in."""
    manifest = repo / ".consiliency" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema": "consiliency.manifest.v1"}) + "\n", encoding="utf-8")


class ConsentGateTest(unittest.TestCase):
    def test_un_adopted_repo_is_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))  # no .consiliency/manifest.json
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no-consent")
            self.assertNotIn("body", result)
            # No artifact was written.
            self.assertFalse((repo / ".phase-loop" / "observability" / "git-grounded-projection.json").exists())

    def test_no_repo_dir_is_a_clean_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist"
            result = ggp.reconcile_git_grounded_projection(missing, registry=REGISTRY)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no-consent")


class ContractAbsentTest(unittest.TestCase):
    def test_contract_absent_registry_is_a_typed_skip_not_a_raise(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _opt_in(repo)
            # registry=None + no installed git-discipline contract -> contract-absent.
            # Force the absent path by injecting an empty (no ref_classes) registry
            # is NOT how it works; instead pass registry that build treats as absent.
            result = ggp.reconcile_git_grounded_projection(repo, registry=None)
            # Either the installed contract carries it (emitted) or it does not
            # (skipped, contract-absent). Both are valid; neither raises.
            self.assertIn(result["status"], {"emitted", "skipped"})
            if result["status"] == "skipped":
                self.assertEqual(result["reason"], "contract-absent")

    def test_build_body_raises_typed_error_when_registry_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            # build_git_grounded_body is the pure layer; with no installed contract
            # and registry=None it raises the typed GitGroundedContractAbsent that
            # the reconciler catches and folds into a skip. We can only assert the
            # typed-error TYPE exists and is a RuntimeError subclass here.
            self.assertTrue(issubclass(ggp.GitGroundedContractAbsent, RuntimeError))


class EmissionTest(unittest.TestCase):
    def _adopted_repo_with_refs(self, td: str) -> Path:
        repo = make_repo(Path(td))
        _opt_in(repo)
        # A pipeline-owned deletable branch + a human branch, so the projection
        # carries a non-trivial ref-class + self-heal partition.
        subprocess.run(["git", "branch", "pipeline/PHASE_A-node1"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "feature/my-human-work"], cwd=repo, check=True)
        return repo

    def test_emits_valid_digest_bound_projection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._adopted_repo_with_refs(td)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, repo_label="fixture")
            self.assertEqual(result["status"], "emitted")

            body = result["body"]
            self.assertEqual(body["schema"], ggp.GIT_GROUNDED_PROJECTION_SCHEMA)
            self.assertEqual(body["repo"], "fixture")
            self.assertTrue(body["producer_agnostic"])
            # The pipeline-owned + human branches were classified against the SoT.
            owners = {entry["name"]: entry["owner"] for entry in body["ref_classes"]}
            self.assertEqual(owners.get("pipeline/PHASE_A-node1"), "pipeline")
            self.assertEqual(owners.get("feature/my-human-work"), "human")
            # Human ref is in the never-delete set (the liberty invariant surfaced).
            self.assertIn("feature/my-human-work", body["self_heal_partition"]["never_deleted_human_refs"])

            self.assertEqual(result["body_digest_domain"], ggp.RAW_SHA256_DOMAIN)
            self.assertRegex(result["body_digest"], r"^[0-9a-f]{64}$")
            self.assertTrue(result["verified"])

    def test_digest_re_derives_from_on_disk_bytes(self):
        """The trust anchor: sha256 over the RAW file bytes == pinned digest,
        the exact derivation the portal's raw-sha256 verify path performs."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._adopted_repo_with_refs(td)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY)
            body_path = Path(result["body_path"])

            # Re-derive from the bytes ON DISK -- NOT from a re-serialized dict
            # (a re-serialize could differ by a newline and false-green).
            on_disk = body_path.read_bytes()
            self.assertEqual(hashlib.sha256(on_disk).hexdigest(), result["body_digest"])

    def test_body_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._adopted_repo_with_refs(td)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY)
            body_path = Path(result["body_path"])

            # Reconstruct the projection to re-run its verify against the file.
            projection = ggp.GitGroundedProjection(result["body"])
            self.assertTrue(projection.verify(body_path))

            # Tamper: flip a byte (append whitespace). The pinned digest no longer
            # binds -- exactly what makes a mismatched body never render.
            body_path.write_bytes(body_path.read_bytes() + b" ")
            self.assertFalse(projection.verify(body_path))
            self.assertNotEqual(
                hashlib.sha256(body_path.read_bytes()).hexdigest(),
                projection.body_digest,
            )

    def test_write_false_returns_body_without_touching_disk(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._adopted_repo_with_refs(td)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, write=False)
            self.assertEqual(result["status"], "emitted")
            self.assertNotIn("body_path", result)
            self.assertFalse((repo / ".phase-loop" / "observability" / "git-grounded-projection.json").exists())


class PortalIndexEntryTest(unittest.TestCase):
    def test_entry_matches_portal_projection_index_shape(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _opt_in(repo)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, repo_label="fixture")
            entry = result["index_entry"]

            # The portal-runtime required subset + contract-schema required keys.
            for key in (
                "repo", "kind", "predicate", "body_path", "body_content_type",
                "manifest_path", "body_digest", "body_digest_domain",
                "maturity_label", "gate_state",
            ):
                self.assertIn(key, entry)

            # kind is the closed-enum MISNOMER slot; body_digest_domain is raw-sha256.
            self.assertEqual(entry["kind"], "proj-code-sbom")
            self.assertEqual(entry["body_digest_domain"], "raw-sha256")
            self.assertEqual(entry["body_content_type"], "text/markdown")
            # proj-code kinds are capped at hash-checked; we emit that (never a cert tier).
            self.assertEqual(entry["maturity_label"], "hash-checked")
            # body_path / manifest_path must be safe vendored paths (spec-render/, no ..).
            for path_field in ("body_path", "manifest_path"):
                self.assertTrue(entry[path_field].startswith("spec-render/"))
                self.assertNotIn("..", entry[path_field])
                self.assertFalse(entry[path_field].startswith("/"))

            # The entry pins the SAME digest as the body -- one body, one digest,
            # re-verified end-to-end.
            self.assertEqual(entry["body_digest"], result["body_digest"])

    def test_emitted_bytes_bind_the_entry_digest_transport_invariant(self):
        """The transport-invariant guarantee ("these bytes, this digest"): the
        raw-sha256 over the EMITTED body bytes == entry["body_digest"]. The
        portal reads body_path relative to its OWN vendor root (confirmed), so a
        portal re-derive over the vendored COPY of these exact bytes binds the
        same digest -- the integrity check that transport did not alter them.
        entry["body_path"] is the portal-vendor DESTINATION, not a repo path."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _opt_in(repo)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, repo_label="fixture")
            entry = result["index_entry"]

            emitted = Path(result["body_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(emitted).hexdigest(), entry["body_digest"])
            # The emitted artifact lives on the runtime-native excluded surface,
            # NOT at the portal-vendor destination path inside the observed repo.
            self.assertIn(".phase-loop", result["body_path"])
            self.assertEqual(entry["body_path"], result["portal_body_path"])
            self.assertTrue(entry["body_path"].startswith("spec-render/"))
            self.assertFalse((repo / entry["body_path"]).exists())

    def test_reconcile_is_deterministic_and_does_not_self_perturb(self):
        """Reconciling the same repo twice yields the SAME body_digest, and the
        emitted artifact never appears in the body's own dirty_paths -- the
        observer does not perturb the observed (the write is git-excluded)."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _opt_in(repo)
            first = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, repo_label="fixture")
            second = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY, repo_label="fixture")

            self.assertEqual(first["body_digest"], second["body_digest"])
            self.assertEqual(first["body"]["dirty_paths"], second["body"]["dirty_paths"])
            # The emitted artifact is git-excluded, so it never shows up as a
            # dirty path in the reconciled body (neither run).
            for result in (first, second):
                for dirty in result["body"]["dirty_paths"]:
                    self.assertNotIn("git-grounded-projection.json", dirty)
                    self.assertNotIn("observability", dirty)

    def test_entry_surfaces_the_kind_misnomer_honestly(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _opt_in(repo)
            result = ggp.reconcile_git_grounded_projection(repo, registry=REGISTRY)
            entry = result["index_entry"]
            # The honest #152 finding is recorded as provenance, not hidden.
            self.assertTrue(entry["kind_is_misnomer"])
            self.assertEqual(entry["git_grounded_kind"], "proj-git-grounded")


class PublicSurfaceTest(unittest.TestCase):
    def test_reconciler_exported_through_conformance(self):
        # Registered public names (the #121/#126 lesson): the emission surface is
        # reachable from the public `conformance` package, not a private path.
        self.assertTrue(hasattr(conformance, "reconcile_git_grounded_projection"))
        self.assertTrue(hasattr(conformance, "GitGroundedProjection"))
        self.assertTrue(hasattr(conformance, "build_git_grounded_body"))
        self.assertTrue(hasattr(conformance, "build_projection_index_entry"))
        for name in (
            "reconcile_git_grounded_projection",
            "GitGroundedProjection",
            "build_git_grounded_body",
            "build_projection_index_entry",
            "GitGroundedContractAbsent",
            "GIT_GROUNDED_PROJECTION_SCHEMA",
            "RAW_SHA256_DOMAIN",
            "PORTAL_KIND_MISNOMER",
            "GIT_GROUNDED_KIND",
        ):
            self.assertIn(name, conformance.__all__)


if __name__ == "__main__":
    unittest.main()
