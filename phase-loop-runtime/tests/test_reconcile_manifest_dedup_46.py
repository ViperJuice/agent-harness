"""#46 — reconcile must not re-add a duplicate imported manifest entry.

`phase-loop status`/reconcile deduped auto-imports against the committed manifest
by SLUG only. A committed planner entry (slug ``phase-plan-v1-CORE``) and the
synthetic auto-import for the SAME file+phase (slug ``v1-CORE``) have different
slugs, so reconcile appended a duplicate ``imported`` row. Dedup is now by
normalized (file, phase_alias) as well.
"""

from __future__ import annotations

from pathlib import Path

import phase_loop_runtime.plan_manifest as pm
from phase_loop_runtime import reconcile


def _entry(slug: str, *, file: str = "plans/phase-plan-v1-CORE.md", phase: str = "CORE",
           status: str = "planned") -> "pm.DotfilesPlanEntry":
    return pm.DotfilesPlanEntry(
        slug=slug,
        file=file,
        type="phase",
        status=status,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        owner_skill="test",
        phase_alias=phase,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "plans").mkdir(parents=True)
    (repo / "specs").mkdir()
    (repo / "specs" / "phase-plans-v1.md").write_text("# roadmap\n", encoding="utf-8")
    (repo / "plans" / "phase-plan-v1-CORE.md").write_text("# CORE plan\n", encoding="utf-8")
    # committed planner entry — slug "phase-plan-v1-CORE"
    pm.append_entry(repo, _entry("phase-plan-v1-CORE"))
    return repo


def test_reconcile_dedupes_import_by_file_phase_not_slug(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    # auto-import yields a DIFFERENT slug for the SAME file + phase
    dup = pm.DotfilesPlanManifest(plans=(_entry("v1-CORE", status="imported"),))
    monkeypatch.setattr(pm, "import_existing_phase_plans", lambda r: dup)
    appended: list[str] = []
    monkeypatch.setattr(pm, "append_entry", lambda r, e: appended.append(e.slug))

    reconcile._reconcile_plan_manifest(
        repo, repo / "specs" / "phase-plans-v1.md", {"CORE": "planned"}
    )
    assert "v1-CORE" not in appended, (
        "#46: reconcile re-added a duplicate imported entry (slug 'v1-CORE') for a "
        "file+phase already represented by the committed 'phase-plan-v1-CORE' entry"
    )


def test_reconcile_still_imports_a_genuinely_new_file(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path)
    (repo / "plans" / "phase-plan-v1-API.md").write_text("# API plan\n", encoding="utf-8")
    new = pm.DotfilesPlanManifest(
        plans=(_entry("v1-API", file="plans/phase-plan-v1-API.md", phase="API", status="imported"),)
    )
    monkeypatch.setattr(pm, "import_existing_phase_plans", lambda r: new)
    appended: list[str] = []
    monkeypatch.setattr(pm, "append_entry", lambda r, e: appended.append(e.slug))

    reconcile._reconcile_plan_manifest(
        repo, repo / "specs" / "phase-plans-v1.md", {"CORE": "planned", "API": "planned"}
    )
    assert "v1-API" in appended, (
        "#46: a genuinely-new file+phase must still be imported (the dedup must not over-block)"
    )
