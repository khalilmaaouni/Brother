"""Tests for capability_precheck.py. Stdlib unittest only, no filesystem, no
network, no real host probe: every host report is injected as a plain dict
(or a deliberately wrong shape, for the NO-DATA cases).

Every case asserts BOTH the verdict and that the reason names the specific
capability and level involved, per the module's own contract: a refusal
that does not name what was missing is not a usable refusal.
"""
import unittest

import capability_precheck as cp


class CoreRuleTests(unittest.TestCase):
    def test_advisory_never_satisfies_enforced(self):
        verdict, reason = cp.precheck(
            ["enforceable_deny"], {"enforceable_deny": "ADVISORY"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("enforceable_deny", reason)
        self.assertIn("ADVISORY", reason)

    def test_enforced_satisfies_enforced(self):
        verdict, reason = cp.precheck(
            ["enforceable_deny"], {"enforceable_deny": "ENFORCED"})
        self.assertEqual(verdict, cp.DISPATCH)
        self.assertIn("enforceable_deny", reason)

    def test_unmentioned_capability_is_unknown_and_refused(self):
        verdict, reason = cp.precheck(
            ["workspace_isolation"], {"enforceable_deny": "ENFORCED"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("workspace_isolation", reason)
        self.assertIn("UNKNOWN", reason)


class EdgeCaseTests(unittest.TestCase):
    def test_unit_requiring_nothing_dispatches(self):
        verdict, reason = cp.precheck([], {"enforceable_deny": "ADVISORY"})
        self.assertEqual(verdict, cp.DISPATCH)
        self.assertIn("no capabilities", reason)

    def test_unit_requiring_nothing_dispatches_even_with_unprobed_host(self):
        # A requirement-free unit places no demand on the host, so a host
        # that could not be probed at all still cannot fail it: there is
        # nothing for its unreachability to fail.
        verdict, reason = cp.precheck([], None)
        self.assertEqual(verdict, cp.DISPATCH)

    def test_host_claims_level_above_what_it_can_prove(self):
        # A level string outside the known ladder (ENFORCED/ADVISORY) is
        # treated as UNKNOWN, never as a grant.
        verdict, reason = cp.precheck(
            ["network_control"], {"network_control": "CERTIFIED-BY-VENDOR"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("network_control", reason)
        self.assertIn("CERTIFIED-BY-VENDOR", reason)
        self.assertIn("UNKNOWN", reason)

    def test_capability_present_at_lower_level(self):
        verdict, reason = cp.precheck(
            ["resume"], {"resume": "ADVISORY", "workspace_isolation": "ENFORCED"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("resume", reason)
        self.assertIn("ADVISORY", reason)

    def test_duplicate_requirements_same_verdict_as_single(self):
        dup = cp.precheck(
            ["enforceable_deny", "enforceable_deny"],
            {"enforceable_deny": "ENFORCED"})
        single = cp.precheck(
            ["enforceable_deny"], {"enforceable_deny": "ENFORCED"})
        self.assertEqual(dup[0], single[0])
        self.assertEqual(dup, single)

    def test_duplicate_requirements_refused_once(self):
        verdict, reason = cp.precheck(
            ["enforceable_deny", "enforceable_deny"],
            {"enforceable_deny": "ADVISORY"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("enforceable_deny", reason)

    def test_empty_host_report_refuses_naming_first_missing(self):
        verdict, reason = cp.precheck(
            ["enforceable_deny", "workspace_isolation"], {})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("enforceable_deny", reason)
        self.assertIn("UNKNOWN", reason)

    def test_host_report_not_a_mapping_is_nodata(self):
        for bad_report in (None, "ENFORCED", ["enforceable_deny"], 42):
            with self.subTest(bad_report=bad_report):
                verdict, reason = cp.precheck(["enforceable_deny"], bad_report)
                self.assertEqual(verdict, cp.REFUSE)
                self.assertIn(cp.NODATA, reason)

    def test_multiple_requirements_all_enforced_dispatches(self):
        verdict, reason = cp.precheck(
            ["enforceable_deny", "workspace_isolation"],
            {"enforceable_deny": "ENFORCED", "workspace_isolation": "ENFORCED"})
        self.assertEqual(verdict, cp.DISPATCH)
        self.assertIn("enforceable_deny", reason)
        self.assertIn("workspace_isolation", reason)

    def test_first_failing_requirement_named_when_several_would_fail(self):
        # Order is preserved from the caller's `required` iterable, so the
        # reason is deterministic rather than dict-iteration-order luck.
        verdict, reason = cp.precheck(
            ["workspace_isolation", "enforceable_deny"],
            {"workspace_isolation": "ADVISORY", "enforceable_deny": "ADVISORY"})
        self.assertEqual(verdict, cp.REFUSE)
        self.assertIn("workspace_isolation", reason)


if __name__ == "__main__":
    unittest.main()
