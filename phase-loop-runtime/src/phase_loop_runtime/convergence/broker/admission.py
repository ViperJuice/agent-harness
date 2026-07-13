"""Durable, metadata-only broker admission ordering."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from phase_loop_runtime.convergence.contracts import AdmissionRequest


@dataclass(frozen=True)
class AdmissionRecord:
    sequence: int
    epoch: int
    request: AdmissionRequest


BrokerAdmissionPolicy = Callable[[AdmissionRequest], bool]


class LinearizableAdmissionStore:
    """Append-only admission log guarded by an OS advisory lock."""
    def __init__(self, root: Path, policy: BrokerAdmissionPolicy | None = None, epoch_blocked: Callable[[], bool] | None = None) -> None:
        self.root, self.policy, self.epoch_blocked = root, policy, epoch_blocked or (lambda: False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path, self.lock_path = root / "admissions.jsonl", root / "admissions.lock"

    def _records(self) -> list[AdmissionRecord]:
        if not self.path.exists(): return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            raw = json.loads(line); raw["request"] = AdmissionRequest(**raw["request"]); records.append(AdmissionRecord(**raw))
        return records

    def admit(self, request: AdmissionRequest) -> AdmissionRecord:
        import fcntl
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.epoch_blocked() or self.policy is None or not self.policy(request):
                    raise PermissionError("broker admission denied")
                records = self._records()
                for record in records:
                    if record.request.idempotency_key == request.idempotency_key:
                        if record.request != request: raise ValueError("conflicting idempotency key")
                        return record
                if records and request.lease_epoch < max(r.epoch for r in records): raise PermissionError("stale epoch")
                record = AdmissionRecord(len(records) + 1, request.lease_epoch, request)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(record), sort_keys=True) + "\n"); stream.flush(); os.fsync(stream.fileno())
                return record
            finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def replay(self) -> tuple[AdmissionRecord, ...]: return tuple(self._records())
