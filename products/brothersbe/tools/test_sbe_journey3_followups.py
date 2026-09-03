#!/usr/bin/env python3
"""Journey 3 follow-up fixes, pinned by test before the fix landed.

Two defects a stranger's walk of the review path found on 2026-08-11:
- `sbe impact` with no dossier named leaked a Python `None` into its
  problem text ("no intake file at None").
- `sbe review-route` with no dossier named silently derived the tier from
  the diff while a readable intake sat on disk, and nothing in the output
  said so.

Both tests were run RED against the pre-fix tree before the fixes landed,
per the calibration rule: a test that cannot fail proves nothing.
"""
import os, sys, json, tempfile, shutil, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import impact as impact_mod
from brothersbe import reviewroute


class TestIntakePathMessages(unittest.TestCase):
    def test_no_dossier_named_does_not_leak_a_python_none(self):
        tier, answers, problem = impact_mod.read_intake(None)
        self.assertIsNone(tier)
        self.assertIsNone(answers)
        self.assertNotIn("None", problem)
        self.assertIn("no dossier", problem)

    def test_a_named_but_absent_path_still_names_that_path(self):
        tier, answers, problem = impact_mod.read_intake("design/x/00-intake.json")
        self.assertIsNone(tier)
        self.assertIn("design/x/00-intake.json", problem)


class TestUnreadIntakeIsNamed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        dossier = os.path.join(self.tmp, "design", "my-change")
        os.makedirs(dossier)
        with open(os.path.join(dossier, "00-intake.json"), "w") as fh:
            json.dump({"answers": {}, "tier": "T0",
                       "override": None, "override_reason": None}, fh)

    def test_route_without_a_dossier_says_an_intake_was_not_read(self):
        tier, source, problem = reviewroute._tier_and_source(
            self.tmp, None, "HEAD", [], None)
        self.assertIn("NOT read", source)
        self.assertIn(os.path.join("design", "my-change", "00-intake.json"),
                      source)

    def test_route_without_a_dossier_and_no_intake_adds_no_note(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        tier, source, problem = reviewroute._tier_and_source(
            empty, None, "HEAD", [], None)
        self.assertNotIn("NOT read", source)


if __name__ == "__main__":
    unittest.main(verbosity=1)
