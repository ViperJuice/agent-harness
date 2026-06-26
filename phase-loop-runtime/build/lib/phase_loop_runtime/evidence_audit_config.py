from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - only used in stripped installs
    yaml = None


DEFAULT_TIER3_CONFIDENCE_THRESHOLD = 0.85


class EvidenceAuditConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceAuditPhaseConfig:
    tier2_enabled: bool = True
    tier3_enabled: bool = False
    tier3_confidence_threshold: float = DEFAULT_TIER3_CONFIDENCE_THRESHOLD
    disable_detectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceAuditConfig:
    tier2_enabled: bool = True
    tier3_enabled: bool = False
    tier3_confidence_threshold: float = DEFAULT_TIER3_CONFIDENCE_THRESHOLD
    phase_aliases_exclude_tier3: tuple[str, ...] = ()
    phases: dict[str, EvidenceAuditPhaseConfig] = field(default_factory=dict)

    def phase_config(self, phase_alias: str) -> EvidenceAuditPhaseConfig:
        alias = phase_alias.strip().upper()
        override = self.phases.get(alias)
        if override is not None:
            return override
        return EvidenceAuditPhaseConfig(
            tier2_enabled=self.tier2_enabled,
            tier3_enabled=self.tier3_enabled,
            tier3_confidence_threshold=self.tier3_confidence_threshold,
        )

    def tier3_excluded(self, phase_alias: str) -> bool:
        return phase_alias.strip().upper() in set(self.phase_aliases_exclude_tier3)


def default_config_path(repo: Path) -> Path:
    return repo / ".phase-loop" / "evidence-audit.yaml"


def load_evidence_audit_config(repo: Path, path: Path | None = None) -> EvidenceAuditConfig:
    config_path = path or default_config_path(repo)
    if not config_path.exists():
        return EvidenceAuditConfig()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceAuditConfigError(f"cannot read evidence audit config: {exc}") from exc
    if not text.strip():
        return EvidenceAuditConfig()
    if yaml is None:
        raise EvidenceAuditConfigError(
            "PyYAML is required to read evidence-audit.yaml; run through scripts/phase-loop-python "
            "or install phase-loop-runtime dependencies"
        )
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:
        raise EvidenceAuditConfigError(f"malformed evidence audit config: {exc}") from exc
    if raw is None:
        return EvidenceAuditConfig()
    if not isinstance(raw, dict):
        raise EvidenceAuditConfigError("evidence audit config must be a mapping")
    allowed = {
        "tier2_enabled",
        "tier3_enabled",
        "tier3_confidence_threshold",
        "phase_aliases_exclude_tier3",
        "phases",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise EvidenceAuditConfigError(f"unknown evidence audit config fields: {', '.join(unknown)}")
    return EvidenceAuditConfig(
        tier2_enabled=_bool(raw.get("tier2_enabled", True), "tier2_enabled"),
        tier3_enabled=_bool(raw.get("tier3_enabled", False), "tier3_enabled"),
        tier3_confidence_threshold=_threshold(
            raw.get("tier3_confidence_threshold", DEFAULT_TIER3_CONFIDENCE_THRESHOLD),
            "tier3_confidence_threshold",
        ),
        phase_aliases_exclude_tier3=_aliases(raw.get("phase_aliases_exclude_tier3", ())),
        phases=_phases(raw.get("phases", {}), raw),
    )


def _bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceAuditConfigError(f"{field_name} must be a bool")
    return value


def _threshold(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidenceAuditConfigError(f"{field_name} must be a number")
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise EvidenceAuditConfigError(f"{field_name} must be between 0.0 and 1.0")
    return value


def _aliases(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise EvidenceAuditConfigError("phase_aliases_exclude_tier3 must be a list")
    aliases: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvidenceAuditConfigError("phase_aliases_exclude_tier3 entries must be non-empty strings")
        aliases.append(item.strip().upper())
    return tuple(aliases)


def _detectors(value: Any, field_name: str = "disable_detectors") -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise EvidenceAuditConfigError(f"{field_name} must be a list")
    detectors: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvidenceAuditConfigError(f"{field_name} entries must be non-empty strings")
        detectors.append(item.strip())
    return tuple(detectors)


def _phases(value: Any, root: dict[str, Any]) -> dict[str, EvidenceAuditPhaseConfig]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise EvidenceAuditConfigError("phases must be a mapping")
    phases: dict[str, EvidenceAuditPhaseConfig] = {}
    default_tier2 = _bool(root.get("tier2_enabled", True), "tier2_enabled")
    default_tier3 = _bool(root.get("tier3_enabled", False), "tier3_enabled")
    default_threshold = _threshold(
        root.get("tier3_confidence_threshold", DEFAULT_TIER3_CONFIDENCE_THRESHOLD),
        "tier3_confidence_threshold",
    )
    allowed = {"tier2_enabled", "tier3_enabled", "tier3_confidence_threshold", "disable_detectors"}
    for alias, raw_phase in value.items():
        if not isinstance(alias, str) or not alias.strip():
            raise EvidenceAuditConfigError("phase aliases must be non-empty strings")
        if not isinstance(raw_phase, dict):
            raise EvidenceAuditConfigError(f"phase {alias} config must be a mapping")
        unknown = sorted(set(raw_phase) - allowed)
        if unknown:
            raise EvidenceAuditConfigError(f"unknown fields for phase {alias}: {', '.join(unknown)}")
        phases[alias.strip().upper()] = EvidenceAuditPhaseConfig(
            tier2_enabled=_bool(raw_phase.get("tier2_enabled", default_tier2), f"phases.{alias}.tier2_enabled"),
            tier3_enabled=_bool(raw_phase.get("tier3_enabled", default_tier3), f"phases.{alias}.tier3_enabled"),
            tier3_confidence_threshold=_threshold(
                raw_phase.get("tier3_confidence_threshold", default_threshold),
                f"phases.{alias}.tier3_confidence_threshold",
            ),
            disable_detectors=_detectors(raw_phase.get("disable_detectors", ()), f"phases.{alias}.disable_detectors"),
        )
    return phases
