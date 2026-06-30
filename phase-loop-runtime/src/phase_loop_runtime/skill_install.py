from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .skill_paths import HARNESS_DEFAULT_SKILL_ROOTS, current_harness, resolve_skill_bundle_root


REQUIRED_SKILLS: tuple[str, ...] = (
    "execute-detailed",
    "execute-phase",
    "plan-phase",
    "plan-detailed",
    "phase-roadmap-builder",
    "phase-loop",
    "run-train",
    "skill-editor",
    "skill-improvement-planner",
    "task-contextualizer",
)


@dataclass(frozen=True)
class InstallAction:
    harness: str
    skill_name: str
    installed_name: str
    source: str
    destination: str
    mode: str
    action: str
    overlay: str | None = None


def install_skills(
    *,
    harness: str,
    source: Path,
    destination: Path | None = None,
    mode: str = "symlink",
    apply: bool = False,
) -> list[InstallAction]:
    normalized = current_harness(harness)
    if mode not in {"symlink", "copy"}:
        raise ValueError("mode must be 'symlink' or 'copy'")

    source_root = Path(source).expanduser().resolve()
    destination_root = Path(destination).expanduser() if destination else resolve_skill_bundle_root(normalized)
    _validate_bundle(source_root)

    actions: list[InstallAction] = []
    for skill_name in REQUIRED_SKILLS:
        source_dir = source_root / skill_name
        installed_name = f"{normalized}-{skill_name}"
        destination_dir = destination_root / installed_name
        overlay_dir = source_dir / "_overrides" / normalized
        overlay = str(overlay_dir) if overlay_dir.exists() else None
        action = _planned_action(source_dir, destination_dir, mode, overlay_dir if overlay else None)
        record = InstallAction(
            harness=normalized,
            skill_name=skill_name,
            installed_name=installed_name,
            source=str(source_dir),
            destination=str(destination_dir),
            mode=mode,
            action=action,
            overlay=overlay,
        )
        actions.append(record)
        if apply:
            _apply_action(source_dir, destination_dir, mode, installed_name, overlay_dir if overlay else None)
    return actions


def actions_to_json(actions: list[InstallAction]) -> str:
    return json.dumps([asdict(action) for action in actions], indent=2, sort_keys=True)


def _validate_bundle(source_root: Path) -> None:
    missing = [name for name in REQUIRED_SKILLS if not (source_root / name / "SKILL.md").is_file()]
    if missing:
        raise FileNotFoundError(f"missing required phase-loop skills: {', '.join(missing)}")


def _planned_action(source_dir: Path, destination_dir: Path, mode: str, overlay_dir: Path | None) -> str:
    if destination_dir.is_symlink() and destination_dir.resolve() == source_dir and mode == "symlink" and overlay_dir is None:
        return "unchanged"
    if destination_dir.exists() or destination_dir.is_symlink():
        return "replace"
    return "create"


def _apply_action(source_dir: Path, destination_dir: Path, mode: str, installed_name: str, overlay_dir: Path | None) -> None:
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    if destination_dir.is_symlink() or destination_dir.exists():
        if destination_dir.is_dir() and not destination_dir.is_symlink():
            shutil.rmtree(destination_dir)
        else:
            destination_dir.unlink()

    shutil.copytree(source_dir, destination_dir, ignore=shutil.ignore_patterns("_overrides"))
    if overlay_dir is not None:
        _copy_overlay(overlay_dir, destination_dir)
    _rewrite_skill_name(destination_dir / "SKILL.md", installed_name)


def _copy_overlay(overlay_dir: Path, destination_dir: Path) -> None:
    for path in overlay_dir.rglob("*"):
        relative = path.relative_to(overlay_dir)
        target = destination_dir / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _rewrite_skill_name(path: Path, installed_name: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines[:8]):
        if line.startswith("name: "):
            lines[index] = f"name: {installed_name}"
            path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
            return
