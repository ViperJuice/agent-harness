"""Append-only terminal evidence for broker operations."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from phase_loop_runtime.convergence.provider_contracts import TerminalOutcomeState, validate_terminal_transition


@dataclass(frozen=True)
class EvidenceRecord:
    idempotency_key: str
    state: TerminalOutcomeState
    evidence_reference: str = ""


class BrokerEvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = root; root.mkdir(parents=True, exist_ok=True); self.path = root / "evidence.jsonl"
    def replay(self) -> dict[str, EvidenceRecord]:
        result: dict[str, EvidenceRecord] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                raw = json.loads(line); raw["state"] = TerminalOutcomeState(raw["state"]); result[raw["idempotency_key"]] = EvidenceRecord(**raw)
        return result
    @property
    def epoch_blocked(self) -> bool:
        return any(r.state is TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED for r in self.replay().values())
    def record_intent(self, key: str) -> EvidenceRecord:
        current = self.replay().get(key)
        if current: return current
        return self._append(EvidenceRecord(key, TerminalOutcomeState.PROVIDER_CALL_IN_FLIGHT))
    def record_terminal(self, record: EvidenceRecord, *, pre_linearization_proven: bool = False) -> EvidenceRecord:
        current = self.replay().get(record.idempotency_key)
        if current is None: raise ValueError("intent must precede evidence")
        if current == record: return current
        if not validate_terminal_transition(current.state, record.state, pre_linearization_proven=pre_linearization_proven): raise ValueError("invalid terminal transition")
        return self._append(record)
    def rejected_before_start(self, key: str, evidence_reference: str) -> EvidenceRecord:
        return self._append(EvidenceRecord(key, TerminalOutcomeState.REJECTED_BEFORE_START, evidence_reference))
    def _append(self, record: EvidenceRecord) -> EvidenceRecord:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record), sort_keys=True) + "\n"); stream.flush(); os.fsync(stream.fileno())
        return record
