from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .skill_install import REQUIRED_SKILLS


ACTIVE_HARNESSES: tuple[str, ...] = ("claude", "codex", "gemini", "opencode")

# Harness-agnostic auxiliary subdirectories carried verbatim into the neutral
# bundle so that `install` (which copytrees the whole skill dir) propagates them
# to every harness root. Without this, the SKILL Step 8 helper
# `scripts/validate_roadmap.py` never leaves the canonical source tree.
AUX_SUBDIRS: tuple[str, ...] = ("scripts", "references", "assets")
# IF-0-CANON-1: the canonical phase-loop skill sources now live IN this repo
# under `skills-src/<harness>/` (one tree per active harness, each holding
# `<harness>-<skill>/` dirs). `build_bundle` consumes these to produce the
# committed `phase-loop-skills/` bundle with NO dotfiles checkout required.
# The fleet's `bootstrap.sh` may still override these with an explicit
# `--source <dotfiles-root>` during the cutover; that path keeps working.
DEFAULT_SOURCES: dict[str, str] = {
    "claude": "skills-src/claude",
    "codex": "skills-src/codex",
    "gemini": "skills-src/gemini",
    "opencode": "skills-src/opencode",
}
OVERRIDE_README = "Harness-specific overlay files for this workflow skill.\n"

# Concrete, harness-SPECIFIC literals that must survive neutralization verbatim.
# `_neutralize_skill` collapses every `<harness>-`/`<harness> `/`<harness>_`/
# `<harness>.`/`Harness` token so the shared base + per-harness overrides read
# the same across harnesses. That is correct for harness-VARIANT tokens (skill
# names like `claude-execute-phase`, config dirs like `claude-config`, install
# paths) — they genuinely change per harness. It is WRONG for concrete Claude
# identifiers that name one real thing and have no per-harness variant: an Opus
# model id is `claude-opus-4-8` for every harness, and `<harness>-in-chrome`
# denotes nothing (the tool is `claude-in-chrome`; no `codex-in-chrome` exists).
# Membership test: "if this skill installed for codex, would the token need to
# become `codex-X`?" Yes -> collapse (NOT here). No, it's Claude-specific by
# nature -> preserve (here). These are masked to a sentinel before the collapse
# regexes run and restored after, so the literal ships intact into the installed
# bundle. (Applied uniformly to base + override; model ids never appear in the
# codex base, so masking is a no-op there and the dedup comparison is unaffected.)
PRESERVE_LITERALS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-in-chrome",
    # Display-name model form as it appears in the `Co-Authored-By:` git trailer.
    # Without this the brand collapse (`Claude` -> `Harness`) rewrites the trailer
    # to `Harness Opus 4.8` — a concrete model attribution corrupted into a name
    # that denotes nothing. One real model, no per-harness variant -> preserve.
    "Claude Opus 4.8",
)
_PRESERVE_SENTINEL = "\x00PRESERVE{index}\x00"


@dataclass(frozen=True)
class BuildWarning:
    skill: str
    message: str
    harness: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class SkippedSkill:
    skill: str
    missing_harnesses: tuple[str, ...]


@dataclass(frozen=True)
class BuildResult:
    skills_regenerated: list[str] = field(default_factory=list)
    overrides_written: list[str] = field(default_factory=list)
    skills_skipped: list[SkippedSkill] = field(default_factory=list)
    warnings: list[BuildWarning] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    dry_run: bool = False
    applied: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "skills_regenerated": self.skills_regenerated,
            "overrides_written": self.overrides_written,
            "skills_skipped": [asdict(skill) for skill in self.skills_skipped],
            "warnings": [asdict(warning) for warning in self.warnings],
            "files_written": self.files_written,
            "dry_run": self.dry_run,
            "applied": self.applied,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def build_bundle(
    sources: Sequence[Path | str] | Mapping[str, Path | str] | None,
    destination: Path | str,
    *,
    dry_run: bool,
    apply: bool,
    force: bool = False,
) -> BuildResult:
    source_roots = _normalize_sources(sources)
    destination_root = Path(destination)
    effective_apply = bool(apply and not dry_run)

    skills_regenerated: list[str] = []
    overrides_written: list[str] = []
    skills_skipped: list[SkippedSkill] = []
    warnings: list[BuildWarning] = []
    files_written: list[str] = []

    for skill in REQUIRED_SKILLS:
        canonical_paths = {
            harness: root / f"{harness}-{skill}" / "SKILL.md"
            for harness, root in source_roots.items()
        }
        missing = tuple(
            harness for harness in ACTIVE_HARNESSES if not canonical_paths[harness].is_file()
        )
        if missing:
            skills_skipped.append(SkippedSkill(skill=skill, missing_harnesses=missing))
            warnings.append(
                BuildWarning(
                    skill=skill,
                    message=f"missing canonical SKILL.md for {', '.join(missing)}",
                )
            )
            continue

        neutral_base = _neutralize_skill(
            canonical_paths["codex"].read_text(encoding="utf-8"),
            harness="codex",
            skill=skill,
        )
        skill_dir = destination_root / skill
        base_path = skill_dir / "SKILL.md"
        if _record_write(base_path, neutral_base, dry_run=dry_run, apply=effective_apply, force=force):
            skills_regenerated.append(skill)
            files_written.append(base_path.as_posix())

        # Carry harness-agnostic auxiliary subdirs (scripts/, references/,
        # assets/) into the neutral bundle. Source from the first harness whose
        # canonical dir provides each subdir (ACTIVE_HARNESSES order), since the
        # content is shared, not harness-specific.
        for aux in AUX_SUBDIRS:
            source_aux = next(
                (
                    canonical_paths[harness].parent / aux
                    for harness in ACTIVE_HARNESSES
                    if (canonical_paths[harness].parent / aux).is_dir()
                ),
                None,
            )
            if source_aux is None:
                continue
            for src_file in sorted(source_aux.rglob("*")):
                if not src_file.is_file():
                    continue
                if "__pycache__" in src_file.parts or src_file.suffix == ".pyc":
                    continue
                target = skill_dir / aux / src_file.relative_to(source_aux)
                files_written.append(target.as_posix())
                if effective_apply:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, target)

        for harness in ACTIVE_HARNESSES:
            readme_path = skill_dir / "_overrides" / harness / "README.md"
            if _record_write(readme_path, OVERRIDE_README, dry_run=dry_run, apply=effective_apply, force=force):
                files_written.append(readme_path.as_posix())

        for harness in ACTIVE_HARNESSES:
            if harness == "codex":
                override_text = None
            else:
                candidate = _neutralize_skill(
                    canonical_paths[harness].read_text(encoding="utf-8"),
                    harness=harness,
                    skill=skill,
                )
                override_text = candidate if candidate != neutral_base else None
            override_path = skill_dir / "_overrides" / harness / "SKILL.md"
            if override_text is None:
                if override_path.exists() and (dry_run or effective_apply):
                    overrides_written.append(override_path.as_posix())
                    files_written.append(override_path.as_posix())
                    if effective_apply:
                        override_path.unlink()
                continue
            if _record_write(override_path, override_text, dry_run=dry_run, apply=effective_apply, force=force):
                overrides_written.append(override_path.as_posix())
                files_written.append(override_path.as_posix())

    return BuildResult(
        skills_regenerated=skills_regenerated,
        overrides_written=overrides_written,
        skills_skipped=skills_skipped,
        warnings=warnings,
        files_written=files_written,
        dry_run=bool(dry_run),
        applied=effective_apply,
    )


def _normalize_sources(
    sources: Sequence[Path | str] | Mapping[str, Path | str] | None,
) -> dict[str, Path]:
    if sources is None:
        return {harness: Path(path) for harness, path in DEFAULT_SOURCES.items()}
    if isinstance(sources, Mapping):
        roots = {str(harness): Path(path) for harness, path in sources.items()}
    else:
        roots = {}
        for raw in sources:
            path = Path(raw)
            harness = _infer_harness(path)
            if harness in roots:
                raise ValueError(f"duplicate source root for {harness}: {path}")
            roots[harness] = path
    missing = [harness for harness in ACTIVE_HARNESSES if harness not in roots]
    if missing:
        raise ValueError(f"missing source root for {', '.join(missing)}")
    unknown = [harness for harness in roots if harness not in ACTIVE_HARNESSES]
    if unknown:
        raise ValueError(f"unknown source harness {', '.join(sorted(unknown))}")
    return {harness: roots[harness] for harness in ACTIVE_HARNESSES}


def _infer_harness(path: Path) -> str:
    text = path.as_posix()
    for harness in ACTIVE_HARNESSES:
        if re.search(rf"(^|[-_/]){re.escape(harness)}($|[-_/])", text):
            return harness
    raise ValueError(f"cannot infer harness from source root: {path}")


def _neutralize_skill(text: str, *, harness: str, skill: str) -> str:
    output = text
    # Mask concrete harness-specific literals so the collapse regexes below cannot
    # corrupt them (e.g. `claude-opus-4-8` -> `<harness>-opus-4-8`). Restored verbatim
    # at the end. See PRESERVE_LITERALS.
    for index, literal in enumerate(PRESERVE_LITERALS):
        output = output.replace(literal, _PRESERVE_SENTINEL.format(index=index))
    output = re.sub(
        rf"(?m)^name:\s+{re.escape(harness)}-{re.escape(skill)}\s*$",
        f"name: {skill}",
        output,
        count=1,
    )
    output = re.sub(rf"\b{re.escape(harness)}-", "<harness>-", output)
    output = re.sub(rf"\b{re.escape(harness)} ", "<harness> ", output)
    output = re.sub(rf"\b{re.escape(harness)}_", "<harness>_", output)
    output = re.sub(rf"\b{re.escape(harness)}\.", "<harness>.", output)
    title = harness.capitalize()
    output = re.sub(rf"\b{re.escape(title)}\b", "Harness", output)
    output = output.replace(
        "Executes one detailed plan artifact produced by `<harness>-plan-detailed`. Use this\n"
        "when one Harness thread should implement a bounded plan end to end without phase\n"
        "lanes or cross-harness dispatch.",
        "Executes one detailed plan artifact produced by `<harness>-plan-detailed`. Use\n"
        "this when one Harness thread should implement a bounded plan end to end without\n"
        "phase lanes or cross-harness dispatch.",
    )
    output = output.replace(
        "- Optionally read `.dev-skills/handoffs/<harness>-plan-detailed/latest.md` when no\n"
        "  explicit plan path is supplied, and only trust it if `from:` is",
        "- Optionally read `.dev-skills/handoffs/<harness>-plan-detailed/latest.md`\n"
        "  when no explicit plan path is supplied, and only trust it if `from:` is",
    )
    # Restore the masked harness-specific literals verbatim.
    for index, literal in enumerate(PRESERVE_LITERALS):
        output = output.replace(_PRESERVE_SENTINEL.format(index=index), literal)
    return output


def _record_write(path: Path, content: str, *, dry_run: bool, apply: bool, force: bool) -> bool:
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    changed = current != content
    should_write = changed or (force and apply)
    if dry_run:
        return changed or force
    if not apply or not should_write:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
