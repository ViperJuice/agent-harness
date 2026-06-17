import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import make_repo, provenanced_event, provenanced_state, write_phase_plan
from phase_loop_runtime.cli import main
from phase_loop_runtime.events import append_event
from phase_loop_runtime.provenance import (
    phase_provenance_map,
    phase_sha256,
    validate_roadmap_phase_headings,
)


class PhaseLoopRoadmapValidateTest(unittest.TestCase):
    def test_validator_accepts_integer_and_decimal_phase_headings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Contract (CONTRACT)\n\n"
                "### Phase 2.1 - Runner Follow-up (RUNNER2)\n"
            )

            self.assertEqual(validate_roadmap_phase_headings(roadmap), [])
            self.assertIn("CONTRACT", phase_provenance_map(roadmap))
            self.assertIsNotNone(phase_sha256(roadmap, "RUNNER2"))

    def test_validator_reports_loose_candidates_duplicates_and_invalid_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase NOTANUMBER - Bad Number (BAD)\n"
                "### Phase 1 - Duplicate (DUP)\n"
                "### Phase 2 - Duplicate Again (DUP)\n"
                "### Phase 3 - Bad Alias (bad)\n"
            )

            findings = validate_roadmap_phase_headings(roadmap)
            reasons = [finding.reason for finding in findings]
            self.assertTrue(any("loose-match" in reason for reason in reasons))
            self.assertTrue(any("duplicate-alias" in reason for reason in reasons))
            self.assertTrue(any("invalid-alias" in reason for reason in reasons))
            self.assertTrue(all(finding.line_number > 0 for finding in findings))
            self.assertTrue(all(finding.raw_text.startswith("### Phase") for finding in findings))
            self.assertTrue(all(finding.suggested_fix for finding in findings))

    def test_clean_roadmap_has_no_findings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            self.assertEqual(validate_roadmap_phase_headings(roadmap), [])

    def test_roadmap_aware_entrypoints_warn_without_blocking_or_corrupting_json(self):
        commands = (
            ("run", ["run", "--phase", "RUNNER", "--dry-run"]),
            ("resume", ["resume"]),
            ("dry-run", ["dry-run"]),
            ("status", ["status", "--json"]),
            ("execute", ["execute", "RUNNER", "--output", "{output}", "--mode", "execute", "--dry-run", "--json"]),
            ("reconcile", ["reconcile", "--phase", "RUNNER", "--repair-summary", "fixture"]),
            ("reopen", ["reopen", "--phase", "RUNNER", "--reason", "fixture", "--allow-dirty"]),
            ("monitor", ["monitor", "--once", "--json"]),
            ("evidence-audit", ["evidence-audit"]),
            ("closeout-drift-audit", ["closeout-drift-audit"]),
        )
        for name, command in commands:
            with self.subTest(command=name), tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = repo / "specs" / "phase-plans-v1.md"
                roadmap.write_text(
                    roadmap.read_text()
                    + "\n### Phase NOTANUMBER - Bad heading (BAD)\n"
                )
                subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "bad roadmap fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                plan = write_phase_plan(repo, "RUNNER", roadmap)
                subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "runner plan fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                if name == "reopen":
                    append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "complete"))

                output = Path(td) / "closeout.json"
                argv = [part.format(output=output) for part in command]
                if name == "closeout-drift-audit":
                    argv.extend(["--repo", str(repo), "--roadmap", str(roadmap)])
                else:
                    argv.extend(["--repo", str(repo), "--roadmap", str(roadmap)])

                stdout = io.StringIO()
                stderr = io.StringIO()
                patches = []
                if name in {"run", "dry-run", "execute"}:
                    patches.append(
                        patch(
                            "phase_loop_runtime.cli.run_loop",
                            return_value=(provenanced_state(repo, roadmap, {"RUNNER": "planned"}), []),
                        )
                    )
                if name == "closeout-drift-audit":
                    class CleanDriftAudit:
                        def to_json(self):
                            return {"findings": []}

                        def render_text(self):
                            return "Closeout drift audit: clean"

                        def has_setup_errors(self):
                            return False

                        def has_drift(self):
                            return False

                    patches.append(patch("phase_loop_runtime.phase_loop_drift_audit.run_drift_audit", return_value=CleanDriftAudit()))
                with contextlib.ExitStack() as stack, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    for active_patch in patches:
                        stack.enter_context(active_patch)
                    code = main(argv)

                self.assertNotEqual(code, 2, stderr.getvalue())
                self.assertIn("phase-loop roadmap warning", stderr.getvalue())
                self.assertIn("line", stderr.getvalue())
                self.assertIn("loose-match", stderr.getvalue())
                self.assertIn("Bad heading", stderr.getvalue())
                self.assertIn("suggested fix", stderr.getvalue())
                if "--json" in argv:
                    json.loads(stdout.getvalue())


_VALID_ROADMAP = """# Test Roadmap

## Context
context.

## Phases

### Phase 1 — Foundation (FOUND)
**Objective**
Do the thing.

**Exit criteria**
- [ ] it works

**Scope notes**
Decompose into 2 lanes.

**Key files**
- src/a.py

**Depends on**
- (none)

**Produces**
- IF-0-FOUND-1

## Top Interface-Freeze Gates
- IF-0-FOUND-1

## Phase Dependency DAG
FOUND

## Execution Notes
notes.

## Verification
verify.
"""


class RoadmapLintModuleTest(unittest.TestCase):
    """The full roadmap lint now lives in the always-installed runtime
    (phase_loop_runtime.roadmap_lint), exposed as `phase-loop validate-roadmap`.
    The skill-bundle script is a thin shim over it (A8)."""

    def test_lint_accepts_a_clean_roadmap(self):
        from phase_loop_runtime.roadmap_lint import lint_roadmap_text

        self.assertEqual(lint_roadmap_text(_VALID_ROADMAP), [])

    def test_lint_flags_missing_alias_headings_gates_and_root(self):
        from phase_loop_runtime.roadmap_lint import lint_roadmap_text

        errors = lint_roadmap_text("# Bad\n\n## Phases\n\n### Phase 1 — No Alias\n")
        codes = " ".join(errors)
        self.assertIn("(A)", codes)  # missing required headings
        self.assertIn("(B)", codes)  # invalid phase heading / no phases
        self.assertIn("(E)", codes)  # no root phases

    def test_lint_detects_dependency_cycle(self):
        from phase_loop_runtime.roadmap_lint import lint_roadmap_text

        cyclic = _VALID_ROADMAP.replace("- (none)", "- LATER") + (
            "\n### Phase 2 — Later (LATER)\n"
            "**Objective**\no\n\n**Exit criteria**\n- [ ] x\n\n**Scope notes**\n2 lanes\n\n"
            "**Key files**\n- src/b.py\n\n**Depends on**\n- FOUND\n\n**Produces**\n- IF-0-LATER-1\n"
        )
        # FOUND now depends on LATER and LATER depends on FOUND → cycle.
        errors = lint_roadmap_text(cyclic)
        self.assertTrue(any(e.startswith("(F)") for e in errors), errors)

    def test_validate_roadmap_cli_subcommand(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.md"
            good.write_text(_VALID_ROADMAP, encoding="utf-8")
            bad = Path(td) / "bad.md"
            bad.write_text("# Bad\n\n## Phases\n\n### Phase 1 — No Alias\n", encoding="utf-8")

            self.assertEqual(main(["validate-roadmap", str(good)]), 0)
            self.assertEqual(main(["validate-roadmap", "--roadmap", str(good)]), 0)
            self.assertEqual(main(["validate-roadmap", str(bad)]), 1)


if __name__ == "__main__":
    unittest.main()
