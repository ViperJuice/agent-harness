from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


AUDIT_STATUSES = ("passed", "drift_detected", "skipped")
MAX_CLAIMS = 32
MAX_DIFF_PATHS = 64

CLAIM_RE = re.compile(r"\b(?:Added|Created|Wrote|Updated)\s+`([^`]+)`", re.IGNORECASE)
BULLET_CLAIM_RE = re.compile(r"^\s*[-*]\s+[A-Za-z][A-Za-z0-9_-]*\s+`([^`]+)`", re.MULTILINE)
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


@dataclass(frozen=True)
class AuditResult:
    matched_claims: list[str]
    unmatched_claims: list[str]
    audit_status: str


def audit_closeout_evidence(commit_sha: str, phase_alias: str, repo_root: Path) -> AuditResult:
    commit_body = _git_output(repo_root, "log", "-1", "--format=%B", commit_sha)
    claims = _parse_claims(commit_body)
    if not claims:
        return AuditResult(matched_claims=[], unmatched_claims=[], audit_status="skipped")

    diff_paths = _diff_paths(repo_root, commit_sha)
    diff_body = _git_output(repo_root, "show", "--format=", "--unified=0", commit_sha)
    matched: list[str] = []
    unmatched: list[str] = []
    for claim in claims:
        if _claim_matches_evidence(claim, diff_paths, diff_body):
            matched.append(claim)
        else:
            unmatched.append(claim)

    return AuditResult(
        matched_claims=matched,
        unmatched_claims=unmatched,
        audit_status="drift_detected" if unmatched else "passed",
    )


def _parse_claims(text: str) -> list[str]:
    claims: list[str] = []
    for pattern in (CLAIM_RE, BULLET_CLAIM_RE):
        for match in pattern.finditer(text):
            claim = match.group(1).strip()
            if claim and claim not in claims:
                claims.append(claim)
            if len(claims) >= MAX_CLAIMS:
                return claims
    return claims


def _diff_paths(repo_root: Path, commit_sha: str) -> list[str]:
    names = _git_output(repo_root, "show", "--format=", "--name-only", commit_sha)
    paths: list[str] = []
    for line in names.splitlines():
        path = line.strip()
        if not path or path in paths:
            continue
        paths.append(path)
        if len(paths) >= MAX_DIFF_PATHS:
            break
    return paths


def _claim_matches_evidence(claim: str, diff_paths: list[str], diff_body: str) -> bool:
    normalized = claim.strip().lstrip("/")
    if not normalized:
        return False
    normalized_posix = normalized.replace("\\", "/")
    claim_path = PurePosixPath(normalized_posix)
    claim_basename = claim_path.name
    claim_stem = claim_path.stem
    for path in diff_paths:
        path_posix = path.replace("\\", "/")
        path_path = PurePosixPath(path_posix)
        if path_posix.endswith(normalized_posix) or path_posix.endswith(f"/{normalized_posix}"):
            return True
        if claim_basename and claim_basename == path_path.name:
            return True
        if claim_stem and claim_stem == path_path.stem:
            return True
    return any(identifier in diff_body for identifier in _identifier_candidates(claim))


def _identifier_candidates(claim: str) -> list[str]:
    candidates: list[str] = []
    for token in IDENTIFIER_RE.findall(claim):
        if len(token) < 4:
            continue
        if not any(char.isupper() for char in token) and "_" not in token:
            continue
        if token not in candidates:
            candidates.append(token)
    return candidates


def _git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout
