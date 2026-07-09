"""CERT / SCHEMA tier -- validate a declared parity certificate against the
contract-distributed ``certificate`` schema.

ONE LIBRARY, TWO ROLES (same discipline as the SHAPE gates).
    :func:`validate_certificate` is the SAME check an actor runs as a mock
    self-check and the fence mounts as the real validator. The actor-side result
    is advisory only -- NEVER authoritative; the fence always re-runs it. It is
    versioned with the contract: it validates against
    ``consiliency_contract.load_schema('certificate')`` (distributed from
    contract 0.6.4+), so an actor and a fence pinned to the same contract
    evaluate byte-for-byte the same schema.

SCOPE -- CERT / SCHEMA TIER, NOT AUTHORITY / PROVENANCE / SIGNING.
    This is STRUCTURAL conformance only: required fields present, and the
    ``result_state`` ``$ref`` closure enforced (``overall_result_state`` and each
    ``dimension_results[].result_state`` must be a real contract result-state
    enum member; ``dimension`` a real ``parity_dimension``). It deliberately does
    NOT:

    * verify the certificate ``digest`` byte-value (produce-once / never-recompute
      is a canon concern),
    * verify any signature / authority chain, or
    * verify canon / provenance of the referenced artifacts.

    Those higher rungs stay downstream in gp (the authority/provenance verifier).
    This surface asserts only that a declared certificate is *shaped like* a
    contract certificate.

CONTRACT-ABSENT DEGRADE.
    Mirrors the ``gate_posture.available()`` pattern used by the SHAPE gates:
    when ``consiliency_contract`` is not importable, or is too old to distribute
    the ``certificate`` schema (``load_schema`` raises), :func:`validate_certificate`
    degrades to a neutral ``skipped`` verdict with a note -- it never crashes. It
    is authoritative only when the schema is present.
"""
from __future__ import annotations

from typing import Any, Mapping


def certificate_schema_available() -> bool:
    """True iff the installed ``consiliency_contract`` distributes the parity
    ``certificate`` schema (contract 0.6.4+). Mirrors ``gate_posture.available()``:
    an older contract -- or no contract at all -- returns False, and
    :func:`validate_certificate` then degrades to a neutral ``skipped`` verdict."""
    try:
        import consiliency_contract as _cc  # noqa: F401
    except Exception:
        return False
    try:
        _cc.load_schema("certificate")
    except Exception:
        return False
    return True


def _certificate_validator():
    """Build a Draft 2020-12 validator for the contract ``certificate`` schema,
    with the cross-file ``result-state.schema.json`` ``$ref`` target registered so
    the result-state enum closure resolves. Returns ``None`` when the contract or
    the cert schema is absent (graceful degrade)."""
    try:
        import consiliency_contract as cc
    except Exception:
        return None
    try:
        cert_schema = cc.load_schema("certificate")
    except Exception:
        return None

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    resources: list[tuple[str, Any]] = []
    cert_id = cert_schema.get("$id")
    if cert_id:
        resources.append((cert_id, Resource.from_contents(cert_schema)))
    # The cert schema's overall_result_state / dimension_results $ref
    # `result-state.schema.json#/$defs/...`, which resolves against the cert
    # $id base to `.../result-state.schema.json`. That URI is the result_state
    # schema's own $id, so registering it lets the cross-file (and intra-file
    # #/$defs) refs resolve. Absence of this schema is itself a degrade signal.
    try:
        rs_schema = cc.load_schema("result_state")
    except Exception:
        return None
    rs_id = rs_schema.get("$id")
    if rs_id:
        resources.append((rs_id, Resource.from_contents(rs_schema)))

    registry = Registry().with_resources(resources)
    return Draft202012Validator(cert_schema, registry=registry)


def validate_certificate(cert: Mapping[str, Any]) -> dict[str, Any]:
    """CERT / SCHEMA tier: structurally validate a DECLARED parity certificate
    against the contract-distributed ``certificate`` schema.

    This is the rung above ``hash-checked`` -- it asserts a declared certificate
    is *shaped like* a contract certificate (required fields present, the
    ``result_state`` ``$ref`` closure enforced), loaded via the same
    ``consiliency_contract`` loader the SHAPE gates use. It is STRUCTURAL only:
    it does NOT verify the certificate digest byte-value, any signature, or
    canon/provenance -- those stay downstream in gp (authority/provenance tier).

    Returns the SHAPE-gate verdict shape::

        {"status": "passed" | "blocked" | "skipped", "findings": [ ... ]}

    * ``passed`` -- the certificate conforms to the contract schema.
    * ``blocked`` -- one or more structural violations (each a finding with a
      ``code``, ``message``, and ``ref`` JSON path into the certificate).
    * ``skipped`` -- ``consent: false`` degrade: the installed contract does not
      distribute the ``certificate`` schema (absent or < 0.6.4), so this tier is
      latent. Never a crash.

    ACTOR RESULT IS NEVER AUTHORITATIVE -- this is the same function the actor
    runs (mock) and the fence mounts (real). The fence always re-runs it; a stale
    or dishonest actor verdict does not matter because the fence recomputes.
    Versioned with the contract: pin actor and fence to the same contract and the
    schema is byte-identical.
    """
    validator = _certificate_validator()
    if validator is None:
        return {
            "status": "skipped",
            "consent": False,
            "maturity": "cert-schema",
            "findings": [],
            "note": (
                "installed consiliency_contract does not distribute the "
                "'certificate' schema (absent or <0.6.4); cert-schema tier latent"
            ),
        }

    if not isinstance(cert, Mapping):
        return {
            "status": "blocked",
            "consent": True,
            "maturity": "cert-schema",
            "findings": [
                {
                    "code": "cert_not_object",
                    "ref": "",
                    "message": "declared certificate is not a JSON object",
                }
            ],
        }

    findings: list[dict[str, Any]] = []
    for err in validator.iter_errors(dict(cert)):
        ref = "/".join(str(p) for p in err.absolute_path)
        findings.append(
            {
                "code": "cert_schema_violation",
                "ref": ref,
                "message": err.message,
                "validator": err.validator,
            }
        )

    status = "passed" if not findings else "blocked"
    return {
        "status": status,
        "consent": True,
        "maturity": "cert-schema",
        "findings": findings,
    }


__all__ = [
    "validate_certificate",
    "certificate_schema_available",
]
