#!/usr/bin/env python3
"""The grade line keeps the exception under a failing test, and the re-brief keeps the whole grade line (owner 2026-10-01).

Run: python3 -B scripts/loop/test_grade_tail_keeps_assertion.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grade_build as G  # noqa: E402

UNITTEST_OUT = """.F.
======================================================================
FAIL: test_no_silent_failure_in_scripts_loop (__main__.Lint.test_no_silent_failure_in_scripts_loop)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/repo/scripts/test_lint.py", line 40, in test_no_silent_failure_in_scripts_loop
    self.assertEqual(found, [])
AssertionError: Lists differ: ['scripts/loop/unit_runner.py:410: bare except'] != []
----------------------------------------------------------------------
Ran 27 tests in 2.587s

FAILED (failures=1)
"""


class FailureTail(unittest.TestCase):
    def test_the_assertion_line_survives(self):
        t = G.failure_tail(UNITTEST_OUT)
        self.assertIn("AssertionError: Lists differ: ['scripts/loop/unit_runner.py:410: bare except']", t)
        self.assertIn("FAIL: test_no_silent_failure_in_scripts_loop", t)

    def test_other_exception_kinds_survive(self):
        for line in ("KeyError: 'unit'", "json.decoder.JSONDecodeError: Expecting value", "    ValueError: bad"):
            self.assertIn(line.strip(), G.failure_tail("Traceback (most recent call last):\n" + line + "\n"))

    def test_the_tail_is_bounded(self):
        many = "\n".join("AssertionError: " + "x" * 500 for _ in range(40))
        t = G.failure_tail(many)
        self.assertLessEqual(len(t), 1500)
        self.assertTrue(t.startswith("AssertionError: "))

    def test_no_match_keeps_the_last_300_chars(self):
        out = "noise " * 200
        self.assertEqual(G.failure_tail(out), out[-300:])

    def test_empty_or_none_is_empty(self):
        self.assertEqual(G.failure_tail(""), "")
        self.assertEqual(G.failure_tail(None), "")

    def test_run_uses_failure_tail(self):
        src = open(os.path.join(HERE, "grade_build.py"), encoding="utf-8").read()
        self.assertTrue(re.search(r"^\s*tail = failure_tail\(out\)\s*$", src, re.M), "run() must take the tail whole from failure_tail")
        self.assertNotIn('if l.startswith(("FAIL", "ERROR", "Ran", "FAILED", "Traceback"', src)


class ReBriefKeepsTheWholeGradeLine(unittest.TestCase):
    def test_the_grade_line_cut_holds_the_whole_tail(self):
        src = open(os.path.join(HERE, "unit_runner.py"), encoding="utf-8").read()
        cap = int(re.search(r"^GRADE_LINE_MAX = (\d+)", src, re.M).group(1))
        self.assertGreaterEqual(cap, 1500 + len("GREEN-WITH-CODE    NO exit 1: "))
        self.assertIn("l[:GRADE_LINE_MAX] for l in open(g", src)


if __name__ == "__main__":
    unittest.main()
