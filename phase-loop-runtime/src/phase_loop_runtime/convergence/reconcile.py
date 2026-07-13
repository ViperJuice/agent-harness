"""Read-only exact-state reconciliation for recovered convergence events."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping

from .contracts import AuthoritySource, InvalidationTrigger, ReconciliationBinding
from .event_log import RecoveredTrainState


@dataclass(frozen=True)
class ExactStateProbes:
    git: Callable[[RecoveredTrainState], Mapping[str, str] | None] | None = None
    github: Callable[[RecoveredTrainState], Mapping[str, str] | None] | None = None
    provider: Callable[[RecoveredTrainState], Mapping[str, str] | None] | None = None
    registry: Callable[[RecoveredTrainState], Mapping[str, str] | None] | None = None


@dataclass(frozen=True)
class ReconciliationVerdict:
    binding: ReconciliationBinding
    observations: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    blocker_reason: str | None = None
    checked_at: str = ""

    @property
    def valid(self) -> bool:
        return self.blocker_reason is None and not self.binding.invalidation_triggers


@dataclass(frozen=True)
class SupportedConvergenceVersions:
    event_schema_version: str = "1"
    transition_model_version: str = "1"
    invalidation_model_version: str = "1"


@dataclass(frozen=True)
class ActionReconciliation:
    action: str
    verdict: ReconciliationVerdict
    verification_valid: bool
    approval_valid: bool

    @property
    def admitted(self) -> bool:
        return self.verdict.valid and self.verification_valid and self.approval_valid


def reconcile_train_state(state: RecoveredTrainState, probes: ExactStateProbes) -> ReconciliationVerdict:
    checked_at = datetime.now(timezone.utc).isoformat()
    if state.ambiguities or state.pending_attempts:
        return _blocked("event_log", state.ambiguities[0] if state.ambiguities else "pending attempt", checked_at)
    required = {"git": probes.git, "github": probes.github, "provider": probes.provider, "registry": probes.registry}
    observations: dict[str, Mapping[str, str]] = {}
    for name, probe in required.items():
        if probe is None:
            return _blocked("event_log", f"required {name} authority unavailable", checked_at)
        value = probe(state)
        if value is None:
            return _blocked("event_log", f"required {name} authority unavailable", checked_at)
        observations[name] = value
    triggers: list[InvalidationTrigger] = []
    for observation in observations.values():
        for key, trigger in (("head_changed", InvalidationTrigger.EFFECTIVE_CODE_CHANGED), ("roadmap_changed", InvalidationTrigger.ROADMAP_CHANGED), ("base_changed", InvalidationTrigger.BASE_SHA_CHANGED), ("dependency_changed", InvalidationTrigger.DEPENDENCY_SHA_CHANGED), ("verification_plan_changed", InvalidationTrigger.VERIFICATION_PLAN_DIGEST_CHANGED)):
            if str(observation.get(key, "")).lower() == "true":
                triggers.append(trigger)
    authority = AuthoritySource.REGISTRY_MANIFEST if observations["registry"].get("released_identity") else AuthoritySource.GIT_HEAD
    binding = ReconciliationBinding(authority, "1", "1", tuple(dict.fromkeys(triggers)))
    return ReconciliationVerdict(binding, observations, "state invalidated" if triggers else None, checked_at)


def invalidate_action_evidence(state: RecoveredTrainState, verdict: ReconciliationVerdict) -> ActionReconciliation:
    invalid = bool(verdict.binding.invalidation_triggers)
    return ActionReconciliation("", verdict, state.verification_valid and not invalid, state.approval_valid and not invalid)


def reconcile_before_action(state: RecoveredTrainState, probes: ExactStateProbes, action: str, *, supported_versions: SupportedConvergenceVersions = SupportedConvergenceVersions()) -> ActionReconciliation:
    versions = (supported_versions.event_schema_version, supported_versions.transition_model_version, supported_versions.invalidation_model_version)
    if state.ambiguities or not state.train_id:
        verdict = reconcile_train_state(state, probes)
        return ActionReconciliation(action, verdict, False, False)
    verdict = reconcile_train_state(state, probes)
    if (verdict.binding.authority_version, verdict.binding.invalidation_model_version) != (versions[0], versions[2]):
        verdict = ReconciliationVerdict(verdict.binding, verdict.observations, "unsupported convergence version", verdict.checked_at)
    invalidated = invalidate_action_evidence(state, verdict)
    return ActionReconciliation(action, verdict, invalidated.verification_valid, invalidated.approval_valid)


def _blocked(authority: str, reason: str, checked_at: str) -> ReconciliationVerdict:
    return ReconciliationVerdict(ReconciliationBinding(AuthoritySource.EVENT_LOG, "1", "1"), {}, reason, checked_at)
