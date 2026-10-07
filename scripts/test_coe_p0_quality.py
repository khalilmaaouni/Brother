#!/usr/bin/env python3
"""Tests for coe_p0_quality.py (P0.5).

Run directly:
    python3 -B scripts/test_coe_p0_quality.py -v
"""

import unittest

import coe_p0_quality


class _HostileInt(int):
    """An int subclass whose every comparison raises, so a guard that ran
    a comparison on it instead of checking its TYPE would crash."""

    def __lt__(self, other):
        raise RuntimeError("hostile comparison")

    def __le__(self, other):
        raise RuntimeError("hostile comparison")

    def __gt__(self, other):
        raise RuntimeError("hostile comparison")

    def __ge__(self, other):
        raise RuntimeError("hostile comparison")

    def __eq__(self, other):
        raise RuntimeError("hostile comparison")

    def __ne__(self, other):
        raise RuntimeError("hostile comparison")


class _HostileObject(object):
    """An object whose bool, repr, str and eq all raise, so any guard that
    inspected the value instead of checking its TYPE would crash."""

    def __bool__(self):
        raise RuntimeError("hostile bool")

    def __repr__(self):
        raise RuntimeError("hostile repr")

    def __str__(self):
        raise RuntimeError("hostile str")

    def __eq__(self, other):
        raise RuntimeError("hostile eq")

    def __hash__(self):
        raise RuntimeError("hostile hash")


class QualityGateCheckTests(unittest.TestCase):
    def test_probe_clean_true_passes(self):
        self.assertTrue(coe_p0_quality.quality_gate_check(True))

    def test_probe_failure_blocks(self):
        # M-QUALITY-SKIP: a probe failure must not pass.
        self.assertFalse(coe_p0_quality.quality_gate_check(False))

    def test_empty_probe_result_refused(self):
        with self.assertRaises(ValueError):
            coe_p0_quality.quality_gate_check(None)

    def test_corrupt_probe_log_refused(self):
        with self.assertRaises(ValueError):
            coe_p0_quality.quality_gate_check("corrupt probe log")

    def test_non_bool_number_refused(self):
        for bad in (0, 1, 1.0, float("nan")):
            with self.assertRaises(ValueError):
                coe_p0_quality.quality_gate_check(bad)

    def test_hostile_object_refused_not_crashed(self):
        # Red team finding class blp_hostile_object: an object whose
        # bool, repr, str and eq all raise must still be REFUSED with the
        # module's own ValueError, never let its hostile dunder reach the
        # error path as a raw interpreter exception.
        with self.assertRaises(ValueError):
            coe_p0_quality.quality_gate_check(_HostileObject())


class BatchLandingPlanTests(unittest.TestCase):
    def test_zero_pending_reports_done_without_reland(self):
        plan = coe_p0_quality.batch_landing_plan(0)
        self.assertIn("done", plan.lower())
        self.assertIn("no reland needed", plan.lower())

    def test_positive_pending_returns_queue_order(self):
        plan = coe_p0_quality.batch_landing_plan(3)
        self.assertIn("queue order", plan)
        self.assertIn("batch 1: 2 units", plan)
        self.assertIn("batch 2: 1 unit", plan)

    def test_negative_pending_refused(self):
        with self.assertRaises(ValueError):
            coe_p0_quality.batch_landing_plan(-1)

    def test_non_int_pending_refused(self):
        for bad in (None, "2", 2.5, True):
            with self.assertRaises(ValueError):
                coe_p0_quality.batch_landing_plan(bad)

    def test_hostile_int_subclass_refused_not_crashed(self):
        # Red team finding blp_hostile_int_subclass: an int subclass whose
        # comparisons raise must be REFUSED by TYPE, never let its hostile
        # __lt__ reach a comparison and reach the caller as RuntimeError.
        with self.assertRaises(ValueError):
            coe_p0_quality.batch_landing_plan(_HostileInt(3))

    def test_hostile_object_refused_not_crashed(self):
        with self.assertRaises(ValueError):
            coe_p0_quality.batch_landing_plan(_HostileObject())

    def test_concurrent_batch_plans_merged_by_queue_order(self):
        plan = coe_p0_quality.batch_landing_plan(5)
        self.assertLess(plan.index("batch 1"), plan.index("batch 2"))
        self.assertLess(plan.index("batch 2"), plan.index("batch 3"))

    def test_landing_slot_and_next_build_named(self):
        plan = coe_p0_quality.batch_landing_plan(1)
        self.assertIn("5 to 8 minutes", plan)
        self.assertIn("next build starts during landing", plan)


if __name__ == "__main__":
    unittest.main()
