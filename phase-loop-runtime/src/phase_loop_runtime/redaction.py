from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from typing import Any, Iterator, Mapping

from .models import CHANGED_PATH_CATEGORIES, SourceTruthImpact


_SOURCE_TRUTH_REASON_BY_CATEGORY = {
    "docs": "docs_source_truth_touched",
    "specs": "specs_source_truth_touched",
    "active_canonical_spec": "active_specs_touched",
    "managed_root_mirror_spec": "managed_mirror_specs_touched",
    "mirror_manifest": "mirror_manifests_touched",
    "archive_manifest": "archive_manifests_touched",
    "archived_spec": "archived_specs_touched",
    "unmanaged_spec": "unmanaged_specs_touched",
    "pipeline_sources": "pipeline_sources_touched",
    "portal_contract_refs": "portal_contract_refs_touched",
    "greenfield_authority_refs": "greenfield_authority_refs_touched",
}

_CATEGORY_BY_PROTECTED_SOURCE_ROLE = {
    "active_canonical_spec": "active_canonical_spec",
    "managed_mirror_file": "managed_root_mirror_spec",
    "mirror_manifest": "mirror_manifest",
    "archive_manifest": "archive_manifest",
    "archived_spec": "archived_spec",
    "unmanaged_spec_input": "unmanaged_spec",
    "root_specs_intake": "unmanaged_spec",
}

_FORBIDDEN_METADATA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("raw_diff", re.compile(r"diff --git|@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@")),
    ("raw_spec_body", re.compile(r"raw spec bod(?:y|ies)|spec body bytes|verbatim spec", re.I)),
    ("raw_transcript", re.compile(r"raw transcript|transcript bytes|verbatim transcript", re.I)),
    # agent-harness#243 CR (defect 3, ORIGINAL): a split argv flag/value pair (e.g.
    # ``["--token", "ABCDEFGHIJKL"]``) has no ``[:=]`` between the keyword and the value --
    # only whitespace, once the elements are joined back into a contiguous string by the
    # traversal below. That round widened the separator alternation to accept EITHER ``[:=]``
    # (the ``key=value``/``key: value`` shape) OR bare one-or-more whitespace (the
    # argv-adjacency shape).
    #
    # agent-harness#243 CR (REGRESSION, cross-vendor codex): accepting bare whitespace AS the
    # separator made the pattern match ordinary prose -- ``token configuration``, ``password
    # authentication documentation``, ``secret management guide`` (keyword + whitespace + 12+
    # alnum chars) -- with no secret anywhere. Because ``metadata_redaction_diagnostic`` runs
    # this matcher over every closeout leaf as a FATAL gate, a legitimate blocker_summary /
    # next_action / finding sentence containing such a phrase was rejected as
    # ``malformed_closeout``, blocking persistence -- a false-positive regression worse than the
    # split-argv miss it fixed. The separator alternation is reverted: a literal ``[:=]`` is
    # REQUIRED between the keyword and the value (whitespace/quote/backslash noise is still
    # tolerated on either side of that required separator -- see the escaped-quote paragraph
    # below -- but never STANDS IN for it). Split-argv adjacency is instead handled
    # STRUCTURALLY in ``_iter_leaf_strings``: when walking a list, an adjacent
    # flag-shaped-element -> value-shaped-element pair is joined into a synthesized
    # ``flag=value`` composite leaf (mirroring the existing dict key/value composite below) and
    # fed through this same strict pattern, rather than loosening the pattern itself. Single
    # shared pattern (SSOT): both the redaction path and the fatal closeout gate stay in sync.
    #
    # agent-harness#243 CR round 4 (codex + Fable): an ORDINARY JSON-formatted secret --
    # e.g. a failing command that ``print(json.dumps({"api_key": "SECRET"}))``s, captured
    # verbatim as a diagnostic's ``raw_tail`` -- was still a blind spot. The rendered text is
    # ``..."api_key":"SECRET"...``: the KEY's own closing quote sits directly between the
    # keyword and the ``:`` separator, so the (pre-existing) ``\s*`` before the separator
    # alternation never advances past that quote character and the match fails. The optional
    # ``['\"]?`` inserted here between the keyword and the separator absorbed that closing
    # quote (a no-op for every previously-matched shape -- ``key=value``, ``key: value``,
    # space-joined argv -- since none of those have a quote in that position), closing the gap
    # for literal JSON text without forking a second pattern.
    #
    # agent-harness#243 CR (cross-vendor, codex): the round-4 fix only tolerated a SINGLE
    # optional quote character on each side of the separator. A shell-escaped verbatim
    # command/log line -- e.g. an operational command captured as text containing
    # ``api_key: \"SECRET\"`` (a literal backslash immediately before the quote, as produced
    # by ``curl -H "X-Api-Key: \"SECRET\""`` or any shlex/json re-quoting of an already-quoted
    # value) -- still broke the match: the backslash sat in the one slot the old pattern
    # expected either a bare quote or nothing, so neither the single optional pre-separator
    # quote nor the single optional pre-value quote advanced past it and
    # ``[A-Za-z0-9_\-]{12,}`` never found a place to start. Rather than special-case yet another
    # escaping shape, both quote slots are now a character class -- ``[\s'\"\\]*`` (whitespace,
    # single quote, double quote, backslash, zero-or-more) -- run BEFORE the required separator
    # and again AFTER it. This closes the WHOLE quoting/escaping class in one shared pattern:
    # plain (``key=value``), single/double-quoted (``key='value'`` / ``key="value"``),
    # backslash-escaped (``key: \"value\"``, any depth of shell/JSON re-quoting), and JSON
    # (``"key":"value"``) all reduce to "some run of separator-ish noise, then a REQUIRED
    # ``[:=]``, then more noise, then the value". (The separator itself was briefly widened to
    # ALSO accept bare whitespace standing in for ``[:=]`` -- see the regression paragraph
    # above -- and then reverted: the noise TOLERATED on each side of the required separator
    # grew from "one optional quote" to "any run of whitespace/quote/backslash", but the
    # separator itself stays a REQUIRED literal ``[:=]``, so a keyword glued to an unrelated
    # word with no ``:``/``=`` anywhere -- ordinary prose -- still does NOT match.) Single
    # shared pattern (SSOT): both the redaction path and the fatal closeout gate stay in sync.
    (
        "secret_like_value",
        re.compile(
            r"(?:api[_-]?key|secret|token|password)[\s'\"\\]*[:=][\s'\"\\]*[A-Za-z0-9_\-]{12,}",
            re.I,
        ),
    ),
    # agent-harness#243 CR (cross-vendor codex, follow-up -- REVERTED, see next paragraph): a
    # dash-anchored CLI-flag pattern was added here to catch a space-separated ``--token
    # VALUE`` embedded in a free-text command STRING (e.g. ``{"command": "curl --token
    # AKIA..."}"``, as produced by ``discovery.py``) -- a shape that matched neither the strict
    # ``secret_like_value`` pattern above (no ``[:=]`` between keyword and value) nor the
    # structural split-argv composite below (there is no list to walk).
    #
    # A further cross-vendor CR round (codex) proved that pattern cannot be made safe: a
    # genuine secret (``--token AKIAIOSFODNN7EXAMPLEKEY``) and ordinary prose (``--token
    # configuration``, from a sentence like "Document the --token configuration behavior.")
    # are BOTH exactly ``-{1,2}keyword`` + whitespace + a 12+ char alnum run -- a regex cannot
    # tell a high-entropy secret from a benign following word by shape alone. The dash anchor
    # (meant to distinguish "CLI flag" from "English sentence") does not help here: prose
    # regularly reads a CLI flag NAME (``--token``, ``--secret``) followed by an ordinary
    # word, exactly as it would read any other technical term, and this matcher runs as a
    # FATAL gate (``metadata_redaction_diagnostic``) over closeout free text, so that
    # collision is a real, reachable false positive (a legitimate closeout blocker_summary /
    # finding sentence rejected as ``malformed_closeout``, blocking persistence) -- not a
    # theoretical one. The pattern is reverted; a space-separated flag inside a free-text
    # command STRING is a documented best-effort limit (see the contract doc), not something
    # this matcher attempts. The STRUCTURAL split-argv composite immediately below remains:
    # a pre-split ``["--token", "VALUE"]`` list has no such ambiguity, because the flag and
    # value are already separate structured elements rather than words in a sentence.
    ("absolute_private_path", re.compile(r"/(?:home|users|mnt/(?:private|evidence|secure|raw|HC_Volume_[^/\s]+))/(?:[^\"'\s]+)", re.I)),
    ("provider_payload", re.compile(r"raw provider payload|provider payload|anthropic[_-]?payload|openai[_-]?payload", re.I)),
    ("credential_payload", re.compile(r"credential payload|private key|-----begin [a-z ]*private key-----", re.I)),
    ("local_env_value", re.compile(r"local env value|\.env(?:\.local)? value|process\.env\[[^\]]+\]\s*=", re.I)),
    ("private_evidence", re.compile(r"private evidence|evidence bytes|raw evidence", re.I)),
)

# agent-harness#243 CR (structural split-argv re-fix): matches a list element that IS a secret
# keyword by itself, DASH-PREFIXED like a CLI flag (``--token``, ``-api-key``, ``--password``)
# -- i.e. the WHOLE element (after stripping whitespace), not merely an element CONTAINING one
# of these words. Used only to decide whether the NEXT list element should be joined into a
# ``flag=value`` composite for ``secret_like_value`` matching (see ``_iter_leaf_strings``); it
# does not itself gate redaction.
#
# The leading dash is REQUIRED (``-{1,2}``, not ``-{0,2}``): a bare undashed keyword element
# (e.g. ``["token", "configuration"]``) would otherwise synthesize the composite
# ``"token=configuration"`` -- reopening, via list structure, the exact ordinary-prose
# false-positive this whole CR round exists to close for plain strings (``"token
# configuration"`` must not match either; see the regression paragraph on the pattern above).
# Every real split-argv shape this composite needs to catch (``--token``, ``-t`` style flags)
# is dash-prefixed in practice, so requiring the dash costs no genuine coverage.
_SECRET_FLAG_RE = re.compile(r"^-{1,2}(?:api[_-]?key|secret|token|password)$", re.I)


def classify_changed_path(path: str, protected_source_roles: Mapping[str, str] | None = None) -> str:
    normalized = _normalize_path(path)
    parts = PurePosixPath(normalized).parts
    lower = normalized.lower()
    role_category = _category_from_protected_source_role(normalized, protected_source_roles)
    if role_category is not None:
        return role_category

    if normalized.startswith("tests/") or "/tests/" in normalized or "/fixtures/" in normalized:
        return "tests"
    if lower == "readme.md" or normalized.startswith("docs/") or normalized.endswith(".md") and "/docs/" in normalized:
        return "docs"
    if _looks_like_mirror_manifest(normalized, lower):
        return "mirror_manifest"
    if _looks_like_archive_manifest(normalized, lower):
        return "archive_manifest"
    if _looks_like_active_canonical_spec(normalized, lower):
        return "active_canonical_spec"
    if _looks_like_archived_spec(normalized, lower):
        return "archived_spec"
    if normalized.startswith("specs/") or normalized.startswith("spec/"):
        return "unmanaged_spec"
    if (
        normalized.startswith(".pipeline/")
        or "pipeline.definition.json" in lower
        or normalized.startswith("packages/pipeline-schema/")
        or normalized.startswith("pipeline-sources/")
    ):
        return "pipeline_sources"
    if (
        "portal-contract" in lower
        or normalized.startswith("portal/contracts/")
        or normalized.startswith("contracts/portal/")
        or normalized.startswith("consiliency-portal/contracts/")
    ):
        return "portal_contract_refs"
    if (
        "greenfield-authority" in lower
        or normalized.startswith("greenfield/authority/")
        or normalized.startswith("greenfield/contracts/")
        or normalized.startswith("authority/greenfield/")
    ):
        return "greenfield_authority_refs"
    if _looks_like_code_path(normalized, parts):
        return "code"
    return "unknown"


def build_source_truth_impact(
    changed_paths: tuple[str, ...] | list[str] | Any,
    protected_source_roles: Mapping[str, str] | None = None,
) -> SourceTruthImpact:
    paths = _stable_paths(changed_paths)
    boundaries = tuple(
        {"path": path, "category": classify_changed_path(path, protected_source_roles)}
        for path in paths
    )
    reasons: list[str] = []
    for boundary in boundaries:
        category = boundary["category"]
        reason = _SOURCE_TRUTH_REASON_BY_CATEGORY.get(category)
        if reason is not None:
            reasons.append(reason)
        if "adoption" in boundary["path"].lower() and "contract" in boundary["path"].lower():
            reasons.append("adoption_contracts_touched")
        if "contract" in boundary["path"].lower() and category in CHANGED_PATH_CATEGORIES:
            reasons.append("contract_refs_touched")
    return SourceTruthImpact(
        changed_path_boundaries=boundaries,
        canonical_refresh_recommended=bool(reasons),
        canonical_refresh_reason_codes=tuple(sorted(dict.fromkeys(reasons))),
        redaction_posture="metadata_only",
    )


def redact_diagnostics_metadata_only(
    diagnostics: Any,
    *,
    force_all: bool = False,
) -> list[dict[str, Any]]:
    """agent-harness#243 (closeout-diagnostic redaction).

    A verification failure diagnostic's ``raw_tail`` is a bounded excerpt of
    ``verification.log`` bytes surfaced into the PERSISTED closeout record (which downstream
    prompts may read) — a real egress widening from disk log to closeout/ledger/prompt. Where
    that excerpt (or any diagnostic field, e.g. an ``argv`` token) carries a secret/PII-shaped
    value, redact that diagnostic to METADATA-ONLY: drop ``raw_tail`` and ``argv`` and keep only
    safe structural metadata (role/index/exit_code/failure_kind/truncated) plus counts and the
    matched reason. The on-disk ``verification.log`` is left FULL — only the closeout egress is
    narrowed. Detection reuses the SAME ``_FORBIDDEN_METADATA_PATTERNS`` the closeout malformed-
    metadata gate enforces, so a diagnostic that would trip that gate is instead redacted (this
    also removes a latent false ``malformed_closeout`` block when a red suite dumps a secret into
    the log). ``force_all`` (operator flag) redacts every diagnostic regardless of a match.
    """
    if not isinstance(diagnostics, (list, tuple)):
        return []
    redacted: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, Mapping):
            continue
        reason = "operator_forced" if force_all else _forbidden_metadata_kind(item)
        if reason is None:
            redacted.append(dict(item))
            continue
        raw_tail = item.get("raw_tail")
        redacted.append(
            {
                "role": item.get("role"),
                "index": item.get("index"),
                "exit_code": item.get("exit_code"),
                "failure_kind": item.get("failure_kind"),
                "truncated": bool(item.get("truncated")),
                "diagnostic_status": "redacted",
                "redacted": True,
                "redaction_reason": reason,
                "raw_tail_bytes": len(raw_tail.encode("utf-8")) if isinstance(raw_tail, str) else 0,
                "argv_len": len(item["argv"]) if isinstance(item.get("argv"), (list, tuple)) else 0,
            }
        )
    return redacted


def apply_diagnostics_redaction(validation_payload: dict[str, Any]) -> dict[str, Any]:
    """agent-harness#266 (source redaction); agent-harness#243 CR recheck (whole-summary
    redaction, closing the ``suite_command`` sibling-field gap).

    SSOT wrapper around :func:`redact_diagnostics_metadata_only` for every call site that
    stores a ``VerificationArtifactValidation.to_json()`` payload — or a larger PERSISTED
    validation *summary* that embeds one, e.g. ``runner_verification`` (see
    ``runner._run_execute_verification``) — into a PERSISTED copy: ``launch.json``,
    ``child_automation``, the run ledger event, the hotfix ``artifact_validation`` ledger
    event and CLI payload, and the closeout record. Each of those copies used to redact
    independently (or, before agent-harness#266, not at all) -- a prior round redacted ONLY
    the rebuilt closeout record, leaving ``runner_verification`` (and everything derived from
    it) carrying the RAW ``raw_tail``/``argv`` text. That raw copy reaches an agent/harness
    context deterministically: ``merge_launch_metadata`` writes it into ``launch.json``,
    ``inspect_state()`` reads the whole launch file back out as ``latest_launch_metadata``,
    and ``phase-loop state --json`` (which the harness SKILL explicitly directs agents to run)
    serializes that object verbatim. Redacting at the SOURCE -- the first place a validation's
    ``to_json()`` is captured into a payload that will be persisted -- closes every one of
    those derived copies at once instead of re-implementing the same redaction ad hoc (or
    forgetting it) at each downstream call site.

    A cross-vendor CR recheck (codex, agent-harness#243) found that round only rewrote the
    NESTED ``diagnostics`` list, leaving a SIBLING field -- ``runner_verification`` /
    ``summary["suite_command"]``, persisted right BESIDE the redacted diagnostic -- carrying
    the SAME secret argv raw: for a failing suite, ``suite_command`` is the exact command that
    produced the (now-redacted) diagnostic. This function now walks the WHOLE payload
    recursively (not just a top-level ``diagnostics`` key): every nested ``diagnostics`` list,
    at any depth, still gets the structured metadata-only treatment above; every OTHER field
    whose name looks command/argv-shaped (``suite_command``, ``install_argv``, ``commands``,
    …) is separately checked against the SAME ``_FORBIDDEN_METADATA_PATTERNS`` matcher (via
    ``_iter_leaf_strings``, so a split argv flag/value pair is caught the same way it is inside
    a diagnostic) and, on a match, replaced wholesale with a ``"<redacted:<field>>"`` placeholder
    -- dropping the value rather than trying to scrub just the matched substring, since a
    command argv has no safe partial-redaction shape. The command/argv name heuristic is
    intentionally narrow (not "scan every field") so it never touches unrelated structural
    fields that legitimately look secret-shaped out of context -- e.g. ``artifact_path`` /
    ``verification_artifact_path`` under a local checkout rooted at ``/home/<user>/...`` would
    otherwise spuriously match the ``absolute_private_path`` pattern.

    Mutates and returns ``validation_payload`` in place (mirrors ``dict.update`` ergonomics of
    the call sites this replaces). A payload with no matching fields is returned unchanged.
    Idempotent: an already metadata-only-redacted diagnostic list, or an already-``<redacted:…>``
    sibling field, re-run through this function, is a no-op -- neither carries secret-shaped
    free text anymore, so neither re-matches a forbidden-metadata pattern and both are copied
    through unchanged (a ``force_all`` re-application only re-shapes an already-safe diagnostic
    dict, never reintroduces raw text). ``PHASE_LOOP_VERIFY_REDACT_DIAGNOSTICS=all`` (operator
    override) forces every diagnostic in the payload to metadata-only regardless of a pattern
    match; it does not force-redact sibling command fields (those are already default-on
    pattern-triggered, independent of the diagnostics-specific operator override).
    """
    force_all = os.environ.get("PHASE_LOOP_VERIFY_REDACT_DIAGNOSTICS", "").strip().lower() == "all"
    _redact_validation_payload_in_place(validation_payload, force_all=force_all)
    return validation_payload


_COMMAND_FIELD_NAME_HINTS = ("command", "argv", "cmd")


def _looks_like_command_field(key: str) -> bool:
    """Narrow, name-based heuristic for "this sibling field holds a command/argv value" —
    see the ``apply_diagnostics_redaction`` docstring for why this is intentionally NOT a
    blanket scan of every field (that would also catch, and wrongly redact, legitimate
    absolute path fields like ``artifact_path``/``log_path``).
    """
    lowered = key.lower()
    return any(hint in lowered for hint in _COMMAND_FIELD_NAME_HINTS)


def _redact_validation_payload_in_place(payload: Any, *, force_all: bool) -> None:
    """Recursive worker for :func:`apply_diagnostics_redaction`. No-op on a non-dict input."""
    if not isinstance(payload, dict):
        return
    diagnostics = payload.get("diagnostics")
    if diagnostics:
        payload["diagnostics"] = redact_diagnostics_metadata_only(diagnostics, force_all=force_all)
    for key, value in payload.items():
        if key == "diagnostics":
            continue  # already narrowed to metadata-only, per-entry, above.
        if isinstance(value, dict):
            _redact_validation_payload_in_place(value, force_all=force_all)
        elif isinstance(value, (list, tuple)) and any(isinstance(item, dict) for item in value):
            for item in value:
                _redact_validation_payload_in_place(item, force_all=force_all)
        elif _looks_like_command_field(key) and _forbidden_metadata_kind(value) is not None:
            payload[key] = f"<redacted:{key}>"


def _scalar_text(value: Any) -> str | None:
    """Return the raw text to match a scalar leaf against, or ``None`` for containers/``None``.

    agent-harness#243 CR (defect 2): the pre-fix walker yielded only ``isinstance(value, str)``
    leaves, so a non-string scalar secret (e.g. ``{"account_id": 123456789012345}``) -- which
    the old ``json.dumps(...)``-blob matcher DID catch, since ``json.dumps`` stringifies it in
    place -- was silently dropped. ``str(value)`` restores that coverage for ``int``/``float``/
    ``bool`` leaves. ``None`` is skipped (``str(None)`` -> ``"None"`` is never a secret and
    would only add noise).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _iter_leaf_strings(value: Any) -> Iterator[str]:
    """Depth-first yield of every text fragment inside a nested dict/list/tuple structure
    (e.g. a diagnostic's nested ``argv`` list, or a closeout payload's nested
    ``evidence_refs``) that must be matched against ``_FORBIDDEN_METADATA_PATTERNS``.

    agent-harness#243 CR: forbidden-metadata matching used to run against a
    ``json.dumps(...)`` serialization of the whole structure. ``json.dumps`` backslash-escapes
    an embedded double quote (``"`` -> ``\\"``), which breaks ``secret_like_value`` for a
    double-quoted secret like ``api_key="SECRETVALUE12"`` — the serialized blob becomes
    ``api_key=\\"SECRETVALUE12\\"``, the injected backslash sits between ``=`` and the quote,
    so the pattern's optional ``['\"]?`` matches zero quotes and the following
    ``[A-Za-z0-9_\\-]{12,}`` starts at the backslash and fails to match. Walking RAW, unescaped
    text and matching it directly closes that blind spot.

    A follow-up cross-vendor review (codex + gemini) found the leaf-only walk was not a true
    superset of a reasonable whole-payload matcher — it missed three shapes:

    1. Dict KEYS (a genuine regression vs. the old ``json.dumps(...)`` blob matcher): the blob
       included key text verbatim (e.g. ``json.dumps({"api_key=ABCDEFGHIJKL": "safe"})`` ->
       ``{"api_key=ABCDEFGHIJKL": "safe"}``, which DOES match ``secret_like_value`` since the
       keyword+separator+value run contiguously inside the key's own quoted text); a
       leaf-VALUES-only walk silently dropped that key. Fixed: every dict key is tested via
       ``str(key)``.
    2. Non-string scalars: see ``_scalar_text`` above. (Note: a bare non-string scalar with no
       adjacent keyword, e.g. a lone ``123456789012345``, does not itself match any current
       forbidden pattern — restoring it to the tested corpus matters once it sits next to a
       keyword, per point 3 below, or against a future pattern.)
    3. Split argv/list flag+value pairs: ``argv=["tool", "--token", "ABCDEFGHIJKL"]`` puts the
       keyword and the value in ADJACENT list elements. This was NOT actually caught by the old
       blob matcher either — ``json.dumps`` puts a closing quote, comma, and space between
       ``"--token"`` and ``"ABCDEFGHIJKL"`` in the serialized array, breaking the immediate
       keyword-then-separator adjacency ``secret_like_value`` requires — so this is new
       coverage, not a restored regression. Originally fixed by widening ``secret_like_value``'s
       separator to also accept bare whitespace, matched against the SPACE-JOINED concatenation
       of the list's stringified scalar elements -- but that widening let the pattern match
       ordinary prose too (``token configuration``), a false-positive regression (see the CR
       note on the pattern itself). Re-fixed STRUCTURALLY instead of by loosening the pattern:
       for every list/tuple, when one element is a DASH-PREFIXED flag-shaped secret keyword
       (``--token``, ``-api-key``, …, matched via ``_SECRET_FLAG_RE`` — i.e. the element IS one
       of the keywords with a leading dash, not merely CONTAINING one) and the next element is
       a scalar, synthesize a ``flag=value`` composite leaf (mirroring the Mapping composite
       below) and feed THAT through the still-strict ``[:=]``-required pattern. The dash is
       REQUIRED, not optional: an undashed bare-word element (``["token", "configuration"]``)
       would otherwise reopen, via list structure, the exact ordinary-prose false positive this
       CR round exists to close for plain strings — every real split-argv shape this needs to
       catch is dash-prefixed in practice, so requiring the dash costs no genuine coverage. The
       space-joined whole-list leaf is still yielded too (for the other, non-secret_like_value
       patterns that scan free text), but no longer carries the separator-widening risk since
       ``secret_like_value`` itself requires a literal ``[:=]``.

    A cross-vendor CR round (codex + Fable, agent-harness#243) found the dict-key fix above
    was still not a true superset: an ORDINARY JSON-shaped secret, e.g. a Mapping entry
    ``{"api_key": "SECRETVALUE12"}``, yields the key leaf (``"api_key"``) and the value leaf
    (``"SECRETVALUE12"``) SEPARATELY — the same interruption problem as the split-argv case,
    just via dict structure instead of list adjacency. Neither leaf alone carries the other's
    context, so ``secret_like_value`` (which requires keyword+separator+value CONTIGUOUS in one
    leaf) never matches, even though the mapping is exactly the shape a matched keyword should
    catch. Fixed: for every Mapping entry whose value is a scalar (``_scalar_text`` -- string,
    int, float, bool), ALSO synthesize a composite ``key="value"`` (and bare ``key=value``) leaf
    that restores keyword→separator→value adjacency for the shared pattern to match. This is
    additive (the bare key and bare value leaves are still yielded too, preserving all prior
    coverage) and stays single-pattern SSOT: only the STRINGS fed to
    ``_FORBIDDEN_METADATA_PATTERNS`` change, not the patterns' semantics. A nested structure
    (``{"outer": {"token": "SECRET"}}``) is caught by the same mechanism one level down, via the
    recursion below.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            scalar = _scalar_text(item)
            if scalar is not None:
                yield f'{key}="{scalar}"'
                yield f"{key}={scalar}"
            yield from _iter_leaf_strings(item)
        return
    if isinstance(value, (list, tuple)):
        items = list(value)
        parts: list[str] = []
        for idx, item in enumerate(items):
            yield from _iter_leaf_strings(item)
            scalar = _scalar_text(item)
            if scalar is not None:
                parts.append(scalar)
            # agent-harness#243 CR (structural split-argv re-fix, see docstring point 3): a
            # flag-shaped element immediately followed by a scalar value element is joined into
            # a synthesized ``flag=value`` composite so the still-strict ``secret_like_value``
            # pattern (which requires a literal ``[:=]``) can see keyword+separator+value
            # contiguously, without loosening the pattern itself to accept bare whitespace.
            if isinstance(item, str) and _SECRET_FLAG_RE.match(item.strip()) and idx + 1 < len(items):
                next_scalar = _scalar_text(items[idx + 1])
                if next_scalar is not None:
                    yield f"{item}={next_scalar}"
        if parts:
            yield " ".join(parts)
        return
    scalar = _scalar_text(value)
    if scalar is not None:
        yield scalar


def _forbidden_metadata_kind(payload: Any) -> str | None:
    # Any -- not just Mapping -- since callers (e.g. the sibling command-field scan in
    # ``_redact_validation_payload_in_place``) also test a bare scalar or list/tuple value
    # (a field's value, not the whole enclosing dict); ``_iter_leaf_strings`` already handles
    # every shape.
    leaves = list(_iter_leaf_strings(payload))
    for kind, pattern in _FORBIDDEN_METADATA_PATTERNS:
        for leaf in leaves:
            if pattern.search(leaf):
                return kind
    return None


def metadata_redaction_diagnostic(payload: Mapping[str, Any] | None) -> dict[str, str] | None:
    if payload is None:
        return None
    kind = _forbidden_metadata_kind(payload)
    if kind is not None:
        return {"kind": "malformed_closeout", "message": f"closeout contains forbidden metadata token: {kind}"}
    return None


def _normalize_path(path: str) -> str:
    normalized = str(path).replace("\\", "/").strip()
    return normalized[2:] if normalized.startswith("./") else normalized


def _stable_paths(paths: tuple[str, ...] | list[str] | Any) -> list[str]:
    if not isinstance(paths, (tuple, list)):
        return []
    return sorted(dict.fromkeys(_normalize_path(str(path)) for path in paths if str(path).strip()))


def _looks_like_code_path(path: str, parts: tuple[str, ...]) -> bool:
    if path.startswith(("codex-config/", "shared/skills/", "scripts/")):
        return True
    if parts and parts[0] in {"bin", "lib", "src"}:
        return True
    return PurePosixPath(path).suffix in {".py", ".sh", ".bash", ".zsh", ".toml", ".yaml", ".yml", ".json"}


def _category_from_protected_source_role(
    path: str,
    protected_source_roles: Mapping[str, str] | None,
) -> str | None:
    if not protected_source_roles:
        return None
    role = protected_source_roles.get(path) or protected_source_roles.get(path.lower())
    if role is None:
        return None
    return _CATEGORY_BY_PROTECTED_SOURCE_ROLE.get(role)


def _looks_like_mirror_manifest(path: str, lower: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name == "mirror-manifest.json" or "mirror_manifest" in lower or "/mirror-manifest" in lower


def _looks_like_archive_manifest(path: str, lower: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name == "archive-manifest.json" or "archive_manifest" in lower or "/archive-manifest" in lower


def _looks_like_active_canonical_spec(path: str, lower: str) -> bool:
    return (
        path.startswith(".pipeline/specs/active/")
        or path.startswith(".pipeline/specs/canonical/")
        or "/active-canonical/" in lower
        or "/canonical-specs/" in lower
    )


def _looks_like_archived_spec(path: str, lower: str) -> bool:
    return path.startswith(".pipeline/specs/archive/") or "/archived-specs/" in lower
