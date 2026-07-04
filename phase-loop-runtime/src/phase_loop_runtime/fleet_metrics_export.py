"""CS-2.1 SA — the one-way SANITIZED export bridge (enforcement → Portal).

This is the PUSH side of the ``.phase-loop`` wall. Enforcement computes the
ledger-faithful aggregate series (``fleet_metrics.derive_fleet_metric_series``)
and pushes ONLY sanitized aggregates + timestamps to Portal's control-plane
ingest. Portal never reaches back into ``.phase-loop`` — the direction is
strictly enforcement → Portal.

Sanitization is PROVABLE, not incidental: :func:`build_sanitized_export` runs
every payload through :func:`assert_sanitized`, which fails closed if any leaf
value looks like a filesystem path, a ``.phase-loop`` / ``events.jsonl``
reference, a secret, or a raw session payload. The named metrics only ever carry
numbers, ISO-8601 timestamps, and a fixed set of enum labels.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from .fleet_metrics import derive_fleet_metric_series, read_fleet_metrics


EXPORT_SCHEMA = "fleet_metrics_export.v1"

# The complete set of NAMED ledger-faithful series this bridge is allowed to
# push. Matches the series_kinds the CS-2.1 spine reserved for `ledger-faithful`.
NAMED_SERIES = ("velocity", "burn_down", "promise_broken_duration")

# A repo id is a control-plane LABEL, never a path — enforce a slug shape.
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# ISO-8601 UTC timestamps are the only strings allowed as leaf *values* (besides
# the fixed enum labels), so a stray path can never masquerade as a timestamp.
_ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

# Substrings that must NEVER appear anywhere in the payload — the wall markers
# plus common secret/path tells. A hit is a hard sanitization failure.
_FORBIDDEN_SUBSTRINGS = (
    "/",
    "\\",
    ".phase-loop",
    "events.jsonl",
    "state.json",
    "secret",
    "token",
    "api_key",
    "authorization",
    "password",
    "session",
    "prompt",
)

# Keys allowed to appear in the payload tree. Anything else (e.g. a raw "path",
# "gate", "payload", "command" field) is a sanitization failure — this is what
# stops raw ledger internals from leaking.
_ALLOWED_KEYS = frozenset(
    {
        "schema",
        "repo",
        "captured_at",
        "series",
        "series_kind",
        "provenance",
        "points",
        "aggregate",
        # numeric/label leaves inside points + aggregate:
        "t",
        "completed_total",
        "total",
        "completed",
        "remaining",
        "broken_seconds",
        "repaired",
        "open_count",
        "max_open_seconds",
        "mean_repaired_seconds",
        "repaired_count",
    }
)


class SanitizationError(ValueError):
    """Raised when an export payload carries a forbidden path/secret/payload field."""


def build_export_payload(repo_id: str, series: dict[str, Any], captured_at: str) -> dict[str, Any]:
    """Shape the derived series into the ``fleet_metrics_export.v1`` wire payload.

    Every pushed series is ``provenance: ledger-faithful`` — the DB CHECK on the
    Portal side binds these three series_kinds to exactly that provenance.
    """
    if not _REPO_SLUG_RE.match(repo_id):
        raise SanitizationError(f"repo id is not a control-plane slug: {repo_id!r}")
    return {
        "schema": EXPORT_SCHEMA,
        "repo": repo_id,
        "captured_at": captured_at,
        "series": [
            {
                "series_kind": name,
                "provenance": "ledger-faithful",
                **series.get(name, {}),
            }
            for name in NAMED_SERIES
        ],
    }


def build_sanitized_export(repo: Path, *, repo_id: str, captured_at: str) -> dict[str, Any]:
    """Read the enforcement ledger, derive the series, and return a proven-sanitized payload."""
    series = derive_fleet_metric_series(read_fleet_metrics(repo), now=captured_at)
    payload = build_export_payload(repo_id, series, captured_at)
    assert_sanitized(payload)
    return payload


def assert_sanitized(payload: Any, *, _path: str = "$") -> None:
    """Fail closed unless every node is a numeric aggregate, ISO timestamp, or fixed label.

    Walks the whole tree:
      * dict keys must be in ``_ALLOWED_KEYS``;
      * every string value must be an allowed enum label or a strict ISO-8601
        timestamp — and must contain no forbidden substring;
      * numbers/bools/None pass; nested lists/dicts recurse.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key not in _ALLOWED_KEYS:
                raise SanitizationError(f"forbidden key at {_path}.{key}")
            assert_sanitized(value, _path=f"{_path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            assert_sanitized(item, _path=f"{_path}[{i}]")
        return
    if isinstance(payload, bool) or payload is None:
        return
    if isinstance(payload, (int, float)):
        return
    if isinstance(payload, str):
        lowered = payload.lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            if bad in lowered:
                raise SanitizationError(f"forbidden substring {bad!r} at {_path}: {payload!r}")
        if payload in _ALLOWED_LABELS or _ISO_TS_RE.match(payload):
            return
        # The one free-form string is the control-plane repo LABEL, allowed only
        # under the `$.repo` key and only if it is a slug (no path/secret shape).
        if _path == "$.repo" and _REPO_SLUG_RE.match(payload):
            return
        raise SanitizationError(f"non-timestamp, non-label string at {_path}: {payload!r}")
    raise SanitizationError(f"unsupported leaf type {type(payload).__name__} at {_path}")


# Fixed enum labels permitted as string leaf VALUES (schema/provenance/kind/repo).
# Note: repo ids are validated separately by shape; the wall markers can never be
# a repo id because a slug forbids "/".
_ALLOWED_LABELS = frozenset({EXPORT_SCHEMA, "ledger-faithful", *NAMED_SERIES})


def push_export(
    url: str,
    payload: dict[str, Any],
    *,
    ingest_key: str,
    timeout: float = 15.0,
) -> int:
    """One-way HTTP POST of a proven-sanitized payload to Portal's ingest route.

    Fails closed on missing url/key. Re-asserts sanitization immediately before
    the wire write so nothing unsanitized can ever leave, even if a caller built
    the payload by hand. Returns the HTTP status code.
    """
    if not url:
        raise ValueError("fleet-metrics export url is not configured")
    if not ingest_key:
        raise ValueError("fleet-metrics export ingest key is not configured")
    assert_sanitized(payload)
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-ledger-export-key": ingest_key,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (trusted control-plane url)
        return int(response.status)
