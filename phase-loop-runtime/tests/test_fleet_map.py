from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.fleet_map import (
    EDGE_KINDS,
    MATURITY_LABELS,
    build_fleet_map,
    run_lockfile_baseline_scan,
)

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "fleet_map"
_FIXTURE_REPOS = (
    _FIXTURE_ROOT / "agent-harness",
    _FIXTURE_ROOT / "gp",
    _FIXTURE_ROOT / "consiliency-portal",
)


class FleetMapTest(unittest.TestCase):
    def test_finds_a_pin_edge_from_the_fixture_pin_json(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        pin_edges = [edge for edge in result.edges if edge.kind == "pin" and edge.from_repo == "gp" and edge.to_repo == "agent-harness"]
        self.assertTrue(pin_edges, msg=result.to_json())
        # The pin.json fixture edge resolves to a known repo among --repo inputs.
        self.assertTrue(
            any(edge.maturity_label == "realized-edge-observed" and edge.evidence.startswith("tools/agent-harness.pin.json") for edge in pin_edges),
            msg=pin_edges,
        )

    def test_finds_a_pin_edge_from_a_bootstrap_sh_style_git_plus_ref(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        script_edges = [edge for edge in result.edges if edge.kind == "pin" and "bootstrap.sh" in edge.evidence]
        self.assertTrue(script_edges, msg=result.to_json())
        self.assertEqual(script_edges[0].from_repo, "gp")
        self.assertEqual(script_edges[0].to_repo, "agent-harness")
        self.assertEqual(script_edges[0].maturity_label, "realized-edge-observed")

    def test_greenfield_contract_lock_json_pin_to_an_unknown_repo_is_presence_only(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        lock_edges = [edge for edge in result.edges if edge.kind == "pin" and edge.to_repo == "downstream-app"]
        self.assertTrue(lock_edges, msg=result.to_json())
        self.assertEqual(lock_edges[0].maturity_label, "presence-only")

    def test_finds_a_copied_literal_drift_edge_between_divergent_copies(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        drift_edges = [edge for edge in result.edges if edge.kind == "copied-literal"]
        self.assertEqual(len(drift_edges), 1, msg=result.to_json())
        edge = drift_edges[0]
        self.assertEqual({edge.from_repo, edge.to_repo}, {"gp", "consiliency-portal"})
        self.assertEqual(edge.maturity_label, "hash-checked")
        self.assertTrue(edge.evidence.endswith("example-contract.json:1"))

    def test_finds_a_host_path_edge_from_a_fixture_source_file(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        host_edges = [edge for edge in result.edges if edge.kind == "host-path"]
        self.assertTrue(host_edges, msg=result.to_json())
        edge = host_edges[0]
        self.assertEqual(edge.from_repo, "gp")
        self.assertEqual(edge.to_repo, "consiliency-portal")
        self.assertEqual(edge.maturity_label, "realized-edge-observed")
        self.assertTrue(edge.evidence.startswith("scripts/spec-certificate-gate.mjs:"))

    def test_every_edge_carries_a_valid_kind_and_maturity_label(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        self.assertTrue(result.edges)
        for edge in result.edges:
            self.assertIn(edge.kind, EDGE_KINDS)
            self.assertIn(edge.maturity_label, MATURITY_LABELS)

    def test_all_three_edge_kinds_from_the_spec_are_present(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        found_kinds = {edge.kind for edge in result.edges}
        self.assertEqual({"pin", "copied-literal", "host-path"} - found_kinds, set())

    def test_lockfile_baseline_scan_is_empty_over_the_same_fixture_tree(self):
        # The money-shot assertion: over the *identical* fixture tree the
        # extractor finds 3 edge kinds in, a package-lockfile-only scan finds
        # nothing — not because there's nothing to scan (the fixture repos
        # carry ordinary pyproject.toml/package.json/requirements.txt
        # third-party deps), but because package-level deps are not how these
        # repos are actually wired together.
        fleet_map_result = build_fleet_map(_FIXTURE_REPOS)
        baseline_edges = run_lockfile_baseline_scan(_FIXTURE_REPOS)

        self.assertTrue(fleet_map_result.edges)
        self.assertEqual(baseline_edges, ())
        self.assertEqual(fleet_map_result.lockfile_baseline_edges, ())

    def test_setup_diagnostic_for_a_missing_repo_path(self):
        result = build_fleet_map([_FIXTURE_ROOT / "does-not-exist"])

        self.assertTrue(result.has_setup_errors())
        self.assertEqual(result.setup_diagnostics[0].message, "repo path is missing or not a directory")

    def test_no_self_edges(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        for edge in result.edges:
            self.assertNotEqual(edge.from_repo, edge.to_repo)

    def test_json_roundtrip(self):
        result = build_fleet_map(_FIXTURE_REPOS)

        payload = json.loads(json.dumps(result.to_json()))
        self.assertEqual(payload["counts"]["edges"], len(result.edges))
        self.assertEqual(payload["counts"]["lockfile_baseline_edges"], 0)

    def test_cli_help_lists_fleet_map_flags(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
            build_parser().parse_args(["fleet-map", "--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        for flag in ("--repo", "--json"):
            self.assertIn(flag, help_text)

    def test_cli_reports_edges_as_json_and_exits_zero(self):
        argv = ["fleet-map", "--json"]
        for repo in _FIXTURE_REPOS:
            argv.extend(["--repo", str(repo)])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(argv)

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertGreater(payload["counts"]["edges"], 0)
        self.assertEqual(payload["counts"]["lockfile_baseline_edges"], 0)

    def test_cli_exit_code_2_on_missing_repo(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["fleet-map", "--repo", str(_FIXTURE_ROOT / "does-not-exist")])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
