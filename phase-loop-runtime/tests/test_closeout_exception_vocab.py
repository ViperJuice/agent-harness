"""PROTO (roadmap v40) — closeout-exception vocabulary freeze.

Asserts the frozen models.py surface that GATE/BREAKGLASS import: the sensitivity
taxonomy, the closeout-exception kinds, the CloseoutException record field set, the
new closeout_scope_violation blocker literal, and the deny-by-default rule stated
in the canonical protocol.
"""

from dataclasses import fields
import unittest

import phase_loop_runtime.models as m

# TESTDECOUPLE (runtime-core): the canonical protocol.md is the runtime's OWN
# contract doc; it ships as _contract_docs package-data and resolves via
# importlib.resources, so this vocabulary-freeze test runs standalone.
from _contract_docs import contract_doc_text


class CloseoutExceptionVocabTest(unittest.TestCase):
    def test_sensitivity_classes_cover_safe_and_unsafe_members(self):
        self.assertEqual(
            set(m.SAFE_SENSITIVITY_CLASSES),
            {"docs", "plans", "handoffs", "config_nonsource"},
        )
        self.assertEqual(
            set(m.UNSAFE_SENSITIVITY_CLASSES),
            {"source", "ci", "secrets", "lockfile"},
        )
        # The flat taxonomy is exactly the union of SAFE and UNSAFE.
        self.assertEqual(
            set(m.SENSITIVITY_CLASSES),
            set(m.SAFE_SENSITIVITY_CLASSES) | set(m.UNSAFE_SENSITIVITY_CLASSES),
        )
        # SAFE and UNSAFE are disjoint.
        self.assertEqual(
            set(m.SAFE_SENSITIVITY_CLASSES) & set(m.UNSAFE_SENSITIVITY_CLASSES),
            set(),
        )

    def test_closeout_exception_kinds_are_soft_and_break_glass(self):
        self.assertEqual(m.CLOSEOUT_EXCEPTION_KINDS, ("soft", "break_glass"))

    def test_closeout_exception_record_field_set(self):
        self.assertEqual(
            {f.name for f in fields(m.CloseoutException)},
            {"paths", "exception_kind", "sensitivity_class", "reason", "verification_status"},
        )
        # The record validates its literals and round-trips to json.
        rec = m.CloseoutException(
            paths=("docs/readme.md",),
            exception_kind="soft",
            sensitivity_class="docs",
        )
        self.assertEqual(rec.verification_status, "passed")
        self.assertIn("exception_kind", rec.to_json())
        with self.assertRaises(ValueError):
            m.CloseoutException(paths=(), exception_kind="bogus", sensitivity_class="docs")
        with self.assertRaises(ValueError):
            m.CloseoutException(paths=(), exception_kind="soft", sensitivity_class="bogus")

    def test_closeout_exceptions_metadata_key(self):
        self.assertEqual(m.CLOSEOUT_EXCEPTIONS_METADATA_KEY, "closeout_exceptions")

    def test_scope_violation_blocker_literal_present(self):
        self.assertIn("closeout_scope_violation", m.BLOCKER_CLASSES)
        # The empty-reason break-glass case reuses the existing literal.
        self.assertIn("operator_override_missing_reason", m.BLOCKER_CLASSES)

    def test_protocol_states_deny_by_default(self):
        # The canonical protocol.md and its shared pointer are byte-identical (the
        # data-files copy is generated from shared/phase-loop/protocol.md); the
        # bundled _contract_docs copy is that same content. This asserts CONTENT
        # presence (not cross-copy drift), so resolving the single bundled copy is
        # equivalent and runs standalone.
        canonical = contract_doc_text("phase-loop", "protocol.md")
        self.assertIn("closeout exception", canonical.lower())
        # Deny-by-default: an unmatched path is UNSAFE. Stated in the canonical doc.
        lowered = canonical.lower()
        self.assertIn("deny-by-default", lowered)
        self.assertIn("unmatched", lowered)


if __name__ == "__main__":
    unittest.main()
