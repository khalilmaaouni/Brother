#!/usr/bin/env python3
"""Runnable self-check for benchmarks/acceptance-time/run.py (roadmap row
S11's done_check): "a benchmark run reports, for each of the three arms,
reviewer time, correct accept or reject, defects found and unnecessary
lines inspected, on the same seeded diff; a missing arm reads NO-DATA and
the run refuses to report a comparison."

Every fixture this reads is synthetic and clearly labeled as such (see
fixtures/README.md); none of it is real reviewer data, and nothing here
ever asserts a real Acceptance Time result. Run: python3 test_run.py -v
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(HERE, "run.py")
FIXTURES = os.path.join(HERE, "fixtures")


def _score(csv_name):
    csv_path = os.path.join(FIXTURES, csv_name)
    return subprocess.run(
        [sys.executable, RUN_PY, "score", csv_path],
        capture_output=True, text=True, timeout=60)


class RefusesWithNoDataWhenArmsAreMissing(unittest.TestCase):
    """(a) zero, one, and two of three arms present: all refuse, none
    report a comparison."""

    def test_zero_arms_present_refuses(self):
        result = _score("synthetic_zero_arms_present.csv")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)
        for condition in ("raw_diff", "ordinary_summary", "brother_receipt"):
            self.assertNotIn("median", result.stdout,
                             "a zero-arm run must not print a median for "
                             "%s" % condition)

    def test_one_arm_present_refuses(self):
        result = _score("synthetic_one_arm_present.csv")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)
        self.assertIn("ordinary_summary", result.stdout)
        self.assertIn("brother_receipt", result.stdout)
        # The narrative read-out must never fire on a refusal: reporting
        # narrative text beside an incomplete comparison would look like
        # part of a result.
        self.assertNotIn("narrative (defects found", result.stdout)

    def test_two_arms_present_refuses(self):
        result = _score("synthetic_two_arms_present.csv")
        self.assertNotEqual(result.returncode, 0,
                            result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)
        self.assertIn("brother_receipt", result.stdout)
        self.assertNotIn("narrative (defects found", result.stdout)


class ReportsAllFourFieldsOnAFullySyntheticComparison(unittest.TestCase):
    """(b) all three arms present, fully synthetic and labeled: run.py
    score computes and prints all four fields per condition."""

    def test_all_three_arms_reports_time_correctness_and_narrative(self):
        result = _score("synthetic_all_three_arms.csv")
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)
        # Mechanical fields: median seconds and correctness rate, all three
        # conditions, same seeded changes (medium-feature / auth-security /
        # schema-migration are the CHANGES scripts/acceptance_time.py
        # names, all present in the fixture).
        for condition in ("raw_diff", "ordinary_summary", "brother_receipt"):
            self.assertIn("%s: median" % condition, result.stdout)
            self.assertIn("n=5", result.stdout)
        self.assertIn("correct 80%", result.stdout)   # raw_diff: 4/5 reject
        self.assertIn("correct 80%", result.stdout)   # ordinary_summary: 4/5
        self.assertIn("correct 100%", result.stdout)  # brother_receipt: 5/5
        # Narrative fields: read out per condition, verbatim, never
        # aggregated into a number.
        for condition in ("raw_diff", "ordinary_summary", "brother_receipt"):
            self.assertIn("%s narrative" % condition, result.stdout)
        self.assertIn("FABRICATED", result.stdout,
                     "the narrative read-out must carry the fixture's own "
                     "FABRICATED marker, proving it is read from the CSV, "
                     "not invented by the harness")


if __name__ == "__main__":
    unittest.main()
