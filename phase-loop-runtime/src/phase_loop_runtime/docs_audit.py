"""Pipeline-independent docs-freshness audit (docs-freshness v4 P1, Layer A).

`phase-loop docs-audit --base <ref>` runs on a git diff ALONE — no `.phase-loop/`
state — so it is immune to the three #18 evasion paths (under-scoped docs lane,
direct-`Agent()` pipeline bypass, absent runtime helper). It classifies the changed
paths against the canonical `docs_surfaces` taxonomy and enforces the per-surface,
relevance-bound decision contract, emitting a `docs_freshness: passed|skipped|blocked`
report and a non-zero exit when a public surface changed without a satisfying decision.

This is the only *non-bypassable* control; the in-pipeline `docs_freshness` closeout
gate (shipped in v0.1.6) is the advisory early-feedback / content-token layer. This
backstop adds the piece that gate structurally cannot catch: the *silent-absence* case
— a release surface changed in the diff but its required doc was never updated.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import docs_surfaces as ds

#: git's well-known empty-tree object — the diff base for a first push / first tag,
#: where the whole tree is "new" and there is no prior ref to diff against.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class DiffUnavailable(RuntimeError):
    """A git diff could not be computed — the audit fails CLOSED, never a silent pass."""

#: Decisions an operator/executor can record per surface. Release-class is held to
#: the relevance binding regardless (a token never substitutes for the real doc).
DECISION_TOKENS = ("docs_updated", "no_doc_delta", "docs_follow_up_filed")
#: Repo-visible decision artifact, recoverable WITHOUT `.phase-loop/` state.
DEFAULT_DECISIONS_PATH = ".doc-decisions.json"
DOC_DECISIONS_SCHEMA_VERSION = 1


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def _rev_ok(repo: Path, ref: str) -> bool:
    return bool(_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"))


def _first_push_base(repo: Path) -> str:
    """Base for a first push / first tag — HEAD~1 if it exists, else the empty tree."""
    return "HEAD~1" if _rev_ok(repo, "HEAD~1") else _EMPTY_TREE


def resolve_base(repo: Path, base: str | None) -> tuple[str | None, str]:
    """Resolve the diff base across the CI contexts. Returns (base, context).

    Explicit `--base` wins. Otherwise: a PR (`GITHUB_BASE_REF`) → `origin/<base>`; a tag
    push (`GITHUB_REF=refs/tags/...`) → the prior tag (first tag → fallback base); a
    branch push → the push **before** SHA (`DOCS_AUDIT_PUSH_BEFORE`, set to
    `github.event.before`) so the WHOLE batch is diffed, never just the tip. An all-zeros
    `before` (first push to the ref) → fallback base.
    """
    if base:
        return base, "explicit"
    pr_base = os.environ.get("GITHUB_BASE_REF")
    if pr_base:
        return f"origin/{pr_base}", "pull_request"
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/"):
        prior = _git(repo, "describe", "--tags", "--abbrev=0", "HEAD^")
        if prior:
            return prior, "push_tag"
        return _first_push_base(repo), "push_tag_first"
    before = os.environ.get("DOCS_AUDIT_PUSH_BEFORE", "").strip()
    if before:
        if set(before) <= {"0"}:  # all-zeros sentinel → first push to this ref
            return _first_push_base(repo), "push_first"
        return before, "push"
    return "HEAD~1", "push"  # local / no-CI-env fallback


def _base_usable(repo: Path, base: str) -> bool:
    return base == _EMPTY_TREE or _rev_ok(repo, base)


def _diff_op(context: str) -> str:
    # PR / explicit: three-dot (changes vs the merge-base). push / tag: two-dot
    # (exactly the pushed commits — the correct semantics for a batched push range).
    return "..." if context in {"pull_request", "explicit"} else ".."


def changed_paths(repo: Path, base: str, op: str = "...", head: str = "HEAD") -> list[str]:
    """`git diff --name-only base<op>head`. Raises `DiffUnavailable` on a git error so
    the caller fails CLOSED — an empty result means "no changes", never "couldn't tell"."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", f"{base}{op}{head}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise DiffUnavailable(str(exc)) from exc
    if completed.returncode not in {0, 1}:
        raise DiffUnavailable(f"git diff exited {completed.returncode} for {base}{op}{head}")
    return [p for p in completed.stdout.splitlines() if p.strip()]


def load_decisions(repo: Path, path: str | None = None) -> dict[str, dict[str, Any]]:
    """Read the repo-visible decision artifact. Missing file → no decisions.

    Returns a mapping `surface-key -> {decision, reason, evidence}`. The key may be a
    glob/path matching changed surfaces, or a class name (`general`/`release`/`*`).
    """
    decisions_path = repo / (path or DEFAULT_DECISIONS_PATH)
    if not decisions_path.is_file():
        return {}
    try:
        data = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("decisions", []) or []:
        surface = str(entry.get("surface") or "").strip()
        decision = str(entry.get("decision") or "").strip()
        if surface and decision in DECISION_TOKENS:
            out[surface] = {
                "decision": decision,
                "reason": str(entry.get("reason") or ""),
                "evidence": tuple(entry.get("evidence") or ()),
            }
    return out


def _decision_for(path: str, klass: str, decisions: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for key in (path, klass, "*"):
        if key in decisions:
            return decisions[key]
    for key, value in decisions.items():
        if any(ch in key for ch in "*?[") and ds._match(path, key):
            return value
    return None


@dataclass
class AuditReport:
    docs_freshness: str  # "passed" | "skipped" | "blocked"
    findings: list[dict[str, Any]] = field(default_factory=list)
    surfaces: dict[str, list[str]] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0 if self.docs_freshness in {"passed", "skipped"} else 1

    def to_json(self) -> dict[str, Any]:
        return {
            "docs_freshness": self.docs_freshness,
            "findings": self.findings,
            "surfaces": self.surfaces,
            "evidence": self.evidence,
        }


def evaluate(changed: list[str], decisions: dict[str, dict[str, Any]]) -> AuditReport:
    """Apply the per-surface, relevance-bound decision contract to a changed-path set."""
    changed_docs = [p for p in changed if ds.is_doc_surface(p)]
    release_surfaces = [p for p in changed if ds.classify_surface(p) == "release" and not ds.is_doc_surface(p)]
    general_surfaces = [p for p in changed if ds.classify_surface(p) == "general" and not ds.is_doc_surface(p)]

    findings: list[dict[str, Any]] = []

    for surface in release_surfaces:
        required = ds.required_docs_for(surface)
        if required:
            relevant_changed = any(ds._match(d, pat) for d in changed_docs for pat in required)
        else:
            relevant_changed = bool(changed_docs)
        if not relevant_changed:
            findings.append({
                "surface": surface,
                "klass": "release",
                "code": "release_docs_unsatisfied",
                "reason": (
                    f"release-class surface `{surface}` changed but its required doc "
                    f"surface(s) {list(required) or ['any doc']} did not — a token or an "
                    f"unrelated doc edit does not satisfy a release surface"
                ),
            })

    for surface in general_surfaces:
        decision = _decision_for(surface, "general", decisions)
        if not (changed_docs or decision):
            findings.append({
                "surface": surface,
                "klass": "general",
                "code": "general_decision_missing",
                "reason": (
                    f"public surface `{surface}` changed with no doc change and no recorded "
                    f"doc decision; update a doc or record a doc decision (no_doc_delta + reason)"
                ),
            })

    surfaces = {
        "release": release_surfaces,
        "general": general_surfaces,
        "docs": changed_docs,
    }
    if not release_surfaces and not general_surfaces:
        return AuditReport("skipped", findings, surfaces, {"reason": "no public surfaces changed"})
    if findings:
        return AuditReport("blocked", findings, surfaces)
    return AuditReport("passed", findings, surfaces)


def run_audit(repo: Path, base: str | None = None, decisions_path: str | None = None) -> AuditReport:
    """Orchestrate the diff-driven audit. Un-evaluable input → `blocked` (never silent pass)."""
    resolved, context = resolve_base(repo, base)
    if not resolved or not _base_usable(repo, resolved):
        return AuditReport(
            "blocked",
            [{
                "surface": None,
                "klass": "audit",
                "code": "base_unresolved",
                "reason": (
                    f"could not resolve the diff base (`{base or '<auto>'}`, context={context}); "
                    f"the audit cannot evaluate freshness — failing closed (never a silent pass)"
                ),
            }],
            evidence={"base": resolved, "context": context},
        )
    try:
        changed = changed_paths(repo, resolved, _diff_op(context))
    except DiffUnavailable as exc:
        return AuditReport(
            "blocked",
            [{
                "surface": None,
                "klass": "audit",
                "code": "diff_unavailable",
                "reason": (
                    f"git diff failed (base=`{resolved}`, context={context}): {exc} — the audit "
                    f"cannot evaluate freshness, failing closed (never a silent pass)"
                ),
            }],
            evidence={"base": resolved, "context": context},
        )
    report = evaluate(changed, load_decisions(repo, decisions_path))
    report.evidence = {"base": resolved, "context": context, "changed_count": len(changed)}
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="phase-loop docs-audit")
    parser.add_argument("--base", help="Diff base ref (auto-resolved from CI env if omitted).")
    parser.add_argument("--repo", default=".", help="Repository root (default: cwd).")
    parser.add_argument("--decisions", help=f"Decision artifact path (default: {DEFAULT_DECISIONS_PATH}).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    # tolerate the leading 'docs-audit' token when dispatched from the main CLI
    args = parser.parse_args([a for a in (argv or []) if a != "docs-audit"])

    report = run_audit(Path(args.repo), args.base, args.decisions)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(f"docs_freshness: {report.docs_freshness}")
        for f in report.findings:
            print(f"  [{f['klass']}] {f.get('surface') or '-'}: {f['reason']}")
        if report.docs_freshness == "blocked":
            print(
                "\nRemediation: update the required doc surface(s), or record a doc decision in "
                f"{args.decisions or DEFAULT_DECISIONS_PATH} (release-class needs a real, relevant "
                "doc change — a token does not satisfy it)."
            )
    return report.exit_code
