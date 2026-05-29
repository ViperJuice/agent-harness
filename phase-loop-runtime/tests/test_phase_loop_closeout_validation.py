import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.closeout_validation import extract_plan_produces, validate_produced_gates
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopCloseoutValidationTest(unittest.TestCase):
    def test_matching_produced_gates_pass(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(
                plan,
                {"terminal_status": "complete", "produced_if_gates": ["IF-0-NATIVE-1", "IF-0-NATIVE-2"]},
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.missing_gates, ())
            self.assertEqual(result.unexpected_gates, ())

    def test_mismatched_produced_gates_reject(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(
                plan,
                {"terminal_status": "complete", "produced_if_gates": ["IF-0-NATIVE-1", "IF-0-NATIVE-9"]},
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.blocker_class, "contract_bug")
            self.assertEqual(result.missing_gates, ("IF-0-NATIVE-2",))
            self.assertEqual(result.unexpected_gates, ("IF-0-NATIVE-9",))

    def test_missing_produced_gates_soft_warns_during_native(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(plan, {"terminal_status": "complete"})

            self.assertTrue(result.ok)
            self.assertIn("compatibility window", result.warning)

    def test_present_empty_complete_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(plan, {"terminal_status": "complete", "produced_if_gates": []})

            self.assertFalse(result.ok)
            self.assertEqual(result.blocker_summary, "completed closeout reported zero produced_if_gates")

    def test_extract_plan_produces_reads_produces_and_lane_interfaces(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            self.assertEqual(extract_plan_produces(plan), ("IF-0-NATIVE-1", "IF-0-NATIVE-2"))

    def test_no_if_gates_plan_allows_empty_produced(self):
        """Plans declaring no IF gates (internal/tooling phases) must not fail the
        contract check when the executor emits an empty produced_if_gates."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_plan_without_if_gates(repo)

            result = validate_produced_gates(
                plan,
                {"terminal_status": "complete", "produced_if_gates": []},
            )

            self.assertTrue(result.ok)
            self.assertIsNone(result.warning)
            self.assertEqual(result.expected_gates, ())
            self.assertEqual(result.produced_gates, ())

    def test_no_if_gates_plan_treats_free_text_as_chatter(self):
        """When the plan declares no IF gates and codex emits commentary like
        '...verified...; active plan declares no interface-freeze gate' into
        produced_if_gates, the validator must accept with a warning."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_plan_without_if_gates(repo)

            result = validate_produced_gates(
                plan,
                {
                    "terminal_status": "complete",
                    "produced_if_gates": [
                        "RUNNER verified; active plan declares no interface-freeze gate",
                    ],
                },
            )

            self.assertTrue(result.ok)
            self.assertIsNotNone(result.warning)
            self.assertIn("non-IF-gate", result.warning)
            self.assertEqual(result.produced_gates, ())

    def test_chatter_filtered_real_gate_still_validated(self):
        """When the executor emits a mix of real IF-gate tokens and chatter,
        the validator must filter the chatter and still match the canonical
        gate against the plan's expected set."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(
                plan,
                {
                    "terminal_status": "complete",
                    "produced_if_gates": [
                        "IF-0-NATIVE-1",
                        "IF-0-NATIVE-2",
                        "phase work verified end-to-end",
                    ],
                },
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.produced_gates, ("IF-0-NATIVE-1", "IF-0-NATIVE-2"))
            self.assertEqual(result.missing_gates, ())
            self.assertEqual(result.unexpected_gates, ())

    def test_chatter_only_complete_still_rejects_when_plan_has_gates(self):
        """A plan that declares IF gates must still reject a closeout whose
        produced_if_gates contains only chatter (no canonical tokens). This
        guards against the chatter filter accidentally relaxing the strict
        contract check when the plan declares real gates."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            result = validate_produced_gates(
                plan,
                {
                    "terminal_status": "complete",
                    "produced_if_gates": ["phase verified, gates declared inline"],
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.blocker_class, "contract_bug")
            self.assertEqual(result.blocker_summary, "completed closeout reported zero produced_if_gates")
            self.assertEqual(result.missing_gates, ("IF-0-NATIVE-1", "IF-0-NATIVE-2"))

    def test_if_gate_regex_round_trips_extract_plan_produces(self):
        """Pin the coupling: every gate token that extract_plan_produces returns
        must also IF_GATE_RE.fullmatch — otherwise the chatter filter would
        silently misclassify a real gate as commentary."""
        from phase_loop_runtime.closeout_validation import IF_GATE_RE

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = self._write_native_plan(repo)

            for gate in extract_plan_produces(plan):
                self.assertIsNotNone(
                    IF_GATE_RE.fullmatch(gate),
                    msg=f"gate {gate!r} from extract_plan_produces does not fullmatch IF_GATE_RE",
                )

    def _write_plan_without_if_gates(self, repo: Path) -> Path:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        return write_phase_plan(
            repo,
            "RUNNER",
            roadmap,
            body=(
                "# RUNNER\n\n"
                "Internal tooling phase, declares no interface-freeze gates.\n\n"
                "## Lanes\n\n"
                "### SL-0 - Tooling\n"
                "- **Owned files**: `tooling.py`\n"
            ),
        )

    def _write_native_plan(self, repo: Path) -> Path:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        return write_phase_plan(
            repo,
            "RUNNER",
            roadmap,
            body=(
                "# RUNNER\n\n"
                "**Produces**: `IF-0-NATIVE-1`\n\n"
                "## Lanes\n\n"
                "### SL-0 - Contract\n"
                "- **Owned files**: `contract.py`\n"
                "- **Interfaces provided**: `IF-0-NATIVE-2`\n"
            ),
        )


if __name__ == "__main__":
    unittest.main()
