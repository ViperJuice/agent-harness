"""Git-discipline guardrail + bounded top-of-loop self-heal (roadmap Slice G).

CONSUMER of the neutral ``@consiliency/contract`` git-discipline contract -- it
does NOT redefine it. It reads two contract artifacts through the existing thin
loaders (``consiliency_contract.load_registry`` / ``load_schema``):

* registry ``pipeline_ref_classes`` -- the falsifiable, ordered enumeration of
  pipeline-owned ref families plus the human default (everything else).
* schema ``git_discipline_protocol`` -- the write-footprint allowlist, the
  self-heal scope, and the invariants (``never_delete_human_refs`` ...). All of
  its knobs are JSON-Schema ``const``s, so the allowlist etc. are read out of
  the schema structure at runtime, never hand-copied.

Two responsibilities, both bounded and autonomy-first:

1. **GUARDRAIL (verification).** Classify the repo's refs against the registry
   and check the write-footprint / branch-naming invariants. Surfaced as the
   fifth L0 ``.consiliency`` gate (``consiliency_gates.git_discipline``):
   SOFT/warn by default, blocking only under
   ``PHASE_LOOP_CONSILIENCY_GATES=hard`` (opt-in). The gate layer NEVER sets
   ``human_required``.

2. **SELF-HEAL PARTITION (top-of-loop).** Decide which refs a bounded,
   idempotent self-heal may reconcile. The partition is PURE and default-deny: a
   ref is eligible for self-heal deletion ONLY when it is pipeline-owned, in a
   ref-class flagged ``deletable_by_self_heal``, currently leased, AND already
   merged. Every human ref, every unleased pipeline ref, and every non-deletable
   pipeline ref is PROTECTED. This is the machine form of the
   NEVER-DELETE-HUMAN-REFS invariant; ``never_deleted_human_refs`` is provably a
   subset of ``protected`` and disjoint from ``deletable_by_self_heal``.

Contract-absent degrade: when the installed ``consiliency_contract`` predates the
git-discipline contract (< 0.4, no ``pipeline_ref_classes`` registry), every
entry point degrades to a neutral no-op (``available()`` is ``False``) -- NOT a
warning -- so existing governed scans are byte-for-byte unaffected until the
contract carrying it is installed.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REF_CLASS_REGISTRY = "pipeline_ref_classes"
GIT_DISCIPLINE_SCHEMA = "git_discipline_protocol"

# One placeholder ``{name}`` OR a glob ``*`` -- each spans exactly one path
# segment (``[^/]+``). Anchoring the compiled pattern (below) is load-bearing:
# unanchored, ``pipeline/{phase}-{node}`` would match ``my-pipeline/foo-bar`` as
# a substring and misclassify a human ref as pipeline-owned/deletable.
_TOKEN_RE = re.compile(r"\{[^}]+\}|\*")


# --------------------------------------------------------------------------- #
# Contract loading (guarded; contract-absent -> neutral no-op)
# --------------------------------------------------------------------------- #
def load_ref_classes() -> dict[str, Any] | None:
    """The ``pipeline_ref_classes`` registry, or ``None`` when the installed
    contract predates the git-discipline contract (< 0.4)."""
    try:
        from consiliency_contract import load_registry

        registry = load_registry(REF_CLASS_REGISTRY)
    except Exception:
        return None
    return registry if isinstance(registry, Mapping) else None


def load_protocol() -> dict[str, Any] | None:
    """The ``git_discipline_protocol`` schema, or ``None`` when absent."""
    try:
        from consiliency_contract import load_schema

        schema = load_schema(GIT_DISCIPLINE_SCHEMA)
    except Exception:
        return None
    return schema if isinstance(schema, Mapping) else None


def available(registry: Mapping[str, Any] | None = None) -> bool:
    """True when the installed contract carries the git-discipline ref-class
    registry (i.e. the guardrail can run against a real contract)."""
    reg = load_ref_classes() if registry is None else registry
    return isinstance(reg, Mapping) and bool(reg.get("ref_classes"))


# --------------------------------------------------------------------------- #
# Ref classification (pure)
# --------------------------------------------------------------------------- #
def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a registry ``pattern`` to an anchored regex. Literal spans are
    ``re.escape``-d; each ``{placeholder}`` / ``*`` becomes one path segment."""
    parts: list[str] = []
    last = 0
    for match in _TOKEN_RE.finditer(pattern):
        parts.append(re.escape(pattern[last : match.start()]))
        parts.append("[^/]+")
        last = match.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


@dataclass(frozen=True)
class RefClassification:
    name: str
    owner: str  # "pipeline" | "human"
    class_id: str
    lease_required: bool
    deletable_by_self_heal: bool
    merge_policy: str | None


def _classification_from(entry: Mapping[str, Any], name: str) -> RefClassification:
    return RefClassification(
        name=name,
        owner=str(entry.get("owner", "human")),
        class_id=str(entry.get("id", "human")),
        lease_required=bool(entry.get("lease_required", False)),
        deletable_by_self_heal=bool(entry.get("deletable_by_self_heal", False)),
        merge_policy=entry.get("merge_policy"),
    )


def classify_ref(name: str, registry: Mapping[str, Any]) -> RefClassification:
    """Classify ``name`` by first-match against ``ref_classes`` in order; a ref
    that matches no pipeline-owned pattern falls through to the human default.

    Default-deny: anything not provably matching a pipeline-owned pattern is
    human-owned and therefore never eligible for self-heal deletion.
    """
    for entry in registry.get("ref_classes", []):
        pattern = str(entry.get("pattern") or "")
        if not pattern:
            continue
        if _pattern_to_regex(pattern).match(name):
            return _classification_from(entry, name)
    human_default = registry.get("human_default") or {"owner": "human", "id": "human"}
    return _classification_from(human_default, name)


# --------------------------------------------------------------------------- #
# Self-heal partition (pure) -- the NEVER-DELETE-HUMAN-REFS invariant in code
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RefState:
    """A ref plus the two facts self-heal eligibility turns on."""

    name: str
    leased: bool = False
    merged: bool = False


def _eligible_for_self_heal(cls: RefClassification, ref: RefState) -> bool:
    """The registry ``self_heal_deletion_rule``: eligible iff owner is
    ``pipeline`` AND the ref-class is ``deletable_by_self_heal`` AND the ref is
    currently leased AND (for the phase-worktree classes -- the only deletable
    ones) already merged. Required uniformly here: any other class is
    non-deletable, so demanding ``merged`` on the deletable ones is exact and
    default-deny for everything else."""
    return (
        cls.owner == "pipeline"
        and cls.deletable_by_self_heal
        and ref.leased
        and ref.merged
    )


def _protected_reason(cls: RefClassification, ref: RefState) -> str:
    if cls.owner == "human":
        return "human ref -- never auto-deleted"
    if not cls.deletable_by_self_heal:
        return f"pipeline ref in non-deletable class {cls.class_id!r} -- reconcile rebases, never deletes"
    if not ref.leased:
        return "pipeline ref in a deletable class but UNLEASED -- self-heal needs a lease"
    if not ref.merged:
        return "pipeline ref leased but not yet merged -- self-heal never moves in-progress work"
    return "protected"


def self_heal_partition(
    refs: Sequence[RefState], registry: Mapping[str, Any]
) -> dict[str, Any]:
    """Partition ``refs`` into what a bounded self-heal MAY delete vs MUST
    protect. Pure -- the caller supplies the lease/merge facts.

    The returned shape mirrors the contract conformance vector
    ``git-discipline-never-delete-human-refs`` so the vector can drive a replay
    test: ``deletable_by_self_heal``, ``protected``, ``human_refs``,
    ``never_deleted_human_refs``, and per-ref ``protected_reasons``.
    """
    deletable: list[str] = []
    protected: list[str] = []
    human_refs: list[str] = []
    reasons: dict[str, str] = {}
    for ref in refs:
        cls = classify_ref(ref.name, registry)
        if cls.owner == "human":
            human_refs.append(ref.name)
        if _eligible_for_self_heal(cls, ref):
            deletable.append(ref.name)
        else:
            protected.append(ref.name)
            reasons[ref.name] = _protected_reason(cls, ref)
    deletable_set = set(deletable)
    return {
        "deletable_by_self_heal": deletable,
        "protected": protected,
        "human_refs": human_refs,
        # A human ref is NEVER eligible, so this is provably == human_refs and
        # disjoint from deletable_by_self_heal (the invariant, asserted in tests).
        "never_deleted_human_refs": [n for n in human_refs if n not in deletable_set],
        "protected_reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Guardrail evaluation (pure) -- write-footprint + branch-naming invariants
# --------------------------------------------------------------------------- #
def write_footprint_allowlist(protocol: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The pipeline write-footprint allowlist, read out of the schema ``const``
    (never hand-copied). Empty tuple when the schema is absent/misshaped."""
    try:
        allow = protocol["properties"]["write_footprint"]["properties"]["allowlist"]["const"]  # type: ignore[index]
    except (KeyError, TypeError):
        return ()
    return tuple(str(p) for p in allow) if isinstance(allow, list) else ()


def _allowlisted(path: str, allowlist: Sequence[str]) -> bool:
    for pattern in allowlist:
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if path == prefix or path.startswith(prefix + "/"):
                return True
        elif path == pattern or fnmatchcase(path, pattern):
            return True
    return False


def _pipeline_prefixes(registry: Mapping[str, Any]) -> list[str]:
    """The static (pre-placeholder) prefix of each pipeline-owned pattern, e.g.
    ``consiliency/pipeline/``, ``phase-loop/sched/``, ``pipeline/``. Used to spot
    a branch that SITS under a pipeline family prefix but matches no canonical
    pattern -- i.e. a pipeline-owned ref that drifted from the contract shape."""
    prefixes: list[str] = []
    for entry in registry.get("ref_classes", []):
        if entry.get("owner") != "pipeline":
            continue
        pattern = str(entry.get("pattern") or "")
        token = _TOKEN_RE.search(pattern)
        prefix = pattern[: token.start()] if token else pattern
        if prefix:
            prefixes.append(prefix)
    return prefixes


def evaluate_git_discipline(
    *,
    current_branch: str,
    dirty_paths: Sequence[str],
    local_branches: Sequence[str],
    registry: Mapping[str, Any],
    protocol: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return git-discipline findings for the repo (pure; the caller injects the
    git facts). Severity is applied by the gate wrapper, not here."""
    findings: list[dict[str, Any]] = []

    # (1) Write-footprint: on a pipeline-owned working branch, dirty writes must
    #     stay inside the contract allowlist (schema policy
    #     ``unauthorized-pipeline-write-refused``).
    current_cls = classify_ref(current_branch, registry)
    if current_cls.owner == "pipeline":
        allowlist = write_footprint_allowlist(protocol)
        if allowlist:  # only enforce when the contract actually declares one
            for path in dirty_paths:
                if not _allowlisted(path, allowlist):
                    findings.append(
                        {
                            "code": "write_footprint_violation",
                            "branch": current_branch,
                            "path": path,
                        }
                    )

    # (2) Branch-naming: a branch under a pipeline-owned family prefix that
    #     matches no canonical pattern (default-deny -> classified human) has
    #     drifted from the contract shape.
    prefixes = _pipeline_prefixes(registry)
    for branch in local_branches:
        if classify_ref(branch, registry).owner == "pipeline":
            continue
        if any(branch.startswith(prefix) for prefix in prefixes):
            findings.append({"code": "pipeline_branch_naming_drift", "branch": branch})

    return findings


# --------------------------------------------------------------------------- #
# Impure adapters -- gather git facts (kept out of the pure core above)
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def gather_repo_ref_facts(repo: Path) -> dict[str, Any]:
    """Best-effort snapshot for :func:`evaluate_git_discipline`. Never raises;
    a git failure degrades to empty facts (the guardrail then finds nothing)."""
    current = _git(repo, "branch", "--show-current")
    current_branch = current.stdout.strip() if current.returncode == 0 else ""

    branches: list[str] = []
    listed = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if listed.returncode == 0:
        branches = [line.strip() for line in listed.stdout.splitlines() if line.strip()]

    dirty_paths: list[str] = []
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            entry = line[3:].strip() if len(line) > 3 else ""
            if " -> " in entry:  # renames: "old -> new"
                entry = entry.split(" -> ", 1)[1]
            if entry:
                dirty_paths.append(entry)

    return {
        "current_branch": current_branch,
        "dirty_paths": tuple(dirty_paths),
        "local_branches": tuple(branches),
    }


def gather_pipeline_ref_states(
    repo: Path,
    registry: Mapping[str, Any],
    *,
    base_ref: str | None = None,
    lease_predicate: Callable[[str], bool] | None = None,
) -> list[RefState]:
    """Snapshot local branches as :class:`RefState` (name / leased / merged) for
    :func:`self_heal_partition`. ``leased`` defaults to ``False`` (conservative
    -> nothing eligible) unless a ``lease_predicate`` is supplied; ``merged`` is
    computed against ``base_ref`` (default: the repo's default branch)."""
    listed = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    branches = (
        [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if listed.returncode == 0
        else []
    )
    base = base_ref or _default_branch(repo)
    merged: set[str] = set()
    if base:
        result = _git(repo, "branch", "--format=%(refname:short)", "--merged", base)
        if result.returncode == 0:
            merged = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    predicate = lease_predicate or (lambda _name: False)
    return [
        RefState(name=b, leased=bool(predicate(b)), merged=b in merged) for b in branches
    ]


def _default_branch(repo: Path) -> str:
    result = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    head = result.stdout.strip() if result.returncode == 0 else ""
    return head.removeprefix("origin/") if head.startswith("origin/") else ""


# --------------------------------------------------------------------------- #
# Bounded top-of-loop self-heal -- detect + idempotent-safe fix; delegate deletes
# --------------------------------------------------------------------------- #
def reconcile_git_discipline(
    repo: Path,
    *,
    registry: Mapping[str, Any] | None = None,
    ref_states: Sequence[RefState] | None = None,
    execute_prune: bool = False,
) -> dict[str, Any]:
    """Bounded top-of-loop self-heal. Detection is always safe (pure reporting);
    the only auto-fix is ``git worktree prune`` (removes stale worktree admin
    records -- it never deletes a branch or a live worktree, so it is inherently
    idempotent-safe). Branch/worktree DELETION for the deletable set is NOT
    performed here; it is surfaced as a plan and left to
    :func:`apply_self_heal_deletions` (opt-in), which re-asserts the partition.

    Returns ``{"status": "skipped"|"reconciled", "partition": {...},
    "findings": [...]}``. ``findings`` are advisory, ``human_required=False``.
    Never raises.
    """
    # Contract-absent: git-discipline is entirely latent when the installed
    # contract predates it (< 0.4) -- nothing to consent to or enforce.
    reg = load_ref_classes() if registry is None else registry
    if not available(reg):
        return {"status": "skipped", "reason": "contract-absent", "findings": []}
    assert reg is not None  # narrowed by available()

    # Consent gate (design §11.5): the git-discipline contract is post-ingestion
    # and opt-in. A repo without a `.consiliency/manifest.json` is a pure no-op --
    # no classification, no advisories, no `git worktree prune`. This mirrors
    # `scan_consiliency_gates`, which the guardrail gate already rides.
    try:
        from .consiliency_layout import find_consiliency_manifest

        if find_consiliency_manifest(Path(repo)) is None:
            return {"status": "skipped", "reason": "no-consent", "findings": []}
    except Exception:
        return {"status": "skipped", "reason": "no-consent", "findings": []}

    states = (
        list(ref_states)
        if ref_states is not None
        else gather_pipeline_ref_states(repo, reg)
    )
    partition = self_heal_partition(states, reg)

    findings: list[dict[str, Any]] = []
    # Drift is advisory: never auto-moved (moving possibly-in-progress work is
    # exactly the failure mode the contract forbids).
    facts = gather_repo_ref_facts(repo)
    for branch in facts["local_branches"]:
        if classify_ref(branch, reg).owner == "pipeline":
            continue
        if any(branch.startswith(p) for p in _pipeline_prefixes(reg)):
            findings.append(
                {
                    "code": "pipeline_branch_naming_drift",
                    "branch": branch,
                    "human_required": False,
                }
            )

    if execute_prune:
        # `git worktree prune` only garbage-collects admin records for worktrees
        # whose working directory is already gone; it cannot touch a ref.
        _git(repo, "worktree", "prune")

    return {"status": "reconciled", "partition": partition, "findings": findings}


def apply_self_heal_deletions(
    repo: Path,
    partition: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Delete ONLY the refs the partition marked ``deletable_by_self_heal``,
    using ``git branch -d`` (lowercase) so git's own merged-check is a second
    gate behind our merged-check. Opt-in; not called by the top-of-loop hook by
    default.

    Runtime guard: every candidate is re-classified and re-checked against the
    registry before deletion; a candidate that is NOT provably pipeline-owned +
    deletable is refused (``refused``), so even a caller bug cannot delete a
    human ref. This is the NEVER-DELETE-HUMAN-REFS invariant enforced a second
    time at the mutation boundary.
    """
    reg = load_ref_classes() if registry is None else registry
    deleted: list[str] = []
    refused: list[str] = []
    if not available(reg):
        return {"deleted": deleted, "refused": list(partition.get("deletable_by_self_heal", []))}
    assert reg is not None
    for name in partition.get("deletable_by_self_heal", []):
        cls = classify_ref(name, reg)
        if not (cls.owner == "pipeline" and cls.deletable_by_self_heal):
            refused.append(name)
            continue
        result = _git(repo, "branch", "-d", name)
        (deleted if result.returncode == 0 else refused).append(name)
    return {"deleted": deleted, "refused": refused}
