"""Slice G -- the git-discipline guardrail + bounded top-of-loop self-heal.

The runtime is a CONSUMER of the neutral ``@consiliency/contract`` git-discipline
contract. Most tests use a small INLINE registry that mirrors the shipped
``pipeline_ref_classes`` shape, so they are version-independent (green whether or
not the installed contract carries the git-discipline artifacts). One test
REPLAYS the real contract conformance vector (skipped when the installed contract
predates it), and one asserts the contract-absent DEGRADE path.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import make_repo

from phase_loop_runtime import git_discipline as gd
from phase_loop_runtime.consiliency_gates import _gate_git_discipline

# Inline registry mirroring core/registries/pipeline-ref-classes.json (v0.4.x).
# Kept local so the pure-logic tests never depend on the installed contract
# version -- they exercise the classification/partition ALGORITHM.
REGISTRY = {
    "default_owner": "human",
    "ref_classes": [
        {"id": "pipeline-working-branch", "pattern": "consiliency/pipeline/{roadmap_version}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": False, "merge_policy": "required"},
        {"id": "pipeline-working-branch-family", "pattern": "consiliency/pipeline/*", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": False, "merge_policy": "required"},
        {"id": "harness-phase-worktree", "pattern": "phase-loop/sched/{target_branch}/{phase}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": True, "merge_policy": "auto"},
        {"id": "gp-phase-node", "pattern": "pipeline/{phase}-{node}", "owner": "pipeline", "lease_required": True, "deletable_by_self_heal": True, "merge_policy": "auto"},
    ],
    "human_default": {"id": "human", "pattern": "*", "owner": "human", "lease_required": False, "deletable_by_self_heal": False, "merge_policy": None},
}

# Inline protocol schema fragment carrying the write-footprint allowlist const.
PROTOCOL = {
    "properties": {
        "write_footprint": {
            "properties": {
                "allowlist": {
                    "const": [
                        ".pipeline/artifacts/**",
                        ".consiliency/**",
                        "pipeline.definition.json",
                        "portal_contracts/**",
                    ]
                }
            }
        }
    }
}


class ClassificationTest(unittest.TestCase):
    def test_pipeline_and_human_refs_classified(self):
        cases = {
            "consiliency/pipeline/v0.4": ("pipeline", False),
            "consiliency/pipeline/v0.3": ("pipeline", False),
            "phase-loop/sched/main/PHASE_A": ("pipeline", True),
            "pipeline/impl-node3": ("pipeline", True),
            "main": ("human", False),
            "feature/my-wip": ("human", False),
        }
        for name, (owner, deletable) in cases.items():
            with self.subTest(name=name):
                cls = gd.classify_ref(name, REGISTRY)
                self.assertEqual(cls.owner, owner)
                self.assertEqual(cls.deletable_by_self_heal, deletable)

    def test_anchoring_rejects_superficial_resemblance(self):
        # Load-bearing: unanchored, `pipeline/{phase}-{node}` would match these as
        # substrings and misclassify a human ref as pipeline-owned/deletable.
        for human_lookalike in (
            "my-pipeline/foo-bar",          # `pipeline/...` is a substring, not a prefix
            "xconsiliency/pipeline/v1",     # not anchored at the start
            "pipeline/deep/impl-node1",     # extra path segment -> drifted, not canonical
            "feature/pipeline/impl-node1",  # pipeline-ish but human-owned
        ):
            with self.subTest(name=human_lookalike):
                self.assertEqual(gd.classify_ref(human_lookalike, REGISTRY).owner, "human")


class SelfHealPartitionTest(unittest.TestCase):
    # The seven refs of the contract conformance vector, expressed as RefState.
    REFS = [
        gd.RefState("consiliency/pipeline/v0.4", leased=True, merged=False),
        gd.RefState("consiliency/pipeline/v0.3", leased=False, merged=True),
        gd.RefState("phase-loop/sched/main/PHASE_A", leased=True, merged=True),
        gd.RefState("pipeline/impl-node3", leased=True, merged=True),
        gd.RefState("pipeline/impl-node9", leased=False, merged=True),
        gd.RefState("main", leased=False, merged=False),
        gd.RefState("feature/my-wip", leased=True, merged=False),
    ]

    def test_partition_matches_contract_expectation(self):
        part = gd.self_heal_partition(self.REFS, REGISTRY)
        self.assertEqual(
            set(part["deletable_by_self_heal"]),
            {"phase-loop/sched/main/PHASE_A", "pipeline/impl-node3"},
        )
        self.assertEqual(set(part["human_refs"]), {"main", "feature/my-wip"})

    def test_never_delete_human_refs_invariant(self):
        part = gd.self_heal_partition(self.REFS, REGISTRY)
        # THE proof: human refs are disjoint from what self-heal may delete.
        self.assertTrue(set(part["human_refs"]).isdisjoint(part["deletable_by_self_heal"]))
        self.assertEqual(set(part["never_deleted_human_refs"]), set(part["human_refs"]))

    def test_leased_human_ref_still_protected(self):
        # feature/my-wip is leased=True but human-owned -> never deletable.
        part = gd.self_heal_partition(self.REFS, REGISTRY)
        self.assertIn("feature/my-wip", part["protected"])
        self.assertNotIn("feature/my-wip", part["deletable_by_self_heal"])

    def test_unleased_pipeline_ref_protected(self):
        # pipeline/impl-node9 is in a deletable class but UNLEASED -> protected.
        part = gd.self_heal_partition(self.REFS, REGISTRY)
        self.assertIn("pipeline/impl-node9", part["protected"])

    def test_non_deletable_pipeline_class_protected(self):
        # consiliency/pipeline/* is pipeline-owned but a non-deletable class.
        part = gd.self_heal_partition(self.REFS, REGISTRY)
        self.assertIn("consiliency/pipeline/v0.4", part["protected"])
        self.assertIn("consiliency/pipeline/v0.3", part["protected"])


class GuardrailEvaluateTest(unittest.TestCase):
    def test_write_footprint_violation_on_pipeline_branch(self):
        findings = gd.evaluate_git_discipline(
            current_branch="consiliency/pipeline/v0.4",
            dirty_paths=["src/app.py", ".pipeline/artifacts/x.json", ".consiliency/manifest.json"],
            local_branches=["consiliency/pipeline/v0.4"],
            registry=REGISTRY,
            protocol=PROTOCOL,
        )
        codes = {(f["code"], f.get("path")) for f in findings}
        self.assertIn(("write_footprint_violation", "src/app.py"), codes)
        # Allowlisted writes do not fire.
        self.assertNotIn(("write_footprint_violation", ".pipeline/artifacts/x.json"), codes)
        self.assertNotIn(("write_footprint_violation", ".consiliency/manifest.json"), codes)

    def test_no_footprint_enforcement_on_human_branch(self):
        findings = gd.evaluate_git_discipline(
            current_branch="feature/my-wip",
            dirty_paths=["src/app.py"],
            local_branches=["feature/my-wip"],
            registry=REGISTRY,
            protocol=PROTOCOL,
        )
        self.assertEqual(findings, [])

    def test_branch_naming_drift_detected(self):
        findings = gd.evaluate_git_discipline(
            current_branch="main",
            dirty_paths=[],
            local_branches=["main", "pipeline/deep/impl-node1", "consiliency/pipeline/v0.4"],
            registry=REGISTRY,
            protocol=PROTOCOL,
        )
        drift = {f["branch"] for f in findings if f["code"] == "pipeline_branch_naming_drift"}
        self.assertIn("pipeline/deep/impl-node1", drift)  # drifted from the contract shape
        self.assertNotIn("consiliency/pipeline/v0.4", drift)  # canonical -> not drift
        self.assertNotIn("main", drift)  # plain human ref -> not drift


class GateSeverityTest(unittest.TestCase):
    """The gate WARNS by default and BLOCKS only on the opt-in, never sets
    human_required. Registry/facts are injected so the test is version-independent."""

    def _patch(self, current_branch, dirty_paths):
        self._orig_load = gd.load_ref_classes
        self._orig_proto = gd.load_protocol
        self._orig_facts = gd.gather_repo_ref_facts
        gd.load_ref_classes = lambda: REGISTRY
        gd.load_protocol = lambda: PROTOCOL
        gd.gather_repo_ref_facts = lambda repo: {
            "current_branch": current_branch,
            "dirty_paths": tuple(dirty_paths),
            "local_branches": (current_branch,),
        }

    def tearDown(self):
        if hasattr(self, "_orig_load"):
            gd.load_ref_classes = self._orig_load
            gd.load_protocol = self._orig_proto
            gd.gather_repo_ref_facts = self._orig_facts

    def test_warns_by_default_blocks_on_opt_in(self):
        self._patch("consiliency/pipeline/v0.4", ["src/app.py"])
        warn = _gate_git_discipline(Path("/nonexistent"), mode="warn")
        hard = _gate_git_discipline(Path("/nonexistent"), mode="hard")
        self.assertEqual(warn["status"], "warn")
        self.assertEqual(hard["status"], "blocked")
        self.assertTrue(warn["findings"])
        # The gate never emits a human_required signal.
        self.assertNotIn("human_required", warn)
        for finding in warn["findings"]:
            self.assertNotIn("human_required", finding)

    def test_clean_pipeline_branch_passes(self):
        self._patch("consiliency/pipeline/v0.4", [".consiliency/manifest.json"])
        result = _gate_git_discipline(Path("/nonexistent"), mode="hard")
        self.assertEqual(result["status"], "passed")


class ContractAbsentDegradeTest(unittest.TestCase):
    def setUp(self):
        self._orig = gd.load_ref_classes
        gd.load_ref_classes = lambda: None  # simulate contract < 0.4

    def tearDown(self):
        gd.load_ref_classes = self._orig

    def test_gate_degrades_to_passed_not_warn(self):
        # Neutral no-op even in hard mode -- must NOT flip governed scans to warn.
        result = _gate_git_discipline(Path("/nonexistent"), mode="hard")
        self.assertEqual(result["status"], "passed")
        self.assertIn("latent", result["note"])

    def test_self_heal_skips_when_contract_absent(self):
        result = gd.reconcile_git_discipline(Path("/nonexistent"))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "contract-absent")


class SelfHealConsentTest(unittest.TestCase):
    """Design §11.5: even with the contract installed, a repo that has NOT
    opted in (no `.consiliency/manifest.json`) is a pure no-op -- no
    classification, no advisories, no `git worktree prune`."""

    def setUp(self):
        self._orig = gd.load_ref_classes
        gd.load_ref_classes = lambda: REGISTRY  # simulate contract present (>=0.4)

    def tearDown(self):
        gd.load_ref_classes = self._orig

    def test_self_heal_skips_ungoverned_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))  # no .consiliency/manifest.json
            result = gd.reconcile_git_discipline(repo, execute_prune=True)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "no-consent")
            self.assertEqual(result["findings"], [])


class ApplyDeletionGuardTest(unittest.TestCase):
    def setUp(self):
        self._orig = gd.load_ref_classes
        gd.load_ref_classes = lambda: REGISTRY

    def tearDown(self):
        gd.load_ref_classes = self._orig

    def test_apply_refuses_ref_not_in_deletable_class(self):
        # A malformed partition that sneaks a human ref into the deletable set is
        # refused at the mutation boundary (the invariant re-asserted), never
        # reaching `git branch -d`.
        bogus = {"deletable_by_self_heal": ["main", "feature/my-wip"]}
        result = gd.apply_self_heal_deletions(Path("/nonexistent"), bogus)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(set(result["refused"]), {"main", "feature/my-wip"})


class SelfHealEndToEndGitTest(unittest.TestCase):
    """A real git repo: prove the partition holds against real refs -- only the
    leased+merged pipeline phase-node is deleted; human refs survive untouched."""

    def _branch(self, repo, name):
        subprocess.run(["git", "branch", name], cwd=repo, check=True)

    def test_only_deletable_ref_removed_human_refs_survive(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))  # default branch created + one commit
            for name in ("feature/my-wip", "consiliency/pipeline/v1", "pipeline/impl-node1"):
                self._branch(repo, name)  # all merged into the default branch

            # Lease only the phase-node branch.
            states = gd.gather_pipeline_ref_states(
                repo, REGISTRY, base_ref="HEAD",
                lease_predicate=lambda n: n == "pipeline/impl-node1",
            )
            part = gd.self_heal_partition(states, REGISTRY)
            self.assertEqual(set(part["deletable_by_self_heal"]), {"pipeline/impl-node1"})

            outcome = gd.apply_self_heal_deletions(repo, part, registry=REGISTRY)
            self.assertEqual(outcome["deleted"], ["pipeline/impl-node1"])

            remaining = subprocess.run(
                ["git", "branch", "--format=%(refname:short)"],
                cwd=repo, check=True, capture_output=True, text=True,
            ).stdout.split()
            self.assertIn("feature/my-wip", remaining)          # human ref survives
            self.assertIn("consiliency/pipeline/v1", remaining)  # non-deletable class survives
            self.assertNotIn("pipeline/impl-node1", remaining)   # only the eligible ref removed


def _load_real_vector():
    """The shipped conformance vector, or None when the installed contract
    predates the git-discipline artifacts (< 0.4)."""
    try:
        from consiliency_contract import load_vector

        return load_vector("git-discipline-never-delete-human-refs")
    except Exception:
        return None


class ContractVectorReplayTest(unittest.TestCase):
    """Replay the neutral contract's own conformance vector through the runtime's
    partition -- the interchangeability check that this consumer agrees with the
    contract. Skipped (not failed) when the installed contract lacks it."""

    @unittest.skipUnless(gd.available(), "installed consiliency_contract lacks the git-discipline contract (<0.4)")
    def test_partition_reproduces_shipped_vector(self):
        vector = _load_real_vector()
        self.assertIsNotNone(vector, "git-discipline contract available but vector missing")
        registry = gd.load_ref_classes()
        refs = [
            gd.RefState(r["name"], leased=bool(r.get("leased")), merged=bool(r.get("merged")))
            for r in vector["input"]["refs"]
        ]
        part = gd.self_heal_partition(refs, registry)
        expected = vector["expected"]
        self.assertEqual(set(part["deletable_by_self_heal"]), set(expected["deletable_by_self_heal"]))
        self.assertEqual(set(part["protected"]), set(expected["protected"]))
        self.assertEqual(set(part["human_refs"]), set(expected["human_refs"]))
        self.assertEqual(set(part["never_deleted_human_refs"]), set(expected["never_deleted_human_refs"]))
        # The invariant, once more against the REAL contract data.
        self.assertTrue(set(part["human_refs"]).isdisjoint(part["deletable_by_self_heal"]))


if __name__ == "__main__":
    unittest.main()
