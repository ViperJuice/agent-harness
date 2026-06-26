from __future__ import annotations

import functools
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

_LOGGER = logging.getLogger("phase_loop_runtime.skill_inventory")


HARNESS_INSTALL_ROOT_HINTS = {
    "codex": ("~/.codex/skills",),
    "claude": ("~/.claude/skills",),
    "gemini": ("~/.gemini/skills",),
    "opencode": ("~/.config/opencode/skills",),
    "command": (),
    "manual": (),
}

# DISENTANGLE (EXTRACTSKILLS SL-2): the generic runtime no longer hardcodes the
# per-harness dotfiles overlay roots here. The neutral generated bundle is the
# shared-canonical artifact (moves to agent-harness); the 4 per-harness source roots
# that produce the `_overrides/` are the dotfiles-OVERLAY and are now contributed by
# the in-tree dotfiles profile through the ``phase_loop_runtime.skill_sources``
# entry-point group (see :func:`iter_skill_source_roots` and ``skill_sources_plugin``).
# The keys are retained (callers iterate them) but carry no built-in dotfiles paths;
# resolution flows through the merged builtin+plugin roots.
HARNESS_SOURCE_ROOTS = {
    "codex": (),
    "claude": (),
    "gemini": (),
    "opencode": (),
    "command": (),
    "manual": (),
}

def _runner_repo_root() -> Path | None:
    """Resolve the runner's source-of-truth repo (where HARNESS_SOURCE_ROOTS live).

    DECOUPLE SL-2: the generic runtime no longer walks up the filesystem into the
    dotfiles tree (the old fleet-relative fallback). Source skill roots are an *optional*
    integration: operators/tests set ``PHASE_LOOP_RUNNER_REPO_ROOT`` and profiles
    contribute roots via the ``phase_loop_runtime.skill_sources`` entry-point group
    (see :func:`iter_skill_source_roots`). With neither configured -- e.g. a clean
    wheel install -- this returns ``None`` and source-skill resolution yields
    nothing rather than pointing at an unrelated path.
    """
    import os
    env = os.environ.get("PHASE_LOOP_RUNNER_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return None


class SkillSourcePluginError(RuntimeError):
    """A REGISTERED skill-source plugin failed to load or invoke.

    Raised (not swallowed) when a plugin that WAS configured -- via the
    ``phase_loop_runtime.skill_sources`` entry-point group or a
    ``PHASE_LOOP_SKILL_SOURCE_PLUGINS`` opt-in spec -- errors on import/load/invoke.
    A registered-but-broken provider is a real bug and must fail loud rather than
    silently degrade to zero roots (which would let injection inject an empty skill
    bundle). An ABSENT provider (clean runtime, nothing configured) is NOT an error:
    it simply yields no roots.
    """


def iter_skill_source_roots() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Yield (harness_target, source_roots) pairs from installed skill-source plugins.

    Profiles contribute roots two ways, mirroring the ``profile_commands`` seam
    (see :func:`phase_loop_runtime.cli._profile_command_registrars`):

    1. the ``phase_loop_runtime.skill_sources`` entry-point group (declared by a
       profile distribution) -- the production seam, live in a real install;
    2. the explicit ``PHASE_LOOP_SKILL_SOURCE_PLUGINS`` opt-in -- a comma-separated
       list of ``module:attr`` provider specs, used in source-mode runs and tests
       where the dist-info entry point is not live (the in-tree dotfiles profile is
       opted in by the test conftest, exactly as the profile plugin is).

    Each provider is a callable returning a ``{harness_target: (root, ...)}`` mapping.
    A clean runtime with neither configured yields nothing -- discovery is plugin-
    driven, never a fleet-path walk. Roots are de-duplicated per harness so a provider
    loaded via BOTH the entry point and the env opt-in contributes each root once.

    Fail-loud (CR #2): a provider that is REGISTERED but raises on load/invoke
    propagates as :class:`SkillSourcePluginError` -- a real bug must not silently
    degrade to zero roots. An absent provider is fine (empty result).

    Cached on the ``PHASE_LOOP_SKILL_SOURCE_PLUGINS`` value (CR #4): the env opt-in is
    read OUTSIDE the cache and passed as the key, so setting the env AFTER a first call
    takes effect (the installed entry-point set is genuinely process-constant). Tests
    that monkeypatch ``importlib.metadata.entry_points`` or the env var must call
    ``iter_skill_source_roots.cache_clear()``.
    """
    import os

    opt_in = os.environ.get("PHASE_LOOP_SKILL_SOURCE_PLUGINS", "")
    return _iter_skill_source_roots_cached(opt_in)


@functools.lru_cache(maxsize=8)
def _iter_skill_source_roots_cached(opt_in: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    import importlib
    import importlib.metadata

    providers: list = []
    seen: set = set()

    def _add(provider) -> None:
        key = id(provider)
        if key in seen:
            return
        seen.add(key)
        providers.append(provider)

    try:
        entry_points = importlib.metadata.entry_points(group="phase_loop_runtime.skill_sources")
    except TypeError:  # pragma: no cover - py<3.10 selectable API
        entry_points = importlib.metadata.entry_points().get("phase_loop_runtime.skill_sources", [])
    for entry_point in entry_points:
        # A registered entry point that fails to load is a real bug -- fail loud.
        try:
            _add(entry_point.load())
        except Exception as exc:
            name = getattr(entry_point, "name", entry_point)
            raise SkillSourcePluginError(
                f"skill-source entry point {name!r} failed to load: {exc}"
            ) from exc

    for spec in opt_in.split(","):
        spec = spec.strip()
        if not spec or ":" not in spec:
            # An empty/malformed spec is "not configured", not a registered failure.
            continue
        module_name, _, attr = spec.partition(":")
        # A spec the operator DID configure that won't import/resolve is a real bug.
        try:
            module = importlib.import_module(module_name)
            _add(getattr(module, attr))
        except Exception as exc:
            raise SkillSourcePluginError(
                f"skill-source opt-in {spec!r} failed to load: {exc}"
            ) from exc

    collected: dict[str, tuple[str, ...]] = {}
    for provider in providers:
        # A registered provider that raises when invoked is a real bug -- fail loud.
        try:
            mapping = provider() if callable(provider) else provider
        except Exception as exc:
            raise SkillSourcePluginError(
                f"skill-source provider {provider!r} raised on invoke: {exc}"
            ) from exc
        if not isinstance(mapping, dict):
            continue
        for harness_target, roots in mapping.items():
            existing = collected.get(harness_target, ())
            merged = list(existing)
            for root in roots:
                if root not in merged:
                    merged.append(root)
            collected[harness_target] = tuple(merged)
    return tuple(collected.items())


# Re-expose cache_clear on the public function so existing tests
# (test_phase_loop_runtime_decouple, test_skill_sources_seam) keep working.
iter_skill_source_roots.cache_clear = _iter_skill_source_roots_cached.cache_clear  # type: ignore[attr-defined]
iter_skill_source_roots.cache_info = _iter_skill_source_roots_cached.cache_info  # type: ignore[attr-defined]


BRIDGE_SKILL_NAMES = {
    "codex": "codex-phase-loop",
    "claude": "claude-phase-loop",
    "gemini": "gemini-phase-loop",
    "opencode": "opencode-phase-loop",
}
CANONICAL_WORKFLOW_SKILLS = {
    "codex": (
        "codex-phase-roadmap-builder",
        "codex-plan-phase",
        "codex-execute-phase",
        "codex-phase-loop",
        "codex-plan-detailed",
        "codex-execute-detailed",
        "codex-task-contextualizer",
        "codex-skill-improvement-planner",
        "codex-skill-editor",
    ),
    "claude": (
        "claude-phase-roadmap-builder",
        "claude-plan-phase",
        "claude-execute-phase",
        "claude-phase-loop",
        "claude-plan-detailed",
        "claude-execute-detailed",
        "claude-task-contextualizer",
        "claude-skill-improvement-planner",
        "claude-skill-editor",
    ),
    "gemini": (
        "gemini-phase-roadmap-builder",
        "gemini-plan-phase",
        "gemini-execute-phase",
        "gemini-phase-loop",
        "gemini-plan-detailed",
        "gemini-execute-detailed",
        "gemini-task-contextualizer",
        "gemini-skill-improvement-planner",
        "gemini-skill-editor",
    ),
    "opencode": (
        "opencode-phase-roadmap-builder",
        "opencode-plan-phase",
        "opencode-execute-phase",
        "opencode-phase-loop",
        "opencode-plan-detailed",
        "opencode-execute-detailed",
        "opencode-task-contextualizer",
        "opencode-skill-improvement-planner",
        "opencode-skill-editor",
    ),
}
VESTIGIAL_WORKFLOW_CANDIDATE_ROOTS = (
    "claude-config/skills/plan-phase",
    "claude-config/skills/execute-phase",
)
LEGACY_CLAUDE_UTILITY_ROOT = "claude-config/skills"
PI_ROLE_SKILL_ROOT = "phase-loop-pi/skills"
NON_CANONICAL_WORKFLOW_ALLOWLIST: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillParity:
    recommended_installed_roots: tuple[str, ...]
    installed_skill_roots: tuple[str, ...]
    installed_skill_warnings: tuple[str, ...]
    bridge_skill_inventory: tuple[dict[str, str | tuple[str, ...] | None], ...] = ()


@dataclass(frozen=True)
class BridgeSkillInventoryRecord:
    harness_target: str
    skill_name: str
    source_dir: str | None
    recommended_installed_roots: tuple[str, ...]
    installed_skill_roots: tuple[str, ...]
    installed_path: str | None
    parity_status: str
    repair_target: str | None

    def to_json(self) -> dict[str, str | tuple[str, ...] | None]:
        return {
            "harness_target": self.harness_target,
            "skill_name": self.skill_name,
            "source_dir": self.source_dir,
            "recommended_installed_roots": self.recommended_installed_roots,
            "installed_skill_roots": self.installed_skill_roots,
            "installed_path": self.installed_path,
            "parity_status": self.parity_status,
            "repair_target": self.repair_target,
        }


@dataclass(frozen=True)
class WorkflowSkillInventoryRecord:
    harness_target: str
    skill_name: str
    source_dir: str | None
    recommended_installed_roots: tuple[str, ...]
    installed_skill_roots: tuple[str, ...]
    installed_path: str | None
    parity_status: str

    def to_json(self) -> dict[str, str | tuple[str, ...] | None]:
        return {
            "harness_target": self.harness_target,
            "skill_name": self.skill_name,
            "source_dir": self.source_dir,
            "recommended_installed_roots": self.recommended_installed_roots,
            "installed_skill_roots": self.installed_skill_roots,
            "installed_path": self.installed_path,
            "parity_status": self.parity_status,
        }


@dataclass(frozen=True)
class VestigialWorkflowCandidateRecord:
    path: str
    candidate_name: str
    exists: bool
    skill_file: str | None
    status: str

    def to_json(self) -> dict[str, str | bool | None]:
        return {
            "path": self.path,
            "candidate_name": self.candidate_name,
            "exists": self.exists,
            "skill_file": self.skill_file,
            "status": self.status,
        }


@dataclass(frozen=True)
class SkillDirectoryClassificationRecord:
    path: str
    skill_name: str
    classification: str
    reason: str
    canonical_replacement: str | None = None
    skill_file: str | None = None

    def to_json(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "skill_name": self.skill_name,
            "classification": self.classification,
            "reason": self.reason,
            "canonical_replacement": self.canonical_replacement,
            "skill_file": self.skill_file,
        }


def recommended_installed_roots(harness_target: str) -> tuple[str, ...]:
    return HARNESS_INSTALL_ROOT_HINTS.get(harness_target, ())


def discover_installed_skill_roots(harness_target: str) -> tuple[str, ...]:
    roots: list[str] = []
    for hint in recommended_installed_roots(harness_target):
        path = Path(hint).expanduser()
        if path.exists():
            roots.append(str(path.resolve()))
    return tuple(roots)


def resolve_source_skill_dir(repo: Path, harness_target: str, skill_name: str) -> Path | None:
    plugin_roots = dict(iter_skill_source_roots()).get(harness_target, ())
    builtin_roots = HARNESS_SOURCE_ROOTS.get(harness_target, ())
    source_roots = (*builtin_roots, *plugin_roots)
    for root in source_roots:
        candidate = repo / root / skill_name
        if candidate.is_dir():
            return candidate.resolve()
    runner_root = _runner_repo_root()
    if runner_root is not None:
        for root in source_roots:
            candidate = runner_root / root / skill_name
            if candidate.is_dir():
                return candidate.resolve()
    return None


def inspect_skill_parity(repo: Path, harness_target: str, expected_skill_pack: tuple[str, ...]) -> SkillParity:
    recommended = recommended_installed_roots(harness_target)
    installed_roots = discover_installed_skill_roots(harness_target)
    bridge_inventory = inspect_bridge_skill(repo, harness_target)
    warnings = (_bridge_skill_warning(bridge_inventory),) if bridge_inventory is not None else ()
    inventory_json = (bridge_inventory.to_json(),) if bridge_inventory is not None else ()
    return SkillParity(
        recommended_installed_roots=recommended,
        installed_skill_roots=installed_roots,
        installed_skill_warnings=warnings,
        bridge_skill_inventory=inventory_json,
    )


def inspect_bridge_skill_inventory(
    repo: Path,
    harness_targets: tuple[str, ...] = ("codex", "claude", "gemini", "opencode"),
) -> tuple[BridgeSkillInventoryRecord, ...]:
    records: list[BridgeSkillInventoryRecord] = []
    for harness_target in harness_targets:
        record = inspect_bridge_skill(repo, harness_target)
        if record is not None:
            records.append(record)
    return tuple(records)


def inspect_workflow_skill_inventory(
    repo: Path,
    harness_targets: tuple[str, ...] = ("codex", "claude", "gemini", "opencode"),
) -> tuple[WorkflowSkillInventoryRecord, ...]:
    records: list[WorkflowSkillInventoryRecord] = []
    for harness_target in harness_targets:
        recommended = recommended_installed_roots(harness_target)
        installed_roots = discover_installed_skill_roots(harness_target)
        for skill_name in CANONICAL_WORKFLOW_SKILLS.get(harness_target, ()):
            source_dir = resolve_source_skill_dir(repo, harness_target, skill_name)
            installed_match = _matching_installed_skill(installed_roots, skill_name)
            if source_dir is None:
                parity_status = "missing_source"
            elif not installed_roots:
                parity_status = "missing_root"
            elif installed_match is None:
                parity_status = "missing_skill"
            elif _paths_equivalent(source_dir, installed_match) or _skill_hash(source_dir) == _skill_hash(installed_match):
                parity_status = "ok"
            else:
                parity_status = "drifted"
            records.append(
                WorkflowSkillInventoryRecord(
                    harness_target=harness_target,
                    skill_name=skill_name,
                    source_dir=str(source_dir) if source_dir is not None else None,
                    recommended_installed_roots=recommended,
                    installed_skill_roots=installed_roots,
                    installed_path=str(installed_match) if installed_match is not None else None,
                    parity_status=parity_status,
                )
            )
    return tuple(records)


def inspect_vestigial_workflow_candidates(repo: Path) -> tuple[VestigialWorkflowCandidateRecord, ...]:
    records: list[VestigialWorkflowCandidateRecord] = []
    for root in VESTIGIAL_WORKFLOW_CANDIDATE_ROOTS:
        path = repo / root
        skill_file = path / "SKILL.md"
        records.append(
            VestigialWorkflowCandidateRecord(
                path=str(path),
                candidate_name=path.name,
                exists=path.exists(),
                skill_file=str(skill_file) if skill_file.exists() else None,
                status="remove" if skill_file.exists() else "archived-history",
            )
        )
    return tuple(records)


def classify_skill_like_directories(
    repo: Path,
    harness_targets: tuple[str, ...] = ("codex", "claude", "gemini", "opencode"),
) -> tuple[SkillDirectoryClassificationRecord, ...]:
    records: list[SkillDirectoryClassificationRecord] = []
    canonical_names = {
        harness_target: set(CANONICAL_WORKFLOW_SKILLS.get(harness_target, ()))
        for harness_target in harness_targets
    }
    plugin_source_roots = dict(iter_skill_source_roots())
    for harness_target in harness_targets:
        # DISENTANGLE SL-2: iterate the MERGED builtin+plugin roots (mirrors
        # resolve_source_skill_dir above) so canonical records still emit once the
        # built-in HARNESS_SOURCE_ROOTS entries are empty and the dotfiles overlay
        # roots arrive through the skill_sources seam.
        merged_roots = (
            *HARNESS_SOURCE_ROOTS.get(harness_target, ()),
            *plugin_source_roots.get(harness_target, ()),
        )
        for root in merged_roots:
            base = repo / root
            if not base.is_dir():
                continue
            for child in sorted((path for path in base.iterdir() if path.is_dir()), key=lambda path: path.name):
                if child.name.startswith("."):
                    continue
                skill_file = child / "SKILL.md"
                if child.name in canonical_names[harness_target]:
                    records.append(
                        SkillDirectoryClassificationRecord(
                            path=str(child),
                            skill_name=child.name,
                            classification="canonical",
                            reason=f"{harness_target} harness workflow source",
                            skill_file=str(skill_file) if skill_file.exists() else None,
                        )
                    )
    legacy_root = repo / LEGACY_CLAUDE_UTILITY_ROOT
    if legacy_root.is_dir():
        vestigial_names = {Path(root).name for root in VESTIGIAL_WORKFLOW_CANDIDATE_ROOTS}
        for child in sorted((path for path in legacy_root.iterdir() if path.is_dir()), key=lambda path: path.name):
            if child.name.startswith("."):
                continue
            skill_file = child / "SKILL.md"
            if child.name in vestigial_names:
                records.append(_classify_vestigial_workflow_directory(child))
            elif skill_file.exists():
                records.append(
                    SkillDirectoryClassificationRecord(
                        path=str(child),
                        skill_name=child.name,
                        classification="legacy-utility",
                        reason="legacy Claude utility skill root, not a harness workflow source",
                        skill_file=str(skill_file),
                    )
                )
    pi_root = repo / PI_ROLE_SKILL_ROOT
    if pi_root.is_dir():
        for child in sorted((path for path in pi_root.iterdir() if path.is_dir()), key=lambda path: path.name):
            skill_file = child / "SKILL.md"
            if skill_file.exists():
                records.append(
                    SkillDirectoryClassificationRecord(
                        path=str(child),
                        skill_name=child.name,
                        classification="pi-role",
                        reason="Pi Agent role-style skill exception",
                        skill_file=str(skill_file),
                    )
                )
    return tuple(records)


def inspect_bridge_skill(repo: Path, harness_target: str) -> BridgeSkillInventoryRecord | None:
    skill_name = BRIDGE_SKILL_NAMES.get(harness_target)
    if skill_name is None:
        return None
    recommended = recommended_installed_roots(harness_target)
    installed_roots = discover_installed_skill_roots(harness_target)
    source_dir = resolve_source_skill_dir(repo, harness_target, skill_name)
    installed_match = _matching_installed_skill(installed_roots, skill_name)
    repair_target = _repair_target(recommended, installed_roots, skill_name)
    if source_dir is None:
        parity_status = "missing_skill"
    elif not installed_roots:
        parity_status = "missing_root"
    elif installed_match is None:
        parity_status = "missing_skill"
    elif _paths_equivalent(source_dir, installed_match) or _skill_hash(source_dir) == _skill_hash(installed_match):
        parity_status = "ok"
    else:
        parity_status = "drifted"
    return BridgeSkillInventoryRecord(
        harness_target=harness_target,
        skill_name=skill_name,
        source_dir=str(source_dir) if source_dir is not None else None,
        recommended_installed_roots=recommended,
        installed_skill_roots=installed_roots,
        installed_path=str(installed_match) if installed_match is not None else None,
        parity_status=parity_status,
        repair_target=repair_target,
    )


def _classify_vestigial_workflow_directory(path: Path) -> SkillDirectoryClassificationRecord:
    skill_file = path / "SKILL.md"
    replacement = {
        "plan-phase": "claude-plan-phase",
        "execute-phase": "claude-execute-phase",
    }.get(path.name)
    if skill_file.exists() and path.name not in NON_CANONICAL_WORKFLOW_ALLOWLIST:
        classification = "remove"
        reason = "non-canonical workflow skill definition under legacy Claude utility root"
    else:
        classification = "archived-history"
        reason = "ignored historical handoff or reflection residue, not a runtime skill source"
    return SkillDirectoryClassificationRecord(
        path=str(path),
        skill_name=path.name,
        classification=classification,
        reason=reason,
        canonical_replacement=replacement,
        skill_file=str(skill_file) if skill_file.exists() else None,
    )


def _matching_installed_skill(installed_roots: tuple[str, ...], skill_name: str) -> Path | None:
    for root in installed_roots:
        candidate = Path(root) / skill_name
        if candidate.exists():
            return candidate.resolve()
    return None


def _paths_equivalent(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _skill_hash(path: Path) -> str:
    digest = hashlib.sha256()
    skill_file = path / "SKILL.md"
    if skill_file.exists():
        digest.update(skill_file.read_bytes())
    else:
        digest.update(path.name.encode("utf-8"))
    return digest.hexdigest()


def _repair_target(recommended_roots: tuple[str, ...], installed_roots: tuple[str, ...], skill_name: str) -> str | None:
    roots = installed_roots or tuple(str(Path(root).expanduser()) for root in recommended_roots)
    if not roots:
        return None
    return str((Path(roots[0]) / skill_name).expanduser())


def _bridge_skill_warning(record: BridgeSkillInventoryRecord) -> str:
    prefix = f"{record.harness_target}:{record.skill_name}"
    if record.parity_status == "ok":
        return f"{prefix}: installed bridge skill is in sync for manual reentry"
    if record.parity_status == "missing_root":
        return (
            f"{prefix}: installed bridge root missing; autonomous injected bundle remains authoritative, "
            f"but local reentry should run sync-skills --apply"
        )
    if record.parity_status == "missing_skill":
        return (
            f"{prefix}: installed bridge skill missing; autonomous injected bundle remains authoritative, "
            f"but local reentry should run sync-skills --apply"
        )
    return (
        f"{prefix}: installed bridge skill drifted from repo source; autonomous injected bundle remains authoritative, "
        f"but local reentry should run sync-skills --apply"
    )
