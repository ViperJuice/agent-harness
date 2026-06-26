from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.cli import main
from phase_loop_runtime.evidence_audit import (
    EvidenceJudgment,
    Tier3Budget,
    build_tier3_audit_event_metadata,
    run_tier3_runner_audit,
)
from phase_loop_runtime.evidence_audit_config import EvidenceAuditConfigError, load_evidence_audit_config
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import ROOT, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _write_config(repo: Path, text: str) -> None:
    config = repo / ".phase-loop" / "evidence-audit.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text, encoding="utf-8")


def _runner_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n"
            "**Produces**: `IF-0-RUNNER-1`\n\n"
            "## Lanes\n\n"
            "### SL-0 - Runner\n"
            "- **Owned files**: `evidence*.json`\n"
            "- **Interfaces provided**: `IF-0-RUNNER-1`\n"
        ),
    )
    return repo, roadmap


def _launch_writing(repo: Path, payload: dict[str, object]):
    def launch(spec, **_kwargs):
        (repo / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")
        closeout = {
            "terminal_status": "complete",
            "verification_status": "passed",
            "dirty_paths": ["evidence.json"],
            "produced_if_gates": ["IF-0-RUNNER-1"],
        }
        return LaunchResult(command=spec.command, returncode=0, output=json.dumps(closeout), executor=spec.executor)

    return launch


def _run_fixture(repo: Path, roadmap: Path, **kwargs):
    with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
        "phase_loop_runtime.runner.launch_with_spec", side_effect=_launch_writing(repo, kwargs.pop("payload"))
    ):
        return run_loop(repo, roadmap, phase="RUNNER", executor="codex", **kwargs)


def _events(repo: Path) -> list[dict]:
    path = repo / ".phase-loop" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class Tier3RunnerPrimitiveTest(unittest.TestCase):
    def test_budget_decrements_until_exhausted(self):
        budget = Tier3Budget(tier3_budget=2)

        self.assertTrue(budget.consume())
        self.assertTrue(budget.consume())
        self.assertFalse(budget.consume())
        self.assertEqual(budget.tier3_calls_made, 2)

    def test_audit_event_metadata_has_required_keys(self):
        metadata = build_tier3_audit_event_metadata(
            tier2_finding=object(),
            sample_path=Path("evidence.json"),
            expected_artifact_characteristics="varied values",
            judgment=EvidenceJudgment("real", 0.9, "varied", ()),
            latency_ms=12,
        )

        for key in (
            "prompt_sha256",
            "response_sha256",
            "verdict",
            "confidence",
            "token_counts",
            "latency_ms",
            "estimated_cost_usd",
        ):
            self.assertIn(key, metadata)

    def test_budget_exhaustion_records_operator_review_marker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for index in range(2):
                (repo / f"evidence{index}.json").write_text(
                    json.dumps({"scores": [1.0, 1.0001, 0.9999, 1.00005]}),
                    encoding="utf-8",
                )
            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("real", 0.9, "ok", ()),
            ) as tier3:
                audit = run_tier3_runner_audit(repo, tier3_budget=1, dirty_only=False)

        self.assertEqual(tier3.call_count, 1)
        self.assertEqual(audit.operator_review_markers[0]["marker"], "UNCERTAIN-OPERATOR-REVIEW")


class EvidenceAuditConfigTest(unittest.TestCase):
    def test_missing_config_defaults_tier3_off(self):
        with tempfile.TemporaryDirectory() as td:
            config = load_evidence_audit_config(Path(td))

        phase = config.phase_config("ANY")
        self.assertTrue(phase.tier2_enabled)
        self.assertFalse(phase.tier3_enabled)

    def test_default_v23_config_excludes_v23_phases(self):
        config = load_evidence_audit_config(ROOT)

        for alias in ("T2DETECTORS", "T3SCHEMA", "T3RUNNER", "T3VALIDATE"):
            self.assertTrue(config.tier3_excluded(alias))

    def test_yaml_config_requires_pyyaml_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_config(repo, "tier3_enabled: false\n")

            with patch("phase_loop_runtime.evidence_audit_config.yaml", None):
                with self.assertRaisesRegex(EvidenceAuditConfigError, "PyYAML is required"):
                    load_evidence_audit_config(repo)

    def test_malformed_config_blocks_runner_closeout_as_contract_bug(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))
            _write_config(repo, "tier3_enabled: definitely\n")

            snapshot, _results = _run_fixture(
                repo,
                roadmap,
                payload={"scores": [0.2, 0.4, 0.8, 0.9]},
                enable_tier_3=True,
            )

        self.assertEqual(snapshot.phases["RUNNER"], "blocked")
        self.assertEqual(snapshot.blocker_class, "contract_bug")
        self.assertIn("Malformed evidence-audit config", snapshot.blocker_summary)


class Tier3RunnerCloseoutIntegrationTest(unittest.TestCase):
    def test_default_flag_off_skips_tier3_for_uncertain_tier2(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch("phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence") as tier3:
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                )

        tier3.assert_not_called()
        self.assertIn(snapshot.phases["RUNNER"], {"complete", "awaiting_phase_closeout"})

    def test_cli_opt_in_invokes_tier3_and_appends_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("real", 0.91, "varied enough", ()),
            ) as tier3:
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

            tier3.assert_called_once()
            self.assertIn(snapshot.phases["RUNNER"], {"complete", "awaiting_phase_closeout"})
            tier3_events = [event for event in _events(repo) if event["action"] == "evidence_audit_tier3"]
            self.assertEqual(len(tier3_events), 1)
            for key in ("prompt_sha256", "response_sha256", "verdict", "confidence", "token_counts", "latency_ms", "estimated_cost_usd"):
                self.assertIn(key, tier3_events[0]["metadata"])

    def test_tier1_suspect_bypasses_tier3(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch("phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence") as tier3:
                _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [0.999999, 0.999999, 0.999999, 0.999999]},
                    enable_tier_3=True,
                )

        tier3.assert_not_called()

    def test_per_phase_exclusion_is_respected_even_with_cli_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))
            _write_config(repo, "phase_aliases_exclude_tier3: [RUNNER]\nphases: {}\n")

            with patch("phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence") as tier3:
                _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

        tier3.assert_not_called()

    def test_per_phase_config_can_opt_in_without_cli_flag(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))
            _write_config(repo, "phases:\n  RUNNER:\n    tier3_enabled: true\n")

            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("real", 0.91, "varied enough", ()),
            ) as tier3:
                _run_fixture(repo, roadmap, payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]})

        tier3.assert_called_once()

    def test_fake_verdict_blocks_with_tier3_judgment_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("fake", 0.95, "template evidence", ("near uniform",)),
            ):
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            launch_event = _events(repo)[-1]
            self.assertEqual(launch_event["blocker"]["blocker_class"], "contract_bug")
            self.assertIn("tier3_judgment", launch_event["blocker"]["metadata"])

    def test_real_verdict_at_threshold_allows_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("real", 0.85, "specific evidence", ()),
            ):
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

        self.assertIn(snapshot.phases["RUNNER"], {"complete", "awaiting_phase_closeout"})

    def test_uncertain_verdict_logs_warning_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch(
                "phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence",
                return_value=EvidenceJudgment("uncertain", 0.0, "timeout", ("timeout",)),
            ):
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

            self.assertIn(snapshot.phases["RUNNER"], {"complete", "awaiting_phase_closeout"})
            self.assertIn("timeout", _events(repo)[-1]["metadata"]["child_automation"]["tier3_audit"]["warnings"])

    def test_tier3_exception_logs_warning_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _runner_fixture(Path(td))

            with patch("phase_loop_runtime.evidence_audit.evaluate_suspected_fake_evidence", side_effect=TimeoutError("slow")):
                snapshot, _results = _run_fixture(
                    repo,
                    roadmap,
                    payload={"scores": [1.0, 1.0001, 0.9999, 1.00005]},
                    enable_tier_3=True,
                )

            self.assertIn(snapshot.phases["RUNNER"], {"complete", "awaiting_phase_closeout"})
            self.assertIn("tier3_call_error", _events(repo)[-1]["metadata"]["child_automation"]["tier3_audit"]["warnings"][0])

    def test_run_help_exposes_tier3_flags(self):
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(stdout):
            main(["run", "--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertIn("--enable-tier-3", help_text)
        self.assertIn("--tier-3-budget", help_text)


if __name__ == "__main__":
    unittest.main()
