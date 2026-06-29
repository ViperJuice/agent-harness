"""#14 — `sync-skills --apply` must never silently no-op.

When `--apply` cannot repair a bridge skill (no skill source resolved), it must
report the unrepaired skills and the CLI must exit non-zero — not produce
`--check`-identical output with exit 0.
"""
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import maintenance
from phase_loop_runtime.cli import _sync_skills_command
from phase_loop_runtime.maintenance import SyncSkillsOptions, sync_bridge_skills


class _FakeRec:
    def __init__(self, **kw):
        self._d = kw

    def to_json(self):
        return dict(self._d)


def _unresolvable_bridge(repo):
    """Patch the inventory so a non-ok bridge skill has no resolvable source."""
    rec = _FakeRec(
        parity_status="missing_skill",
        source_dir=None,                 # the `missing_source` case
        repair_target=str(repo / ".codex/skills/codex-phase-loop"),
        harness_target="codex",
        skill_name="codex-phase-loop",
    )
    return [
        patch.object(maintenance, "inspect_bridge_skill_inventory", return_value=[rec]),
        patch.object(maintenance, "inspect_workflow_skill_inventory", return_value=[]),
        patch.object(maintenance, "inspect_vestigial_workflow_candidates", return_value=[]),
        patch.object(maintenance, "classify_skill_like_directories", return_value=[]),
        patch.object(maintenance, "active_loop_blocker", return_value=None),
    ]


class SyncSkillsApplyFailLoudTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_apply_collects_unrepaired_instead_of_silent_skip(self):
        patches = _unresolvable_bridge(self.repo)
        for p in patches:
            p.start()
        try:
            summary = sync_bridge_skills(self.repo, SyncSkillsOptions(harnesses=("codex",), apply=True))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(summary["changed"], [])               # nothing repaired
        self.assertEqual(len(summary["unrepaired"]), 1)        # but it is REPORTED, not skipped
        self.assertEqual(summary["unrepaired"][0]["reason"], "no_source_resolved")
        self.assertEqual(summary["unrepaired"][0]["skill_name"], "codex-phase-loop")

    def test_cli_apply_exits_nonzero_when_unrepaired(self):
        summary = {
            "mode": "apply", "repo": str(self.repo), "harnesses": ["codex"],
            "bridge_skills": [], "workflow_sources": [], "vestigial_workflow_candidates": [],
            "skill_classifications": [], "changed": [],
            "unrepaired": [{"harness_target": "codex", "skill_name": "codex-phase-loop", "reason": "no_source_resolved"}],
            "blocked": False, "blocker": None,
        }
        args = argparse.Namespace(harness=("codex",), apply=True)
        with patch.object(maintenance, "sync_bridge_skills", return_value=summary):
            rc = _sync_skills_command(repo=self.repo, args=args, as_json=False)
        self.assertEqual(rc, 1)   # fail loud — NOT a silent exit 0

    def test_cli_apply_exits_zero_when_all_repaired(self):
        summary = {
            "mode": "apply", "repo": str(self.repo), "harnesses": ["codex"],
            "bridge_skills": [], "workflow_sources": [], "vestigial_workflow_candidates": [],
            "skill_classifications": [],
            "changed": [{"harness_target": "codex", "skill_name": "codex-phase-loop", "repair_target": "x"}],
            "unrepaired": [], "blocked": False, "blocker": None,
        }
        args = argparse.Namespace(harness=("codex",), apply=True)
        with patch.object(maintenance, "sync_bridge_skills", return_value=summary):
            rc = _sync_skills_command(repo=self.repo, args=args, as_json=False)
        self.assertEqual(rc, 0)

    def test_check_mode_is_unaffected(self):
        # --check (apply=False) never reports unrepaired and always exits 0.
        summary = {
            "mode": "check", "repo": str(self.repo), "harnesses": ["codex"],
            "bridge_skills": [], "workflow_sources": [], "vestigial_workflow_candidates": [],
            "skill_classifications": [], "changed": [], "unrepaired": [],
            "blocked": False, "blocker": None,
        }
        args = argparse.Namespace(harness=("codex",), apply=False)
        with patch.object(maintenance, "sync_bridge_skills", return_value=summary):
            rc = _sync_skills_command(repo=self.repo, args=args, as_json=False)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
