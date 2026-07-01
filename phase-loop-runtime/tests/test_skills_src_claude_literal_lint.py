"""#26 item 4 — flag NEW un-classified ``claude-*`` literals in canonical skills-src.

The build-bundle neutralizer collapses harness-VARIANT ``claude-X`` tokens to
``<harness>-X`` and preserves only the concrete literals enumerated in
``build_bundle.PRESERVE_LITERALS``. A *new* concrete ``claude-X`` literal (a new
model id, an ``@anthropic-ai/...`` reference, a Claude-specific product token)
added to ``skills-src/`` would **silently collapse** into a ``<harness>-X`` form
that denotes nothing — until someone remembers to add it to ``PRESERVE_LITERALS``.
Today the only backstop is the install-output parity gate; this lint makes the
source itself fail-loud so the classification decision is explicit.

Every ``claude-*`` token in ``skills-src/`` must be one of:
  1. a preserved literal (``PRESERVE_LITERALS`` — survives neutralization), or
  2. a phase-loop skill name / derivative (``claude-<skill>...`` — SHOULD collapse), or
  3. a known intentional harness-variant infra token (``_INFRA_VARIANT_ALLOW``).
Anything else fails, forcing the author to classify it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from phase_loop_runtime.build_bundle import PRESERVE_LITERALS
from phase_loop_runtime.skill_install import REQUIRED_SKILLS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_SRC = _REPO_ROOT / "skills-src"
_TOKEN_RE = re.compile(r"claude-[a-z0-9][a-z0-9-]*")

# Known Claude-specific harness-VARIANT infra tokens that intentionally collapse
# to ``<harness>-X`` (Claude Code config/skills dirs, product names). Extend this
# only deliberately, with justification — the whole point of the lint is to force
# a conscious classification of every concrete claude-* token.
_INFRA_VARIANT_ALLOW = frozenset(
    {
        "claude-config",
        "claude-code",
        "claude-code-skills",
        "claude-skills",
        "claude-bundle",
    }
)


def _is_classified(token: str) -> bool:
    if token in PRESERVE_LITERALS:
        return True
    if token in _INFRA_VARIANT_ALLOW:
        return True
    for skill in REQUIRED_SKILLS:
        if token == f"claude-{skill}" or token.startswith(f"claude-{skill}"):
            return True
    return False


def test_no_unclassified_claude_literal_in_skills_src():
    if not _SKILLS_SRC.is_dir():
        pytest.skip("skills-src/ not present (from-wheel install); source-tree lint only")

    unknown: dict[str, str] = {}
    for md in sorted(_SKILLS_SRC.rglob("*.md")):
        for token in _TOKEN_RE.findall(md.read_text(encoding="utf-8")):
            if not _is_classified(token):
                unknown.setdefault(token, md.relative_to(_REPO_ROOT).as_posix())

    assert not unknown, (
        "#26 item 4: un-classified claude-* literal(s) in skills-src/ — these will "
        "SILENTLY collapse to <harness>-X on neutralization:\n"
        + "\n".join(f"  {tok}  (first seen: {path})" for tok, path in sorted(unknown.items()))
        + "\nClassify each: add to build_bundle.PRESERVE_LITERALS if it must survive "
        "neutralization (a real Claude-only literal), or to this test's "
        "_INFRA_VARIANT_ALLOW if it is an intentional harness-variant collapse."
    )
