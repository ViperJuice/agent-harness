"""GATE (roadmap v40) — sensitivity classifier (IF-0-GATE-1).

`classify_unowned_path` maps a repo-relative path to a SensitivityVerdict per the
PROTO SENSITIVITY_CLASSES taxonomy. Precedence is load-bearing: UNSAFE-specific
patterns (secrets, lockfile, ci) win over broad SAFE rules; tests are UNSAFE;
config_nonsource is a tight allowlist; unmatched is deny-by-default UNSAFE.
"""

import unittest

import phase_loop_runtime.models as m
from phase_loop_runtime.closeout_classifier import classify_unowned_path, SensitivityVerdict


class CloseoutClassifierTest(unittest.TestCase):
    def _cls(self, path):
        return classify_unowned_path(path).sensitivity_class

    def test_safe_classes(self):
        for path in ("README.md", "docs/guide.md", "docs/sub/x.md", "notes.rst"):
            v = classify_unowned_path(path)
            self.assertEqual(v.sensitivity_class, "docs", path)
            self.assertTrue(v.safe, path)
        self.assertEqual(self._cls("plans/phase-plan-v40-GATE.md"), "plans")
        self.assertTrue(classify_unowned_path("plans/p.md").safe)
        self.assertEqual(self._cls(".dev-skills/handoffs/codex-execute-phase/latest.md"), "handoffs")
        self.assertTrue(classify_unowned_path(".dev-skills/handoffs/x/latest.md").safe)

    def test_config_nonsource_is_a_tight_allowlist(self):
        for path in (".gitignore", ".editorconfig", "setup.cfg", "tox.ini"):
            v = classify_unowned_path(path)
            self.assertEqual(v.sensitivity_class, "config_nonsource", path)
            self.assertTrue(v.safe, path)

    def test_source_is_unsafe(self):
        for path in ("ai_stack/router/models.py", "scripts/run.sh", "src/app.ts", "weird.bin", "unknown.xyz", "Makefile"):
            v = classify_unowned_path(path)
            self.assertFalse(v.safe, path)
            self.assertIn(v.sensitivity_class, m.UNSAFE_SENSITIVITY_CLASSES, path)

    def test_txt_is_docs_only_under_docs_dir(self):
        # A bare .txt is NOT auto-docs (source-adjacent text); only docs are SAFE.
        self.assertFalse(classify_unowned_path("src/foreign.txt").safe)
        self.assertEqual(classify_unowned_path("docs/notes.txt").sensitivity_class, "docs")
        self.assertTrue(classify_unowned_path("docs/notes.txt").safe)

    def test_tests_are_unsafe(self):
        for path in ("tests/test_x.py", "tests/queue/test_db_migrations.py", "pkg/__tests__/a.test.ts", "test_top.py"):
            v = classify_unowned_path(path)
            self.assertFalse(v.safe, path)
            self.assertEqual(v.sensitivity_class, "source", path)

    def test_precedence_unsafe_specific_beats_broad_safe(self):
        # CI config files are not docs/config_nonsource — they are ci (UNSAFE).
        self.assertEqual(self._cls(".github/workflows/release.yml"), "ci")
        self.assertFalse(classify_unowned_path(".github/workflows/release.yml").safe)
        # Secrets win regardless of suffix.
        for path in (".env", ".env.production", "deploy/server.pem", "secrets/token.txt"):
            self.assertEqual(self._cls(path), "secrets", path)
            self.assertFalse(classify_unowned_path(path).safe, path)
        # Lockfiles.
        for path in ("package-lock.json", "uv.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock"):
            self.assertEqual(self._cls(path), "lockfile", path)
            self.assertFalse(classify_unowned_path(path).safe, path)
        # A .toml/.yaml/.json is NOT auto-SAFE config — it is source (UNSAFE).
        for path in ("pyproject.toml", "config/nodes.toml", "settings.yaml", "data.json"):
            self.assertFalse(classify_unowned_path(path).safe, path)

    def test_deny_by_default_unmatched_is_unsafe(self):
        for path in ("", "no_extension", "a/b/c.weirdext", "binaryblob"):
            self.assertFalse(classify_unowned_path(path).safe, path)

    def test_verdict_safe_matches_taxonomy(self):
        for path in ("docs/a.md", "plans/p.md", ".gitignore", "src/a.py", "tests/t.py", ".env", "uv.lock", ".github/x.yml", "weird.bin"):
            v = classify_unowned_path(path)
            self.assertIn(v.sensitivity_class, m.SENSITIVITY_CLASSES, path)
            self.assertEqual(v.safe, v.sensitivity_class in m.SAFE_SENSITIVITY_CLASSES, path)

    def test_verdict_is_frozen_dataclass(self):
        v = SensitivityVerdict(sensitivity_class="docs", safe=True)
        with self.assertRaises(Exception):
            v.safe = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
