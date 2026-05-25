from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


MERGE_POLICY_ON_PASS = ("required", "auto", "never")


class MergePolicyParseError(ValueError):
    pass


@dataclass(frozen=True)
class MergePolicy:
    on_pass: str = "required"
    approvers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.on_pass not in MERGE_POLICY_ON_PASS:
            raise MergePolicyParseError(f"invalid merge_policy.on_pass: {self.on_pass}")
        normalized = tuple(str(approver).strip() for approver in self.approvers if str(approver).strip())
        object.__setattr__(self, "approvers", normalized)

    def to_json(self) -> dict[str, object]:
        return {"on_pass": self.on_pass, "approvers": list(self.approvers)}


def parse(phase_block: dict) -> MergePolicy:
    if "merge_policy" not in phase_block or phase_block.get("merge_policy") in (None, ""):
        return MergePolicy(on_pass="required", approvers=())

    raw = _coerce_policy_block(phase_block["merge_policy"])
    on_pass = str(raw.get("on_pass") or "required").strip()
    if on_pass not in MERGE_POLICY_ON_PASS:
        raise MergePolicyParseError(f"invalid merge_policy.on_pass: {on_pass}")

    approvers = _approver_tuple(raw.get("approvers"))
    if on_pass == "required" and not approvers:
        raise MergePolicyParseError("explicit merge_policy.on_pass=required requires at least one approver")
    return MergePolicy(on_pass=on_pass, approvers=approvers)


def _coerce_policy_block(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        if stripped in MERGE_POLICY_ON_PASS:
            return {"on_pass": stripped}
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise MergePolicyParseError(f"invalid merge_policy JSON: {exc.msg}") from exc
        if isinstance(decoded, dict):
            return decoded
    raise MergePolicyParseError("merge_policy must be an object or JSON object string")


def _approver_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise MergePolicyParseError("merge_policy.approvers must be a list, tuple, or comma-separated string")
    return tuple(str(item).strip() for item in items if str(item).strip())
