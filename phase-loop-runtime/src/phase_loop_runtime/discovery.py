from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:  # Optional in stripped adapter runtimes; normal installs and tests provide it.
    import yaml
except Exception:  # pragma: no cover
    yaml = None

from .events import read_events
from .models import (
    BLOCKER_CLASSES,
    DispatchHints,
    ExecutionPolicyDocument,
    ExecutionPolicyParseError,
    ExecutionPolicyRule,
    PHASE_SOURCE_BUNDLE_SCHEMA,
    PHASE_STATUSES,
    PhaseSourceBundle,
    PhaseTeamEligibility,
    PipelineMetadataDiagnostic,
    PipelinePlanMetadata,
    PipelineProtectedSource,
    PIPELINE_PROTECTED_SOURCE_ROLES,
    PRODUCT_LOOP_ACTIONS,
    require_literal,
)
from .provenance import roadmap_sha256
from .runtime_paths import phase_loop_state_read_file


PHASE_RE = re.compile(
    r"^###\s+Phase\s+\d+(?:\.\d+)?[A-Z]?\s+.*?\(([A-Z][A-Z0-9._-]*)(?:\s*,[^)]*)?\)[ \t]*(?:\S[^\n]*)?$",
    re.MULTILINE,
)
PLAN_RE = re.compile(r"phase-plan-(v[\w.-]+?)-([A-Z][A-Za-z0-9._-]*?)\.md$")
LANE_SECTION_RE = re.compile(
    r"^###\s+(?:SL-\d+[A-Z]?|[A-Z][A-Z0-9]*-\d+[A-Z]?|Lane\s+\d+|Swim\s*lane\s+\d+)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
OWNED_FILES_RE = re.compile(r"^\s*(?:-\s+)?\*\*Owned files\*\*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
DISPATCH_SECTION_RE = re.compile(
    r"^#{2,3}\s+Dispatch Hints\s*$\n(?P<body>.*?)(?=^#{1,3}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
DISPATCH_SUBSECTION_RE = re.compile(
    r"^#{3,4}\s+(?P<label>default|roadmap|plan|roadmap default|plan default|"
    r"roadmap|plan|execute|repair|review|maintain-skills)\s*$\n(?P<body>.*?)(?=^#{3,4}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
DISPATCH_LINE_RE = re.compile(
    r"^\s*-\s*(?:(?P<action>roadmap|plan|execute|repair|review|maintain-skills)\s+)?"
    r"(?P<label>preferred executors|allowed executors|fallback executors|disabled executors|required capabilities)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
EXECUTION_POLICY_SECTION_RE = re.compile(
    r"^#{2,3}\s+Execution Policy\s*$\n(?P<body>.*?)(?=^#{1,3}\s+\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
EXECUTION_POLICY_LINE_RE = re.compile(r"^\s*-\s*(?P<selector>[^:]+?)\s*:\s*(?P<value>.+?)\s*$", re.MULTILINE)
EXECUTION_POLICY_ASSIGNMENT_RE = re.compile(
    r"(?P<key>executor|model|effort|work-unit|work_unit|unsupported|fallback|inherit-default|inherit_default|reason)"
    r"\s*=\s*(?P<value>`[^`]+`|[^,;]+)",
    re.IGNORECASE,
)

WORKFLOW_PLAN_SKILLS = (
    "codex-plan-phase",
    "claude-plan-phase",
    "gemini-plan-phase",
    "opencode-plan-phase",
)

WORKFLOW_EXECUTE_SKILLS = (
    "codex-execute-phase",
    "claude-execute-phase",
    "gemini-execute-phase",
    "opencode-execute-phase",
)


@dataclass(frozen=True)
class RepoIdentity:
    root: Path
    repo_hash: str
    branch: str
    branch_slug: str
    commit: str


@dataclass(frozen=True)
class PlanOwnership:
    owned_patterns: tuple[str, ...]
    control_paths: tuple[str, ...]
    valid: bool
    errors: tuple[str, ...] = ()

    def matches(self, repo_path: str) -> bool:
        return (
            repo_path in self.control_paths
            or repo_path in self.owned_patterns
            or any(_owned_pattern_matches(repo_path, pattern) for pattern in self.owned_patterns)
        )

    def matches_dirty_output(self, repo_path: str) -> bool:
        return self.matches(repo_path) or expanded_dirty_ownership_matches(self, repo_path)


@dataclass(frozen=True)
class PlanLane:
    heading: str
    owned_patterns: tuple[str, ...]
    read_only: bool
    text: str


DIRTY_WORKTREE_METADATA_KEYS = (
    "completion_dirty_worktree",
    "plan_dirty_worktree",
    "incomplete_execute_dirty_worktree",
)


def previous_phase_owned_dirty_paths(repo: Path, phase: str | None) -> tuple[str, ...]:
    if not phase:
        return ()
    phase_alias = phase.upper()
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase_alias:
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for key in DIRTY_WORKTREE_METADATA_KEYS:
            dirty = metadata.get(key)
            if isinstance(dirty, dict) and "dirty_paths" in dirty:
                return _normalized_path_tuple(dirty.get("phase_owned_dirty_paths"))
        terminal = metadata.get("terminal_summary")
        if isinstance(terminal, dict) and "dirty_paths" in terminal:
            return _normalized_path_tuple(terminal.get("phase_owned_dirty_paths"))
    return ()


def _normalized_path_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path or path in seen:
            continue
        paths.append(path)
        seen.add(path)
    return tuple(paths)


@dataclass(frozen=True)
class DispatchHintsParseError:
    """Surfaced when ``## Dispatch Hints`` emits a literal not in the runner's
    DISPATCH_CAPABILITIES / EXECUTORS allowlist. Mirrors F3's
    ExecutionPolicyParseError pattern: callers convert this to a
    contract_bug blocker instead of crashing on raw ValueError."""

    path: str
    bucket: str  # e.g., "default", "execute", "plan"
    raw_message: str  # the original ValueError from require_literal
    invalid_literal: str | None = None  # extracted from raw_message when possible


@dataclass(frozen=True)
class CloseoutParseError:
    """Native closeout literal drift surfaced before BAML schema parsing.

    The runner converts these diagnostics to repairable non-human
    ``contract_bug`` blockers, preserving the original invalid literal.
    """

    source: str
    field: str
    raw_message: str
    invalid_literal: str | None = None


@dataclass(frozen=True)
class SuiteCommandFinding:
    code: str
    message: str
    source: str
    value: object = None


def _extract_invalid_literal(message: str) -> str | None:
    """Best-effort extraction of the offending literal from a require_literal
    ValueError message. Format is typically: 'invalid {label}: {value}'."""
    if ":" in message:
        candidate = message.rsplit(":", 1)[-1].strip()
        if candidate:
            return candidate
    return None


CLOSEOUT_VERIFICATION_STATUSES = ("not_run", "passed", "failed", "blocked")


def parse_closeout_payload(text: str, *, kind: str = "closeout") -> dict[str, Any] | None:
    """Backward-compatible payload-only closeout parser.

    Literal drift returns ``None`` so existing callers can keep using a simple
    truthy payload check. Schema/type errors still follow the BAML validation
    path and raise ``BamlValidationError``.
    """
    payload, errors = parse_closeout_payload_doc(text, kind=kind)
    return None if errors else payload


def parse_closeout_payload_doc(
    text: str, *, kind: str
) -> tuple[dict[str, Any] | None, tuple[CloseoutParseError, ...]]:
    """Parse native closeout JSON with graceful soft-fail for enum drift.

    Unknown ``terminal_status``, ``verification_status``, and
    ``blocker_class`` literals become structured diagnostics instead of raw
    ``ValueError`` crashes. Valid payloads still pass through the canonical
    ``EmitPhaseCloseout`` BAML validation path for schema and type checks.
    """
    from .baml_modular import parse_baml_response

    extracted = _find_closeout_payload_doc(text)
    parse_text = json.dumps(extracted) if extracted is not None else str(text or "")
    literal_source = extracted if extracted is not None else {}
    errors = _closeout_literal_errors(literal_source, kind=kind) if isinstance(literal_source, dict) else []
    if errors:
        return None, tuple(errors)
    return parse_baml_response("EmitPhaseCloseout", parse_text).payload, ()


def _find_closeout_payload_doc(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    raw = str(text or "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, str):
        return _find_closeout_payload_doc(data)
    if isinstance(data, dict) and {"terminal_status", "verification_status", "dirty_paths"}.issubset(data):
        return data
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and {"terminal_status", "verification_status", "dirty_paths"}.issubset(data):
            return data
    return None


def _closeout_literal_errors(payload: dict[str, Any], *, kind: str) -> list[CloseoutParseError]:
    errors: list[CloseoutParseError] = []
    terminal_status = payload.get("terminal_status")
    verification_status = payload.get("verification_status")
    blocker_class = payload.get("blocker_class")

    _append_literal_error(errors, kind=kind, field="terminal_status", value=terminal_status, allowed=PHASE_STATUSES)
    _append_literal_error(
        errors,
        kind=kind,
        field="verification_status",
        value=verification_status,
        allowed=CLOSEOUT_VERIFICATION_STATUSES,
    )
    if blocker_class is not None:
        _append_literal_error(
            errors,
            kind=kind,
            field="blocker_class",
            value=blocker_class,
            allowed=(*BLOCKER_CLASSES, "none"),
        )
    if errors:
        return errors

    terminal_text = str(terminal_status)
    verification_text = str(verification_status)
    if terminal_text == "complete" and verification_text != "passed":
        errors.append(
            CloseoutParseError(
                source=kind,
                field="terminal_status+verification_status",
                raw_message="invalid closeout field-pair: terminal_status complete requires verification_status passed",
                invalid_literal=f"{terminal_text}/{verification_text}",
            )
        )
    elif terminal_text in {"planned", "unplanned"} and verification_text != "not_run":
        errors.append(
            CloseoutParseError(
                source=kind,
                field="terminal_status+verification_status",
                raw_message=f"invalid closeout field-pair: terminal_status {terminal_text} requires verification_status not_run",
                invalid_literal=f"{terminal_text}/{verification_text}",
            )
        )
    elif terminal_text == "blocked" and verification_text not in {"failed", "blocked"}:
        errors.append(
            CloseoutParseError(
                source=kind,
                field="terminal_status+verification_status",
                raw_message="invalid closeout field-pair: terminal_status blocked requires verification_status failed or blocked",
                invalid_literal=f"{terminal_text}/{verification_text}",
            )
        )
    return errors


def _append_literal_error(
    errors: list[CloseoutParseError], *, kind: str, field: str, value: Any, allowed: tuple[str, ...]
) -> None:
    try:
        require_literal(str(value), allowed, field)
    except ValueError as exc:
        errors.append(
            CloseoutParseError(
                source=kind,
                field=field,
                raw_message=str(exc),
                invalid_literal=_extract_invalid_literal(str(exc)),
            )
        )


def resolve_repo(repo: str | Path | None = None) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    try:
        out = subprocess.check_output(["git", "-C", str(base), "rev-parse", "--show-toplevel"], text=True)
        return Path(out.strip()).resolve()
    except Exception:
        return base


def slug_branch(branch: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-").lower()
    slug = re.sub(r"-+", "-", slug)
    return slug or "unknown"


def repo_identity(repo: str | Path | None = None) -> RepoIdentity:
    root = resolve_repo(repo)
    repo_hash = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    branch = _git(root, "branch", "--show-current") or _git(root, "rev-parse", "--short", "HEAD")
    commit = _git(root, "rev-parse", "--short", "HEAD") or "unknown"
    branch_slug = slug_branch(branch if branch else f"detached-{commit}")
    return RepoIdentity(root=root, repo_hash=repo_hash, branch=branch, branch_slug=branch_slug, commit=commit)


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def select_roadmap(repo: Path, explicit: str | Path | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = repo / path
        if not path.exists():
            raise FileNotFoundError(f"roadmap not found: {path}")
        return path.resolve()

    state_roadmap = active_state_roadmap(repo)
    if state_roadmap is not None:
        return state_roadmap

    manifest_roadmap = manifest_backed_roadmap(repo)
    if manifest_roadmap is not None:
        return manifest_roadmap

    handoff = latest_handoff_roadmap(repo_identity(repo), "codex-phase-roadmap-builder")
    if handoff is not None:
        return handoff

    candidates = sorted((repo / "specs").glob("phase-plans-v*.md"))
    if not candidates:
        raise FileNotFoundError("no specs/phase-plans-v*.md roadmap found")
    if len(candidates) != 1:
        raise RuntimeError("ambiguous roadmap selection")
    return candidates[0].resolve()


def active_state_roadmap(repo: Path) -> Path | None:
    path = phase_loop_state_read_file(repo)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    roadmap = data.get("roadmap")
    if not isinstance(roadmap, str) or not roadmap:
        return None
    roadmap_path = Path(roadmap).expanduser()
    if not roadmap_path.is_absolute():
        roadmap_path = repo / roadmap_path
    try:
        resolved = roadmap_path.resolve()
        resolved.relative_to(repo.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.exists() else None


def manifest_backed_roadmap(repo: Path) -> Path | None:
    if _phase_manifest_disabled():
        return None
    candidates: list[Path] = []
    for entry in _phase_manifest_entries(repo):
        if entry.roadmap_ref is None or entry.status == "orphaned":
            continue
        path = repo / entry.roadmap_ref.file
        try:
            resolved = path.resolve()
            resolved.relative_to(repo.resolve())
        except (OSError, ValueError):
            continue
        if resolved.exists() and resolved not in candidates:
            candidates.append(resolved)
    return candidates[0] if len(candidates) == 1 else None


def latest_handoff_roadmap(identity: RepoIdentity, predecessor: str) -> Path | None:
    path = Path.home() / ".codex" / "skills" / predecessor / "handoffs" / identity.repo_hash / identity.branch_slug / "latest.md"
    if not path.exists():
        return None
    frontmatter = parse_frontmatter(path.read_text())
    if frontmatter.get("from") != predecessor:
        return None
    if frontmatter.get("repo") != identity.repo_hash:
        return None
    if Path(frontmatter.get("repo_root", "")).resolve() != identity.root:
        return None
    if frontmatter.get("branch_slug") != identity.branch_slug:
        return None
    artifact = frontmatter.get("artifact")
    if not artifact:
        return None
    artifact_path = Path(artifact).expanduser().resolve()
    try:
        artifact_path.relative_to(identity.root)
    except ValueError:
        return None
    return artifact_path if artifact_path.exists() else None


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def parse_frontmatter_document(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    body = text[4:end]
    if yaml is not None:
        try:
            data = yaml.safe_load(body)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    data: dict[str, Any] = {}
    current_mapping: dict[str, Any] | None = None
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and current_mapping is not None and ":" in line:
            key, value = line.split(":", 1)
            current_mapping[key.strip()] = _plain_frontmatter_scalar(value.strip())
            continue
        current_mapping = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = _plain_frontmatter_scalar(value)
        else:
            nested: dict[str, Any] = {}
            data[key] = nested
            current_mapping = nested
    return data


def resolve_suite_command(repo: Path, roadmap: Path, plan: Path | None = None) -> list[str] | None:
    command, _findings = resolve_suite_command_doc(repo, roadmap, plan)
    return command


def resolve_suite_command_doc(repo: Path, roadmap: Path, plan: Path | None = None) -> tuple[list[str] | None, tuple[SuiteCommandFinding, ...]]:
    findings: list[SuiteCommandFinding] = []
    roadmap_value = _automation_suite_command(roadmap)
    plan_value = _automation_suite_command(plan) if plan is not None else None
    source = str(plan) if plan_value is not None and plan is not None else str(roadmap)
    raw_value = plan_value if plan_value is not None else roadmap_value
    if raw_value is None:
        return None, ()
    command, finding = _normalize_suite_command(raw_value, source=source)
    if finding is not None:
        findings.append(finding)
    return command, tuple(findings)


def validate_plan_verification_commands_for_intake(repo: Path, plan: Path) -> list[Any]:
    from .verification_evidence import ValidationFinding, validate_verification_commands

    commands, _operational = verification_commands_from_plan(plan)
    findings = list(validate_verification_commands(repo, commands))
    _suite, suite_findings = resolve_suite_command_doc(repo, _roadmap_from_plan(repo, plan), plan)
    for suite_finding in suite_findings:
        findings.append(
            ValidationFinding(
                code=suite_finding.code,
                message=suite_finding.message,
                command_index=-1,
                value=str(suite_finding.value) if suite_finding.value is not None else None,
            )
        )
    return findings


def verification_commands_from_plan(plan: Path) -> tuple[list[list[str]], list[dict[str, Any]]]:
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return [], []
    match = re.search(r"^##\s+Verification\s*$\n(?P<body>.*?)(?=^##\s+\S|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return [], []
    commands: list[list[str]] = []
    operational: list[dict[str, Any]] = []
    for line_number, line in enumerate(match.group("body").splitlines(), start=text[: match.start("body")].count("\n") + 1):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        raw = stripped[2:].strip()
        command_text = _strip_markdown_command(raw)
        if not command_text:
            continue
        if re.search(r"\bevidence\s*:\s*operational\b", raw, re.IGNORECASE):
            operational.append({"line": line_number, "command": command_text, "reason": "evidence: operational"})
            continue
        for chunk in _split_shell_and(command_text):
            try:
                argv = shlex.split(chunk)
            except ValueError:
                argv = []
            if argv:
                commands.append(argv)
    return commands, operational


def _automation_suite_command(path: Path | None) -> object:
    if path is None:
        return None
    try:
        data = parse_frontmatter_document(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    automation = data.get("automation")
    if not isinstance(automation, dict):
        return None
    return automation.get("suite_command")


def _normalize_suite_command(value: object, *, source: str) -> tuple[list[str] | None, SuiteCommandFinding | None]:
    if isinstance(value, str):
        try:
            argv = shlex.split(value)
        except ValueError as exc:
            return None, SuiteCommandFinding("malformed_suite_command", str(exc), source, value)
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        argv = list(value)
    elif isinstance(value, list):
        return None, SuiteCommandFinding(
            "malformed_suite_command",
            "automation.suite_command list entries must be strings",
            source,
            value,
        )
    else:
        return None, SuiteCommandFinding(
            "malformed_suite_command",
            "automation.suite_command must be a shell string or list of strings",
            source,
            value,
        )
    if not argv or any(not part for part in argv):
        return None, SuiteCommandFinding("empty_suite_command", "automation.suite_command must not be empty", source, value)
    return argv, None


def _strip_markdown_command(value: str) -> str:
    text = value.strip()
    if text.startswith("`") and "`" in text[1:]:
        end = text.find("`", 1)
        return text[1:end].strip()
    return text


def _split_shell_and(command: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+&&\s+", command) if part.strip()]


def _plain_frontmatter_scalar(value: str) -> Any:
    value = value.strip().strip("'\"")
    if value.startswith("[") and value.endswith("]"):
        try:
            data = json.loads(value)
            return data
        except json.JSONDecodeError:
            return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    return value


def _roadmap_from_plan(repo: Path, plan: Path) -> Path:
    metadata = plan_metadata(plan)
    roadmap = metadata.get("roadmap")
    return (repo / roadmap) if roadmap else repo / "specs" / "phase-plans-v1.md"


def parse_roadmap_phases(roadmap: Path) -> list[str]:
    text = roadmap.read_text()
    aliases = [match.group(1).strip() for match in PHASE_RE.finditer(text)]
    return aliases


def roadmap_closeout_evidence_audit_enabled(roadmap: Path) -> bool:
    text = roadmap.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    if _truthy_metadata_value(frontmatter.get("closeout_evidence_audit")):
        return True
    body_start = 0
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            body_start = end + len("\n---")
    body = text[body_start:]
    first_h2 = re.search(r"^##\s+.*$", body, re.MULTILINE)
    if first_h2 is None:
        return False
    second_h2 = re.search(r"^##\s+.*$", body[first_h2.end() :], re.MULTILINE)
    first_h2_body = (
        body[first_h2.end() :]
        if second_h2 is None
        else body[first_h2.end() : first_h2.end() + second_h2.start()]
    )
    return bool(
        re.search(
            r"^\s*(?:-\s*)?closeout_evidence_audit\s*:\s*true\s*$",
            first_h2_body,
            re.IGNORECASE | re.MULTILINE,
        )
    )


def _truthy_metadata_value(value: object) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "1", "on"}


def parse_dispatch_hints(path: Path, *, kind: str) -> dict[str, DispatchHints]:
    """Backward-compat entry: returns just the hints dict, silently dropping
    buckets that contain unknown literals (which used to crash). For the
    diagnostic-aware version that surfaces a parse_error for the runner to
    convert to a contract_bug blocker, use ``parse_dispatch_hints_doc``."""
    hints, _ = parse_dispatch_hints_doc(path, kind=kind)
    return hints


def parse_dispatch_hints_doc(
    path: Path, *, kind: str
) -> tuple[dict[str, DispatchHints], tuple[DispatchHintsParseError, ...]]:
    """Parse ## Dispatch Hints with graceful soft-fail.

    Per-bucket DispatchHints construction wrapped in try/except so a planner
    that invented an unknown literal (e.g. ``browser_automation`` before it
    was allowlisted) surfaces a structured DispatchHintsParseError rather
    than crashing the whole runner. Mirrors F3's parse_execution_policy
    pattern (parse_error field → runner converts to contract_bug blocker).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ()
    section_match = DISPATCH_SECTION_RE.search(text)
    if not section_match:
        return {}, ()
    section_body = section_match.group("body")
    buckets: dict[str, dict[str, list[str]]] = {}
    subsection_matches = list(DISPATCH_SUBSECTION_RE.finditer(section_body))
    if subsection_matches:
        for match in subsection_matches:
            label = match.group("label").strip().lower()
            key = "default" if label in {"default", "roadmap", "plan", "roadmap default", "plan default"} else label
            _collect_dispatch_lines(match.group("body"), buckets, default_key=key)
    else:
        _collect_dispatch_lines(section_body, buckets, default_key="default")
    result: dict[str, DispatchHints] = {}
    errors: list[DispatchHintsParseError] = []
    for action_key, values in buckets.items():
        action = None if action_key == "default" else action_key
        try:
            result[action_key] = DispatchHints(
                preferred_executors=tuple(values.get("preferred_executors", ())),
                allowed_executors=tuple(values.get("allowed_executors", ())),
                fallback_executors=tuple(values.get("fallback_executors", ())),
                disabled_executors=tuple(values.get("disabled_executors", ())),
                required_capabilities=tuple(values.get("required_capabilities", ())),
                source=kind if action is None else f"{kind}:{action}",
                action=action,
            )
        except ValueError as exc:
            errors.append(
                DispatchHintsParseError(
                    path=str(path),
                    bucket=action_key,
                    raw_message=str(exc),
                    invalid_literal=_extract_invalid_literal(str(exc)),
                )
            )
            # Skip the bucket — downstream callers see no hints for this
            # action_key, which is preferable to crashing the whole loop.
    return result, tuple(errors)


def dispatch_hints_for_action(hints: dict[str, DispatchHints], action: str) -> DispatchHints | None:
    action_key = action.lower()
    specific = hints.get(action_key)
    default = hints.get("default")
    if specific and default:
        return DispatchHints(
            preferred_executors=specific.preferred_executors or default.preferred_executors,
            allowed_executors=specific.allowed_executors or default.allowed_executors,
            fallback_executors=specific.fallback_executors or default.fallback_executors,
            disabled_executors=tuple(dict.fromkeys((*default.disabled_executors, *specific.disabled_executors))),
            required_capabilities=tuple(dict.fromkeys((*default.required_capabilities, *specific.required_capabilities))),
            source=specific.source,
            action=action_key,
        )
    if specific:
        return specific
    if default:
        return DispatchHints(
            preferred_executors=default.preferred_executors,
            allowed_executors=default.allowed_executors,
            fallback_executors=default.fallback_executors,
            disabled_executors=default.disabled_executors,
            required_capabilities=default.required_capabilities,
            source=default.source,
            action=action_key,
        )
    return None


def parse_execution_policy(path: Path, *, kind: str) -> ExecutionPolicyDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ExecutionPolicyDocument(source=kind)
    section_match = EXECUTION_POLICY_SECTION_RE.search(text)
    if not section_match:
        return ExecutionPolicyDocument(source=kind)
    body = section_match.group("body")
    body_offset = section_match.start("body")
    rules: list[ExecutionPolicyRule] = []
    for match in EXECUTION_POLICY_LINE_RE.finditer(body):
        selector = match.group("selector").strip()
        try:
            assignments = _execution_policy_assignments(match.group("value"))
            rules.append(_execution_policy_rule(selector, assignments, source=kind))
        except ValueError as exc:
            raw_line = text[match.start() + body_offset : match.end() + body_offset]
            line_number = text.count("\n", 0, match.start() + body_offset) + 1
            return ExecutionPolicyDocument(
                rules=(),
                source=kind,
                parse_error=ExecutionPolicyParseError(
                    path=str(path),
                    line_number=line_number,
                    raw_line=raw_line.strip(),
                    detail=str(exc),
                ),
            )
    return ExecutionPolicyDocument(rules=tuple(rules), source=kind)


def execution_policy_for_action(document: ExecutionPolicyDocument, action: str) -> ExecutionPolicyRule | None:
    action_key = action.lower()
    default_rule: ExecutionPolicyRule | None = None
    specific_rule: ExecutionPolicyRule | None = None
    for rule in document.rules:
        if rule.lane is not None:
            continue
        if rule.action == action_key:
            specific_rule = rule
        elif rule.selector == "default":
            default_rule = rule
    if specific_rule and default_rule:
        return _merge_execution_policy_rules(default_rule, specific_rule, selector=action_key, action=action_key)
    if specific_rule:
        return specific_rule
    if default_rule:
        return _merge_execution_policy_rules(default_rule, None, selector=action_key, action=action_key)
    return None


def execution_policy_for_lane(document: ExecutionPolicyDocument, action: str, lane: str) -> ExecutionPolicyRule | None:
    action_rule = execution_policy_for_action(document, action)
    lane_key = lane.strip().upper()
    lane_rule = next((rule for rule in document.rules if rule.lane == lane_key), None)
    if lane_rule and action_rule:
        return _merge_execution_policy_rules(action_rule, lane_rule, selector=lane_key, action=action.lower(), lane=lane_key)
    if lane_rule:
        return _merge_execution_policy_rules(lane_rule, None, selector=lane_key, action=action.lower(), lane=lane_key)
    return action_rule


def execution_policy_dispatch_hints(rule: ExecutionPolicyRule | None) -> DispatchHints | None:
    if rule is None or not rule.executor:
        return None
    fallbacks = (rule.fallback,) if rule.fallback in {"codex", "claude", "gemini", "opencode", "command", "manual"} else ()
    return DispatchHints(
        preferred_executors=(rule.executor,),
        fallback_executors=fallbacks,
        source=f"{rule.source}-policy",
        action=rule.action,
    )


def _execution_policy_assignments(value: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for match in EXECUTION_POLICY_ASSIGNMENT_RE.finditer(value):
        key = match.group("key").lower().replace("-", "_")
        raw_value = match.group("value").strip().strip("`").strip()
        assignments[key] = raw_value
    if not assignments:
        raise ValueError(f"malformed execution policy line: {value}")
    return assignments


def _execution_policy_rule(selector: str, assignments: dict[str, str], *, source: str) -> ExecutionPolicyRule:
    selector_key = selector.strip().lower()
    action = selector_key if selector_key in PRODUCT_LOOP_ACTIONS else None
    lane = None
    normalized_selector = selector_key
    if selector_key.startswith("lane "):
        lane = selector_key.split(None, 1)[1].strip().upper()
        normalized_selector = lane
    elif re.match(r"^[a-z]+-\d+[a-z]?$", selector_key, re.IGNORECASE):
        lane = selector_key.upper()
        normalized_selector = lane
    elif selector_key in {"default", "work-unit defaults", "work unit defaults"}:
        normalized_selector = "default"
    elif action is None:
        raise ValueError(f"invalid execution policy selector: {selector}")
    inherit_default = assignments.get("inherit_default", "false").lower() in {"true", "yes", "1"}
    return ExecutionPolicyRule(
        selector=normalized_selector,
        action=action,
        lane=lane,
        executor=assignments.get("executor"),
        model=assignments.get("model"),
        effort=assignments.get("effort"),
        work_unit_kind=assignments.get("work_unit"),
        unsupported_policy_behavior=assignments.get("unsupported", "block"),
        fallback=assignments.get("fallback"),
        inherit_default=inherit_default,
        source=source if action is None and lane is None else f"{source}:{normalized_selector}",
        override_reason=assignments.get("reason"),
    )


def _merge_execution_policy_rules(
    base: ExecutionPolicyRule,
    override: ExecutionPolicyRule | None,
    *,
    selector: str,
    action: str | None,
    lane: str | None = None,
) -> ExecutionPolicyRule:
    if override is None:
        return ExecutionPolicyRule(
            selector=selector,
            action=action,
            lane=lane,
            executor=base.executor,
            model=base.model,
            effort=base.effort,
            work_unit_kind=base.work_unit_kind,
            unsupported_policy_behavior=base.unsupported_policy_behavior,
            fallback=base.fallback,
            inherit_default=base.inherit_default,
            source=base.source,
            override_reason=base.override_reason,
        )
    return ExecutionPolicyRule(
        selector=selector,
        action=action,
        lane=lane,
        executor=override.executor or base.executor,
        model=override.model or base.model,
        effort=override.effort or base.effort,
        work_unit_kind=override.work_unit_kind or base.work_unit_kind,
        unsupported_policy_behavior=override.unsupported_policy_behavior
        if override.unsupported_policy_behavior != "block" or override.fallback or override.inherit_default
        else base.unsupported_policy_behavior,
        fallback=override.fallback or base.fallback,
        inherit_default=override.inherit_default or base.inherit_default,
        source=override.source,
        override_reason=override.override_reason or base.override_reason,
    )


def find_plan_artifact(repo: Path, phase: str, roadmap: Path | None = None) -> Path | None:
    manifest_plan, regex_conflict = manifest_plan_artifact(repo, phase, roadmap=roadmap)
    if manifest_plan is not None:
        return manifest_plan
    if regex_conflict is not None:
        return None

    # Fast path: when we know the roadmap, construct the exact expected
    # plan-doc filename and check it directly. This handles cases the legacy
    # glob+regex iteration can't disambiguate, including hyphenated aliases
    # (e.g. SL-1 in v32) and suffix-bearing spec names (e.g.
    # phase-plans-v32-VISUALPARITY.md → phase-plan-v32-VISUALPARITY-SL-1.md).
    # Surfaced by the regen 2026-05-22/23 v32-VISUALPARITY incident; see
    # plans/detailed-phase-loop-plan-discovery-bugs-20260523-0224.md.
    if roadmap is not None:
        m = re.fullmatch(r"phase-plans-(v[\w.-]+)\.md", roadmap.name)
        if m:
            version = m.group(1)
            expected = repo / "plans" / f"phase-plan-{version}-{phase}.md"
            if expected.is_file():
                if plan_matches_roadmap(repo, expected, roadmap, phase):
                    return expected.resolve()
    # Fallback: glob+regex iteration (back-compat for case-folded matching
    # or when roadmap is None). PLAN_RE captures (version, alias);
    # group(2) is the alias.
    phase_lower = phase.lower()
    plans = sorted((repo / "plans").glob("phase-plan-v*-*.md"))
    for plan in plans:
        match = PLAN_RE.search(plan.name)
        if not match:
            continue
        if match.group(2).lower() != phase_lower:
            continue
        if roadmap is not None and not plan_matches_roadmap(repo, plan, roadmap, phase):
            continue
        return plan.resolve()
    return None


def manifest_plan_artifact(repo: Path, phase: str, roadmap: Path | None = None) -> tuple[Path | None, dict[str, object] | None]:
    if _phase_manifest_disabled():
        return None, None
    phase_upper = phase.upper()
    candidates: list[tuple[object, Path]] = []
    for entry in _phase_manifest_entries(repo):
        if str(entry.phase_alias or "").upper() != phase_upper:
            continue
        if entry.status == "orphaned":
            continue
        plan = repo / entry.file
        if not plan.exists():
            return None, {
                "source": "manifest",
                "phase": phase_upper,
                "status": entry.status,
                "reason": "manifest_plan_file_missing",
                "file": entry.file,
                "slug": entry.slug,
            }
        if roadmap is not None:
            roadmap_ref = entry.roadmap_ref.file if entry.roadmap_ref else None
            if roadmap_ref != roadmap_repo_relative_path(repo, roadmap):
                continue
            if not plan_matches_roadmap(repo, plan, roadmap, phase):
                continue
        candidates.append((entry.updated_at, plan.resolve()))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: str(item[0]))
    manifest_plan = candidates[-1][1]
    regex_plan = _regex_plan_artifact(repo, phase, roadmap=roadmap)
    if regex_plan is not None and regex_plan.resolve() != manifest_plan.resolve():
        return manifest_plan, {
            "source": "manifest",
            "phase": phase_upper,
            "status": "conflict",
            "reason": "manifest_regex_plan_conflict",
            "manifest_file": str(manifest_plan.relative_to(repo.resolve())),
            "regex_file": str(regex_plan.relative_to(repo.resolve())),
        }
    return manifest_plan, None


def _regex_plan_artifact(repo: Path, phase: str, roadmap: Path | None = None) -> Path | None:
    if roadmap is not None:
        m = re.fullmatch(r"phase-plans-(v[\w.-]+)\.md", roadmap.name)
        if m:
            version = m.group(1)
            expected = repo / "plans" / f"phase-plan-{version}-{phase}.md"
            if expected.is_file() and plan_matches_roadmap(repo, expected, roadmap, phase):
                return expected.resolve()
    phase_lower = phase.lower()
    for plan in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
        match = PLAN_RE.search(plan.name)
        if not match or match.group(2).lower() != phase_lower:
            continue
        if roadmap is not None and not plan_matches_roadmap(repo, plan, roadmap, phase):
            continue
        return plan.resolve()
    return None


def _phase_manifest_disabled() -> bool:
    return os.environ.get("PHASE_LOOP_MANIFEST_DISABLED") == "1"


def _phase_manifest_entries(repo: Path) -> tuple[object, ...]:
    try:
        from .plan_manifest import read_manifest, validate_manifest
    except Exception:
        return ()
    manifest_path = repo / "plans" / "manifest.json"
    if manifest_path.exists() and not validate_manifest(manifest_path).valid:
        return ()
    try:
        manifest = read_manifest(repo)
    except Exception:
        return ()
    return tuple(entry for entry in manifest.plans if entry.type == "phase")


def plan_is_stale(plan: Path, roadmap: Path) -> bool:
    repo = plan.resolve().parents[1] if plan.parent.name == "plans" else plan.parent
    return not plan_matches_roadmap(repo, plan, roadmap)


def plan_metadata(plan: Path) -> dict[str, str]:
    try:
        return parse_frontmatter(plan.read_text())
    except OSError:
        return {}


def roadmap_fingerprint(roadmap: Path) -> str:
    return roadmap_sha256(roadmap)


def roadmap_repo_relative_path(repo: Path, roadmap: Path) -> str:
    resolved_repo = repo.resolve()
    resolved_roadmap = roadmap.resolve()
    try:
        return str(resolved_roadmap.relative_to(resolved_repo))
    except ValueError:
        return str(resolved_roadmap)


def plan_matches_roadmap(repo: Path, plan: Path, roadmap: Path, phase: str | None = None) -> bool:
    return plan_artifact_diagnostic(repo, plan, roadmap, phase) is None


def plan_artifact_diagnostic(repo: Path, plan: Path, roadmap: Path, phase: str | None = None) -> str | None:
    metadata = plan_metadata(plan)
    if metadata.get("phase_loop_plan_version") != "1":
        return "missing_or_invalid_phase_loop_plan_version"
    if phase is not None and metadata.get("phase", "").upper() != phase.upper():
        return "mismatched_phase"
    if metadata.get("roadmap") != roadmap_repo_relative_path(repo, roadmap):
        return "mismatched_roadmap_path"
    try:
        if metadata.get("roadmap_sha256") != roadmap_fingerprint(roadmap):
            return "mismatched_roadmap_sha256"
    except OSError:
        return "roadmap_unreadable"
    pipeline_diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)
    if pipeline_diagnostic is not None:
        return f"pipeline_metadata:{pipeline_diagnostic.kind}"
    return None


def parse_pipeline_plan_metadata(plan_or_frontmatter: Path | dict[str, str]) -> PipelinePlanMetadata:
    metadata = plan_metadata(plan_or_frontmatter) if isinstance(plan_or_frontmatter, Path) else dict(plan_or_frontmatter)
    return PipelinePlanMetadata(
        source_bundle=metadata.get("source_bundle") or None,
        source_bundle_sha256=metadata.get("source_bundle_sha256") or None,
        pipeline_phase_id=metadata.get("pipeline_phase_id") or None,
        pipeline_mode=metadata.get("pipeline_mode") or None,
    )


def pipeline_plan_metadata_diagnostic(
    repo: Path,
    plan: Path,
    *,
    pipeline_required: bool = False,
) -> PipelineMetadataDiagnostic | None:
    try:
        metadata = parse_pipeline_plan_metadata(plan)
    except ValueError as exc:
        return PipelineMetadataDiagnostic(kind="invalid_pipeline_mode", message=str(exc))

    if not pipeline_required and not metadata.required:
        return None
    if not metadata.source_bundle:
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle",
            message="pipeline-required plan is missing source_bundle frontmatter",
            metadata=metadata,
        )
    if not metadata.source_bundle_sha256:
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle_sha256",
            message="pipeline-required plan is missing source_bundle_sha256 frontmatter",
            metadata=metadata,
        )

    source_bundle = Path(metadata.source_bundle).expanduser()
    if not source_bundle.is_absolute():
        source_bundle = repo / source_bundle
    if not source_bundle.exists():
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle_file",
            message="pipeline-required source_bundle does not exist",
            metadata=metadata,
            expected_sha256=metadata.source_bundle_sha256,
        )
    actual_sha256 = hashlib.sha256(source_bundle.read_bytes()).hexdigest()
    if actual_sha256 != metadata.source_bundle_sha256:
        return PipelineMetadataDiagnostic(
            kind="mismatched_source_bundle_sha256",
            message="pipeline-required source_bundle_sha256 does not match the source bundle file",
            metadata=metadata,
            expected_sha256=metadata.source_bundle_sha256,
            actual_sha256=actual_sha256,
        )
    bundle_diagnostic = phase_source_bundle_diagnostic(
        repo,
        source_bundle,
        pipeline_mode=metadata.pipeline_mode,
    )
    if bundle_diagnostic is not None:
        return bundle_diagnostic
    return None


def pipeline_execution_plan_diagnostic(
    repo: Path,
    plan: Path,
    *,
    phase: str | None = None,
    roadmap: Path | None = None,
) -> PipelineMetadataDiagnostic | None:
    try:
        metadata = parse_pipeline_plan_metadata(plan)
    except ValueError as exc:
        return PipelineMetadataDiagnostic(kind="invalid_pipeline_mode", message=str(exc))

    if metadata.empty:
        return None
    if not metadata.source_bundle:
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle",
            message="Pipeline-aware execution plan is missing source_bundle frontmatter",
            metadata=metadata,
        )
    if not metadata.source_bundle_sha256:
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle_sha256",
            message="Pipeline-aware execution plan is missing source_bundle_sha256 frontmatter",
            metadata=metadata,
        )

    source_bundle = _resolve_bundle_path(repo, metadata.source_bundle)
    if not source_bundle.exists():
        return PipelineMetadataDiagnostic(
            kind="missing_source_bundle_file",
            message="Pipeline-aware execution source_bundle does not exist",
            metadata=metadata,
            expected_sha256=metadata.source_bundle_sha256,
        )
    actual_sha256 = hashlib.sha256(source_bundle.read_bytes()).hexdigest()
    if actual_sha256 != metadata.source_bundle_sha256:
        return PipelineMetadataDiagnostic(
            kind="mismatched_source_bundle_sha256",
            message="Pipeline-aware execution source_bundle_sha256 does not match the source bundle file",
            metadata=metadata,
            expected_sha256=metadata.source_bundle_sha256,
            actual_sha256=actual_sha256,
        )
    return phase_source_bundle_diagnostic(
        repo,
        source_bundle,
        phase=phase,
        roadmap=roadmap,
        pipeline_mode=metadata.pipeline_mode,
    )


def pipeline_execution_blocker(diagnostic: PipelineMetadataDiagnostic) -> dict[str, object]:
    return {
        "human_required": diagnostic.human_required,
        "blocker_class": diagnostic.blocker_class,
        "blocker_summary": f"Pipeline execution freshness validation failed: {diagnostic.kind}",
        "required_human_inputs": (),
        "access_attempts": (),
    }


def load_execution_phase_source_bundle(
    repo: Path,
    plan: Path,
    *,
    phase: str | None = None,
    roadmap: Path | None = None,
) -> PhaseSourceBundle | None:
    try:
        metadata = parse_pipeline_plan_metadata(plan)
    except ValueError:
        return None
    if metadata.empty or not metadata.source_bundle:
        return None
    return load_phase_source_bundle(
        repo,
        metadata.source_bundle,
        phase=phase,
        roadmap=roadmap,
        pipeline_mode=metadata.pipeline_mode,
    )


def load_phase_source_bundle(
    repo: Path,
    bundle_path: str | Path | None,
    *,
    phase: str | None = None,
    roadmap: Path | None = None,
    pipeline_mode: str | None = None,
) -> PhaseSourceBundle | None:
    bundle, diagnostic = _read_phase_source_bundle(
        repo,
        bundle_path,
        phase=phase,
        roadmap=roadmap,
        pipeline_mode=pipeline_mode,
    )
    return None if diagnostic is not None else bundle


def phase_source_bundle_diagnostic(
    repo: Path,
    bundle_path: str | Path | None,
    *,
    phase: str | None = None,
    roadmap: Path | None = None,
    pipeline_mode: str | None = None,
) -> PipelineMetadataDiagnostic | None:
    _bundle, diagnostic = _read_phase_source_bundle(
        repo,
        bundle_path,
        phase=phase,
        roadmap=roadmap,
        pipeline_mode=pipeline_mode,
    )
    return diagnostic


def _read_phase_source_bundle(
    repo: Path,
    bundle_path: str | Path | None,
    *,
    phase: str | None,
    roadmap: Path | None,
    pipeline_mode: str | None,
) -> tuple[PhaseSourceBundle | None, PipelineMetadataDiagnostic | None]:
    try:
        mode = pipeline_mode or ("pipeline_optional" if bundle_path else "standalone")
        PipelinePlanMetadata(pipeline_mode=mode)
    except ValueError as exc:
        return None, PipelineMetadataDiagnostic(kind="invalid_pipeline_mode", message=str(exc))

    if not bundle_path:
        if mode == "pipeline_required":
            return None, PipelineMetadataDiagnostic(
                kind="missing_source_bundle",
                message="pipeline-required planning run is missing a source bundle",
                metadata=PipelinePlanMetadata(pipeline_mode=mode),
            )
        return None, None

    source_bundle = _resolve_bundle_path(repo, bundle_path)
    metadata = PipelinePlanMetadata(source_bundle=_relative_repo_path(repo, source_bundle), pipeline_mode=mode)
    if not source_bundle.exists():
        return None, PipelineMetadataDiagnostic(
            kind="missing_source_bundle_file",
            message="Pipeline source bundle does not exist",
            metadata=metadata,
        )
    try:
        raw = source_bundle.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message=f"Pipeline source bundle is not valid JSON: {exc}",
            metadata=metadata,
        )
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if not isinstance(data, dict):
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message="Pipeline source bundle root must be an object",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )
    if data.get("schema") != PHASE_SOURCE_BUNDLE_SCHEMA:
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message=f"Pipeline source bundle schema must be {PHASE_SOURCE_BUNDLE_SCHEMA}",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )

    required = ("phase", "roadmap", "protected_sources", "delegated_write_policy", "source_files", "artifact_target_root", "freshness")
    missing = [field for field in required if field not in data]
    if missing:
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message=f"Pipeline source bundle is missing required fields: {', '.join(missing)}",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )

    phase_data = data.get("phase")
    roadmap_data = data.get("roadmap")
    protected_data = data.get("protected_sources")
    if not isinstance(phase_data, dict) or not isinstance(roadmap_data, dict) or not isinstance(protected_data, dict):
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message="Pipeline source bundle phase, roadmap, and protected_sources fields must be objects",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )
    phase_missing = [field for field in ("phase_id", "phase_alias", "phase_plan_path") if not phase_data.get(field)]
    roadmap_missing = [field for field in ("path", "sha256") if not roadmap_data.get(field)]
    if phase_missing or roadmap_missing:
        missing_text = ", ".join([*(f"phase.{field}" for field in phase_missing), *(f"roadmap.{field}" for field in roadmap_missing)])
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message=f"Pipeline source bundle is missing required nested fields: {missing_text}",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )

    freshness = data.get("freshness") if isinstance(data.get("freshness"), dict) else {}
    expected_bundle_hash = freshness.get("source_bundle_hash")
    if isinstance(expected_bundle_hash, str) and _looks_like_sha256(expected_bundle_hash) and expected_bundle_hash != actual_sha256:
        return None, PipelineMetadataDiagnostic(
            kind="mismatched_source_bundle_sha256",
            message="Pipeline source bundle freshness.source_bundle_hash does not match the source bundle file",
            metadata=metadata,
            expected_sha256=expected_bundle_hash,
            actual_sha256=actual_sha256,
        )

    phase_id = str(phase_data["phase_id"])
    phase_alias = str(phase_data["phase_alias"])
    if _phase_is_unknown(repo, roadmap, roadmap_data, phase, phase_id, phase_alias):
        return None, PipelineMetadataDiagnostic(
            kind="unknown_phase_id",
            message=f"Pipeline source bundle phase does not match the selected roadmap phase: {phase_id}/{phase_alias}",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )

    protected_entries = protected_data.get("entries")
    if not isinstance(protected_entries, list) or not protected_entries:
        return None, PipelineMetadataDiagnostic(
            kind="missing_protected_source_entries",
            message="Pipeline source bundle protected_sources.entries must contain at least one entry",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )
    require_roles = bool(protected_data.get("requires_roles") or protected_data.get("role_required"))
    protected_sources: list[PipelineProtectedSource] = []
    for entry in protected_entries:
        if not isinstance(entry, dict):
            return None, PipelineMetadataDiagnostic(
                kind="malformed_source_bundle",
                message="Pipeline source bundle protected source entries must be objects",
                metadata=metadata,
                actual_sha256=actual_sha256,
            )
        if require_roles and not entry.get("role"):
            return None, PipelineMetadataDiagnostic(
                kind="malformed_source_bundle",
                message="Pipeline source bundle requires adoption-sensitive protected source role metadata",
                metadata=metadata,
                actual_sha256=actual_sha256,
            )
        try:
            protected_source = PipelineProtectedSource(
                path=str(entry.get("path", "")),
                category=str(entry.get("category", "")),
                sha256=str(entry["sha256"]) if entry.get("sha256") is not None else None,
                role=str(entry["role"]) if entry.get("role") is not None else None,
            )
        except ValueError as exc:
            return None, PipelineMetadataDiagnostic(
                kind="malformed_source_bundle",
                message=f"Pipeline source bundle has invalid protected source entry: {exc}",
                metadata=metadata,
                actual_sha256=actual_sha256,
            )
        if require_roles and protected_source.role not in PIPELINE_PROTECTED_SOURCE_ROLES:
            return None, PipelineMetadataDiagnostic(
                kind="malformed_source_bundle",
                message="Pipeline source bundle requires adoption-sensitive protected source role metadata",
                metadata=metadata,
                actual_sha256=actual_sha256,
            )
        protected_diagnostic = _protected_source_file_diagnostic(repo, protected_source, metadata, actual_sha256)
        if protected_diagnostic is not None:
            return None, protected_diagnostic
        protected_sources.append(protected_source)

    source_files = data.get("source_files")
    if not isinstance(source_files, list):
        return None, PipelineMetadataDiagnostic(
            kind="malformed_source_bundle",
            message="Pipeline source bundle source_files must be a list",
            metadata=metadata,
            actual_sha256=actual_sha256,
        )

    bundle = PhaseSourceBundle(
        path=_relative_repo_path(repo, source_bundle),
        sha256=actual_sha256,
        phase_id=phase_id,
        phase_alias=phase_alias,
        phase_plan_path=str(phase_data["phase_plan_path"]),
        roadmap_path=str(roadmap_data["path"]),
        roadmap_sha256=str(roadmap_data["sha256"]),
        protected_sources=tuple(protected_sources),
        delegated_write_policy=dict(data.get("delegated_write_policy") if isinstance(data.get("delegated_write_policy"), dict) else {}),
        source_files=tuple(item for item in source_files if isinstance(item, dict)),
        artifact_target_root=str(data.get("artifact_target_root") or ""),
        freshness=dict(freshness),
        pipeline_mode=mode if mode != "standalone" else "pipeline_optional",
    )
    return bundle, None


def _resolve_bundle_path(repo: Path, bundle_path: str | Path) -> Path:
    path = Path(bundle_path).expanduser()
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def _looks_like_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()))


def _phase_is_unknown(
    repo: Path,
    roadmap: Path | None,
    roadmap_data: dict[str, Any],
    selected_phase: str | None,
    phase_id: str,
    phase_alias: str,
) -> bool:
    candidates = {phase_id.upper(), phase_alias.upper()}
    if selected_phase:
        return selected_phase.upper() not in candidates
    roadmap_path = roadmap
    if roadmap_path is None:
        raw_roadmap = roadmap_data.get("path")
        if isinstance(raw_roadmap, str) and raw_roadmap:
            roadmap_path = Path(raw_roadmap).expanduser()
            if not roadmap_path.is_absolute():
                roadmap_path = repo / roadmap_path
    if roadmap_path is None or not roadmap_path.exists():
        return False
    aliases = {alias.upper() for alias in parse_roadmap_phases(roadmap_path)}
    return not bool(candidates & aliases)


def _protected_source_file_diagnostic(
    repo: Path,
    source: PipelineProtectedSource,
    metadata: PipelinePlanMetadata,
    bundle_sha256: str,
) -> PipelineMetadataDiagnostic | None:
    source_path = Path(source.path).expanduser()
    if not source_path.is_absolute():
        source_path = repo / source_path
    if not source_path.exists():
        return PipelineMetadataDiagnostic(
            kind="missing_protected_source_file",
            message=f"Pipeline protected source does not exist: {source.path}",
            metadata=metadata,
            actual_sha256=bundle_sha256,
        )
    if source.sha256 and _looks_like_sha256(source.sha256):
        actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if actual != source.sha256:
            return PipelineMetadataDiagnostic(
                kind="mismatched_protected_source_sha256",
                message=f"Pipeline protected source sha256 does not match: {source.path}",
                metadata=metadata,
                expected_sha256=source.sha256,
                actual_sha256=actual,
            )
    return None


def parse_plan_ownership(repo: Path, roadmap: Path, plan: Path | None) -> PlanOwnership:
    # plans/manifest.json is written by the runtime's own skill bundle and read
    # back for plan discovery in every repo, so the closeout classifier must
    # treat it as a control path (phase-owned) rather than an unowned dirty file.
    runtime_control = ("plans/manifest.json",)
    control_paths = (_relative_repo_path(repo, roadmap), *runtime_control)
    if plan is None:
        return PlanOwnership(owned_patterns=(), control_paths=control_paths, valid=False, errors=("missing_plan_artifact",))

    control_paths = (_relative_repo_path(repo, roadmap), _relative_repo_path(repo, plan), *runtime_control)
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return PlanOwnership(owned_patterns=(), control_paths=control_paths, valid=False, errors=("plan_unreadable",))

    from .plan_ir import parse_phase_plan_ir

    lane_ir = parse_phase_plan_ir(plan)
    if lane_ir.lanes:
        errors = []
        for diagnostic in lane_ir.diagnostics:
            if diagnostic.kind in {"missing_owned_files", "malformed_owned_files"}:
                errors.append(f"{diagnostic.kind}:{diagnostic.details.get('heading', diagnostic.lane_id or 'plan')}")
            else:
                errors.append(f"{diagnostic.kind}:{diagnostic.lane_id or 'plan'}")
        return PlanOwnership(
            owned_patterns=tuple(
                dict.fromkeys(
                    pattern
                    for lane in lane_ir.lanes
                    if not lane.read_only
                    for pattern in lane.owned_files
                )
            ),
            control_paths=control_paths,
            valid=lane_ir.valid,
            errors=tuple(errors),
        )

    lane_starts = list(LANE_SECTION_RE.finditer(text))
    if not lane_starts:
        return PlanOwnership(owned_patterns=(), control_paths=control_paths, valid=False, errors=("missing_lane_sections",))

    owned_patterns: list[str] = []
    errors: list[str] = []
    for index, lane_start in enumerate(lane_starts):
        end = lane_starts[index + 1].start() if index + 1 < len(lane_starts) else len(text)
        section = text[lane_start.start() : end]
        raw = _owned_files_text(section)
        if raw is None:
            errors.append(f"missing_owned_files:{lane_start.group(0).strip()}")
            continue
        normalized = raw.strip("`").strip().lower()
        if normalized.startswith("none") or normalized.startswith("(none"):
            continue
        entries = [item.strip() for item in re.findall(r"`([^`]+)`", raw)]
        if not entries:
            errors.append(f"malformed_owned_files:{lane_start.group(0).strip()}")
            continue
        for entry in entries:
            if entry:
                owned_patterns.append(entry)

    return PlanOwnership(
        owned_patterns=tuple(dict.fromkeys(owned_patterns)),
        control_paths=control_paths,
        valid=not errors,
        errors=tuple(errors),
    )


def classify_phase_team_eligibility(repo: Path, roadmap: Path, plan: Path | None) -> PhaseTeamEligibility:
    ownership = parse_plan_ownership(repo, roadmap, plan)
    if plan is None:
        return PhaseTeamEligibility(
            allowed_execution_modes=("solo",),
            default_execution_mode="solo",
            eligible_for_native_team=False,
            has_disjoint_write_lanes=False,
            has_only_read_only_lanes=False,
            unmanaged_write_risk=True,
            reason="missing_phase_plan",
            invalid_reasons=("missing_phase_plan",),
        )
    from .plan_ir import parse_phase_plan_ir

    lane_ir = parse_phase_plan_ir(plan) if plan is not None else None
    if (
        lane_ir is not None
        and lane_ir.lanes
        and lane_ir.diagnostics
        and all(diagnostic.kind == "overlapping_write_ownership" for diagnostic in lane_ir.diagnostics)
    ):
        lanes = tuple(
            PlanLane(
                heading=lane.heading,
                owned_patterns=lane.owned_files,
                read_only=lane.read_only,
                text="",
            )
            for lane in lane_ir.lanes
        )
        write_lanes = [lane for lane in lanes if not lane.read_only]
        overlaps = _overlapping_lane_pairs(write_lanes)
        return PhaseTeamEligibility(
            allowed_execution_modes=("solo",),
            default_execution_mode="solo",
            eligible_for_native_team=False,
            has_disjoint_write_lanes=False,
            has_only_read_only_lanes=False,
            unmanaged_write_risk=True,
            reason="overlapping_owned_files",
            invalid_reasons=tuple(f"overlap:{left}<->{right}" for left, right in overlaps),
            lane_summaries=tuple(
                {
                    "heading": lane.heading,
                    "read_only": lane.read_only,
                    "owned_patterns": list(lane.owned_patterns),
                }
                for lane in lanes
            ),
        )

    if not ownership.valid:
        return PhaseTeamEligibility(
            allowed_execution_modes=("solo",),
            default_execution_mode="solo",
            eligible_for_native_team=False,
            has_disjoint_write_lanes=False,
            has_only_read_only_lanes=False,
            unmanaged_write_risk=True,
            reason="invalid_owned_files_contract",
            invalid_reasons=ownership.errors,
        )

    if lane_ir is not None and lane_ir.lanes:
        lanes = tuple(
            PlanLane(
                heading=lane.heading,
                owned_patterns=lane.owned_files,
                read_only=lane.read_only,
                text="",
            )
            for lane in lane_ir.lanes
        )
    else:
        lanes = _parse_plan_lanes(plan)
    if not lanes:
        return PhaseTeamEligibility(
            allowed_execution_modes=("solo",),
            default_execution_mode="solo",
            eligible_for_native_team=False,
            has_disjoint_write_lanes=False,
            has_only_read_only_lanes=False,
            unmanaged_write_risk=True,
            reason="missing_lane_sections",
            invalid_reasons=("missing_lane_sections",),
        )

    write_lanes = [lane for lane in lanes if not lane.read_only]
    read_only_lanes = [lane for lane in lanes if lane.read_only]
    overlaps = _overlapping_lane_pairs(write_lanes)
    unmanaged_write_risk = bool(overlaps)
    has_disjoint_write_lanes = bool(write_lanes) and not unmanaged_write_risk
    has_only_read_only_lanes = bool(lanes) and len(read_only_lanes) == len(lanes)
    eligible = has_only_read_only_lanes or has_disjoint_write_lanes
    allowed_modes = ("solo", "subagent", "agent_team") if eligible else ("solo",)
    reason = (
        "read_only_lanes_only"
        if has_only_read_only_lanes
        else "disjoint_write_lanes"
        if has_disjoint_write_lanes
        else "overlapping_owned_files"
    )
    invalid_reasons = tuple(f"overlap:{left}<->{right}" for left, right in overlaps)
    lane_summaries = tuple(
        {
            "heading": lane.heading,
            "read_only": lane.read_only,
            "owned_patterns": list(lane.owned_patterns),
        }
        for lane in lanes
    )
    return PhaseTeamEligibility(
        allowed_execution_modes=allowed_modes,
        default_execution_mode="solo",
        eligible_for_native_team=eligible,
        has_disjoint_write_lanes=has_disjoint_write_lanes,
        has_only_read_only_lanes=has_only_read_only_lanes,
        unmanaged_write_risk=unmanaged_write_risk,
        reason=reason,
        invalid_reasons=invalid_reasons,
        lane_summaries=lane_summaries,
    )


def _owned_files_text(section: str) -> str | None:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        owned_match = re.match(r"^\s*(?:-\s+)?\*\*Owned files\*\*:\s*(.+?)\s*$", line, re.IGNORECASE)
        if not owned_match:
            continue
        parts = [owned_match.group(1).strip()]
        for continuation in lines[index + 1 :]:
            stripped = continuation.strip()
            if not stripped:
                break
            if re.match(r"^(?:-\s+)?\*\*[^*]+\*\*:", stripped) or stripped.startswith("#"):
                break
            if "`" not in stripped:
                break
            parts.append(stripped)
        return " ".join(parts)
    return None


def _parse_plan_lanes(plan: Path) -> tuple[PlanLane, ...]:
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return ()
    lane_starts = list(LANE_SECTION_RE.finditer(text))
    lanes: list[PlanLane] = []
    for index, lane_start in enumerate(lane_starts):
        end = lane_starts[index + 1].start() if index + 1 < len(lane_starts) else len(text)
        section = text[lane_start.start() : end]
        raw = _owned_files_text(section)
        if raw is None:
            continue
        normalized = raw.strip().lower()
        if normalized.startswith("none"):
            lanes.append(
                PlanLane(
                    heading=lane_start.group(0).strip(),
                    owned_patterns=(),
                    read_only=True,
                    text=section,
                )
            )
            continue
        entries = tuple(item.strip() for item in re.findall(r"`([^`]+)`", raw) if item.strip())
        if not entries:
            continue
        lanes.append(
            PlanLane(
                heading=lane_start.group(0).strip(),
                owned_patterns=entries,
                read_only=False,
                text=section,
            )
        )
    return tuple(lanes)


def _overlapping_lane_pairs(lanes: list[PlanLane]) -> tuple[tuple[str, str], ...]:
    overlaps: list[tuple[str, str]] = []
    for index, left in enumerate(lanes):
        for right in lanes[index + 1 :]:
            if _lane_patterns_overlap(left.owned_patterns, right.owned_patterns):
                overlaps.append((left.heading, right.heading))
    return tuple(overlaps)


def _lane_patterns_overlap(left_patterns: tuple[str, ...], right_patterns: tuple[str, ...]) -> bool:
    for left in left_patterns:
        for right in right_patterns:
            if _patterns_overlap(left, right):
                return True
    return False


def _patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_has_glob = _has_glob(left)
    right_has_glob = _has_glob(right)
    if not left_has_glob and fnmatchcase(left, right):
        return True
    if not right_has_glob and fnmatchcase(right, left):
        return True
    if left_has_glob and right_has_glob:
        left_prefix = _pattern_prefix(left)
        right_prefix = _pattern_prefix(right)
        return bool(left_prefix and right_prefix and (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)))
    return False


def _owned_pattern_matches(repo_path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return repo_path.startswith(pattern)
    if "*" not in pattern and "?" not in pattern:
        return False
    return fnmatchcase(repo_path, pattern)


def expanded_dirty_ownership_matches(ownership: PlanOwnership, repo_path: str) -> bool:
    if not _valid_relative_posix_path(repo_path):
        return False
    return any(_dirty_output_matches_owned_pattern(repo_path, pattern) for pattern in ownership.owned_patterns)


def _dirty_output_matches_owned_pattern(repo_path: str, pattern: str) -> bool:
    if not _valid_relative_posix_path(pattern):
        return False
    return _matches_test_or_fixture_sibling(repo_path, pattern) or _matches_vendor_module_test(repo_path, pattern)


def _valid_relative_posix_path(repo_path: str) -> bool:
    path = PurePosixPath(repo_path)
    return bool(repo_path) and not path.is_absolute() and ".." not in path.parts


def _matches_test_or_fixture_sibling(repo_path: str, pattern: str) -> bool:
    if pattern.endswith("/") or _has_glob(pattern):
        return False
    owned = PurePosixPath(pattern)
    target = PurePosixPath(repo_path)
    if not owned.suffix or target.parent.parent != owned.parent:
        return False
    if target.parent.name == "__tests__":
        return target.name in {f"{owned.stem}.test{owned.suffix}", f"{owned.stem}.spec{owned.suffix}"}
    if target.parent.name == "__fixtures__":
        return target.name.startswith(f"{owned.stem}.")
    return False


def _matches_vendor_module_test(repo_path: str, pattern: str) -> bool:
    owned = PurePosixPath(pattern)
    target = PurePosixPath(repo_path)
    if len(owned.parts) < 3 or owned.parts[0] != "vendor" or owned.parts[2] != "src":
        return False
    return (
        len(target.parts) == 4
        and target.parts[0] == "vendor"
        and target.parts[1] == owned.parts[1]
        and target.parts[2] == "tests"
        and target.parts[3].startswith("test_")
        and target.suffix == ".py"
    )


def _has_glob(pattern: str) -> bool:
    return any(token in pattern for token in ("*", "?", "["))


def _pattern_prefix(pattern: str) -> str:
    match = re.match(r"^[^*?\[]+", pattern)
    return match.group(0).rstrip("/") if match else ""


def handoff_matches_roadmap(repo: Path, phase: str, roadmap: Path, handoff: dict[str, str] | None) -> bool:
    if not handoff:
        return False
    artifact = handoff.get("artifact")
    if not artifact:
        return False
    artifact_path = Path(artifact).expanduser().resolve()
    try:
        artifact_path.relative_to(repo.resolve())
    except ValueError:
        return False
    if not artifact_path.exists() or PLAN_RE.search(artifact_path.name) is None:
        return False
    return plan_matches_roadmap(repo, artifact_path, roadmap, phase)


def list_plan_artifacts(repo: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for plan in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
        match = PLAN_RE.search(plan.name)
        if match:
            result[match.group(2).upper()] = plan.resolve()
    return result


def latest_skill_handoff(identity: RepoIdentity, skill: str) -> dict[str, str] | None:
    path = Path.home() / ".codex" / "skills" / skill / "handoffs" / identity.repo_hash / identity.branch_slug / "latest.md"
    if not path.exists():
        return None
    text = path.read_text()
    frontmatter = parse_frontmatter(text)
    if frontmatter.get("from") != skill:
        return None
    if frontmatter.get("repo") != identity.repo_hash:
        return None
    if Path(frontmatter.get("repo_root", "")).expanduser().resolve() != identity.root:
        return None
    if frontmatter.get("branch_slug") != identity.branch_slug:
        return None
    if frontmatter.get("branch") != identity.branch:
        return None
    if not frontmatter.get("commit"):
        return None
    artifact = frontmatter.get("artifact")
    if not artifact:
        return None
    artifact_path = Path(artifact).expanduser().resolve()
    try:
        artifact_path.relative_to(identity.root)
    except ValueError:
        return None
    if not artifact_path.exists():
        return None
    automation = parse_automation_status(text)
    return {
        **frontmatter,
        **automation,
        "workflow_skill": skill,
        "originating_harness": skill.split("-", 1)[0],
    }


def latest_workflow_handoff(
    identity: RepoIdentity,
    repo: Path,
    roadmap: Path,
    skills: Iterable[str],
) -> dict[str, str] | None:
    candidates: list[tuple[str, int, dict[str, str]]] = []
    for index, skill in enumerate(skills):
        handoff = latest_skill_handoff(identity, skill)
        if not handoff:
            continue
        artifact = handoff.get("artifact")
        if not artifact:
            continue
        artifact_path = Path(artifact).expanduser().resolve()
        if not plan_matches_roadmap(repo, artifact_path, roadmap):
            continue
        candidates.append((handoff.get("timestamp", ""), -index, handoff))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]


def parse_automation_status(text: str) -> dict[str, object]:
    text = _last_automation_block(text)
    values: dict[str, object] = {}
    for key in ("status", "next_skill", "next_command", "human_required", "blocker_class", "blocker_summary", "verification_status"):
        match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
        if match:
            values[f"automation_{key}"] = match.group(1).strip().strip("'\"")
    list_match = re.search(r"^\s*required_human_inputs:\s*(?:\n(?P<items>(?:\s+-\s+.+\n?)+)|(?P<inline>\[\]))", text, re.MULTILINE)
    if list_match:
        if list_match.group("inline") == "[]":
            values["automation_required_human_inputs"] = []
        else:
            items = []
            for line in (list_match.group("items") or "").splitlines():
                item = re.sub(r"^\s+-\s+", "", line).strip().strip("'\"")
                if item:
                    items.append(item)
            values["automation_required_human_inputs"] = items
    return values


def _last_automation_block(text: str) -> str:
    normalized_marker = "Normalized shared automation closeout:"
    normalized_index = text.rfind(normalized_marker)
    if normalized_index >= 0:
        original_index = text.find("\n\nOriginal child output:", normalized_index)
        if original_index >= 0:
            text = text[normalized_index:original_index]
        else:
            text = text[normalized_index:]
    matches = list(re.finditer(r"(?m)^(?P<indent>[ \t]*)automation:\s*$", text))
    if not matches:
        return text
    match = matches[-1]
    base_indent = len(match.group("indent"))
    selected: list[str] = []
    for index, line in enumerate(text[match.start() :].splitlines()):
        if index == 0 or not line.strip():
            selected.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent > base_indent:
            selected.append(line)
            continue
        break
    return "\n".join(selected)


def phase_after(phases: Iterable[str], current: str) -> str | None:
    items = list(phases)
    for index, phase in enumerate(items):
        if phase.lower() == current.lower() and index + 1 < len(items):
            return items[index + 1]
    return None


def _collect_dispatch_lines(section: str, buckets: dict[str, dict[str, list[str]]], *, default_key: str) -> None:
    for match in DISPATCH_LINE_RE.finditer(section):
        action = (match.group("action") or default_key).strip().lower()
        if action != "default":
            _validate_action(action)
        label = match.group("label").strip().lower().replace(" ", "_")
        values = _parse_dispatch_values(match.group("value"))
        bucket = buckets.setdefault(action, {})
        current = bucket.setdefault(label, [])
        for value in values:
            if value not in current:
                current.append(value)


def _parse_dispatch_values(raw: str) -> list[str]:
    quoted = re.findall(r"`([^`]+)`", raw)
    values = quoted or [part.strip() for part in raw.split(",")]
    cleaned = [value.strip().lower() for value in values if value.strip()]
    return cleaned


def _validate_action(action: str) -> None:
    if action not in PRODUCT_LOOP_ACTIONS:
        raise ValueError(f"invalid dispatch hint action: {action}")


def _relative_repo_path(repo: Path, path: Path) -> str:
    resolved_repo = repo.resolve()
    resolved_path = path.resolve()
    try:
        return str(resolved_path.relative_to(resolved_repo))
    except ValueError:
        return str(resolved_path)
