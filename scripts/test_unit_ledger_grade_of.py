#!/usr/bin/env python3
"""unit_ledger.grade_of reads the grader's own exit 3 (no slot, no sandbox, a leg that never ran) as NO-DATA, never as a
FAIL the ledger and EV history would count as a failed round (review 2026-10-03).

Run: python3 -B scripts/test_unit_ledger_grade_of.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import unit_ledger as UL  # noqa: E402


class GradeOfExitThree(unittest.TestCase):
    def test_a_grader_fault_is_no_data(self):
        self.assertEqual(UL.grade_of("FAIL no machine slot within 600 s\nexit=3\n"), ("NO-DATA", ""))

    def test_a_no_data_verdict_is_no_data(self):
        self.assertEqual(UL.grade_of("GREEN-ON-OLD       NO-DATA: x\nNO-DATA: one version\nexit=3\n"), ("NO-DATA", ""))

    def test_control_a_real_fail_is_a_fail(self):
        self.assertEqual(UL.grade_of("FAIL: tests red\nexit=1\n")[0], "FAIL")

    def test_control_a_real_pass_is_a_pass(self):
        self.assertEqual(UL.grade_of("PASS\nexit=0\n"), ("PASS", ""))


if __name__ == "__main__":
    unittest.main()
