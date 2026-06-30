"""CANON / IF-0-CANON-2 — hard parity gate for the committed skill bundle.

Asserts the committed ``phase-loop-skills/`` is byte-identical to a fresh
``build_bundle(<canonical skills-src sources>)``. An edit to a skill source
under ``skills-src/`` that is not followed by
``scripts/regenerate_skills_bundle.py`` cannot ship a stale committed bundle with
green CI, and a hand-edit to ``phase-loop-skills/`` that diverges from the sources
is likewise caught.

This is the upstream half of the bundle pipeline (the downstream
``phase-loop-skills/`` -> packaged ``skills_bundle/`` half is guarded by
``test_skills_bundle_drift.py``).

SELF-CONTAINED: reads only in-repo paths (``skills-src/`` + ``phase-loop-skills/``),
no dotfiles checkout. It is intentionally NOT marked ``dotfiles_integration`` so it
runs on every CI lane. It IS skipped in the standalone-from-wheel clean-room, where
the sibling sources are isolated away (same gate the sync-drift test uses).

README NOTE: ``phase-loop-skills/README.md`` is a hand-authored bundle index, NOT
``build_bundle`` output, so it is excluded from the byte-for-byte comparison
below. Everything ``build_bundle`` *does* own (every ``<skill>/`` tree) is
compared exactly.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.build_bundle import DEFAULT_SOURCES, build_bundle

PKG = Path(__file__).resolve().parents[1]      # phase-loop-runtime/
REPO = PKG.parent                              # agent-harness/
SKILLS_SRC = {harness: REPO / rel for harness, rel in DEFAULT_SOURCES.items()}
COMMITTED_BUNDLE = REPO / "phase-loop-skills"

# Hand-authored, not build_bundle output. Excluded from parity (see module docstring).
NON_GENERATED = {"README.md"}


def _bundle_files(root: Path) -> dict[Path, Path]:
    out: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(root)
        if rel.as_posix() in NON_GENERATED:
            continue
        out[rel] = path
    return out


class SkillsCanonParityTest(unittest.TestCase):
    def _require_sources(self) -> None:
        if not all(root.is_dir() for root in SKILLS_SRC.values()):
            self.skipTest("canonical skills-src/ sources absent (from-wheel layout)")
        if not COMMITTED_BUNDLE.is_dir():
            self.skipTest("committed phase-loop-skills/ absent (from-wheel layout)")

    def test_committed_bundle_is_byte_identical_to_canonical_build(self):
        self._require_sources()
        with tempfile.TemporaryDirectory() as td:
            fresh_root = Path(td) / "phase-loop-skills"
            result = build_bundle(SKILLS_SRC, fresh_root, dry_run=False, apply=True, force=True)
            self.assertEqual(
                [s.skill for s in result.skills_skipped],
                [],
                "build_bundle skipped skills; a canonical source root is missing a SKILL.md",
            )

            committed = _bundle_files(COMMITTED_BUNDLE)
            fresh = _bundle_files(fresh_root)
            self.assertEqual(
                set(committed),
                set(fresh),
                "phase-loop-skills/ file set drifted from build_bundle(skills-src/); "
                "run scripts/regenerate_skills_bundle.py",
            )
            for rel, cpath in committed.items():
                with self.subTest(path=str(rel)):
                    self.assertEqual(
                        cpath.read_bytes(),
                        fresh[rel].read_bytes(),
                        f"phase-loop-skills/{rel} drifted from its skills-src source; "
                        "run scripts/regenerate_skills_bundle.py",
                    )

    def test_regenerate_script_is_a_noop_on_committed_tree(self):
        """The documented one-command regenerate must rewrite no SKILL.md / override
        on a parity-clean tree (the script is wired correctly and reaches parity).

        NB: ``BuildResult.files_written`` is NOT a clean would-change signal for the
        aux subdirs (``scripts/``/``references/``/``assets/``) — ``build_bundle``
        appends those paths unconditionally whether or not their content changed. The
        sound no-op signals are ``skills_regenerated`` (base ``SKILL.md`` rewrites) and
        ``overrides_written`` (per-harness override rewrites); aux-content drift is
        covered byte-for-byte by ``test_committed_bundle_is_byte_identical_to_canonical_build``.
        """
        self._require_sources()
        import importlib.util

        script = PKG / "scripts" / "regenerate_skills_bundle.py"
        spec = importlib.util.spec_from_file_location("regenerate_skills_bundle_under_test", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.regenerate(dry_run=True)
        self.assertEqual(
            (result.skills_regenerated, result.overrides_written),
            ([], []),
            "regenerate_skills_bundle.py --dry-run would rewrite a SKILL.md/override; "
            "committed phase-loop-skills/ is stale vs skills-src/ — run the regenerate",
        )

    def test_installed_bundle_preserves_concrete_harness_literals(self):
        """CR blind-spot gate: parity (committed == build) does NOT catch a
        neutralizer that corrupts concrete harness literals — both sides agree on
        the *corrupt* token. This asserts the INSTALLED body instead.

        ``_neutralize_skill`` collapses harness-VARIANT tokens (skill names,
        config dirs) to ``<harness>-`` and there is NO install-time body
        re-expansion (``skill_install._rewrite_skill_name`` only touches the
        ``name:`` frontmatter). So a concrete Claude identifier that leaked into
        the neutralizer would install literally as ``<harness>-opus-4-8`` /
        ``<harness>-in-chrome`` — which denote nothing — a content regression in
        released fleet skills. Assert the real literals survive, and that no
        ``<harness>-``-corrupted form of a preserved literal remains.

        NB: skill-name refs such as ``<harness>-execute-phase`` in the installed
        body are BY DESIGN (the shared base is harness-neutral); this gate must
        NOT assert anything about those.
        """
        self._require_sources()
        import tempfile

        from phase_loop_runtime.build_bundle import PRESERVE_LITERALS
        from phase_loop_runtime.skill_install import install_skills

        # claude carries every preserved literal (model ids + claude-in-chrome).
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            install_skills(
                harness="claude",
                source=COMMITTED_BUNDLE,
                destination=dest,
                mode="copy",
                apply=True,
            )
            installed = "\n".join(
                p.read_text(encoding="utf-8") for p in sorted(dest.rglob("SKILL.md"))
            )
        for literal in PRESERVE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    installed,
                    f"installed claude bundle lost concrete literal {literal!r} "
                    "(neutralizer corrupted it to a <harness>- form)",
                )
                # The corrupted form depends on the literal's shape: a `claude-X`
                # token (model id / claude-in-chrome) collapses to `<harness>-X`,
                # while a `Claude X` brand-display form (the Co-Authored-By model
                # attribution) collapses to `Harness X` via the brand regex.
                if literal.startswith("claude-"):
                    corrupted = literal.replace("claude-", "<harness>-", 1)
                else:
                    corrupted = literal.replace("Claude", "Harness", 1)
                self.assertNotIn(
                    corrupted,
                    installed,
                    f"installed claude bundle contains corrupted {corrupted!r}; "
                    f"the literal {literal!r} must survive neutralization verbatim",
                )

    def test_neutralize_preserves_concrete_literals_but_collapses_variant_tokens(self):
        """Unit contract on ``_neutralize_skill``: concrete Claude identifiers
        survive verbatim; harness-VARIANT tokens (skill names, config dirs, brand)
        still collapse to ``<harness>``. Locks the preserve-vs-collapse boundary
        directly, independent of the full bundle round-trip."""
        from phase_loop_runtime.build_bundle import _neutralize_skill

        sample = (
            "Run `claude-execute-phase`; route via `claude-config/claude-skills`. "
            "Models: claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5. "
            "Screenshot with claude-in-chrome. Claude Code drives it."
        )
        out = _neutralize_skill(sample, harness="claude", skill="execute-phase")
        # Concrete literals preserved verbatim.
        for literal in ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-in-chrome"):
            self.assertIn(literal, out, f"{literal} must survive neutralization")
        # Harness-variant tokens collapsed.
        self.assertIn("<harness>-execute-phase", out)
        self.assertIn("<harness>-config", out)
        self.assertNotIn("claude-execute-phase", out)
        self.assertNotIn("claude-config", out)
        # Brand prose collapsed; but never a corrupted model/tool form.
        self.assertNotIn("<harness>-opus-4-8", out)
        self.assertNotIn("<harness>-in-chrome", out)

    def test_other_harnesses_do_not_leak_claude_only_skillname_refs(self):
        """Guard the converse: the claude-specific *tool* literal (claude-in-chrome,
        a real cross-harness tool name with no per-harness variant) legitimately
        appears in every harness's shared base, but a claude *skill-name* ref must
        never leak un-neutralized into another harness's installed body."""
        self._require_sources()
        import tempfile

        for harness in ("codex", "gemini", "opencode"):
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td)
                install_skills_other = __import__(
                    "phase_loop_runtime.skill_install", fromlist=["install_skills"]
                ).install_skills
                install_skills_other(
                    harness=harness,
                    source=COMMITTED_BUNDLE,
                    destination=dest,
                    mode="copy",
                    apply=True,
                )
                installed = "\n".join(
                    p.read_text(encoding="utf-8") for p in sorted(dest.rglob("SKILL.md"))
                )
            with self.subTest(harness=harness):
                # No leaked claude skill-name prefix (a real cross-harness bug if present).
                self.assertNotIn(
                    "claude-execute-phase",
                    installed,
                    f"{harness} installed body leaked claude skill-name ref",
                )

    # ----------------------------------------------------------------------
    # PRESERVE_LITERALS enumeration backstop (#26 item 4).
    #
    # PRESERVE_LITERALS is a hardcoded tuple. A NEW concrete `claude-…` literal
    # (a future model id like `claude-haiku-5-0`, an `@anthropic-ai/claude-foo`
    # package, etc.) that lands in skills-src but is NOT added to the tuple would
    # be silently collapsed to `<harness>-…` by the neutralizer — a content
    # regression the parity gate cannot see (both committed + build agree on the
    # corrupt token). The install-output gate (test above) catches it ONLY for the
    # specific literals already enumerated. This lint is the missing backstop: it
    # flags any `claude-[a-z]` token in skills-src that is neither a known
    # harness-VARIANT token (skill names / config dirs that SHOULD collapse) nor a
    # preserved literal — i.e. a candidate new concrete literal a maintainer must
    # consciously triage (add to PRESERVE_LITERALS, or to the variant allowlist).
    # ----------------------------------------------------------------------

    # Harness-VARIANT stems: tokens that legitimately become `<harness>-…` per
    # harness (skill-name dirs, config/skill roots, bundle/code prefixes). A token
    # is "known-collapsible" iff it starts with one of these.
    _VARIANT_STEMS = (
        "claude-execute-",
        "claude-plan-",
        "claude-phase-",
        "claude-skill-",
        "claude-skills",
        "claude-task-",
        "claude-config",
        "claude-code-",
        "claude-bundle",
    )

    import re as _re

    _CLAUDE_TOKEN = _re.compile(r"claude-[a-z][a-z0-9-]*")

    def _claude_tokens_in_skills_src(self) -> set[str]:
        from phase_loop_runtime.build_bundle import PRESERVE_LITERALS

        preserved = {lit for lit in PRESERVE_LITERALS if lit.startswith("claude-")}
        unexpected: set[str] = set()
        for skill_md in sorted((SKILLS_SRC["claude"]).rglob("SKILL.md")):
            text = skill_md.read_text(encoding="utf-8")
            for tok in self._CLAUDE_TOKEN.findall(text):
                tok = tok.rstrip("-")  # `claude-skill-editor-` etc.
                if tok in preserved:
                    continue
                if any(tok.startswith(stem) for stem in self._VARIANT_STEMS):
                    continue
                unexpected.add(tok)
        return unexpected

    def test_no_unguarded_claude_literal_in_skills_src(self):
        """No `claude-…` token in skills-src outside the variant allowlist or
        PRESERVE_LITERALS. A failure means a new concrete Claude literal landed and
        must be triaged: add it to PRESERVE_LITERALS (so it survives neutralization)
        or extend ``_VARIANT_STEMS`` (if it is genuinely a per-harness variant)."""
        self._require_sources()
        unexpected = self._claude_tokens_in_skills_src()
        self.assertEqual(
            set(),
            unexpected,
            "unguarded claude-* literal(s) in skills-src/claude/: "
            f"{sorted(unexpected)} — add to PRESERVE_LITERALS in build_bundle.py "
            "(if a concrete literal that must survive) or to the variant allowlist "
            "in this test (if a per-harness skill/config token).",
        )

    def test_lint_fires_on_an_injected_unguarded_literal(self):
        """Negative control: the lint MUST catch a new concrete literal. Inject a
        fake `claude-frobnicate` into a temp skills-src/claude tree and assert the
        scan reports it (a lint that cannot fail is no backstop)."""
        self._require_sources()
        with tempfile.TemporaryDirectory() as td:
            fake_src = Path(td) / "claude" / "claude-execute-phase"
            fake_src.mkdir(parents=True)
            (fake_src / "SKILL.md").write_text(
                "Use the claude-frobnicate model for this step.\n", encoding="utf-8"
            )
            tokens = {
                tok.rstrip("-")
                for skill_md in fake_src.rglob("SKILL.md")
                for tok in self._CLAUDE_TOKEN.findall(
                    skill_md.read_text(encoding="utf-8")
                )
            }
            # The injected literal is neither a preserved literal nor a variant stem.
            self.assertIn("claude-frobnicate", tokens)
            self.assertFalse(
                any("claude-frobnicate".startswith(stem) for stem in self._VARIANT_STEMS),
                "test bug: the injected literal must not match a variant stem",
            )


if __name__ == "__main__":
    unittest.main()
