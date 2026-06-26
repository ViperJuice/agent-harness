"""TESTDECOUPLE SL-2: the bundled contract-doc package-data is resolvable AND in
sync with the canonical sources.

This is a runtime-core test (NO dotfiles_integration marker): it must pass
STANDALONE against an installed wheel with no dotfiles tree reachable. It proves
the repoint of the substrate-soak core tests works (the docs resolve via
``importlib.resources``, not ``parents[3]``) and that the bundle has not drifted
from the canonical dotfiles sources WHEN those sources are reachable (in-tree).
"""
from __future__ import annotations

import unittest

from _contract_docs import contract_doc, contract_doc_text
from _dotfiles_tree import dotfiles_root


# The contract docs every runtime-core test resolves from package-data. Kept in
# step with scripts/sync_runtime_package_data.py's manifest.
BUNDLED = (
    ("phase-loop", "substrate-soak-regenesis.md"),
    ("phase-loop", "substrate-soak-report.md"),
    ("phase-loop", "substrate-soak-governed-pipeline.md"),
    ("phase-loop", "substrate-soak-portal-projection.md"),
    ("phase-loop", "runtime-boundary.md"),
    ("phase-loop", "harness-substrate-manifest.md"),
    ("phase-loop", "harness-capability-matrix.md"),
    ("phase-loop", "harness-skill-matrix.md"),
    ("phase-loop", "granular-execution-policy.md"),
    ("phase-loop", "extraction-readiness.md"),
    ("phase-loop", "pi-loop-control.md"),
    ("phase-loop", "spec-discovery-roots.md"),
    ("phase-loop", "protocol.md"),
    ("runtime", "verification-evidence-contract.md"),
)

# bundled relpath under _contract_docs/  ->  canonical source relpath under dotfiles root
CANONICAL_SOURCE = {
    ("phase-loop", "substrate-soak-regenesis.md"): "docs/phase-loop/substrate-soak-regenesis.md",
    ("phase-loop", "substrate-soak-report.md"): "docs/phase-loop/substrate-soak-report.md",
    ("phase-loop", "substrate-soak-governed-pipeline.md"): "docs/phase-loop/substrate-soak-governed-pipeline.md",
    ("phase-loop", "substrate-soak-portal-projection.md"): "docs/phase-loop/substrate-soak-portal-projection.md",
    ("phase-loop", "runtime-boundary.md"): "docs/phase-loop/runtime-boundary.md",
    ("phase-loop", "harness-substrate-manifest.md"): "docs/phase-loop/harness-substrate-manifest.md",
    ("phase-loop", "harness-capability-matrix.md"): "docs/phase-loop/harness-capability-matrix.md",
    ("phase-loop", "harness-skill-matrix.md"): "docs/phase-loop/harness-skill-matrix.md",
    ("phase-loop", "granular-execution-policy.md"): "docs/phase-loop/granular-execution-policy.md",
    ("phase-loop", "extraction-readiness.md"): "docs/phase-loop/extraction-readiness.md",
    ("phase-loop", "pi-loop-control.md"): "docs/phase-loop/pi-loop-control.md",
    ("phase-loop", "spec-discovery-roots.md"): "docs/phase-loop/spec-discovery-roots.md",
    # The FULL canonical protocol doc, not the shared/ stub (which only points here).
    ("phase-loop", "protocol.md"): "vendor/phase-loop-runtime/protocol/protocol.md",
    ("runtime", "verification-evidence-contract.md"): "docs/runtime/verification-evidence-contract.md",
}


class RuntimePackageDataTest(unittest.TestCase):
    def test_every_bundled_doc_resolves_via_importlib_resources(self):
        # The point: resolution goes through importlib.resources (package-anchored),
        # so it works standalone. No parents[3] / dotfiles path involved.
        for parts in BUNDLED:
            with self.subTest(doc="/".join(parts)):
                text = contract_doc_text(*parts)
                self.assertTrue(text.strip(), f"empty bundled doc: {'/'.join(parts)}")

    def test_bundle_root_is_inside_the_installed_package_not_parents3(self):
        # contract_doc anchors on the importable package. Standalone there is no
        # parents[3] dotfiles root at all; the resolved path must still be a real
        # readable resource under phase_loop_runtime.
        node = contract_doc("phase-loop", "substrate-soak-report.md")
        self.assertTrue(node.is_file(), f"bundled doc not a file: {node}")
        self.assertIn("phase_loop_runtime", str(node))

    def test_bundle_is_byte_identical_to_canonical_sources_when_in_tree(self):
        # Drift guard. Only meaningful in-tree (the canonical docs/ sources are only
        # present under a dotfiles checkout); skip standalone where they are absent.
        root = dotfiles_root()
        if root is None:
            self.skipTest("dotfiles tree absent; canonical sources not reachable")
        for parts, src_rel in CANONICAL_SOURCE.items():
            with self.subTest(doc="/".join(parts)):
                canonical = (root / src_rel).read_bytes()
                bundled = contract_doc(*parts).read_bytes()
                self.assertEqual(
                    bundled,
                    canonical,
                    f"package-data {'/'.join(parts)} drifted from {src_rel}; "
                    "run scripts/sync_runtime_package_data.py",
                )


if __name__ == "__main__":
    unittest.main()
