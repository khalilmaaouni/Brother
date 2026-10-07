#!/usr/bin/env python3
"""FX-09.6: the planner's hand typed CONSTRAINTS sentence steps aside for the grader's generated screen when the
switch is on, and the unit's command runners reach the prompt either way.

WHY: build_plan.py told the planner "no file imports ... shutil", a hand copy of the grader's screen that had drifted
(shutil is allowed). With BROTHER_BRIEF_SCREEN=on the specification excerpt carries THE GRADER'S SCREEN block and the
planner is told to restate its lines; off (the code default) the prompt is byte identical to today's; an unreadable
switch keeps today's sentence (plan() never raises; build_brief has already refused that round).
Drives plan(), the entry point, with a capturing runner. Run: python3 -B scripts/test_build_plan_screen.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import build_plan as BP  # noqa: E402

GOOD = "FILES: a\nEDITS: b\nTESTS: c\nDONE_CHECK: python3 -B scripts/test_a.py\nCONSTRAINTS: d"
#: today's sentence, copied from build_plan.py at the FX-09 base commit
HAND = ("CONSTRAINTS: no file imports subprocess, urllib, socket, http, requests or shutil and none calls run(), "
        "getattr() or exec() dynamically, except allow listed command runners: %s; Python 3.9 and 3.13; tests must "
        "fail without the code.\n")


class ThePlannerFollowsTheSwitch(unittest.TestCase):
    def setUp(self):
        old = os.environ.pop("BROTHER_BRIEF_SCREEN", None)
        self.addCleanup(lambda: os.environ.__setitem__("BROTHER_BRIEF_SCREEN", old) if old is not None
                        else os.environ.pop("BROTHER_BRIEF_SCREEN", None))

    def prompt(self, value):
        if value is None:
            os.environ.pop("BROTHER_BRIEF_SCREEN", None)
        else:
            os.environ["BROTHER_BRIEF_SCREEN"] = value
        seen = []
        out = BP.plan("SPEC", "FILES", runners="scripts/guard.py", runner=lambda p: seen.append(p) or GOOD)
        self.assertTrue(out and out.startswith("BUILD PLAN ("), out)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def test_planner_constraints_follow_the_switch(self):
        off = self.prompt(None)
        self.assertEqual(off, BP.PROMPT % ("scripts/guard.py", "SPEC", "FILES"))
        self.assertIn(HAND % "scripts/guard.py", off)
        self.assertEqual(self.prompt("off"), off)
        on = self.prompt("on")
        self.assertIn("THE GRADER'S SCREEN", on)
        self.assertNotIn("shutil", on)
        self.assertIn("scripts/guard.py", on)
        self.assertEqual(on, off.replace(HAND % "scripts/guard.py", BP.CONSTRAINTS_SCREEN % "scripts/guard.py"))

    def test_an_unreadable_switch_keeps_todays_sentence_and_never_raises(self):
        self.assertEqual(self.prompt("maybe"), self.prompt(None))

    def test_the_prompt_is_todays_bytes_when_off(self):
        self.assertEqual(BP.CONSTRAINTS_HAND, HAND)
        self.assertIn(HAND, BP.PROMPT)


if __name__ == "__main__":
    unittest.main()
