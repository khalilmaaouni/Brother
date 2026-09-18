#!/usr/bin/env python3
"""Calibration for scripts/evidence_obligation_registry.py.

The property this file exists to assert is not that the registry has the
right shape, it is that DOM-10.03's deciding property cannot regress
silently: an unknown change type must never be accepted under the
weakest (OPTIONAL) obligation, and a known change type's required
evidence kind must never be satisfied by a bare passing exit code (a
"kind" outside claim_discriminativeness.EVIDENCE_KINDS, or no evidence
at all). A test suite that only checked the happy path would pass right
through a change that quietly relaxed either rule, which is why every
test below names the bad state a green run would otherwise also pass.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claim_discriminativeness  # noqa: E402
import evidence_obligation  # noqa: E402
import evidence_obligation_registry as reg  # noqa: E402
import orchestrator_invariants  # noqa: E402


class TestVocabularyReuse(unittest.TestCase):
    def test_change_types_is_task_classes(self):
        # Bad state a green check would also pass: a silently forked
        # copy of TASK_CLASSES that happens to match today but drifts
        # the next time either list is edited. Identity, not just an
        # equal-looking literal, is what "reused, never retyped" means.
        self.assertIs(reg.CHANGE_TYPES, orchestrator_invariants.TASK_CLASSES)

    def test_evidence_kinds_is_claim_discriminativeness(self):
        self.assertIs(reg.EVIDENCE_KINDS, claim_discriminativeness.EVIDENCE_KINDS)

    def test_obligation_levels_matches_evidence_obligation(self):
        self.assertEqual(reg.OBLIGATION_LEVELS, frozenset(evidence_obligation.LEVELS))

    def test_registry_covers_exactly_the_known_change_types(self):
        self.assertEqual(frozenset(reg._REGISTRY), reg.CHANGE_TYPES)

    def test_every_registry_entry_uses_known_vocabulary(self):
        for change_type, (level, required) in reg._REGISTRY.items():
            self.assertIn(level, reg.OBLIGATION_LEVELS)
            self.assertTrue(required <= reg.EVIDENCE_KINDS)


class TestRequiredEvidence(unittest.TestCase):
    def test_known_type_returns_its_entry(self):
        self.assertEqual(reg.required_evidence("test"), ("REQUIRED_FOR_MERGE", frozenset(("red_before",))))

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            reg.required_evidence("not-a-real-change-type")

    def test_unhashable_type_raises_valueerror_not_typeerror(self):
        # A caller passing a list instead of a string must see the same
        # ValueError as any other unknown type, not an uncaught TypeError
        # from the dict lookup underneath.
        with self.assertRaises(ValueError):
            reg.required_evidence(["implementation"])


class TestEvidenceKindsPresent(unittest.TestCase):
    def test_empty_and_none_both_give_empty_set(self):
        self.assertEqual(reg.evidence_kinds_present([]), frozenset())
        self.assertEqual(reg.evidence_kinds_present(None), frozenset())

    def test_known_kind_is_collected(self):
        items = [{"kind": "red_before", "note": "x"}]
        self.assertEqual(reg.evidence_kinds_present(items), frozenset(("red_before",)))

    def test_unknown_kind_is_dropped_not_raised(self):
        # The bad state: "exit-code-only" is not a discriminating kind
        # (it is not in EVIDENCE_KINDS), so it must be dropped, never
        # silently treated as if it were red_before or mutation.
        items = [{"kind": "exit-code-only", "note": "the check passed"}]
        self.assertEqual(reg.evidence_kinds_present(items), frozenset())

    def test_malformed_item_does_not_crash(self):
        items = ["not a dict", 42, {"no_kind_key": True}, {"kind": "red_before"}]
        self.assertEqual(reg.evidence_kinds_present(items), frozenset(("red_before",)))


class TestEvaluateChange(unittest.TestCase):
    def test_empty_requirement_always_accepts(self):
        for change_type in ("planning", "research", "documentation", "review", "architecture"):
            accepted, _ = reg.evaluate_change(change_type, [])
            self.assertTrue(accepted, "%r should accept with no evidence" % change_type)
            accepted, _ = reg.evaluate_change(change_type, [{"kind": "nonsense"}])
            self.assertTrue(accepted, "%r should accept regardless of evidence" % change_type)

    def test_missing_required_kind_is_blocked(self):
        accepted, reason = reg.evaluate_change("test", [])
        self.assertFalse(accepted)
        self.assertIn("red_before", reason)

    def test_bare_exit_code_evidence_does_not_satisfy_requirement(self):
        # The deciding property's own headline case: "generic tests-
        # passed evidence is refused". An evidence item that only claims
        # the check exited 0, with no recognised discriminating kind,
        # must not satisfy a non-empty requirement.
        items = [{"kind": "exit-code-only", "note": "pytest exited 0"}]
        accepted, reason = reg.evaluate_change("implementation", items)
        self.assertFalse(accepted)
        self.assertIn("mutation", reason)

    def test_present_required_kind_accepts(self):
        items = [{"kind": "red_before", "note": "failed before the fix"}]
        accepted, _ = reg.evaluate_change("test", items)
        self.assertTrue(accepted)

    def test_partial_required_kinds_still_blocked(self):
        # implementation requires both mutation and red_before: having
        # only one of the two must still block, not be read as "close
        # enough".
        items = [{"kind": "red_before", "note": "x"}]
        accepted, reason = reg.evaluate_change("implementation", items)
        self.assertFalse(accepted)
        self.assertIn("mutation", reason)

    def test_unknown_change_type_raises_never_accepts(self):
        # The rule the whole unit exists to enforce: unknown blocks, it
        # never defaults to the weakest (OPTIONAL) obligation, which
        # would silently accept anything.
        with self.assertRaises(ValueError):
            reg.evaluate_change("not-a-real-change-type", [{"kind": "mutation"}])


class TestMainCli(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_accepted_exits_zero(self):
        path = self._write(
            "ok.json",
            '{"change_type": "test", "evidence": [{"kind": "red_before"}]}',
        )
        self.assertEqual(reg.main([path]), 0)

    def test_blocked_missing_evidence_exits_one(self):
        path = self._write("blocked.json", '{"change_type": "test", "evidence": []}')
        self.assertEqual(reg.main([path]), 1)

    def test_unknown_change_type_exits_one(self):
        path = self._write(
            "unknown.json",
            '{"change_type": "not-a-real-change-type", "evidence": []}',
        )
        self.assertEqual(reg.main([path]), 1)

    def test_missing_file_is_no_data_exit_two(self):
        self.assertEqual(reg.main([os.path.join(self.tmpdir, "missing.json")]), 2)

    def test_bad_json_is_no_data_exit_two(self):
        path = self._write("bad.json", "{not valid json")
        self.assertEqual(reg.main([path]), 2)

    def test_wrong_shape_is_no_data_exit_two(self):
        path = self._write("wrong.json", '{"change_type": 5, "evidence": []}')
        self.assertEqual(reg.main([path]), 2)


if __name__ == "__main__":
    unittest.main()
